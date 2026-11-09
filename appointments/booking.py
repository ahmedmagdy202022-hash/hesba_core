"""S3 (HG-077): working hours, free slots and the public online booking ("حجز أونلاين ٢٤ ساعة").

Rules, stated once:

* each entity has its own settings row, weekly opening hours, the services its public page offers (each with its
  duration) and the employees who take online bookings there. An employee may have their own weekly hours in that
  entity; they are always clipped to the entity's. A weekday with no opening hours is closed; no overnight windows;
* a free slot starts on the entity's grid (every ``slot_minutes`` from opening), fits the service's duration inside
  the employee's hours, is at least ``min_notice_minutes`` away and at most ``max_days_ahead`` days ahead, all in the
  installation's time zone (Africa/Cairo). It is free when the employee holds no appointment overlapping it, read
  exactly as ``services._check_overlap`` reads: group-wide (an employee's diary is one across entities), statuses that
  hold time (``HOLDS_TIME``), each appointment's own duration;
* a public booking is re-checked against a freshly computed slot list, then made through ``services.book`` while
  working in the page's entity, so ``_check_overlap`` takes the employee row lock and refuses a slot taken in the
  meantime (race-safe). "Any employee" tries each free employee in turn; a booking never goes out without one;
* the customer is matched by phone (``reports.aging.whatsapp_number`` on both sides, against phone and WhatsApp of
  active customers, never the walk-in customer) and left unchanged, else created. Nothing is priced, paid or posted
  here: a deposit (S5) is taken by the shop, never online.
"""

import secrets
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import services
from .models import HOLDS_TIME, Appointment, BookableService, BookingSettings, BookingStaff, WorkingHours, Weekday

CAPABILITY = "online_booking"
MODULE = "appointments_visits"
SYSTEM_USERNAME = "hesba-online-booking"
DONE_SALT = "hesba.booking.done.v1"
DONE_MAX_AGE = 2 * 86400
SLOT_CHOICES = (10, 15, 20, 30, 45, 60, 90, 120)
MAX_NOTICE_MINUTES = 7 * 24 * 60
MAX_AHEAD_DAYS = 365
#: Egypt's week starts on Saturday: the order the settings screen lists the days in.
WEEK_ORDER = (Weekday.SATURDAY, Weekday.SUNDAY, Weekday.MONDAY, Weekday.TUESDAY, Weekday.WEDNESDAY, Weekday.THURSDAY, Weekday.FRIDAY)
DAY_NAMES = {
    "ar": {0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"},
    "en": {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday"},
}
SHORT_DAY_NAMES = {
    "ar": {0: "اثنين", 1: "ثلاثاء", 2: "أربعاء", 3: "خميس", 4: "جمعة", 5: "سبت", 6: "أحد"},
    "en": {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"},
}
MONTH_NAMES = {
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
}

MESSAGES = {
    "ar": {
        "name": "اكتب اسمك.", "phone": "اكتب رقم موبايل صحيح.", "service": "اختار خدمة من القايمة.", "staff": "اختار حد من القايمة.",
        "when": "اختار يوم وميعاد من المواعيد الفاضية.", "taken": "الميعاد ده لسه اتحجز. اختار ميعاد تاني من المواعيد الفاضية.",
        "failed": "مقدرناش نسجّل الحجز دلوقتي. جرّب تاني أو كلّم المحل.", "customer_note": "اتضاف من صفحة الحجز الأونلاين.",
        "slot": "مدة الخانة لازم تكون واحدة من {choices} دقيقة.", "notice": "أقل وقت قبل الميعاد لازم من 0 لـ {max} دقيقة.",
        "ahead": "أقصى عدد أيام قدام لازم من 1 لـ {max}.", "hours": "مواعيد {day} مش صحيحة: الفتح لازم قبل القفل في نفس اليوم.",
        "duration": "مدة «{name}» لازم من {a} لـ {b} دقيقة.", "no_hours": "حدّد مواعيد الفتح ليوم واحد على الأقل قبل ما تفتح الحجز.",
        "no_staff": "اختار موظف واحد على الأقل بياخد حجوزات قبل ما تفتح الحجز.", "no_services": "اختار خدمة واحدة على الأقل قبل ما تفتح الحجز.",
        "staff_hours": "مواعيد {name} يوم {day} مش صحيحة: الفتح لازم قبل القفل.",
    },
    "en": {
        "name": "Enter your name.", "phone": "Enter a valid mobile number.", "service": "Choose a service from the list.", "staff": "Choose someone from the list.",
        "when": "Choose a day and one of the free times.", "taken": "That time was just taken. Please choose another free time.",
        "failed": "We could not record the booking now. Try again or contact the shop.", "customer_note": "Added by the online booking page.",
        "slot": "The slot length must be one of {choices} minutes.", "notice": "The minimum notice must be 0 to {max} minutes.",
        "ahead": "The days ahead must be 1 to {max}.", "hours": "The hours for {day} are not valid: opening must come before closing on the same day.",
        "duration": "The duration of “{name}” must be {a} to {b} minutes.", "no_hours": "Set the opening hours for at least one day before opening bookings.",
        "no_staff": "Choose at least one employee who takes bookings before opening bookings.", "no_services": "Choose at least one service before opening bookings.",
        "staff_hours": "{name}'s hours on {day} are not valid: opening must come before closing.",
    },
}


# ---- switches and settings -------------------------------------------------------


def enabled():
    """The «الحجز أونلاين» capability is on and the appointments module is on (CAP-001)."""

    from settings_core.capabilities import capability_enabled
    from settings_core.setup_services import module_is_enabled

    return capability_enabled(CAPABILITY) and module_is_enabled(MODULE)


def _new_key():
    return secrets.token_urlsafe(12)


def settings_for(entity):
    """The entity's booking settings, made (closed, with defaults) the first time they are asked for."""

    found = BookingSettings.objects.filter(entity=entity).first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return BookingSettings.objects.create(entity=entity, public_key=_new_key())
    except IntegrityError:
        return BookingSettings.objects.get(entity=entity)


def by_key(key):
    """The settings behind a public link, or None when that link must answer 404: unknown key, the capability or the
    appointments module off, bookings closed for the entity, or the entity inactive."""

    if not key or len(key) > 40 or not enabled():
        return None
    found = BookingSettings.objects.select_related("entity").filter(public_key=key).first()
    if found is None or not found.accepting or not found.entity.active:
        return None
    return found


def public_url(booking, lang="ar"):
    """The entity's public link, built from settings.PUBLIC_BASE_URL only (Ahmed's standing rule, as S2); None without it."""

    base = getattr(settings, "PUBLIC_BASE_URL", "")
    if not base:
        return None
    return f"{base.rstrip('/')}{reverse('book_page', args=[booking.public_key])}?lang={lang}"


def services_of(entity):
    return list(BookableService.objects.filter(entity=entity, item__active=True).select_related("item").order_by("item__item_name", "pk"))


def staff_of(entity):
    return [row.employee for row in BookingStaff.objects.filter(entity=entity, employee__active=True).select_related("employee").order_by("employee__name", "pk")]


def hours_map(entity):
    """{(employee id or None, weekday): (opens, closes)} for the entity."""

    return {(row.employee_id, row.weekday): (row.opens, row.closes) for row in WorkingHours.objects.filter(entity=entity)}


def window(hours, employee, weekday):
    """The employee's working window that weekday (their own hours clipped to the entity's), or None when closed."""

    shop = hours.get((None, weekday))
    if shop is None:
        return None
    own = hours.get((employee.pk if employee is not None else None, weekday)) if employee is not None else None
    if employee is not None and own is None and any(key[0] == employee.pk for key in hours):
        return None  # the employee has their own week and does not work this day
    opens, closes = shop
    if own is not None:
        opens, closes = max(opens, own[0]), min(closes, own[1])
    return (opens, closes) if closes > opens else None


# ---- the slot engine -------------------------------------------------------------


def _aware(day, clock):
    return timezone.make_aware(datetime.combine(day, clock))


def booking_days(booking, now=None):
    """(first day, last day) a customer may book, in the local time zone."""

    today = timezone.localdate(now or timezone.now())
    return today, today + timedelta(days=booking.max_days_ahead)


def _busy(employees, day):
    """{employee id: [(start, end)]} of the appointments holding their time around ``day``, group-wide, as _check_overlap reads."""

    start = _aware(day, time.min)
    end = start + timedelta(days=1)
    busy = {employee.pk: [] for employee in employees}
    rows = Appointment.objects.filter(employee__in=[employee.pk for employee in employees], status__in=HOLDS_TIME,
                                      starts_at__lt=end, starts_at__gte=start - timedelta(minutes=services.MAX_MINUTES))
    for row in rows.only("employee_id", "starts_at", "duration_minutes"):
        busy[row.employee_id].append((row.starts_at, row.ends_at))
    return busy


def free_slots(booking, service, day, employee=None, now=None, hours=None, staff=None):
    """[{"start": aware datetime, "employees": [Employee, ...]}] in time order for ``service`` on ``day``.

    ``employee`` None means "any": a slot is listed when at least one employee who takes bookings is free, and lists
    every one who is. A day outside the booking window, or closed, has none."""

    now = now or timezone.now()
    first, last = booking_days(booking, now)
    if not first <= day <= last:
        return []
    staff = staff if staff is not None else staff_of(booking.entity)
    people = [person for person in staff if employee is None or person.pk == employee.pk]
    if not people:
        return []
    hours = hours if hours is not None else hours_map(booking.entity)
    shop = hours.get((None, day.weekday()))
    if shop is None:
        return []
    earliest = now + timedelta(minutes=booking.min_notice_minutes)
    length = timedelta(minutes=service.duration_minutes)
    step = timedelta(minutes=max(int(booking.slot_minutes), 5))
    busy = _busy(people, day)
    windows = {person.pk: window(hours, person, day.weekday()) for person in people}
    slots = []
    start, shop_close = _aware(day, shop[0]), _aware(day, shop[1])
    while start + length <= shop_close:
        end = start + length
        if start >= earliest:
            free = []
            for person in people:
                span = windows[person.pk]
                if span is None or start < _aware(day, span[0]) or end > _aware(day, span[1]):
                    continue
                if any(other_start < end and other_end > start for other_start, other_end in busy[person.pk]):
                    continue
                free.append(person)
            if free:
                slots.append({"start": start, "employees": free})
        start += step
    return slots


def next_free(booking, service, employee=None, after=None, now=None):
    """The first free slot from ``after`` (a day) to the end of the booking window, or None."""

    now = now or timezone.now()
    first, last = booking_days(booking, now)
    day = max(after or first, first)
    hours, staff = hours_map(booking.entity), staff_of(booking.entity)
    while day <= last:
        found = free_slots(booking, service, day, employee, now, hours=hours, staff=staff)
        if found:
            return found[0]
        day += timedelta(days=1)
    return None


# ---- the public booking ----------------------------------------------------------


def system_user():
    """The account public bookings are made in (``created_by`` and the audit trail need a user). It cannot sign in:
    inactive, with no usable password."""

    from django.contrib.auth import get_user_model

    User = get_user_model()
    found = User.objects.filter(username=SYSTEM_USERNAME).first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            user = User(username=SYSTEM_USERNAME, first_name="Online booking", is_active=False, is_staff=False, is_superuser=False)
            user.set_unusable_password()
            user.save()
            return user
    except IntegrityError:
        return User.objects.get(username=SYSTEM_USERNAME)


def latin_digits(text):
    return "".join(str(int(ch)) if ch.isdecimal() else ch for ch in str(text or ""))


def phone_key(raw):
    from reports.aging import whatsapp_number

    return whatsapp_number(raw)


def clean_phone(raw, words):
    typed = latin_digits(raw).strip()
    digits = "".join(ch for ch in typed if ch.isdigit())
    if not 8 <= len(digits) <= 15 or any(ch not in "0123456789+ -()" for ch in typed):
        raise ValidationError(words["phone"], code="phone")
    return typed[:50]


def find_customer(phone):
    """The active customer with this phone (or WhatsApp) number however it was typed, never the walk-in customer."""

    from master_data.models import Customer
    from sales.pos import WALK_IN_CODE

    key = phone_key(phone)
    if not key:
        return None
    rows = Customer.objects.filter(active=True).exclude(customer_code=WALK_IN_CODE).order_by("pk").values_list("pk", "phone", "whatsapp")
    for pk, number, whatsapp in rows:
        if key in (phone_key(number), phone_key(whatsapp)):
            return Customer.objects.get(pk=pk)
    return None


def _customer(name, phone, user, words):
    from config.numbering import next_in_series
    from master_data.models import Customer

    found = find_customer(phone)
    if found is not None:
        return found, False  # reused as it is: public input never changes a customer's card
    for _attempt in range(5):
        try:
            with transaction.atomic():
                customer = Customer.objects.create(customer_code=next_in_series(Customer, "customer_code", "C-"), name=name, phone=phone,
                                                   notes=words["customer_note"])
        except IntegrityError:
            continue
        AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="master_data", action="online_booking_customer",
                                object_type="master_data.Customer", object_id=str(customer.pk), before_data={},
                                after_data={"code": customer.customer_code}, reason="Added by the online booking page.")
        return customer, True
    raise ValidationError(words["failed"], code="failed")


def book_online(booking, service, employee, starts_at, name, phone, notes="", lang="ar", now=None):
    """Book ``service`` at ``starts_at`` for the customer with this name and phone. Returns the appointment.

    Refuses with a neutral message (``code="taken"``) a time that is not one of the free slots right now, and a slot
    taken between the page and the submit (the employee-row lock in ``_check_overlap`` serialises the last two)."""

    words = MESSAGES[lang]
    name = " ".join(str(name or "").split())[:120]
    if len(name) < 2:
        raise ValidationError(words["name"], code="name")
    phone = clean_phone(phone, words)
    notes = " ".join(str(notes or "").split())[:200]
    if service is None or service.entity_id != booking.entity_id or not service.item.active:
        raise ValidationError(words["service"], code="service")
    staff = staff_of(booking.entity)
    if employee is not None and employee.pk not in {person.pk for person in staff}:
        raise ValidationError(words["staff"], code="staff")
    if starts_at is None:
        raise ValidationError(words["when"], code="when")
    day = timezone.localtime(starts_at).date()
    slot = next((row for row in free_slots(booking, service, day, employee, now, staff=staff) if row["start"] == starts_at), None)
    if slot is None:
        raise ValidationError(words["taken"], code="taken")
    from entities.current import working_in

    user = system_user()
    with transaction.atomic():
        customer, _created = _customer(name, phone, user, words)
        with working_in(booking.entity):  # HG-074: the appointment is stamped with the page's entity
            for person in slot["employees"]:
                try:
                    appointment = services.book({"customer": customer, "employee": person, "service": service.item, "price": None,
                                                 "starts_at": starts_at, "duration_minutes": service.duration_minutes,
                                                 "notes": notes}, user, lang)
                except ValidationError as exc:
                    if getattr(exc, "code", None) == "overlap":
                        continue  # taken a moment ago: the next free employee, if any
                    raise ValidationError(words["failed"], code="failed")
                AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="appointments", action="book_online",
                                        object_type="appointments.Appointment", object_id=str(appointment.pk), before_data={},
                                        after_data={"entity": booking.entity_id, "number": appointment.number})  # no personal data
                return appointment
        raise ValidationError(words["taken"], code="taken")


def done_token(appointment):
    return signing.dumps({"a": appointment.pk}, salt=DONE_SALT, compress=True)


def read_done_token(token, booking):
    """The appointment behind a confirmation link of this page, or None (forged, old, or another entity's)."""

    try:
        data = signing.loads(token, salt=DONE_SALT, max_age=DONE_MAX_AGE)
    except signing.BadSignature:
        return None
    pk = data.get("a") if isinstance(data, dict) else None
    if not isinstance(pk, int):
        return None
    return Appointment.objects.select_related("service", "employee", "entity").filter(pk=pk, entity=booking.entity).first()


# ---- the owner's settings --------------------------------------------------------


def _clock(raw):
    try:
        return datetime.strptime(str(raw or "").strip(), "%H:%M").time()
    except ValueError:
        return None


def _int(raw, default=None):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


@transaction.atomic
def save_settings(entity, data, user, lang="ar"):
    """Store the entity's booking settings from the settings screen's fields. ``data`` is a QueryDict-like mapping.

    Fields: accepting, show_prices, slot_minutes, min_notice_minutes, max_days_ahead, day_<wd>_on/_opens/_closes,
    service_<item id> (ticked) with duration_<item id>, staff_<employee id> (ticked) with
    emp_<employee id>_day_<wd>_on/_opens/_closes for their own hours (none ticked: the entity's hours)."""

    from master_data.models import Item
    from staff.models import Employee

    words = MESSAGES[lang]
    names = DAY_NAMES[lang]
    booking = BookingSettings.objects.select_for_update().get(pk=settings_for(entity).pk)
    slot = _int(data.get("slot_minutes"))
    if slot not in SLOT_CHOICES:
        raise ValidationError(words["slot"].format(choices="، ".join(str(n) for n in SLOT_CHOICES)))
    notice = _int(data.get("min_notice_minutes"), -1)
    if not 0 <= notice <= MAX_NOTICE_MINUTES:
        raise ValidationError(words["notice"].format(max=MAX_NOTICE_MINUTES))
    ahead = _int(data.get("max_days_ahead"), 0)
    if not 1 <= ahead <= MAX_AHEAD_DAYS:
        raise ValidationError(words["ahead"].format(max=MAX_AHEAD_DAYS))
    shop_hours = {}
    for weekday in WEEK_ORDER:
        if data.get(f"day_{weekday}_on"):
            opens, closes = _clock(data.get(f"day_{weekday}_opens")), _clock(data.get(f"day_{weekday}_closes"))
            if opens is None or closes is None or closes <= opens:
                raise ValidationError(words["hours"].format(day=names[weekday]))
            shop_hours[weekday] = (opens, closes)
    chosen_services = []
    for item in Item.objects.filter(active=True, is_stock_tracked=False):
        if data.get(f"service_{item.pk}"):
            minutes = _int(data.get(f"duration_{item.pk}"), -1)
            if not services.MIN_MINUTES <= minutes <= services.MAX_MINUTES:
                raise ValidationError(words["duration"].format(name=item.item_name, a=services.MIN_MINUTES, b=services.MAX_MINUTES))
            chosen_services.append((item, minutes))
    chosen_staff, staff_hours = [], {}
    for person in Employee.objects.filter(active=True):
        if not data.get(f"staff_{person.pk}"):
            continue
        chosen_staff.append(person)
        for weekday in WEEK_ORDER:
            prefix = f"emp_{person.pk}_day_{weekday}"
            if data.get(f"{prefix}_on"):
                opens, closes = _clock(data.get(f"{prefix}_opens")), _clock(data.get(f"{prefix}_closes"))
                if opens is None or closes is None or closes <= opens:
                    raise ValidationError(words["staff_hours"].format(name=person.name, day=names[weekday]))
                staff_hours[(person.pk, weekday)] = (opens, closes)
    accepting = bool(data.get("accepting"))
    if accepting and not shop_hours:
        raise ValidationError(words["no_hours"])
    if accepting and not chosen_staff:
        raise ValidationError(words["no_staff"])
    if accepting and not chosen_services:
        raise ValidationError(words["no_services"])
    before = {"accepting": booking.accepting, "slot_minutes": booking.slot_minutes, "min_notice_minutes": booking.min_notice_minutes,
              "max_days_ahead": booking.max_days_ahead, "show_prices": booking.show_prices}
    booking.accepting, booking.show_prices = accepting, bool(data.get("show_prices"))
    booking.slot_minutes, booking.min_notice_minutes, booking.max_days_ahead = slot, notice, ahead
    booking.updated_by = user
    if data.get("new_link"):
        booking.public_key = _new_key()
    booking.save()
    WorkingHours.objects.filter(entity=entity).delete()
    WorkingHours.objects.bulk_create([WorkingHours(entity=entity, weekday=weekday, opens=span[0], closes=span[1]) for weekday, span in shop_hours.items()]
                                     + [WorkingHours(entity=entity, employee_id=key[0], weekday=key[1], opens=span[0], closes=span[1])
                                        for key, span in staff_hours.items()])
    # Kept rows are updated in place so their ids (in customers' half-finished booking links) stay valid.
    kept = {row.item_id: row for row in BookableService.objects.filter(entity=entity)}
    BookableService.objects.filter(entity=entity).exclude(item_id__in=[item.pk for item, _ in chosen_services]).delete()
    for item, minutes in chosen_services:
        row = kept.get(item.pk)
        if row is None:
            BookableService.objects.create(entity=entity, item=item, duration_minutes=minutes)
        elif row.duration_minutes != minutes:
            row.duration_minutes = minutes
            row.save(update_fields=["duration_minutes"])
    BookingStaff.objects.filter(entity=entity).delete()
    BookingStaff.objects.bulk_create([BookingStaff(entity=entity, employee=person) for person in chosen_staff])
    after = {"accepting": booking.accepting, "slot_minutes": slot, "min_notice_minutes": notice, "max_days_ahead": ahead,
             "show_prices": booking.show_prices, "days": sorted(shop_hours), "services": [item.pk for item, _ in chosen_services],
             "staff": [person.pk for person in chosen_staff], "new_link": bool(data.get("new_link"))}
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="appointments", action="booking_settings",
                            object_type="appointments.BookingSettings", object_id=str(booking.pk), before_data=before, after_data=after)
    return booking


def day_label(day, lang):
    return f"{DAY_NAMES[lang][day.weekday()]} {day.day} {MONTH_NAMES[lang][day.month - 1]}"


def clock_label(moment, lang):
    """4:30 م / 4:30 PM, from an aware datetime or a time."""

    clock = timezone.localtime(moment).time() if isinstance(moment, datetime) else moment
    hour = clock.hour % 12 or 12
    suffix = ("م" if clock.hour >= 12 else "ص") if lang == "ar" else ("PM" if clock.hour >= 12 else "AM")
    return f"{hour}:{clock.minute:02d}", suffix


def parse_day(raw):
    try:
        return date.fromisoformat(str(raw or ""))
    except ValueError:
        return None


def parse_start(day, raw):
    clock = _clock(raw)
    if day is None or clock is None:
        return None
    return _aware(day, clock)
