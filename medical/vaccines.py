"""M7 (HG-090): the vet's vaccines, the doses given to an animal, their due dates and the optional draft bill.

A vaccine is a practice set-up record (owner and manager). A dose is a clinical record (anyone who may write patient
records) that names the animal, the vaccine and the day, and works out when the next dose is expected. Nothing here
moves money or stock:

* **the bill** is one ordinary draft sales invoice in the owner's name, made on request through the same tax-aware draft
  service the dental steps and the stay use (``agreements.services.draft_invoice``). Posting it on the sales page is what
  touches the owner's account and the stock of a vaccine item; posting code is never called from here;
* **the due list** and **the reminder** only read doses.

The due date rule (``due_after``): an interval that is a whole number of years (365, 730, ...) lands on the same calendar
day that many years on (29 Feb clamps to 28 Feb), so «سنوي» never drifts by a day in a leap year; any other interval adds
that many days.

A dose is *superseded* when a later dose of the same vaccine was given to the same animal (a later day, or the same day
and a later row). It is worked out when read (``open_doses``) and never stored, so a dose recorded by mistake and then
removed brings the earlier due date back and nothing has to be undone.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission

from . import vet
from .models import Animal, ClinicalVisit, Vaccination, Vaccine
from .services import WRITE, can_view, can_write

ZERO = Decimal("0")
#: Making the draft invoice, as every clinical bill (``medical.dental.BILL``, ``medical.inpatient.BILL``).
BILL = "sales.create_sales_invoice"
#: The longest interval a vaccine may carry (10 years of days).
MAX_INTERVAL = 3650
#: The due list looks this many days ahead (its «الشهر ده» filter); the week filter is a part of it.
WINDOW_DAYS = 30
WEEK_DAYS = 7

MESSAGES = {
    "ar": {
        "setup": "تجهيز التطعيمات محتاج صلاحية إدارة الملفات الطبية.", "clinical": "تسجيل التطعيم محتاج صلاحية طبية (تسجيل الكشوفات).",
        "bill": "عمل فاتورة التطعيم محتاج صلاحية إنشاء فاتورة بيع.", "read": "التطعيمات للي معاه صلاحية طبية بس.", "vet_only": "التطعيمات للعيادات البيطرية بس.",
        "name": "اكتب اسم التطعيم.", "species": "نوع الحيوان مش معروف.", "interval": "المدة لحد الجرعة الجاية لازم تكون رقم من 1 لـ {max} يوم، أو سيبها فاضية لو مرة واحدة.",
        "item": "اختار صنف أو خدمة فعّالة للفاتورة، أو سيبها فاضية.", "vaccine": "اختار التطعيم.", "vaccine_off": "التطعيم ده موقوف.",
        "wrong_species": "التطعيم ده لـ {species}؛ الحيوان ده {animal_species}.", "animal": "اختار الحيوان.",
        "date": "تاريخ التطعيم مش مظبوط.", "future": "تاريخ التطعيم لسه مجاش.", "old": "تاريخ التطعيم قديم أوي.",
        "due": "ميعاد الجرعة الجاية مش مظبوط.", "due_before": "ميعاد الجرعة الجاية لازم يكون بعد يوم التطعيم.",
        "visit_other": "الكشف ده مش بتاع الحيوان ده.", "given_by": "الدكتور ده مش فعّال.",
        "twice": "التطعيم ده اتسجل للحيوان ده في نفس اليوم.", "not_yours": "التطعيم ده سجّله دكتور تاني؛ يشيله هو أو المالك.",
        "billed": "التطعيم ده على فاتورة؛ مينفعش يتشال. الغي الفاتورة الأول.", "no_item": "التطعيم ده مالوش صنف للفاتورة؛ حدد له صنف من «أنواع التطعيمات».",
        "item_off": "صنف التطعيم ده موقوف.", "already": "التطعيم ده اتفوتر خلاص ({number}).", "location": "لازم يكون فيه مخزن بيع نشط.",
        "invoice_note": "تطعيم {animal} — {day}", "invoice_line": "{vaccine} — {animal} — {day}", "removed_audit": "اتشال تطعيم مسجّل بالغلط.",
    },
    "en": {
        "setup": "Setting up vaccines needs the practice-wide records permission.", "clinical": "Recording a vaccination needs a clinical permission (recording visits).",
        "bill": "Making the vaccination invoice needs the create-sales-invoice permission.", "read": "Vaccinations are for those with a clinical permission.",
        "vet_only": "Vaccinations are for vet clinics.",
        "name": "Enter the vaccine's name.", "species": "The species is not known.", "interval": "Days until the next dose must be a number from 1 to {max}, or empty for a one-off.",
        "item": "Choose an active item or service for the invoice, or leave it empty.", "vaccine": "Choose the vaccine.", "vaccine_off": "This vaccine is switched off.",
        "wrong_species": "This vaccine is for {species}; this animal is {animal_species}.", "animal": "Choose the animal.",
        "date": "The vaccination date is not valid.", "future": "The vaccination date is still to come.", "old": "The vaccination date is too far back.",
        "due": "The next-dose date is not valid.", "due_before": "The next dose must be after the day it was given.",
        "visit_other": "This visit is not this animal's.", "given_by": "This doctor is not active.",
        "twice": "This vaccine was already recorded for this animal on the same day.", "not_yours": "Another doctor recorded this vaccination; they or the owner can remove it.",
        "billed": "This vaccination is on an invoice; it cannot be removed. Cancel the invoice first.", "no_item": "This vaccine has no item to bill; set one under Vaccines.",
        "item_off": "This vaccine's item is switched off.", "already": "This vaccination is already billed ({number}).", "location": "An active selling location is needed.",
        "invoice_note": "Vaccination {animal} — {day}", "invoice_line": "{vaccine} — {animal} — {day}", "removed_audit": "A vaccination recorded by mistake was removed.",
    },
}


def _lang(lang):
    return "en" if lang == "en" else "ar"


def _words(lang):
    return MESSAGES[_lang(lang)]


# ---- who ---------------------------------------------------------------------------


def is_vet():
    """A vet installation, or a vet entity of a group (``vet.is_vet`` follows the entity being worked in)."""

    return vet.is_vet()


def can_setup(user):
    """Vaccines are the practice's own set-up: owner and manager (``medical.write_records``)."""

    return user_has_permission(user, WRITE)


def can_bill(user):
    return user_has_permission(user, BILL)


def shows(user):
    """The vaccination screens and tab: a vet installation, for those who may read patient records (HG-084)."""

    return is_vet() and can_view(user)


# ---- reading -----------------------------------------------------------------------


def scoped_vaccines(queryset=None):
    """The vaccines of the entity being worked in (all of them in the whole-group view), as every scoped record (HG-074)."""

    return entity_scope.scope((queryset if queryset is not None else Vaccine.objects).all(), "entity")


def scoped_vaccinations(queryset=None):
    return entity_scope.scope((queryset if queryset is not None else Vaccination.objects).all(), "entity")


def vaccines_for(animal):
    """The active vaccines this animal may be given: its species' own, and those for any species."""

    return scoped_vaccines().filter(active=True).filter(Q(species="") | Q(species=animal.species))


def _later_dose():
    return Vaccination.objects.filter(animal=OuterRef("animal"), vaccine=OuterRef("vaccine")).filter(
        Q(given_on__gt=OuterRef("given_on")) | Q(given_on=OuterRef("given_on"), pk__gt=OuterRef("pk")))


def open_doses(queryset=None):
    """The doses no later dose has closed: the last dose of each vaccine of each animal. The one query the due list, the
    animal card and the reminder all use. A later dose in another entity counts too: the animal belongs to the group."""

    return (queryset if queryset is not None else Vaccination.objects).all().annotate(_closed=Exists(_later_dose())).filter(_closed=False)


def superseded(vaccination):
    return Vaccination.objects.filter(pk=vaccination.pk).annotate(_closed=Exists(_later_dose())).filter(_closed=True).exists()


def due_after(given_on, interval_days):
    """The day the next dose is due: ``None`` for a one-off; whole years on the same calendar day; otherwise plain days."""

    if not interval_days:
        return None
    if interval_days % 365 == 0:
        years = interval_days // 365
        try:
            return given_on.replace(year=given_on.year + years)
        except ValueError:  # 29 February in a year that has none
            return given_on.replace(year=given_on.year + years, day=28)
    return given_on + timedelta(days=interval_days)


def written(day):
    """A date inside a sentence, as it goes on the invoice: 2026/11/09 (one left-to-right run in an Arabic line)."""

    return f"{day:%Y/%m/%d}"


def _date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


# ---- vaccines (set-up) ---------------------------------------------------------------


@transaction.atomic
def save_vaccine(data, user, vaccine=None, lang="ar"):
    """Add a vaccine or change one. ``data``: name_ar, name_en, species, interval_days, item (an active item or none), active."""

    words = _words(lang)
    if not can_setup(user):
        raise PermissionDenied(words["setup"])
    if not is_vet():
        raise ValidationError(words["vet_only"])
    name = (data.get("name_ar") or "").strip()[:120]
    if not name:
        raise ValidationError(words["name"])
    species = (data.get("species") or "").strip()
    if species and species not in vet.SPECIES_LABELS["en"]:
        raise ValidationError(words["species"])
    raw = str(data.get("interval_days") if data.get("interval_days") is not None else "").strip()
    interval = None
    if raw:
        if not raw.isdecimal() or not 1 <= int(raw) <= MAX_INTERVAL:
            raise ValidationError(words["interval"].format(max=MAX_INTERVAL))
        interval = int(raw)
    item = data.get("item")
    if item is not None and not item.active:
        raise ValidationError(words["item"])
    creating = vaccine is None
    if creating:
        vaccine = Vaccine(entity=entity_scope.entity() or None, created_by=user)
    else:
        vaccine = Vaccine.objects.select_for_update().get(pk=vaccine.pk)
    vaccine.name_ar, vaccine.name_en = name, (data.get("name_en") or "").strip()[:120]
    vaccine.species, vaccine.interval_days, vaccine.item = species, interval, item
    if "active" in data:
        vaccine.active = bool(data.get("active"))
    vaccine.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if creating else AuditEventType.UPDATE, actor=user, module="medical", action="save_vaccine",
                            object_type="medical.Vaccine", object_id=str(vaccine.pk),
                            after_data={"name": vaccine.name_ar, "species": species, "interval_days": interval,
                                        "item": item.item_code if item is not None else "", "active": vaccine.active})
    return vaccine


# ---- recording a dose ----------------------------------------------------------------


@transaction.atomic
def record(animal, data, user, lang="ar"):
    """Record a dose given to ``animal``.

    ``data``: vaccine (required, active, in scope, for this species), given_on (default today, never in the future),
    next_due (typed: kept as typed; empty: worked out from the vaccine's interval), no_next (true: none, whatever the
    interval), batch, given_by (an employee), visit (one of this animal's), note (clinical)."""

    words = _words(lang)
    if not can_write(user):
        raise PermissionDenied(words["clinical"])
    if not is_vet():
        raise ValidationError(words["vet_only"])
    if animal is None:
        raise ValidationError(words["animal"])
    animal = Animal.objects.select_for_update().select_related("owner").get(pk=animal.pk)  # two clicks at once never record a dose twice
    vaccine = data.get("vaccine")
    if vaccine is None:
        raise ValidationError(words["vaccine"])
    vaccine = scoped_vaccines().filter(pk=vaccine.pk).first()
    if vaccine is None:
        raise ValidationError(words["vaccine"])
    if not vaccine.active:
        raise ValidationError(words["vaccine_off"])
    if vaccine.species and vaccine.species != animal.species:
        labels = vet.SPECIES_LABELS[_lang(lang)]
        raise ValidationError(words["wrong_species"].format(species=labels.get(vaccine.species, vaccine.species), animal_species=labels.get(animal.species, animal.species)))
    today = timezone.localdate()
    try:
        given_on = _date(data["given_on"]) if data.get("given_on") else today
    except ValueError:
        raise ValidationError(words["date"])
    if given_on > today:
        raise ValidationError(words["future"])
    if given_on < today - timedelta(days=366 * 40):
        raise ValidationError(words["old"])
    if data.get("no_next"):
        next_due = None
    elif data.get("next_due"):
        try:
            next_due = _date(data["next_due"])
        except ValueError:
            raise ValidationError(words["due"])
        if next_due <= given_on:
            raise ValidationError(words["due_before"])
    else:
        next_due = due_after(given_on, vaccine.interval_days)
    visit = data.get("visit")
    if visit is not None and visit.animal_id != animal.pk:
        raise ValidationError(words["visit_other"])
    given_by = data.get("given_by")
    if given_by is not None and not given_by.active:
        raise ValidationError(words["given_by"])
    if Vaccination.objects.filter(animal=animal, vaccine=vaccine, given_on=given_on).exists():
        raise ValidationError(words["twice"])
    dose = Vaccination.objects.create(
        animal=animal, vaccine=vaccine, given_on=given_on, next_due=next_due, batch=(data.get("batch") or "").strip()[:60], given_by=given_by, visit=visit,
        note=(data.get("note") or "").strip()[:5000], entity=entity_scope.entity() or None, created_by=user)
    AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="medical", action="record_vaccination", object_type="medical.Vaccination",
                            object_id=str(dose.pk), after_data={"animal": animal.pk, "vaccine": vaccine.pk, "given_on": given_on.isoformat(),
                                                                "next_due": next_due.isoformat() if next_due else "", "batch": dose.batch,
                                                                "visit": visit.pk if visit is not None else None, "has_note": bool(dose.note)})
    return dose


def on_live_invoice(dose):
    """Whether the dose is on an invoice that still stands (a draft or a posted one); a cancelled one frees it."""

    return dose.invoice_id is not None and dose.invoice.status != "cancelled"


@transaction.atomic
def remove(dose, user, lang="ar"):
    """Remove a dose recorded by mistake: its own recorder or the owner/manager, and only while it is on no live invoice."""

    words = _words(lang)
    if not can_write(user):
        raise PermissionDenied(words["clinical"])
    dose = Vaccination.objects.select_for_update(of=("self",)).select_related("invoice", "animal", "vaccine").get(pk=dose.pk)
    if dose.created_by_id != user.pk and not user_has_permission(user, WRITE):
        raise PermissionDenied(words["not_yours"])
    if on_live_invoice(dose):
        raise ValidationError(words["billed"])
    before = {"animal": dose.animal_id, "vaccine": dose.vaccine_id, "given_on": dose.given_on.isoformat(),
              "next_due": dose.next_due.isoformat() if dose.next_due else "", "invoice": dose.invoice.invoice_number if dose.invoice_id else ""}
    pk = dose.pk
    dose.delete()
    AuditLog.objects.create(event_type=AuditEventType.DELETE, actor=user, module="medical", action="remove_vaccination", object_type="medical.Vaccination",
                            object_id=str(pk), before_data=before, after_data={"removed": True})


# ---- billing ---------------------------------------------------------------------------


def _selling_location(dose):
    """The selling store of the dose's entity, whatever entity the biller works in (HG-074)."""

    from entities.current import working_in
    from entities.services import main_entity
    from master_data.models import Location

    with working_in(dose.entity or main_entity()):
        return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


def billable(dose):
    """Whether «اعمل فاتورة» applies: the vaccine has an item and the dose is on no live invoice."""

    return dose.vaccine.item_id is not None and not on_live_invoice(dose)


@transaction.atomic
def bill(dose, user, lang="ar"):
    """One draft sales invoice in the owner's name for one dose, at its vaccine item's price today. Returns the invoice.

    Never automatic. A dose is billed once; if its invoice was cancelled it can be billed again. The note and the batch are
    clinical and never go on the invoice. Nothing is posted: the stock of a vaccine item leaves when the draft is posted."""

    from agreements.services import draft_invoice

    words = _words(lang)
    if not can_bill(user):
        raise PermissionDenied(words["bill"])
    Vaccination.objects.select_for_update(of=("self",)).get(pk=dose.pk)  # two clicks at once never bill a dose twice
    dose = Vaccination.objects.select_related("invoice", "animal__owner", "vaccine__item", "entity").get(pk=dose.pk)
    if on_live_invoice(dose):
        raise ValidationError(words["already"].format(number=dose.invoice.invoice_number))
    item = dose.vaccine.item
    if item is None:
        raise ValidationError(words["no_item"])
    if not item.active:
        raise ValidationError(words["item_off"])
    location = _selling_location(dose)
    if location is None:
        raise ValidationError(words["location"])
    animal, day = dose.animal, written(dose.given_on)
    rows = [{"item": item, "quantity": Decimal("1"), "unit_sale_price": money_round(item.default_sale_price or ZERO), "line_discount_amount": ZERO,
             "description": words["invoice_line"].format(vaccine=dose.vaccine.display(lang), animal=animal.name, day=day)[:255]}]
    invoice = draft_invoice(animal.owner, rows, words["invoice_note"].format(animal=animal.name, day=day), user, location)
    Vaccination.objects.filter(pk=dose.pk).update(invoice=invoice)
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="medical", action="bill_vaccination", object_type="medical.Vaccination",
                            object_id=str(dose.pk), after_data={"invoice": invoice.invoice_number, "subtotal": str(invoice.subtotal),
                                                                "tax": str(invoice.tax_amount), "total": str(invoice.total_amount)})
    return invoice


# ---- the due list ---------------------------------------------------------------------------

FILTERS = ("all", "overdue", "week", "month")


def due_list(user, today=None, window="all", q="", lang="ar"):
    """Animals whose next dose is overdue or due within ``WINDOW_DAYS``, with their owner's name and phone.

    Only for those who may read patient records (``PermissionDenied`` otherwise: the owner's phone is never shown to anyone
    else). Each row is the last dose of one vaccine of one animal (``open_doses``) whose ``next_due`` is set, in the entity
    being worked in; an animal that no longer comes («لسه بييجي» off) or an owner switched off is left out.
    ``window``: all (overdue and the next 30 days), overdue, week (today to 7 days) or month (today to 30 days).
    Returns {"rows", "counts"}; ``counts`` is for the three chips and ignores ``window``."""

    words = _words(lang)
    if not can_view(user):
        raise PermissionDenied(words["read"])
    if not is_vet():
        raise ValidationError(words["vet_only"])
    from reports.aging import whatsapp_number

    today = today or timezone.localdate()
    doses = open_doses(scoped_vaccinations()).filter(next_due__isnull=False, next_due__lte=today + timedelta(days=WINDOW_DAYS), animal__active=True,
                                                      animal__owner__active=True).exclude(animal__owner__customer_code="WALK-IN")
    query = (q or "").strip()[:100]
    if query:
        doses = doses.filter(Q(animal__name__icontains=query) | Q(animal__owner__name__icontains=query) | Q(animal__owner__phone__icontains=query)
                             | Q(animal__owner__whatsapp__icontains=query) | Q(vaccine__name_ar__icontains=query) | Q(vaccine__name_en__icontains=query))
    rows = []
    for dose in doses.select_related("animal__owner", "vaccine").order_by("next_due", "animal__name", "pk")[:500]:
        owner = dose.animal.owner
        phone = (owner.whatsapp or owner.phone or "").strip()
        days = (dose.next_due - today).days
        rows.append({"dose": dose, "animal": dose.animal, "owner": owner, "vaccine": dose.vaccine, "due": dose.next_due, "days": days, "late": -days if days < 0 else 0,
                     "overdue": days < 0, "phone": phone, "call": "".join(ch for ch in (owner.phone or owner.whatsapp or "") if ch.isdigit() or ch == "+"),
                     "whatsapp": whatsapp_number(phone)})
    counts = {"overdue": sum(1 for row in rows if row["overdue"]), "week": sum(1 for row in rows if 0 <= row["days"] <= WEEK_DAYS),
              "month": sum(1 for row in rows if 0 <= row["days"] <= WINDOW_DAYS)}
    window = window if window in FILTERS else "all"
    if window == "overdue":
        rows = [row for row in rows if row["overdue"]]
    elif window == "week":
        rows = [row for row in rows if 0 <= row["days"] <= WEEK_DAYS]
    elif window == "month":
        rows = [row for row in rows if 0 <= row["days"] <= WINDOW_DAYS]
    return {"rows": rows, "counts": counts}


def card(animal, today=None):
    """The animal's vaccination history for its page, newest first: each dose with whether a later dose closed it, whether it is
    overdue (open and past its date), whether it is on a live invoice and whether it may be billed. Group-wide, like its visits."""

    today = today or timezone.localdate()
    rows = []
    for dose in animal.vaccinations.select_related("vaccine", "given_by", "invoice", "vaccine__item", "created_by"):
        closed = superseded(dose)
        rows.append({"dose": dose, "closed": closed, "overdue": not closed and dose.next_due is not None and dose.next_due < today,
                     "days": (dose.next_due - today).days if dose.next_due else None, "live": on_live_invoice(dose), "billable": billable(dose)})
    return rows
