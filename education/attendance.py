"""EDU-002: taking attendance for a class, and reading it back.

The roster of a session is the group's enrolments that were running on its
day. A teacher (a user linked to an employee) takes attendance for their own
groups; whoever manages students can take it for any group. Attendance never
touches money.
"""

from datetime import timedelta
from urllib.parse import quote

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import services
from .models import Attendance, ClassSession, Enrollment, Presence, SessionStatus, StudyGroup, Weekday

WEEKDAY_OF = {5: "sat", 6: "sun", 0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri"}  # Python's weekday() → ours
COUNTS_AS_PRESENT = (Presence.PRESENT, Presence.LATE)

MESSAGES = {
    "ar": {"future": "مينفعش تسجّل حضور ليوم لسه مجاش.", "teacher_only": "تقدر تسجّل حضور مجموعاتك بس.",
           "no_roster": "مفيش طلاب مسجّلين في المجموعة في اليوم ده.", "date": "التاريخ مش مظبوط.", "closed": "المجموعة دي مقفولة.",
           "off_day": "المجموعة دي مالهاش حصة في اليوم ده (برّه أيامها أو مواعيد بدايتها ونهايتها).",
           "absent_msg": "أهلًا {parent}، {student} كان غايب {when} عن حصة {group} في {company}. لو فيه حاجة كلمنا.",
           "late_msg": "أهلًا {parent}، {student} وصل متأخر {when} عن حصة {group} في {company}.",
           "today": "النهارده", "on_day": "يوم {date}"},
    "en": {"future": "Attendance cannot be taken for a day still to come.", "teacher_only": "You can take attendance for your own groups only.",
           "no_roster": "No students were enrolled in this group on that day.", "date": "The date is not valid.", "closed": "This group is closed.",
           "off_day": "This group has no class on that day (not one of its days, or outside its dates).",
           "absent_msg": "Hello {parent}, {student} was absent {when} from {group} at {company}. Please get in touch if anything is wrong.",
           "late_msg": "Hello {parent}, {student} arrived late {when} to {group} at {company}.",
           "today": "today", "on_day": "on {date}"},
}


def weekday(day):
    return WEEKDAY_OF[day.weekday()]


def teacher_of(user):
    employee = getattr(user, "employee", None) if getattr(user, "is_authenticated", False) else None
    return employee if employee is not None and employee.active else None


def can_take(user, group):
    """Managers take attendance for any group; a teacher for their own."""

    if services.can_manage(user):
        return True
    teacher = teacher_of(user)
    return teacher is not None and group.teacher_id == teacher.pk


def can_see(user, group=None):
    if services.can_view(user):
        return True
    teacher = teacher_of(user)
    return teacher is not None and (group is None or group.teacher_id == teacher.pk)


def roster_on(group, day):
    """Enrolments running on ``day``: started by then and not stopped before it."""

    rows = (Enrollment.objects.filter(group=group, start_date__lte=day).filter(Q(end_date__isnull=True) | Q(end_date__gte=day))
            .select_related("student__payer").order_by("student__name", "-start_date", "-pk"))
    # Stopped and enrolled again on the same day gives two running enrolments: one student, one row (the newer).
    seen, unique = set(), []
    for row in rows:
        if row.student_id not in seen:
            seen.add(row.student_id)
            unique.append(row)
    return unique


def meets_on(group, day):
    """Whether the group has a class on ``day``: one of its weekdays, within its dates."""

    if group.starts_on and day < group.starts_on or group.ends_on and day > group.ends_on:
        return False
    return weekday(day) in group.weekdays


def last_class_day(group, today=None):
    """The latest day up to ``today`` the group met (within a fortnight), else None."""

    today = today or timezone.localdate()
    return next((today - timedelta(days=n) for n in range(14) if meets_on(group, today - timedelta(days=n))), None)


def groups_on(day, user=None):
    """Active groups meeting on ``day`` (by weekday and dates), the ones ``user`` teaches only when they are a teacher, not a manager."""

    code = weekday(day)
    rows = services.scoped(StudyGroup.objects).filter(active=True).filter(Q(starts_on__isnull=True) | Q(starts_on__lte=day)).filter(
        Q(ends_on__isnull=True) | Q(ends_on__gte=day)).select_related("course", "teacher").order_by("start_time", "name")
    if user is not None and not services.can_view(user):
        teacher = teacher_of(user)
        rows = rows.filter(teacher=teacher) if teacher else rows.none()
    return [group for group in rows if code in group.weekdays]


def _date(raw, words):
    if hasattr(raw, "year"):
        return raw
    try:
        from datetime import date

        return date.fromisoformat(str(raw or "").strip()) if raw else timezone.localdate()
    except ValueError:
        raise ValidationError(words["date"])


@transaction.atomic
def take(group, user, day, marks, *, topic="", cancelled=False, lang="ar"):
    """Save one class: ``marks`` maps student id → presence (unmarked students count as present)."""

    words = MESSAGES[lang]
    if not can_take(user, group):
        raise PermissionDenied(words["teacher_only"])
    day = _date(day, words)
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    group = StudyGroup.objects.select_for_update().get(pk=group.pk)
    if not group.active:
        raise ValidationError(words["closed"])
    if not meets_on(group, day):
        raise ValidationError(words["off_day"])  # an unscheduled "class" would change everyone's attendance rate
    roster = list(roster_on(group, day))
    if not roster and not cancelled:
        raise ValidationError(words["no_roster"])
    session, created = ClassSession.objects.get_or_create(group=group, date=day, defaults={"taken_by": user})
    session.status = SessionStatus.CANCELLED if cancelled else SessionStatus.HELD
    session.topic, session.taken_by = (topic or "").strip()[:255], user
    session.save()
    session.marks.all().delete()
    if not cancelled:
        def presence_of(student_id):
            value = marks.get(str(student_id), marks.get(student_id))
            return value if value in Presence.values else Presence.PRESENT

        Attendance.objects.bulk_create([Attendance(session=session, student=row.student, presence=presence_of(row.student_id),
                                                   note=(marks.get(f"note_{row.student_id}") or "").strip()[:255]) for row in roster])
    counts = dict(session.marks.values_list("presence").annotate(n=Count("id")))
    AuditLog.objects.create(event_type=AuditEventType.CREATE if created else AuditEventType.UPDATE, actor=user, module="education", action="take_attendance",
                            object_type="education.ClassSession", object_id=str(session.pk), before_data={},
                            after_data={"group": group.code, "date": day.isoformat(), "status": session.status, "counts": counts})
    return session


def rate(queryset):
    """Share of held classes attended (late counts as attended), as a whole percentage, or None with nothing recorded."""

    totals = queryset.filter(session__status=SessionStatus.HELD).aggregate(
        all=Count("id"), came=Count("id", filter=Q(presence__in=COUNTS_AS_PRESENT)))
    return round(100 * totals["came"] / totals["all"]) if totals["all"] else None


def student_summary(student, days=30, today=None):
    today = today or timezone.localdate()
    recent = Attendance.objects.filter(student=student, session__date__gte=today - timedelta(days=days))
    return {
        "rate": rate(recent),
        "absences": list(recent.filter(presence=Presence.ABSENT, session__status=SessionStatus.HELD)
                         .select_related("session__group__course").order_by("-session__date")[:10]),
    }


def group_rates(group, days=30, today=None):
    """{student id: attendance % over the last ``days``} for the group's own classes."""

    today = today or timezone.localdate()
    rows = (Attendance.objects.filter(session__group=group, session__status=SessionStatus.HELD, session__date__gte=today - timedelta(days=days))
            .values("student").annotate(all=Count("id"), came=Count("id", filter=Q(presence__in=COUNTS_AS_PRESENT))))
    return {row["student"]: round(100 * row["came"] / row["all"]) for row in rows if row["all"]}


def parent_message(attendance, lang="ar"):
    """A WhatsApp link telling the parent about an absence or a late arrival, or ''."""

    from printing.company import company_details
    from reports.aging import whatsapp_number

    payer = attendance.student.payer
    number = whatsapp_number(payer.whatsapp or payer.phone)
    if not number or attendance.presence not in (Presence.ABSENT, Presence.LATE):
        return ""
    words = MESSAGES[lang]
    template = words["absent_msg" if attendance.presence == Presence.ABSENT else "late_msg"]
    day = attendance.session.date
    when = words["today"] if day == timezone.localdate() else words["on_day"].format(date=day.isoformat())  # an older class says its own date
    text = template.format(parent=payer.name, student=attendance.student.name, group=attendance.session.group.name, company=company_details()["name"], when=when)
    return f"https://wa.me/{number}?text={quote(text)}"
