from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement, CashboxMovementType
from inventory.models import StockMovement, StockMovementType
from inventory.services import get_item_location_stock_quantity, recalculate_item_average_cost
from .models import (
    CustomerLedgerEntry,
    CustomerLedgerEntryType,
    SalesInvoice,
    SalesInvoiceStatus,
    money_round,
)


def _line_unit_cost(line):
    if not line.item.is_stock_tracked:
        return Decimal("0")
    return line.item.average_cost or Decimal("0")


def _line_cost_amount(line, unit_cost):
    return money_round(line.quantity * unit_cost)


def _validate_stock_available(invoice, lines):
    for line in lines:
        if not line.item.is_stock_tracked:
            continue
        available = get_item_location_stock_quantity(line.item, invoice.selling_location)
        if available < line.quantity:
            raise ValidationError(
                f"Not enough stock for item {line.item}. Available: {available}, required: {line.quantity}."
            )


@transaction.atomic
def post_sales_invoice(invoice_id, user=None):
    """Post a draft sales invoice into traceable movements.

    Posting effects:
    - Customer ledger increases only by remaining_due.
    - Cashbox moves in only by paid_now.
    - Inventory decreases by sales lines from selling_location.
    - Cost and profit are saved on sales lines for controlled reporting.
    - Audit log records the posting.
    """

    invoice = (
        SalesInvoice.objects.select_for_update()
        .select_related("customer", "selling_location", "cashbox")
        .prefetch_related("lines__item")
        .get(pk=invoice_id)
    )

    if invoice.status != SalesInvoiceStatus.DRAFT:
        raise ValidationError("Only draft sales invoices can be posted.")

    invoice.full_clean()

    lines = list(invoice.lines.all())
    if not lines:
        raise ValidationError("Sales invoice must have at least one line before posting.")

    for line in lines:
        line.full_clean()

    _validate_stock_available(invoice, lines)

    if invoice.remaining_due and invoice.remaining_due > 0:
        CustomerLedgerEntry.objects.create(
            customer=invoice.customer,
            entry_date=invoice.invoice_date,
            entry_type=CustomerLedgerEntryType.SALES_DUE,
            sales_invoice=invoice,
            due_increase=invoice.remaining_due,
            due_decrease=Decimal("0"),
            description=f"Sales invoice {invoice.invoice_number} remaining due",
            created_by=user,
        )

    if invoice.paid_now and invoice.paid_now > 0:
        CashboxMovement.objects.create(
            cashbox=invoice.cashbox,
            movement_date=invoice.invoice_date,
            movement_type=CashboxMovementType.SALES_RECEIPT,
            direction=CashboxDirection.IN,
            amount=invoice.paid_now,
            sales_invoice=invoice,
            description=f"Sales invoice {invoice.invoice_number} paid now",
            created_by=user,
        )

    affected_items = {}
    for line in lines:
        unit_cost = _line_unit_cost(line)
        line_cost = _line_cost_amount(line, unit_cost)
        line_profit = money_round(line.line_total_amount - line_cost)

        line.unit_cost = unit_cost
        line.line_cost_amount = line_cost
        line.line_profit_amount = line_profit
        line.save(update_fields=["unit_cost", "line_cost_amount", "line_profit_amount", "updated_at"])

        if not line.item.is_stock_tracked:
            continue

        StockMovement.objects.create(
            movement_date=invoice.invoice_date,
            movement_type=StockMovementType.SALE_OUT,
            item=line.item,
            location=invoice.selling_location,
            quantity=line.quantity,
            unit_cost=unit_cost,
            sales_invoice=invoice,
            sales_line=line,
            notes=f"Sales invoice {invoice.invoice_number}",
            created_by=user,
        )
        affected_items[line.item_id] = line.item

    for item in affected_items.values():
        recalculate_item_average_cost(item)

    invoice.status = SalesInvoiceStatus.POSTED
    invoice.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user,
        module="sales",
        action="post_sales_invoice",
        object_type="SalesInvoice",
        object_id=str(invoice.id),
        after_data={
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "customer_id": invoice.customer_id,
            "selling_location_id": invoice.selling_location_id,
            "total_amount": str(invoice.total_amount),
            "paid_now": str(invoice.paid_now),
            "remaining_due": str(invoice.remaining_due),
        },
    )

    return invoice


@transaction.atomic
def cancel_posted_sales_invoice(invoice_id, user=None, reason=""):
    """Cancel a posted sales invoice using traceable reverse movements."""

    invoice = (
        SalesInvoice.objects.select_for_update()
        .select_related("customer", "selling_location", "cashbox")
        .prefetch_related("lines__item")
        .get(pk=invoice_id)
    )

    if invoice.status != SalesInvoiceStatus.POSTED:
        raise ValidationError("Only posted sales invoices can be cancelled.")

    lines = list(invoice.lines.all())
    if not lines:
        raise ValidationError("Posted sales invoice has no lines to reverse.")

    if invoice.remaining_due and invoice.remaining_due > 0:
        CustomerLedgerEntry.objects.create(
            customer=invoice.customer,
            entry_date=invoice.invoice_date,
            entry_type=CustomerLedgerEntryType.SALES_RETURN,
            sales_invoice=invoice,
            due_increase=Decimal("0"),
            due_decrease=invoice.remaining_due,
            description=f"Cancel sales invoice {invoice.invoice_number}",
            created_by=user,
        )

    if invoice.paid_now and invoice.paid_now > 0:
        CashboxMovement.objects.create(
            cashbox=invoice.cashbox,
            movement_date=invoice.invoice_date,
            movement_type=CashboxMovementType.ADJUSTMENT,
            direction=CashboxDirection.OUT,
            amount=invoice.paid_now,
            sales_invoice=invoice,
            description=f"Cancel sales invoice {invoice.invoice_number}",
            created_by=user,
        )

    affected_items = {}
    for line in lines:
        if not line.item.is_stock_tracked:
            continue

        StockMovement.objects.create(
            movement_date=invoice.invoice_date,
            movement_type=StockMovementType.SALE_RETURN_IN,
            item=line.item,
            location=invoice.selling_location,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            sales_invoice=invoice,
            sales_line=line,
            notes=f"Cancel sales invoice {invoice.invoice_number}",
            created_by=user,
        )
        affected_items[line.item_id] = line.item

    for item in affected_items.values():
        recalculate_item_average_cost(item)

    invoice.status = SalesInvoiceStatus.CANCELLED
    invoice.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user,
        module="sales",
        action="cancel_posted_sales_invoice",
        object_type="SalesInvoice",
        object_id=str(invoice.id),
        reason=reason,
        after_data={
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "customer_id": invoice.customer_id,
            "total_amount": str(invoice.total_amount),
            "paid_now": str(invoice.paid_now),
            "remaining_due": str(invoice.remaining_due),
        },
    )

    return invoice
