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
    CustomerPayment,
    CustomerPaymentStatus,
    SalesInvoice,
    SalesInvoiceStatus,
    SalesLine,
    money_round,
)


@transaction.atomic
def create_sales_draft(header, lines, user=None):
    """Create a validated sales draft while leaving posting effects untouched."""

    prepared_lines = []
    subtotal = Decimal("0")
    for number, data in enumerate(lines, start=1):
        line_discount = data.get("line_discount_amount") or Decimal("0")
        line_total = money_round(
            (data["quantity"] * data["unit_sale_price"]) - line_discount
        )
        if line_total < 0:
            raise ValidationError("A sales line discount cannot exceed its gross amount.")
        prepared_lines.append((number, data, line_total))
        subtotal += line_total

    if not prepared_lines:
        raise ValidationError("Sales invoice must have at least one line.")

    invoice_discount = header.get("discount_amount") or Decimal("0")
    tax_amount = header.get("tax_amount") or Decimal("0")
    paid_now = header.get("paid_now") or Decimal("0")
    total_amount = money_round(subtotal - invoice_discount + tax_amount)
    if total_amount < 0:
        raise ValidationError("Invoice discount cannot make the total negative.")

    invoice = SalesInvoice(
        invoice_number=header["invoice_number"],
        invoice_date=header["invoice_date"],
        customer=header["customer"],
        selling_location=header["selling_location"],
        cashbox=header.get("cashbox"),
        subtotal=money_round(subtotal),
        discount_amount=invoice_discount,
        tax_amount=tax_amount,
        total_amount=total_amount,
        paid_now=paid_now,
        remaining_due=money_round(total_amount - paid_now),
        notes=header.get("notes", ""),
        created_by=user,
    )
    invoice.payment_status = invoice.calculate_payment_status()
    invoice.full_clean()
    invoice.save()

    for number, data, line_total in prepared_lines:
        line = SalesLine(
            invoice=invoice,
            line_number=number,
            item=data["item"],
            description=data.get("description", ""),
            quantity=data["quantity"],
            unit_sale_price=data["unit_sale_price"],
            line_discount_amount=data.get("line_discount_amount") or Decimal("0"),
            line_total_amount=line_total,
        )
        line.full_clean()
        line.save()

    AuditLog.objects.create(
        event_type=AuditEventType.CREATE,
        actor=user,
        module="sales",
        action="create_sales_draft",
        object_type="SalesInvoice",
        object_id=str(invoice.id),
        after_data={
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "customer_id": invoice.customer_id,
            "line_count": len(prepared_lines),
            "total_amount": str(invoice.total_amount),
            "paid_now": str(invoice.paid_now),
            "remaining_due": str(invoice.remaining_due),
        },
    )
    return invoice


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


@transaction.atomic
def record_customer_payment(payment_number, payment_date, customer, cashbox, amount, user=None, notes=""):
    """Record standalone customer payment.

    Effects:
    - Customer due decreases by payment amount.
    - Cashbox moves in by payment amount.
    - No supplier, purchase, inventory, or cost effect.
    """

    payment = CustomerPayment.objects.create(
        payment_number=payment_number,
        payment_date=payment_date,
        customer=customer,
        cashbox=cashbox,
        amount=amount,
        notes=notes,
        created_by=user,
    )
    payment.full_clean()

    CustomerLedgerEntry.objects.create(
        customer=customer,
        entry_date=payment_date,
        entry_type=CustomerLedgerEntryType.CUSTOMER_PAYMENT,
        customer_payment=payment,
        due_increase=Decimal("0"),
        due_decrease=amount,
        description=f"Customer payment {payment_number}",
        created_by=user,
    )

    CashboxMovement.objects.create(
        cashbox=cashbox,
        movement_date=payment_date,
        movement_type=CashboxMovementType.CUSTOMER_PAYMENT,
        direction=CashboxDirection.IN,
        amount=amount,
        customer_payment=payment,
        description=f"Customer payment {payment_number}",
        created_by=user,
    )

    AuditLog.objects.create(
        event_type=AuditEventType.CREATE,
        actor=user,
        module="sales",
        action="record_customer_payment",
        object_type="CustomerPayment",
        object_id=str(payment.id),
        after_data={
            "payment_number": payment.payment_number,
            "customer_id": payment.customer_id,
            "cashbox_id": payment.cashbox_id,
            "amount": str(payment.amount),
            "status": payment.status,
        },
    )

    return payment


@transaction.atomic
def cancel_customer_payment(payment_id, user=None, reason=""):
    """Cancel a posted customer payment using reverse rows."""

    payment = (
        CustomerPayment.objects.select_for_update()
        .select_related("customer", "cashbox")
        .get(pk=payment_id)
    )

    if payment.status != CustomerPaymentStatus.POSTED:
        raise ValidationError("Only posted customer payments can be cancelled.")

    CustomerLedgerEntry.objects.create(
        customer=payment.customer,
        entry_date=payment.payment_date,
        entry_type=CustomerLedgerEntryType.ADJUSTMENT,
        customer_payment=payment,
        due_increase=payment.amount,
        due_decrease=Decimal("0"),
        description=f"Cancel customer payment {payment.payment_number}",
        created_by=user,
    )

    CashboxMovement.objects.create(
        cashbox=payment.cashbox,
        movement_date=payment.payment_date,
        movement_type=CashboxMovementType.ADJUSTMENT,
        direction=CashboxDirection.OUT,
        amount=payment.amount,
        customer_payment=payment,
        description=f"Cancel customer payment {payment.payment_number}",
        created_by=user,
    )

    payment.status = CustomerPaymentStatus.CANCELLED
    payment.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user,
        module="sales",
        action="cancel_customer_payment",
        object_type="CustomerPayment",
        object_id=str(payment.id),
        reason=reason,
        after_data={
            "payment_number": payment.payment_number,
            "customer_id": payment.customer_id,
            "cashbox_id": payment.cashbox_id,
            "amount": str(payment.amount),
            "status": payment.status,
        },
    )

    return payment
