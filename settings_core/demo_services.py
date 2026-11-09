"""DEMO-SVC: what a visitor who picks "services" (a repair workshop or a salon) finds filled in on the showcase.

Everything goes through the real services: employees (``staff.services``), work orders (``workorders.services``, delivery
included, so the invoice posts through the sales engine), appointments (``appointments.services``) and campaigns
(``messaging.campaigns``). Nothing here writes a posting, a ledger or a cashbox row by hand, and nothing calls an outside
service: the customer's WhatsApp messages only reach the local outbox.

Idempotent: ``DEMO-SVC-01`` (the first extra service item) is the sentinel, created inside the same transaction as the
rest, so a run either adds the whole showcase or finds it there and adds nothing. Only for the services activity, and only
for the sub-activities that have these screens (maintenance and beauty): any other business gets none of it.

Not seeded: online booking (S3, not on develop yet), gift cards (S6, not built), photo and signature files (S1, a real file
cannot be a sample), deposits (S5, off until the owner switches it on) and the pay link (S4, mock mode only).
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

SENTINEL = "DEMO-SVC-01"
SHOWCASE_CATALOGS = ("services.maintenance", "services.beauty")

#: (name, title, commission %): the technicians who share the work, each on their own rate (S9 reads it).
CREW = {
    "services.maintenance": (("كريم محمود", "فني تكييف", "8"), ("حسام الدسوقي", "فني غسالات وثلاجات", "10"), ("عمرو السيد", "فني كهرباء وسباكة", "7")),
    "services.beauty": (("نهى سمير", "خبيرة مكياج", "12"), ("دينا فتحي", "أخصائية أظافر", "10"), ("ياسمين طارق", "كوافيرة", "10")),
}

#: (code, name, unit, price): non-stock services, beside the catalog's own.
SERVICES = {
    "services.maintenance": ((SENTINEL, "تنظيف وغسيل تكييف", "خدمة", "450.00"), ("DEMO-SVC-02", "شحن فريون", "خدمة", "600.00"),
                             ("DEMO-SVC-03", "زيارة طوارئ", "زيارة", "350.00"), ("DEMO-SVC-04", "تركيب وتشغيل جهاز", "خدمة", "400.00")),
    "services.beauty": ((SENTINEL, "ميك أب سواريه", "خدمة", "900.00"), ("DEMO-SVC-02", "بروتين شعر", "خدمة", "1500.00"),
                        ("DEMO-SVC-03", "تنظيف بشرة", "خدمة", "450.00"), ("DEMO-SVC-04", "حمام زيت", "خدمة", "200.00")),
}

#: Where a workshop's visits go (a visit needs an address).
ADDRESSES = ("15 شارع مصطفى النحاس، مدينة نصر", "برج الأمل، شارع التسعين، التجمع الخامس", "عمارة 7، شارع فيصل، الجيزة",
             "22 شارع عباس العقاد، مدينة نصر", "الحي الثالث، الشيخ زايد")

#: (days after the week's first day, hour, crew index, customer index, service index, minutes, status once past, status still to come, note).
#: Crew index 3 wraps to the first new technician (the catalog's own technician is never booked here).
APPOINTMENTS = {
    "services.maintenance": (
        (0, 10, 0, 0, 0, 60, "done", "confirmed", "كشف على تكييف الريسبشن"), (0, 13, 1, 1, 1, 90, "done", "confirmed", "الغسالة بتفصل في النص"),
        (1, 11, 2, 3, 2, 60, "done", "confirmed", "ماس كهربا في المطبخ"), (2, 9, 3, 2, 0, 60, "no_show", "booked", "العميل مردش"),
        (2, 15, 0, 1, 1, 60, "done", "confirmed", "صيانة دورية لتكييفين"), (3, 12, 1, 0, 3, 120, "done", "booked", "تركيب ديب فريزر"),
        (4, 10, 2, 2, 0, 60, "cancelled", "booked", "العميل لغى"), (5, 14, 0, 3, 2, 60, "done", "booked", "زيارة طوارئ لمحل"),
        (7, 10, 1, 0, 0, 60, "done", "confirmed", "كشف ثلاجة"), (7, 13, 2, 1, 1, 90, "done", "booked", "تغيير مواسير حمام"),
        (8, 11, 0, 3, 3, 60, "done", "confirmed", "تركيب تكييف جديد"), (9, 9, 3, 2, 2, 60, "done", "booked", "طوارئ تسريب مياه"),
        (9, 15, 1, 0, 0, 60, "done", "booked", "كشف غسالة أطباق"), (10, 12, 2, 1, 1, 60, "done", "confirmed", "فحص لوحة كهرباء"),
        (12, 14, 1, 2, 1, 60, "done", "booked", "صيانة سخان"),
    ),
    "services.beauty": (
        (0, 10, 0, 0, 0, 90, "done", "confirmed", "ميك أب خطوبة"), (0, 12, 1, 1, 2, 60, "done", "confirmed", "مانيكير وباديكير"),
        (1, 11, 2, 3, 1, 120, "done", "confirmed", "بروتين شعر"), (2, 13, 3, 2, 0, 45, "no_show", "booked", "العميلة مجتش"),
        (2, 17, 0, 1, 3, 60, "done", "confirmed", "تنظيف بشرة"), (3, 12, 1, 0, 0, 90, "done", "booked", "قص وتصفيف"),
        (4, 15, 2, 2, 2, 60, "cancelled", "booked", "العميلة لغت"), (5, 11, 3, 3, 1, 150, "done", "booked", "صبغة كاملة"),
        (7, 11, 0, 0, 0, 120, "done", "confirmed", "ميك أب سواريه"), (7, 14, 1, 1, 2, 60, "done", "booked", "مانيكير"),
        (8, 12, 2, 3, 1, 120, "done", "confirmed", "صبغة وقص"), (9, 10, 3, 2, 3, 45, "done", "booked", "حمام زيت"),
        (9, 16, 0, 0, 4, 90, "done", "booked", "ميك أب تصوير"), (10, 13, 1, 1, 0, 60, "done", "confirmed", "باديكير"),
        (12, 15, 2, 2, 1, 60, "done", "booked", "تنضيف شعر"),
    ),
}

CAMPAIGNS = {
    "services.maintenance": ("صيانة دورية قبل الصيف", "أهلاً {name}، من {company}: اقترب الصيف، احجز صيانة دورية لتكييفك واستلم كشف مجاني مع أي إصلاح.", "inactive", 90),
    "services.beauty": ("عرض الويك إند", "أهلاً {name}، من {company}: وحشتينا! خصم على الباديكير والمانيكير يوم الخميس والجمعة، احجزي ميعادك.", "inactive", 45),
}


def applies(catalog):
    """Only the services activity, and only a workshop or a salon (never a business that merely resembles one)."""

    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return bool(profile and profile.activity_slug == "services" and catalog.key in SHOWCASE_CATALOGS)


def seed(master, staff, owner, catalog, extras):
    """Fill the showcase once. ``staff`` is the catalog's own technician (already booked by the old sample)."""

    from master_data.models import Item

    if Item.objects.filter(item_code=SENTINEL).exists():
        return False
    with transaction.atomic():
        services = _services(master, catalog)
        crew = _crew(catalog, owner)
        customers = [customer for customer in master["customers"] if customer.customer_code != "WALK-IN"]
        if "workorders" in extras:
            _work_orders(master, crew, customers, owner)
        if "appointments" in extras:
            # Only the new crew: the catalog's own technician already holds the old sample's relative-date slots,
            # and a clash there would fail the whole sample on some weekdays.
            _appointments(catalog, crew, customers, services, owner)
            _online_booking(catalog, crew, services, owner)
        _campaign(catalog, owner)
    return True


def _services(master, catalog):
    """The catalog's own non-stock services first, then the showcase's: the appointments' menu."""

    from master_data.models import Item

    category = master["items"][0].category
    rows = [item for item in master["items"] if not item.is_stock_tracked]
    for code, name, unit, price in SERVICES[catalog.key]:
        item, _ = Item.objects.get_or_create(item_code=code, defaults={
            "item_name": name, "category": category, "unit": unit, "default_sale_price": Decimal(price), "default_purchase_price": Decimal("0"),
            "min_stock": Decimal("0"), "is_stock_tracked": False, "active": True, "import_batch_id": "DEMO-BUSINESS"})
        rows.append(item)
    return rows


def _crew(catalog, owner):
    from staff.services import save_employee

    return [save_employee({"name": name, "title": title, "commission_percent": rate}, owner) for name, title, rate in CREW[catalog.key]]


# ---- work orders (S1-S2, S7, S9): a workshop only ----

def _work_orders(master, crew, customers, owner):
    from workorders import services as wo
    from workorders.models import Status, WorkOrder

    if not wo.is_workshop():
        return  # a salon has no work orders
    labour = [item for item in master["items"] if not item.is_stock_tracked]
    parts = [item for item in master["items"] if item.is_stock_tracked]
    if not (labour and parts and customers):
        return
    cashbox = next((box for box in master["cashboxes"] if box.is_default), None) or (master["cashboxes"][0] if master["cashboxes"] else None)
    today, now = timezone.localdate(), timezone.now()

    def receive(n, device, problem, days_ago, lines, path):
        order = wo.receive({"customer": customers[n % len(customers)], "device": device, "problem": problem, "technician": crew[n % len(crew)],
                            "promised_on": today + timedelta(days=3 - days_ago), "warranty_days": "30", "serial": f"SVC-{5200 + n}"}, owner)
        WorkOrder.objects.filter(pk=order.pk).update(received_at=now - timedelta(days=days_ago))
        for item, quantity in lines:
            wo.add_line(order, item, owner, quantity=str(quantity))
        for status in path:
            wo.move(order, status, owner)
        return order

    # Quoted: the S7 message with the customer's link goes to the outbox, and the order waits for the answer.
    receive(0, "تكييف كاريير 1.5 حصان", "بيسرّب مياه من الوحدة الداخلية", 2, ((labour[0], 1), (parts[0], 1)), (Status.DIAGNOSED, Status.QUOTED))
    # The customer approved from the link (S2), and the technician has started.
    approved = receive(1, "غسالة إل جي 8 كيلو", "صوت عالي في الدوران", 4, ((labour[-1], 1), (parts[0], 1)), (Status.DIAGNOSED, Status.QUOTED))
    try:
        approved, _changed = wo.customer_decide(approved, "approve", fingerprint=wo.quote_fingerprint(approved))
        wo.move(approved, Status.IN_REPAIR, owner)
    except ValidationError:
        pass
    # The customer refused the quote from the link: only the inspection fee stays, and the device waits at the counter.
    refused = receive(2, "بوتاجاز يونيفرسال", "الشعلة بتطفي لوحدها", 5, ((labour[-1], 1), (parts[-1], 1)), (Status.DIAGNOSED, Status.QUOTED))
    try:
        refused, _changed = wo.customer_decide(refused, "refuse", fingerprint=wo.quote_fingerprint(refused))
        wo.add_line(refused, labour[0], owner, quantity="1", description="رسم الكشف")
        wo.move(refused, Status.READY, owner)
    except ValidationError:
        pass
    # Delivered three days ago and paid in cash, on labour only: the technician's commission shows in the S9 report.
    delivered = receive(3, "سخان غاز أريستون", "مش بيولّع", 6, ((labour[-1], 2),), (Status.IN_REPAIR, Status.READY))
    try:
        paid = wo.totals(delivered)["total"]
        wo.deliver(delivered, owner, paid_now=str(paid) if cashbox else "0", cashbox=cashbox, invoice_date=today - timedelta(days=3))
        WorkOrder.objects.filter(pk=delivered.pk).update(delivered_at=now - timedelta(days=3), warranty_until=today + timedelta(days=27))
    except ValidationError:
        pass  # the order stays ready for pickup
    # Cancelled by the customer before anyone touched it.
    cancelled = receive(4, "ميكروويف سامسونج", "العميل لغى الصيانة", 3, (), ())
    wo.move(cancelled, Status.CANCELLED, owner, note="العميل لغى")


# ---- appointments (S10) and their commission (S9) ----

def _appointments(catalog, crew, customers, services, owner):
    from appointments import services as appointments
    from appointments.models import AppointmentKind, AppointmentStatus
    from appointments.views_week import week_start_of

    visits = catalog.key == "services.maintenance"
    first, now = week_start_of(timezone.localdate()), timezone.localtime()
    made = []
    for n, (offset, hour, who, whom, what, minutes, then, later, note) in enumerate(APPOINTMENTS[catalog.key]):
        starts = timezone.make_aware(datetime.combine(first + timedelta(days=offset), time(hour)))
        values = {"customer": customers[whom % len(customers)], "employee": crew[who % len(crew)], "service": services[what % len(services)],
                  "starts_at": starts, "duration_minutes": minutes, "notes": note}
        if visits:
            values.update(kind=AppointmentKind.VISIT, address=ADDRESSES[n % len(ADDRESSES)])
        appointment = appointments.book(values, owner)
        if then == AppointmentStatus.CANCELLED or starts + timedelta(minutes=minutes) <= now:  # a customer may cancel ahead of time
            status = then
        elif starts <= now:
            status = AppointmentStatus.ARRIVED
        else:
            status = later
        if status != AppointmentStatus.BOOKED:
            appointments.set_status(appointment, status, owner)
        made.append(appointment)
    _bill_done(made, owner)


def _bill_done(made, owner):
    """Two finished appointments billed and posted by the services that always do it, so S9 has earnings to show.

    Credit sales on the customers with room under their limit: a refusal leaves that appointment unbilled."""

    from appointments import services as appointments
    from appointments.models import AppointmentStatus
    from sales.services import post_sales_invoice

    billed = 0
    for appointment in made:
        if billed == 2:
            break
        appointment.refresh_from_db()
        if appointment.status != AppointmentStatus.DONE or appointment.customer.customer_code == "DEMO-CUST-03" or appointment.employee is None:
            continue
        try:
            with transaction.atomic():
                invoice = appointments.bill(appointment, owner)
                post_sales_invoice(invoice.pk, owner)
            billed += 1
        except ValidationError:
            continue


# ---- online booking (S3) ----

#: (opens, closes, show prices) for the public booking page; Friday closed, the others open.
BOOKING_HOURS = {"services.maintenance": ("09:00", "18:00", False), "services.beauty": ("11:00", "21:00", True)}


def _online_booking(catalog, crew, services, owner):
    """The public booking page set up and open: the shop's hours, the showcase services with their length, the new crew.

    Saved through the settings screen's own service; the page still answers only once the owner turns the
    «الحجز أونلاين» capability on (it is off by default)."""

    from appointments import booking
    from appointments.models import Weekday
    from entities.services import main_entity

    opens, closes, show_prices = BOOKING_HOURS[catalog.key]
    data = {"accepting": "1", "slot_minutes": "30", "min_notice_minutes": "120", "max_days_ahead": "14"}
    if show_prices:
        data["show_prices"] = "1"
    for weekday in Weekday.values:
        if weekday != Weekday.FRIDAY:
            data.update({f"day_{weekday}_on": "1", f"day_{weekday}_opens": opens, f"day_{weekday}_closes": closes})
    for item in services[-len(SERVICES[catalog.key]):]:  # the showcase's own services
        data.update({f"service_{item.pk}": "1", f"duration_{item.pk}": "60"})
    for person in crew:
        data[f"staff_{person.pk}"] = "1"
    booking.save_settings(main_entity(), data, owner)


# ---- campaigns (S8) ----

def _campaign(catalog, owner):
    """One draft, never queued: the owner reads it and decides."""

    from messaging import campaigns
    from messaging.models import Campaign

    name, text, audience, days = CAMPAIGNS[catalog.key]
    if not Campaign.objects.filter(name=name).exists():
        campaigns.save_draft({"name": name, "text": text, "audience": audience, "threshold": days, "scheduled_day": timezone.localdate() + timedelta(days=2)}, owner)
