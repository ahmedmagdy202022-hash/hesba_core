"""HG-071 (S6): sell, redeem, write off and reverse a gift card.

Money moves only through services that exist, called and not changed beyond the HG-071 flag:

* **sell** = ``cashboxes.services.create_cashbox_operation`` (transfer, gift-card box → cash or bank, the card's value),
  with ``permission_checked=True`` after ``giftcards.sell``: cash +V, box −V, booked Dr 1101/1102, Cr 2110;
* **redeem** = ``sales.services.record_customer_payment`` into the gift-card box for the invoice's customer, and an
  ``InvoiceSettlement`` (source gift card): customer due −R, box +R, booked Dr 2110, Cr 1103. The invoice's own
  figures stay frozen; the till posts the card part as ``remaining_due`` and redeems it in the same transaction;
* **breakage** = ``create_cashbox_operation`` (direct in to the box) after ``giftcards.breakage``; the projector books
  it to sundry income (4201). Manual only, with a reason, holdable for approval; expiry alone never does it;
* **reverse** = ``cancel_customer_payment`` for a redemption (on the payment's own date), ``cancel_cashbox_operation``
  for a sale (only while the card is untouched) or a breakage, on a chosen date.

HG-034: a card belongs to the entity whose cashbox took its money, and it pays invoices of that entity only.
"""

import secrets
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.db.models.signals import post_save
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission
from settlements import services as settlements
from settlements.models import SettlementSource

from .models import EntryKind, GiftCard, GiftCardEntry, GiftCardStatus

ZERO = Decimal("0.00")
CAPABILITY = "gift_cards"
SELL = "giftcards.sell"  # to sell and to redeem (owner, manager, cashier)
RECEIVE = "sales.receive_customer_payment"  # with SELL, to cancel a redemption (it is a collection undone)
BREAKAGE = "giftcards.breakage"  # to write off, and to reverse a sale or a write-off (owner, manager)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: read aloud at a till
CODE_LENGTH = 12
ATTEMPTS = 5

MESSAGES = {
    "ar": {
        "off": "كروت الهدايا مقفولة؛ شغّلها من الإعدادات ← الخصائص («كروت الهدايا والعضويات»).",
        "amount": "مبلغ الكارت مش صحيح.", "cashbox": "اختار الخزنة أو الحساب البنكي اللي استلم تمن الكارت.",
        "expiry": "تاريخ انتهاء الكارت لازم يكون بعد النهارده.",
        "not_found": "مفيش كارت هدية بالكود ده.", "cancelled": "بيع الكارت ده اتلغى؛ مينفعش يتصرف.",
        "other_entity": "الكارت ده اتباع في فرع تاني؛ بيتصرف في نفس الفرع اللي اتباع فيه بس.",
        "not_posted": "الكارت بيدفع فاتورة مترحّلة بس.", "redeem_amount": "المبلغ اللي هيتدفع بالكارت مش صحيح.",
        "above_due": "المبلغ أكبر من الباقي على الفاتورة ({due}).", "above_balance": "رصيد الكارت مش كفاية؛ الرصيد {balance}.",
        "nothing_left": "الكارت ده مفيش عليه رصيد.", "reason": "اكتب السبب.",
        "touched": "الكارت ده اتصرف منه أو اتشطب جزء منه؛ ألغِ الصرف الأول، أو اشطب الباقي.",
        "not_live": "الحركة دي اتلغت قبل كده.", "wrong_entry": "الحركة دي مينفعش تتلغي من هنا.",
        "sale_reason": "بيع كارت هدية {number}", "breakage_reason": "شطب باقي كارت هدية {number}: {reason}",
        "redeem_notes": "دفع بكارت هدية {number} — فاتورة {invoice}", "invoice_cancelled": "إلغاء الفاتورة {invoice} اللي اتدفعت بالكارت",
        "walk_in_short": "الكاش والكارت مع بعض أقل من المطلوب؛ البيع الآجل محتاج عميل باسمه.",
        "no_permission": "مش معاك صلاحية الدفع بكارت الهدية.",
        "returned": "الفاتورة دي عليها مرتجع؛ مينفعش تتلغي دفعة الكارت منها.",
        "walk_in_redemption": "دي دفعة كارت على بيع لعميل نقدي؛ ألغِ الفاتورة كلها، والكارت هيرجع له رصيده.",
        "statuses": {"active": "شغال", "cancelled": "بيعه اتلغى"},
        "kinds": {"sold": "اتباع", "redeemed": "اتصرف في فاتورة", "breakage": "اتشطب الباقي", "reversed": "اتلغت حركة"},
    },
    "en": {
        "off": "Gift cards are switched off; switch them on from Settings → Features (“Gift cards & memberships”).",
        "amount": "Invalid card amount.", "cashbox": "Choose the cashbox or bank account that took the card's money.",
        "expiry": "The card's expiry date must be after today.",
        "not_found": "No gift card has this code.", "cancelled": "This card's sale was cancelled; it cannot be spent.",
        "other_entity": "This card was sold in another entity; it is spent only in the entity it was sold in.",
        "not_posted": "A card pays a posted invoice only.", "redeem_amount": "Invalid amount to pay by card.",
        "above_due": "The amount is more than what is left on the invoice ({due}).", "above_balance": "The card's balance is not enough; it holds {balance}.",
        "nothing_left": "Nothing is left on this card.", "reason": "Enter the reason.",
        "touched": "This card was spent or written off in part; cancel that first, or write off the rest.",
        "not_live": "This was already reversed.", "wrong_entry": "This cannot be reversed here.",
        "sale_reason": "Gift card {number} sold", "breakage_reason": "Gift card {number} rest written off: {reason}",
        "redeem_notes": "Paid by gift card {number} — invoice {invoice}", "invoice_cancelled": "Invoice {invoice} paid by the card was cancelled",
        "walk_in_short": "Cash and card together are less than the total; a credit sale needs a named customer.",
        "no_permission": "You may not take payment by gift card.",
        "returned": "This invoice has a posted return; its card payment cannot be cancelled.",
        "walk_in_redemption": "This card payment is on a walk-in sale; cancel the whole invoice, and the card gets its money back.",
        "statuses": {"active": "Active", "cancelled": "Sale cancelled"},
        "kinds": {"sold": "Sold", "redeemed": "Spent on an invoice", "breakage": "Rest written off", "reversed": "Reversed"},
    },
}


def enabled():
    """Whether «كروت الهدايا والعضويات» is at work (CAP-001, off by default). Off: no new card, sale, redemption or
    screen. Following the sales engine (an invoice or a payment cancelled) runs whatever the switch says."""

    from settings_core.capabilities import capability_enabled

    return capability_enabled(CAPABILITY)


def status_label(card, lang="ar"):
    return MESSAGES[lang]["statuses"].get(card.status, card.status)


def kind_label(entry, lang="ar"):
    return MESSAGES[lang]["kinds"].get(entry.kind, entry.kind)


def _require(user, code):
    if not user_has_permission(user, code):
        raise PermissionDenied(f"Gift cards need {code}.")


def _audit(card, user, action, after, before=None, reason="", event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="giftcards", action=action, object_type="giftcards.GiftCard",
                            object_id=str(card.pk), reason=reason, before_data=before or {}, after_data=after)


def _amount(value, message):
    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError(message)
    if not number.is_finite() or number <= 0:
        raise ValidationError(message)
    return money_round(number)


def _reason(reason, words):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(words["reason"])
    return reason[:200]


# ---- reading ---------------------------------------------------------------------


def normalize(code):
    """What the holder typed, as stored: letters and digits only, upper case."""

    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())[:20]


def pretty(code):
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def find(code):
    code = normalize(code)
    return GiftCard.objects.filter(code=code).select_related("entity").first() if code else None


#: An entry counts while the document behind it is still posted.
LIVE = (Q(kind__in=(EntryKind.SOLD, EntryKind.BREAKAGE), operation__status="posted")
        | Q(kind=EntryKind.REDEEMED, payment__status="posted"))


def _sums(entries):
    return entries.filter(LIVE).aggregate(
        sold=Sum("amount", filter=Q(kind=EntryKind.SOLD)),
        redeemed=Sum("amount", filter=Q(kind=EntryKind.REDEEMED)),
        breakage=Sum("amount", filter=Q(kind=EntryKind.BREAKAGE)),
    )


def figures(card):
    """{sold, redeemed, breakage, balance} from the live entries."""

    sums = {key: money_round(value or ZERO) for key, value in _sums(GiftCardEntry.objects.filter(card=card)).items()}
    sums["balance"] = money_round(sums["sold"] - sums["redeemed"] - sums["breakage"])
    return sums


def balance(card):
    return figures(card)["balance"]


def balances(cards):
    """{card pk: balance} for a list of cards (one query)."""

    ids = [getattr(card, "pk", card) for card in cards]
    rows = (GiftCardEntry.objects.filter(card_id__in=ids).filter(LIVE).values("card_id")
            .annotate(sold=Sum("amount", filter=Q(kind=EntryKind.SOLD)), redeemed=Sum("amount", filter=Q(kind=EntryKind.REDEEMED)),
                      breakage=Sum("amount", filter=Q(kind=EntryKind.BREAKAGE))))
    result = {pk: ZERO for pk in ids}
    for row in rows:
        result[row["card_id"]] = money_round((row["sold"] or ZERO) - (row["redeemed"] or ZERO) - (row["breakage"] or ZERO))
    return result


def outstanding_for(entity):
    """What the entity's live cards still hold: the 2110 figure the gift-card box mirrors (negated)."""

    return sum(balances(GiftCard.objects.filter(entity=entity)).values(), ZERO)


def returned_due(invoice):
    """What posted returns took off the customer's due on this invoice."""

    from sales.models import SalesReturn

    total = SalesReturn.objects.filter(source_invoice=invoice, status="posted").aggregate(t=Sum("due_amount"))["t"]
    return money_round(total or ZERO)


def redeemable_due(invoice):
    """What a card may still pay on a posted invoice: ``remaining_due − applied − posted returns' due``, never below 0."""

    if invoice.status != "posted":
        return ZERO
    # HG-071 D8: an instalment plan collects through its own payments, which neither change remaining_due nor settle
    # the invoice; a card there could pay what was already collected. Such invoices are paid by their instalments.
    from installments.models import InstalmentPlan, PlanStatus

    if InstalmentPlan.objects.filter(invoice=invoice, status=PlanStatus.ACTIVE).exists():
        return ZERO
    return max(money_round(Decimal(invoice.remaining_due) - settlements.applied_to(invoice) - returned_due(invoice)), ZERO)


def masked(code):
    """The code as every screen shows it: the last four characters only (HG-071 review: the holder is whoever has it)."""

    return f"••••-••••-{code[-4:]}" if code else ""


def entity_of_invoice(invoice):
    from entities.services import main_entity

    location = invoice.selling_location
    return getattr(location, "entity", None) or main_entity()


def gift_box(entity, currency=None):
    """The entity's one gift-card box (made on its first card sale; kept out of every picker by ``HELD_KINDS``)."""

    from cashboxes.models import Cashbox, CashboxKind

    box = Cashbox.objects.filter(kind=CashboxKind.GIFT_CARD, entity=entity).order_by("pk").first()
    if box is None:
        if currency is None:
            from settings_core.models import ClientProfile

            currency = getattr(ClientProfile.get_active(), "default_currency", None) or "EGP"
        try:
            with transaction.atomic():
                box = Cashbox.objects.create(cashbox_code=f"GIFT-CARDS-{entity.pk}", name_ar="كروت هدايا غير مصروفة", name_en="Unspent gift cards",
                                             kind=CashboxKind.GIFT_CARD, entity=entity, currency=currency,
                                             notes="HG-071: its negative balance is what the gift cards still hold.")
        except IntegrityError:  # another till made it a moment ago
            box = Cashbox.objects.get(cashbox_code=f"GIFT-CARDS-{entity.pk}")
    if not box.active:
        box.active = True
        box.save(update_fields=["active", "updated_at"])
    return box


# ---- sell ------------------------------------------------------------------------


def _new_code():
    for _attempt in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not GiftCard.objects.filter(code=code).exists():
            return code
    raise ValidationError("Could not make a gift card code; try again.")


def _create_card(**fields):
    from config.numbering import next_in_series

    for attempt in range(ATTEMPTS):
        try:
            with transaction.atomic():
                return GiftCard.objects.create(number=next_in_series(GiftCard, "number", "GC-"), code=_new_code(), **fields)
        except IntegrityError:
            if attempt == ATTEMPTS - 1:
                raise


@transaction.atomic
def sell(face_value, cashbox, user, holder_name="", holder_phone="", buyer=None, expires_on=None, lang="ar"):
    """Sell a card: cash or bank +V, the entity's gift-card box −V. No invoice, no revenue, no VAT."""

    from cashboxes.models import HELD_KINDS, CashboxOperationType
    from cashboxes.services import create_cashbox_operation
    from entities.services import main_entity
    from sales.pos import WALK_IN_CODE

    words = MESSAGES[lang]
    if not enabled():
        raise ValidationError(words["off"])
    _require(user, SELL)
    amount = _amount(face_value, words["amount"])
    if cashbox is None or cashbox.kind in HELD_KINDS or not cashbox.active:
        raise ValidationError(words["cashbox"])
    today = timezone.localdate()
    if expires_on is not None and expires_on <= today:
        raise ValidationError(words["expiry"])
    if buyer is not None and buyer.customer_code == WALK_IN_CODE:
        buyer = None
    entity = cashbox.entity or main_entity()
    box = gift_box(entity, cashbox.currency)
    card = _create_card(entity=entity, face_value=amount, buyer=buyer, holder_name=(holder_name or "").strip()[:120],
                        holder_phone=(holder_phone or "").strip()[:50], expires_on=expires_on, created_by=user)
    operation = create_cashbox_operation(f"GC-SALE-{card.number}", today, CashboxOperationType.TRANSFER, amount,
                                         words["sale_reason"].format(number=card.number), user, source_cashbox=box,
                                         destination_cashbox=cashbox, permission_checked=True)
    GiftCardEntry.objects.create(card=card, kind=EntryKind.SOLD, amount=amount, entry_date=today, operation=operation, created_by=user)
    _audit(card, user, "sell_gift_card", {"number": card.number, "face_value": str(amount), "cashbox": cashbox.pk, "box": box.pk,
                                          "operation": operation.pk, "expires_on": expires_on.isoformat() if expires_on else None},
           event=AuditEventType.CREATE)
    return card


# ---- redeem ----------------------------------------------------------------------


def _record_payment(day, customer, box, amount, user, notes):
    """``record_customer_payment`` under a fresh ``GCR-YYYYMMDD-NNN``; a number taken in the same instant retries."""

    from config.numbering import next_in_series
    from sales.models import CustomerPayment
    from sales.services import record_customer_payment

    for attempt in range(ATTEMPTS):
        number = next_in_series(CustomerPayment, "payment_number", f"GCR-{day:%Y%m%d}-", width=3)
        try:
            with transaction.atomic():
                return record_customer_payment(number, day, customer, box, amount, user, notes=notes)
        except IntegrityError:
            if attempt == ATTEMPTS - 1 or not CustomerPayment.objects.filter(payment_number=number).exists():
                raise


def check_card(card, invoice_entity, words):
    """Refuse a card that cannot pay in this entity (HG-034 / HG-071 deviation D1: no redemption across entities)."""

    if card is None:
        raise ValidationError(words["not_found"])
    if card.status != GiftCardStatus.ACTIVE:
        raise ValidationError(words["cancelled"])
    if card.entity_id != invoice_entity.pk:
        raise ValidationError(words["other_entity"])


@transaction.atomic
def redeem(card, invoice, amount, user, lang="ar"):
    """Pay ``amount`` of a posted invoice from the card: a payment into the gift-card box, settled on the invoice.

    One lock order everywhere: the invoice, then the card. The balance is read under the card's lock, so two tills
    cannot spend the same money; the card is spent at most to its balance and the invoice at most to what it asks.
    Expiry is shown, not enforced (HG-071).
    """

    from sales.models import SalesInvoice

    words = MESSAGES[lang]
    if not enabled():
        raise ValidationError(words["off"])
    _require(user, SELL)
    if card is None:
        raise ValidationError(words["not_found"])
    invoice = SalesInvoice.objects.select_for_update(of=("self",)).select_related("customer", "selling_location").get(pk=invoice.pk)
    if invoice.status != "posted":
        raise ValidationError(words["not_posted"])
    card = GiftCard.objects.select_for_update(of=("self",)).get(pk=card.pk)
    check_card(card, entity_of_invoice(invoice), words)
    amount = _amount(amount, words["redeem_amount"])
    due = redeemable_due(invoice)
    if amount > due:
        raise ValidationError(words["above_due"].format(due=f"{max(due, ZERO):,.2f}"))
    held = balance(card)
    if amount > held:
        raise ValidationError(words["above_balance"].format(balance=f"{held:,.2f}"))
    day = timezone.localdate()
    payment = _record_payment(day, invoice.customer, gift_box(card.entity), amount, user,
                              words["redeem_notes"].format(number=card.number, invoice=invoice.invoice_number))
    settlements.settle(invoice, payment, amount, SettlementSource.GIFT_CARD)
    entry = GiftCardEntry.objects.create(card=card, kind=EntryKind.REDEEMED, amount=amount, entry_date=day, payment=payment, invoice=invoice, created_by=user)
    _audit(card, user, "redeem_gift_card", {"invoice": invoice.invoice_number, "amount": str(amount), "payment": payment.payment_number,
                                            "balance_after": str(money_round(held - amount))})
    return entry


@transaction.atomic
def cancel_redemption(entry, user, reason, lang="ar"):
    """A mistaken redemption: cancel its payment (on the payment's own date). The invoice is due again, the card holds
    the money again. The screen gates it as ``sales.cancel_payment`` (FB-27)."""

    from sales.services import cancel_customer_payment

    from sales.pos import WALK_IN_CODE

    words = MESSAGES[lang]
    _require(user, SELL)
    _require(user, RECEIVE)
    reason = _reason(reason, words)
    if entry.kind != EntryKind.REDEEMED or entry.payment_id is None:
        raise ValidationError(words["wrong_entry"])
    if entry.payment.status != "posted":
        raise ValidationError(words["not_live"])
    invoice = entry.invoice
    if invoice is not None and invoice.status == "posted":
        if invoice.customer.customer_code == WALK_IN_CODE:  # the walk-in never owes: its card payment goes with the invoice
            raise ValidationError(words["walk_in_redemption"])
        if returned_due(invoice) > 0 or invoice.returns.filter(status="posted").exists():
            raise ValidationError(words["returned"])
    cancel_customer_payment(entry.payment_id, user, reason)  # the audit hook writes the reversed entry
    return entry


# ---- breakage --------------------------------------------------------------------


@transaction.atomic
def breakage(card, user, reason, lang="ar"):
    """Write the card's unspent rest off as sundry income (4201): a direct cash in to the gift-card box.

    Manual only, with a reason; the screen holds it for approval under ``giftcards.breakage`` (FB-27). Never automatic.
    """

    from cashboxes.models import CashboxOperation, CashboxOperationType
    from cashboxes.services import create_cashbox_operation
    from config.numbering import next_in_series

    words = MESSAGES[lang]
    _require(user, BREAKAGE)
    reason = _reason(reason, words)
    card = GiftCard.objects.select_for_update(of=("self",)).get(pk=card.pk)
    if card.status != GiftCardStatus.ACTIVE:
        raise ValidationError(words["cancelled"])
    rest = balance(card)
    if rest <= 0:
        raise ValidationError(words["nothing_left"])
    today = timezone.localdate()
    operation = create_cashbox_operation(next_in_series(CashboxOperation, "reference_number", f"GC-BRK-{card.number}-", width=2), today,
                                         CashboxOperationType.DIRECT_IN, rest, words["breakage_reason"].format(number=card.number, reason=reason),
                                         user, destination_cashbox=gift_box(card.entity), permission_checked=True)
    entry = GiftCardEntry.objects.create(card=card, kind=EntryKind.BREAKAGE, amount=rest, entry_date=today, operation=operation, reason=reason, created_by=user)
    _audit(card, user, "gift_card_breakage", {"amount": str(rest), "operation": operation.pk}, reason=reason)
    return entry


# ---- reversing a sale or a breakage ------------------------------------------------


@transaction.atomic
def reverse(entry, reversal_date, reason, user, lang="ar"):
    """Reverse a card's sale (cash back, only while the card is untouched) or a breakage (the liability back), dated
    ``reversal_date`` (an open month). Holdable under ``giftcards.reverse`` (FB-27)."""

    from cashboxes.services import cancel_cashbox_operation

    words = MESSAGES[lang]
    _require(user, BREAKAGE)
    reason = _reason(reason, words)
    if entry.kind not in (EntryKind.SOLD, EntryKind.BREAKAGE) or entry.operation_id is None:
        raise ValidationError(words["wrong_entry"])
    card = GiftCard.objects.select_for_update(of=("self",)).get(pk=entry.card_id)
    entry = GiftCardEntry.objects.select_related("operation").get(pk=entry.pk)
    if entry.operation.status != "posted":
        raise ValidationError(words["not_live"])
    if entry.kind == EntryKind.SOLD:
        sums = figures(card)
        if sums["redeemed"] or sums["breakage"] or sums["balance"] != card.face_value:
            raise ValidationError(words["touched"])
    cancel_cashbox_operation(entry.operation_id, reversal_date, reason, user, permission_checked=True)
    GiftCardEntry.objects.create(card=card, kind=EntryKind.REVERSED, amount=entry.amount, entry_date=reversal_date, operation=entry.operation,
                                 reverses=entry, reason=reason, created_by=user)
    if entry.kind == EntryKind.SOLD:
        card.status = GiftCardStatus.CANCELLED
        card.save(update_fields=["status"])
    _audit(card, user, "reverse_gift_card_" + entry.kind, {"amount": str(entry.amount), "reversal_date": reversal_date.isoformat(),
                                                             "operation": entry.operation_id}, reason=reason)
    return entry


# ---- following the sales engine ----------------------------------------------------


def _mark_reversed(payment_id, actor, reason):
    entry = GiftCardEntry.objects.filter(kind=EntryKind.REDEEMED, payment_id=payment_id).first()
    if entry is None or GiftCardEntry.objects.filter(reverses=entry).exists():
        return None
    reversed_entry = GiftCardEntry.objects.create(card=entry.card, kind=EntryKind.REVERSED, amount=entry.amount, entry_date=timezone.localdate(),
                                                  payment=entry.payment, invoice=entry.invoice, reverses=entry, reason=(reason or "")[:255], created_by=actor)
    _audit(entry.card, actor, "reverse_gift_card_redemption", {"amount": str(entry.amount), "payment": entry.payment.payment_number}, reason=reason or "")
    return reversed_entry


def _on_audit(sender, instance, created, **kwargs):
    """A cancelled redemption payment gives the card its money back; a cancelled invoice cancels its redemptions.

    HG-071 deviation D3: without the second, ``settlements.release`` (run by the deposits hook) drops the invoice's
    settlement rows, the redemption payment stays as the customer's credit (the walk-in's too) and the card stays
    spent. The payment is cancelled on its own date in the same transaction, so a closed month refuses the whole
    cancellation, as for any payment.
    """

    if not created or instance.module != "sales":
        return
    if instance.action == "cancel_customer_payment":
        _mark_reversed(int(instance.object_id), instance.actor, instance.reason)
    elif instance.action == "cancel_posted_sales_invoice":
        from sales.services import cancel_customer_payment

        invoice_id = int(instance.object_id)
        for entry in GiftCardEntry.objects.filter(kind=EntryKind.REDEEMED, invoice_id=invoice_id, payment__status="posted").select_related("payment", "invoice"):
            words = MESSAGES["ar"]
            cancel_customer_payment(entry.payment_id, instance.actor, words["invoice_cancelled"].format(invoice=entry.invoice.invoice_number))


def connect():
    post_save.connect(_on_audit, sender=AuditLog, dispatch_uid="giftcards-follow-the-sales-engine", weak=False)
