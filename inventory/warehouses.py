"""R2-7: each warehouse at a glance, read from stock movements only.

Nothing here moves stock or changes a figure the other reports compute: the
on-hand quantity per warehouse is the same movement sum the stock screen uses
(item + location), and its value is that quantity at the item's authoritative
average cost, shown as an estimate.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Sum

from master_data.models import Item

from .models import StockMovement
from .services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES, get_item_authoritative_average_cost


ZERO = Decimal("0")
SLOW_DAYS = 60
RECENT_DAYS = 30


def on_hand(locations):
    """{(location_id, item_id): quantity} for the given locations."""

    totals = defaultdict(lambda: ZERO)
    rows = (StockMovement.objects.filter(location__in=locations)
            .values("location_id", "item_id", "movement_type").annotate(total=Sum("quantity")))
    for row in rows:
        key = (row["location_id"], row["item_id"])
        if row["movement_type"] in IN_MOVEMENT_TYPES:
            totals[key] += row["total"] or ZERO
        elif row["movement_type"] in OUT_MOVEMENT_TYPES:
            totals[key] -= row["total"] or ZERO
    return totals


def _costs(item_ids):
    return {item.pk: get_item_authoritative_average_cost(item) for item in Item.objects.filter(pk__in=item_ids)}


def summaries(locations, today, with_value=False):
    """One card per warehouse: what is on its shelves and how busy it is."""

    locations = list(locations)
    quantities = on_hand(locations)
    costs = _costs({item for (_, item), qty in quantities.items() if qty > 0}) if with_value else {}
    activity = {row["location_id"]: row for row in StockMovement.objects.filter(location__in=locations)
                .values("location_id").annotate(last=Max("movement_date"))}
    recent = {row["location_id"]: row["n"] for row in StockMovement.objects.filter(
        location__in=locations, movement_date__gte=today - timedelta(days=RECENT_DAYS)).values("location_id").annotate(n=Count("id"))}
    cards = []
    for location in locations:
        held = {item: qty for (loc, item), qty in quantities.items() if loc == location.pk}
        stocked = {item: qty for item, qty in held.items() if qty > 0}
        cards.append({
            "location": location,
            "items": len(stocked),
            "units": sum(stocked.values(), ZERO),
            "empty": sum(1 for qty in held.values() if qty <= 0),
            "value": sum((qty * costs.get(item, ZERO) for item, qty in stocked.items()), ZERO).quantize(Decimal("0.01")) if with_value else None,
            "recent": recent.get(location.pk, 0),
            "last": (activity.get(location.pk) or {}).get("last"),
        })
    return cards


def detail(location, today, with_value=False):
    """What one warehouse holds, its slow movers and what changed lately."""

    quantities = {item: qty for (_, item), qty in on_hand([location]).items() if qty > 0}
    items = {item.pk: item for item in Item.objects.filter(pk__in=quantities).select_related("category")}
    costs = _costs(quantities) if with_value else {}
    last_out = {row["item_id"]: row["last"] for row in StockMovement.objects.filter(
        location=location, movement_type__in=OUT_MOVEMENT_TYPES).values("item_id").annotate(last=Max("movement_date"))}
    rows = []
    for item_id, qty in quantities.items():
        item = items[item_id]
        cost = costs.get(item_id, ZERO)
        last = last_out.get(item_id)
        rows.append({
            "item": item, "quantity": qty, "unit_cost": cost if with_value else None,
            "value": (qty * cost).quantize(Decimal("0.01")) if with_value else None,
            "last_out": last, "idle_days": (today - last).days if last else None,
            "slow": last is None or (today - last).days >= SLOW_DAYS,
        })
    rows.sort(key=lambda row: (-(row["value"] or ZERO), -row["quantity"], row["item"].item_code))
    categories = defaultdict(lambda: {"items": 0, "value": ZERO})
    for row in rows:
        name = row["item"].category.name_ar if row["item"].category_id else ""
        categories[name]["items"] += 1
        categories[name]["value"] += row["value"] or ZERO
    movements = (StockMovement.objects.filter(location=location).select_related("item")
                 .order_by("-movement_date", "-pk")[:15])
    return {
        "rows": rows,
        "slow": [row for row in rows if row["slow"]],
        "total_value": sum((row["value"] or ZERO for row in rows), ZERO) if with_value else None,
        "units": sum((row["quantity"] for row in rows), ZERO),
        "categories": sorted(({"name": name, **data} for name, data in categories.items()), key=lambda c: -c["value"]),
        "movements": movements,
    }
