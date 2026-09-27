"""UNITS-001: turn a line entered in a bigger unit into base-unit figures.

A line of ``qty`` cartons at ``price`` a carton becomes ``qty x factor`` base
units. The base price has two decimals like every price in Hesba, so it is
rounded *up* to the piastre and the few piastres over are given back as line
discount: the line total is always exactly qty x price - discount, the amount
the customer (or supplier) sees on the carton price.
"""

from decimal import ROUND_CEILING, Decimal

from django.core.exceptions import ValidationError

from config.money import money_round
from settings_core.capabilities import capability_enabled

from .models import ItemUnit


CENT = Decimal("0.01")
QTY = Decimal("0.001")


def units_enabled():
    return capability_enabled("units")


def convert_line(data, price_key, lang="ar"):
    """Return a copy of a cleaned line dict in base units (unchanged without a unit)."""

    unit = data.get("unit")
    if not unit:
        return data
    item = data["item"]
    if unit.item_id != item.pk or not unit.active:
        raise ValidationError(f"The unit {unit} does not belong to item {item.item_code}.")
    quantity = Decimal(data["quantity"])
    price = Decimal(data[price_key])
    discount = Decimal(data.get("line_discount_amount") or 0)
    base_quantity = quantity * unit.factor
    if base_quantity != base_quantity.quantize(QTY):
        raise ValidationError(f"{quantity} x {unit.factor} is not a whole number of thousandths of the base unit.")
    base_quantity = base_quantity.quantize(QTY)
    gross = money_round(quantity * price)
    target = money_round(gross - discount)
    base_price = (gross / base_quantity).quantize(CENT, rounding=ROUND_CEILING) if base_quantity else Decimal("0.00")
    base_discount = money_round(base_quantity * base_price - target)
    note = f"{quantity.normalize():f} {unit.label(lang)} × {price:.2f}"
    description = (data.get("description") or "").strip()
    result = dict(data)
    result.update({
        "quantity": base_quantity,
        price_key: base_price,
        "line_discount_amount": base_discount,
        # Printing shows the description in place of the item name, so keep the name in it.
        "description": f"{description or item.item_name} — {note}",
    })
    result.pop("unit", None)
    return result


def convert_lines(lines, price_key, lang="ar"):
    return [convert_line(line, price_key, lang) for line in lines]


def units_catalog(purchase=False):
    """{item_id: [{id, name_ar, name_en, factor, price, barcode}]} for the screens, or None when off."""

    if not units_enabled():
        return None
    catalog = {}
    for unit in ItemUnit.objects.filter(active=True, item__active=True).select_related("item"):
        catalog.setdefault(str(unit.item_id), []).append({
            "id": unit.pk, "name_ar": unit.name_ar, "name_en": unit.name_en or unit.name_ar,
            "factor": str(unit.factor), "barcode": unit.barcode,
            "price": str(unit.default_purchase_price() if purchase else unit.default_sale_price()),
        })
    return catalog


def split_quantity(item, quantity, lang="ar"):
    """'8 كرتونة + 2 قطعة' style breakdown of a base quantity, largest unit first."""

    units = list(ItemUnit.objects.filter(item=item, active=True).order_by("-factor"))
    if not units or quantity <= 0:
        return ""
    parts, left = [], Decimal(quantity)
    for unit in units:
        whole = (left / unit.factor).to_integral_value(rounding="ROUND_FLOOR")
        if whole:
            parts.append(f"{whole} {unit.label(lang)}")
            left -= whole * unit.factor
    if left:
        parts.append(f"{left.normalize():f} {item.unit}")
    return " + ".join(parts)
