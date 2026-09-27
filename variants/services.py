"""VARIANT-001: create a model's size x colour items, extend them, price them, see them as a grid."""

import re
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from audit.models import AuditEventType, AuditLog
from barcode.symbology import internal_ean13
from inventory.models import StockMovement
from inventory.services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES
from master_data.models import Item
from settings_core.capabilities import capability_enabled

from .models import Variant, VariantGroup


ZERO = Decimal("0")
MAX_COMBINATIONS = 400


def variants_enabled():
    return capability_enabled("variants")


def parse_values(raw):
    """'S, M,L ، XL' -> ['S', 'M', 'L', 'XL']: comma (Arabic or Latin) or newline separated, no repeats."""

    values = []
    for part in re.split(r"[,،\n]+", raw or ""):
        value = part.strip()[:80]
        if value and value.casefold() not in {v.casefold() for v in values}:
            values.append(value)
    return values


def _slug(value):
    slug = re.sub(r"[^0-9A-Za-z؀-ۿ]+", "", value).upper()
    return slug[:12] or "X"


def _free_code(base, taken):
    code, n = base[:80], 2
    while code in taken:
        suffix = f"-{n}"
        code, n = f"{base[:80 - len(suffix)]}{suffix}", n + 1
    taken.add(code)
    return code


def _free_barcode(item, taken):
    sequence = item.pk
    code = internal_ean13(sequence)
    while code in taken:
        sequence += 1_000_000
        code = internal_ean13(sequence)
    taken.add(code)
    return code


def _combinations(group):
    sizes, colors = group.sizes or [""], group.colors or [""]
    return [(size, color) for size in sizes for color in colors]


def _create_missing(group, user, barcodes):
    existing = set(Variant.objects.filter(group=group).values_list("size", "color"))
    missing = [combo for combo in _combinations(group) if combo not in existing]
    if len(existing) + len(missing) > MAX_COMBINATIONS:
        raise ValidationError(f"A model can have at most {MAX_COMBINATIONS} size/colour combinations.")
    codes = set(Item.objects.values_list("item_code", flat=True))
    taken_barcodes = set(Item.objects.exclude(barcode="").values_list("barcode", flat=True)) if barcodes else set()
    created = []
    for size, color in missing:
        base = "-".join(part for part in (group.code, _slug(size) if size else "", _slug(color) if color else "") if part)
        item = Item(
            item_code=_free_code(base, codes), item_name=group.name, category=group.category, size=size, color=color, unit=group.unit,
            default_sale_price=group.default_sale_price, default_purchase_price=group.default_purchase_price, is_stock_tracked=True, active=True,
        )
        item.full_clean()
        item.save()
        if barcodes:
            item.barcode = _free_barcode(item, taken_barcodes)
            item.save(update_fields=["barcode", "updated_at"])
        Variant.objects.create(group=group, item=item, size=size, color=color)
        created.append(item)
    return created


def _audit(group, user, action, before, after):
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE if action == "create_variant_group" else AuditEventType.UPDATE, actor=user, module="variants", action=action,
        object_type="variants.VariantGroup", object_id=str(group.pk), before_data=before, after_data=after,
    )


@transaction.atomic
def create_group(*, code, name, sizes, colors, sale_price, purchase_price, user, unit="unit", category=None, barcodes=True):
    code = re.sub(r"\s+", "", code or "").upper()[:40]
    if not code or not (name or "").strip():
        raise ValidationError("A model needs a code and a name.")
    if not sizes and not colors:
        raise ValidationError("Enter at least one size or one colour.")
    if VariantGroup.objects.filter(code=code).exists():
        raise ValidationError(f"The model code {code} is already used.")
    group = VariantGroup(code=code, name=name.strip()[:255], sizes=sizes, colors=colors, default_sale_price=sale_price, default_purchase_price=purchase_price,
                         unit=(unit or "unit")[:50], category=category, created_by=user)
    group.full_clean()
    group.save()
    created = _create_missing(group, user, barcodes)
    _audit(group, user, "create_variant_group", {}, {"code": group.code, "sizes": sizes, "colors": colors, "items": [item.item_code for item in created]})
    return group, created


@transaction.atomic
def extend_group(group, *, sizes=(), colors=(), user, barcodes=True):
    """Add sizes and/or colours; the missing combinations become new items."""

    group = VariantGroup.objects.select_for_update().get(pk=group.pk)
    before = {"sizes": list(group.sizes), "colors": list(group.colors)}
    group.sizes = group.sizes + [s for s in sizes if s.casefold() not in {v.casefold() for v in group.sizes}]
    group.colors = group.colors + [c for c in colors if c.casefold() not in {v.casefold() for v in group.colors}]
    group.save(update_fields=["sizes", "colors"])
    created = _create_missing(group, user, barcodes)
    if created:
        _audit(group, user, "extend_variant_group", before, {"sizes": group.sizes, "colors": group.colors, "items": [item.item_code for item in created]})
    return created


@transaction.atomic
def set_group_prices(group, *, sale_price=None, purchase_price=None, user):
    """Give every combination of the model the same sale and/or purchase price."""

    items = Item.objects.filter(variant__group=group)
    changes = {}
    if sale_price is not None:
        changes["default_sale_price"] = sale_price
    if purchase_price is not None:
        changes["default_purchase_price"] = purchase_price
    if not changes:
        return 0
    before = {item.item_code: [str(item.default_sale_price), str(item.default_purchase_price)] for item in items}
    count = items.update(**changes)
    VariantGroup.objects.filter(pk=group.pk).update(**changes)
    _audit(group, user, "price_variant_group", before, {key: str(value) for key, value in changes.items()})
    return count


def _stock(item_ids):
    totals = defaultdict(lambda: ZERO)
    for row in StockMovement.objects.filter(item_id__in=item_ids).values("item_id", "movement_type").annotate(total=Sum("quantity")):
        sign = 1 if row["movement_type"] in IN_MOVEMENT_TYPES else -1 if row["movement_type"] in OUT_MOVEMENT_TYPES else 0
        totals[row["item_id"]] += sign * (row["total"] or ZERO)
    return totals


def matrix(group):
    """{"sizes", "colors", "rows": [{size, cells: [{variant, item, stock}|None]}], totals}."""

    variants = {(v.size, v.color): v for v in Variant.objects.filter(group=group).select_related("item")}
    stock = _stock([v.item_id for v in variants.values()])
    colors = group.colors or [""]
    rows, color_totals, grand = [], defaultdict(lambda: ZERO), ZERO
    for size in group.sizes or [""]:
        cells, size_total = [], ZERO
        for color in colors:
            variant = variants.get((size, color))
            if variant is None:
                cells.append(None)
                continue
            quantity = stock[variant.item_id]
            cells.append({"variant": variant, "item": variant.item, "stock": quantity})
            size_total += quantity
            color_totals[color] += quantity
        rows.append({"size": size, "cells": cells, "total": size_total})
        grand += size_total
    return {"sizes": group.sizes, "colors": colors, "rows": rows, "color_totals": [color_totals[c] for c in colors], "total": grand}
