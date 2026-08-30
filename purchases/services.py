from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement, CashboxMovementType
from cashboxes.services import get_cashbox_balance
from config.money import allocate_proportionally, cost_round, money_round
from inventory.models import StockMovement, StockMovementType
from inventory.services import get_item_location_stock_quantity, recalculate_item_average_cost
from permissions.services import user_has_permission
from .models import (
    PurchaseInvoice,
    PurchaseInvoiceStatus,
    PurchaseLine,
    PurchaseReturn,
    PurchaseReturnLine,
    PurchaseReturnStatus,
    SupplierLedgerEntry,
    SupplierLedgerEntryType,
    SupplierPayment,
    SupplierPaymentStatus,
)


@transaction.atomic
def create_purchase_draft(header, lines, user=None):
    """Create a complete, internally consistent draft without posting it.

    Views supply validated input only. Totals, payment state, model validation,
    persistence, and audit stay here so no template or JavaScript becomes a
    second accounting implementation.
    """

    prepared_lines = []
    subtotal = Decimal("0")
    for number, data in enumerate(lines, start=1):
        quantity = data["quantity"]
        unit_price = data["unit_purchase_price"]
        line_discount = data.get("line_discount_amount") or Decimal("0")
        exact_total = (quantity * unit_price) - line_discount
        line_total = money_round(exact_total)
        if line_total < 0:
            raise ValidationError("A purchase line discount cannot exceed its gross amount.")
        prepared_lines.append((number, data, line_total))
        subtotal += line_total

    if not prepared_lines:
        raise ValidationError("Purchase invoice must have at least one line.")

    invoice_discount = header.get("discount_amount") or Decimal("0")
    tax_amount = header.get("tax_amount") or Decimal("0")
    paid_now = header.get("paid_now") or Decimal("0")
    total_amount = money_round(subtotal - invoice_discount + tax_amount)
    if total_amount < 0:
        raise ValidationError("Invoice discount cannot make the total negative.")

    invoice = PurchaseInvoice(
        invoice_number=header["invoice_number"],
        invoice_date=header["invoice_date"],
        supplier=header["supplier"],
        receiving_location=header["receiving_location"],
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
        line = PurchaseLine(
            invoice=invoice,
            line_number=number,
            item=data["item"],
            description=data.get("description", ""),
            quantity=data["quantity"],
            unit_purchase_price=data["unit_purchase_price"],
            line_discount_amount=data.get("line_discount_amount") or Decimal("0"),
            line_total_amount=line_total,
        )
        line.full_clean()
        line.save()

    AuditLog.objects.create(
        event_type=AuditEventType.CREATE,
        actor=user,
        module="purchases",
        action="create_purchase_draft",
        object_type="PurchaseInvoice",
        object_id=str(invoice.id),
        after_data={
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "supplier_id": invoice.supplier_id,
            "line_count": len(prepared_lines),
            "total_amount": str(invoice.total_amount),
            "paid_now": str(invoice.paid_now),
            "remaining_due": str(invoice.remaining_due),
        },
    )
    return invoice


def _purchase_line_allocations(invoice, lines):
    weights = [line.line_total_amount for line in lines]
    if sum(weights, Decimal("0")) <= 0:
        weights = [Decimal("1") for _ in lines]
    return {
        line.pk: amount
        for line, amount in zip(lines, allocate_proportionally(invoice.total_amount, weights))
    }


def _purchase_line_unit_cost(line):
    if not line.quantity:
        return Decimal("0")
    source_movement = line.stock_movements.filter(
        movement_type=StockMovementType.PURCHASE_IN,
        purchase_invoice=line.invoice,
        reversal_of__isnull=True,
    ).first()
    if source_movement is not None:
        return source_movement.unit_cost
    allocations = _purchase_line_allocations(line.invoice, list(line.invoice.lines.all()))
    return cost_round(allocations[line.pk] / line.quantity)


def _recalculate_affected_items(items_by_id):
    for item in items_by_id.values():
        recalculate_item_average_cost(item)


@transaction.atomic
def post_purchase_invoice(invoice_id, user=None):
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
    financial_allocations = _purchase_line_allocations(invoice, lines)
    for line in lines:
        if not line.item.is_stock_tracked:
            continue
        StockMovement.objects.create(
            movement_date=invoice.invoice_date,
            movement_type=StockMovementType.PURCHASE_IN,
            item=line.item,
            location=invoice.receiving_location,
            quantity=line.quantity,
            unit_cost=cost_round(financial_allocations[line.pk] / line.quantity),
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
    invoice = (
        PurchaseInvoice.objects.select_for_update()
        .select_related("supplier", "receiving_location", "cashbox")
        .prefetch_related("lines__item")
        .get(pk=invoice_id)
    )

    if invoice.status != PurchaseInvoiceStatus.POSTED:
        raise ValidationError("Only posted purchase invoices can be cancelled.")
    if invoice.returns.filter(status=PurchaseReturnStatus.POSTED).exists():
        raise ValidationError(
            "A purchase invoice with posted return documents cannot be cancelled."
        )

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
            description=f"Cancel purchase invoice {invoice.invoice_number}",
            created_by=user,
        )

    affected_items = {}
    for line in lines:
        if not line.item.is_stock_tracked:
            continue
        StockMovement.objects.create(
            movement_date=invoice.invoice_date,
            movement_type=StockMovementType.PURCHASE_RETURN_OUT,
            item=line.item,
            location=invoice.receiving_location,
            quantity=line.quantity,
            unit_cost=_purchase_line_unit_cost(line),
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


def _ensure_return_period(action_date):
    from closing.services import ensure_period_is_open

    return ensure_period_is_open(action_date)


def _require_purchase_return_permission(user):
    if not user_has_permission(user, "purchases.return_purchase"):
        raise PermissionDenied("Purchase returns require purchases.return_purchase.")


def _purchase_source_allocations(invoice, source_lines):
    weights = [line.line_total_amount for line in source_lines]
    if sum(weights, Decimal("0")) <= 0:
        weights = [Decimal("1") for _ in source_lines]
    allocations = allocate_proportionally(invoice.total_amount, weights)
    return {line.pk: amount for line, amount in zip(source_lines, allocations)}


def _prepare_purchase_return_lines(invoice, source_lines, requested_lines):
    by_id = {line.pk: line for line in source_lines}
    allocations = _purchase_source_allocations(invoice, source_lines)
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
            purchase_return__status=PurchaseReturnStatus.POSTED
        ).aggregate(quantity=Sum("quantity"), amount=Sum("amount"))
        prior_quantity = prior["quantity"] or Decimal("0")
        prior_amount = prior["amount"] or Decimal("0")
        cumulative_quantity = prior_quantity + quantity
        if cumulative_quantity > line.quantity:
            raise ValidationError(
                f"Return quantity exceeds the remaining quantity for line {line.line_number}."
            )
        allocated_total = allocations[line.pk]
        if cumulative_quantity == line.quantity:
            amount = money_round(allocated_total - prior_amount)
        else:
            amount = money_round(allocated_total * quantity / line.quantity)
        prepared.append(
            {
                "source_line": line,
                "quantity": quantity,
                "amount": amount,
                "unit_cost": _purchase_line_unit_cost(line),
            }
        )
    if not prepared:
        raise ValidationError("A purchase return must contain at least one line.")
    return prepared


def _purchase_return_payment_split(invoice, return_total):
    previous = invoice.returns.filter(status=PurchaseReturnStatus.POSTED).aggregate(
        total=Sum("total_amount"), cash=Sum("cash_amount")
    )
    previous_total = previous["total"] or Decimal("0")
    previous_cash = previous["cash"] or Decimal("0")
    cumulative_total = money_round(previous_total + return_total)
    if cumulative_total > invoice.total_amount:
        raise ValidationError("Purchase returns cannot exceed the source invoice total.")
    if not invoice.total_amount:
        cash_amount = Decimal("0")
    elif cumulative_total == invoice.total_amount:
        cash_amount = money_round(invoice.paid_now - previous_cash)
    else:
        target_cash = money_round(invoice.paid_now * cumulative_total / invoice.total_amount)
        cash_amount = money_round(target_cash - previous_cash)
    return cash_amount, money_round(return_total - cash_amount)


@transaction.atomic
def create_purchase_return(
    return_number,
    return_date,
    source_invoice_id,
    lines,
    reason,
    user,
):
    """Post a partial/full independent purchase return document."""

    _require_purchase_return_permission(user)
    _ensure_return_period(return_date)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Purchase return reason is required.")
    invoice = (
        PurchaseInvoice.objects.select_for_update()
        .select_related("supplier", "receiving_location", "cashbox")
        .get(pk=source_invoice_id)
    )
    if invoice.status != PurchaseInvoiceStatus.POSTED:
        raise ValidationError("Purchase returns require a posted source invoice.")
    source_lines = list(
        PurchaseLine.objects.select_for_update()
        .filter(invoice=invoice)
        .select_related("item")
        .order_by("line_number")
    )
    prepared = _prepare_purchase_return_lines(invoice, source_lines, lines)
    requested_stock = {}
    for row in prepared:
        line = row["source_line"]
        if line.item.is_stock_tracked:
            key = (line.item_id, invoice.receiving_location_id)
            aggregate = requested_stock.setdefault(
                key,
                {
                    "item": line.item,
                    "location": invoice.receiving_location,
                    "quantity": Decimal("0"),
                },
            )
            aggregate["quantity"] += row["quantity"]
    for aggregate in requested_stock.values():
        available = get_item_location_stock_quantity(
            aggregate["item"], aggregate["location"]
        )
        if available < aggregate["quantity"]:
            raise ValidationError(
                f"Not enough stock to return item {aggregate['item']}. "
                f"Available: {available}."
            )
    total_amount = money_round(sum((row["amount"] for row in prepared), Decimal("0")))
    cash_amount, due_amount = _purchase_return_payment_split(invoice, total_amount)
    purchase_return = PurchaseReturn(
        return_number=(return_number or "").strip(),
        return_date=return_date,
        source_invoice=invoice,
        total_amount=total_amount,
        cash_amount=cash_amount,
        due_amount=due_amount,
        reason=reason,
        created_by=user,
    )
    purchase_return.full_clean()
    purchase_return.save()

    affected_items = {}
    for row in prepared:
        return_line = PurchaseReturnLine(purchase_return=purchase_return, **row)
        return_line.full_clean()
        return_line.save()
        item = row["source_line"].item
        if not item.is_stock_tracked:
            continue
        movement = StockMovement(
            movement_date=return_date,
            movement_type=StockMovementType.PURCHASE_RETURN_OUT,
            item=item,
            location=invoice.receiving_location,
            quantity=row["quantity"],
            unit_cost=row["unit_cost"],
            purchase_return=purchase_return,
            purchase_return_line=return_line,
            notes=f"Purchase return {purchase_return.return_number}",
            created_by=user,
        )
        movement.full_clean()
        movement.save()
        affected_items[item.pk] = item

    if due_amount > 0:
        SupplierLedgerEntry.objects.create(
            supplier=invoice.supplier,
            entry_date=return_date,
            entry_type=SupplierLedgerEntryType.PURCHASE_RETURN,
            purchase_return=purchase_return,
            due_decrease=due_amount,
            description=f"Purchase return {purchase_return.return_number}",
            created_by=user,
        )
    if cash_amount > 0:
        CashboxMovement.objects.create(
            cashbox=invoice.cashbox,
            movement_date=return_date,
            movement_type=CashboxMovementType.PURCHASE_RETURN,
            direction=CashboxDirection.IN,
            amount=cash_amount,
            purchase_return=purchase_return,
            description=f"Purchase return {purchase_return.return_number}",
            created_by=user,
        )
    _recalculate_affected_items(affected_items)
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE,
        actor=user,
        module="purchases",
        action="create_purchase_return",
        object_type="PurchaseReturn",
        object_id=str(purchase_return.pk),
        reason=reason,
        after_data={
            "return_number": purchase_return.return_number,
            "source_invoice_id": invoice.pk,
            "total_amount": str(total_amount),
            "cash_amount": str(cash_amount),
            "due_amount": str(due_amount),
            "status": purchase_return.status,
        },
    )
    return purchase_return


@transaction.atomic
def cancel_purchase_return(return_id, reversal_date, reason, user):
    _require_purchase_return_permission(user)
    _ensure_return_period(reversal_date)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Purchase return reversal reason is required.")
    purchase_return = (
        PurchaseReturn.objects.select_for_update()
        .select_related(
            "source_invoice__supplier",
            "source_invoice__receiving_location",
            "source_invoice__cashbox",
        )
        .prefetch_related("lines__source_line__item")
        .get(pk=return_id)
    )
    if purchase_return.status != PurchaseReturnStatus.POSTED:
        raise ValidationError("Only posted purchase returns can be reversed.")
    invoice = purchase_return.source_invoice
    if purchase_return.cash_amount > 0:
        if get_cashbox_balance(invoice.cashbox) < purchase_return.cash_amount:
            raise ValidationError("The cashbox cannot become negative when reversing this return.")

    affected_items = {}
    original_movements = {
        movement.purchase_return_line_id: movement
        for movement in purchase_return.stock_movements.filter(reversal_of__isnull=True)
    }
    for return_line in purchase_return.lines.all():
        item = return_line.source_line.item
        if not item.is_stock_tracked:
            continue
        movement = StockMovement(
            movement_date=reversal_date,
            movement_type=StockMovementType.PURCHASE_IN,
            item=item,
            location=invoice.receiving_location,
            quantity=return_line.quantity,
            unit_cost=return_line.unit_cost,
            purchase_return=purchase_return,
            purchase_return_line=return_line,
            reversal_of=original_movements[return_line.pk],
            notes=f"Reverse purchase return {purchase_return.return_number}",
            created_by=user,
        )
        movement.full_clean()
        movement.save()
        affected_items[item.pk] = item
    if purchase_return.due_amount > 0:
        SupplierLedgerEntry.objects.create(
            supplier=invoice.supplier,
            entry_date=reversal_date,
            entry_type=SupplierLedgerEntryType.PURCHASE_RETURN,
            purchase_return=purchase_return,
            due_increase=purchase_return.due_amount,
            description=f"Reverse purchase return {purchase_return.return_number}",
            created_by=user,
        )
    if purchase_return.cash_amount > 0:
        original_cash = purchase_return.cashbox_movements.get(reversal_of__isnull=True)
        CashboxMovement.objects.create(
            cashbox=invoice.cashbox,
            movement_date=reversal_date,
            movement_type=CashboxMovementType.PURCHASE_RETURN,
            direction=CashboxDirection.OUT,
            amount=purchase_return.cash_amount,
            purchase_return=purchase_return,
            reversal_of=original_cash,
            description=f"Reverse purchase return {purchase_return.return_number}",
            created_by=user,
        )
    _recalculate_affected_items(affected_items)
    purchase_return.status = PurchaseReturnStatus.CANCELLED
    purchase_return.cancelled_by = user
    purchase_return.cancelled_at = timezone.now()
    purchase_return.cancellation_reason = reason
    purchase_return.reversal_date = reversal_date
    purchase_return.save(
        update_fields=[
            "status", "cancelled_by", "cancelled_at", "cancellation_reason", "reversal_date"
        ]
    )
    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user,
        module="purchases",
        action="cancel_purchase_return",
        object_type="PurchaseReturn",
        object_id=str(purchase_return.pk),
        reason=reason,
        after_data={"status": purchase_return.status, "reversal_date": str(reversal_date)},
    )
    return purchase_return


@transaction.atomic
def cancel_supplier_payment(payment_id, user=None, reason=""):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Supplier payment cancellation reason is required.")
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
