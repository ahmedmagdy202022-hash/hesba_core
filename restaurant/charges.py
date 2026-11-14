"""Restaurants R3 (HG-113): the service charge, the minimum per guest, and the bill they make.

Rules, stated once:

* three system settings, no schema: ``restaurant.service_percent`` (empty = off; the usual figure is 12), the order kinds it
  applies to (``restaurant.service_kinds``; dine-in only by default) and ``restaurant.min_per_guest`` (empty = off);
* the bill, in this order: (1) the food lines after their line discounts (R1b's combos; R1a's priced add-ons are food);
  (2) the delivery fee, never in any base; (3) the minimum, on a dine-in bill only: if guests × the minimum is above the
  food, a top-up line «فرق الحد الأدنى» for the difference; (4) the service: percent × (food + top-up), rounded to the
  piastre; (5) VAT per row at each item's own rate; the bill discount stays the cashier's, after tax (TAX-001), so it never
  reduces the service line;
* a table paid in parts (R2a): each part pays service on its own food and never a top-up. The minimum is the whole table's:
  it is settled on the table's own bill, which is always paid last (an open part blocks it), against the table's guests
  and the food of the table's bill plus that of its parts already paid on a posted invoice (and, while one is still open,
  of that part too, so the bill box and the check show what will post);
* service and the top-up are ordinary invoice lines of two non-stock service items, ``SERVICE-CHARGE`` and ``MIN-CHARGE``,
  made once at the first bill that needs them (like ``DELIVERY-FEE``) and refused if someone makes them stock-tracked.

``preview`` works the whole bill out and writes nothing: the order page, the board, the bill box's «الباقي» and the printed
check all read it, so every figure shown is the one ``bill_rows`` posts. With both settings off it returns the bill exactly
as before R3 and touches no item.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from .models import OrderKind, OrderStatus

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
SERVICE_ITEM_CODE = "SERVICE-CHARGE"
MIN_ITEM_CODE = "MIN-CHARGE"
CHARGE_CODES = (SERVICE_ITEM_CODE, MIN_ITEM_CODE)
PERCENT_KEY, KINDS_KEY, MINIMUM_KEY = "restaurant.service_percent", "restaurant.service_kinds", "restaurant.min_per_guest"
DEFAULT_KINDS = (OrderKind.DINE_IN.value,)
MAX_PERCENT = Decimal("50")
MAX_MINIMUM = Decimal("100000")

MESSAGES = {
    "ar": {
        "percent": "نسبة الخدمة لازم تكون رقم أكبر من صفر ولحد {max}%، أو سيبها فاضية لو مفيش خدمة.",
        "kinds": "اختار نوع طلب واحد على الأقل تتحسب عليه الخدمة.",
        "minimum": "الحد الأدنى للفرد لازم يكون رقم صفر أو أكبر (أقل من {max})، أو سيبه فاضي لو مفيش حد أدنى.",
        "service_item": "خدمة", "min_item": "فرق الحد الأدنى",
        "service_line": "خدمة {percent}%", "min_line": "فرق الحد الأدنى ({guests} × {minimum})",
        "stocked": "الصنف {code} متسجل صنف مخزني؛ الخدمة والحد الأدنى لازم يكونوا خدمة. عدّله من الأصناف وشيل «يتتبع في المخزن».",
        "charge_item": "«{item}» بيتحسب لوحده على الحساب؛ مش طبق يتطلب.",
    },
    "en": {
        "percent": "The service charge must be a number above zero and up to {max}%, or left empty for none.",
        "kinds": "Choose at least one order type the service charge applies to.",
        "minimum": "The minimum per guest must be zero or more (below {max}), or left empty for none.",
        "service_item": "خدمة", "min_item": "فرق الحد الأدنى",
        "service_line": "Service {percent}%", "min_line": "Minimum charge top-up ({guests} × {minimum})",
        "stocked": "Item {code} is a stocked item; the service charge and the minimum must be services. Edit it under Items and untick stock tracking.",
        "charge_item": "“{item}” is added to the bill by itself; it is not a dish to order.",
    },
}


def words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


# ---- the settings ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    percent: Decimal | None
    kinds: tuple
    minimum: Decimal | None

    @property
    def on(self):
        return self.percent is not None or self.minimum is not None


def _stored():
    from settings_core.models import SystemSetting

    return dict(SystemSetting.objects.filter(key__in=(PERCENT_KEY, KINDS_KEY, MINIMUM_KEY), active=True).values_list("key", "value"))


def _number(raw):
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _kinds(raw):
    kinds = tuple(kind for kind in OrderKind.values if kind in {part.strip() for part in str(raw or "").split(",")})
    return kinds


def settings():
    """The three settings as they are now (one query). A value that is no longer valid reads as off."""

    stored = _stored()
    percent = _number(stored.get(PERCENT_KEY) or "")
    percent = percent if percent is not None and ZERO < percent <= MAX_PERCENT else None
    minimum = _number(stored.get(MINIMUM_KEY) or "")
    minimum = money_round(minimum) if minimum is not None and ZERO < minimum < MAX_MINIMUM else None
    kinds = _kinds(stored[KINDS_KEY]) if KINDS_KEY in stored else DEFAULT_KINDS
    return Config(percent=percent, kinds=kinds or DEFAULT_KINDS, minimum=minimum)


def _shown_percent(percent):
    return f"{percent.normalize():f}" if percent is not None else ""


def _snapshot(config):
    return {"percent": _shown_percent(config.percent), "kinds": list(config.kinds), "minimum": f"{config.minimum:.2f}" if config.minimum is not None else ""}


@transaction.atomic
def save_settings(data, user, lang="ar"):
    """Save the service percent, its order kinds and the minimum per guest (empty or 0 switches each off). Audited.

    Everything is checked before anything is written. The screen that calls this checks who may (the tables page's own
    permission)."""

    from settings_core.models import SystemSetting

    text = words(lang)
    raw_percent = str(data.get("percent") or "").strip()
    percent = None
    if raw_percent:
        percent = _number(raw_percent)
        if percent is None or percent < 0 or percent > MAX_PERCENT:
            raise ValidationError(text["percent"].format(max=MAX_PERCENT))
        percent = percent.quantize(Decimal("0.01")) if percent else None
    kinds = _kinds(",".join(str(kind) for kind in (data.get("kinds") or [])))
    if percent is not None and not kinds:
        raise ValidationError(text["kinds"])
    raw_minimum = str(data.get("minimum") or "").strip()
    minimum = None
    if raw_minimum:
        minimum = _number(raw_minimum)
        if minimum is None or minimum < 0 or minimum >= MAX_MINIMUM:
            raise ValidationError(text["minimum"].format(max=MAX_MINIMUM))
        minimum = money_round(minimum) if minimum else None
    before = _snapshot(settings())
    after = Config(percent=percent, kinds=kinds or DEFAULT_KINDS, minimum=minimum)
    values = {PERCENT_KEY: (_shown_percent(percent), SystemSetting.DataType.DECIMAL, "HG-113: the service charge percent on a restaurant bill (empty = off)."),
              KINDS_KEY: (",".join(after.kinds), SystemSetting.DataType.STRING, "HG-113: the order kinds the service charge applies to."),
              MINIMUM_KEY: (f"{minimum:.2f}" if minimum is not None else "", SystemSetting.DataType.DECIMAL,
                            "HG-113: the minimum per guest on a dine-in bill (empty = off).")}
    for key, (value, data_type, description) in values.items():
        SystemSetting.objects.update_or_create(key=key, defaults={"value": value, "data_type": data_type, "active": True, "description": description})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="restaurant", action="save_charges", object_type="settings_core.SystemSetting",
                            object_id="restaurant.charges", before_data=before, after_data=_snapshot(after))
    return after


# ---- the items ------------------------------------------------------------------------------------------------------


def _item_name(code, text):
    return text["service_item"] if code == SERVICE_ITEM_CODE else text["min_item"]


def charge_item(code, lang="ar"):
    """The service item a charge is billed as; made once (two first bills at once share it), refused if stock-tracked."""

    from master_data.models import Item

    text = words(lang)
    item, _ = Item.objects.get_or_create(item_code=code, defaults={"item_name": _item_name(code, text), "is_stock_tracked": False,
                                                                     "default_sale_price": Decimal("0")})
    if item.is_stock_tracked:  # a charge must never move stock
        raise ValidationError(text["stocked"].format(code=code))
    return item


def refuse_charge_item(item, lang="ar"):
    """A charge item is never a dish on an order (it would be counted in the top dishes and in the service base)."""

    if item is not None and item.item_code in CHARGE_CODES:
        raise ValidationError(words(lang)["charge_item"].format(item=item.item_name))


def uncoded_items():
    """The charge codes whose item has no e-receipt code yet (an item not made yet counts: it will have none)."""

    from einvoice.models import ItemCode

    coded = set(ItemCode.objects.filter(item__item_code__in=CHARGE_CODES).values_list("item__item_code", flat=True))
    return [code for code in CHARGE_CODES if code not in coded]


# ---- the bill -------------------------------------------------------------------------------------------------------


def _food(lines):
    return money_round(sum((line.amount for line in lines if not line.voided), ZERO))


def _parts_food(order):
    """R2a: the food of ``order``'s parts already paid on a posted invoice (a cancelled invoice no longer counts) and of its
    parts still open. An open part never exists when the table's bill is paid (``_no_open_part``); counting it keeps the
    bill box and the printed check right meanwhile, since the part is either paid later or put back onto this bill."""

    from django.db.models import Q

    if order.split_from_id:
        return ZERO
    parts = order.splits.filter(Q(status=OrderStatus.OPEN) | Q(status=OrderStatus.PAID, invoice__status="posted")).prefetch_related("lines")
    return money_round(sum((_food(list(part.lines.all())) for part in parts), ZERO))


def amounts(order, lines, config=None):
    """{"food", "topup", "service", "percent", "minimum"} for ``order``'s live ``lines``. Reads the parts only when a minimum is on."""

    config = config or settings()
    food = _food(lines)
    topup = service = ZERO
    if config.minimum is not None and order.kind == OrderKind.DINE_IN and not order.split_from_id and any(not line.voided for line in lines):
        floor = money_round(Decimal(order.guests) * config.minimum)
        topup = max(floor - food - _parts_food(order), ZERO)
    if config.percent is not None and order.kind in config.kinds:
        service = money_round((food + topup) * config.percent / HUNDRED)
    return {"food": food, "topup": topup, "service": service, "percent": config.percent, "minimum": config.minimum}


def charge_rows(order, lines, lang="ar", config=None, make=False):
    """The top-up and the service as invoice rows (in that order), only those above 0.00. ``make`` gets or makes their items
    (paying); without it the item is the one that exists, or None (a preview writes nothing)."""

    from master_data.models import Item

    config = config or settings()
    if not config.on:
        return []
    text = words(lang)
    figures = amounts(order, lines, config)
    rows = []
    for kind, code, amount, description in (
            ("min", MIN_ITEM_CODE, figures["topup"], text["min_line"].format(guests=order.guests, minimum=f"{config.minimum:.2f}" if config.minimum else "")),
            ("service", SERVICE_ITEM_CODE, figures["service"], text["service_line"].format(percent=_shown_percent(config.percent)))):
        if amount <= 0:
            continue
        item = charge_item(code, lang) if make else Item.objects.filter(item_code=code).first()
        rows.append({"kind": kind, "code": code, "item": item, "quantity": Decimal("1"), "unit_sale_price": amount, "line_discount_amount": ZERO,
                     "description": description})
    return rows


def _row_tax(item, net, rate_cache):
    from taxes.services import default_rate, rate_for

    key = item.pk if item is not None else None
    if key not in rate_cache:
        rate_cache[key] = rate_for(item) if item is not None else default_rate()
    rate = rate_cache[key]
    return money_round(net * (rate.rate if rate is not None else ZERO) / HUNDRED)


def preview(order, lines=None, lang="ar", config=None):
    """The whole bill as ``pay`` would post it, before the cashier's discount, writing nothing:

    {"rows": [{"kind", "code", "item", "quantity", "unit_sale_price", "line_discount_amount", "description", "net", "tax",
    "line"}], "food", "fee", "topup", "service", "percent", "minimum", "subtotal", "tax", "total"}. ``kind`` is "food",
    "addon", "fee", "min" or "service"; ``line`` is the order line behind a food row. VAT is per row as ``checkout`` works
    it (the item's rate; for a fee or charge item not made yet, the default rate it will take), and only when VAT is on."""

    from taxes.services import vat_enabled

    from . import modifiers
    from .services import FEE_ITEM_CODE, MESSAGES, _row_description

    from master_data.models import Item

    config = config or settings()
    if lines is None:
        lines = list(order.lines.select_related("item", "combo__item"))
    live = [line for line in lines if not line.voided]
    by_pk = {line.pk: line for line in live}
    rows = []
    for line in modifiers.families(live):
        addon = line.parent_id in by_pk
        rows.append({"kind": "addon" if addon else "food", "code": line.item.item_code, "item": line.item, "quantity": line.quantity,
                     "unit_sale_price": line.unit_price, "line_discount_amount": line.line_discount,
                     "description": modifiers.bill_description(line, by_pk[line.parent_id]) if addon else _row_description(line, lang), "line": line})
    fee = order.delivery_fee if order.kind == OrderKind.DELIVERY and order.delivery_fee > 0 else ZERO
    if fee:
        rows.append({"kind": "fee", "code": FEE_ITEM_CODE, "item": Item.objects.filter(item_code=FEE_ITEM_CODE).first(), "quantity": Decimal("1"),
                     "unit_sale_price": fee, "line_discount_amount": ZERO, "description": MESSAGES[lang]["fee_line"]})
    rows.extend(charge_rows(order, live, lang, config))
    vat = vat_enabled()
    cache = {}
    for row in rows:
        row.setdefault("line", None)
        row["net"] = money_round(row["quantity"] * row["unit_sale_price"] - row["line_discount_amount"])
        row["tax"] = _row_tax(row["item"], row["net"], cache) if vat else ZERO
    figures = {kind: money_round(sum((row["net"] for row in rows if row["kind"] == kind), ZERO)) for kind in ("min", "service")}
    subtotal = money_round(sum((row["net"] for row in rows), ZERO))
    tax = money_round(sum((row["tax"] for row in rows), ZERO))
    return {"rows": rows, "food": _food(live), "fee": fee, "topup": figures["min"], "service": figures["service"], "percent": config.percent,
            "minimum": config.minimum, "subtotal": subtotal, "tax": tax, "total": money_round(subtotal + tax)}
