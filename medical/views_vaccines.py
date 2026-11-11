"""M7 (HG-090) screens: the due list, the vaccines set-up, the record form and the dose's two actions (bill, remove).

Every screen is a vet screen: 404 outside a vet installation (or a vet entity of a group), 403 without a clinical permission
(an employee link alone gives nothing, HG-084). The set-up is the owner's and manager's. Another entity's vaccine or dose answers
404 before any action runs, as every scoped record does (HG-034, HG-074). Every rule is checked again in ``medical.vaccines``.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from entities import scope as entity_scope

from . import vaccines, vet
from .models import Animal, Vaccination
from .views import _base, _guard, _id, _lang, _tabs

WORDS = {
    "ar": {
        "title": "التطعيمات", "intro": "كل حيوان جرعته الجاية امتى: المتأخر والجاي في الشهر، وموبايل صاحبه تتصل بيه أو تبعتله واتساب.",
        "search": "دوّر باسم الحيوان أو صاحبه أو تليفونه أو التطعيم", "find": "بحث", "filter": "فلترة", "clear": "مسح البحث",
        "f_all": "الكل", "f_overdue": "متأخر", "f_week": "الأسبوع ده", "f_month": "الشهر ده",
        "animal": "الحيوان", "owner": "صاحبه", "vaccine": "التطعيم", "due": "الجرعة الجاية", "phone": "التليفون", "last_dose": "آخر جرعة",
        "late_n": "متأخر {n} يوم", "late_1": "متأخر يوم", "today": "النهارده", "in_n": "بعد {n} يوم", "in_1": "بعد يوم",
        "call": "اتصل", "whatsapp": "واتساب", "wa_text": "أهلاً {name}، تذكير من {company}: ميعاد جرعة {vaccine} لـ {animal} يوم {date}. تحب نحجزلك ميعاد؟",
        "none": "مفيش جرعات جاية ولا متأخرة.", "none_filtered": "مفيش نتايج بالبحث ده.", "setup": "أنواع التطعيمات",
        "no_vaccines": "لسه مفيش أنواع تطعيمات. ضيف أول واحد من «أنواع التطعيمات» عشان تسجّل جرعات.", "open_animal": "افتح ملف الحيوان",
        "card": "التطعيمات", "record": "سجّل تطعيم", "no_doses": "مفيش تطعيمات متسجّلة للحيوان ده.", "given": "اتاخد", "next": "الجرعة الجاية",
        "closed": "اتقفلت بجرعة أحدث", "no_next": "من غير ميعاد", "batch": "رقم التشغيلة", "by": "الدكتور", "note": "ملاحظة",
        "bill": "اعمل فاتورة", "billed": "اتفوتر", "draft": "مسودة", "posted": "مترحّلة", "cancelled": "ملغية", "invoice": "الفاتورة", "open_invoice": "افتح الفاتورة",
        "remove": "شيل", "remove_q": "تشيل تطعيم {vaccine} اللي اتسجّل بتاريخ {day}؟", "remove_yes": "أيوه، شيله", "remove_hint": "للتطعيم اللي اتسجّل بالغلط ومش على فاتورة.",
        "bill_hint": "بتطلع فاتورة بيع مسودة باسم صاحب الحيوان؛ مفيش حاجة بتترحّل.",
        "form_title": "تسجيل تطعيم", "form_intro": "اختار التطعيم والتاريخ. الجرعة الجاية بتتحسب من مدة التطعيم، وتقدر تغيّرها.",
        "choose": "اختار", "given_on": "تاريخ التطعيم", "next_due": "ميعاد الجرعة الجاية", "next_hint": "سيبه فاضي عشان يتحسب لوحده من مدة التطعيم.",
        "no_next_box": "من غير جرعة جاية", "visit": "من كشف", "visit_none": "من غير كشف", "doctor": "الدكتور", "doctor_none": "—", "save": "سجّل التطعيم",
        "cancel": "رجوع", "every_n": "كل {n} يوم", "every_year": "كل سنة", "every_years": "كل {n} سنين", "one_off": "مرة واحدة", "any_species": "كل الأنواع",
        "saved": "اتسجّل تطعيم {vaccine} لـ {animal}.", "removed": "اتشال التطعيم.", "billed_ok": "فاتورة التطعيم {number} مسودة.",
        "bill_open": "الفاتورة {number} ({status})",
        "setup_title": "أنواع التطعيمات", "setup_intro": "التطعيمات اللي العيادة بتديها، ومدة كل واحد لحد الجرعة الجاية، والصنف اللي بيتفوتر بيه.",
        "new_vaccine": "تطعيم جديد", "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي (اختياري)", "species": "النوع", "interval": "الجرعة الجاية بعد (يوم)",
        "interval_hint": "سيبها فاضية لو مرة واحدة. 365 يعني سنة بنفس التاريخ.", "item": "الصنف للفاتورة", "item_none": "من غير فاتورة من هنا",
        "item_hint": "اختياري: لو اخترت صنف، «اعمل فاتورة» بتطلع مسودة بسعره، والمخزون بيتخصم لما الفاتورة تترحّل.",
        "active": "شغّال", "off": "موقوف", "save_vaccine": "حفظ", "add_vaccine": "ضيف التطعيم", "edit": "تعديل", "none_vaccines": "مفيش أنواع تطعيمات لسه.",
        "setup_saved": "اتحفظ التطعيم.", "back": "التطعيمات", "back_animal": "ملف الحيوان", "doses_n": "{n} جرعة", "price": "السعر",
    },
    "en": {
        "title": "Vaccinations", "intro": "When each animal's next dose is due: the overdue ones and the month ahead, with the owner's phone to call or message.",
        "search": "Search by animal, owner, phone or vaccine", "find": "Search", "filter": "Filter", "clear": "Clear the search",
        "f_all": "All", "f_overdue": "Overdue", "f_week": "This week", "f_month": "This month",
        "animal": "Animal", "owner": "Owner", "vaccine": "Vaccine", "due": "Next dose", "phone": "Phone", "last_dose": "Last dose",
        "late_n": "{n} days late", "late_1": "1 day late", "today": "Today", "in_n": "In {n} days", "in_1": "In 1 day",
        "call": "Call", "whatsapp": "WhatsApp", "wa_text": "Hello {name}, a reminder from {company}: {animal}'s {vaccine} dose is due on {date}. Would you like us to book it?",
        "none": "No doses due or overdue.", "none_filtered": "Nothing matches this search.", "setup": "Vaccines",
        "no_vaccines": "No vaccines yet. Add the first one under “Vaccines” to record doses.", "open_animal": "Open the animal's file",
        "card": "Vaccinations", "record": "Record a vaccination", "no_doses": "No vaccinations recorded for this animal.", "given": "Given", "next": "Next dose",
        "closed": "Closed by a later dose", "no_next": "No next dose", "batch": "Batch / lot", "by": "Doctor", "note": "Note",
        "bill": "Make an invoice", "billed": "Billed", "draft": "Draft", "posted": "Posted", "cancelled": "Cancelled", "invoice": "Invoice", "open_invoice": "Open the invoice",
        "remove": "Remove", "remove_q": "Remove the {vaccine} dose recorded on {day}?", "remove_yes": "Yes, remove it", "remove_hint": "For a dose recorded by mistake and on no invoice.",
        "bill_hint": "Makes a draft sales invoice in the owner's name; nothing is posted.",
        "form_title": "Record a vaccination", "form_intro": "Choose the vaccine and the day. The next dose is worked out from the vaccine's interval, and you can change it.",
        "choose": "Choose", "given_on": "Date given", "next_due": "Next dose date", "next_hint": "Leave it empty to work it out from the vaccine's interval.",
        "no_next_box": "No next dose", "visit": "Part of visit", "visit_none": "No visit", "doctor": "Doctor", "doctor_none": "—", "save": "Record the vaccination",
        "cancel": "Back", "every_n": "every {n} days", "every_year": "every year", "every_years": "every {n} years", "one_off": "one dose", "any_species": "any species",
        "saved": "{vaccine} recorded for {animal}.", "removed": "The vaccination was removed.", "billed_ok": "Vaccination invoice {number} is a draft.",
        "bill_open": "Invoice {number} ({status})",
        "setup_title": "Vaccines", "setup_intro": "The vaccines the clinic gives, the days until each one's next dose, and the item they are billed on.",
        "new_vaccine": "New vaccine", "name_ar": "Name in Arabic", "name_en": "Name in English (optional)", "species": "Species", "interval": "Next dose after (days)",
        "interval_hint": "Leave it empty for a one-off. 365 means a year, on the same date.", "item": "Item to bill", "item_none": "Not billed from here",
        "item_hint": "Optional: with an item, “Make an invoice” makes a draft at its price, and stock leaves when the invoice is posted.",
        "active": "On", "off": "Off", "save_vaccine": "Save", "add_vaccine": "Add the vaccine", "edit": "Edit", "none_vaccines": "No vaccines yet.",
        "setup_saved": "Vaccine saved.", "back": "Vaccinations", "back_animal": "Animal's file", "doses_n": "{n} doses", "price": "Price",
    },
}


def _words(lang):
    return WORDS[lang]


def _error(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _guard_vet(request, write=False):
    """A medical install, a clinical permission (the shared guard), and a vet: another business's vaccination page is a 404."""

    _guard(request, write=write)
    if not vaccines.is_vet():
        raise Http404("Vaccinations are for vet clinics.")


def _animal(pk):
    return get_object_or_404(Animal.objects.select_related("owner"), pk=pk)


def interval_text(vaccine, lang):
    words = _words(lang)
    days = vaccine.interval_days
    if not days:
        return words["one_off"]
    if days % 365 == 0:
        years = days // 365
        return words["every_year"] if years == 1 else words["every_years"].format(n=years)
    return words["every_n"].format(n=days)


def status_text(days, lang):
    words = _words(lang)
    if days < 0:
        return words["late_1"] if days == -1 else words["late_n"].format(n=-days)
    if days == 0:
        return words["today"]
    return words["in_1"] if days == 1 else words["in_n"].format(n=days)


def _tab_context(request, current="vaccinations"):
    return _tabs(request, current)


# ---- the due list ----------------------------------------------------------------------


def due_list(request):
    from settings_core.templatetags.hesba_format import day_text

    from printing.company import company_details
    from printing.share import whatsapp_link

    _guard_vet(request)
    lang = _lang(request)
    words = _words(lang)
    window = request.GET.get("f") if request.GET.get("f") in vaccines.FILTERS else "all"
    query = (request.GET.get("q") or "").strip()[:100]
    result = vaccines.due_list(request.user, window=window, q=query, lang=lang)
    company = company_details()["name"]
    for row in result["rows"]:
        row["status_text"] = status_text(row["days"], lang)
        row["due_text"] = day_text(row["due"], lang)
        row["last_text"] = day_text(row["dose"].given_on, lang)
        row["wa_url"] = (whatsapp_link(row["phone"], words["wa_text"].format(name=row["owner"].name, company=company, vaccine=row["vaccine"].display(lang),
                                                                              animal=row["animal"].name, date=row["due_text"])) if row["whatsapp"] else "")
    chips = [("all", words["f_all"], None), ("overdue", words["f_overdue"], result["counts"]["overdue"]), ("week", words["f_week"], result["counts"]["week"]),
             ("month", words["f_month"], result["counts"]["month"])]
    has_vaccines = vaccines.scoped_vaccines().filter(active=True).exists()
    return render(request, "medical/vaccinations.html", _base(
        lang, page_title=words["title"], vx=words, **_tab_context(request), rows=result["rows"], chips=chips, window=window, q=query, filtered=bool(query or window != "all"),
        can_setup=vaccines.can_setup(request.user), has_vaccines=has_vaccines))


# ---- the vaccines set-up ---------------------------------------------------------------


def _items():
    from master_data.models import Item

    return Item.objects.filter(active=True).order_by("item_name")


def vaccine_setup(request):
    from master_data.models import Item

    _guard_vet(request)
    if not vaccines.can_setup(request.user):
        raise PermissionDenied("Setting up vaccines needs medical.write_records.")
    lang = _lang(request)
    words = _words(lang)
    error, editing, post = "", None, None
    if request.method == "POST":
        post = request.POST
        vaccine = get_object_or_404(vaccines.scoped_vaccines(), pk=_id(post.get("vaccine"))) if post.get("vaccine") else None
        editing = vaccine.pk if vaccine is not None else None
        item_id = _id(post.get("item")) if post.get("item") else None
        try:
            vaccines.save_vaccine({"name_ar": post.get("name_ar"), "name_en": post.get("name_en"), "species": post.get("species"),
                                   "interval_days": post.get("interval_days"), "item": Item.objects.filter(pk=item_id).first() if item_id else None,
                                   **({"active": post.get("active") == "1"} if vaccine is not None else {})}, request.user, vaccine, lang)
        except ValidationError as exc:
            error = _error(exc)
        else:
            messages.success(request, words["setup_saved"])
            return redirect(f"{reverse('medical:vaccine_setup')}?lang={lang}")
    rows = list(vaccines.scoped_vaccines().select_related("item"))
    for row in rows:
        row.species_label = vet.SPECIES_LABELS[lang].get(row.species, words["any_species"]) if row.species else words["any_species"]
        row.interval_label = interval_text(row, lang)
        row.doses_label = words["doses_n"].format(n=row.vaccinations.count()) if row.vaccinations.exists() else ""
        row.bound = post is not None and editing == row.pk  # the form that was posted shows what was typed
    return render(request, "medical/vaccine_setup.html", _base(
        lang, page_title=words["setup_title"], vx=words, **_tab_context(request, "vaccine_setup"), rows=rows, items=_items(), species=_species(lang), error=error, post=post,
        new_bound=post is not None and editing is None))


def _species(lang):
    from .models import Species

    return [(value, vet.SPECIES_LABELS[lang][value]) for value in Species.values]


# ---- recording a dose ------------------------------------------------------------------


def record(request, pk):
    _guard_vet(request, write=True)
    lang = _lang(request)
    words = _words(lang)
    animal = _animal(pk)
    choices = list(vaccines.vaccines_for(animal))
    for vaccine in choices:
        vaccine.interval_label = interval_text(vaccine, lang)
    visits = list(animal.visits.order_by("-visit_date", "-pk")[:15])
    own = getattr(request.user, "employee", None)
    error, post = "", None
    if request.method == "POST":
        post = request.POST
        vaccine = get_object_or_404(vaccines.scoped_vaccines(), pk=_id(post.get("vaccine"))) if post.get("vaccine") else None  # another entity's: 404
        visit = get_object_or_404(animal.visits, pk=_id(post.get("visit"))) if post.get("visit") else None
        doctor = _doctors().filter(pk=_id(post.get("given_by"))).first() if post.get("given_by") else None
        try:
            dose = vaccines.record(animal, {"vaccine": vaccine, "given_on": post.get("given_on") or None, "next_due": post.get("next_due") or None,
                                            "no_next": post.get("no_next") == "1", "batch": post.get("batch"), "given_by": doctor, "visit": visit,
                                            "note": post.get("note")}, request.user, lang)
        except ValidationError as exc:
            error = _error(exc)
        else:
            messages.success(request, words["saved"].format(vaccine=dose.vaccine.display(lang), animal=animal.name))
            return redirect(f"{reverse('medical:animal', args=[animal.pk])}?lang={lang}#vaccinations")
    wanted_visit = _id((post or request.GET).get("visit"))
    return render(request, "medical/vaccination_form.html", _base(
        lang, page_title=words["form_title"], vx=words, animal=animal, vaccines=choices, visits=visits, doctors=_doctors(), error=error, post=post,
        today=timezone.localdate(), default_doctor=_id(post.get("given_by")) if post else (own.pk if own is not None else 0),
        chosen_visit=wanted_visit, chosen_vaccine=_id(post.get("vaccine")) if post else _id(request.GET.get("vaccine"))))


def _doctors():
    from staff.models import Employee

    return Employee.objects.filter(active=True).order_by("name")


# ---- bill and remove -------------------------------------------------------------------


def dose_action(request, pk, action):
    """POST: «اعمل فاتورة» or «شيل» on one dose. A dose of another entity answers 404 before anything runs."""

    from django.http import HttpResponseNotAllowed

    _guard_vet(request, write=(action == "remove"))
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if action not in ("bill", "remove"):
        raise Http404("Unknown action.")
    lang = _lang(request)
    words = _words(lang)
    dose = entity_scope.get_or_404(Vaccination.objects.select_related("animal__owner", "vaccine"), "entity", pk=pk)
    here = f"{reverse('medical:animal', args=[dose.animal_id])}?lang={lang}#vaccinations"
    try:
        if action == "bill":
            invoice = vaccines.bill(dose, request.user, lang)
            messages.success(request, words["billed_ok"].format(number=invoice.invoice_number))
        else:
            vaccines.remove(dose, request.user, lang)
            messages.success(request, words["removed"])
    except ValidationError as exc:
        messages.error(request, _error(exc))
    return redirect(here)
