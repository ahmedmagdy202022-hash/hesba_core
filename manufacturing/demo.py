"""Manufacturing F14: demo data for every merged manufacturing feature, and the addresses of the «جرّب» tour's own records.

The food factory and the furniture workshop are built through the services their screens use, never by writing a row where a
service exists. Two hooks run inside ``prepare_demo``'s extras:

* ``seed_routing`` runs **before** the sample production orders are opened, so those orders copy a routed stage list (a work centre
  on every stage) and the by-product is on the recipe before any order starts (a recipe with an order on the floor refuses a change
  to its outputs);
* ``seed_features`` runs **after** the payroll, so the new employees (the production worker) are not paid by it. It is also where the
  food's quality release is switched on: after every plain run of the sample month, so no run of that recipe skips its check.

From then on the sample makes that product the way its recipe page demands: ``checked_batch`` (called by the sample's daily top-up)
opens an order, closes its stages, finishes it on hold and releases all of it with a check.

Every date is counted from the day the demo is built: an order that is late, one due today and some later. Nothing here changes a
rule. Where a service refuses, the build stops loudly: a demo that cannot be built says so.

Food: the lots come from production runs (F3), an order made from a confirmed sales order (F1), a draft purchase order from the
requirements (F2), work centres with a schedule (F6a, F6b), a finished order with actuals (F12), a lot on quality hold and one released
with a reject (F5), a stage out at an outside centre and one back with its posted bill (F4), a by-product (F9), maintenance (F10) and
the production worker's «شغلي» (F8). Furniture: a quotation with specifications and a cost sheet, one made into a custom job (F7),
a breakdown on the saw (F10), sawdust as a by-product (F9). The other factories keep the catalogue and the orders they always had.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from math import ceil

from django.utils import timezone

WORKER_USERNAME = "worker"
WORKER = ("خالد عبد الغني", "عامل إنتاج")
RESTOCK_NUMBER = "DEMO-PI-MFG-01"
PARTS_NUMBER = "DEMO-PI-MFG-02"

#: The recipes, by the position of their product in the catalogue (items 4 and 5 are the planted low and sold-out ones: no order of this
#: seed makes them).
FIRST, SECOND, THIRD = 0, 1, 2

OUTSIDE_SUPPLIER = ("DEMO-SUP-OUT", "مركز تعبئة النيل")
TECHNICIAN_SUPPLIER = ("DEMO-SUP-MNT", "مركز الصيانة الصناعية")

# ---- the food factory ----------------------------------------------------------------------------------------------------

#: (code, Arabic name, English name, kind, hours a day, rate per hour)
FOOD_CENTRES = (
    ("DEMO-MIX", "خلاط العجين", "Dough mixer", "machine", D("8"), D("80.00")),
    ("DEMO-OVEN", "فرن الخَبز", "Baking oven", "machine", D("10"), D("150.00")),
    ("DEMO-PACK", "خط التعبئة والتغليف", "Packing line", "line", D("8"), D("70.00")),
)
#: (Arabic, English, centre code, minutes per carton, setup minutes): the routing of the first three recipes
FOOD_ROUTING = (
    ("تحضير وخلط", "Preparation & mixing", "DEMO-MIX", D("6"), D("20")),
    ("خَبز", "Baking", "DEMO-OVEN", D("12"), D("15")),
    ("تعبئة وتغليف", "Packing", "DEMO-PACK", D("5"), D("10")),
)
RELEASE_CRITERIA = (
    {"name_ar": "شكل الكرتونة وغلافها", "name_en": "Carton and wrapping", "kind": "pass_fail"},
    {"name_ar": "وزن الكرتونة بالكيلو", "name_en": "Carton weight (kg)", "kind": "number", "min_value": "4.8", "max_value": "5.2"},
)
CRUMBS = ("DEMO-BYP-01", "فتات كيك للتحلية", "كجم", D("30.00"), "40.00")
OVEN_PART = ("DEMO-PART-01", "عنصر تسخين للفرن", "قطعة", D("350.00"))
#: What is left of each material (by catalogue position) once the orders below have taken their share: the butter, flour and sugar are less
#: than the orders still to start need, so the requirements screen has three materials short whatever the day's trade did to the stock.
FOOD_BUFFER = {0: D("60"), 1: D("20"), 2: D("6"), 3: D("40")}
#: The orders the food seed starts, by recipe position and quantity; the restock buys what they take out of stock.
FOOD_STARTED = ((FIRST, "20"), (FIRST, "15"), (SECOND, "10"), (THIRD, "12"), (THIRD, "18"), (SECOND, "8"), (FIRST, "30"))
EXTRA_FLOUR = D("3")
LATE_NOTE = "تأخر لأن الخلاط وقف"
DAILY_NOTE = "دفعة اليوم"

# ---- the furniture workshop ----------------------------------------------------------------------------------------------

FURNITURE_CENTRES = (
    ("DEMO-SAW", "منشار الخشب", "Wood saw", "machine", D("8"), D("90.00")),
    ("DEMO-ASM", "طاولة التجميع", "Assembly bench", "manual", D("8"), D("55.00")),
    ("DEMO-PAINT", "كابينة الدهان", "Spray booth", "machine", D("8"), D("95.00")),
)
FURNITURE_ROUTING = (
    ("تقطيع الخشب", "Cutting", "DEMO-SAW", D("6"), D("30")),
    ("تجميع", "Assembly", "DEMO-ASM", D("18"), D("20")),
    ("دهان", "Painting", "DEMO-PAINT", D("10"), D("15")),
)
SAWDUST = ("DEMO-BYP-02", "نشارة خشب", "كيس", D("15.00"), "20.00")
SAW_PART = ("DEMO-PART-02", "شفرة منشار", "قطعة", D("520.00"))
FURNITURE_BUFFER = {0: D("8"), 1: D("10"), 2: D("15"), 3: D("6")}
#: The wardrobes' cost sheet, for the whole job of six: (raw material position, quantity).
WARDROBE_MATERIALS = ((0, "9"), (1, "24"), (2, "12"), (3, "12"))
FURNITURE_STARTED = ((FIRST, "10"), (FIRST, "6"))

FACTORIES = {
    "manufacturing.food": {"centres": FOOD_CENTRES, "routing": FOOD_ROUTING, "byproduct": (SECOND, CRUMBS, "0.4"), "worker_centre": "DEMO-OVEN"},
    "manufacturing.furniture": {"centres": FURNITURE_CENTRES, "routing": FURNITURE_ROUTING, "byproduct": (FIRST, SAWDUST, "0.5"), "worker_centre": "DEMO-ASM"},
}


def seeds(catalog):
    """Whether this factory's demo gets the F14 records (the food factory and the furniture workshop)."""

    return catalog.key in FACTORIES


def _is_food(catalog):
    return catalog.key == "manufacturing.food"


def _centre(code):
    from .models import WorkCentre

    return WorkCentre.objects.get(code=code)


def _recipe(master, index):
    from .models import Recipe

    return Recipe.objects.get(product=master["items"][index], active=True)


def _supplier(code, name):
    from master_data.models import Supplier

    return Supplier.objects.update_or_create(supplier_code=code, defaults={"name": name, "active": True, "import_batch_id": "DEMO-BUSINESS"})[0]


def _item(master, code, name, unit, price, sale="0"):
    """A stock-tracked supply of the factory that is not in the catalogue (a spare part, a by-product)."""

    from master_data.models import Item

    values = {"item_name": name, "category": master["raw"][0].category, "unit": unit, "default_sale_price": D(sale), "default_purchase_price": price,
              "min_stock": D("0"), "active": True, "import_batch_id": "DEMO-BUSINESS"}
    return Item.objects.update_or_create(item_code=code, defaults=values, create_defaults={**values, "is_stock_tracked": True})[0]


def _buy(master, owner, number, day, supplier, lines, paid_share="0"):
    """A posted purchase invoice of ``lines`` [(item, quantity)] at each item's purchase price, through the sample's own posting."""

    from reports.management.commands.seed_demo_business import Command as Seed

    seed = Seed()
    subtotal = sum((seed._line_total(item.default_purchase_price, quantity) for item, quantity in lines), D("0"))
    paid = (subtotal * D(paid_share)).quantize(D("0.01"))
    return seed._post_purchase(number, day, supplier, master["location"], master["cashboxes"][0], lines, paid, owner)


def _moment(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


# ---- the early hook: centres, routing, quality release, a by-product -----------------------------------------------------

def seed_routing(master, catalog, owner):
    """Work centres with their routed stages (F6a) and the by-product of one recipe (F9). Before the sample orders are opened.
    Safe to run twice."""

    from . import byproducts, workcentres
    from .models import WorkCentre

    config = FACTORIES.get(catalog.key)
    if config is None or WorkCentre.objects.filter(code=config["centres"][0][0]).exists():
        return
    for code, name_ar, name_en, kind, hours, rate in config["centres"]:
        workcentres.save_work_centre({"code": code, "name_ar": name_ar, "name_en": name_en, "kind": kind, "hours_per_day": hours, "rate_per_hour": rate}, owner)
    rows = [{"name_ar": ar, "name_en": en, "work_centre": _centre(code).pk, "minutes_per_batch": minutes, "setup_minutes": setup}
            for ar, en, code, minutes, setup in config["routing"]]
    for index in (FIRST, SECOND, THIRD):
        workcentres.save_routing(_recipe(master, index), rows, owner)
    index, (code, name_ar, unit, value, sale), quantity = config["byproduct"]
    byproduct = _item(master, code, name_ar, unit, value, sale=sale)
    byproducts.save_outputs(_recipe(master, index), owner, [{"item": byproduct, "quantity": quantity, "kind": "by_product", "value_per_unit": value}])


def _offers_on():
    """Quotations and sales orders are switched on by the factory showcases (``config.demo_templates``), not by the setup of
    a factory. A sample business built without them (the screens crawl builds one) gets every other record; nothing here
    switches a capability on."""

    from settings_core.capabilities import capability_enabled

    return capability_enabled("quotations")


# ---- the late hook ---------------------------------------------------------------------------------------------------------

def seed_features(master, catalog, owner, staff):
    """Every merged manufacturing feature with its records; ``staff`` is the production supervisor the sample already has."""

    from purchases.models import PurchaseInvoice

    if not seeds(catalog) or PurchaseInvoice.objects.filter(invoice_number=RESTOCK_NUMBER).exists():
        return
    today = timezone.localdate()
    _worker(owner, FACTORIES[catalog.key]["worker_centre"])
    if _is_food(catalog):
        _food(master, owner, staff, today)
    else:
        _furniture(master, owner, today)
    from . import schedule

    schedule.arrange(today, owner)  # F6b: every open order laid on the work centres from today
    _requirements(owner)  # F2: what is short, as a draft purchase order


def _worker(owner, centre_code):
    """F8: the production worker's employee (linked to the demo login), ticked on one work centre."""

    from django.contrib.auth import get_user_model

    from staff.services import save_employee

    from . import workcentres

    user = get_user_model().objects.filter(username=WORKER_USERNAME, is_active=True).first()
    employee = save_employee({"name": WORKER[0], "title": WORKER[1], "user": user.pk if user else ""}, owner)
    centre = _centre(centre_code)
    workcentres.save_work_centre({"code": centre.code, "name_ar": centre.name_ar, "name_en": centre.name_en, "kind": centre.kind,
                                  "hours_per_day": centre.hours_per_day, "workers": [employee.pk]}, owner, centre=centre)
    return employee


def _restock(master, owner, day, started, buffer, extra=()):
    """Buy the materials the orders below take (``started``: (recipe position, quantity); ``extra``: (material position, quantity)),
    plus a ``buffer`` per material: what is left once they have taken their share."""

    from inventory.services import get_item_location_stock_quantity

    need = defaultdict(lambda: D("0"))
    for index, quantity in started:
        recipe = _recipe(master, index)
        for line in recipe.lines.select_related("component"):
            need[line.component_id] += line.quantity * D(quantity) / recipe.output_quantity
    for index, quantity in extra:
        need[master["raw"][index].pk] += D(quantity)
    lines = []
    for index, left in buffer.items():
        item = master["raw"][index]
        buy = need[item.pk] + left - get_item_location_stock_quantity(item, master["location"])
        if buy > 0:
            lines.append((item, D(ceil(buy * 10)) / 10))
    return _buy(master, owner, RESTOCK_NUMBER, day, master["suppliers"][0], lines, paid_share="0.5")


def _run_stages(order, owner, minutes):
    from . import orders

    for each in minutes:
        orders.advance(order, owner, minutes=each)


def _open(master, owner, index, quantity, due, *, note="", customer=None, labour="250", overhead="100"):
    from . import orders

    return orders.create_order({"recipe": _recipe(master, index), "location": master["location"], "quantity": quantity, "customer": customer, "due_date": due,
                                "labor_cost": labour, "overhead_cost": overhead, "notes": note}, owner)


def _finished(master, owner, index, quantity, due, *, note, minutes):
    """An order opened, every stage closed with its minutes, and finished: its lot comes from its run."""

    from . import orders

    order = _open(master, owner, index, quantity, due, note=note)
    _run_stages(order, owner, minutes)
    return orders.finish(order, owner)


def _answers(recipe):
    from .models import QualityCriterion

    return {criterion.pk: ("pass" if criterion.kind == "pass_fail" else "5.0") for criterion in QualityCriterion.objects.filter(recipe=recipe)}


def checked_batch(master, recipe, quantity, actor):
    """A batch of a recipe that needs a quality release (F5), made the way its recipe page demands: an order, its stages, the
    finish on hold, then a check that releases all of it to the shelf. For the sample's daily top-up, whose plain run would put
    the product on the shelf unchecked. No labour or overhead is added, so the unit cost is the materials', as a plain run's."""

    from . import orders, quality

    from . import schedule
    from .models import OrderStep

    order = orders.create_order({"recipe": recipe, "location": master["location"], "quantity": quantity, "due_date": timezone.localdate(),
                                 "labor_cost": "0", "overhead_cost": "0", "notes": DAILY_NOTE}, actor)
    # The day's batch is already made when it is recorded: its stages count as started, so a machine the demo keeps down for
    # maintenance (the mixer's open breakdown, F10) does not refuse recording them (video finding F12 holds only unstarted stages).
    schedule.ensure_steps(order)
    OrderStep.objects.filter(order=order, started_at__isnull=True).update(started_at=timezone.now())
    for _stage in order.stages:
        order = orders.advance(order, actor)
    order = orders.finish(order, actor)
    quality.check_order(order, actor, passed=str(order.run.output_quantity), rejected="0", results=_answers(recipe))
    return order.run


def _food(master, owner, staff, today):
    from . import quality

    customers = master["customers"]
    # F5: switched on here, after the sample month's plain runs; from now on the product is made by orders and released by a check
    quality.save_recipe_quality(_recipe(master, FIRST), owner, needs_release=True, rows=list(RELEASE_CRITERIA))
    _restock(master, owner, today + timedelta(days=-8), FOOD_STARTED, FOOD_BUFFER, extra=((0, EXTRA_FLOUR),))
    # F5: two lots of the first product wait on hold; one is checked and released with a small reject
    released = _finished(master, owner, FIRST, "20", today + timedelta(days=-4), note="دفعة الصباح", minutes=(40, 70, 35))
    _finished(master, owner, FIRST, "15", today, note="دفعة بانتظار الفحص", minutes=(35, 60, 30))
    _release(released, owner)
    # F9 + F3: the cake's run makes crumbs besides the cake, and a lot of its own
    _finished(master, owner, SECOND, "10", today + timedelta(days=-2), note="كيك الصباح", minutes=(30, 55, 25))
    # F12: extra flour, butter back, and the real labour, against the standard
    _finished_with_actuals(master, owner, staff, today)
    # F4: one stage out at an outside centre, one back with its posted bill
    _outside_pair(master, owner, today)
    # F1: a confirmed sales order makes a production order; the worker has started its stage
    if _offers_on():
        _sales_order(master, owner, today, customers[1])
    # the late order (the mixer stopped); the sample's own three were opened before and are due later
    _open(master, owner, SECOND, "12", today + timedelta(days=-3), note=LATE_NOTE, customer=customers[3], labour="300", overhead="120")
    _food_maintenance(master, owner, today)


def _release(order, owner):
    """F5: a check on the lot waiting on hold: eighteen released, two rejected (a torn wrapping), every criterion answered."""

    from . import quality

    return quality.check_order(order, owner, passed="18", rejected="2", note="كرتونتين غلافهم اتقطع", results=_answers(order.recipe))


def _finished_with_actuals(master, owner, staff, today):
    from . import actuals, orders

    order = _open(master, owner, THIRD, "12", today + timedelta(days=-1), note="معمول للعيد", labour="200", overhead="150")
    orders.advance(order, owner, minutes=35)
    actuals.extra_issue(order, owner, item=master["raw"][0], quantity=EXTRA_FLOUR, reason="عجينة زيادة بعد الوزن")
    actuals.return_materials(order, owner, item=master["raw"][2], quantity="0.5", reason="زبدة زيادة رجعت للمخزن")
    actuals.add_cost_line(order, owner, kind="labour", description="تشغيل الفرن والتعبئة", employee=staff, hours="6", rate="45")
    actuals.add_cost_line(order, owner, kind="overhead", description="كهرباء وغاز الفرن", amount="120")
    orders.advance(order, owner, minutes=55)
    orders.advance(order, owner, minutes=40)
    return orders.finish(order, owner)


def _outside_pair(master, owner, today):
    """F4: the packing of one order is out at an outside centre; the packing of another is back and its bill is posted."""

    from purchases.services import post_purchase_invoice

    from . import outside

    centre = _supplier(*OUTSIDE_SUPPLIER)
    out = _open(master, owner, THIRD, "18", today + timedelta(days=2), note="تعبئة هدايا بالخارج", labour="150", overhead="80")
    _run_stages(out, owner, (30, 50))
    outside.send(out, owner, supplier=centre, unit_price="12", sent_on=today + timedelta(days=-1), note="تغليف هدايا بشريط وكرت")
    back = _open(master, owner, SECOND, "8", today + timedelta(days=1), note="تغليف خاص رجع", labour="120", overhead="60")
    _run_stages(back, owner, (25, 40))
    job = outside.send(back, owner, supplier=centre, unit_price="18", sent_on=today + timedelta(days=-4), note="علب هدايا مطبوعة")
    outside.receive(job, owner, good="7", defect="1", returned_on=today + timedelta(days=-2), note="كرتونة واحدة اتبللت")
    invoice = outside.make_bill(job, owner, bill_date=today + timedelta(days=-2))
    post_purchase_invoice(invoice.pk, owner)


def _sales_order(master, owner, today, customer):
    """F1: a confirmed sales order for thirty cartons and twelve more, to be delivered today: the first line is made and its stage is
    started by the worker; the second is still to make."""

    from django.contrib.auth import get_user_model

    from orders import services as sales
    from orders.models import Status

    from . import floor, make_to_order, orders

    order = sales.create_quote({"customer": customer, "quote_date": today + timedelta(days=-3), "deliver_by": today,
                                "terms": "التسليم في ميعاده والدفع عند الاستلام."}, owner)
    big = sales.add_line(order, master["items"][FIRST], owner, quantity="30")
    sales.add_line(order, master["items"][SECOND], owner, quantity="12")
    order = sales.move(sales.move(order, Status.SENT, owner), Status.CONFIRMED, owner)
    made = make_to_order.make(big, owner, {"labor_cost": "400", "overhead_cost": "150"})
    orders.advance(made, owner, minutes=60)
    floor.start(made, get_user_model().objects.get(username=WORKER_USERNAME))
    return order


def _breakdown(centre, owner, part, location, cause, day, *, opened, closed=None, amount=None, note=""):
    """F10: a breakdown with a spare part taken; closed, with the technician's posted bill, when ``closed`` is given."""

    from purchases.services import post_purchase_invoice

    from . import maintenance

    job = maintenance.open_job(centre, owner, kind="breakdown", opened_at=opened, cause=cause)
    maintenance.take_part(job, owner, item=part, location=location, quantity="1", taken_on=day)
    if closed is not None:
        supplier = _supplier(*TECHNICIAN_SUPPLIER)
        maintenance.close_job(job, owner, closed_at=closed, employee=None, supplier=supplier, note=note)
        link = maintenance.make_bill(job, owner, supplier=supplier, amount=amount, bill_date=day, description="تغيير القطعة")
        post_purchase_invoice(link.purchase_invoice_id, owner)
    return job


def _food_maintenance(master, owner, today):
    from . import maintenance

    oven, mixer, pack = _centre("DEMO-OVEN"), _centre("DEMO-MIX"), _centre("DEMO-PACK")
    maintenance.save_plan(oven, {"title": "تنظيف الفرن وفحص السخانات", "every_days": 30, "last_done": today + timedelta(days=-41)}, owner)
    maintenance.save_plan(pack, {"title": "تزييت سير التعبئة", "every_days": 60, "every_hours": 200, "last_done": today + timedelta(days=-20)}, owner)
    code, name_ar, unit, price = OVEN_PART
    part = _item(master, code, name_ar, unit, price)
    _buy(master, owner, PARTS_NUMBER, today + timedelta(days=-12), master["suppliers"][0], [(part, D("4"))])
    day = today + timedelta(days=-9)
    # a closed breakdown on the oven: a heating element changed, a technician's bill
    _breakdown(oven, owner, part, master["location"], "السخان الأمامي بطّل يسخّن", day, opened=_moment(day, 9, 15), closed=_moment(day, 14, 45),
               amount="500", note="اتغيّر العنصر واتجرّب الفرن")
    # the mixer stopped a few hours ago and is still down
    maintenance.open_job(mixer, owner, kind="breakdown", opened_at=timezone.now() - timedelta(hours=3), cause="سير الخلاط اتقطع")


# ---- the furniture workshop --------------------------------------------------------------------------------------------------

#: The specifications of the two jobs, in the furniture template's fields.
DINING_SPEC = {"width": "220", "depth": "100", "height": "76", "wood": "زان", "finish": "ورنيش لامع", "colour": "بني غامق", "upholstery": "جلد طبيعي للكراسي"}
WARDROBE_SPEC = {"width": "180", "depth": "55", "height": "220", "wood": "زان وخشب مضغوط", "finish": "ورنيش مط", "colour": "بني فاتح"}


def _furniture(master, owner, today):
    from . import maintenance, orders

    customers = master["customers"]
    _restock(master, owner, today + timedelta(days=-8), FURNITURE_STARTED, FURNITURE_BUFFER, extra=WARDROBE_MATERIALS)
    # F9 + F3: the chairs' run makes sawdust besides the chairs
    _finished(master, owner, FIRST, "10", today + timedelta(days=-2), note="كراسي المعرض", minutes=(60, 170, 95))
    # F7: a quotation with its specifications and cost sheet, still offered; and one accepted and made into a job
    if _offers_on():
        _offered_quote(master, owner, today, customers[0])
        _custom_job(master, owner, today, customers[3])
    # the order of the day, and a late one
    chairs = _open(master, owner, FIRST, "6", today, note="كراسي للتسليم النهارده", customer=customers[0])
    orders.advance(chairs, owner, minutes=45)
    _open(master, owner, SECOND, "2", today + timedelta(days=-2), note="ترابيزتين متأخرين عن العميل", customer=customers[3], labour="400", overhead="150")
    # F10: a plan that is due, and the saw down now with a blade changed
    saw, bench = _centre("DEMO-SAW"), _centre("DEMO-ASM")
    maintenance.save_plan(saw, {"title": "شحذ المنشار وتغيير الشفرة", "every_days": 45, "last_done": today + timedelta(days=-50)}, owner)
    maintenance.save_plan(bench, {"title": "فحص المشابك والمسامير", "every_days": 90, "last_done": today + timedelta(days=-30)}, owner)
    code, name_ar, unit, price = SAW_PART
    blade = _item(master, code, name_ar, unit, price)
    _buy(master, owner, PARTS_NUMBER, today + timedelta(days=-12), master["suppliers"][0], [(blade, D("3"))])
    _breakdown(saw, owner, blade, master["location"], "الشفرة اتكسرت", today, opened=timezone.now() - timedelta(hours=2))


def _sheet(master, owner, line, spec, materials, extras, margin):
    """F7: the specifications and the cost sheet of a quotation line, and its price from the margin. ``extras``: (kind, text, quantity, unit cost)."""

    from . import quotes

    quotes.save_spec(line, owner, "furniture", spec)
    for index, quantity in materials:
        quotes.add_cost_line(line, owner, kind="material", item=master["raw"][index], quantity=quantity)
    for kind, text, quantity, cost in extras:
        quotes.add_cost_line(line, owner, kind=kind, description=text, quantity=quantity, unit_cost=cost)
    quotes.set_price(line, owner, margin=margin)


def _offered_quote(master, owner, today, customer):
    from orders import services as sales
    from orders.models import Status

    order = sales.create_quote({"customer": customer, "quote_date": today + timedelta(days=-2), "deliver_by": today + timedelta(days=21),
                                "terms": "نصف المبلغ مقدم والباقي عند التسليم."}, owner)
    line = sales.add_line(order, master["items"][SECOND], owner, quantity="4", description="ترابيزة سفرة مقاس خاص")
    _sheet(master, owner, line, DINING_SPEC, ((0, "9"), (2, "4"), (3, "5")),
           (("labour", "نجارة وتجميع", "32", "60"), ("overhead", "كهرباء المنشار والدهان", "1", "300"), ("outside", "تنجيد الكراسي بالجلد", "4", "900")), "30")
    return sales.move(order, Status.SENT, owner)


def _custom_job(master, owner, today, customer):
    """F7: six wardrobes made to measure: specifications, cost sheet, price, the customer accepts, and the job's own item, recipe and
    production order are made; the job runs its stages and is finished, so its item has stock (a job item at zero would show as out of stock)."""

    from orders import services as sales
    from orders.models import Status

    from . import orders, quotes

    order = sales.create_quote({"customer": customer, "quote_date": today + timedelta(days=-6), "deliver_by": today + timedelta(days=10),
                                "terms": "التسليم والتركيب في الموقع."}, owner)
    line = sales.add_line(order, master["items"][3], owner, quantity="6", description="دولاب غرفة نوم مقاس خاص")
    _sheet(master, owner, line, WARDROBE_SPEC, WARDROBE_MATERIALS,
           (("labour", "نجارة وتجميع", "48", "60"), ("overhead", "كهرباء المنشار والدهان", "1", "400"), ("outside", "تنجيد الأبواب بالجلد", "6", "350")), "25")
    sales.move(sales.move(order, Status.SENT, owner), Status.CONFIRMED, owner)
    job = quotes.convert(line, owner, {})
    _run_stages(job, owner, (50, 90, 70, 60))
    orders.finish(job, owner)
    return order


# ---- the requirements ------------------------------------------------------------------------------------------------------

def _requirements(owner):
    """F2: a draft purchase order, for what the requirements calculation says is short and has a supplier."""

    from . import mrp

    short = [row for row in mrp.calculate()["materials"] if row["to_buy"] > 0 and row["supplier"] is not None]
    if short:
        mrp.make_purchase_orders([{"item": row["item"].pk, "quantity": "", "supplier": row["supplier"]} for row in short], owner)


# ---- the tour's own records ------------------------------------------------------------------------------------------------

def _first(queryset):
    return queryset.order_by("pk").first()


def tour_path(url_name):
    """The address of the demo screen a «جرّب» step opens, for the steps that belong to one record of the demo: the sales order that made
    a production order, the finished order with its actual labour (or the custom job), the run that made a by-product, the released lot's
    trace, a machine's maintenance page, a quotation line's specifications and its converted job. None when the demo has no such record (an
    emptied demo, or a factory without this seed), and the card then drops the step."""

    from django.urls import reverse

    from batches.models import Batch

    from .models import CostSheet, MaintenanceJob, ProductionOrder, QualityCheck

    done = ProductionOrder.objects.filter(status="done")
    if url_name == "manufacturing:tour_sales_order":
        made = _first(ProductionOrder.objects.filter(sales_order_line__isnull=False, sales_order_line__cost_sheet__isnull=True))
        return reverse("orders:detail", args=[made.sales_order_line.order_id]) if made else None
    if url_name == "manufacturing:order":
        sheet = _first(CostSheet.objects.filter(job_item__isnull=False))
        found = _first(sheet.sales_order_line.production_orders.all()) if sheet else None
        found = found or _first(done.filter(cost_lines__isnull=False).distinct())
        return reverse("manufacturing:order", args=[found.pk]) if found else None
    if url_name == "manufacturing:run":
        found = _first(done.filter(run__outputs__isnull=False).distinct())
        return reverse("manufacturing:run", args=[found.run_id]) if found else None
    if url_name == "manufacturing:trace":
        check = _first(QualityCheck.objects.filter(release_operation__isnull=False, order__isnull=False))
        lot = Batch.objects.filter(production_run=check.order.run_id).first() if check else None
        return f"{reverse('manufacturing:trace')}?lot={lot.pk}" if lot else None
    if url_name == "manufacturing:maintenance_centre":
        job = _first(MaintenanceJob.objects.filter(closed_at__isnull=False)) or _first(MaintenanceJob.objects.all())
        return reverse("manufacturing:maintenance_centre", args=[job.work_centre_id]) if job else None
    if url_name == "manufacturing:quote_sheet":
        sheet = _first(CostSheet.objects.filter(job_item__isnull=True))
        return reverse("manufacturing:quote_sheet", args=[sheet.sales_order_line_id]) if sheet else None
    if url_name == "manufacturing:tour_job_sheet":
        sheet = _first(CostSheet.objects.filter(job_item__isnull=False))
        return reverse("manufacturing:quote_sheet", args=[sheet.sales_order_line_id]) if sheet else None
    return None
