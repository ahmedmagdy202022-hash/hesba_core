from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import cost_round
from permissions.services import user_has_permission

from .models import (
    StockAdjustmentDirection,
    StockMovement,
    StockMovementType,
    StockOperation,
    StockOperationStatus,
    StockOperationType,
)


IN_MOVEMENT_TYPES = {
    StockMovementType.PURCHASE_IN,
    StockMovementType.SALE_RETURN_IN,
    StockMovementType.TRANSFER_IN,
    StockMovementType.ADJUSTMENT_IN,
    StockMovementType.OPENING_STOCK,
}

OUT_MOVEMENT_TYPES = {
    StockMovementType.SALE_OUT,
    StockMovementType.PURCHASE_RETURN_OUT,
    StockMovementType.TRANSFER_OUT,
    StockMovementType.ADJUSTMENT_OUT,
}


def _movement_quantity_total(item, movement_types, location=None):
    qs = StockMovement.objects.filter(item=item, movement_type__in=movement_types)
    if location is not None:
        qs = qs.filter(location=location)
    return qs.aggregate(total=Sum("quantity")).get("total") or Decimal("0")


def get_item_stock_quantity(item):
    """Calculate current item stock quantity from stock movements."""

    in_quantity = _movement_quantity_total(item, IN_MOVEMENT_TYPES)
    out_quantity = _movement_quantity_total(item, OUT_MOVEMENT_TYPES)
    return in_quantity - out_quantity


def get_item_location_stock_quantity(item, location):
    """Calculate item stock quantity in one location from stock movements."""

    in_quantity = _movement_quantity_total(item, IN_MOVEMENT_TYPES, location=location)
    out_quantity = _movement_quantity_total(item, OUT_MOVEMENT_TYPES, location=location)
    return in_quantity - out_quantity


def _movement_value_total(item, movement_types):
    total = Decimal("0")
    movements = StockMovement.objects.filter(
        item=item,
        movement_type__in=movement_types,
    ).values_list("quantity", "unit_cost")

    for quantity, unit_cost in movements:
        total += quantity * unit_cost

    return total


def get_item_stock_value(item):
    """Calculate current item stock value from movement quantity and unit cost."""

    in_value = _movement_value_total(item, IN_MOVEMENT_TYPES)
    out_value = _movement_value_total(item, OUT_MOVEMENT_TYPES)
    return in_value - out_value


def get_item_authoritative_average_cost(item):
    """Derive current average cost from movement value and quantity.

    ``Item.average_cost`` is a cache for display and query convenience. Posting
    services call this function so a stale cache can never become sales cost.
    """

    if not item.is_stock_tracked:
        return Decimal("0")
    current_quantity = get_item_stock_quantity(item)
    if current_quantity <= 0:
        return Decimal("0")
    return cost_round(get_item_stock_value(item) / current_quantity)


def recalculate_item_average_cost(item):
    """Recalculate and save item average cost from stock movements.

    Average cost is protected data and should only be shown to users with cost
    permission. This helper only stores the controlled value on the item.
    """

    if not item.is_stock_tracked:
        return item.average_cost

    item.average_cost = get_item_authoritative_average_cost(item)

    item.save(update_fields=["average_cost", "updated_at"])
    return item.average_cost


def _ensure_open_period(action_date):
    from closing.services import ensure_period_is_open

    return ensure_period_is_open(action_date)


def _require_permission(user, permission_code):
    if not user_has_permission(user, permission_code):
        raise PermissionDenied(f"This stock operation requires {permission_code}.")


def _create_operation_movement(
    operation,
    movement_date,
    movement_type,
    location,
    quantity,
    user,
    *,
    reversal_of=None,
):
    movement = StockMovement(
        movement_date=movement_date,
        movement_type=movement_type,
        item=operation.item,
        location=location,
        quantity=quantity,
        unit_cost=operation.unit_cost,
        stock_operation=operation,
        reversal_of=reversal_of,
        notes=f"Stock operation {operation.reference_number}",
        created_by=user,
    )
    movement.full_clean()
    movement.save()
    return movement


def _audit_operation(operation, user, action, reason):
    AuditLog.objects.create(
        event_type=AuditEventType.ADJUSTMENT,
        actor=user,
        module="inventory",
        action=action,
        object_type="StockOperation",
        object_id=str(operation.pk),
        reason=reason,
        after_data={
            "reference_number": operation.reference_number,
            "operation_type": operation.operation_type,
            "item_id": operation.item_id,
            "quantity": str(operation.quantity),
            "unit_cost": str(operation.unit_cost),
            "status": operation.status,
        },
    )


@transaction.atomic
def transfer_stock(
    reference_number,
    operation_date,
    item,
    source_location,
    destination_location,
    quantity,
    user,
    reason="",
):
    _require_permission(user, "inventory.transfer_stock")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Stock transfer reason is required.")
    _ensure_open_period(operation_date)
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValidationError("Transfer quantity must be greater than zero.")
    if source_location.pk == destination_location.pk:
        raise ValidationError("Transfer locations must be different.")

    from master_data.models import Item, Location

    locked_item = Item.objects.select_for_update().get(pk=item.pk)
    locked_locations = {
        location.pk: location
        for location in Location.objects.select_for_update()
        .filter(pk__in=[source_location.pk, destination_location.pk])
        .order_by("pk")
    }
    source = locked_locations[source_location.pk]
    destination = locked_locations[destination_location.pk]
    available = get_item_location_stock_quantity(locked_item, source)
    if available < quantity:
        raise ValidationError(
            f"Not enough stock to transfer. Available: {available}, required: {quantity}."
        )

    operation = StockOperation(
        reference_number=reference_number,
        operation_date=operation_date,
        operation_type=StockOperationType.TRANSFER,
        item=locked_item,
        source_location=source,
        destination_location=destination,
        quantity=quantity,
        unit_cost=get_item_authoritative_average_cost(locked_item),
        reason=reason,
        created_by=user,
    )
    operation.full_clean()
    operation.save()
    _create_operation_movement(
        operation,
        operation_date,
        StockMovementType.TRANSFER_OUT,
        source,
        quantity,
        user,
    )
    _create_operation_movement(
        operation,
        operation_date,
        StockMovementType.TRANSFER_IN,
        destination,
        quantity,
        user,
    )
    recalculate_item_average_cost(locked_item)
    _audit_operation(operation, user, "transfer_stock", operation.reason)
    return operation


@transaction.atomic
def adjust_stock(
    reference_number,
    operation_date,
    item,
    location,
    direction,
    quantity,
    reason,
    user,
    unit_cost=None,
):
    _require_permission(user, "inventory.adjust_stock")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Stock adjustment reason is required.")
    _ensure_open_period(operation_date)
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValidationError("Adjustment quantity must be greater than zero.")
    if direction not in StockAdjustmentDirection.values:
        raise ValidationError("Adjustment direction must be in or out.")

    from master_data.models import Item, Location

    locked_item = Item.objects.select_for_update().get(pk=item.pk)
    locked_location = Location.objects.select_for_update().get(pk=location.pk)
    if direction == StockAdjustmentDirection.OUT:
        available = get_item_location_stock_quantity(locked_item, locked_location)
        if available < quantity:
            raise ValidationError(
                f"Not enough stock to adjust out. Available: {available}, required: {quantity}."
            )
    if unit_cost is not None:
        if not user_has_permission(user, "inventory.view_cost"):
            raise PermissionDenied("Setting adjustment cost requires inventory.view_cost.")
        operation_cost = cost_round(unit_cost)
    else:
        operation_cost = get_item_authoritative_average_cost(locked_item)

    operation = StockOperation(
        reference_number=reference_number,
        operation_date=operation_date,
        operation_type=StockOperationType.ADJUSTMENT,
        item=locked_item,
        source_location=(
            locked_location if direction == StockAdjustmentDirection.OUT else None
        ),
        destination_location=(
            locked_location if direction == StockAdjustmentDirection.IN else None
        ),
        adjustment_direction=direction,
        quantity=quantity,
        unit_cost=operation_cost,
        reason=reason,
        created_by=user,
    )
    operation.full_clean()
    operation.save()
    _create_operation_movement(
        operation,
        operation_date,
        (
            StockMovementType.ADJUSTMENT_IN
            if direction == StockAdjustmentDirection.IN
            else StockMovementType.ADJUSTMENT_OUT
        ),
        locked_location,
        quantity,
        user,
    )
    recalculate_item_average_cost(locked_item)
    _audit_operation(operation, user, "adjust_stock", reason)
    return operation


@transaction.atomic
def cancel_stock_operation(operation_id, reversal_date, reason, user):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Stock operation reversal reason is required.")
    _ensure_open_period(reversal_date)
    operation = (
        StockOperation.objects.select_for_update()
        .select_related("item", "source_location", "destination_location")
        .get(pk=operation_id)
    )
    required_permission = (
        "inventory.transfer_stock"
        if operation.operation_type == StockOperationType.TRANSFER
        else "inventory.adjust_stock"
    )
    _require_permission(user, required_permission)
    if operation.status != StockOperationStatus.POSTED:
        raise ValidationError("Only posted stock operations can be reversed.")

    from master_data.models import Item

    locked_item = Item.objects.select_for_update().get(pk=operation.item_id)
    original_movements = {
        movement.movement_type: movement
        for movement in operation.movements.filter(reversal_of__isnull=True)
    }
    if operation.operation_type == StockOperationType.TRANSFER:
        available = get_item_location_stock_quantity(
            locked_item, operation.destination_location
        )
        if available < operation.quantity:
            raise ValidationError(
                "The destination no longer has enough stock to reverse this transfer."
            )
        _create_operation_movement(
            operation,
            reversal_date,
            StockMovementType.TRANSFER_OUT,
            operation.destination_location,
            operation.quantity,
            user,
            reversal_of=original_movements[StockMovementType.TRANSFER_IN],
        )
        _create_operation_movement(
            operation,
            reversal_date,
            StockMovementType.TRANSFER_IN,
            operation.source_location,
            operation.quantity,
            user,
            reversal_of=original_movements[StockMovementType.TRANSFER_OUT],
        )
    else:
        original_type = (
            StockMovementType.ADJUSTMENT_IN
            if operation.adjustment_direction == StockAdjustmentDirection.IN
            else StockMovementType.ADJUSTMENT_OUT
        )
        location = operation.destination_location or operation.source_location
        if operation.adjustment_direction == StockAdjustmentDirection.IN:
            available = get_item_location_stock_quantity(locked_item, location)
            if available < operation.quantity:
                raise ValidationError(
                    "The location no longer has enough stock to reverse this adjustment."
                )
            reversal_type = StockMovementType.ADJUSTMENT_OUT
        else:
            reversal_type = StockMovementType.ADJUSTMENT_IN
        _create_operation_movement(
            operation,
            reversal_date,
            reversal_type,
            location,
            operation.quantity,
            user,
            reversal_of=original_movements[original_type],
        )

    operation.item = locked_item
    recalculate_item_average_cost(locked_item)
    operation.status = StockOperationStatus.CANCELLED
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
    _audit_operation(operation, user, "cancel_stock_operation", reason)
    return operation
