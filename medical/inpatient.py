"""M8 (HG-088): inpatient — wards and beds, admission and discharge, the stay's charges and its bill.

A ward holds beds; a bed is billed per day through its ``daily_service``. A patient is admitted to a free bed,
may be moved to another, collects charges (a procedure, an operation with its surgeon, a supply) and is
discharged. Nothing here moves money or stock:

* **the bill** is one ordinary draft sales invoice made at discharge through the tax-aware draft service
  (``agreements.services.draft_invoice`` → ``taxes.services.create_sales_draft_with_tax``), exactly as the
  dental steps are billed. Posting it on the sales page is what touches the patient's account, the cashbox
  and the stock of the supplies; posting code is never called from here;
* **the advance** is an S5 deposit (``deposits.services.take``) whose target is the admission: a customer
  payment on account made by that service. It is attached to the stay's draft and settles it when that draft
  posts. It has no lateness and is never forfeited from here; a cancelled admission's advance is refunded
  through the same path a cancelled booking's is (HG-068 approval, closed-month blocker).

The bed-day rule (``bed_days``): calendar days in Cairo local date; the admission day is billed, the discharge
day is not; a stay that begins and ends on the same day is one day; on the day of a move the bed the patient
sleeps in (the new one) is billed, so no day is ever billed twice.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .models import Admission, AdmissionCharge, AdmissionState, Bed, ChargeKind, Ward
from .services import WRITE, can_view, can_write, is_medical_install

CAPABILITY = "inpatient"
ZERO = Decimal("0")
ONE = Decimal("1")
#: Reception works the bed board with the booking permission, the same one the queue and appointments use.
DESK = "sales.create_sales_invoice"
#: Making the stay's draft invoice, as every clinical bill (``medical.dental.BILL``).
BILL = "sales.create_sales_invoice"
#: Taking and refunding the advance, as any collection (``deposits.services.RECEIVE``).
RECEIVE = "sales.receive_customer_payment"
PRICE_LIMIT = Decimal("10000000")
QUANTITY_LIMIT = Decimal("100000")
#: The kinds a person adds; a bed-day is written by the service only.
ADDED_KINDS = (ChargeKind.PROCEDURE, ChargeKind.OPERATION, ChargeKind.SUPPLY)

KIND_LABELS = {
    "ar": {"bed_day": "إقامة", "procedure": "خدمة", "operation": "عملية", "supply": "مستلزم"},
    "en": {"bed_day": "Bed day", "procedure": "Service", "operation": "Operation", "supply": "Supply"},
}
STATE_LABELS = {
    "ar": {"admitted": "مقيم", "discharged": "خرج", "cancelled": "اتلغى"},
    "en": {"admitted": "Admitted", "discharged": "Discharged", "cancelled": "Cancelled"},
}
MESSAGES = {
    "ar": {
        "beds": "الأسرّة محتاجة صلاحية الاستقبال (الحجز) أو صلاحية طبية.", "clinical": "ده محتاج صلاحية طبية (تسجيل الكشوفات).",
        "setup": "تجهيز العنابر والأسرّة محتاج صلاحية إدارة الملفات الطبية.", "bill": "عمل فاتورة الإقامة محتاج صلاحية إنشاء فاتورة بيع.",
        "patient": "اختار المريض.", "bed": "اختار السرير.", "bed_off": "السرير ده خارج الخدمة.", "bed_taken": "السرير {bed} عليه مريض ({number}).",
        "twice": "المريض ده مقيم بالفعل ({number}، سرير {bed}).", "future": "مينفعش الوقت يكون لسه مجاش.",
        "before": "مينفعش الوقت يكون قبل ما المريض ياخد السرير ده.", "not_open": "الإقامة دي «{state}»؛ مينفعش تتغيّر.",
        "same_bed": "المريض على السرير ده بالفعل.", "other_entity": "السرير ده في فرع تاني.",
        "kind": "اختار نوع البند.", "item": "اختار البند.", "service_item": "الخدمة والعملية بيتسجلوا على خدمة مش على صنف مخزون.",
        "stock_item": "المستلزم لازم يكون صنف ليه مخزون.", "quantity": "الكمية لازم تكون رقم أكبر من صفر.",
        "price": "السعر لازم يكون رقم صفر أو أكبر.", "day": "اليوم لازم يكون بين يوم الدخول والنهارده.",
        "billed": "البند ده اتفوتر خلاص؛ مينفعش يتشال.", "bed_day": "أيام الإقامة بتتحسب لوحدها؛ مينفعش تتشال.",
        "not_discharged": "الفاتورة بتتعمل بعد الخروج.", "nothing": "مفيش حاجة جديدة تتفوتر؛ كل بنود الإقامة على فاتورتها.",
        "location": "لازم يكون فيه مخزن بيع نشط.", "has_charges": "الإقامة دي عليها بنود؛ شيلها الأول، أو اعمل خروج.",
        "advance_closed": "المقدم بيتاخد والمريض لسه مقيم.", "advance_box": "اختار الخزنة أو الحساب البنكي اللي استلم المقدم.",
        "advance_note": "مقدم إقامة {number}", "cancel_reason": "إلغاء الإقامة {number}",
        "invoice_note": "إقامة {number}: {patient} — من {first} لـ {last}",
        "bed_line": "إقامة — {ward} / سرير {bed} — {days} يوم (من {first} لـ {last})", "bed_line_one": "إقامة — {ward} / سرير {bed} — {first}",
        "line": "{kind}: {item} — {day}", "line_surgeon": "{kind}: {item} — {surgeon} — {day}",
        "ward_name": "اكتب اسم العنبر.", "bed_code": "اكتب رقم السرير.", "bed_code_taken": "الرقم ده موجود في العنبر ده.",
        "daily_service": "اختار خدمة الإقامة اليومية (خدمة مش صنف مخزون).", "bed_busy": "السرير عليه مريض؛ مينفعش يخرج من الخدمة دلوقتي.",
    },
    "en": {
        "beds": "Beds need the reception (booking) permission or a clinical permission.", "clinical": "This needs a clinical permission (recording visits).",
        "setup": "Setting up wards and beds needs the practice-wide records permission.", "bill": "Making the stay's invoice needs the create-sales-invoice permission.",
        "patient": "Choose the patient.", "bed": "Choose the bed.", "bed_off": "This bed is out of service.", "bed_taken": "Bed {bed} is taken ({number}).",
        "twice": "This patient is already admitted ({number}, bed {bed}).", "future": "The time cannot be in the future.",
        "before": "The time cannot be before the patient took this bed.", "not_open": "This stay is “{state}”; it cannot change.",
        "same_bed": "The patient is already in this bed.", "other_entity": "This bed belongs to another branch.",
        "kind": "Choose the kind of charge.", "item": "Choose the item.", "service_item": "A service or an operation is recorded on a service, not a stock item.",
        "stock_item": "A supply must be a stock item.", "quantity": "The quantity must be a number above zero.",
        "price": "The price must be zero or more.", "day": "The day must be between the admission day and today.",
        "billed": "This charge is already on an invoice; it cannot be removed.", "bed_day": "Bed days are worked out by the system; they cannot be removed.",
        "not_discharged": "The invoice is made after discharge.", "nothing": "Nothing new to bill; every charge of this stay is on its invoice.",
        "location": "An active selling location is needed.", "has_charges": "This stay has charges; remove them first, or discharge the patient.",
        "advance_closed": "An advance is taken while the patient is still admitted.", "advance_box": "Choose the cashbox or bank account that received the advance.",
        "advance_note": "Advance for stay {number}", "cancel_reason": "Stay {number} cancelled",
        "invoice_note": "Stay {number}: {patient} — {first} to {last}",
        "bed_line": "Stay — {ward} / bed {bed} — {days} days ({first} to {last})", "bed_line_one": "Stay — {ward} / bed {bed} — {first}",
        "line": "{kind}: {item} — {day}", "line_surgeon": "{kind}: {item} — {surgeon} — {day}",
        "ward_name": "Enter the ward's name.", "bed_code": "Enter the bed's number.", "bed_code_taken": "This number is already in this ward.",
        "daily_service": "Choose the daily stay service (a service, not a stock item).", "bed_busy": "The bed is occupied; it cannot go out of service now.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _lang(lang):
    return "en" if lang == "en" else "ar"


# ---- who ---------------------------------------------------------------------------


def has_inpatient():
    """A medical business that switched «الإقامة والأسرّة» on (suggested for a medical centre; a small clinic never sees beds)."""

    from settings_core.capabilities import capability_enabled

    return is_medical_install() and capability_enabled(CAPABILITY)


def is_desk(user):
    return user_has_permission(user, DESK)


def can_see_board(user):
    """The bed board and a stay's page: clinical staff, and reception (who admits and bills). Reception never
    sees the diagnosis or a charge's note; those follow ``can_view`` (HG-084)."""

    return has_inpatient() and (can_view(user) or is_desk(user))


def can_manage_beds(user):
    """Admit, move to another bed, cancel a mistaken admission: the admissions desk or clinical staff."""

    return can_write(user) or is_desk(user)


def can_setup(user):
    """Wards, beds and their daily service are the practice's own set-up: owner and manager (``medical.write_records``)."""

    return user_has_permission(user, WRITE)


def sees_board(user):
    """For the menu."""

    return can_see_board(user)


# ---- the bed-day rule --------------------------------------------------------------


def bed_days(admitted_on, since_on, until_on, closing):
    """The calendar days (Cairo dates) one bed is billed for.

    ``admitted_on``: the first day of the whole stay. ``since_on``: the day the patient took this bed (the
    admission day, or the day of the move into it). ``until_on``: the day they left it (moved on, or discharged).
    ``closing``: leaving it ends the stay (a discharge), not a move.

    Each day from ``since_on`` up to, but not including, ``until_on`` is billed to this bed: the day a patient
    arrives in a bed is theirs, the day they leave it is not (it belongs to the next bed, or to nobody on
    discharge). A stay that begins and ends on the same day is still one day, billed to the bed it ended in.
    So every day of a stay is billed exactly once, whatever the moves.
    """

    until_on = max(until_on, since_on)
    days = [since_on + timedelta(days=n) for n in range((until_on - since_on).days)]
    if closing and until_on == admitted_on:
        days = [admitted_on]
    return days


def _local_day(moment):
    return timezone.localtime(moment).date()


def _written(day):
    """A date inside a sentence, as it goes on the invoice: 2026/11/09. With slashes the whole date stays one
    left-to-right number in an Arabic line; a hyphenated ISO date is shown back to front there (09-11-2026)."""

    return f"{day:%Y/%m/%d}"


def _bed_day_price(bed):
    return money_round(bed.daily_service.default_sale_price or ZERO)


def pending_bed_days(admission, until=None):
    """The bed-days a discharge at ``until`` (now) would write for the bed the patient is in: (day, price), not saved."""

    if admission.state != AdmissionState.ADMITTED:
        return []
    days = bed_days(_local_day(admission.admitted_at), _local_day(admission.bed_since), _local_day(until or timezone.now()), closing=True)
    written = set(AdmissionCharge.objects.filter(admission=admission, kind=ChargeKind.BED_DAY, day__in=days).values_list("day", flat=True))
    price = _bed_day_price(admission.bed)
    return [(day, price) for day in days if day not in written]


def _write_bed_days(admission, until, closing, user):
    """Write the bed-days of the bed the patient is leaving. A day already written is never written again
    (the table also refuses two bed-days on one day of one stay)."""

    days = bed_days(_local_day(admission.admitted_at), _local_day(admission.bed_since), _local_day(until), closing)
    written = set(AdmissionCharge.objects.filter(admission=admission, kind=ChargeKind.BED_DAY, day__in=days).values_list("day", flat=True))
    bed = admission.bed
    price = _bed_day_price(bed)
    rows = [AdmissionCharge.objects.create(admission=admission, kind=ChargeKind.BED_DAY, item=bed.daily_service, quantity=ONE, price=price, day=day,
                                           bed=bed, created_by=user) for day in days if day not in written]
    return rows


# ---- reading -----------------------------------------------------------------------


def scoped_wards(queryset=None):
    """The wards of the entity being worked in (all of them in the whole-group view), as every scoped record (HG-074)."""

    return entity_scope.scope((queryset if queryset is not None else Ward.objects).all(), "entity")


def scoped_beds(queryset=None):
    return entity_scope.scope((queryset if queryset is not None else Bed.objects).all(), "ward__entity")


def scoped_admissions(queryset=None):
    return entity_scope.scope((queryset if queryset is not None else Admission.objects).all(), "entity")


def patients():
    """Who can be admitted: a named, active customer who is not an insurance company."""

    from master_data.models import Customer

    return Customer.objects.exclude(customer_code="WALK-IN").filter(active=True, insurance_contract__isnull=True)


def stay_day(admission, today=None):
    """«اليوم N من الإقامة»: the admission day is day 1."""

    last = _local_day(admission.discharged_at) if admission.discharged_at else (today or timezone.localdate())
    return max((last - _local_day(admission.admitted_at)).days, 0) + 1


def board():
    """The wards in scope, each with its beds and who is in them: [{"ward", "beds": [{"bed", "state", "admission"}], "free", "occupied"}]."""

    wards = list(scoped_wards().filter(active=True))
    beds = list(Bed.objects.filter(ward__in=wards).select_related("ward", "daily_service"))
    open_stays = {a.bed_id: a for a in Admission.objects.filter(bed__in=beds, state=AdmissionState.ADMITTED).select_related("patient", "doctor")}
    today = timezone.localdate()
    rows = []
    for ward in wards:
        cells = []
        for bed in (b for b in beds if b.ward_id == ward.pk):
            admission = open_stays.get(bed.pk)
            if admission is not None:
                admission.day_number = stay_day(admission, today)
            cells.append({"bed": bed, "admission": admission, "state": "occupied" if admission is not None else ("free" if bed.active else "out")})
        rows.append({"ward": ward, "beds": cells, "free": sum(1 for c in cells if c["state"] == "free"),
                     "occupied": sum(1 for c in cells if c["state"] == "occupied")})
    return rows


def free_beds():
    """Beds a patient can be given now, in the entity being worked in."""

    taken = Admission.objects.filter(state=AdmissionState.ADMITTED).values_list("bed_id", flat=True)
    return scoped_beds().filter(active=True, ward__active=True).exclude(pk__in=taken).select_related("ward", "daily_service")


def unbilled(admission):
    """The stay's charges that are not on a live invoice: never billed, or their invoice was cancelled."""

    return AdmissionCharge.objects.filter(admission=admission).filter(Q(invoice__isnull=True) | Q(invoice__status="cancelled"))


def invoices(admission):
    """The invoices the stay's charges are on, newest first. After a cancelled bill is made again the charges
    point at the new draft; the cancelled one stays in the audit trail (``bill_admission``)."""

    from sales.models import SalesInvoice

    return SalesInvoice.objects.filter(admission_charges__admission=admission).distinct().order_by("-pk")


def advance_of(admission):
    """The advance the stay's page shows: the held deposit, else the last one settled (``deposits.latest_for``)."""

    from deposits import services as deposits

    return deposits.latest_for(admission)


def advance_cashboxes(admission):
    """Where an advance may land: the admission's entity's active cash and bank boxes (as a booking's deposit, HG-074)."""

    from cashboxes.models import Cashbox, CashboxKind
    from entities.current import working_in
    from entities.services import main_entity

    with working_in(admission.entity or main_entity()):
        return entity_scope.cashboxes(Cashbox.objects.filter(active=True, kind__in=(CashboxKind.CASH, CashboxKind.BANK))).order_by("-is_default", "pk")


def _selling_location(admission):
    """The selling store of the admission's entity, whatever entity the biller works in (HG-074)."""

    from entities.current import working_in
    from entities.services import main_entity
    from master_data.models import Location

    with working_in(admission.entity or main_entity()):
        return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


# ---- helpers -----------------------------------------------------------------------


def _audit(user, action, admission, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="medical", action=action, object_type="medical.Admission",
                            object_id=str(admission.pk), before_data=before or {}, after_data=after)


def _locked(admission):
    # ``of=self``: PostgreSQL refuses FOR UPDATE on the nullable side of an outer join (the doctor, the entity).
    return Admission.objects.select_for_update(of=("self",)).select_related("patient", "bed__ward", "bed__daily_service", "doctor", "entity").get(pk=admission.pk)


def _open(admission, words, lang):
    if admission.state != AdmissionState.ADMITTED:
        raise ValidationError(words["not_open"].format(state=STATE_LABELS[_lang(lang)].get(admission.state, admission.state)))


def _moment(when, words, floor=None):
    now = timezone.now()
    when = when or now
    if when > now + timedelta(minutes=1):
        raise ValidationError(words["future"])
    if floor is not None and when < floor:
        raise ValidationError(words["before"])
    return when


def _number(raw, words, key, low_open, limit):
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError(words[key])
    if not value.is_finite() or value >= limit or (value <= 0 if low_open else value < 0):
        raise ValidationError(words[key])
    return value


def _free_bed(bed, words, exclude=None):
    """Lock the bed and refuse one that is out of service or taken."""

    if bed is None:
        raise ValidationError(words["bed"])
    bed = Bed.objects.select_for_update(of=("self",)).select_related("ward", "daily_service").get(pk=bed.pk)
    if not bed.active or not bed.ward.active:
        raise ValidationError(words["bed_off"])
    taken = Admission.objects.filter(bed=bed, state=AdmissionState.ADMITTED)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    other = taken.first()
    if other is not None:
        raise ValidationError(words["bed_taken"].format(bed=bed.code, number=other.number))
    return bed


# ---- wards and beds ----------------------------------------------------------------


@transaction.atomic
def save_ward(data, user, ward=None, lang="ar"):
    words = _words(lang)
    if not can_setup(user):
        raise PermissionDenied(words["setup"])
    name = (data.get("name_ar") or "").strip()[:120]
    if not name:
        raise ValidationError(words["ward_name"])
    creating = ward is None
    if creating:
        ward = Ward(entity=entity_scope.entity() or None, position=(Ward.objects.count() + 1))
    ward.name_ar, ward.name_en = name, (data.get("name_en") or "").strip()[:120]
    if "active" in data:
        ward.active = bool(data.get("active"))
    ward.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if creating else AuditEventType.UPDATE, actor=user, module="medical",
                            action="save_ward", object_type="medical.Ward", object_id=str(ward.pk), after_data={"name": ward.name_ar, "active": ward.active})
    return ward


@transaction.atomic
def save_bed(ward, data, user, bed=None, lang="ar"):
    """Add a bed to ``ward`` or change one. ``data``: code, daily_service (a service item), active."""

    words = _words(lang)
    if not can_setup(user):
        raise PermissionDenied(words["setup"])
    creating = bed is None
    code = (data.get("code") or "").strip()[:20] if (creating or "code" in data) else bed.code
    if not code:
        raise ValidationError(words["bed_code"])
    service = data.get("daily_service") if (creating or "daily_service" in data) else bed.daily_service
    if service is None or service.is_stock_tracked or not service.active:
        raise ValidationError(words["daily_service"])
    if creating:
        bed = Bed(ward=ward)
    else:
        bed = Bed.objects.select_for_update(of=("self",)).get(pk=bed.pk)
    if Bed.objects.filter(ward=bed.ward, code=code).exclude(pk=bed.pk).exists():
        raise ValidationError(words["bed_code_taken"])
    active = bool(data.get("active")) if "active" in data else bed.active
    if not active and not creating and Admission.objects.filter(bed=bed, state=AdmissionState.ADMITTED).exists():
        raise ValidationError(words["bed_busy"])
    bed.code, bed.daily_service, bed.active = code, service, active
    bed.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if creating else AuditEventType.UPDATE, actor=user, module="medical",
                            action="save_bed", object_type="medical.Bed", object_id=str(bed.pk),
                            after_data={"ward": bed.ward_id, "code": bed.code, "daily_service": service.item_code, "active": bed.active})
    return bed


# ---- admit, move, cancel -----------------------------------------------------------


@transaction.atomic
def admit(patient, bed, user, *, doctor=None, diagnosis="", when=None, advance=None, cashbox=None, lang="ar"):
    """Admit ``patient`` to a free ``bed``. One open admission per patient and per bed.

    ``advance`` (optional) is taken as a deposit in the same transaction: if it is refused, nobody is admitted.
    The diagnosis is clinical: it is kept only when the user may write clinical records."""

    from config.numbering import next_in_series
    from master_data.models import Customer

    from .services import file_for

    words = _words(lang)
    if not can_manage_beds(user):
        raise PermissionDenied(words["beds"])
    if patient is None or not patients().filter(pk=patient.pk).exists():
        raise ValidationError(words["patient"])
    Customer.objects.select_for_update().filter(pk=patient.pk).exists()  # two clicks at once admit the patient once
    bed = _free_bed(bed, words)
    when = _moment(when, words)
    current = Admission.objects.filter(patient=patient, state=AdmissionState.ADMITTED).select_related("bed").first()
    if current is not None:
        raise ValidationError(words["twice"].format(number=current.number, bed=current.bed.code))
    values = {"patient": patient, "bed": bed, "bed_since": when, "admitted_at": when, "doctor": doctor, "entity": bed.ward.entity,
              "diagnosis": (diagnosis or "").strip()[:5000] if can_write(user) else "", "created_by": user}
    admission = None
    for attempt in range(5):
        number = next_in_series(Admission, "number", "ADM-")
        try:
            with transaction.atomic():
                admission = Admission.objects.create(number=number, **values)
            break
        except IntegrityError:  # a number taken at the same moment is retried; a patient or a bed taken meanwhile is refused above next time
            if attempt == 4 or not Admission.objects.filter(number=number).exists():
                raise
    file_for(patient)  # the patient's file opens on first use, as with a visit
    _audit(user, "admit_patient", admission, {"number": admission.number, "patient": patient.pk, "bed": bed.pk, "bed_code": bed.code,
                                              "doctor": doctor.pk if doctor else None, "admitted_at": when.isoformat(), "has_diagnosis": bool(admission.diagnosis)},
           event=AuditEventType.CREATE)
    if advance not in (None, ""):
        take_advance(admission, cashbox, advance, user, lang)
    return admission


@transaction.atomic
def take_advance(admission, cashbox, amount, user, lang="ar"):
    """The advance on a stay: an S5 deposit on the admission (a customer payment on account, made by that service)."""

    from deposits import services as deposits

    words = _words(lang)
    admission = _locked(admission)
    if admission.state != AdmissionState.ADMITTED:
        raise ValidationError(words["advance_closed"])
    if cashbox is None or not advance_cashboxes(admission).filter(pk=cashbox.pk).exists():
        raise ValidationError(words["advance_box"])
    return deposits.take(admission, admission.patient, cashbox, amount, user, _lang(lang), notes=words["advance_note"].format(number=admission.number))


@transaction.atomic
def set_diagnosis(admission, text, user, lang="ar"):
    words = _words(lang)
    if not can_write(user):
        raise PermissionDenied(words["clinical"])
    admission = _locked(admission)
    admission.diagnosis = (text or "").strip()[:5000]
    admission.save(update_fields=["diagnosis"])
    _audit(user, "set_admission_diagnosis", admission, {"has_diagnosis": bool(admission.diagnosis)})
    return admission


@transaction.atomic
def move(admission, bed, user, *, when=None, lang="ar"):
    """Move the patient to another free bed. The days spent in the old bed are written now, at its price; the day
    of the move goes to the new bed."""

    words = _words(lang)
    if not can_manage_beds(user):
        raise PermissionDenied(words["beds"])
    admission = _locked(admission)
    _open(admission, words, lang)
    if bed is not None and bed.pk == admission.bed_id:
        raise ValidationError(words["same_bed"])
    bed = _free_bed(bed, words, exclude=admission)
    if bed.ward.entity_id != admission.entity_id:
        raise ValidationError(words["other_entity"])
    when = _moment(when, words, floor=admission.bed_since)
    written = _write_bed_days(admission, when, closing=False, user=user)
    before = {"bed": admission.bed_id, "bed_code": admission.bed.code, "bed_since": admission.bed_since.isoformat()}
    admission.bed, admission.bed_since = bed, when
    admission.save(update_fields=["bed", "bed_since"])
    _audit(user, "move_bed", admission, {"bed": bed.pk, "bed_code": bed.code, "moved_at": when.isoformat(),
                                         "bed_days_written": [row.day.isoformat() for row in written]}, before)
    return admission


def _refund_advance(admission, user, lang):
    """A cancelled admission's advance follows the booking's refund path (``appointments.cancel_with_deposit_policy``):
    the collection permission, the closed-month blocker, HG-068's second person, then ``deposits.refund``.
    Never a forfeit: an admission has no lateness (``deposits.policy`` answers "refund" for it)."""

    from approvals.services import hold
    from deposits import services as deposits

    deposit = deposits.held_for(admission)
    result = {"deposit": deposit, "outcome": None, "detail": ""}
    if deposit is None:
        return result
    reason = _words(lang)["cancel_reason"].format(number=admission.number)
    if not user_has_permission(user, RECEIVE):
        result["outcome"] = "refund_not_allowed"
        return result
    blocker = deposits.refund_blocker(deposit, _lang(lang))
    if blocker:
        result.update(outcome="refund_blocked", detail=blocker)
        return result
    if hold(user, "sales.cancel_payment", deposit.payment_id, {"reason": reason}, deposit.payment.payment_number) is not None:  # HG-068
        result["outcome"] = "refund_pending"
        return result
    deposits.refund(deposit, user, reason, _lang(lang))
    result["outcome"] = "refunded"
    return result


@transaction.atomic
def cancel(admission, user, lang="ar"):
    """Undo an admission entered by mistake: it has no charges yet. The bed is free again and the advance, if one
    is held, is refunded through the existing path. Returns {"admission", "deposit", "outcome", "detail"}."""

    words = _words(lang)
    if not can_manage_beds(user):
        raise PermissionDenied(words["beds"])
    admission = _locked(admission)
    _open(admission, words, lang)
    if admission.charges.exists():
        raise ValidationError(words["has_charges"])
    admission.state, admission.discharged_at = AdmissionState.CANCELLED, timezone.now()
    admission.save(update_fields=["state", "discharged_at"])
    _audit(user, "cancel_admission", admission, {"state": AdmissionState.CANCELLED}, {"state": AdmissionState.ADMITTED, "bed": admission.bed_id})
    return {"admission": admission, **_refund_advance(admission, user, lang)}


@transaction.atomic
def refund_advance(admission, user, lang="ar"):
    """The advance still held on a cancelled admission (its refund was refused or waited), tried again."""

    admission = _locked(admission)
    if admission.state != AdmissionState.CANCELLED:
        raise ValidationError(_words(lang)["not_open"].format(state=STATE_LABELS[_lang(lang)].get(admission.state, admission.state)))
    return _refund_advance(admission, user, lang)


# ---- charges -----------------------------------------------------------------------


@transaction.atomic
def add_charge(admission, data, user, lang="ar"):
    """Add a charge to an open stay. ``data``: kind (procedure / operation / supply), item, quantity (default 1),
    price (default the item's), day (default today), note, surgeon (an operation's)."""

    words = _words(lang)
    if not can_write(user):
        raise PermissionDenied(words["clinical"])
    admission = _locked(admission)
    _open(admission, words, lang)
    kind = data.get("kind")
    if kind not in ADDED_KINDS:
        raise ValidationError(words["kind"])
    item = data.get("item")
    if item is None or not item.active:
        raise ValidationError(words["item"])
    if kind == ChargeKind.SUPPLY and not item.is_stock_tracked:
        raise ValidationError(words["stock_item"])
    if kind != ChargeKind.SUPPLY and item.is_stock_tracked:
        raise ValidationError(words["service_item"])
    quantity = _number(data.get("quantity") if data.get("quantity") not in (None, "") else "1", words, "quantity", True, QUANTITY_LIMIT).quantize(Decimal("0.001"))
    if quantity <= 0:
        raise ValidationError(words["quantity"])
    price = money_round(_number(data.get("price") if data.get("price") not in (None, "") else (item.default_sale_price or ZERO), words, "price", False, PRICE_LIMIT))
    day = data.get("day") or timezone.localdate()
    if not _local_day(admission.admitted_at) <= day <= timezone.localdate():
        raise ValidationError(words["day"])
    charge = AdmissionCharge.objects.create(admission=admission, kind=kind, item=item, quantity=quantity, price=price, day=day,
                                            surgeon=data.get("surgeon") if kind == ChargeKind.OPERATION else None,
                                            note=(data.get("note") or "").strip()[:255], created_by=user)
    _audit(user, "add_admission_charge", admission, {"charge": charge.pk, "kind": kind, "item": item.item_code, "quantity": str(quantity),
                                                     "price": str(price), "day": day.isoformat(), "surgeon": charge.surgeon_id}, event=AuditEventType.CREATE)
    return charge


@transaction.atomic
def remove_charge(charge, user, lang="ar"):
    """Remove a charge that is not billed yet. A charge on a live invoice is frozen; a bed-day is never removed."""

    words = _words(lang)
    if not can_write(user):
        raise PermissionDenied(words["clinical"])
    admission = _locked(charge.admission)
    charge = AdmissionCharge.objects.select_for_update(of=("self",)).select_related("invoice", "item").get(pk=charge.pk)
    if charge.kind == ChargeKind.BED_DAY:
        raise ValidationError(words["bed_day"])
    if charge.invoice_id is not None and charge.invoice.status != "cancelled":
        raise ValidationError(words["billed"])
    if admission.state == AdmissionState.CANCELLED:
        raise ValidationError(words["not_open"].format(state=STATE_LABELS[_lang(lang)]["cancelled"]))
    before = {"charge": charge.pk, "kind": charge.kind, "item": charge.item.item_code, "quantity": str(charge.quantity), "price": str(charge.price),
              "day": charge.day.isoformat()}
    charge.delete()
    _audit(user, "remove_admission_charge", admission, {"charge": before["charge"], "removed": True}, before, event=AuditEventType.DELETE)
    return admission


# ---- discharge and the bill --------------------------------------------------------


@transaction.atomic
def discharge(admission, user, *, when=None, lang="ar"):
    """Close the stay: write the bed-days of the bed the patient is in and free it. Billing is ``bill``."""

    words = _words(lang)
    if not can_write(user):
        raise PermissionDenied(words["clinical"])
    admission = _locked(admission)
    _open(admission, words, lang)
    when = _moment(when, words, floor=admission.bed_since)
    written = _write_bed_days(admission, when, closing=True, user=user)
    admission.state, admission.discharged_at = AdmissionState.DISCHARGED, when
    admission.save(update_fields=["state", "discharged_at"])
    _audit(user, "discharge_patient", admission, {"state": AdmissionState.DISCHARGED, "discharged_at": when.isoformat(),
                                                  "bed_days_written": [row.day.isoformat() for row in written],
                                                  "bed_days": admission.charges.filter(kind=ChargeKind.BED_DAY).count()},
           {"state": AdmissionState.ADMITTED, "bed": admission.bed_id})
    return admission


def _rows(charges, lang):
    """The invoice lines for ``charges`` (saved or previewed): bed-days first, the days in a row in one bed at one
    price on one line; then each other charge on its own line. A charge's note never goes on the invoice."""

    words, labels = _words(lang), KIND_LABELS[_lang(lang)]
    rows, run = [], []

    def close_run():
        if not run:
            return
        first, last, bed = run[0], run[-1], run[0].bed
        ward = bed.ward.display(_lang(lang)) if bed is not None else ""
        code = bed.code if bed is not None else "—"
        text = (words["bed_line_one"].format(ward=ward, bed=code, first=_written(first.day)) if len(run) == 1 else
                words["bed_line"].format(ward=ward, bed=code, days=len(run), first=_written(first.day), last=_written(last.day)))
        rows.append({"item": first.item, "quantity": Decimal(len(run)), "unit_sale_price": first.price, "line_discount_amount": ZERO, "description": text[:255]})
        run.clear()

    for charge in sorted((c for c in charges if c.kind == ChargeKind.BED_DAY), key=lambda c: c.day):
        if run and (charge.bed_id != run[-1].bed_id or charge.item_id != run[-1].item_id or charge.price != run[-1].price
                    or charge.day != run[-1].day + timedelta(days=1)):
            close_run()
        run.append(charge)
    close_run()
    for charge in sorted((c for c in charges if c.kind != ChargeKind.BED_DAY), key=lambda c: (c.day, c.pk or 0)):
        key = "line_surgeon" if charge.surgeon_id else "line"
        text = words[key].format(kind=labels[charge.kind], item=charge.item.item_name, day=_written(charge.day),
                                 surgeon=charge.surgeon.name if charge.surgeon_id else "")
        rows.append({"item": charge.item, "quantity": charge.quantity, "unit_sale_price": charge.price, "line_discount_amount": ZERO, "description": text[:255]})
    return rows


def _for_rows(queryset):
    return queryset.select_related("item", "bed__ward", "surgeon")


@transaction.atomic
def bill(admission, user, lang="ar"):
    """One draft sales invoice for the discharged stay's charges that are not on a live invoice. Returns the invoice.

    Run again, it finds nothing and raises (a charge is billed once). If the invoice was cancelled, its charges
    are billed again on a new draft. The held advance is pointed at the draft, so posting it applies the advance."""

    from agreements.services import draft_invoice
    from deposits.services import attach

    words = _words(lang)
    if not user_has_permission(user, BILL):
        raise PermissionDenied(words["bill"])
    admission = _locked(admission)  # two clicks at once never bill a charge twice
    if admission.state != AdmissionState.DISCHARGED:
        raise ValidationError(words["not_discharged"])
    charges = list(_for_rows(unbilled(admission).select_for_update(of=("self",))))
    if not charges:
        raise ValidationError(words["nothing"])
    location = _selling_location(admission)
    if location is None:
        raise ValidationError(words["location"])
    notes = words["invoice_note"].format(number=admission.number, patient=admission.patient.name, first=_written(_local_day(admission.admitted_at)),
                                         last=_written(_local_day(admission.discharged_at)))
    invoice = draft_invoice(admission.patient, _rows(charges, lang), notes, user, location)
    AdmissionCharge.objects.filter(pk__in=[charge.pk for charge in charges]).update(invoice=invoice)
    attach(admission, invoice)  # S5 (HG-069): the held advance settles this invoice when it posts
    _audit(user, "bill_admission", admission, {"invoice": invoice.invoice_number, "charges": [charge.pk for charge in charges],
                                               "subtotal": str(invoice.subtotal), "tax": str(invoice.tax_amount), "total": str(invoice.total_amount)})
    return invoice


@transaction.atomic
def discharge_and_bill(admission, user, *, when=None, lang="ar"):
    """The one button for someone who may do both: discharge, then the draft invoice, in one transaction."""

    admission = discharge(admission, user, when=when, lang=lang)
    return admission, bill(admission, user, lang)


def review(admission, lang="ar", until=None):
    """What a discharge now would bill, before anything is saved: the lines (written charges plus the bed-days still
    to be written), the subtotal, the tax as the draft service will compute it, the total and the advance held."""

    from deposits import services as deposits
    from taxes.services import compute_lines, vat_enabled

    charges = list(_for_rows(unbilled(admission)))
    pending = [AdmissionCharge(admission=admission, kind=ChargeKind.BED_DAY, item=admission.bed.daily_service, quantity=ONE, price=price, day=day,
                               bed=admission.bed) for day, price in pending_bed_days(admission, until)]
    rows = _rows(charges + pending, lang)
    for row in rows:
        row["total"] = money_round(row["quantity"] * row["unit_sale_price"])
    subtotal = sum((row["total"] for row in rows), ZERO)
    tax = money_round(sum((line[3] for line in compute_lines(rows)), ZERO)) if vat_enabled() and rows else ZERO
    held = deposits.held_for(admission)
    advance = held.payment.amount if held is not None else ZERO
    total = money_round(subtotal + tax)
    return {"rows": rows, "bed_days": sum(1 for c in charges + pending if c.kind == ChargeKind.BED_DAY), "subtotal": subtotal, "tax": tax, "total": total,
            "advance": advance, "after_advance": max(total - advance, ZERO), "credit_left": max(advance - total, ZERO)}
