"""S3 (HG-077): the public online booking page (no account) and the owner's booking settings screen.

The public page is server-rendered and works without JavaScript: each step is a GET with the choices so far in the
query string (service → employee → day and time → details); the details are POSTed once (CSRF-protected, with a
honeypot field and a per-address and per-phone throttle) and answered with a redirect to a signed confirmation link.
It answers 404 unless the «الحجز أونلاين» capability and the appointments module are on and the entity takes bookings.
It shows no staff link, no other customer's name and no booked time's owner, only what is free.
"""

from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import booking as engine
from .views import BOOK

SETTINGS_PERMISSION = "settings.view_settings"  # with BOOK: owners and managers, not the till staff
RATE_WINDOW = 60
PAGE_LIMIT = 120  # page views per address per minute (each computes free slots)
BOOK_WINDOW = 3600
BOOK_IP_LIMIT = 10  # booking attempts per address per hour
BOOK_PHONE_LIMIT = 5  # booking attempts per phone number per hour
STRIP_DAYS = 7

WORDS = {
    "ar": {
        "title": "احجز ميعادك", "lead": "اختار الخدمة وبعدها الموظف واليوم. من غير تسجيل دخول.",
        "steps": ("الخدمة", "الموظف", "الميعاد", "بياناتك"), "step_of": "الخطوة {n} من 4",
        "sec_service": "اختار الخدمة", "sec_staff": "اختار الموظف", "sec_time": "اليوم والميعاد", "sec_details": "بياناتك",
        "head_staff": "اختار حد معيّن، أو سيبنا نختارلك أول حد فاضي.", "head_time": "المواعيد دي فاضية فعلاً دلوقتي.",
        "head_details": "محتاجين اسمك ورقمك علشان المحل يقدر يكلمك.",
        "any": "أي حد فاضي", "any_sub": "أقرب ميعاد متاح", "min": "دقيقة", "change": "تغيير", "continue": "كمّل",
        "prev": "الأسبوع اللي فات", "next": "الأسبوع الجاي", "today": "النهاردة", "closed": "إجازة", "full": "مشغول", "free_n": "متاح",
        "morning": "الصبح", "afternoon": "بعد الضهر", "evening": "بالليل", "slots_hint": "المواعيد معروضة على مدة الخدمة ({n} دقيقة).",
        "empty_h": "مفيش مواعيد فاضية في اليوم ده", "empty_p": "جرّب يوم تاني من الشريط اللي فوق.", "empty_any": "أو جرّب أي حد فاضي في نفس اليوم",
        "sugg": "أقرب ميعاد متاح", "sugg_book": "اختار الميعاد ده", "none_left": "مفيش مواعيد فاضية في الفترة الجاية. كلّم المحل.",
        "name": "اسمك", "phone": "رقم الموبايل", "phone_hint": "المحل هيكلمك أو يبعتلك على الرقم ده.", "notes": "ملاحظات للمحل", "optional": "اختياري",
        "confirm": "أكّد الحجز", "sum": "ملخص حجزك", "k_service": "الخدمة", "k_staff": "مع", "k_when": "الميعاد", "k_dur": "المدة", "k_price": "السعر",
        "k_name": "الاسم", "k_addr": "العنوان", "nopay": "مفيش دفع هنا: الحساب في المحل.",
        "deposit": "ممكن المحل يطلب عربون على الحجز. لو لغيت قبل الميعاد بأقل من {hours} ساعة (أو مجيتش) العربون مابيترجعش.",
        "done_h": "تم حجز ميعادك", "done_p": "ميعادك اتسجّل عند {shop}.", "ref": "رقم الحجز", "remind": "لو احتجت تغيّر الميعاد أو تلغيه، كلّم المحل.",
        "call": "اتصل", "wa": "واتساب", "book_another": "احجز ميعاد تاني", "foot": "الحجز بيتسجل مباشرة عند المحل.", "honeypot": "سيب الخانة دي فاضية",
        "too_many": "طلبات كتير في وقت قصير. استنى شوية وجرّب تاني.", "no_services": "مفيش خدمات متاحة للحجز دلوقتي. كلّم المحل.",
    },
    "en": {
        "title": "Book your appointment", "lead": "Choose a service, then who and a day. No account needed.",
        "steps": ("Service", "Who", "Time", "Your details"), "step_of": "Step {n} of 4",
        "sec_service": "Choose a service", "sec_staff": "Choose who", "sec_time": "Day and time", "sec_details": "Your details",
        "head_staff": "Pick someone, or let us choose whoever is free first.", "head_time": "These times are really free right now.",
        "head_details": "We need your name and number so the shop can reach you.",
        "any": "Anyone available", "any_sub": "Earliest free time", "min": "min", "change": "Change", "continue": "Continue",
        "prev": "Previous week", "next": "Next week", "today": "Today", "closed": "Closed", "full": "Full", "free_n": "Open",
        "morning": "Morning", "afternoon": "Afternoon", "evening": "Evening", "slots_hint": "Times are shown for the length of the service ({n} minutes).",
        "empty_h": "No free times on this day", "empty_p": "Try another day from the strip above.", "empty_any": "Or try anyone available on this day",
        "sugg": "Next available time", "sugg_book": "Choose this time", "none_left": "No free times in the coming days. Please contact the shop.",
        "name": "Your name", "phone": "Mobile number", "phone_hint": "The shop will call or message you on this number.", "notes": "Notes for the shop", "optional": "optional",
        "confirm": "Confirm booking", "sum": "Your booking", "k_service": "Service", "k_staff": "With", "k_when": "When", "k_dur": "Duration", "k_price": "Price",
        "k_name": "Name", "k_addr": "Address", "nopay": "Nothing is paid here: you pay at the shop.",
        "deposit": "The shop may ask for a deposit on the booking. Cancelling fewer than {hours} hours before (or not coming) forfeits it.",
        "done_h": "Your appointment is booked", "done_p": "Your appointment is saved at {shop}.", "ref": "Booking no.", "remind": "Need to change or cancel? Contact the shop.",
        "call": "Call", "wa": "WhatsApp", "book_another": "Book another appointment", "foot": "Bookings go straight to the shop.", "honeypot": "Leave this field empty",
        "too_many": "Too many requests in a short time. Please wait a little and try again.", "no_services": "No services can be booked right now. Please contact the shop.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _private(response):
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    patch_vary_headers(response, ("Accept-Language",))
    return response


def _address(request):
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def _throttled(bucket, who, limit, window):
    """A counter per bucket, caller and window (the payments pattern, HG-070); enough to blunt a flood."""

    key = f"booking:{bucket}:{who}:{int(timezone.now().timestamp()) // window}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window * 2)
        count = 1
    return count > limit


def _too_many(lang):
    return _private(HttpResponse(WORDS[lang]["too_many"], status=429, content_type="text/plain; charset=utf-8"))


def _company():
    from printing.company import company_details
    from reports.aging import whatsapp_number

    company = company_details()
    return company, company["phone"], whatsapp_number(company["phone"])


def _deposit_text(words):
    from deposits import services as deposits

    return words["deposit"].format(hours=deposits.cancel_hours()) if deposits.enabled() else ""


def _price(service, booking, lang, amount=None):
    """The shown price: the item's list price, or `amount` (the booked appointment's saved price) when given."""

    if not booking.show_prices:
        return ""
    from settings_core.templatetags.hesba_format import money

    from workorders.public import CURRENCY_WORDS

    company, _, _ = _company()
    value = service.item.default_sale_price if amount is None else amount
    return f"{money(value)} {CURRENCY_WORDS.get(company['currency'], {}).get(lang, company['currency'])}"


class _Choices:
    """What the customer picked so far, read from the query string or the form, each checked against this page."""

    def __init__(self, booking, source):
        self.services = engine.services_of(booking.entity)
        self.staff = engine.staff_of(booking.entity)
        raw = str(source.get("service") or "")
        self.service = next((row for row in self.services if str(row.pk) == raw), None)
        raw = str(source.get("staff") or "")
        self.any = raw == "any"
        self.employee = next((person for person in self.staff if str(person.pk) == raw), None)
        self.staff_chosen = self.any or self.employee is not None
        self.day = engine.parse_day(source.get("day"))
        self.time = str(source.get("time") or "")[:5]
        self.start = engine.parse_start(self.day, self.time) if self.time else None

    def query(self, lang, **override):
        values = {"lang": lang}
        if self.service is not None:
            values["service"] = self.service.pk
        if self.staff_chosen:
            values["staff"] = "any" if self.any else self.employee.pk
        if self.day is not None:
            values["day"] = self.day.isoformat()
        if self.time:
            values["time"] = self.time
        values.update(override)
        return "?" + urlencode({key: value for key, value in values.items() if value not in (None, "")})


def _groups(slots, lang, words):
    groups = {"morning": [], "afternoon": [], "evening": []}
    for slot in slots:
        hour = timezone.localtime(slot["start"]).hour
        label, suffix = engine.clock_label(slot["start"], lang)
        groups["morning" if hour < 12 else "afternoon" if hour < 17 else "evening"].append(
            {"time": f"{timezone.localtime(slot['start']):%H:%M}", "label": label, "suffix": suffix})
    return [{"key": key, "title": words[key], "slots": rows} for key, rows in groups.items() if rows]


def _context(request, booking, lang, choices, error="", form=None):
    words = WORDS[lang]
    company, phone, whatsapp = _company()
    base = reverse("book_page", args=[booking.public_key])
    now = timezone.now()
    first, last = engine.booking_days(booking, now)
    step = 1
    if choices.service is not None:
        step = 3 if choices.staff_chosen else 2
    employee = choices.employee if not choices.any else None
    context = {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "company": company, "phone": phone, "whatsapp": whatsapp,
        "switch_url": base + choices.query("ar" if lang == "en" else "en"), "base": base, "choices": choices, "error": error,
        "form": form or {}, "booking": booking, "entity_name": booking.entity.display(lang) if _many_entities() else "",
        "services": [{"row": row, "price": _price(row, booking, lang), "url": base + choices.query(lang, service=row.pk, staff=None, day=None, time=None),
                      "selected": choices.service is not None and row.pk == choices.service.pk} for row in choices.services],
    }
    if step >= 2:
        context["staff"] = [{"pk": "any", "name": words["any"], "sub": words["any_sub"], "initial": "", "any": True, "selected": choices.any,
                             "url": base + choices.query(lang, staff="any", time=None)}] + [
            {"pk": person.pk, "name": person.name, "sub": person.title, "initial": person.name[:1], "any": False,
             "selected": employee is not None and person.pk == employee.pk, "url": base + choices.query(lang, staff=person.pk, time=None)}
            for person in choices.staff]
    if step >= 3:
        hours, staff = engine.hours_map(booking.entity), choices.staff
        strip_start = engine.parse_day(request.GET.get("from")) or (choices.day if choices.day else first)
        strip_start = min(max(strip_start, first), last)
        strip_start = max(first, strip_start - timedelta(days=(strip_start - first).days % STRIP_DAYS))
        days, chosen_day = [], choices.day if choices.day and first <= choices.day <= last else None
        for offset in range(STRIP_DAYS):
            day = strip_start + timedelta(days=offset)
            if day > last:
                break
            free = engine.free_slots(booking, choices.service, day, employee, now, hours=hours, staff=staff)
            shut = hours.get((None, day.weekday())) is None
            if chosen_day is None and free:
                chosen_day = day
            days.append({"day": day, "free": len(free), "closed": shut, "w": engine.SHORT_DAY_NAMES[lang][day.weekday()], "n": day.day,
                         "tag": words["today"] if day == first else words["closed"] if shut else words["full"] if not free else words["free_n"],
                         "label": engine.day_label(day, lang), "url": base + choices.query(lang, day=day.isoformat(), time=None, **{"from": strip_start.isoformat()})})
        chosen_day = chosen_day or strip_start
        slots = engine.free_slots(booking, choices.service, chosen_day, employee, now, hours=hours, staff=staff)
        for row in days:
            row["selected"] = row["day"] == chosen_day
        picked = next((slot for slot in slots if choices.start is not None and slot["start"] == choices.start), None)
        if picked is not None and choices.day == chosen_day:
            step = 4
        elif choices.time and not error:
            error = engine.MESSAGES[lang]["taken"]
        suggestion = None
        if not slots:
            found = engine.next_free(booking, choices.service, employee, after=chosen_day + timedelta(days=1), now=now)
            if found is not None:
                found_day = timezone.localtime(found["start"]).date()
                label, suffix = engine.clock_label(found["start"], lang)
                suggestion = {"label": f"{engine.day_label(found_day, lang)} · {label} {suffix}",
                              "url": base + choices.query(lang, day=found_day.isoformat(), time=f"{timezone.localtime(found['start']):%H:%M}",
                                                         **{"from": found_day.isoformat()})}
        prev_start = strip_start - timedelta(days=STRIP_DAYS)
        next_start = strip_start + timedelta(days=STRIP_DAYS)
        context.update({
            "days": days, "chosen_day": chosen_day, "groups": _groups(slots, lang, words), "has_slots": bool(slots), "suggestion": suggestion,
            "month": f"{engine.MONTH_NAMES[lang][strip_start.month - 1]} {strip_start.year}",
            "prev_url": base + choices.query(lang, day=None, time=None, **{"from": max(prev_start, first).isoformat()}) if strip_start > first else "",
            "next_url": base + choices.query(lang, day=None, time=None, **{"from": next_start.isoformat()}) if next_start <= last else "",
            "any_url": base + choices.query(lang, staff="any", time=None) if employee is not None else "",
            "slots_hint": words["slots_hint"].format(n=choices.service.duration_minutes),
            "slot_base": base + choices.query(lang, day=chosen_day.isoformat(), time=None),
        })
    if step == 4:
        label, suffix = engine.clock_label(choices.start, lang)
        context["when"] = f"{engine.day_label(choices.day, lang)} · {label} {suffix}"
        context["deposit_text"] = _deposit_text(words)
    context["step"] = step
    context["steps"] = [{"label": label, "state": "done" if index + 1 < step else "now" if index + 1 == step else ""} for index, label in enumerate(words["steps"])]
    context["step_of"] = words["step_of"].format(n=step)
    if choices.service is not None:
        price = _price(choices.service, booking, lang)
        context["summary"] = [(words["k_service"], choices.service.item.item_name)] + (
            [(words["k_staff"], words["any"] if choices.any else choices.employee.name)] if choices.staff_chosen else []) + (
            [(words["k_when"], context["when"])] if step == 4 else []) + [(words["k_dur"], f"{choices.service.duration_minutes} {words['min']}")] + (
            [(words["k_price"], price)] if price else [])
        context["chips"] = [{"text": choices.service.item.item_name, "url": base + choices.query(lang, service=None, staff=None, day=None, time=None)}]
        if choices.staff_chosen:
            context["chips"].append({"text": words["any"] if choices.any else choices.employee.name, "url": base + choices.query(lang, staff=None, time=None)})
        if step == 4:
            context["chips"].append({"text": context["when"], "url": base + choices.query(lang, time=None)})
    return context


def _many_entities():
    from entities.services import is_multi_entity

    return is_multi_entity()


def _render(request, booking, lang, choices, error="", form=None, status=200):
    return _private(render(request, "appointments/book_public.html", _context(request, booking, lang, choices, error, form), status=status))


@never_cache
@csrf_protect
def page(request, key):
    booking = engine.by_key(key)
    if booking is None:
        raise Http404("No booking page here.")
    lang = _lang(request)
    if _throttled("page", _address(request), PAGE_LIMIT, RATE_WINDOW):
        return _too_many(lang)
    if request.method != "POST":
        return _render(request, booking, lang, _Choices(booking, request.GET))
    return _submit(request, booking, lang)


def _submit(request, booking, lang):
    words = engine.MESSAGES[lang]
    choices = _Choices(booking, request.POST)
    form = {"name": request.POST.get("name", "")[:120], "phone": request.POST.get("phone", "")[:30], "notes": request.POST.get("notes", "")[:200]}
    if request.POST.get("website"):  # the honeypot: a person never sees it, a script fills it in
        return _render(request, booking, lang, choices, words["failed"], form, status=400)
    if _throttled("book_ip", _address(request), BOOK_IP_LIMIT, BOOK_WINDOW):
        return _too_many(lang)
    phone_digits = engine.phone_key(engine.latin_digits(form["phone"]))
    if phone_digits and _throttled("book_phone", phone_digits, BOOK_PHONE_LIMIT, BOOK_WINDOW):
        return _too_many(lang)
    if choices.service is None or not choices.staff_chosen:
        return _render(request, booking, lang, choices, words["service" if choices.service is None else "staff"], form, status=400)
    try:
        appointment = engine.book_online(booking, choices.service, choices.employee if not choices.any else None, choices.start,
                                         form["name"], form["phone"], form["notes"], lang)
    except ValidationError as exc:
        code = getattr(exc, "code", None)
        if code in ("taken", "when"):
            choices.time = ""  # back to the free times, refreshed
            choices.start = None
            return _render(request, booking, lang, choices, exc.messages[0], form, status=409)
        return _render(request, booking, lang, choices, exc.messages[0], form, status=400)
    done = reverse("book_done", args=[booking.public_key, engine.done_token(appointment)])
    return _private(HttpResponseRedirect(f"{done}?lang={lang}", status=303))


@never_cache
def done(request, key, token):
    booking = engine.by_key(key)
    if booking is None:
        raise Http404("No booking page here.")
    lang = _lang(request)
    if _throttled("page", _address(request), PAGE_LIMIT, RATE_WINDOW):
        return _too_many(lang)
    appointment = engine.read_done_token(token, booking)
    if appointment is None:
        raise Http404("No such booking.")
    words = WORDS[lang]
    company, phone, whatsapp = _company()
    label, suffix = engine.clock_label(appointment.starts_at, lang)
    local_day = timezone.localtime(appointment.starts_at).date()
    service = next((row for row in engine.services_of(booking.entity) if row.item_id == appointment.service_id), None)
    rows = [(words["k_service"], appointment.service.item_name if appointment.service else "—"),
            (words["k_staff"], appointment.employee.name if appointment.employee else "—"),
            (words["k_when"], f"{engine.day_label(local_day, lang)} · {label} {suffix}"),
            (words["k_dur"], f"{appointment.duration_minutes} {words['min']}")]
    if service is not None and booking.show_prices:
        rows.append((words["k_price"], _price(service, booking, lang, amount=appointment.price)))
    if company.get("address"):
        rows.append((words["k_addr"], company["address"]))
    other = "en" if lang == "ar" else "ar"
    base = reverse("book_page", args=[booking.public_key])
    return _private(render(request, "appointments/book_done.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "company": company, "phone": phone, "whatsapp": whatsapp,
        "appointment": appointment, "rows": rows, "done_p": words["done_p"].format(shop=booking.entity.display(lang) if _many_entities() else company["name"]),
        "deposit_text": _deposit_text(words), "again_url": f"{base}?lang={lang}", "switch_url": f"{request.path}?lang={other}",
        "entity_name": booking.entity.display(lang) if _many_entities() else "",
    }))


# ---- the owner's settings screen ---------------------------------------------------


SETTINGS_WORDS = {
    "ar": {
        "title": "الحجز أونلاين", "intro": "صفحة عامة العميل بيحجز منها لوحده من الموبايل، على المواعيد الفاضية فعلاً. حدّد مواعيد العمل والخدمات والموظفين.",
        "entity": "الفرع", "show": "عرض", "accepting": "الصفحة العامة بتاخد حجوزات", "link": "لينك الصفحة العامة", "copy": "انسخ اللينك",
        "no_base": "اللينك بيتعمل من عنوان الموقع الرسمي (PUBLIC_BASE_URL)، ولسه مش متحدد. اطلبه من الدعم.", "new_link": "اعمل لينك جديد (اللينك القديم يبطّل)",
        "rules": "قواعد المواعيد", "slot": "المواعيد كل (دقيقة)", "notice": "أقل وقت قبل الميعاد (دقيقة)", "ahead": "الحجز لحد كام يوم قدام",
        "show_prices": "اعرض سعر الخدمة في الصفحة العامة", "prices_note": "مقفول افتراضيًا لحد ما أحمد يقرر.",
        "hours": "مواعيد العمل", "hours_note": "اليوم اللي مش متعلّم عليه إجازة. من غير مواعيد بعد نص الليل.", "open": "شغال", "opens": "من", "closes": "لحد",
        "services": "الخدمات اللي بتتحجز أونلاين", "services_note": "الخدمات بس (مش أصناف المخزن). المدة هي مدة الميعاد.", "duration": "المدة (دقيقة)",
        "no_items": "مفيش خدمات لسه. ضيف صنف خدمة (من غير مخزون) من الأصناف.",
        "staff": "الموظفين اللي بياخدوا حجوزات", "staff_note": "لكل موظف مواعيد خاصة (اختياري)؛ لو مفيش بياخد مواعيد الفرع.", "own_hours": "مواعيده الخاصة",
        "no_staff": "مفيش موظفين لسه. ضيفهم من شاشة الموظفين.", "save": "احفظ", "saved": "اتحفظت إعدادات الحجز أونلاين.", "back": "المواعيد",
        "off": "خاصية «الحجز أونلاين» مقفولة؛ شغّلها من الإعدادات ← الخصائص.", "days": engine.DAY_NAMES["ar"],
    },
    "en": {
        "title": "Online booking", "intro": "A public page customers book on by themselves from their phones, on times that are really free. Set the working hours, services and employees.",
        "entity": "Branch", "show": "Show", "accepting": "The public page takes bookings", "link": "Public page link", "copy": "Copy link",
        "no_base": "The link is built from the site's official address (PUBLIC_BASE_URL), which is not set yet. Ask support.", "new_link": "Make a new link (the old one stops working)",
        "rules": "Time rules", "slot": "Times every (minutes)", "notice": "Minimum notice (minutes)", "ahead": "Book up to how many days ahead",
        "show_prices": "Show service prices on the public page", "prices_note": "Off by default until Ahmed decides.",
        "hours": "Working hours", "hours_note": "An unticked day is closed. No hours past midnight.", "open": "Open", "opens": "From", "closes": "To",
        "services": "Services bookable online", "services_note": "Services only (not stock items). The duration is the appointment's length.", "duration": "Duration (min)",
        "no_items": "No services yet. Add a service item (not stock-tracked) from the items screen.",
        "staff": "Employees who take bookings", "staff_note": "Each can have their own hours (optional); without them they work the branch's hours.", "own_hours": "Own hours",
        "no_staff": "No employees yet. Add them from the staff screen.", "save": "Save", "saved": "Online booking settings saved.", "back": "Appointments",
        "off": "The “Online booking” feature is off; switch it on from Settings → Features.", "days": engine.DAY_NAMES["en"],
    },
}


def _settings_entity(request):
    """The entity being set up: the one worked in, the main one on a single-entity install, else one picked from the
    entities this user may work in (whole-group view)."""

    from entities.current import allowed_entities, current_entity
    from entities.services import main_entity

    chosen = current_entity()
    if chosen is not None:
        return chosen, []
    allowed = allowed_entities(request.user)
    if len(allowed) <= 1:
        return (allowed[0] if allowed else main_entity()), []
    raw = str(request.GET.get("entity") or request.POST.get("entity") or "")
    return next((one for one in allowed if str(one.pk) == raw), allowed[0]), allowed


def can_manage(user):
    return user_has_permission(user, BOOK) and user_has_permission(user, SETTINGS_PERMISSION)


@require_permission(BOOK)
def booking_settings(request):
    if not user_has_permission(request.user, SETTINGS_PERMISSION):
        raise PermissionDenied("Online booking settings need settings.view_settings.")
    from master_data.models import Item
    from staff.models import Employee

    lang = _lang(request)
    words = SETTINGS_WORDS[lang]
    entity, entity_choices = _settings_entity(request)
    booking = engine.settings_for(entity)
    error = ""
    if request.method == "POST":
        try:
            engine.save_settings(entity, request.POST, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('appointments:booking_settings')}?lang={lang}&entity={entity.pk}")
    hours = engine.hours_map(entity)
    posted = request.POST if request.method == "POST" else None

    def day_rows(employee=None):
        rows = []
        for weekday in engine.WEEK_ORDER:
            span = hours.get((employee.pk if employee else None, weekday))
            prefix = f"emp_{employee.pk}_day_{weekday}" if employee else f"day_{weekday}"
            if posted is not None:
                on, opens, closes = bool(posted.get(f"{prefix}_on")), posted.get(f"{prefix}_opens", ""), posted.get(f"{prefix}_closes", "")
            else:
                on, opens, closes = span is not None, f"{span[0]:%H:%M}" if span else "10:00", f"{span[1]:%H:%M}" if span else "20:00"
            rows.append({"weekday": weekday, "name": words["days"][weekday], "prefix": prefix, "on": on, "opens": opens, "closes": closes})
        return rows

    chosen_services = {row.item_id: row.duration_minutes for row in engine.BookableService.objects.filter(entity=entity)}
    chosen_staff = {row.employee_id for row in engine.BookingStaff.objects.filter(entity=entity)}
    items = [{"item": item, "on": bool(posted.get(f"service_{item.pk}")) if posted is not None else item.pk in chosen_services,
              "duration": posted.get(f"duration_{item.pk}", "30") if posted is not None else chosen_services.get(item.pk, 30)}
             for item in Item.objects.filter(active=True, is_stock_tracked=False).order_by("item_name")]
    people = [{"employee": person, "on": bool(posted.get(f"staff_{person.pk}")) if posted is not None else person.pk in chosen_staff,
               "own": any(key[0] == person.pk for key in hours), "days": day_rows(person)} for person in Employee.objects.filter(active=True)]
    values = posted if posted is not None else {"slot_minutes": booking.slot_minutes, "min_notice_minutes": booking.min_notice_minutes,
                                                "max_days_ahead": booking.max_days_ahead, "accepting": booking.accepting, "show_prices": booking.show_prices}
    from .views import _base

    return render(request, "appointments/booking_settings.html", _base(
        request, words=words, page_title=words["title"], entity=entity, entity_choices=entity_choices, booking=booking, error=error,
        public_url=engine.public_url(booking, lang), feature_on=engine.enabled(), days=day_rows(), items=items, people=people,
        values=values, slot_choices=engine.SLOT_CHOICES,
    ))
