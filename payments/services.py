"""HG-070 (S4): make a payment link, record money from a verified gateway callback, and book the gateway's payout.

Money moves only through services that exist, called and not changed:

* **payment** = ``sales.services.record_customer_payment`` into the invoice's entity's «بوابة الدفع» bank account,
  on the one transition new / pending → confirmed of a ``GatewayTransaction``, under row locks (the invoice, the link,
  the transaction row), with ``payment_number = <PREFIX>-<transaction id>`` (unique) as a second guard;
* **settlement** = ``settlements.services.settle`` (source ``payment_link``) for ``min(taken, remaining_due − applied)``
  of a posted invoice; anything above stays as the customer's credit on account. The invoice's own figures stay frozen;
* **payout** = ``cashboxes.services.create_cashbox_operation``: a transfer of the net to a real bank account and a
  ``bank_fee`` (booked in 5212) for the gateway's fee, when Paymob pays out.

The customer's browser is never trusted: the pay page and the return page read status only.
"""

import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission
from settlements import services as settlements
from settlements.models import SettlementSource

from . import providers
from .models import GatewayTransaction, LinkStatus, PaymentLink, TransactionOutcome
from .providers import GatewayError, Outcome

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
CAPABILITY = "payment_links"
RECEIVE = "sales.receive_customer_payment"  # to make or cancel a link, as for any collection
MOVE_CASH = "cashboxes.move_cash"  # to book the payout
GATEWAY_BOX_NAME = "بوابة الدفع"
SALT = "hesba.paylink.v1"
CURRENCY = "EGP"


def link_hours():
    return max(1, int(getattr(settings, "PAYMENT_LINK_HOURS", 72) or 72))


MESSAGES = {
    "ar": {
        "off": "لينك الدفع مقفول؛ شغّله من الإعدادات ← الخصائص («لينك الدفع أونلاين»).",
        "not_connected": "بوابة الدفع غير موصّلة على السيرفر، فمينفعش يتعمل لينك.",
        "no_base": "عنوان السيستم العام (PUBLIC_BASE_URL) مش متظبط، فمينفعش يطلع لينك يتبعت.",
        "not_posted": "اللينك بيتعمل لفاتورة مترحّلة بس.",
        "walk_in": "العميل النقدي مينفعش ياخد لينك دفع؛ الفاتورة لازم تكون باسم عميل.",
        "nothing_due": "الفاتورة دي مفيش عليها باقي.",
        "amount": "مبلغ اللينك مش صحيح.",
        "above_due": "مبلغ اللينك أكبر من الباقي على الفاتورة ({due}).",
        "live": "الفاتورة دي عليها لينك دفع لسه شغال؛ استنى يتدفع أو الغيه الأول.",
        "no_box": "لازم يكون فيه حساب بنكي اسمه «بوابة الدفع» في نفس الفرع بتاع الفاتورة.",
        "two_boxes": "فيه أكتر من حساب بنكي اسمه «بوابة الدفع» في الفرع ده؛ سيب واحد بس نشط.",
        "gateway": "بوابة الدفع رفضت أو مش بترد دلوقتي؛ جرّب كمان شوية.",
        "not_new": "اللينك ده مش شغال خلاص.",
        "payout_box": "اختار حساب «بوابة الدفع» والحساب البنكي اللي استلم التحويل، في نفس الفرع.",
        "payout_amount": "اكتب صافي التحويل والعمولة كأرقام صحيحة.",
        "payout_ref": "اكتب رقم التحويل اللي في كشف البوابة.", "payout_twice": "التحويل رقم {ref} اتسجّل قبل كده.",
        "payout_reason": "تحويل بوابة الدفع {ref}", "fee_reason": "عمولة بوابة الدفع {ref}",
        "statuses": {"new": "مستني الدفع", "paid": "اتدفع", "mismatch": "مبلغ مختلف", "failed": "الدفع ما تمّش", "expired": "انتهى"},
    },
    "en": {
        "off": "Payment links are switched off; switch them on from Settings → Features (“Online payment link”).",
        "not_connected": "The payment gateway is not connected on the server, so no link can be made.",
        "no_base": "The public address (PUBLIC_BASE_URL) is not set, so no link can be sent.",
        "not_posted": "A link is made for a posted invoice only.",
        "walk_in": "The walk-in customer cannot get a payment link; the invoice must name a customer.",
        "nothing_due": "Nothing is left to pay on this invoice.",
        "amount": "Invalid link amount.",
        "above_due": "The link amount is more than what is left on the invoice ({due}).",
        "live": "This invoice already has a live payment link; wait for it to be paid or cancel it first.",
        "no_box": "A bank account named “بوابة الدفع” (payment gateway) is needed in the invoice's own entity.",
        "two_boxes": "More than one bank account named “بوابة الدفع” is active in this entity; keep one.",
        "gateway": "The payment gateway refused or is not answering; try again shortly.",
        "not_new": "This link is no longer live.",
        "payout_box": "Choose the payment-gateway account and the bank account that received the payout, in one entity.",
        "payout_amount": "Enter the net payout and the fee as valid numbers.",
        "payout_ref": "Enter the payout reference from the gateway's statement.", "payout_twice": "Payout {ref} was already recorded.",
        "payout_reason": "Payment gateway payout {ref}", "fee_reason": "Payment gateway fee {ref}",
        "statuses": {"new": "Waiting for payment", "paid": "Paid", "mismatch": "Paid, different amount", "failed": "Payment failed", "expired": "Expired"},
    },
}


def enabled():
    from settings_core.capabilities import capability_enabled

    return capability_enabled(CAPABILITY)


def connected():
    """The active provider, or None («غير موصّلة»)."""

    return providers.active()


def status_label(link, lang="ar"):
    return MESSAGES[lang]["statuses"].get(link.status, link.status)


def _require(user, code):
    if not user_has_permission(user, code):
        raise PermissionDenied(f"Payment links need {code}.")


def _audit(action, object_type, object_id, after, user=None, before=None, event=AuditEventType.UPDATE, reason=""):
    AuditLog.objects.create(event_type=event, actor=user, module="payments", action=action, object_type=object_type,
                            object_id=str(object_id or ""), reason=reason, before_data=before or {}, after_data=after)


# ---- reading -------------------------------------------------------------------------


def entity_of(invoice):
    from entities.services import main_entity

    location = invoice.selling_location
    return getattr(location, "entity", None) or main_entity()


def outstanding(invoice):
    """What the invoice still asks for: ``remaining_due − applied`` (deposits, paid links, card redemptions)."""

    if invoice.status != "posted":
        return ZERO
    return money_round(Decimal(invoice.remaining_due) - settlements.applied_to(invoice))


def gateway_box(entity, lang="ar"):
    """The entity's one active bank account named «بوابة الدفع…» (never a cheque or courier box)."""

    from cashboxes.models import Cashbox, CashboxKind

    boxes = list(Cashbox.objects.filter(kind=CashboxKind.BANK, active=True, entity=entity, name_ar__startswith=GATEWAY_BOX_NAME).order_by("pk")[:2])
    if not boxes:
        raise ValidationError(MESSAGES[lang]["no_box"])
    if len(boxes) > 1:
        raise ValidationError(MESSAGES[lang]["two_boxes"])
    return boxes[0]


def gateway_boxes(entity=None):
    from cashboxes.models import Cashbox, CashboxKind

    rows = Cashbox.objects.filter(kind=CashboxKind.BANK, active=True, name_ar__startswith=GATEWAY_BOX_NAME)
    return rows.filter(entity=entity) if entity is not None else rows


def expire_stale(invoice=None, now=None):
    """Expire live links past their time. Each one by a conditional update (``status = new`` in the WHERE), so a link
    a confirmation marked paid in the meantime is never overwritten; only a link actually expired is audited."""

    now = now or timezone.now()
    lookup = {"status": LinkStatus.NEW, "expires_at__lte": now}
    if invoice is not None:
        lookup["invoice"] = invoice
    for link in list(PaymentLink.objects.filter(**lookup)):
        if PaymentLink.objects.filter(pk=link.pk, status=LinkStatus.NEW).update(status=LinkStatus.EXPIRED, updated_at=now):
            _audit("expire_link", "payments.PaymentLink", link.pk, {"status": LinkStatus.EXPIRED, "why": "time"}, before={"status": LinkStatus.NEW})


def live_link(invoice):
    expire_stale(invoice)
    return PaymentLink.objects.filter(invoice=invoice, status=LinkStatus.NEW).first()


def is_payable(link, now=None):
    """Whether the pay page may send the customer to the gateway now (status, time, invoice and amount still right)."""

    if link.status != LinkStatus.NEW or link.expires_at <= (now or timezone.now()):
        return False
    return link.invoice.status == "posted" and link.amount <= outstanding(link.invoice)


# ---- the customer's address ----------------------------------------------------------


def sign_token(link):
    return signing.dumps(link.token, salt=SALT)


def read_token(signed):
    """The link behind a signed address, or None for a forged or altered one (expiry is the link's own)."""

    try:
        token = signing.loads(signed, salt=SALT)
    except signing.BadSignature:
        return None
    if not isinstance(token, str):
        return None
    return PaymentLink.objects.select_related("invoice", "customer").filter(token=token).first()


def link_url(link, lang="ar"):
    """Built from PUBLIC_BASE_URL only (Ahmed's standing rule), never from the request's host. None when it is unset."""

    base = getattr(settings, "PUBLIC_BASE_URL", "")
    if not base:
        return None
    return f"{base.rstrip('/')}{reverse('pay_page', args=[sign_token(link)])}?lang={lang}"


def to_cents(amount):
    return int((money_round(amount) * HUNDRED).to_integral_exact())


def from_cents(cents):
    return money_round(Decimal(int(cents)) / HUNDRED)


def _amount(value, words):
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError(words["amount"])
    if not number.is_finite() or number <= 0:
        raise ValidationError(words["amount"])
    return money_round(number)


# ---- make and cancel a link ------------------------------------------------------------


@transaction.atomic
def create_link(invoice, user, amount=None, lang="ar"):
    """A link for a posted invoice, for at most what it still asks for. The gateway order is made here, server side."""

    from sales.models import SalesInvoice
    from sales.pos import WALK_IN_CODE

    words = MESSAGES[lang]
    if not enabled():
        raise ValidationError(words["off"])
    _require(user, RECEIVE)
    provider = connected()
    if provider is None:
        raise ValidationError(words["not_connected"])
    if not getattr(settings, "PUBLIC_BASE_URL", ""):
        raise ValidationError(words["no_base"])
    invoice = SalesInvoice.objects.select_for_update(of=("self",)).select_related("customer", "selling_location").get(pk=invoice.pk)
    if invoice.status != "posted":
        raise ValidationError(words["not_posted"])
    if invoice.customer is None or invoice.customer.customer_code == WALK_IN_CODE:
        raise ValidationError(words["walk_in"])
    expire_stale(invoice)
    if PaymentLink.objects.filter(invoice=invoice, status=LinkStatus.NEW).exists():
        raise ValidationError(words["live"])
    due = outstanding(invoice)
    if due <= 0:
        raise ValidationError(words["nothing_due"])
    amount = due if amount in (None, "") else _amount(amount, words)
    if amount > due:
        raise ValidationError(words["above_due"].format(due=due))
    box = gateway_box(entity_of(invoice), lang)
    hours = link_hours()
    link = PaymentLink.objects.create(
        invoice=invoice, customer=invoice.customer, cashbox=box, amount=amount, currency=CURRENCY, provider=provider.code,
        reference=f"HSB-{secrets.token_hex(10)}", token=secrets.token_urlsafe(32), expires_at=timezone.now() + timedelta(hours=hours), created_by=user,
    )
    try:
        checkout = provider.create_checkout(amount_cents=to_cents(amount), currency=CURRENCY, merchant_reference=link.reference,
                                            customer=invoice.customer, expires_seconds=hours * 3600)
    except GatewayError:
        raise ValidationError(words["gateway"])
    link.gateway_order_id, link.checkout_url = checkout.order_id, checkout.checkout_url
    link.save(update_fields=["gateway_order_id", "checkout_url", "updated_at"])
    _audit("create_link", "payments.PaymentLink", link.pk, {"invoice": invoice.invoice_number, "amount": str(amount), "provider": provider.code,
                                                             "gateway_order_id": link.gateway_order_id, "cashbox": box.pk,
                                                             "expires_at": link.expires_at.isoformat()}, user=user, event=AuditEventType.CREATE)
    return link


@transaction.atomic
def cancel_link(link, user, lang="ar"):
    _require(user, RECEIVE)
    link = PaymentLink.objects.select_for_update().get(pk=link.pk)
    if link.status != LinkStatus.NEW:
        raise ValidationError(MESSAGES[lang]["not_new"])
    link.status = LinkStatus.EXPIRED
    link.save(update_fields=["status", "updated_at"])
    _audit("cancel_link", "payments.PaymentLink", link.pk, {"status": LinkStatus.EXPIRED, "why": "cancelled"}, user=user, before={"status": LinkStatus.NEW})
    return link


# ---- the gateway's callback ----------------------------------------------------------------


def _summary(payload):
    obj = payload.get("obj") if isinstance(payload, dict) else None
    obj = obj if isinstance(obj, dict) else {}
    order = obj.get("order") if isinstance(obj.get("order"), dict) else {}
    return {"transaction_id": str(obj.get("id", ""))[:64], "order_id": str(order.get("id", ""))[:64], "amount_cents": str(obj.get("amount_cents", ""))[:20],
            "success": str(obj.get("success", ""))[:5], "pending": str(obj.get("pending", ""))[:5]}


def handle_callback(payload, received_hmac, provider=None):
    """(HTTP status, result) for one gateway callback. 400 with an audit row and no other row when it is not verified.

    The capability is not checked here: money the gateway confirms for a link already sent is recorded whatever the
    switch says now.
    """

    provider = provider or connected()
    if provider is None or not provider.verify_callback(payload, received_hmac):
        _audit("callback_rejected", "payments.GatewayTransaction", "", {**_summary(payload), "why": "no provider" if provider is None else "hmac"})
        return 400, "rejected"
    try:
        callback = provider.parse_callback(payload)
    except (GatewayError, KeyError, TypeError) as error:
        _audit("callback_rejected", "payments.GatewayTransaction", "", {**_summary(payload), "why": str(error)[:200]})
        return 400, "rejected"
    with transaction.atomic():
        return 200, _apply_callback(provider, callback)


def _find_link(provider, callback):
    """Only the gateway's own order id (``order.id``, covered by the HMAC) picks the link. ``merchant_order_id`` is not
    signed, so it never steers a callback for another order to one of our invoices (Codex #315 P1)."""

    if not callback.order_id:
        return None
    link = PaymentLink.objects.filter(provider=provider.code, gateway_order_id=callback.order_id).exclude(gateway_order_id="").first()
    if link is not None and callback.merchant_reference and callback.merchant_reference != link.reference:
        return None  # a reference is present but not this link's own: treated as an unknown order (review)
    return link


def _transaction_row(provider, callback, link):
    """The unique row for this transaction id, made in a savepoint (a racing first callback makes it once), then locked."""

    created = False
    if not GatewayTransaction.objects.filter(provider=provider.code, transaction_id=callback.transaction_id).exists():
        try:
            with transaction.atomic():
                GatewayTransaction.objects.create(provider=provider.code, transaction_id=callback.transaction_id, link=link, outcome=callback.outcome,
                                                  amount_cents=callback.amount_cents, currency=callback.currency[:3], hmac_ok=True, payload=callback.raw)
                created = True
        except IntegrityError:
            created = False
    row = GatewayTransaction.objects.select_for_update(of=("self",)).get(provider=provider.code, transaction_id=callback.transaction_id)
    return row, created


def _apply_callback(provider, callback):
    from sales.models import SalesInvoice

    link = _find_link(provider, callback)
    if link is not None:  # one lock order everywhere: the invoice, then its link, then the transaction row
        SalesInvoice.objects.select_for_update(of=("self",)).get(pk=link.invoice_id)
        link = PaymentLink.objects.select_for_update(of=("self",)).select_related("invoice", "customer", "cashbox").get(pk=link.pk)
    txn, created = _transaction_row(provider, callback, link)
    result = _transition(provider, txn, link, callback, created)
    _audit("callback", "payments.GatewayTransaction", txn.pk, {"transaction_id": callback.transaction_id, "order_id": callback.order_id,
                                                                 "outcome": callback.outcome, "amount_cents": callback.amount_cents,
                                                                 "currency": callback.currency, "result": result, "link": getattr(link, "pk", None),
                                                                 "payment": txn.customer_payment.payment_number if txn.customer_payment_id else None})
    return result


def _flag(txn, note, **fields):
    txn.needs_review, txn.note = True, note[:300]
    for key, value in fields.items():
        setattr(txn, key, value)
    txn.save()


def _transition(provider, txn, link, callback, created):
    if txn.customer_payment_id:  # confirmed once: nothing the machine says later reverses it
        if callback.outcome == Outcome.FAILED:
            _flag(txn, "failed, void or refund after confirmation: check by hand, nothing reversed")
            return "flagged_after_confirmed"
        return "repeat_confirmed" if callback.outcome == Outcome.CONFIRMED else "late_pending_ignored"
    if not created and txn.link_id != getattr(link, "pk", None):
        # The row was first logged for another link, or as an unknown order: a replay never posts, whatever the
        # unsigned fields say now.
        if callback.outcome == Outcome.CONFIRMED:
            _flag(txn, "replay that names a different link than the first callback: check by hand, nothing recorded")
        return "unknown_order" if txn.link_id is None else "link_changed"
    if callback.child:
        _flag(txn, "child transaction (refund, void or capture): check by hand, nothing recorded or reversed")
        return "flagged_child"
    if txn.outcome == TransactionOutcome.FAILED and not created:
        # HG-070: money moves only on new or pending → confirmed. A failed transaction that later says pending or
        # confirmed is logged and flagged for a person, never posted by the machine.
        if callback.outcome == Outcome.CONFIRMED:
            _flag(txn, "confirmed after failed for the same transaction: check by hand, nothing recorded")
            return "flagged_after_failed"
        return "repeat_failed" if callback.outcome == Outcome.FAILED else "late_pending_ignored"
    if callback.outcome == Outcome.PENDING:
        if txn.outcome == TransactionOutcome.PENDING and not created:
            return "repeat_pending"
        txn.outcome, txn.payload, txn.amount_cents = TransactionOutcome.PENDING, callback.raw, callback.amount_cents
        txn.save()
        return "pending"
    if callback.outcome == Outcome.FAILED:
        txn.outcome, txn.payload = TransactionOutcome.FAILED, callback.raw
        txn.save()
        if link is not None and link.status == LinkStatus.NEW:
            link.status = LinkStatus.FAILED
            link.save(update_fields=["status", "updated_at"])
        elif link is not None and (link.status in (LinkStatus.PAID, LinkStatus.MISMATCH)
                                   or link.transactions.filter(customer_payment__isnull=False).exists()):
            _flag(txn, "a failure on a link already paid: check by hand, nothing reversed")
            return "flagged_after_paid"
        return "failed"
    # confirmed, and nothing recorded yet: the one transition that moves money
    txn.outcome, txn.payload, txn.amount_cents, txn.currency = TransactionOutcome.CONFIRMED, callback.raw, callback.amount_cents, callback.currency[:3]
    if link is None:
        _flag(txn, "confirmed for an order no link of ours made: recorded and ignored")
        return "unknown_order"
    if txn.link_id is None:
        txn.link = link
    if callback.currency.upper() != link.currency:
        _flag(txn, f"currency {callback.currency} is not taken: nothing recorded")
        return "currency"
    if int(callback.amount_cents) != to_cents(link.amount):
        # Codex #315 P1: the gateway's order was made for exactly the link's amount; anything else is a person's job.
        _flag(txn, f"confirmed {from_cents(callback.amount_cents)} for a link of {link.amount}: nothing recorded, check by hand")
        return "amount_mismatch"
    return _post(provider, txn, link, callback)


def _post(provider, txn, link, callback):
    from sales.services import record_customer_payment

    amount = from_cents(callback.amount_cents)
    if amount <= 0:
        _flag(txn, "confirmed with no amount: nothing recorded")
        return "no_amount"
    already_paid = link.status in (LinkStatus.PAID, LinkStatus.MISMATCH)
    try:
        with transaction.atomic():
            payment = record_customer_payment(f"{provider.payment_prefix}-{callback.transaction_id}", timezone.localdate(), link.customer, link.cashbox,
                                              amount, None, notes=f"Online payment link {link.reference} — invoice {link.invoice.invoice_number}")
            txn.customer_payment = payment
            txn.save()
            settled = _settle(link, payment, amount)
            if not already_paid:
                link.status = LinkStatus.PAID  # the amount equals the link's: a different one never reaches here
                link.paid_amount = amount
                link.save(update_fields=["status", "paid_amount", "updated_at"])
    except (ValidationError, IntegrityError) as error:
        message = "; ".join(getattr(error, "messages", [str(error)]))
        txn.customer_payment = None
        _flag(txn, f"could not record the payment: {message}")
        return "posting_failed"
    if already_paid:
        _flag(txn, "a second confirmed payment on a link already paid: recorded on account")
    return "posted" if settled == amount else f"posted_settled_{settled}"


def _settle(link, payment, amount):
    from sales.models import SalesInvoice

    # The invoice's row lock, as gift-card redemption takes it, so a link and a card cannot both settle the same due.
    invoice = SalesInvoice.objects.select_for_update(of=("self",)).get(pk=link.invoice_id)
    room = outstanding(invoice)
    settled = min(amount, room) if invoice.status == "posted" else ZERO
    if settled > 0:
        settlements.settle(invoice, payment, settled, SettlementSource.PAYMENT_LINK)
    return max(settled, ZERO)


# ---- the gateway's payout ------------------------------------------------------------------


@transaction.atomic
def book_payout(gateway, bank, net, fee, user, reference="", lang="ar", day=None):
    """Paymob paid out: the net goes from the gateway account to the real bank, the fee is a bank charge (5212)."""

    from cashboxes.models import CashboxKind, CashboxOperationType, HELD_KINDS
    from cashboxes.services import create_cashbox_operation

    words = MESSAGES[lang]
    _require(user, MOVE_CASH)
    if (gateway is None or bank is None or gateway.pk == bank.pk or gateway.kind != CashboxKind.BANK
            or not gateway.name_ar.startswith(GATEWAY_BOX_NAME) or bank.kind in HELD_KINDS or gateway.entity_id != bank.entity_id):
        raise ValidationError(words["payout_box"])
    try:
        net = money_round(Decimal(str(net or "0").strip().replace(",", "")))
        fee = money_round(Decimal(str(fee or "0").strip().replace(",", "")))
    except (InvalidOperation, ValueError):
        raise ValidationError(words["payout_amount"])
    if net < 0 or fee < 0 or net + fee <= 0:
        raise ValidationError(words["payout_amount"])
    day = day or timezone.localdate()
    reference = (reference or "").strip()[:60]
    if not reference:  # the gateway's payout reference is what stops the same payout being booked twice
        raise ValidationError(words["payout_ref"])
    # ``CashboxOperation.reference_number`` is unique: the transfer and the fee each get their own.
    base = f"GW-{reference}"
    from cashboxes.models import CashboxOperation

    if CashboxOperation.objects.filter(reference_number__in=(f"{base}-NET", f"{base}-FEE")).exists():
        raise ValidationError(words["payout_twice"].format(ref=reference))
    operations = []
    if net > 0:
        operations.append(create_cashbox_operation(f"{base}-NET", day, CashboxOperationType.TRANSFER, net, words["payout_reason"].format(ref=reference).strip(),
                                                   user, source_cashbox=gateway, destination_cashbox=bank))
    if fee > 0:
        operations.append(create_cashbox_operation(f"{base}-FEE", day, CashboxOperationType.BANK_FEE, fee, words["fee_reason"].format(ref=reference).strip(),
                                                   user, source_cashbox=gateway))
    _audit("book_payout", "cashboxes.Cashbox", gateway.pk, {"bank": bank.pk, "net": str(net), "fee": str(fee), "reference": reference,
                                                            "operations": [op.pk for op in operations]}, user=user, event=AuditEventType.CREATE)
    return operations
