"""STOCKTAKE-001: count what is on the shelf and post the differences.

A count is a batch of ordinary stock adjustments made through ``adjust_stock``
(same permission, period check, average-cost rule, audit and reversal), so no
new accounting path exists. The counted quantity is the truth: the difference
is taken against the system quantity *at posting time*, inside the same
transaction, so a sale made while the sheet was being filled is not lost.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from config.money import money_round
from master_data.models import Item

from .models import StockAdjustmentDirection, StockOperation
from .services import get_item_authoritative_average_cost, get_item_location_stock_quantity, adjust_stock


SHEET_LIMIT = 300  # one sheet stays well under DATA_UPLOAD_MAX_NUMBER_FIELDS
PREFIX = "CNT"


def sheet_items(query=""):
    items = Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_code")
    query = (query or "").strip()
    if query:
        items = items.filter(Q(item_code__icontains=query) | Q(item_name__icontains=query) | Q(barcode=query))
    return list(items[: SHEET_LIMIT + 1])


def count_sheet(location, items):
    return [
        {"item": item, "system": get_item_location_stock_quantity(item, location)}
        for item in items[:SHEET_LIMIT]
    ]


def parse_counts(post, items):
    """{item: counted} for every filled box. Blank boxes were not counted."""

    counts, errors = {}, []
    by_id = {str(item.pk): item for item in items}
    for key, raw in post.items():
        if not key.startswith("count_") or key[6:] not in by_id:
            continue
        raw = (raw or "").strip().replace(",", ".")
        if raw == "":
            continue
        try:
            value = Decimal(raw)
        except InvalidOperation:
            errors.append(by_id[key[6:]].item_code)
            continue
        if value < 0 or value != value.quantize(Decimal("0.001")):
            errors.append(by_id[key[6:]].item_code)
            continue
        counts[by_id[key[6:]]] = value
    return counts, errors


def variances(location, counts):
    """Preview rows: system, counted, difference and its value at average cost."""

    rows = []
    for item, counted in sorted(counts.items(), key=lambda pair: pair[0].item_code):
        system = get_item_location_stock_quantity(item, location)
        diff = counted - system
        cost = get_item_authoritative_average_cost(item)
        rows.append({"item": item, "system": system, "counted": counted, "diff": diff, "value": money_round(diff * cost)})
    return rows


def variance_totals(rows):
    return {
        "counted": len(rows),
        "changed": sum(1 for row in rows if row["diff"]),
        "gain": money_round(sum((row["value"] for row in rows if row["value"] > 0), Decimal("0"))),
        "loss": money_round(sum((-row["value"] for row in rows if row["value"] < 0), Decimal("0"))),
        "net": money_round(sum((row["value"] for row in rows), Decimal("0"))),
    }


def next_count_number(count_date):
    stem = f"{PREFIX}-{count_date:%Y%m%d}-"
    used = StockOperation.objects.filter(reference_number__startswith=stem).values_list("reference_number", flat=True)
    numbers = [int(ref[len(stem):].split("/")[0]) for ref in used if ref[len(stem):].split("/")[0].isdigit()]
    return f"{stem}{max(numbers, default=0) + 1:02d}"


@transaction.atomic
def post_stock_count(*, location, count_date, counts, user, note=""):
    """Post one adjustment per item whose count differs. Returns (number, operations)."""

    if not counts:
        raise ValidationError("Enter at least one counted quantity.")
    number = next_count_number(count_date)
    reason = f"جرد {number}" + (f" — {note.strip()}" if (note or "").strip() else "")
    operations = []
    for item, counted in sorted(counts.items(), key=lambda pair: pair[0].item_code):
        if counted < 0:
            raise ValidationError("Counted quantity cannot be negative.")
        diff = counted - get_item_location_stock_quantity(item, location)
        if not diff:
            continue
        operations.append(
            adjust_stock(
                reference_number=f"{number}/{item.item_code}"[:100],
                operation_date=count_date,
                item=item,
                location=location,
                direction=StockAdjustmentDirection.IN if diff > 0 else StockAdjustmentDirection.OUT,
                quantity=abs(diff),
                reason=reason,
                user=user,
            )
        )
    return number, operations
