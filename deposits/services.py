"""HG-069 (S5): take, apply, forfeit and refund a deposit; the cancellation policy.

Money moves only through the sales engine, called and not changed:

* **take** = ``record_customer_payment`` on account (customer due −D, cashbox or bank +D, the day it is taken);
* **apply** = when the service invoice posts, an ``InvoiceSettlement`` row (the invoice's own figures stay frozen);
* **forfeit** = a posted «رسوم إلغاء» invoice for a non-stock service item, with VAT *inside* the forfeited amount,
  settled by the deposit, so the customer owes nothing after it;
* **refund** = ``cancel_customer_payment`` (HG-068 can hold it for a second person); refused when the deposit's
  month has closed, because that service reverses on the payment's own date.

The policy is one setting per business: a cancellation closer to the booking than ``cancel_hours`` (or a no-show)
forfeits the deposit; earlier than that it is refunded.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.signals import post_save
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission
from settlements import services as settlements
from settlements.models import SettlementSource

from .models import Deposit, DepositStatus

ZERO = Decimal("0.00")
PIASTRE = Decimal("0.01")
HUNDRED = Decimal("100")
SETTING_KEY = "deposits.cancel_hours"
DEFAULT_HOURS = 24
MAX_HOURS = 24 * 30
FEE_ITEM_CODE = "CANCEL-FEE"
RECEIVE = "sales.receive_customer_payment"  # to take and to refund, as for any collection
SELL = "sales.create_sales_invoice"  # to forfeit: it posts a sale

MESSAGES = {
    "ar": {
        "amount": "مبلغ العربون مش صحيح.", "cashbox": "اختار الخزنة أو الحساب البنكي اللي استلم العربون.", "walk_in": "العميل النقدي مينفعش يدفع عربون؛ اختار عميل باسمه.",
        "already": "الحجز ده عليه عربون بالفعل.", "status": "العربون يتاخد على حجز لسه محجوز أو مؤكد بس.", "not_held": "العربون ده اتصرف فيه خلاص ({status}).",
        "closed_month": "العربون اتاخد في شهر اتقفل، فمينفعش يترد على تاريخه. يا تتعمل له فاتورة رسوم إلغاء بتاريخ النهارده، يا يفضل رصيد للعميل.",
        "off": "العربون مقفول؛ شغّله من الإعدادات ← الخصائص («العربون وسياسة الإلغاء»).",
        "setup": "لازم يكون فيه مخزن بيع نشط.", "hours": "عدد الساعات لازم يكون رقم من 0 لـ {max}.", "reason": "اكتب سبب الرد.",
        "fee_item": "رسوم إلغاء", "fee_notes": "رسوم إلغاء حجز {number} — العربون {payment}", "deposit_notes": "عربون حجز {number}",
        "statuses": {"held": "محجوز", "applied": "اتخصم من الفاتورة", "forfeited": "اتحوّل لرسوم إلغاء", "refunded": "اترد"},
    },
    "en": {
        "amount": "Invalid deposit amount.", "cashbox": "Choose the cashbox or bank account that received the deposit.", "walk_in": "The walk-in customer cannot pay a deposit; choose a named customer.",
        "already": "This booking already holds a deposit.", "status": "A deposit is taken only on a booking that is still booked or confirmed.", "not_held": "This deposit was already settled ({status}).",
        "closed_month": "The deposit was taken in a month that has closed, so it cannot be refunded on its date. Either post a cancellation-fee invoice dated today, or leave it as the customer's credit.",
        "off": "Deposits are switched off; switch them on from Settings → Features (“Deposits & cancellation policy”).",
        "setup": "An active selling location is needed.", "hours": "The hours must be a number from 0 to {max}.", "reason": "Enter the refund reason.",
        "fee_item": "Cancellation fee", "fee_notes": "Cancellation fee for booking {number} — deposit {payment}", "deposit_notes": "Deposit for booking {number}",
        "statuses": {"held": "Held", "applied": "Applied to the invoice", "forfeited": "Forfeited as a cancellation fee", "refunded": "Refunded"},
    },
}


CAPABILITY = "deposits"
NUMBER_ATTEMPTS = 5


def enabled():
    """Whether the «العربون» capability is at work (CAP-001). Off: no new deposit and no deposit screen. What follows the
    sales engine (apply on posting, release on cancelling, refund on a cancelled payment) runs whatever the switch says,
    so a deposit already held keeps being booked right."""

    from settings_core.capabilities import capability_enabled

    return capability_enabled(CAPABILITY)


def status_label(deposit, lang="ar"):
    return MESSAGES[lang]["statuses"].get(deposit.status, deposit.status)


# ---- the policy setting ----------------------------------------------------------


def cancel_hours():
    """Hours before the booking under which a cancellation forfeits the deposit (default 24)."""

    from settings_core.models import SystemSetting

    raw = SystemSetting.objects.filter(key=SETTING_KEY, active=True).values_list("value", flat=True).first()
    try:
        return max(0, min(int(raw), MAX_HOURS)) if raw not in (None, "") else DEFAULT_HOURS
    except (TypeError, ValueError):
        return DEFAULT_HOURS


def set_cancel_hours(hours, user, lang="ar"):
    """Only the account owner sets the policy."""

    from sales.short_sales import is_owner
    from settings_core.models import SystemSetting

    if not is_owner(user):
        raise PermissionDenied("Only the account owner sets the cancellation policy.")
    try:
        hours = int(str(hours).strip())
    except (TypeError, ValueError):
        raise ValidationError(MESSAGES[lang]["hours"].format(max=MAX_HOURS))
    if not 0 <= hours <= MAX_HOURS:
        raise ValidationError(MESSAGES[lang]["hours"].format(max=MAX_HOURS))
    before = cancel_hours()
    SystemSetting.objects.update_or_create(key=SETTING_KEY, defaults={
        "value": str(hours), "data_type": SystemSetting.DataType.INTEGER, "active": True,
        "description": "HG-069: a cancellation closer than this many hours to the booking forfeits the deposit."})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="deposits", action="set_cancel_hours",
                            object_type="SystemSetting", object_id=SETTING_KEY, before_data={"hours": before}, after_data={"hours": hours})
    return hours


def is_late(target, now=None):
    """Whether cancelling ``target`` now forfeits the deposit: a no-show, or inside the policy window."""

    if getattr(target, "status", "") == "no_show":
        return True
    starts_at = getattr(target, "starts_at", None)
    if starts_at is None:
        return False
    return (now or timezone.now()) >= starts_at - timedelta(hours=cancel_hours())


def policy(target, now=None):
    return "forfeit" if is_late(target, now) else "refund"


# ---- reading ---------------------------------------------------------------------


def _type(target):
    return ContentType.objects.get_for_model(target.__class__)


def for_target(target):
    return Deposit.objects.filter(content_type=_type(target), object_id=target.pk).select_related("payment", "invoice")


def held_for(target):
    return for_target(target).filter(status=DepositStatus.HELD).first()


def latest_for(target):
    """The deposit the booking's screen shows: the held one, else the last one settled."""

    return for_target(target).order_by("-pk").first()


def held_amounts(targets):
    """{target pk: held deposit amount} for a list of bookings of one model (the agenda)."""

    targets = list(targets)
    if not targets:
        return {}
    rows = Deposit.objects.filter(content_type=_type(targets[0]), object_id__in=[t.pk for t in targets], status=DepositStatus.HELD).select_related("payment")
    return {row.object_id: row.payment.amount for row in rows}


# ---- helpers ---------------------------------------------------------------------


def _require(user, code):
    if not user_has_permission(user, code):
        raise PermissionDenied(f"Deposits need {code}.")


def _audit(deposit, user, action, after, before=None, reason=""):
    AuditLog.objects.create(event_type=AuditEventType.CREATE if action == "take_deposit" else AuditEventType.UPDATE, actor=user, module="deposits",
                            action=action, object_type="deposits.Deposit", object_id=str(deposit.pk), reason=reason, before_data=before or {}, after_data=after)


def _amount(value, words):
    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", ""))
    except InvalidOperation:
        raise ValidationError(words["amount"])
    if not number.is_finite() or number <= 0:
        raise ValidationError(words["amount"])
    return money_round(number)


def _next_payment_number(day):
    """The next free ``DEP-YYYYMMDD-NNN`` (the highest used + 1, AUTONUM's ``next_in_series``)."""

    from config.numbering import next_in_series
    from sales.models import CustomerPayment

    return next_in_series(CustomerPayment, "payment_number", f"DEP-{day:%Y%m%d}-", width=3)


def _record_payment(day, customer, cashbox, amount, user, notes):
    """``record_customer_payment`` under a fresh number. Two deposits taken in the same instant can compute the same
    number; the second then hits the UNIQUE ``payment_number``, its savepoint rolls back and it takes the next one."""

    from sales.models import CustomerPayment
    from sales.services import record_customer_payment

    for attempt in range(NUMBER_ATTEMPTS):
        number = _next_payment_number(day)
        try:
            with transaction.atomic():
                return record_customer_payment(number, day, customer, cashbox, amount, user, notes=notes)
        except IntegrityError:
            if attempt == NUMBER_ATTEMPTS - 1 or not CustomerPayment.objects.filter(payment_number=number).exists():
                raise


def _unique_invoice_number(base):
    from sales.models import SalesInvoice

    number, suffix = base, 1
    while SalesInvoice.objects.filter(invoice_number=number).exists():
        suffix += 1
        number = f"{base}-{suffix}"
    return number


def _lock(deposit):
    # ``of=self``: PostgreSQL refuses FOR UPDATE on the nullable side of the outer join to ``invoice``.
    return Deposit.objects.select_for_update(of=("self",)).select_related("payment", "payment__customer", "invoice").get(pk=deposit.pk)


def _selling_location(words):
    from entities import scope as entity_scope
    from master_data.models import Location

    location = entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()
    if location is None:
        raise ValidationError(words["setup"])
    return location


# ---- take ------------------------------------------------------------------------


@transaction.atomic
def take(target, customer, cashbox, amount, user, lang="ar", payment_date=None, notes=""):
    """Take a deposit on ``target`` for ``customer``: a customer payment on account, linked to the booking."""

    from sales.pos import WALK_IN_CODE

    words = MESSAGES[lang]
    if not enabled():
        raise ValidationError(words["off"])
    _require(user, RECEIVE)
    amount = _amount(amount, words)
    if cashbox is None:
        raise ValidationError(words["cashbox"])
    if customer is None or customer.customer_code == WALK_IN_CODE:
        raise ValidationError(words["walk_in"])
    target = target.__class__.objects.select_for_update().get(pk=target.pk)
    if getattr(target, "status", "booked") not in ("booked", "confirmed"):
        raise ValidationError(words["status"])
    if held_for(target) is not None:
        raise ValidationError(words["already"])
    day = payment_date or timezone.localdate()
    payment = _record_payment(day, customer, cashbox, amount, user, notes or words["deposit_notes"].format(number=getattr(target, "number", target.pk)))
    deposit = Deposit.objects.create(payment=payment, content_type=_type(target), object_id=target.pk, created_by=user)
    _audit(deposit, user, "take_deposit", {"payment": payment.payment_number, "amount": str(amount), "target": f"{deposit.content_type.app_label}.{deposit.content_type.model}:{target.pk}"})
    return deposit


# ---- apply -----------------------------------------------------------------------


def attach(target, invoice):
    """Point the booking's held deposit at the draft invoice that will settle it, so posting applies it."""

    deposit = held_for(target)
    if deposit is None or deposit.payment.customer_id != invoice.customer_id:
        return None
    deposit.invoice = invoice
    deposit.save(update_fields=["invoice", "updated_at"])
    return deposit


@transaction.atomic
def apply(invoice):
    """When a draft that holds deposits posts: settle it with each held deposit, up to what the invoice still asks for.

    Called from the posting audit row (inside the posting's transaction). A deposit larger than the invoice settles
    it in full and the rest stays as the customer's credit on account; one too small settles its own amount.
    """

    applied = []
    for deposit in Deposit.objects.select_for_update().filter(invoice=invoice, status=DepositStatus.HELD).select_related("payment").order_by("pk"):
        room = Decimal(invoice.remaining_due) - settlements.applied_to(invoice)
        amount = min(deposit.payment.amount, room)
        if amount <= 0:
            continue
        settlements.settle(invoice, deposit.payment, amount, SettlementSource.DEPOSIT)
        deposit.status = DepositStatus.APPLIED
        deposit.save(update_fields=["status", "updated_at"])
        _audit(deposit, None, "apply_deposit", {"invoice": invoice.invoice_number, "amount": str(amount)}, {"status": DepositStatus.HELD})
        applied.append(deposit)
    return applied


@transaction.atomic
def release(invoice):
    """The invoice a deposit settled was cancelled: the settlement row goes, the deposit is held again (2105)."""

    settlements.release(invoice)
    released = []
    for deposit in Deposit.objects.select_for_update().filter(invoice=invoice, status__in=(DepositStatus.APPLIED, DepositStatus.FORFEITED)):
        before = {"status": deposit.status, "invoice": invoice.invoice_number}
        deposit.status, deposit.invoice, deposit.rounding_credit = DepositStatus.HELD, None, ZERO
        deposit.save(update_fields=["status", "invoice", "rounding_credit", "updated_at"])
        _audit(deposit, None, "release_deposit", {"status": DepositStatus.HELD}, before)
        released.append(deposit)
    return released


# ---- forfeit ---------------------------------------------------------------------


def fee_line(amount, rate):
    """(net, total) for one tax-exclusive line whose total with VAT at ``rate`` is ``amount``, or the nearest below it.

    ``compute_lines`` rounds the tax half-up to the piastre from the net, so the net is back-computed and checked;
    when no net reaches the amount exactly, the best total *below* it wins (one or two piastres less at most) and the
    difference stays as the customer's credit. The fee is never above the deposit.
    """

    amount = money_round(amount)
    rate = Decimal(rate or 0)
    if rate <= 0:
        return amount, amount
    base = money_round(amount / (1 + rate / HUNDRED))
    best = None
    for step in (0, -1, 1, -2, -3, -4):
        net = base + PIASTRE * step
        if net <= 0:
            continue
        total = net + money_round(net * rate / HUNDRED)
        if total == amount:
            return net, total
        if total < amount and (best is None or total > best[1]):
            best = (net, total)
    return best if best is not None else (ZERO, ZERO)


def fee_item(lang="ar"):
    """The non-stock service item the cancellation fee is billed on (made once)."""

    from master_data.models import Item

    item = Item.objects.filter(item_code=FEE_ITEM_CODE).first()
    if item is None:
        item = Item.objects.create(item_code=FEE_ITEM_CODE, item_name=MESSAGES["ar"]["fee_item"], is_stock_tracked=False, unit="service")
    elif not item.active:
        item.active = True
        item.save(update_fields=["active"])
    return item


def fee_rate(item):
    from taxes.services import rate_for, vat_enabled

    if not vat_enabled():
        return ZERO
    rate = rate_for(item)
    return Decimal(rate.rate) if rate is not None else ZERO


@transaction.atomic
def forfeit(deposit, user, lang="ar", reason=""):
    """Late cancellation: post a «رسوم إلغاء» invoice equal to the deposit (VAT inside) and settle it with the deposit."""

    from sales.services import post_sales_invoice
    from taxes.services import create_sales_draft_with_tax

    words = MESSAGES[lang]
    _require(user, SELL)
    deposit = _lock(deposit)
    if deposit.status != DepositStatus.HELD:
        raise ValidationError(words["not_held"].format(status=status_label(deposit, lang)))
    location = _selling_location(words)
    item = fee_item(lang)
    amount = deposit.payment.amount
    net, total = fee_line(amount, fee_rate(item))
    if total <= 0:
        raise ValidationError(words["amount"])
    target_number = getattr(deposit.target, "number", deposit.object_id)
    invoice = create_sales_draft_with_tax(
        {"invoice_number": _unique_invoice_number(f"FEE-{deposit.payment.payment_number}"), "invoice_date": timezone.localdate(),
         "customer": deposit.payment.customer, "selling_location": location, "cashbox": None, "discount_amount": ZERO, "tax_amount": ZERO,
         "paid_now": ZERO, "notes": words["fee_notes"].format(number=target_number, payment=deposit.payment.payment_number)},
        [{"item": item, "quantity": Decimal("1"), "unit_sale_price": net, "line_discount_amount": ZERO, "description": words["fee_item"]}],
        user,
    )
    post_sales_invoice(invoice.pk, user)
    invoice.refresh_from_db()
    if invoice.total_amount != total or invoice.total_amount > amount:  # the two roundings must agree; never bill above the deposit
        raise ValidationError(words["amount"])
    settlements.settle(invoice, deposit.payment, invoice.total_amount, SettlementSource.DEPOSIT)
    deposit.status, deposit.invoice, deposit.rounding_credit = DepositStatus.FORFEITED, invoice, money_round(amount - invoice.total_amount)
    deposit.save(update_fields=["status", "invoice", "rounding_credit", "updated_at"])
    _audit(deposit, user, "forfeit_deposit", {"invoice": invoice.invoice_number, "total": str(invoice.total_amount), "net": str(net),
                                               "tax": str(invoice.tax_amount), "rounding_credit": str(deposit.rounding_credit)}, {"status": DepositStatus.HELD}, reason)
    return invoice


# ---- refund ----------------------------------------------------------------------


def refund_blocker(deposit, lang="ar"):
    """Why a refund cannot run now (None when it can): the deposit's month has closed (HG-011 reverses on its date)."""

    from closing.models import PeriodStatus
    from closing.services import get_period_for_date

    period = get_period_for_date(deposit.payment.payment_date)
    if period is not None and period.status not in (PeriodStatus.OPEN, PeriodStatus.REOPENED):
        return MESSAGES[lang]["closed_month"]
    return None


@transaction.atomic
def refund(deposit, user, reason, lang="ar"):
    """Early cancellation: cancel the deposit's payment (customer due +D, cashbox −D on its date). The screen gates it (HG-068)."""

    from sales.services import cancel_customer_payment

    words = MESSAGES[lang]
    _require(user, RECEIVE)
    if not (reason or "").strip():
        raise ValidationError(words["reason"])
    deposit = _lock(deposit)
    if deposit.status != DepositStatus.HELD:
        raise ValidationError(words["not_held"].format(status=status_label(deposit, lang)))
    blocker = refund_blocker(deposit, lang)
    if blocker:
        raise ValidationError(blocker)
    cancel_customer_payment(deposit.payment_id, user, reason)
    deposit.refresh_from_db()
    if deposit.status == DepositStatus.HELD:  # the audit hook marks it; this is the belt to its braces
        _mark_refunded(deposit, user)
    return deposit


def _mark_refunded(deposit, user=None):
    before = deposit.status
    deposit.status = DepositStatus.REFUNDED
    deposit.save(update_fields=["status", "updated_at"])
    _audit(deposit, user, "refund_deposit", {"status": DepositStatus.REFUNDED, "payment": deposit.payment.payment_number}, {"status": before})


# ---- following the sales engine ----------------------------------------------------


def _on_audit(sender, instance, created, **kwargs):
    """Posting applies attached deposits; cancelling an invoice releases them; cancelling the payment refunds."""

    if not created or instance.module != "sales":
        return
    if instance.action == "post_sales_invoice":
        from sales.models import SalesInvoice

        if Deposit.objects.filter(invoice_id=int(instance.object_id), status=DepositStatus.HELD).exists():
            apply(SalesInvoice.objects.get(pk=int(instance.object_id)))
    elif instance.action == "cancel_posted_sales_invoice":
        from sales.models import SalesInvoice

        from settlements.models import InvoiceSettlement

        if InvoiceSettlement.objects.filter(invoice_id=int(instance.object_id)).exists() or Deposit.objects.filter(invoice_id=int(instance.object_id)).exists():
            release(SalesInvoice.objects.get(pk=int(instance.object_id)))
    elif instance.action == "cancel_customer_payment":
        deposit = Deposit.objects.filter(payment_id=int(instance.object_id), status=DepositStatus.HELD).select_related("payment").first()
        if deposit is not None:
            _mark_refunded(deposit, instance.actor)


def connect():
    post_save.connect(_on_audit, sender=AuditLog, dispatch_uid="deposits-follow-the-sales-engine", weak=False)
