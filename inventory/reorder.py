"""REORDER-001: what to buy before it runs out. Read-only; nothing here moves stock.

For every active stock-tracked item:

* on hand      = stock movements in - out, every location;
* daily sales  = (sold - returned by customers) over the last ``window`` days / window;
* days of cover = on hand / daily sales;
* suggested    = enough to cover ``target`` days, and never less than what
  brings the item back up to its minimum stock; rounded up to whole units.

An item is listed when it is at or below its minimum, or would run out before
``target`` days. The last supplier and last purchase price come from the most
recent posted purchase of the item.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from django.db.models import Sum
from django.utils import timezone

from master_data.models import Item

from .models import StockMovement, StockMovementType
from .services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES


ZERO = Decimal("0")


def _on_hand():
    totals = defaultdict(lambda: ZERO)
    for row in StockMovement.objects.values("item_id", "movement_type").annotate(total=Sum("quantity")):
        if row["movement_type"] in IN_MOVEMENT_TYPES:
            totals[row["item_id"]] += row["total"] or ZERO
        elif row["movement_type"] in OUT_MOVEMENT_TYPES:
            totals[row["item_id"]] -= row["total"] or ZERO
    return totals


def _net_sold(since):
    sold = defaultdict(lambda: ZERO)
    rows = StockMovement.objects.filter(movement_date__gte=since, movement_type__in=[StockMovementType.SALE_OUT, StockMovementType.SALE_RETURN_IN])
    for row in rows.values("item_id", "movement_type").annotate(total=Sum("quantity")):
        sign = 1 if row["movement_type"] == StockMovementType.SALE_OUT else -1
        sold[row["item_id"]] += sign * (row["total"] or ZERO)
    return sold


def _last_purchases(item_ids):
    from purchases.models import PurchaseLine

    latest = {}
    lines = (PurchaseLine.objects.filter(item_id__in=item_ids, invoice__status="posted")
             .select_related("invoice__supplier").order_by("item_id", "-invoice__invoice_date", "-id"))
    for line in lines:
        latest.setdefault(line.item_id, line)
    return latest


def suggestions(window=30, target=14, today=None):
    today = today or timezone.localdate()
    on_hand, sold = _on_hand(), _net_sold(today - timedelta(days=window - 1))
    rows = []
    for item in Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_code"):
        quantity = on_hand[item.pk]
        daily = max(sold[item.pk], ZERO) / window
        cover = (quantity / daily) if daily > 0 else None
        below_minimum = item.min_stock > 0 and quantity <= item.min_stock
        running_out = daily > 0 and cover < target
        if not (below_minimum or running_out):
            continue
        wanted = max(daily * target - quantity, (item.min_stock - quantity) if item.min_stock > 0 else ZERO, ZERO)
        if wanted <= 0 and quantity <= 0 and item.min_stock > 0:
            wanted = item.min_stock
        rows.append({
            "item": item, "on_hand": quantity, "daily": daily.quantize(Decimal("0.01")),
            "cover": cover.quantize(Decimal("0.1")) if cover is not None else None,
            "suggested": wanted.to_integral_value(rounding=ROUND_CEILING), "below_minimum": below_minimum,
            "urgent": quantity <= 0 or (cover is not None and cover < 3),
        })
    latest = _last_purchases([row["item"].pk for row in rows])
    for row in rows:
        line = latest.get(row["item"].pk)
        row["supplier"] = line.invoice.supplier if line else None
        row["last_price"] = line.unit_purchase_price if line else row["item"].default_purchase_price
        row["last_date"] = line.invoice.invoice_date if line else None
    rows.sort(key=lambda row: (not row["urgent"], row["cover"] if row["cover"] is not None else Decimal("-1"), row["item"].item_code))
    return [row for row in rows if row["suggested"] > 0]
