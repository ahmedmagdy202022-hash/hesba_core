"""MED-001: who may read and write patient files, and writing them."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from . import prescribing
from .models import BloodType, ClinicalVisit, Gender, PatientFile

#: Practice-wide (owner and manager): every file, the clinic report, every doctor's follow-ups.
VIEW = "medical.view_records"
#: Practice-wide: change any visit or file, another doctor's included.
WRITE = "medical.write_records"
#: HG-084: clinical staff (the seeded «الدكتور أو الأخصائي» role): read the files and record visits.
TREAT = "medical.treat_patients"


def is_medical_install():
    """A medical installation, or (ENT-002) working in a medical entity of a group."""

    from entities.current import effective_activity

    return effective_activity()[0] == "medical"


def can_view(user):
    """HG-084: clinical records are read by those who hold a clinical permission by role: the practice-wide
    one (owner, manager) or ``medical.treat_patients`` (the doctor's role). Being linked to an employee is
    no longer enough: a receptionist or a cashier on the payroll is linked too. The employee link only
    narrows what a doctor sees as theirs (their follow-ups, their turn in the queue)."""

    return user_has_permission(user, VIEW) or user_has_permission(user, TREAT)


def can_see_report(user):
    """WATCH1B (SH-12): the reports hub offers the clinic report to those it opens for: a medical installation and the records permission."""

    return is_medical_install() and user_has_permission(user, VIEW)


def can_write(user):
    return user_has_permission(user, WRITE) or user_has_permission(user, TREAT)


def age(file, today=None):
    if not file or not file.date_of_birth:
        return None
    today = today or timezone.localdate()
    born = file.date_of_birth
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _next_number():
    last = PatientFile.objects.order_by("-pk").values_list("file_number", flat=True).first()
    try:
        n = int((last or "P-0").split("-")[-1]) + 1
    except ValueError:
        n = PatientFile.objects.count() + 1
    while PatientFile.objects.filter(file_number=f"P-{n:05d}").exists():
        n += 1
    return f"P-{n:05d}"


def file_for(customer):
    """The patient's file, opened on first use."""

    found = PatientFile.objects.filter(customer=customer).first()
    if found:
        return found
    with transaction.atomic():
        return PatientFile.objects.create(customer=customer, file_number=_next_number())


def _date(value, label, errors):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.append(label)
        return None


def _decimal(value, label, errors, low, high):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        errors.append(label)
        return None
    if not (low <= number <= high):
        errors.append(label)
        return None
    return number


MESSAGES = {
    "ar": {"date_of_birth": "تاريخ الميلاد مش صحيح.", "future_birth": "تاريخ الميلاد مينفعش يكون في المستقبل.",
           "visit_date": "تاريخ الكشف مش صحيح.", "follow_up_date": "تاريخ المتابعة مش صحيح.",
           "pulse": "النبض لازم يكون رقم بين 20 و250.", "temperature": "الحرارة لازم تكون بين 30 و45.",
           "weight": "الوزن لازم يكون بين 0.5 و400 كجم.", "height": "الطول لازم يكون بين 20 و250 سم.",
           "empty": "اكتب الشكوى أو التشخيص أو العلاج، أو ضيف دوا، على الأقل.", "follow_before": "ميعاد المتابعة لازم يكون بعد يوم الكشف.",
           "not_yours": "الكشف ده سجّله دكتور تاني؛ يعدّله هو أو المالك."},
    "en": {"date_of_birth": "The date of birth is not valid.", "future_birth": "The date of birth cannot be in the future.",
           "visit_date": "The visit date is not valid.", "follow_up_date": "The follow-up date is not valid.",
           "pulse": "Pulse must be a number between 20 and 250.", "temperature": "Temperature must be between 30 and 45.",
           "weight": "Weight must be between 0.5 and 400 kg.", "height": "Height must be between 20 and 250 cm.",
           "empty": "Write at least the complaint, the diagnosis or the treatment, or add a medicine.", "follow_before": "The follow-up must be after the visit.",
           "not_yours": "Another doctor recorded this visit; they or the owner can change it."},
}


def _audit(user, action, obj, after):
    AuditLog.objects.create(event_type=AuditEventType.CREATE if action.startswith("create") else AuditEventType.UPDATE, actor=user,
                            module="medical", action=action, object_type=f"medical.{type(obj).__name__}", object_id=str(obj.pk),
                            after_data=after)


def save_profile(file, data, user, lang="ar"):
    words, errors = MESSAGES[lang], []
    born = _date(data.get("date_of_birth"), words["date_of_birth"], errors)
    if born and born > timezone.localdate():
        errors.append(words["future_birth"])
    if errors:
        raise ValidationError(errors)
    file.date_of_birth = born
    file.gender = data.get("gender") if data.get("gender") in Gender.values else ""
    file.blood_type = data.get("blood_type") if data.get("blood_type") in BloodType.values else ""
    for field in ("allergies", "chronic_conditions", "current_medications", "emergency_contact", "notes"):
        setattr(file, field, (data.get(field) or "").strip()[:255 if field == "emergency_contact" else 5000])
    file.save()
    _audit(user, "update_patient_file", file, {"file_number": file.file_number})
    return file


def save_visit(patient, data, user, visit=None, lang="ar"):
    words, errors = MESSAGES[lang], []
    if visit is not None and visit.created_by_id != user.pk and not user_has_permission(user, WRITE):
        raise ValidationError(words["not_yours"])
    visit_date = _date(data.get("visit_date"), words["visit_date"], errors) or timezone.localdate()
    follow = _date(data.get("follow_up_date"), words["follow_up_date"], errors)
    if follow and follow <= visit_date:
        errors.append(words["follow_before"])
    vet = bool(data.get("vet"))  # MED-006: a budgie weighs 40 g, a horse 500 kg, a rabbit's pulse runs past 250
    pulse = _decimal(data.get("pulse"), words["pulse"], errors, *((5, 1000) if vet else (20, 250)))
    numbers = {"temperature": _decimal(data.get("temperature"), words["temperature"], errors, *((25, 46) if vet else (30, 45))),
               "weight": _decimal(data.get("weight"), words["weight"], errors, *((Decimal("0.005"), 2000) if vet else (Decimal("0.5"), 400))),
               "height": _decimal(data.get("height"), words["height"], errors, *((1, 400) if vet else (20, 250)))}
    texts = {field: (data.get(field) or "").strip()[:5000] for field in ("complaint", "examination", "diagnosis", "treatment")}
    # M2 (HG-086): the medicine lines, only when the caller sends them; without the key a visit's lines stay as they are.
    medicines = prescribing.resolve(data["medicines"], visit, lang, errors) if "medicines" in data else None
    has_medicines = bool(medicines) if medicines is not None else bool(visit is not None and visit.pk and visit.medicines.exists())
    if not any(texts[f] for f in ("complaint", "diagnosis", "treatment")) and not has_medicines:
        errors.append(words["empty"])
    animal = data.get("animal")
    from .vet import MESSAGES as VET_MESSAGES

    vet_words = VET_MESSAGES["en" if lang == "en" else "ar"]
    if animal is not None and animal.owner_id != patient.pk:  # MED-006: a vet visit is for one of the owner's own animals
        errors.append(vet_words["not_theirs"])
    elif vet and animal is None:  # a vet visit always names its animal, so it shows on the animal's card
        errors.append(vet_words["choose_animal"] if patient.animals.exists() else vet_words["no_animal"])
    if errors:
        raise ValidationError(errors)
    visit = visit or ClinicalVisit(patient=patient, created_by=user)
    visit.visit_date, visit.follow_up_date = visit_date, follow
    visit.pulse = int(pulse) if pulse is not None else None
    visit.blood_pressure = (data.get("blood_pressure") or "").strip()[:15]
    for field, value in {**numbers, **texts}.items():
        setattr(visit, field, value)
    if data.get("doctor") is not None:
        visit.doctor = data.get("doctor")
    if "animal" in data:
        visit.animal = animal
    if data.get("appointment") is not None and visit.appointment_id is None:
        visit.appointment = data.get("appointment")
    creating = visit.pk is None
    with transaction.atomic():  # the visit, its medicine lines and the audit row land together or not at all
        visit.save()
        if medicines is not None:
            prescribing.write(visit, medicines)
        file_for(patient)
        after = {"patient": patient.pk, "animal": visit.animal_id, "visit_date": visit.visit_date.isoformat(), "diagnosis": visit.diagnosis[:200]}
        if medicines is not None:
            after["medicines"] = len(medicines)
        _audit(user, "create_clinical_visit" if creating else "update_clinical_visit", visit, after)
    return visit


def shows_files(user):
    """Whether other screens should link to patient files for this user."""

    return is_medical_install() and can_view(user)
