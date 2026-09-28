"""SHIFT-001: open, summarise and close a cashier shift; post an agreed difference."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement
from config.money import money_round

from .models import Shift, ShiftStatus


ZERO = Decimal("0.00")
MESSAGES = {
    "ar": {"already_open": "عندك وردية مفتوحة بالفعل.", "not_open": "الوردية دي مقفولة.", "amount": "المبلغ لازم رقم مش سالب.",
           "no_difference": "مفيش فرق يتسجل.", "posted": "الفرق اتسجل قبل كده."},
    "en": {"already_open": "You already have an open shift.", "not_open": "This shift is closed.", "amount": "The amount must be a number, not negative.",
           "no_difference": "There is no difference to post.", "posted": "The difference was already posted."},
}
GROUPS = {
    "sales_receipt": "sales", "sales_return": "refunds", "customer_payment": "collections", "supplier_payment": "supplier_payments",
    "purchase_payment": "purchases", "purchase_return": "purchase_refunds", "direct_in": "cash_in", "direct_out": "cash_out",
}


def open_shift_for(user):
    return Shift.objects.filter(cashier=user, status=ShiftStatus.OPEN).select_related("cashbox").first()


def _audit(shift, user, action, after):
    AuditLog.objects.create(event_type=AuditEventType.CREATE if action == "open_shift" else AuditEventType.UPDATE, actor=user, module="shifts",
                            action=action, object_type="shifts.Shift", object_id=str(shift.pk), before_data={}, after_data=after)


@transaction.atomic
def open_shift(user, cashbox, opening_float, lang="ar"):
    words = MESSAGES[lang]
    opening_float = money_round(Decimal(opening_float))
    if opening_float < 0:
        raise ValidationError(words["amount"])
    if open_shift_for(user):
        raise ValidationError(words["already_open"])
    try:
        with transaction.atomic():
            shift = Shift.objects.create(cashier=user, cashbox=cashbox, opened_at=timezone.now(), opening_float=opening_float)
    except IntegrityError:
        raise ValidationError(words["already_open"])
    _audit(shift, user, "open_shift", {"cashbox": cashbox.pk, "opening_float": str(opening_float)})
    return shift


def movements(shift, until=None):
    queryset = CashboxMovement.objects.filter(cashbox=shift.cashbox, created_by=shift.cashier, created_at__gte=shift.opened_at)
    # A posted shift difference belongs to the shift it settles, not to whoever posts it.
    queryset = queryset.exclude(cashbox_operation__reference_number__startswith="SHIFT-")
    end = until or shift.closed_at
    if end is not None:
        queryset = queryset.filter(created_at__lte=end)
    return queryset


def summary(shift, until=None):
    """Cash in and out by kind during the shift, and the cash that should be in the drawer."""

    groups = defaultdict(lambda: ZERO)
    cash_in = cash_out = ZERO
    for row in movements(shift, until).values("movement_type", "direction").annotate(total=Sum("amount")):
        amount = row["total"] or ZERO
        group = GROUPS.get(row["movement_type"], "other")
        if row["direction"] == CashboxDirection.IN:
            cash_in += amount
            groups[group] += amount
        else:
            cash_out += amount
            groups[group] -= amount
    invoices = movements(shift, until).filter(sales_invoice__isnull=False).values("sales_invoice").distinct().count()
    return {
        "groups": {key: str(money_round(value)) for key, value in groups.items()},
        "cash_in": str(money_round(cash_in)), "cash_out": str(money_round(cash_out)), "invoices": invoices,
        "expected": str(money_round(shift.opening_float + cash_in - cash_out)),
    }


@transaction.atomic
def close_shift(shift, counted_cash, user, notes="", lang="ar"):
    words = MESSAGES[lang]
    shift = Shift.objects.select_for_update().get(pk=shift.pk)
    if shift.status != ShiftStatus.OPEN:
        raise ValidationError(words["not_open"])
    counted_cash = money_round(Decimal(counted_cash))
    if counted_cash < 0:
        raise ValidationError(words["amount"])
    now = timezone.now()
    data = summary(shift, now)
    shift.closed_at, shift.status, shift.summary = now, ShiftStatus.CLOSED, data
    shift.expected_cash = Decimal(data["expected"])
    shift.counted_cash = counted_cash
    shift.difference = money_round(counted_cash - shift.expected_cash)
    shift.notes = (notes or "").strip()[:255]
    shift.save()
    _audit(shift, user, "close_shift", {"expected": data["expected"], "counted": str(counted_cash), "difference": str(shift.difference)})
    return shift


@transaction.atomic
def post_difference(shift, user, lang="ar"):
    """Record a closed shift's shortage (cash out) or overage (cash in) in the cashbox."""

    from cashboxes.models import CashboxOperationType
    from cashboxes.services import create_cashbox_operation

    words = MESSAGES[lang]
    shift = Shift.objects.select_for_update().get(pk=shift.pk)
    if shift.status != ShiftStatus.CLOSED:
        raise ValidationError(words["not_open"])
    if shift.difference_operation_id:
        raise ValidationError(words["posted"])
    if not shift.difference:
        raise ValidationError(words["no_difference"])
    shortage = shift.difference < 0
    reason = f"Shift {shift.pk} {'shortage' if shortage else 'overage'} ({shift.cashier.get_username()})"
    kwargs = {"source_cashbox": shift.cashbox} if shortage else {"destination_cashbox": shift.cashbox}
    shift.difference_operation = create_cashbox_operation(f"SHIFT-{shift.pk}-DIFF", timezone.localdate(), CashboxOperationType.DIRECT_OUT if shortage else CashboxOperationType.DIRECT_IN,
                                                          abs(shift.difference), reason, user, **kwargs)
    shift.save(update_fields=["difference_operation"])
    _audit(shift, user, "post_shift_difference", {"difference": str(shift.difference), "operation": shift.difference_operation.reference_number})
    return shift
