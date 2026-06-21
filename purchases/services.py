from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement, CashboxMovementType
from inventory.models import StockMovement, StockMovementType
from inventory.services import recalculate_item_average_cost
from .models import (
    PurchaseInvoice,
    PurchaseInvoiceStatus,
    SupplierLedgerEntry,
    SupplierLedgerEntryType,
    SupplierPayment,
    SupplierPaymentStatus,
)


def _purchase_line_unit_cost(line):
    if not line.quantity:
        return Decimal("0")
    return line.line_total_amount / line.quantity


def _recalculate_affected_items(items_by_id):
    for item in items_by_id.values():
        recalculate_item_average_cost(item)


@transaction.atomic
def post_purchase_invoice(invoice_id, user=None):
    """Post a draft purchase invoice into traceable movements."""

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

    affected_items = {}
    for line in lines:
        if not line.item.is_stock_tracked:
            continue

        unit_cost = _purchase_line_unit_cost(line)
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
        affected_items[line.item_id] = line.item

    _recalculate_affected_items(affected_items)

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


@transaction.atomic
def cancel_posted_purchase_invoice(invoice_id, user=None, reason=""):
    """Cancel a posted purchase invoice using traceable reverse movements."""

    invoice = (
        PurchaseInvoice.objects.select_for_update()
        .select_related("supplier", "receiving_location", "cashbox")
        .prefetch_related("lines__item")
        .get(pk=invoice_id)
    )

    if invoice.status != PurchaseInvoiceStatus.POSTED:
        raise ValidationError("Only posted purchase invoices can be cancelled.")

    lines = list(invoice.lines.all())
    if not lines:
        raise ValidationError("Posted purchase invoice has no lines to reverse.")

    if invoice.remaining_due and invoice.remaining_due > 0:
        SupplierLedgerEntry.objects.create(
            supplier=invoice.supplier,
            entry_date=invoice.invoice_date,
            entry_type=SupplierLedgerEntryType.PURCHASE_RETURN,
            purchase_invoice=invoice,
            due_increase=Decimal("0"),
            due_decrease=invoice.remaining_due,
            description=f"Cancel purchase invoice {invoice.invoice_number}",
            created_by=user,
        )

    if invoice.paid_now and invoice.paid_now > 0:
        CashboxMovement.objects.create(
            cashbox=invoice.cashbox,
            movement_date=invoice.invoice_date,
            movement_type=CashboxMovementType.ADJUSTMENT,
            direction=CashboxDirection.IN,
            amount=invoice.paid_now,
            purchase_invoice=invoice,
            description=f"Cancel purchase invoice {invoice.invoice_number} paid amount reversal",
            created_by=user,
        )

    affected_items = {}
    for line in lines:
        if not line.item.is_stock_tracked:
            continue

        unit_cost = _purchase_line_unit_cost(line)
        StockMovement.objects.create(
            movement_date=invoice.invoice_date,
            movement_type=StockMovementType.PURCHASE_RETURN_OUT,
            item=line.item,
            location=invoice.receiving_location,
            quantity=line.quantity,
            unit_cost=unit_cost,
            purchase_invoice=invoice,
            purchase_line=line,
            notes=f"Cancel purchase invoice {invoice.invoice_number}",
            created_by=user,
        )
        affected_items[line.item_id] = line.item

    _recalculate_affected_items(affected_items)

    invoice.status = PurchaseInvoiceStatus.CANCELLED
    invoice.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user,
        module="purchases",
        action="cancel_posted_purchase_invoice",
        object_type="PurchaseInvoice",
        object_id=str(invoice.id),
        reason=reason,
        after_data={
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "supplier_id": invoice.supplier_id,
            "total_amount": str(invoice.total_amount),
            "paid_now": str(invoice.paid_now),
            "remaining_due": str(invoice.remaining_due),
        },
    )

    return invoice


@transaction.atomic
def record_supplier_payment(payment_number, payment_date, supplier, cashbox, amount, user=None, notes=""):
    """Record standalone supplier payment.

    Effects:
    - Supplier due decreases by payment amount.
    - Cashbox moves out by payment amount.
    - No customer, sales, or inventory effect.
    """

    payment = SupplierPayment.objects.create(
        payment_number=payment_number,
        payment_date=payment_date,
        supplier=supplier,
        cashbox=cashbox,
        amount=amount,
        notes=notes,
        created_by=user,
    )
    payment.full_clean()

    SupplierLedgerEntry.objects.create(
        supplier=supplier,
        entry_date=payment_date,
        entry_type=SupplierLedgerEntryType.SUPPLIER_PAYMENT,
        supplier_payment=payment,
        due_increase=Decimal("0"),
        due_decrease=amount,
        description=f"Supplier payment {payment_number}",
        created_by=user,
    )

    CashboxMovement.objects.create(
        cashbox=cashbox,
        movement_date=payment_date,
        movement_type=CashboxMovementType.SUPPLIER_PAYMENT,
        direction=CashboxDirection.OUT,
        amount=amount,
        supplier_payment=payment,
        description=f"Supplier payment {payment_number}",
        created_by=user,
    )

    AuditLog.objects.create(
        event_type=AuditEventType.CREATE,
        actor=user,
        module="purchases",
        action="record_supplier_payment",
        object_type="SupplierPayment",
        object_id=str(payment.id),
        after_data={
            "payment_number": payment.payment_number,
            "supplier_id": payment.supplier_id,
            "cashbox_id": payment.cashbox_id,
            "amount": str(payment.amount),
            "status": payment.status,
        },
    )

    return payment


@transaction.atomic
def cancel_supplier_payment(payment_id, user=None, reason=""):
    """Cancel a posted supplier payment using reverse rows."""

    payment = (
        SupplierPayment.objects.select_for_update()
        .select_related("supplier", "cashbox")
        .get(pk=payment_id)
    )

    if payment.status != SupplierPaymentStatus.POSTED:
        raise ValidationError("Only posted supplier payments can be cancelled.")

    SupplierLedgerEntry.objects.create(
        supplier=payment.supplier,
        entry_date=payment.payment_date,
        entry_type=SupplierLedgerEntryType.ADJUSTMENT,
        supplier_payment=payment,
        due_increase=payment.amount,
        due_decrease=Decimal("0"),
        description=f"Cancel supplier payment {payment.payment_number}",
        created_by=user,
    )

    CashboxMovement.objects.create(
        cashbox=payment.cashbox,
        movement_date=payment.payment_date,
        movement_type=CashboxMovementType.ADJUSTMENT,
        direction=CashboxDirection.IN,
        amount=payment.amount,
        supplier_payment=payment,
        description=f"Cancel supplier payment {payment.payment_number}",
        created_by=user,
    )

    payment.status = SupplierPaymentStatus.CANCELLED
    payment.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user,
        module="purchases",
        action="cancel_supplier_payment",
        object_type="SupplierPayment",
        object_id=str(payment.id),
        reason=reason,
        after_data={
            "payment_number": payment.payment_number,
            "supplier_id": payment.supplier_id,
            "cashbox_id": payment.cashbox_id,
            "amount": str(payment.amount),
            "status": payment.status,
        },
    )

    return payment
