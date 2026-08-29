from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement, CashboxMovementType
from cashboxes.services import get_cashbox_balance
from config.money import allocate_proportionally
from inventory.models import StockMovement, StockMovementType
from inventory.services import (
    get_item_authoritative_average_cost,
    get_item_location_stock_quantity,
    recalculate_item_average_cost,
)
from permissions.services import user_has_permission
from .models import (
    CustomerLedgerEntry,
    CustomerLedgerEntryType,
    CustomerPayment,
    CustomerPaymentStatus,
    SalesInvoice,
    SalesInvoiceStatus,
    SalesLine,
    SalesReturn,
    SalesReturnLine,
    SalesReturnStatus,
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
    return get_item_authoritative_average_cost(line.item)


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

    financial_weights = [line.line_total_amount for line in lines]
    if sum(financial_weights, Decimal("0")) <= 0:
        financial_weights = [Decimal("1") for _ in lines]
    financial_allocations = allocate_proportionally(invoice.total_amount, financial_weights)

    affected_items = {}
    for line, financial_amount in zip(lines, financial_allocations):
        unit_cost = _line_unit_cost(line)
        line_cost = _line_cost_amount(line, unit_cost)
        line_profit = money_round(financial_amount - line_cost)

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
    if invoice.returns.filter(status=SalesReturnStatus.POSTED).exists():
        raise ValidationError(
            "A sales invoice with posted return documents cannot be cancelled."
        )

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


def _ensure_return_period(action_date):
    from closing.services import ensure_period_is_open

    return ensure_period_is_open(action_date)


def _require_sales_return_permission(user):
    if not user_has_permission(user, "sales.return_sale"):
        raise PermissionDenied("Sales returns require sales.return_sale.")


def _sales_source_allocations(invoice, source_lines):
    weights = [line.line_total_amount for line in source_lines]
    if sum(weights, Decimal("0")) <= 0:
        weights = [Decimal("1") for _ in source_lines]
    allocations = allocate_proportionally(invoice.total_amount, weights)
    return {line.pk: amount for line, amount in zip(source_lines, allocations)}


def _prepare_sales_return_lines(invoice, source_lines, requested_lines):
    by_id = {line.pk: line for line in source_lines}
    allocations = _sales_source_allocations(invoice, source_lines)
    prepared = []
    seen = set()
    for requested in requested_lines:
        source_line = requested.get("source_line")
        source_line_id = getattr(source_line, "pk", source_line)
        quantity = Decimal(requested.get("quantity") or 0)
        if source_line_id in seen:
            raise ValidationError("A source line may appear only once in a return document.")
        seen.add(source_line_id)
        line = by_id.get(source_line_id)
        if line is None:
            raise ValidationError("Every return line must belong to the source invoice.")
        if quantity <= 0:
            raise ValidationError("Return quantity must be greater than zero.")
        prior = line.return_lines.filter(
            sales_return__status=SalesReturnStatus.POSTED
        ).aggregate(quantity=Sum("quantity"), amount=Sum("amount"), cost=Sum("cost_amount"))
        prior_quantity = prior["quantity"] or Decimal("0")
        prior_amount = prior["amount"] or Decimal("0")
        prior_cost = prior["cost"] or Decimal("0")
        cumulative_quantity = prior_quantity + quantity
        if cumulative_quantity > line.quantity:
            raise ValidationError(
                f"Return quantity exceeds the remaining quantity for line {line.line_number}."
            )
        allocated_total = allocations[line.pk]
        if cumulative_quantity == line.quantity:
            amount = money_round(allocated_total - prior_amount)
            cost_amount = money_round(line.line_cost_amount - prior_cost)
        else:
            amount = money_round(allocated_total * quantity / line.quantity)
            cost_amount = money_round(line.line_cost_amount * quantity / line.quantity)
        prepared.append(
            {
                "source_line": line,
                "quantity": quantity,
                "amount": amount,
                "unit_cost": line.unit_cost,
                "cost_amount": cost_amount,
            }
        )
    if not prepared:
        raise ValidationError("A sales return must contain at least one line.")
    return prepared


def _sales_return_payment_split(invoice, return_total):
    previous = invoice.returns.filter(status=SalesReturnStatus.POSTED).aggregate(
        total=Sum("total_amount"), cash=Sum("cash_amount")
    )
    previous_total = previous["total"] or Decimal("0")
    previous_cash = previous["cash"] or Decimal("0")
    cumulative_total = money_round(previous_total + return_total)
    if cumulative_total > invoice.total_amount:
        raise ValidationError("Sales returns cannot exceed the source invoice total.")
    if not invoice.total_amount:
        cash_amount = Decimal("0")
    elif cumulative_total == invoice.total_amount:
        cash_amount = money_round(invoice.paid_now - previous_cash)
    else:
        target_cash = money_round(invoice.paid_now * cumulative_total / invoice.total_amount)
        cash_amount = money_round(target_cash - previous_cash)
    return cash_amount, money_round(return_total - cash_amount)


@transaction.atomic
def create_sales_return(
    return_number,
    return_date,
    source_invoice_id,
    lines,
    reason,
    user,
):
    """Post a partial/full independent sales return document."""

    _require_sales_return_permission(user)
    _ensure_return_period(return_date)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Sales return reason is required.")
    invoice = (
        SalesInvoice.objects.select_for_update()
        .select_related("customer", "selling_location", "cashbox")
        .get(pk=source_invoice_id)
    )
    if invoice.status != SalesInvoiceStatus.POSTED:
        raise ValidationError("Sales returns require a posted source invoice.")
    source_lines = list(
        SalesLine.objects.select_for_update()
        .filter(invoice=invoice)
        .select_related("item")
        .order_by("line_number")
    )
    prepared = _prepare_sales_return_lines(invoice, source_lines, lines)
    total_amount = money_round(sum((row["amount"] for row in prepared), Decimal("0")))
    cost_amount = money_round(sum((row["cost_amount"] for row in prepared), Decimal("0")))
    cash_amount, due_amount = _sales_return_payment_split(invoice, total_amount)
    if cash_amount > 0 and get_cashbox_balance(invoice.cashbox) < cash_amount:
        raise ValidationError("The cashbox cannot become negative for this sales return refund.")
    sales_return = SalesReturn(
        return_number=(return_number or "").strip(),
        return_date=return_date,
        source_invoice=invoice,
        total_amount=total_amount,
        cash_amount=cash_amount,
        due_amount=due_amount,
        cost_amount=cost_amount,
        reason=reason,
        created_by=user,
    )
    sales_return.full_clean()
    sales_return.save()

    affected_items = {}
    for row in prepared:
        return_line = SalesReturnLine(sales_return=sales_return, **row)
        return_line.full_clean()
        return_line.save()
        item = row["source_line"].item
        if not item.is_stock_tracked:
            continue
        movement = StockMovement(
            movement_date=return_date,
            movement_type=StockMovementType.SALE_RETURN_IN,
            item=item,
            location=invoice.selling_location,
            quantity=row["quantity"],
            unit_cost=row["unit_cost"],
            sales_return=sales_return,
            sales_return_line=return_line,
            notes=f"Sales return {sales_return.return_number}",
            created_by=user,
        )
        movement.full_clean()
        movement.save()
        affected_items[item.pk] = item

    if due_amount > 0:
        CustomerLedgerEntry.objects.create(
            customer=invoice.customer,
            entry_date=return_date,
            entry_type=CustomerLedgerEntryType.SALES_RETURN,
            sales_return=sales_return,
            due_decrease=due_amount,
            description=f"Sales return {sales_return.return_number}",
            created_by=user,
        )
    if cash_amount > 0:
        CashboxMovement.objects.create(
            cashbox=invoice.cashbox,
            movement_date=return_date,
            movement_type=CashboxMovementType.SALES_RETURN,
            direction=CashboxDirection.OUT,
            amount=cash_amount,
            sales_return=sales_return,
            description=f"Sales return {sales_return.return_number}",
            created_by=user,
        )
    for item in affected_items.values():
        recalculate_item_average_cost(item)
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE,
        actor=user,
        module="sales",
        action="create_sales_return",
        object_type="SalesReturn",
        object_id=str(sales_return.pk),
        reason=reason,
        after_data={
            "return_number": sales_return.return_number,
            "source_invoice_id": invoice.pk,
            "total_amount": str(total_amount),
            "cash_amount": str(cash_amount),
            "due_amount": str(due_amount),
            "cost_amount": str(cost_amount),
            "status": sales_return.status,
        },
    )
    return sales_return


@transaction.atomic
def cancel_sales_return(return_id, reversal_date, reason, user):
    _require_sales_return_permission(user)
    _ensure_return_period(reversal_date)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Sales return reversal reason is required.")
    sales_return = (
        SalesReturn.objects.select_for_update()
        .select_related(
            "source_invoice__customer",
            "source_invoice__selling_location",
            "source_invoice__cashbox",
        )
        .prefetch_related("lines__source_line__item")
        .get(pk=return_id)
    )
    if sales_return.status != SalesReturnStatus.POSTED:
        raise ValidationError("Only posted sales returns can be reversed.")
    invoice = sales_return.source_invoice
    for return_line in sales_return.lines.all():
        item = return_line.source_line.item
        if not item.is_stock_tracked:
            continue
        available = get_item_location_stock_quantity(item, invoice.selling_location)
        if available < return_line.quantity:
            raise ValidationError(
                f"Not enough returned stock to reverse item {item}. Available: {available}."
            )

    affected_items = {}
    original_movements = {
        movement.sales_return_line_id: movement
        for movement in sales_return.stock_movements.filter(reversal_of__isnull=True)
    }
    for return_line in sales_return.lines.all():
        item = return_line.source_line.item
        if not item.is_stock_tracked:
            continue
        movement = StockMovement(
            movement_date=reversal_date,
            movement_type=StockMovementType.SALE_OUT,
            item=item,
            location=invoice.selling_location,
            quantity=return_line.quantity,
            unit_cost=return_line.unit_cost,
            sales_return=sales_return,
            sales_return_line=return_line,
            reversal_of=original_movements[return_line.pk],
            notes=f"Reverse sales return {sales_return.return_number}",
            created_by=user,
        )
        movement.full_clean()
        movement.save()
        affected_items[item.pk] = item
    if sales_return.due_amount > 0:
        CustomerLedgerEntry.objects.create(
            customer=invoice.customer,
            entry_date=reversal_date,
            entry_type=CustomerLedgerEntryType.SALES_RETURN,
            sales_return=sales_return,
            due_increase=sales_return.due_amount,
            description=f"Reverse sales return {sales_return.return_number}",
            created_by=user,
        )
    if sales_return.cash_amount > 0:
        original_cash = sales_return.cashbox_movements.get(reversal_of__isnull=True)
        CashboxMovement.objects.create(
            cashbox=invoice.cashbox,
            movement_date=reversal_date,
            movement_type=CashboxMovementType.SALES_RETURN,
            direction=CashboxDirection.IN,
            amount=sales_return.cash_amount,
            sales_return=sales_return,
            reversal_of=original_cash,
            description=f"Reverse sales return {sales_return.return_number}",
            created_by=user,
        )
    for item in affected_items.values():
        recalculate_item_average_cost(item)
    sales_return.status = SalesReturnStatus.CANCELLED
    sales_return.cancelled_by = user
    sales_return.cancelled_at = timezone.now()
    sales_return.cancellation_reason = reason
    sales_return.reversal_date = reversal_date
    sales_return.save(
        update_fields=[
            "status", "cancelled_by", "cancelled_at", "cancellation_reason", "reversal_date"
        ]
    )
    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user,
        module="sales",
        action="cancel_sales_return",
        object_type="SalesReturn",
        object_id=str(sales_return.pk),
        reason=reason,
        after_data={"status": sales_return.status, "reversal_date": str(reversal_date)},
    )
    return sales_return


@transaction.atomic
def cancel_customer_payment(payment_id, user=None, reason=""):
    """Cancel a posted customer payment using reverse rows."""

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Customer payment cancellation reason is required.")

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
