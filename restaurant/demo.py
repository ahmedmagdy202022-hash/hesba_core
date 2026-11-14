"""Restaurants R4: what the restaurant demos show of the kitchen screen; R1a: one add-on group and one required choice;
R1b: one combo. Called from ``prepare_demo._floor``; safe to run twice."""

from datetime import timedelta

from django.utils import timezone

from decimal import ROUND_FLOOR, Decimal

from master_data.models import Category, Item

from . import modifiers, services, stations
from .models import KitchenStation, KitchenTicket, ModifierGroup


def seed_stations(catalog, owner):
    """Two stations, «المطبخ» (takes everything the bar does not) and «المشروبات» (the bar's category), where the catalog has a bar."""

    if not catalog.bar:
        return []
    from reports.management.commands.seed_demo_business import BAR_CATEGORY_CODE

    bar = Category.objects.filter(category_code=BAR_CATEGORY_CODE).first()
    made = []
    for name_ar, name_en, sort, is_default, categories in (("المطبخ", "Kitchen", 1, True, []), ("المشروبات", "Drinks", 2, False, [bar.pk] if bar else [])):
        station = KitchenStation.objects.filter(name_ar=name_ar).first()
        if station is None:
            station = stations.save_station({"name_ar": name_ar, "name_en": name_en, "sort": sort, "is_default": is_default, "categories": categories}, owner)
        made.append(station)
    return made


def age_tickets(order, minutes_by_station):
    """Backdate a seeded order's tickets (``{station name: minutes ago}``), so the screen shows fresh, amber and red cards."""

    now = timezone.now()
    for ticket in KitchenTicket.objects.filter(order=order).select_related("station"):
        minutes = minutes_by_station.get(ticket.station.name_ar if ticket.station_id else "")
        if minutes is not None:
            KitchenTicket.objects.filter(pk=ticket.pk).update(sent_at=now - timedelta(minutes=minutes))


def mark_ready(order, item):
    """The station marks one seeded line ready (as a tap on the screen does)."""

    line = order.lines.filter(item=item, voided=False).first()
    if line is not None:
        stations.mark_line(line)
    return line


def void_after_firing(order, item, owner, reason):
    line = order.lines.filter(item=item, voided=False, ticket__isnull=False).first()
    if line is not None:
        services.void_line(line, owner, reason=reason)
    return line


#: R1a: the required question each restaurant demo asks of one dish: (catalog key, dish index, group ar, group en, options).
REQUIRED_CHOICE = {
    "restaurants.restaurant": (1, "درجة التسوية", "Doneness", (("ميديم", "Medium"), ("ويل دن", "Well done"))),
    "restaurants.cafe": (0, "السكر", "Sugar", (("سادة", "No sugar"), ("مظبوط", "Medium"), ("زيادة", "Sweet"))),
}
DEFAULT_CHOICE = (1, "طريقة التقديم", "How it is served", (("عادي", "Regular"), ("حار", "Spicy")))
ADDON_CODE = "DEMO-ADDON-01"


def seed_modifiers(catalog, dishes, owner):
    """R1a (HG-109): «إضافات» (optional, up to 2: a priced extra cheese at 15.00 and a free «من غير بصل») on the first two
    dishes, and one required choice on one dish, through the real services. Run after the demo's orders are placed, so no
    seeded figure moves. Returns the two groups."""

    if not dishes:
        return []
    index, name_ar, name_en, choices = REQUIRED_CHOICE.get(catalog.key, DEFAULT_CHOICE)
    dish = dishes[index % len(dishes)]
    cheese, _ = Item.objects.get_or_create(item_code=ADDON_CODE, defaults={"item_name": "جبنة زيادة", "category": dish.category, "unit": dish.unit,
                                                                          "default_sale_price": Decimal("15.00"), "is_stock_tracked": False,
                                                                          "import_batch_id": "DEMO-BUSINESS"})
    extras = ModifierGroup.objects.filter(name_ar="إضافات").first() or modifiers.save_group(
        {"name_ar": "إضافات", "name_en": "Extras", "min_choices": "0", "max_choices": "2", "sort": "2",
         "options": [{"name_ar": "جبنة زيادة", "name_en": "Extra cheese", "item": cheese}, {"name_ar": "من غير بصل", "name_en": "No onions"}]}, owner)
    required = ModifierGroup.objects.filter(name_ar=name_ar).first() or modifiers.save_group(
        {"name_ar": name_ar, "name_en": name_en, "min_choices": "1", "max_choices": "1", "sort": "1",
         "options": [{"name_ar": ar, "name_en": en} for ar, en in choices]}, owner)
    for target in dict.fromkeys(dishes[:2] + [dish]):
        wanted = ([required] if target.pk == dish.pk else []) + ([extras] if target in dishes[:2] else [])
        if not modifiers.groups_of(target):
            modifiers.set_dish_groups(target, wanted, owner)
    return [extras, required]


COMBO_NAME = ("وجبة التوفير", "Saver combo")


def seed_combo(catalog, dishes, items, owner):
    """R1b (HG-112): one combo through the real set-up: «الطبق» (the first two dishes, the dearer one for half the difference
    more) and «المشروب» (two other menu items, the bar's first), priced 10% under its cheapest choices, rounded down to 5.
    Run after every demo order, so no seeded figure moves; nothing is ordered with it. Returns the combo, or None."""

    from . import combos
    from .models import Combo

    existing = Combo.objects.filter(item__item_name=COMBO_NAME[0]).first()
    if existing is not None or len(dishes) < 2:
        return existing
    first, second = dishes[0], dishes[1]
    bar = [items[index] for index in (catalog.bar or ()) if index < len(items)]
    others = [item for item in bar + list(items) if item.pk not in (first.pk, second.pk) and item.active]
    drinks = list(dict.fromkeys(others))[:2]
    if not drinks:
        return None
    step = Decimal("5")
    extra = (max(Decimal("0"), Decimal(second.default_sale_price) - Decimal(first.default_sale_price)) / 2 / step).to_integral_value(rounding=ROUND_FLOOR) * step
    cheapest = min(Decimal(first.default_sale_price), Decimal(second.default_sale_price) - extra) + min(Decimal(item.default_sale_price) for item in drinks)
    price = (cheapest * Decimal("0.9") / step).to_integral_value(rounding=ROUND_FLOOR) * step
    return combos.save_combo({"name_ar": COMBO_NAME[0], "name_en": COMBO_NAME[1], "price": str(price), "active": True,
                              "slots": [{"name_ar": "الطبق", "name_en": "Main", "choices": [{"item": first}, {"item": second, "upcharge": str(extra)}]},
                                        {"name_ar": "المشروب", "name_en": "Drink", "choices": [{"item": item} for item in drinks]}]}, owner)
