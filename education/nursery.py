"""EDU-005: the nursery's day — the child is checked in in the morning and handed only to someone allowed to pick them up.

Each child has a short list of people allowed to pick them up (parents, a
grandparent, the driver), each with a phone and an ID to check at the door.
In the morning the child is checked in (with who brought them); at the end
of the day they are checked out to one of those people, never to anyone
else, and the parent gets a WhatsApp in one tap. Both times are kept for the
day. Nothing here bills or moves money: the monthly fee stays the ordinary
fee invoice.
"""

from urllib.parse import quote

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import services
from .attendance import teacher_of
from .models import DayRecord, PickupPerson, Student

MESSAGES = {
    "ar": {"manage": "تعديل اللي مسموح لهم يستلموا الطفل محتاج صلاحية إدارة الطلاب.", "desk": "الحضور والانصراف للإدارة أو المدرّسين بس.",
           "name": "اكتب اسم الشخص.", "phone": "اكتب رقم تليفونه عشان تتأكد منه.", "in": "{student} اتسجل حضوره بالفعل النهارده الساعة {time}.",
           "not_in": "{student} لسه ماتسجلش حضوره النهارده.", "out": "{student} مشي بالفعل الساعة {time} مع {person}.",
           "who": "اختار الشخص اللي هيستلم الطفل.", "not_allowed": "{person} مش في قايمة اللي مسموح لهم يستلموا {student}؛ مينفعش الطفل يمشي معاه.",
           "none_allowed": "مفيش حد مسموح له يستلم {student}؛ ضيف الأب أو الأم الأول من ملف الطفل.", "inactive": "{student} مش طالب شغال.",
           "left_msg": "أهلًا {parent}، {student} مشي من {company} الساعة {time} مع {person}."},
    "en": {"manage": "Changing who may pick a child up needs the manage-students permission.", "desk": "Check-in and pick-up are for the office or the teachers.",
           "name": "Enter the person's name.", "phone": "Enter their phone, to check who they are.", "in": "{student} was already checked in today at {time}.",
           "not_in": "{student} has not been checked in today.", "out": "{student} already left at {time} with {person}.",
           "who": "Choose who is picking the child up.", "not_allowed": "{person} is not on {student}'s pick-up list; the child cannot leave with them.",
           "none_allowed": "Nobody may pick {student} up yet; add a parent first from the child's file.", "inactive": "{student} is not an active student.",
           "left_msg": "Hello {parent}, {student} left {company} at {time} with {person}."},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def is_nursery():
    from entities.current import effective_activity

    return effective_activity() == ("education", "nursery")


def can_desk(user):
    """The office (managing students) or any teacher checks children in and out."""

    return services.can_manage(user) or teacher_of(user) is not None


def _audit(user, action, obj, after, event=AuditEventType.CREATE, before=None):
    AuditLog.objects.create(event_type=event, actor=user, module="education", action=action, object_type=f"education.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


def _time(moment):
    return f"{timezone.localtime(moment):%H:%M}"


def allowed(student):
    return student.pickup_people.filter(active=True)


@transaction.atomic
def save_person(student, data, user, person=None, lang="ar"):
    """Add someone allowed to pick the child up, or change them (``data``: name, relation, phone, id_number, active)."""

    words = _words(lang)
    if not services.can_manage(user):
        raise PermissionDenied(words["manage"])
    name = (data.get("name") or "").strip()[:120]
    phone = (data.get("phone") or "").strip()[:50]
    if not name:
        raise ValidationError(words["name"])
    if not phone:
        raise ValidationError(words["phone"])
    if person is None:
        person = PickupPerson(student=student, created_by=user)
        before = {}
    else:
        person = PickupPerson.objects.select_for_update().get(pk=person.pk, student=student)
        before = {"name": person.name, "relation": person.relation, "phone": person.phone, "id_number": person.id_number, "active": person.active}
    creating = person.pk is None
    person.name, person.phone = name, phone
    person.relation = (data.get("relation") or "").strip()[:40]
    person.id_number = (data.get("id_number") or "").strip()[:30]
    person.active = bool(data.get("active", True))
    person.save()
    _audit(user, "add_pickup_person" if creating else "update_pickup_person", person,
           {"student": student.pk, "name": name, "relation": person.relation, "phone": phone, "id_number": person.id_number, "active": person.active},
           AuditEventType.CREATE if creating else AuditEventType.UPDATE, before)
    return person


@transaction.atomic
def check_in(student, user, *, brought_by="", note="", at=None, lang="ar"):
    words = _words(lang)
    if not can_desk(user):
        raise PermissionDenied(words["desk"])
    student = Student.objects.select_for_update().get(pk=student.pk)  # two taps never make two mornings
    if not student.active:
        raise ValidationError(words["inactive"].format(student=student.name))
    at = at or timezone.now()
    day = timezone.localtime(at).date()
    existing = DayRecord.objects.filter(student=student, day=day).first()
    if existing is not None:
        raise ValidationError(words["in"].format(student=student.name, time=_time(existing.arrived_at)))
    try:
        with transaction.atomic():
            record = DayRecord.objects.create(student=student, day=day, arrived_at=at, arrived_with=(brought_by or "").strip()[:120],
                                              note=(note or "").strip()[:255], checked_in_by=user)
    except IntegrityError:
        raise ValidationError(words["in"].format(student=student.name, time=_time(at)))
    _audit(user, "nursery_check_in", record, {"student": student.pk, "day": day.isoformat(), "at": at.isoformat(), "brought_by": record.arrived_with})
    return record


@transaction.atomic
def check_out(student, person, user, *, at=None, lang="ar"):
    """Hand the child to ``person``, who must be on their list. Returns the day's record."""

    words = _words(lang)
    if not can_desk(user):
        raise PermissionDenied(words["desk"])
    at = at or timezone.now()
    day = timezone.localtime(at).date()
    record = DayRecord.objects.select_for_update(of=("self",)).select_related("picked_by").filter(student=student, day=day).first()
    if record is None:
        raise ValidationError(words["not_in"].format(student=student.name))
    if record.left_at is not None:
        raise ValidationError(words["out"].format(student=student.name, time=_time(record.left_at), person=record.picked_by.name))
    if person is None:
        raise ValidationError(words["who"] if allowed(student).exists() else words["none_allowed"].format(student=student.name))
    if person.student_id != student.pk or not person.active:
        raise ValidationError(words["not_allowed"].format(person=person.name, student=student.name))
    record.left_at, record.picked_by, record.checked_out_by = at, person, user
    record.save(update_fields=["left_at", "picked_by", "checked_out_by"])
    _audit(user, "nursery_check_out", record, {"student": student.pk, "day": day.isoformat(), "at": at.isoformat(), "picked_by": person.pk,
                                               "picked_by_name": person.name}, AuditEventType.UPDATE)
    return record


def board(day=None):
    """The day's children: every active student of the entity, with their record when there is one, in name order.

    Returns (rows, counts) with counts = {"all", "in", "left", "away"}."""

    day = day or timezone.localdate()
    students = list(services.scoped(Student.objects.filter(active=True)).select_related("payer").order_by("name")[:500])
    records = {record.student_id: record for record in DayRecord.objects.filter(day=day, student__in=students).select_related("picked_by")}
    people = {}
    for person in PickupPerson.objects.filter(student__in=students, active=True).order_by("name"):
        people.setdefault(person.student_id, []).append(person)
    rows = [{"student": student, "record": records.get(student.pk), "people": people.get(student.pk, [])} for student in students]
    inside = sum(1 for row in rows if row["record"] and not row["record"].left_at)
    left = sum(1 for row in rows if row["record"] and row["record"].left_at)
    return rows, {"all": len(rows), "in": inside, "left": left, "away": len(rows) - inside - left}


def left_message(record, lang="ar"):
    """A WhatsApp link telling the parent who picked the child up and when, or ''."""

    from printing.company import company_details
    from reports.aging import whatsapp_number

    payer = record.student.payer
    number = whatsapp_number(payer.whatsapp or payer.phone)
    if not number or record.left_at is None:
        return ""
    text = _words(lang)["left_msg"].format(parent=payer.name, student=record.student.name, company=company_details()["name"],
                                           time=_time(record.left_at), person=record.picked_by.name)
    return f"https://wa.me/{number}?text={quote(text)}"


def history(student, limit=30):
    return list(student.day_records.select_related("picked_by")[:limit])
