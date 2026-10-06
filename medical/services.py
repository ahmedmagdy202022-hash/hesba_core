"""MED-001: who may read and write patient files, and writing them."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from .models import BloodType, ClinicalVisit, Gender, PatientFile

VIEW = "medical.view_records"
WRITE = "medical.write_records"


def is_medical_install():
    """A medical installation, or (ENT-002) working in a medical entity of a group."""

    from entities.current import effective_activity

    return effective_activity()[0] == "medical"


def _is_doctor(user):
    employee = getattr(user, "employee", None) if getattr(user, "is_authenticated", False) else None
    return employee is not None and employee.active


def can_view(user):
    """Owners and managers by role; a doctor (a user linked to an active
    employee) reads the files of the patients they treat."""

    return user_has_permission(user, VIEW) or _is_doctor(user)


def can_write(user):
    return user_has_permission(user, WRITE) or _is_doctor(user)


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
           "empty": "اكتب الشكوى أو التشخيص أو العلاج على الأقل.", "follow_before": "ميعاد المتابعة لازم يكون بعد يوم الكشف.",
           "not_yours": "الكشف ده سجّله دكتور تاني؛ يعدّله هو أو المالك."},
    "en": {"date_of_birth": "The date of birth is not valid.", "future_birth": "The date of birth cannot be in the future.",
           "visit_date": "The visit date is not valid.", "follow_up_date": "The follow-up date is not valid.",
           "pulse": "Pulse must be a number between 20 and 250.", "temperature": "Temperature must be between 30 and 45.",
           "weight": "Weight must be between 0.5 and 400 kg.", "height": "Height must be between 20 and 250 cm.",
           "empty": "Write at least the complaint, the diagnosis or the treatment.", "follow_before": "The follow-up must be after the visit.",
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
    pulse = _decimal(data.get("pulse"), words["pulse"], errors, 20, 250)
    numbers = {"temperature": _decimal(data.get("temperature"), words["temperature"], errors, 30, 45),
               "weight": _decimal(data.get("weight"), words["weight"], errors, Decimal("0.5"), 400),
               "height": _decimal(data.get("height"), words["height"], errors, 20, 250)}
    texts = {field: (data.get(field) or "").strip()[:5000] for field in ("complaint", "examination", "diagnosis", "treatment")}
    if not any(texts[f] for f in ("complaint", "diagnosis", "treatment")):
        errors.append(words["empty"])
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
    if data.get("appointment") is not None and visit.appointment_id is None:
        visit.appointment = data.get("appointment")
    creating = visit.pk is None
    visit.save()
    file_for(patient)
    _audit(user, "create_clinical_visit" if creating else "update_clinical_visit", visit,
           {"patient": patient.pk, "visit_date": visit.visit_date.isoformat(), "diagnosis": visit.diagnosis[:200]})
    return visit


def shows_files(user):
    """Whether other screens should link to patient files for this user."""

    return is_medical_install() and can_view(user)
