from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from reports.selectors import cashbox_report, customer_report, profit_report, stock_report, supplier_report
from .models import (
    ClosingFrequency,
    ClosingRun,
    ClosingRunStatus,
    Period,
    PeriodStatus,
    PeriodSummary,
    PostClosingAdjustment,
    PostClosingAdjustmentStatus,
)


def get_period_for_date(action_date):
    return Period.objects.filter(start_date__lte=action_date, end_date__gte=action_date).order_by("-start_date").first()


MONTH_NAMES_AR = (
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)


def _month_bounds(action_date):
    first = action_date.replace(day=1)
    next_first = (first.replace(year=first.year + 1, month=1) if first.month == 12 else first.replace(month=first.month + 1))
    return first, next_first - timedelta(days=1)


@transaction.atomic
def provision_period_for(action_date, user=None):
    """Open the calendar-month period that covers ``action_date`` (HG-010).

    A shop should never have to create accounting periods by hand before it can
    record cash. When no period covers a date, this opens one for that calendar
    month, trimmed so it never overlaps an existing period.

    It refuses a date on or before the end of the latest closed period (those
    books are shut, and a new period there would reopen them by the back door)
    and a date after the current month (a future period is opened on purpose).
    Returns the period covering the date (existing or new).
    """

    existing = get_period_for_date(action_date)
    if existing is not None:
        return existing
    if action_date > _month_bounds(timezone.localdate())[1]:
        raise ValidationError("No period found for this date, and future months are not opened automatically.")
    last_closed = Period.objects.filter(status=PeriodStatus.CLOSED).order_by("-end_date").first()
    if last_closed is not None and action_date <= last_closed.end_date:
        raise ValidationError("This date falls inside closed books; no new period can be opened there.")

    start, end = _month_bounds(action_date)
    before = Period.objects.filter(end_date__gte=start, end_date__lt=action_date).order_by("-end_date").first()
    after = Period.objects.filter(start_date__lte=end, start_date__gt=action_date).order_by("start_date").first()
    if before is not None:
        start = before.end_date + timedelta(days=1)
    if after is not None:
        end = after.start_date - timedelta(days=1)

    code = f"{action_date:%Y-%m}"
    suffix = 1
    while Period.objects.filter(period_code=code).exists():
        suffix += 1
        code = f"{action_date:%Y-%m}-{suffix}"
    period = Period(
        period_code=code,
        name=f"{MONTH_NAMES_AR[action_date.month - 1]} {action_date.year} / {action_date:%B %Y}",
        frequency=ClosingFrequency.MONTHLY,
        start_date=start,
        end_date=end,
        status=PeriodStatus.OPEN,
        notes="Opened automatically for the first entry dated in this month.",
    )
    period.full_clean()
    period.save()
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE,
        actor=user if user is not None and getattr(user, "is_authenticated", False) else None,
        module="closing",
        action="auto_open_period",
        object_type="Period",
        object_id=str(period.id),
        reason=f"First entry dated {action_date.isoformat()}",
        after_data={"period_code": period.period_code, "start_date": start.isoformat(), "end_date": end.isoformat(), "status": period.status},
    )
    return period


def ensure_period_is_open(action_date):
    period = get_period_for_date(action_date)
    if period is None:
        period = provision_period_for(action_date)
    # HG-011: reopening exists to correct a period, so a reopened period takes
    # entries like an open one until it is closed again.
    if period.status not in (PeriodStatus.OPEN, PeriodStatus.REOPENED):
        raise ValidationError("Period must be open for posting.")
    return period


def _decimal_sum(values):
    total = Decimal("0")
    for value in values:
        total += value or Decimal("0")
    return total


def _next_run_number(period):
    last_run = period.closing_runs.order_by("-run_number").first()
    if last_run is None:
        return 1
    return last_run.run_number + 1


def build_period_summary_payload(period):
    from purchases.models import PurchaseInvoice
    from sales.models import SalesInvoice

    posted_sales = SalesInvoice.objects.filter(
        status="posted",
        invoice_date__gte=period.start_date,
        invoice_date__lte=period.end_date,
    )
    posted_purchases = PurchaseInvoice.objects.filter(
        status="posted",
        invoice_date__gte=period.start_date,
        invoice_date__lte=period.end_date,
    )

    sales_total = _decimal_sum(posted_sales.values_list("total_amount", flat=True))
    purchase_total = _decimal_sum(posted_purchases.values_list("total_amount", flat=True))
    profit_total = _decimal_sum(row["profit_amount"] for row in profit_report(period.start_date, period.end_date))
    stock_value_total = _decimal_sum(row["stock_value"] for row in stock_report())
    customer_balance_total = _decimal_sum(row["balance"] for row in customer_report())
    supplier_balance_total = _decimal_sum(row["balance"] for row in supplier_report())
    cashbox_balance_total = _decimal_sum(row["balance"] for row in cashbox_report())

    return [
        {"code": "sales_total", "name": "Sales total", "amount": sales_total, "quantity": Decimal("0")},
        {"code": "purchase_total", "name": "Purchase total", "amount": purchase_total, "quantity": Decimal("0")},
        {"code": "profit_total", "name": "Profit total", "amount": profit_total, "quantity": Decimal("0")},
        {"code": "stock_value_total", "name": "Stock value total", "amount": stock_value_total, "quantity": Decimal("0")},
        {"code": "customer_balance_total", "name": "Customer balance total", "amount": customer_balance_total, "quantity": Decimal("0")},
        {"code": "supplier_balance_total", "name": "Supplier balance total", "amount": supplier_balance_total, "quantity": Decimal("0")},
        {"code": "cashbox_balance_total", "name": "Cashbox balance total", "amount": cashbox_balance_total, "quantity": Decimal("0")},
    ]


@transaction.atomic
def complete_period_closing(period_id, user=None, reason=""):
    period = Period.objects.select_for_update().get(pk=period_id)
    if period.status == PeriodStatus.CLOSED:
        raise ValidationError("Period is already closed.")

    closing_run = ClosingRun.objects.create(
        period=period,
        run_number=_next_run_number(period),
        status=ClosingRunStatus.COMPLETED,
        completed_at=timezone.now(),
        reason=reason,
        created_by=user,
    )

    for item in build_period_summary_payload(period):
        PeriodSummary.objects.create(
            period=period,
            closing_run=closing_run,
            summary_code=item["code"],
            summary_name=item["name"],
            amount=item["amount"],
            quantity=item["quantity"],
            metadata={"source": "060_closing_service"},
        )

    period.status = PeriodStatus.CLOSED
    period.closed_at = closing_run.completed_at
    period.closed_by = user
    period.save(update_fields=["status", "closed_at", "closed_by", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.CLOSING,
        actor=user,
        module="closing",
        action="complete_period_closing",
        object_type="Period",
        object_id=str(period.id),
        reason=reason,
        after_data={"period_code": period.period_code, "status": period.status, "run_number": closing_run.run_number},
    )

    return closing_run


@transaction.atomic
def reopen_period(period_id, user=None, reason=""):
    if not reason:
        raise ValidationError("Reopen reason is required.")

    period = Period.objects.select_for_update().get(pk=period_id)
    if period.status != PeriodStatus.CLOSED:
        raise ValidationError("Only closed periods can be reopened.")

    period.status = PeriodStatus.REOPENED
    period.reopened_at = timezone.now()
    period.reopened_by = user
    period.reopen_reason = reason
    period.save(update_fields=["status", "reopened_at", "reopened_by", "reopen_reason", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.REOPENING,
        actor=user,
        module="closing",
        action="reopen_period",
        object_type="Period",
        object_id=str(period.id),
        reason=reason,
        after_data={"period_code": period.period_code, "status": period.status},
    )

    return period


@transaction.atomic
def create_post_closing_adjustment(adjustment_number, related_closed_period, adjustment_date, reason, user=None, notes=""):
    if related_closed_period.status != PeriodStatus.CLOSED:
        raise ValidationError("Post-closing adjustment must reference a closed period.")
    ensure_period_is_open(adjustment_date)

    adjustment = PostClosingAdjustment.objects.create(
        adjustment_number=adjustment_number,
        related_closed_period=related_closed_period,
        adjustment_date=adjustment_date,
        reason=reason,
        notes=notes,
        created_by=user,
    )
    adjustment.full_clean()

    AuditLog.objects.create(
        event_type=AuditEventType.ADJUSTMENT,
        actor=user,
        module="closing",
        action="create_post_closing_adjustment",
        object_type="PostClosingAdjustment",
        object_id=str(adjustment.id),
        reason=reason,
        after_data={
            "adjustment_number": adjustment.adjustment_number,
            "related_closed_period_id": adjustment.related_closed_period_id,
            "status": adjustment.status,
        },
    )

    return adjustment


@transaction.atomic
def post_closing_adjustment(adjustment_id, user=None):
    adjustment = PostClosingAdjustment.objects.select_for_update().select_related("related_closed_period").get(pk=adjustment_id)
    if adjustment.status != PostClosingAdjustmentStatus.DRAFT:
        raise ValidationError("Only draft post-closing adjustments can be posted.")
    if adjustment.related_closed_period.status != PeriodStatus.CLOSED:
        raise ValidationError("Related period must be closed.")
    ensure_period_is_open(adjustment.adjustment_date)

    adjustment.status = PostClosingAdjustmentStatus.POSTED
    adjustment.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.ADJUSTMENT,
        actor=user,
        module="closing",
        action="post_closing_adjustment",
        object_type="PostClosingAdjustment",
        object_id=str(adjustment.id),
        reason=adjustment.reason,
        after_data={"adjustment_number": adjustment.adjustment_number, "status": adjustment.status},
    )

    return adjustment


@transaction.atomic
def cancel_post_closing_adjustment(adjustment_id, user=None, reason=""):
    if not reason:
        raise ValidationError("Cancel reason is required.")

    adjustment = PostClosingAdjustment.objects.select_for_update().get(pk=adjustment_id)
    if adjustment.status != PostClosingAdjustmentStatus.POSTED:
        raise ValidationError("Only posted post-closing adjustments can be cancelled.")

    adjustment.status = PostClosingAdjustmentStatus.CANCELLED
    adjustment.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.ADJUSTMENT,
        actor=user,
        module="closing",
        action="cancel_post_closing_adjustment",
        object_type="PostClosingAdjustment",
        object_id=str(adjustment.id),
        reason=reason,
        after_data={"adjustment_number": adjustment.adjustment_number, "status": adjustment.status},
    )

    return adjustment
