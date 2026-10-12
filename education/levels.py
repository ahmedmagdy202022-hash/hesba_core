"""EDU-006: a language centre's levels — the placement test, then moving a group's students up a level.

A course (English, French…) is taught in levels: each study group says the
level it teaches. A new student sits a placement test; the level it gives
points to the course's groups at that level with a free seat. At the end of
a level the students who passed move up together: each one's enrolment in
the old group stops the day before, and a new one starts in the next level's
group, with the same discount. Fees stay the ordinary monthly bills; for a
monthly course the move starts on the first of a month, so no month is
billed twice.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import services
from .models import EnrollmentStatus, FeeBasis, PlacementTest, StudyGroup

MAX_LEVEL = 20

MESSAGES = {
    "ar": {"manage": "تحديد المستوى ونقل الطلاب محتاج صلاحية إدارة الطلاب.", "course": "اختار الكورس.", "level": "المستوى رقم من 1 لـ 20.",
           "score": "الدرجة لازم تكون رقم من صفر لحد الدرجة النهائية.", "date": "التاريخ مش مظبوط.", "future": "مينفعش تسجّل امتحان في يوم لسه ماجاش.",
           "nobody": "اختار الطلاب اللي هيطلعوا المستوى الجاي.", "target": "اختار مجموعة المستوى الجاي من نفس الكورس.",
           "higher": "مجموعة {group} مش في مستوى أعلى من {current}.", "first_day": "الكورس بالشهر: النقل يبدأ أول الشهر عشان الشهر مايتحسبش مرتين.",
           "not_here": "{student} مش في المجموعة دي دلوقتي.", "early": "{student} لسه داخل المجموعة يوم {date}؛ النقل لازم يكون بعدها.",
           "seats": "مجموعة {group} فيها {free} مكان بس وانت بتنقل {count}.", "no_level": "المجموعة دي مالهاش مستوى؛ حدده من تعديل المجموعة الأول."},
    "en": {"manage": "Placement and moving students up need the manage-students permission.", "course": "Choose the course.", "level": "The level is a number from 1 to 20.",
           "score": "The mark must be a number from zero up to the full mark.", "date": "The date is not valid.", "future": "A test cannot be recorded on a day still to come.",
           "nobody": "Choose the students moving up to the next level.", "target": "Choose the next level's group in the same course.",
           "higher": "Group {group} is not at a higher level than {current}.", "first_day": "A monthly course moves up on the first of a month, so no month is billed twice.",
           "not_here": "{student} is not in this group now.", "early": "{student} only joined this group on {date}; the move must come after that.",
           "seats": "Group {group} has only {free} free seats and you are moving {count}.", "no_level": "This group has no level; set it from the group's edit form first."},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def is_levels_install():
    """Language centres and training (courses taught in levels)."""

    from entities.current import effective_activity

    return effective_activity() in (("education", "languages"), ("education", "training"))


def _number(raw, message, allow_blank=False):
    if raw in (None, "") and allow_blank:
        return None
    try:
        value = Decimal(services._plain_number(raw if raw is not None else ""))
    except InvalidOperation:
        raise ValidationError(message)
    if not value.is_finite() or value < 0:
        raise ValidationError(message)
    return value


def _audit(user, action, obj, after, event=AuditEventType.CREATE):
    AuditLog.objects.create(event_type=event, actor=user, module="education", action=action, object_type=f"education.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data={}, after_data=after)


@transaction.atomic
def record_placement(student, data, user, lang="ar"):
    """``data``: course, level, taken_on (default today), score and out_of (optional), notes. Returns the PlacementTest."""

    words = _words(lang)
    if not services.can_manage(user):
        raise PermissionDenied(words["manage"])
    course = data.get("course")
    if course is None:
        raise ValidationError(words["course"])
    level = _number(data.get("level"), words["level"])
    if level != level.to_integral_value() or not 1 <= level <= MAX_LEVEL:
        raise ValidationError(words["level"])
    score = _number(data.get("score"), words["score"], allow_blank=True)
    out_of = _number(data.get("out_of"), words["score"], allow_blank=True)
    if score is not None and (score >= Decimal("10000") or (out_of is not None and (out_of == 0 or score > out_of or out_of >= Decimal("10000")))):
        raise ValidationError(words["score"])
    try:
        taken_on = services._date(data.get("taken_on"), services.MESSAGES["en" if lang == "en" else "ar"]) or timezone.localdate()
    except ValidationError:
        raise ValidationError(words["date"])
    if taken_on > timezone.localdate():
        raise ValidationError(words["future"])
    test = PlacementTest.objects.create(student=student, course=course, level=int(level), score=score, out_of=out_of, taken_on=taken_on,
                                        notes=(data.get("notes") or "").strip()[:255], created_by=user)
    _audit(user, "placement_test", test, {"student": student.code, "course": course.name, "level": int(level),
                                          "score": str(score) if score is not None else "", "out_of": str(out_of) if out_of is not None else ""})
    return test


def latest(student):
    return {test.course_id: test for test in reversed(list(student.placements.select_related("course")))}


def groups_at(course, level, student=None):
    """The course's active groups at ``level`` with a free seat (the student's own entity), fullest last."""

    rows = services.scoped(StudyGroup.objects.filter(course=course, level=level, active=True))
    if student is not None:
        rows = services.same_entity_as(rows, student)
    found = []
    for group in rows.select_related("teacher").order_by("name"):
        taken = services.active_count(group)
        if not group.capacity or taken < group.capacity:
            group.free = (group.capacity - taken) if group.capacity else None
            found.append(group)
    return found


def next_groups(group):
    """The groups a class can move up to: the same course, a higher level, active, the same entity."""

    if group.level is None:
        return []
    rows = services.same_entity_as(services.scoped(StudyGroup.objects.filter(course=group.course, level__gt=group.level, active=True)), group)
    return list(rows.order_by("level", "name"))


@transaction.atomic
def move_up(group, students, target, user, *, on_date=None, lang="ar"):
    """Move ``students`` of ``group`` to ``target`` (the same course, a higher level). Returns the new enrolments.

    All or nothing: one student who cannot move stops the whole move, with the reason."""

    words = _words(lang)
    if not services.can_manage(user):
        raise PermissionDenied(words["manage"])
    if not students:
        raise ValidationError(words["nobody"])
    if group.level is None:
        raise ValidationError(words["no_level"])
    if target is None or target.course_id != group.course_id or target.pk == group.pk:
        raise ValidationError(words["target"])
    if target.level is None or target.level <= group.level:
        raise ValidationError(words["higher"].format(group=target.name, current=group.level))
    day = on_date or timezone.localdate()
    if group.course.basis == FeeBasis.MONTHLY and day.day != 1:
        raise ValidationError(words["first_day"])
    target_locked = StudyGroup.objects.select_for_update().get(pk=target.pk)
    if target_locked.capacity:
        free = target_locked.capacity - services.active_count(target_locked)
        if len(students) > free:
            raise ValidationError(words["seats"].format(group=target.name, free=max(free, 0), count=len(students)))
    moved = []
    for student in students:
        enrollment = group.enrollments.select_for_update().filter(student=student, status=EnrollmentStatus.ACTIVE).first()
        if enrollment is None:
            raise ValidationError(words["not_here"].format(student=student.name))
        if day - timedelta(days=1) < enrollment.start_date:
            raise ValidationError(words["early"].format(student=student.name, date=enrollment.start_date.isoformat()))
        services.stop(enrollment, user, end_date=day - timedelta(days=1), lang=lang)
        moved.append(services.enroll(student, target, user, start_date=day, discount_percent=str(enrollment.discount_percent),
                                     notes=enrollment.notes, lang=lang))
    _audit(user, "move_up_level", group, {"from": group.code, "to": target.code, "from_level": group.level, "to_level": target.level,
                                          "on": day.isoformat(), "students": [student.code for student in students]}, AuditEventType.UPDATE)
    return moved
