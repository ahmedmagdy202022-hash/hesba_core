from decimal import Decimal

from django.db.models import Sum

from .models import StockMovement, StockMovementType


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


def recalculate_item_average_cost(item):
    """Recalculate and save item average cost from stock movements.

    Average cost is protected data and should only be shown to users with cost
    permission. This helper only stores the controlled value on the item.
    """

    if not item.is_stock_tracked:
        return item.average_cost

    current_quantity = get_item_stock_quantity(item)
    if current_quantity <= 0:
        item.average_cost = Decimal("0")
    else:
        current_value = get_item_stock_value(item)
        item.average_cost = current_value / current_quantity

    item.save(update_fields=["average_cost", "updated_at"])
    return item.average_cost
