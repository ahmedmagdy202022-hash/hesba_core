"""Restaurants R4: what the restaurant demos show of the kitchen screen; R1a: one add-on group and one required choice;
R1b: one combo. Called from ``prepare_demo._floor``; safe to run twice.

Restaurants close-out: ``seed_features`` (called from ``prepare_demo._restaurant_close_out``, after the shop's own samples) shows every
other merged feature (R2a, R2b, R3, R5a, R6, R7, R8, R10, R11, R14 and «إلغاء التحضير») with a few records made through the service each
screen uses, and ``tour_path`` gives the «جرّب» card the address of the screens that belong to one of them. Nothing here writes a money, stock
or ledger row itself; R5b (pay from the table) is not shown, because it needs a connected payment gateway and the online payment link stays
off in the demo (``config.demo_templates``); R12 (branches) is not shown either, because a second branch switches the whole demo into group mode."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from decimal import ROUND_FLOOR, Decimal

from master_data.models import Category, Item

from . import modifiers, services, stations
from .models import DiningTable, KitchenStation, KitchenTicket, ModifierGroup, Order


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


# ---- Restaurants close-out ----------------------------------------------------------------------------------------------------

BIRTHDAY_NOTE = "عيد ميلاد على الطاولة"  # the open bill the «الشيك» step opens
COUPON = ("MARHABA15", "كوبون الترحيب", "15")
HAPPY_HOUR = ("هابي أور المشروبات", "20", "17:00", "19:00")
TABLE_MINIMUM = "40"
SERVICE_PERCENT = "12"
BRANDS = (("بيت الواحة", "Bayt El Waha", "مطبخ بيت الواحة", "food"), ("واحة جوس", "Waha Juice", "عصائر طازجة من مطبخ بيت الواحة", "bar"))
ZONES = (("مدينة نصر", "Nasr City", "20.00", "35"), ("مصر الجديدة", "Heliopolis", "25.00", "40"), ("التجمع الخامس", "Fifth Settlement", "35.00", "55"))
PLATFORMS = (("طلبات", "Talabat", "TB-48210", "18", True), ("elmenus", "elmenus", "EM-77305", "15", False))
CANCELLED_PREPARATION = "تحضير زائد عن احتياج الوردية"


def _plain(dishes):
    """The dishes that ask no required choice: the ones a pre-order, a QR cart and an agreed line may take."""

    from . import modifiers

    found = []
    for dish in dishes:
        try:
            modifiers.choose(dish, [])
        except ValidationError:
            continue
        found.append(dish)
    return found or list(dishes)


def _options_for(dish, addons=True):
    """The option pks that make a dish's required choices (the first option of every required group) and, with ``addons``, its priced add-on."""

    from . import modifiers

    picked = []
    for group in modifiers.groups_of(dish):
        options = list(group.live_options)
        if group.min_choices:
            picked.append(options[0].pk)
        elif addons:
            picked += [option.pk for option in options if option.item_id][:1]
    return picked


def _add(order, item, owner, quantity=1):
    """``services.add_item`` with the required choice of the item when it has one (a café's coffee asks its sugar)."""

    return services.add_item(order, item, owner, quantity, options=_options_for(item, addons=False))


def _open(owner, table, **extra):
    """A new order on ``table``, which must be free (``open_order`` would hand back the open one, and the scene would pile onto it)."""

    if Order.objects.filter(table=table, status="open").exists():
        raise ValueError(f"The demo's table {table.name} is in service already.")
    return services.open_order(owner, table=table, **extra)


def _walk_in():
    from sales.pos import walk_in_customer

    return walk_in_customer()


def _bill(order, coupon="", customer=None):
    from . import charges, offers

    return charges.preview(order, lang="ar", offer=offers.Offer(coupon=coupon, customer=customer or order.customer or _walk_in()))


def seed_combo_table(dishes, drink, table, waiter, owner):
    """R1a, R1b: a table of four with a dish with extra cheese (and its required choice), a combo and a drink, sent to the kitchen:
    the order the «طاولة بإضافات» step opens."""

    from . import services
    from .models import Combo

    combo = Combo.objects.filter(item__item_name=COMBO_NAME[0]).first()
    if combo is None or not dishes:
        return None
    order = _open(owner, table, guests=4, waiter=waiter)
    first = dishes[0]
    services.add_item(order, first, owner, 2, options=_options_for(first))
    if len(dishes) > 1:
        services.add_item(order, dishes[1], owner, 1, options=_options_for(dishes[1]))
    choices = {slot.pk: slot.choices.select_related("item").order_by("pk").first() for slot in combo.slots.order_by("pk")}
    services.add_combo(order, combo, owner, {slot: choice.pk for slot, choice in choices.items()},  # the first choice of each slot,
                       options={slot: _options_for(choice.item, addons=False) for slot, choice in choices.items()})  # with what the dish requires
    if drink is not None:
        _add(order, drink, owner, 2)
    services.send_to_kitchen(order, owner)
    return order


def seed_split_and_merge(dishes, tables, waiter, cashbox, owner):
    """R2a: table 5 and table 6 are one bill (merged), and two plates of it are paid on their own (a part by items). Returns the open bill."""

    from . import services

    plain = _plain(dishes)
    first = _open(owner, tables[0], guests=2, waiter=waiter)
    line = services.add_item(first, plain[0], owner, 2)
    services.add_item(first, plain[1 % len(plain)], owner, 1)
    other = _open(owner, tables[1], guests=3, waiter=waiter)
    services.add_item(other, plain[2 % len(plain)], owner, 2)
    order = services.merge_orders(first, other, owner)
    part = services.split_order(order, {line: 1}, owner)
    services.pay(part, owner, cashbox=cashbox, tendered=Decimal("100000"))
    return order


def seed_offers(owner):
    """R6: a coupon («كوبون الترحيب», 15%) and a happy hour on the drinks, beside the sample's own offers. Skipped where promotions are off."""

    from promotions import services as promotions
    from promotions.models import Promotion

    from . import offers

    if not offers.promotions_on() or Promotion.objects.filter(coupon_code=COUPON[0]).exists():
        return None
    from reports.management.commands.seed_demo_business import BAR_CATEGORY_CODE

    promotions.save({"name": COUPON[1], "kind": "percent", "percent": COUPON[2], "coupon_code": COUPON[0], "active": "1"}, owner)
    bar = Category.objects.filter(category_code=BAR_CATEGORY_CODE).first()
    if bar is not None:
        promotions.save({"name": HAPPY_HOUR[0], "kind": "percent", "percent": HAPPY_HOUR[1], "category": str(bar.pk), "from_time": HAPPY_HOUR[2],
                         "to_time": HAPPY_HOUR[3], "active": "1"}, owner)
    return COUPON[0]


def seed_split_tenders(dishes, tables, waiter, cashboxes, owner):
    """R2b, R6: a bill of three plates with the coupon, paid two ways (what is left after 40% in the drawer, the 40% into the bank when there is one)."""

    from . import offers, services

    plain = _plain(dishes)
    coupon = COUPON[0] if offers.promotions_on() else ""
    order = _open(owner, tables[5], guests=3, waiter=waiter)
    services.add_item(order, plain[0], owner, 2)
    services.add_item(order, plain[1 % len(plain)], owner, 1)
    total = _bill(order, coupon)["total"]
    second = (total * Decimal("0.4")).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    first = total - second
    return services.pay(order, owner, cashbox=cashboxes[0], coupon=coupon, tenders=[(cashboxes[0], first), (cashboxes[1], second)])[0]


def seed_points_and_gift(dishes, tables, waiter, customer, cashbox, owner):
    """R6: a customer spends some of the points the sample month earned them, and a gift card (sold for twice that) pays half of what is left,
    so half of the card stays on it."""

    from giftcards import services as giftcards
    from loyalty import services as loyalty

    from . import offers, services

    if not (offers.loyalty_on() and offers.gift_cards_on(owner)):
        return None
    plain = _plain(dishes)
    order = _open(owner, tables[5], guests=2, waiter=waiter, customer=customer)
    services.add_item(order, plain[0], owner, 1)
    services.add_item(order, plain[1 % len(plain)], owner, 1)
    have = loyalty.balance(customer)["points"]
    points = min(have, 100)
    if points < loyalty.rules().min_points:
        points = 0
    bill = _bill(order)
    _spent, points_off = offers.points_part(customer, str(points), bill["rows"]) if points else (0, Decimal("0.00"))
    gift = ((bill["total"] - points_off) / 2).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    card = giftcards.sell(gift * 2, cashbox, owner, holder_name=customer.name, holder_phone=customer.phone)
    return services.pay(order, owner, cashbox=cashbox, customer=customer, points=str(points) if points else "", gift=(card, gift),
                        tendered=Decimal("100000"))[0]


def seed_table_requests(dishes, drink, tables):
    """R5a: a guest's order waiting at table 6 (two dishes and a drink) and a call for the bill at the table in service."""

    from . import qr
    from .models import RequestKind, TableRequest

    if TableRequest.objects.exists():
        return []
    plain = _plain(dishes)
    cart = [{"item": plain[0].pk, "quantity": 2}, {"item": plain[1 % len(plain)].pk, "quantity": 1}]
    if drink is not None:
        cart.append({"item": drink.pk, "quantity": 2, "options": _options_for(drink, addons=False)})
    return [qr.create_request(tables[5], RequestKind.ORDER, cart, note="بدون بصل لو سمحت"), qr.create_request(tables[3], RequestKind.BILL)]


def seed_zones_and_callers(master, owner):
    """R7: three delivery zones, two saved addresses for the first customer (the home one in a zone) and one for the second."""

    from . import callcentre

    if callcentre.zones().exists():
        return None
    zones = [callcentre.save_zone({"name_ar": ar, "name_en": en, "fee": fee, "minutes": minutes}, owner) for ar, en, fee, minutes in ZONES]
    first, second = master["customers"][0], master["customers"][1]
    home = callcentre.save_address(first, {"label": "البيت", "address": "12 شارع النصر", "area": "مدينة نصر", "landmark": "جنب مسجد رابعة العدوية",
                                           "zone": zones[0].pk, "is_default": True}, owner)
    callcentre.save_address(first, {"label": "الشغل", "address": "5 شارع التسعين الشمالي", "area": "التجمع الخامس", "zone": zones[2].pk}, owner)
    callcentre.save_address(second, {"label": "البيت", "address": "30 شارع الحجاز", "area": "مصر الجديدة", "zone": zones[1].pk, "is_default": True}, owner)
    return home


def seed_repeat_delivery(master, home, waiter, owner):
    """R7: the caller's last paid delivery repeated onto their home address: an open delivery with the zone's fee, its dishes at today's prices."""

    from . import callcentre

    customer = master["customers"][0]
    past = [row["order"] for row in callcentre.last_orders(customer)]
    source = next((order for order in past if order.kind == "delivery"), past[0] if past else None)
    if home is None or source is None:
        return None
    order, _count, _skipped = callcentre.repeat_as_delivery(owner, source, customer=customer, address=home)
    return order


def seed_reservations(master, tables, cashbox, owner):
    """R8: three bookings tonight (one confirmed with a deposit, one still to confirm, one for a table of two) and one for tomorrow."""

    from deposits import services as deposits

    from . import reservations
    from .models import TableReservation

    if TableReservation.objects.exists():
        return []
    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    first, second = master["customers"][1], master["customers"][0]
    made = []
    for data in (
        {"name": first.name, "phone": first.phone, "party_size": 4, "day": today.isoformat(), "time": "19:30", "table": tables[2], "customer": first, "notes": "مناسبة عائلية"},
        {"name": second.name, "phone": second.phone, "party_size": 4, "day": today.isoformat(), "time": "21:00", "table": tables[1], "customer": second},
        {"name": "هبة السيد", "phone": "01012345678", "party_size": 2, "day": today.isoformat(), "time": "21:30", "table": tables[5]},
        {"name": "كريم فؤاد", "phone": "01123456789", "party_size": 4, "day": tomorrow.isoformat(), "time": "20:00", "table": tables[0], "notes": "ركن هادي لو متاح"},
    ):
        made.append(reservations.save_reservation(data, owner))
    for reservation in made[:1] + made[2:3]:
        reservations.confirm(reservation, owner)
    if deposits.enabled():
        reservations.take_deposit(made[0], cashbox, Decimal("200.00"), owner)
    return made


def seed_preorders(master, dishes, cashbox, owner):
    """R10: two pre-orders for tomorrow's pick-up (the first with a deposit, six plates of a dish for the kitchen to prepare), and the dish's kept preparation."""

    from deposits import services as deposits

    from . import preorders
    from .models import PreOrder

    if PreOrder.objects.exists():
        return []
    tomorrow = timezone.localdate() + timedelta(days=1)
    plain = _plain(dishes)
    first, second = master["customers"][0], master["customers"][1]
    made = [preorders.save_preorder({"customer": first, "day": tomorrow.isoformat(), "time": "14:00", "notes": "غداء اجتماع الفريق"},
                                    [{"item": plain[0], "quantity": "6"}], owner),
            preorders.save_preorder({"customer": second, "day": tomorrow.isoformat(), "time": "18:00", "notes": "تغليف منفصل لكل طلب"},
                                    [{"item": plain[2 % len(plain)], "quantity": "4", "note": "بدون بصل"}, {"item": plain[1 % len(plain)], "quantity": "2"}], owner)]
    preorders.confirm(made[0], owner)
    if deposits.enabled():
        preorders.take_deposit(made[0], cashbox, Decimal("300.00"), owner)
    return made


def seed_platforms(master, dishes, cashbox, owner):
    """R14: two platforms, each its own customer (the first with its price list) and courier. «طلبات»'s order is posted on its account, handed to
    its rider and collected (the amount waits in its box); elmenus's is handed over and still with its rider."""

    from master_data.models import Customer
    from pricing import services as pricing
    from shipping import services as shipping

    from . import platforms

    if platforms.platforms():
        return []
    plain = _plain(dishes)
    made = []
    for n, (name, name_en, number, commission, collected) in enumerate(PLATFORMS, start=1):
        customer, _ = Customer.objects.get_or_create(customer_code=f"DEMO-PLT-{n:02d}", defaults={"name": name, "active": True, "import_batch_id": "DEMO-BUSINESS"})
        courier = shipping.save_courier({"name": name, "fee_per_parcel": "0"}, owner)
        platform = platforms.save_platform(customer, courier, commission, owner)
        if n == 1 and pricing is not None:
            from config.money import money_round

            price_list = pricing.save_price_list(user=owner, code="DEMO-PLT-01", name_ar=f"أسعار {name}", name_en=f"{name_en} prices")
            pricing.set_item_prices(price_list, {dish: money_round(Decimal(dish.default_sale_price) * Decimal("1.1")) for dish in plain[:3]}, owner)
            pricing.assign_customers(price_list, [customer], owner)
        from . import services

        order = platforms.open_order(owner, platform, number)
        for dish in plain[:2]:
            services.add_item(order, dish, owner, 2)
        services.pay(order, owner, cashbox=cashbox)
        order = Order.objects.get(pk=order.pk)  # as paid now: it has its invoice
        platforms.hand_over(order, owner)
        if collected:
            platforms.collected(order, owner)
        made.append(order)
    return made


def seed_brands(catalog, drink, waiter, owner):
    """R11: two brands from the one kitchen (the food, and the drinks bar), and a takeaway of the drinks brand open. Run after every other order,
    so the first brand does not stick to the ones the sample made before it."""

    from reports.management.commands.seed_demo_business import BAR_CATEGORY_CODE

    from . import brands, services
    from .models import Brand

    if Brand.objects.exists():
        return []
    food = Category.objects.filter(category_code="DEMO-CAT-01").first()
    bar = Category.objects.filter(category_code=BAR_CATEGORY_CODE).first()
    made = []
    for name_ar, name_en, header, which in BRANDS:
        category = food if which == "food" else bar
        made.append(brands.save_brand({"name_ar": name_ar, "name_en": name_en, "header": header, "sort": str(len(made) + 1),
                                       "categories": [category.pk] if category else []}, owner))
    if drink is not None:
        order = services.open_order(owner, kind="takeaway", waiter=waiter, brand=made[1])
        _add(order, drink, owner, 2)
    return made


def seed_check_table(dishes, drink, table, waiter, owner):
    """R3: service charge (12% on dine-in) and a minimum per guest (40.00), switched on, and a table of four whose bill needs the minimum's
    top-up: the check the «الشيك» step opens. Run last, so it is the only seeded bill that carries them."""

    from . import charges, services

    order = Order.objects.filter(notes=BIRTHDAY_NOTE, status="open").first()
    if order is not None:
        return order
    charges.save_settings({"percent": SERVICE_PERCENT, "kinds": ["dine_in"], "minimum": TABLE_MINIMUM}, owner)
    order = _open(owner, table, guests=4, waiter=waiter, notes=BIRTHDAY_NOTE)
    plain = _plain(dishes)
    services.add_item(order, plain[-1], owner, 1)
    if drink is not None:
        _add(order, drink, owner)
    return order


def _has_cancelled_preparation(dish):
    from manufacturing.models import ProductionRun, RunStatus

    return ProductionRun.objects.filter(recipe__product=dish, status=RunStatus.CANCELLED).exists()


def seed_cancelled_preparation(dishes, owner):
    """«إلغاء التحضير»: six plates prepared ahead and cancelled whole (the materials come back), then four kept: the dish's page lists both."""

    from . import kitchen

    dish = dishes[0]
    if not kitchen.is_kitchen_made(dish) or _has_cancelled_preparation(dish):
        return None
    run = kitchen.prepare(dish, "6", owner)
    kitchen.cancel_preparation(run, owner, CANCELLED_PREPARATION)
    return kitchen.prepare(dish, "4", owner)


def seed_features(master, catalog, dishes, owner):
    """Every feature the restaurant demo did not yet show, in the order that leaves each seeded figure as it was: the open scenes first, the
    paid ones, the brands (so none of the earlier orders carries one), then the check table with the service charge and the minimum."""

    from staff.models import Employee

    tables = list(DiningTable.objects.order_by("sort_order", "name"))
    if len(tables) < 6 or not dishes or Order.objects.filter(notes=BIRTHDAY_NOTE).exists():
        return None
    waiter = Employee.objects.filter(title=catalog.staff[1]).first()
    drink = master["items"][catalog.bar[0]] if catalog.bar else None
    cashboxes = list(master["cashboxes"])
    from cashboxes.models import Cashbox, CashboxKind

    bank = Cashbox.objects.filter(kind=CashboxKind.BANK, active=True).order_by("pk").first()
    cash = cashboxes[0]
    seed_combo_table(dishes, drink, tables[0], waiter, owner)
    seed_split_and_merge(dishes, tables[4:6], waiter, cash, owner)
    seed_offers(owner)
    seed_split_tenders(dishes, tables, waiter, [cash, bank or cashboxes[1]], owner)
    seed_points_and_gift(dishes, tables, waiter, master["customers"][0], cash, owner)
    seed_table_requests(dishes, drink, tables)
    home = seed_zones_and_callers(master, owner)
    seed_repeat_delivery(master, home, waiter, owner)
    seed_reservations(master, tables, cash, owner)
    seed_preorders(master, dishes, cash, owner)
    seed_platforms(master, dishes, cash, owner)
    seed_cancelled_preparation(dishes, owner)
    seed_brands(catalog, drink, waiter, owner)
    return seed_check_table(dishes, drink, tables[2], waiter, owner)


# ---- the «جرّب» tour: the screens that belong to one of these records -----------------------------------------------------------

def tour_path(url_name):
    """The address of the demo screen a restaurant «جرّب» step opens, for the steps that belong to a record the demo seeded: the open table with
    the combo, the merged one, the check that carries the service charge, a table's QR menu, the day of the bookings and of the pre-orders, the
    caller with saved addresses, the platforms, the brands, the dish with a cancelled preparation. None when the demo has no such record (an
    emptied demo, a bakery with no tables), and the card then drops the step. A path may carry its own query."""

    from django.urls import reverse

    from . import kitchen
    from .models import Brand, CustomerAddress, OrderLine, PreOrder, TableReservation, TableRequest

    if url_name == "restaurant:order_combo":
        line = OrderLine.objects.filter(combo__isnull=False, order__status="open", voided=False).select_related("order").order_by("pk").first()
        return reverse("restaurant:order", args=[line.order_id]) if line else None
    if url_name == "restaurant:order_split":
        order = Order.objects.filter(status="open", split_from__isnull=True, splits__isnull=False).order_by("pk").first()
        return reverse("restaurant:order", args=[order.pk]) if order else None
    if url_name == "restaurant:check":
        order = Order.objects.filter(notes=BIRTHDAY_NOTE, status="open").first()
        if order is None:
            return None
        from promotions.models import Promotion

        coupon = f"?coupon={COUPON[0]}" if Promotion.objects.filter(coupon_code=COUPON[0], active=True).exists() else ""
        return reverse("restaurant:check", args=[order.pk]) + coupon
    if url_name == "restaurant:table_menu":
        request = TableRequest.objects.select_related("table").order_by("pk").first()
        return reverse("table_menu", args=[request.table.qr_key]) if request else None
    if url_name == "restaurant:reservations":
        reservation = TableReservation.objects.order_by("starts_at").first()
        return f"{reverse('restaurant:reservations')}?day={timezone.localtime(reservation.starts_at).date().isoformat()}" if reservation else None
    if url_name == "restaurant:preorders":
        preorder = PreOrder.objects.order_by("starts_at").first()
        day = timezone.localtime(preorder.starts_at).date().isoformat() if preorder else None
        return f"{reverse('restaurant:preorders')}?day={day}&make={day}" if day else None
    if url_name == "restaurant:call":
        address = CustomerAddress.objects.filter(active=True, customer__phone__gt="").select_related("customer").order_by("pk").first()
        return f"{reverse('restaurant:call')}?phone={address.customer.phone}" if address else None
    if url_name == "restaurant:platforms":
        from . import platforms

        return reverse("restaurant:platforms") if platforms.platforms() else None
    if url_name == "restaurant:brands":
        return reverse("restaurant:brands") if Brand.objects.exists() else None
    if url_name == "restaurant:dish":
        dish = Item.objects.filter(item_code="DEMO-ITEM-01").first()
        if dish is None or not kitchen.is_kitchen_made(dish) or not _has_cancelled_preparation(dish):
            return reverse("restaurant:costing")
        return reverse("restaurant:dish", args=[dish.pk])
    return None
