"""E1: attendance by scan. A student's card carries the student code (``ST-00012``) as a QR; a scan marks that student for the
class they are at, and nothing else.

The code is a convenience for staff, never a credential: it opens no page and unlocks nothing. Who may scan is exactly who may
take attendance for the group today (a manager for any group, a teacher for their own), and a code from another entity is "not
found", never a different message.

Money: per-class fees bill what ``billing._attended`` counts, the present and late marks. A scan writes one student's mark under
the same group lock ``attendance.take`` takes, so it never races a teacher's save, and it never touches a sale, a cashbox or a
ledger. A student who is not scanned gets no mark at all: the sheet shows them absent once the class has started (see the
attendance view), so closing the class cannot bill a student who never came.

Rules, in order: the student (active, in this entity) → the groups they have today → the ones this user may take → the one whose
window (30 minutes before the start until the class ends) holds now. Late is strictly more than ``LATE_MINUTES`` after the start.
A group with no start time is open all day and never late.

E8 (HG-105), the makeup class: a student with a booked makeup seat (``makeup.MakeupSeat``) for a group this user may take today is, for that
group only, treated as its guest. The answer is «حصة تعويضية» and the seat is marked attended, and that is all it writes: no ``Attendance``
row, no ``ClassSession`` (a held class is what per-class teacher pay counts, so a guest scanned first must not open one) and an audit row that
is not ``scan_attendance`` (which ``touched`` reads to flip a register into its «not scanned is absent» mode and so change per-class billing).
A student with no seat gets every answer above unchanged.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import attendance, makeup, services
from .models import Attendance, ClassSession, Enrollment, Presence, SessionStatus, Student, StudyGroup

LATE_MINUTES = 10
EARLY_MINUTES = 30

MESSAGES = {
    "ar": {"denied": "تسجيل الحضور بالمسح للإدارة أو المدرّسين بس.", "empty": "امسح الكارت أو اكتب الكود.",
           "not_found": "الكارت ده مش معروف. اتأكد من الكود.", "inactive": "تسجيل {student} موقوف.",
           "refused": "الكارت ده مش لحد من طلاب مجموعاتك النهارده.", "cancelled": "حصة {group} النهارده اتلغت؛ مفيش حضور بيتسجّل عليها.",
           "no_today": "مفيش حصص النهارده لـ{student}.", "no_now": "مفيش حصة دلوقتي لـ{student}. حصص النهارده:",
           "choose": "{student} في أكتر من حصة دلوقتي. الحضور لأنهي حصة؟", "again": "حضور {student} اتسجّل قبل كده.",
           "present": "حضور", "late": "تأخير", "absent": "غياب", "excused": "بعذر", "was_absent": "كان متسجّل غياب واتحوّل لتأخير.",
           "bad_group": "المجموعة دي مش من حصص الطالب النهارده.", "makeup": "حصة تعويضية", "makeup_note": "حصة تعويضية: حضور {student} اتسجّل.",
           "makeup_was_missed": "الحجز كان متسجّل غياب واتصحّح لحضور."},
    "en": {"denied": "Scanning attendance is for the office or the teachers.", "empty": "Scan the card or type the code.",
           "not_found": "This card is not known. Check the code.", "inactive": "{student} is not an active student.",
           "refused": "This student is not in your groups today.", "cancelled": "Today's {group} class was cancelled; no attendance is taken for it.",
           "no_today": "{student} has no classes today.", "no_now": "{student} has no class right now. Today's classes:",
           "choose": "{student} has more than one class right now. Which one are they at?", "again": "{student} was already marked.",
           "present": "Present", "late": "Late", "absent": "Absent", "excused": "Excused", "was_absent": "Was marked absent; now late.",
           "bad_group": "That group is not one of the student's classes today.", "makeup": "Makeup class", "makeup_note": "Makeup class: {student}'s attendance was recorded.",
           "makeup_was_missed": "The booking was marked absent; now corrected to attended."},
}


@dataclass
class ScanResult:
    """What a scan did, for the screen: ``status`` is one of ok, again, choose, no_class, not_found, inactive, refused, cancelled."""

    status: str
    message: str = ""
    student: Student | None = None
    group: object = None
    presence: str = ""
    at: str = ""
    changed: bool = False
    was_absent: bool = False
    choices: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    lang: str = "ar"

    @property
    def ok(self):
        return self.status in ("ok", "again")

    def as_json(self):
        def line(group):
            return {"id": group.pk, "name": group.name, "course": group.course.name, "time": group.start_time.strftime("%H:%M") if group.start_time else ""}

        words = MESSAGES[self.lang]
        return {"status": self.status, "message": self.message, "changed": self.changed, "time": self.at, "presence": self.presence,
                "presence_label": words.get(self.presence, ""), "was_absent": self.was_absent,
                "student": {"name": self.student.name, "code": self.student.code, "stage": self.student.stage} if self.student else None,
                "group": line(self.group) if self.group else None, "choices": [line(group) for group in self.choices],
                "groups": [line(group) for group in self.groups]}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _clean(code):
    return "".join(str(code or "").split()).upper()[:40]


def find_student(code):
    """The active student with this code in the entity being worked in, else None. An inactive one is returned separately."""

    code = _clean(code)
    if not code:
        return None
    return services.scoped(Student.objects).filter(code__iexact=code).first()


def _today_groups(student, day):
    """The student's groups that meet on ``day``: running enrolments (``roster_on`` semantics), an active group, one of its days."""

    rows = (Enrollment.objects.filter(student=student, start_date__lte=day).filter(Q(end_date__isnull=True) | Q(end_date__gte=day))
            .filter(group__in=services.scoped(StudyGroup.objects.filter(active=True))).select_related("group__course", "group__teacher")
            .order_by("group__start_time", "group__name", "-start_date", "-pk"))
    seen, groups = set(), []
    for row in rows:
        if row.group_id not in seen and attendance.meets_on(row.group, day):
            seen.add(row.group_id)
            groups.append(row.group)
    return groups


def _start(group, day):
    return timezone.make_aware(datetime.combine(day, group.start_time)) if group.start_time else None


def in_window(group, now):
    """Whether ``now`` is from ``EARLY_MINUTES`` before the group's start until its class ends (both ends inclusive)."""

    start = _start(group, timezone.localtime(now).date())
    if start is None:
        return True
    return start - timedelta(minutes=EARLY_MINUTES) <= now <= start + timedelta(minutes=group.duration_minutes)


def presence_at(group, now):
    """Present, or late when ``now`` is strictly more than ``LATE_MINUTES`` after the start."""

    start = _start(group, timezone.localtime(now).date())
    return Presence.LATE if start is not None and now > start + timedelta(minutes=LATE_MINUTES) else Presence.PRESENT


def touched(session_ids):
    """Which of these classes a scan has marked, as a set of ids.

    The signal is the ``scan_attendance`` audit row (written in the same transaction as the mark, append-only, and naming its class):
    it needs no new column, and a centre that never scans has none, so its sheets keep behaving exactly as before."""

    ids = {str(pk) for pk in session_ids}
    if not ids:
        return set()
    rows = AuditLog.objects.filter(module="education", action="scan_attendance", object_type="education.ClassSession", object_id__in=ids)
    return {int(pk) for pk in rows.values_list("object_id", flat=True)}


@transaction.atomic
def mark(code, user, now=None, *, group=None, lang="ar"):
    """Mark the student with this code for the class they are at, and say what happened. ``group`` (an id) answers a «which class?» choice."""

    words = _words(lang)
    if not attendance.can_scan(user):
        raise PermissionDenied(words["denied"])
    now = now or timezone.now()
    local = timezone.localtime(now)
    day, at = local.date(), f"{local:%H:%M}"
    if not _clean(code):
        return ScanResult("not_found", words["empty"], lang=lang)
    student = find_student(code)
    if student is None:
        return ScanResult("not_found", words["not_found"], lang=lang)
    if not student.active:
        if not services.can_manage(user):  # a teacher learns nothing about a stopped student who may never have been theirs
            return ScanResult("refused", words["refused"], lang=lang)
        return ScanResult("inactive", words["inactive"].format(student=student.name), student=student, lang=lang)
    today = _today_groups(student, day)
    allowed = [row for row in today if attendance.can_take(user, row)]
    # E8: a booked makeup seat for a group of theirs that this user may take opens that group to the scan, as a guest (never a roster row).
    guest_seats = {seat.group_id: seat for seat in makeup.seats_today(student, day, user) if seat.group_id not in {row.pk for row in allowed}}
    allowed = allowed + [seat.group for seat in guest_seats.values()]
    if not allowed:
        if not services.can_manage(user):  # a teacher is told nothing about a student who is not theirs, not even the name
            return ScanResult("refused", words["refused"], lang=lang)
        return ScanResult("no_class", words["no_today"].format(student=student.name), student=student, lang=lang)
    if group not in (None, ""):
        chosen = [row for row in allowed if str(row.pk) == str(group)]
        if not chosen:
            return ScanResult("refused", words["bad_group"], lang=lang)
    else:
        chosen = [row for row in allowed if in_window(row, now)]
        if not chosen:
            return ScanResult("no_class", words["no_now"].format(student=student.name), student=student, groups=allowed, lang=lang)
        if len(chosen) > 1:
            return ScanResult("choose", words["choose"].format(student=student.name), student=student, choices=chosen, lang=lang)
    target = chosen[0]
    if target.pk in guest_seats:
        return _guest(guest_seats[target.pk], student, user, local, day, at, allowed, words, lang)
    # The same lock ``take`` holds for this group: a scan during a teacher's save waits for it and is never lost.
    locked = attendance.lock_group(target)
    if not any(row.pk == locked.pk for row in _today_groups(student, day)):
        return ScanResult("no_class", words["no_now"].format(student=student.name), student=student, groups=allowed, lang=lang)
    session, created = ClassSession.objects.get_or_create(group=locked, date=day, defaults={"taken_by": user})
    if session.status == SessionStatus.CANCELLED:
        return ScanResult("cancelled", words["cancelled"].format(group=locked.name), student=student, group=target, lang=lang)
    wanted = presence_at(locked, now)
    current = Attendance.objects.filter(session=session, student=student).first()
    before, was_absent = (current.presence if current else None), False
    if current is None:
        Attendance.objects.create(session=session, student=student, presence=wanted)
        presence, changed = wanted, True
    elif current.presence == Presence.ABSENT:  # the class was closed before they came: late, never present
        current.presence = presence = Presence.LATE
        current.save(update_fields=["presence"])
        changed, was_absent = True, True
    else:  # present stays present (a rescan never makes it late), late stays late, and a teacher's «excused» is theirs to change
        presence, changed = current.presence, False
    if changed:
        AuditLog.objects.create(event_type=AuditEventType.CREATE if created else AuditEventType.UPDATE, actor=user, module="education", action="scan_attendance",
                                object_type="education.ClassSession", object_id=str(session.pk),
                                before_data={"student": student.code, "presence": before},
                                after_data={"student": student.code, "group": locked.code, "date": day.isoformat(), "presence": presence, "at": local.isoformat(),
                                            "session_started": created})
    message = words["again"].format(student=student.name) if not changed else (words["was_absent"] if was_absent else "")
    return ScanResult("ok" if changed else "again", message, student=student, group=target, presence=presence, at=at, changed=changed, was_absent=was_absent, lang=lang)


def _guest(seat, student, user, local, day, at, allowed, words, lang):
    """E8: the card of a student with a booked makeup seat for this class: the seat is marked attended and nothing else is written (no mark in the
    class's own register, no class opened: see ``makeup.attend_by_scan``). Under the same group lock a teacher's save takes."""

    locked = attendance.lock_group(seat.group)
    if not locked.active or not attendance.meets_on(locked, day):
        return ScanResult("no_class", words["no_now"].format(student=student.name), student=student, groups=allowed, lang=lang)
    state, seat = makeup.attend_by_scan(seat, user, local, local.isoformat())
    if state == "cancelled":
        return ScanResult("cancelled", words["cancelled"].format(group=locked.name), student=student, group=locked, lang=lang)
    if state == "again":
        return ScanResult("again", words["again"].format(student=student.name), student=student, group=locked, presence="makeup", at=at, lang=lang)
    return ScanResult("ok", words["makeup_was_missed"] if seat.was_missed else words["makeup_note"].format(student=student.name), student=student, group=locked,
                      presence="makeup", at=at, changed=True, lang=lang)


NURSERY_MESSAGES = {
    "ar": {"in": "{student} وصل الساعة {time}.", "inside": "{student} موجود جوه. اختار مين هيستلمه.", "gone": "{student} مشي بالفعل الساعة {time}."},
    "en": {"in": "{student} arrived at {time}.", "inside": "{student} is inside. Choose who is picking them up.", "gone": "{student} already left at {time}."},
}


def nursery_mark(code, user, now=None, *, lang="ar"):
    """The nursery's scan: a child not yet in is checked in (``nursery.check_in``, unchanged); one already in is sent to the pick-up choice
    (status ``inside``, the pick-up rule stays ``nursery.check_out``'s); one already gone is only reported."""

    from . import nursery
    from .models import DayRecord

    words, nursery_words = _words(lang), NURSERY_MESSAGES["en" if lang == "en" else "ar"]
    if not nursery.can_desk(user):
        raise PermissionDenied(nursery._words(lang)["desk"])
    now = now or timezone.now()
    student = find_student(code) if _clean(code) else None
    if student is None:
        return ScanResult("not_found", words["empty"] if not _clean(code) else words["not_found"], lang=lang)
    if not student.active:
        return ScanResult("inactive", words["inactive"].format(student=student.name), student=student, lang=lang)
    day = timezone.localtime(now).date()
    record = DayRecord.objects.filter(student=student, day=day).first()
    if record is None:
        record = nursery.check_in(student, user, at=now, lang=lang)
        return ScanResult("checked_in", nursery_words["in"].format(student=student.name, time=f"{timezone.localtime(record.arrived_at):%H:%M}"),
                          student=student, at=f"{timezone.localtime(record.arrived_at):%H:%M}", changed=True, lang=lang)
    if record.left_at is None:
        return ScanResult("inside", nursery_words["inside"].format(student=student.name), student=student, lang=lang)
    return ScanResult("gone", nursery_words["gone"].format(student=student.name, time=f"{timezone.localtime(record.left_at):%H:%M}"), student=student, lang=lang)
