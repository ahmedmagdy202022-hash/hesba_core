from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from reports.selectors import cashbox_report, customer_report, profit_report, purchase_report, stock_report, supplier_report
from .models import ClosingRun, ClosingRunStatus, Period, PeriodStatus, PeriodSummary


def get_period_for_date(action_date):
    """Return the period covering a date."""

    return Period.objects.filter(start_date__lte=action_date, end_date__gte=action_date).order_by("-start_date").first()


def ensure_period_is_open(action_date):
    """Guard transaction posting against closed periods."""

    period = get_period_for_date(action_date)
    if period is None:
        raise ValidationError("No period found for this date.")
    if period.status == PeriodStatus.CLOSED:
        raise ValidationError("This period is closed and read-only.")
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
    """Build read-only summary values from reports and movements."""

    sales_total = _decimal_sum(
        invoice.total_amount for invoice in profit_report(period.start_date, period.end_date) if False
    )
    sales_invoices = purchase_report(period.start_date, period.end_date).none()
    del sales_invoices

    from sales.models import SalesInvoice
    from purchases.models import PurchaseInvoice

    posted_sales = SalesInvoice.objects.filter(status="posted", invoice_date__gte=period.start_date, invoice_date__lte=period.end_date)
    posted_purchases = PurchaseInvoice.objects.filter(status="posted", invoice_date__gte=period.start_date, invoice_date__lte=period.end_date)

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
    """Complete a period closing run and save read-only summaries."""

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
    """Reopen a closed period with required reason and audit trail."""

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
