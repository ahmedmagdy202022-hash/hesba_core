from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement, CashboxMovementType
from inventory.models import StockMovement, StockMovementType
from .models import (
    PurchaseInvoice,
    PurchaseInvoiceStatus,
    SupplierLedgerEntry,
    SupplierLedgerEntryType,
)


@transaction.atomic
def post_purchase_invoice(invoice_id, user=None):
    """Post a draft purchase invoice into traceable movements.

    Posting effects:
    - Supplier ledger increases only by remaining_due.
    - Cashbox moves out only by paid_now.
    - Inventory increases by purchase lines into receiving_location.
    - Invoice status becomes posted.
    - Audit log records the posting.
    """

    invoice = (
        PurchaseInvoice.objects.select_for_update()
        .select_related("supplier", "receiving_location", "cashbox")
        .prefetch_related("lines__item")
        .get(pk=invoice_id)
    )

    if invoice.status != PurchaseInvoiceStatus.DRAFT:
        raise ValidationError("Only draft purchase invoices can be posted.")

    invoice.full_clean()

    lines = list(invoice.lines.all())
    if not lines:
        raise ValidationError("Purchase invoice must have at least one line before posting.")

    for line in lines:
        line.full_clean()

    if invoice.remaining_due and invoice.remaining_due > 0:
        SupplierLedgerEntry.objects.create(
            supplier=invoice.supplier,
            entry_date=invoice.invoice_date,
            entry_type=SupplierLedgerEntryType.PURCHASE_DUE,
            purchase_invoice=invoice,
            due_increase=invoice.remaining_due,
            due_decrease=Decimal("0"),
            description=f"Purchase invoice {invoice.invoice_number} remaining due",
            created_by=user,
        )

    if invoice.paid_now and invoice.paid_now > 0:
        CashboxMovement.objects.create(
            cashbox=invoice.cashbox,
            movement_date=invoice.invoice_date,
            movement_type=CashboxMovementType.PURCHASE_PAYMENT,
            direction=CashboxDirection.OUT,
            amount=invoice.paid_now,
            purchase_invoice=invoice,
            description=f"Purchase invoice {invoice.invoice_number} paid now",
            created_by=user,
        )

    for line in lines:
        if not line.item.is_stock_tracked:
            continue

        unit_cost = Decimal("0")
        if line.quantity:
            unit_cost = line.line_total_amount / line.quantity

        StockMovement.objects.create(
            movement_date=invoice.invoice_date,
            movement_type=StockMovementType.PURCHASE_IN,
            item=line.item,
            location=invoice.receiving_location,
            quantity=line.quantity,
            unit_cost=unit_cost,
            purchase_invoice=invoice,
            purchase_line=line,
            notes=f"Purchase invoice {invoice.invoice_number}",
            created_by=user,
        )

    invoice.status = PurchaseInvoiceStatus.POSTED
    invoice.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user,
        module="purchases",
        action="post_purchase_invoice",
        object_type="PurchaseInvoice",
        object_id=str(invoice.id),
        after_data={
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "supplier_id": invoice.supplier_id,
            "receiving_location_id": invoice.receiving_location_id,
            "total_amount": str(invoice.total_amount),
            "paid_now": str(invoice.paid_now),
            "remaining_due": str(invoice.remaining_due),
        },
    )

    return invoice
