from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from .models import (
    Cashbox,
    CashboxDirection,
    CashboxMovement,
    CashboxMovementType,
    CashboxOperation,
    CashboxOperationStatus,
    CashboxOperationType,
    FinancialAdjustmentStatus,
    OpeningBalanceAdjustment,
    OpeningBalanceTarget,
)


def get_cashbox_balance(cashbox):
    totals = cashbox.movements.values("direction").annotate(total=Sum("amount"))
    by_direction = {row["direction"]: row["total"] for row in totals}
    return money_round(
        cashbox.opening_balance
        + (by_direction.get(CashboxDirection.IN) or Decimal("0"))
        - (by_direction.get(CashboxDirection.OUT) or Decimal("0"))
    )


def target_has_operational_use(target_type, target):
    if target_type == OpeningBalanceTarget.CUSTOMER:
        return (
            target.ledger_entries.exists()
            or target.sales_invoices.exists()
            or target.customer_payments.exists()
        )
    if target_type == OpeningBalanceTarget.SUPPLIER:
        return (
            target.ledger_entries.exists()
            or target.purchase_invoices.exists()
            or target.supplier_payments.exists()
        )
    if target_type == OpeningBalanceTarget.CASHBOX:
        return target.movements.exists()
    raise ValidationError("Unknown opening-balance target type.")


def _require_opening_adjustment_permission(user):
    if not user_has_permission(user, "master_data.adjust_opening_balances"):
        raise PermissionDenied("Opening-balance adjustments require an explicit permission.")


def _target_model(target_type):
    if target_type == OpeningBalanceTarget.CUSTOMER:
        from master_data.models import Customer

        return Customer
    if target_type == OpeningBalanceTarget.SUPPLIER:
        from master_data.models import Supplier

        return Supplier
    if target_type == OpeningBalanceTarget.CASHBOX:
        return Cashbox
    raise ValidationError("Unknown opening-balance target type.")


def _ensure_open_period(action_date):
    from closing.services import ensure_period_is_open

    return ensure_period_is_open(action_date)


def _next_adjustment_number(target_type, target):
    sequence = (
        OpeningBalanceAdjustment.objects.filter(
            target_type=target_type,
            **{target_type: target},
        ).count()
        + 1
    )
    return f"OBA-{target_type.upper()}-{target.pk}-{sequence:04d}"


def _create_adjustment_effect(adjustment, amount, user, description):
    if adjustment.target_type == OpeningBalanceTarget.CUSTOMER:
        from sales.models import CustomerLedgerEntry, CustomerLedgerEntryType

        CustomerLedgerEntry.objects.create(
            customer=adjustment.customer,
            entry_date=(adjustment.reversal_date or adjustment.adjustment_date),
            entry_type=CustomerLedgerEntryType.ADJUSTMENT,
            opening_balance_adjustment=adjustment,
            due_increase=amount if amount > 0 else Decimal("0"),
            due_decrease=-amount if amount < 0 else Decimal("0"),
            description=description,
            created_by=user,
        )
        return

    if adjustment.target_type == OpeningBalanceTarget.SUPPLIER:
        from purchases.models import SupplierLedgerEntry, SupplierLedgerEntryType

        SupplierLedgerEntry.objects.create(
            supplier=adjustment.supplier,
            entry_date=(adjustment.reversal_date or adjustment.adjustment_date),
            entry_type=SupplierLedgerEntryType.ADJUSTMENT,
            opening_balance_adjustment=adjustment,
            due_increase=amount if amount > 0 else Decimal("0"),
            due_decrease=-amount if amount < 0 else Decimal("0"),
            description=description,
            created_by=user,
        )
        return

    cashbox = adjustment.cashbox
    if amount < 0 and get_cashbox_balance(cashbox) < -amount:
        raise ValidationError("Opening-balance correction cannot make the cashbox negative.")
    CashboxMovement.objects.create(
        cashbox=cashbox,
        movement_date=(adjustment.reversal_date or adjustment.adjustment_date),
        movement_type=CashboxMovementType.ADJUSTMENT,
        direction=CashboxDirection.IN if amount > 0 else CashboxDirection.OUT,
        amount=abs(amount),
        opening_balance_adjustment=adjustment,
        description=description,
        created_by=user,
    )


@transaction.atomic
def create_opening_balance_adjustment(
    target_type,
    target_id,
    adjustment_date,
    amount,
    reason,
    user,
):
    _require_opening_adjustment_permission(user)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Adjustment reason is required.")
    amount = money_round(amount)
    if amount == 0:
        raise ValidationError("Adjustment amount cannot be zero.")
    _ensure_open_period(adjustment_date)

    model = _target_model(target_type)
    target = model.objects.select_for_update().get(pk=target_id)
    if not target_has_operational_use(target_type, target):
        raise ValidationError(
            "This record has no operational use yet; edit its opening balance directly."
        )

    target_field = {target_type: target}
    adjustment = OpeningBalanceAdjustment(
        adjustment_number=_next_adjustment_number(target_type, target),
        target_type=target_type,
        adjustment_date=adjustment_date,
        amount=amount,
        reason=reason,
        created_by=user,
        **target_field,
    )
    adjustment.full_clean()
    adjustment.save()
    _create_adjustment_effect(
        adjustment,
        amount,
        user,
        f"Opening balance adjustment {adjustment.adjustment_number}",
    )

    AuditLog.objects.create(
        event_type=AuditEventType.ADJUSTMENT,
        actor=user,
        module="cashboxes" if target_type == OpeningBalanceTarget.CASHBOX else "master_data",
        action="create_opening_balance_adjustment",
        object_type="OpeningBalanceAdjustment",
        object_id=str(adjustment.pk),
        reason=reason,
        after_data={
            "adjustment_number": adjustment.adjustment_number,
            "target_type": target_type,
            "target_id": target.pk,
            "amount": str(amount),
            "status": adjustment.status,
        },
    )
    return adjustment


def _require_move_cash_permission(user):
    if not user_has_permission(user, "cashboxes.move_cash"):
        raise PermissionDenied("Cashbox operations require cashboxes.move_cash.")


def _create_cash_operation_movement(
    operation,
    movement_date,
    cashbox,
    movement_type,
    direction,
    user,
    *,
    reversal_of=None,
):
    movement = CashboxMovement(
        cashbox=cashbox,
        movement_date=movement_date,
        movement_type=movement_type,
        direction=direction,
        amount=operation.amount,
        cashbox_operation=operation,
        reversal_of=reversal_of,
        description=f"Cash operation {operation.reference_number}",
        created_by=user,
    )
    movement.full_clean()
    movement.save()
    return movement


def _audit_cash_operation(operation, user, action, reason):
    AuditLog.objects.create(
        event_type=AuditEventType.ADJUSTMENT,
        actor=user,
        module="cashboxes",
        action=action,
        object_type="CashboxOperation",
        object_id=str(operation.pk),
        reason=reason,
        after_data={
            "reference_number": operation.reference_number,
            "operation_type": operation.operation_type,
            "source_cashbox_id": operation.source_cashbox_id,
            "destination_cashbox_id": operation.destination_cashbox_id,
            "amount": str(operation.amount),
            "status": operation.status,
        },
    )


@transaction.atomic
def create_cashbox_operation(
    reference_number,
    operation_date,
    operation_type,
    amount,
    reason,
    user,
    source_cashbox=None,
    destination_cashbox=None,
):
    """Post direct cash in/out or a linked, atomic cashbox transfer."""

    _require_move_cash_permission(user)
    _ensure_open_period(operation_date)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Cash operation reason is required.")
    amount = money_round(amount)
    if amount <= 0:
        raise ValidationError("Cash operation amount must be greater than zero.")
    if operation_type not in CashboxOperationType.values:
        raise ValidationError("Unknown cash operation type.")

    cashbox_ids = {
        cashbox.pk for cashbox in (source_cashbox, destination_cashbox) if cashbox is not None
    }
    locked = {
        cashbox.pk: cashbox
        for cashbox in Cashbox.objects.select_for_update()
        .filter(pk__in=cashbox_ids, active=True)
        .order_by("pk")
    }
    source = locked.get(getattr(source_cashbox, "pk", None))
    destination = locked.get(getattr(destination_cashbox, "pk", None))
    if len(locked) != len(cashbox_ids):
        raise ValidationError("Cash operations require active cashboxes.")
    if operation_type in (CashboxOperationType.DIRECT_OUT, CashboxOperationType.TRANSFER):
        if source is None or get_cashbox_balance(source) < amount:
            raise ValidationError("The source cashbox cannot become negative.")
    if operation_type == CashboxOperationType.TRANSFER:
        if destination is None:
            raise ValidationError("A destination cashbox is required.")
        if source.pk == destination.pk:
            raise ValidationError("Transfer cashboxes must be different.")
        if source.currency != destination.currency:
            raise ValidationError("Cashbox transfers require the same currency.")

    operation = CashboxOperation(
        reference_number=(reference_number or "").strip(),
        operation_date=operation_date,
        operation_type=operation_type,
        source_cashbox=source,
        destination_cashbox=destination,
        amount=amount,
        reason=reason,
        created_by=user,
    )
    operation.full_clean()
    operation.save()
    if operation_type == CashboxOperationType.DIRECT_IN:
        _create_cash_operation_movement(
            operation,
            operation_date,
            destination,
            CashboxMovementType.DIRECT_IN,
            CashboxDirection.IN,
            user,
        )
    elif operation_type == CashboxOperationType.DIRECT_OUT:
        _create_cash_operation_movement(
            operation,
            operation_date,
            source,
            CashboxMovementType.DIRECT_OUT,
            CashboxDirection.OUT,
            user,
        )
    else:
        _create_cash_operation_movement(
            operation,
            operation_date,
            source,
            CashboxMovementType.TRANSFER_OUT,
            CashboxDirection.OUT,
            user,
        )
        _create_cash_operation_movement(
            operation,
            operation_date,
            destination,
            CashboxMovementType.TRANSFER_IN,
            CashboxDirection.IN,
            user,
        )
    _audit_cash_operation(operation, user, "create_cashbox_operation", reason)
    return operation


@transaction.atomic
def cancel_cashbox_operation(operation_id, reversal_date, reason, user):
    """Reverse a cash operation with append-only inverse rows."""

    _require_move_cash_permission(user)
    _ensure_open_period(reversal_date)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Cash operation reversal reason is required.")
    operation = (
        CashboxOperation.objects.select_for_update()
        .select_related("source_cashbox", "destination_cashbox")
        .get(pk=operation_id)
    )
    if operation.status != CashboxOperationStatus.POSTED:
        raise ValidationError("Only posted cashbox operations can be reversed.")

    cashbox_ids = {
        cashbox_id
        for cashbox_id in (operation.source_cashbox_id, operation.destination_cashbox_id)
        if cashbox_id is not None
    }
    locked = {
        cashbox.pk: cashbox
        for cashbox in Cashbox.objects.select_for_update()
        .filter(pk__in=cashbox_ids)
        .order_by("pk")
    }
    source = locked.get(operation.source_cashbox_id)
    destination = locked.get(operation.destination_cashbox_id)
    originals = {
        movement.movement_type: movement
        for movement in operation.movements.filter(reversal_of__isnull=True)
    }
    if operation.operation_type == CashboxOperationType.DIRECT_IN:
        if get_cashbox_balance(destination) < operation.amount:
            raise ValidationError("The cashbox cannot become negative when reversing this cash in.")
        _create_cash_operation_movement(
            operation,
            reversal_date,
            destination,
            CashboxMovementType.DIRECT_OUT,
            CashboxDirection.OUT,
            user,
            reversal_of=originals[CashboxMovementType.DIRECT_IN],
        )
    elif operation.operation_type == CashboxOperationType.DIRECT_OUT:
        _create_cash_operation_movement(
            operation,
            reversal_date,
            source,
            CashboxMovementType.DIRECT_IN,
            CashboxDirection.IN,
            user,
            reversal_of=originals[CashboxMovementType.DIRECT_OUT],
        )
    else:
        if get_cashbox_balance(destination) < operation.amount:
            raise ValidationError(
                "The destination cashbox cannot become negative when reversing this transfer."
            )
        _create_cash_operation_movement(
            operation,
            reversal_date,
            destination,
            CashboxMovementType.TRANSFER_OUT,
            CashboxDirection.OUT,
            user,
            reversal_of=originals[CashboxMovementType.TRANSFER_IN],
        )
        _create_cash_operation_movement(
            operation,
            reversal_date,
            source,
            CashboxMovementType.TRANSFER_IN,
            CashboxDirection.IN,
            user,
            reversal_of=originals[CashboxMovementType.TRANSFER_OUT],
        )

    operation.status = CashboxOperationStatus.CANCELLED
    operation.cancelled_by = user
    operation.cancelled_at = timezone.now()
    operation.cancellation_reason = reason
    operation.reversal_date = reversal_date
    operation.save(
        update_fields=[
            "status",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "reversal_date",
        ]
    )
    _audit_cash_operation(operation, user, "cancel_cashbox_operation", reason)
    return operation


@transaction.atomic
def cancel_opening_balance_adjustment(
    adjustment_id,
    reversal_date,
    reason,
    user,
):
    _require_opening_adjustment_permission(user)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Reversal reason is required.")
    _ensure_open_period(reversal_date)

    adjustment = (
        OpeningBalanceAdjustment.objects.select_for_update()
        .select_related("customer", "supplier", "cashbox")
        .get(pk=adjustment_id)
    )
    if adjustment.status != FinancialAdjustmentStatus.POSTED:
        raise ValidationError("Only posted opening-balance adjustments can be reversed.")

    adjustment.reversal_date = reversal_date
    _create_adjustment_effect(
        adjustment,
        -adjustment.amount,
        user,
        f"Reverse opening balance adjustment {adjustment.adjustment_number}",
    )
    adjustment.status = FinancialAdjustmentStatus.CANCELLED
    adjustment.cancelled_by = user
    adjustment.cancelled_at = timezone.now()
    adjustment.cancellation_reason = reason
    adjustment.save(
        update_fields=[
            "status",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "reversal_date",
        ]
    )

    AuditLog.objects.create(
        event_type=AuditEventType.ADJUSTMENT,
        actor=user,
        module=(
            "cashboxes"
            if adjustment.target_type == OpeningBalanceTarget.CASHBOX
            else "master_data"
        ),
        action="cancel_opening_balance_adjustment",
        object_type="OpeningBalanceAdjustment",
        object_id=str(adjustment.pk),
        reason=reason,
        after_data={
            "adjustment_number": adjustment.adjustment_number,
            "amount": str(adjustment.amount),
            "status": adjustment.status,
            "reversal_date": str(reversal_date),
        },
    )
    return adjustment
