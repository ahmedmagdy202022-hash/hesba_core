"""E8 (HG-105): a makeup class: a student who missed (or will miss) a class of their own group attends one class of ANOTHER group of the
same course. The centre books a seat, the seat counts against the room, the teacher marks the guest on that day's register, and the
guardian sees it in the portal.

The hard line of this item: a seat is NOT attendance. It writes no ``Attendance`` row and no ``ClassSession`` (a held session is what a
teacher paid per class is counted on, and a mark is what a per-class fee bills), it makes no fee charge, no invoice and no teacher
payment, and nothing in ``billing``, ``pay`` or ``insights`` reads this table. Nothing here moves money, stock, a ledger or a cashbox.

Who: ``attendance.can_take`` decides everything, and no permission was added. Booking and cancelling need whoever may take attendance
for the HOME group or for the TARGET group; marking the guest attended or missed needs whoever may take the TARGET group's attendance.

Capacity: the booking locks the target group's row first (the lock ``attendance.take`` and a scan hold), then counts the live seats of
that day and the group's active enrolments under it, so two bookings at once never overfill the room. A cancelled seat frees its place.
Nothing is deleted; every change writes one audit row whose action is never ``scan_attendance`` (that one tells a register a scan marked it).
"""

from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import attendance as register
from . import services
from .models import LIVE_SEATS, ClassSession, Enrollment, EnrollmentStatus, MakeupSeat, SeatStatus, SessionStatus, StudyGroup

WINDOW_DAYS = 14  # the booking form lists the classes from today and for the next 14 days (today and the 13 after it)
PAST_DAYS = 30  # the guardian also sees the seats attended or missed in the last 30 days, as the attendance block counts them
NOTE_MAX = 255

MESSAGES = {
    "ar": {"forbidden": "الحصص التعويضية لإدارة المركز ولمدرس المجموعة بس.", "forbidden_mark": "تسجّل حضور الضيف لإدارة المركز ولمدرس المجموعة اللي بيحضر عندها بس.",
           "date": "التاريخ مش مظبوط.", "student_inactive": "الطالب ده موقوف.", "no_enrollment": "الطالب ده مش مسجّل دلوقتي في المجموعة الأصلية.",
           "same_group": "الحصة التعويضية لازم تكون في مجموعة تانية غير المجموعة الأصلية.", "other_course": "الحصة التعويضية لازم تكون في مجموعة من نفس الكورس.",
           "entity": "الطالب والمجموعتين لازم يكونوا في نفس الكيان.", "closed": "المجموعة دي مقفولة.", "in_group": "الطالب ده مسجّل في المجموعة دي أصلًا.",
           "off_day": "المجموعة دي مالهاش حصة في اليوم ده.", "past": "مينفعش تحجز حصة في يوم عدّى.", "cancelled_class": "حصة المجموعة دي في اليوم ده اتلغت.",
           "missed_on": "اليوم اللي اتغاب فيه لازم يكون من أيام المجموعة الأصلية.", "note_long": "الملاحظة أطول من اللازم (255 حرف على الأكتر).",
           "duplicate": "الطالب ده حاجز مكان في الحصة دي بالفعل.", "full": "الحصة دي مكتملة ({capacity} مكان).", "gone": "الحجز ده اتلغى.",
           "attended_cancel": "الحجز ده اتسجّل عليه حضور أو غياب، فمينفعش يتلغى.", "future": "مينفعش تسجّل حضور حصة لسه مجاتش.",
           "bad_status": "الحالة مش معروفة.", "choose_home": "اختار المجموعة الأصلية للطالب.", "choose_class": "اختار الحصة اللي هيحضرها."},
    "en": {"forbidden": "Makeup classes are for the centre's managers and the group's teacher only.",
           "forbidden_mark": "Only the centre's managers and the teacher of the group being attended can mark the guest.",
           "date": "The date is not valid.", "student_inactive": "This student is inactive.", "no_enrollment": "This student is not enrolled in the home group now.",
           "same_group": "The makeup class must be in a different group from the home group.", "other_course": "The makeup class must be in a group of the same course.",
           "entity": "The student and both groups must belong to the same entity.", "closed": "This group is closed.", "in_group": "This student is already enrolled in this group.",
           "off_day": "This group has no class on that day.", "past": "A class on a day that has passed cannot be booked.", "cancelled_class": "This group's class on that day was cancelled.",
           "missed_on": "The missed day must be one of the home group's class days.", "note_long": "The note is too long (255 characters at most).",
           "duplicate": "This student already holds a seat in that class.", "full": "This class is full ({capacity} seats).", "gone": "This booking was cancelled.",
           "attended_cancel": "Attendance or absence was already recorded on this booking, so it cannot be cancelled.", "future": "Attendance cannot be marked for a class still to come.",
           "bad_status": "The status is not recognised.", "choose_home": "Choose the student's home group.", "choose_class": "Choose the class they will attend."},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def live_seats():
    """Every seat that still holds a place (booked, attended or missed); only a cancelled one frees it."""

    return MakeupSeat.objects.filter(status__in=LIVE_SEATS)


def scoped_seats():
    """Every seat of the groups of the entity being worked in (a seat's entity is its target group's)."""

    return MakeupSeat.objects.filter(group__in=services.scoped(StudyGroup.objects))


# ---- who ----

def can_book(user, home, group):
    """Whoever may take attendance for the home group or for the target group."""

    return register.can_take(user, home) or register.can_take(user, group)


def can_mark(user, group):
    """Whoever may take the TARGET group's attendance: the guest is theirs for that class."""

    return register.can_take(user, group)


def can_use(user, student):
    """Whether this user could book some makeup class for this student: they may take the attendance of one of the student's groups, or of
    another group of the same course. Decides who sees the booking screen (the booking itself checks the exact pair)."""

    if services.can_manage(user):
        return True
    teacher = register.teacher_of(user)
    if teacher is None:
        return False
    homes = StudyGroup.objects.filter(enrollments__student=student, enrollments__status=EnrollmentStatus.ACTIVE, active=True)
    return StudyGroup.objects.filter(active=True, teacher=teacher).filter(Q(pk__in=homes) | Q(course__in=homes.values("course"))).exists()


# ---- seats and room ----

def taken(group, day):
    """Live seats of this class (one group, one day)."""

    return live_seats().filter(group=group, day=day).count()


def free_seats(group, day, seats=None, enrolled=None):
    """Places still free in this group's class on ``day``: its capacity less its active enrolments and the live seats. ``None`` when it has no
    capacity (0 means no limit). One function, used by the booking form and by the booking itself, so what the page shows is what is enforced.

    ``seats`` and ``enrolled``: the live seats of that day and the group's active enrolments when the caller already counted them (the form
    counts every candidate at once); the formula is this one either way."""

    if not group.capacity:
        return None
    used = taken(group, day) if seats is None else seats
    return max(group.capacity - (services.active_count(group) if enrolled is None else enrolled) - used, 0)


def _date(raw, words):
    if hasattr(raw, "year"):
        return raw
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError:
        raise ValidationError(words["date"])


def _cancelled_class(group, day):
    return ClassSession.objects.filter(group=group, date=day, status=SessionStatus.CANCELLED).exists()


def _snapshot(seat):
    return {"student": seat.student.code, "home": seat.home_group.code, "group": seat.group.code, "day": seat.day.isoformat(),
            "missed_on": seat.missed_on.isoformat() if seat.missed_on else None, "status": seat.status, "note": seat.note}


def _log(user, action, seat, before):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE if before else AuditEventType.CREATE, actor=user, module="education", action=action,
                            object_type="education.MakeupSeat", object_id=str(seat.pk), before_data=before, after_data=_snapshot(seat))


def classes_for(student, home, user, today=None, days=WINDOW_DAYS):
    """The classes ``student`` could make ``home`` up in: [{group, day, time, free, bookable, held}] for the other running groups of the same
    course in the same entity that the student is not enrolled in, on the days each meets, from today for ``days`` days, soonest first.

    A class that is full is listed (so the screen can say «مكتمل») with ``bookable`` False, as is one the student already holds a seat in
    (``held``). A class whose day was cancelled is not listed. A user who may not take the home group's attendance sees only the groups they may."""

    today = today or timezone.localdate()
    last = today + timedelta(days=days - 1)
    groups = list(services.same_entity_as(services.scoped(StudyGroup.objects), home).filter(active=True, course_id=home.course_id).exclude(pk=home.pk)
                  .exclude(pk__in=Enrollment.objects.filter(student=student, status=EnrollmentStatus.ACTIVE).values("group_id")).select_related("course", "teacher").order_by("start_time", "name", "pk"))
    if not register.can_take(user, home):
        groups = [group for group in groups if register.can_take(user, group)]
    if not groups:
        return []
    ids = [group.pk for group in groups]
    counts = {(row["group"], row["day"]): row["n"] for row in live_seats().filter(group_id__in=ids, day__range=(today, last)).values("group", "day").annotate(n=Count("pk"))}
    held = set(live_seats().filter(student=student, group_id__in=ids, day__range=(today, last)).values_list("group_id", "day"))
    cancelled = set(ClassSession.objects.filter(group_id__in=ids, date__range=(today, last), status=SessionStatus.CANCELLED).values_list("group_id", "date"))
    enrolled = {group.pk: services.active_count(group) for group in groups if group.capacity}
    rows = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        for group in groups:
            if not register.meets_on(group, day) or (group.pk, day) in cancelled:
                continue
            free = free_seats(group, day, counts.get((group.pk, day), 0), enrolled.get(group.pk))
            rows.append({"group": group, "day": day, "time": group.start_time.strftime("%H:%M") if group.start_time else "", "free": free,
                         "held": (group.pk, day) in held, "bookable": free != 0 and (group.pk, day) not in held})
    rows.sort(key=lambda row: (row["day"], row["group"].start_time is None, row["group"].start_time, row["group"].name, row["group"].pk))
    return rows


def for_student(student, limit=10):
    """The student's seats for their page, newest class first (cancelled ones included, labelled)."""

    return list(student.makeup_seats.select_related("group__course", "home_group").order_by("-day", "-pk")[:limit])


def guests_of(groups, day):
    """``{group id: [seat, …]}``: the live seats of these groups on ``day``, for the register and today's classes (one read)."""

    out = {}
    ids = [group.pk for group in groups]
    if not ids:
        return out
    for seat in live_seats().filter(group_id__in=ids, day=day).select_related("student", "home_group__course").order_by("student__name", "pk"):
        out.setdefault(seat.group_id, []).append(seat)
    return out


# ---- the changes ----

@transaction.atomic
def book(student, home, group, day, user, *, missed_on=None, note="", today=None, lang="ar"):
    """Book ``student`` a seat in ``group``'s class on ``day`` in place of a class of ``home``. Returns the seat.

    Refused, in this order: a user who may take neither group's attendance; a day that is not a date; the same group, another course or another
    entity; an inactive student, one with no active enrolment in ``home``, or one already enrolled in ``group``; a closed group; a day in the
    past, one the group does not meet (its weekdays and dates) or a class that was cancelled; a missed day that is not one of ``home``'s class
    days; a seat the student already holds there; a full room. The target group is locked first, so the capacity check and the insert are one step."""

    words = _words(lang)
    if not can_book(user, home, group):
        raise PermissionDenied(words["forbidden"])
    day = _date(day, words)
    missed = _date(missed_on, words) if missed_on not in (None, "") else None
    note = (note or "").strip()
    if len(note) > NOTE_MAX:
        raise ValidationError(words["note_long"])
    group = register.lock_group(group)  # the lock every writer of a class takes first: two bookings at once never overfill it
    if home.pk == group.pk:
        raise ValidationError(words["same_group"])
    if home.course_id != group.course_id:
        raise ValidationError(words["other_course"])
    if len({services.entity_id_of(student), services.entity_id_of(home), services.entity_id_of(group)}) != 1:
        raise ValidationError(words["entity"])
    if not student.active:
        raise ValidationError(words["student_inactive"])
    if not Enrollment.objects.filter(student=student, group=home, status=EnrollmentStatus.ACTIVE).exists():
        raise ValidationError(words["no_enrollment"])
    if Enrollment.objects.filter(student=student, group=group, status=EnrollmentStatus.ACTIVE).exists():
        raise ValidationError(words["in_group"])
    if not group.active:
        raise ValidationError(words["closed"])
    if day < (today or timezone.localdate()):
        raise ValidationError(words["past"])
    if not register.meets_on(group, day):
        raise ValidationError(words["off_day"])
    if _cancelled_class(group, day):
        raise ValidationError(words["cancelled_class"])
    if missed is not None and not register.meets_on(home, missed):
        raise ValidationError(words["missed_on"])
    if live_seats().filter(student=student, group=group, day=day).exists():
        raise ValidationError(words["duplicate"])
    if free_seats(group, day) == 0:
        raise ValidationError(words["full"].format(capacity=group.capacity))
    try:
        with transaction.atomic():  # the database's own rule (one live seat per student, group and day) is the backstop under the lock
            seat = MakeupSeat.objects.create(student=student, home_group=home, missed_on=missed, group=group, day=day, note=note, created_by=user)
    except IntegrityError:
        raise ValidationError(words["duplicate"])
    _log(user, "book_makeup_seat", seat, {})
    return seat


def _locked(seat):
    """The seat's row, under the lock of its target group (taken first, as a booking does, so the two never wait on each other)."""

    register.lock_group(seat.group)
    return MakeupSeat.objects.select_for_update().select_related("student", "home_group", "group").get(pk=seat.pk)


@transaction.atomic
def cancel(seat, user, lang="ar"):
    """Cancel a booked seat and free its place. Cancelling twice is not an error; a seat already marked attended or missed stays as it is."""

    words = _words(lang)
    if not can_book(user, seat.home_group, seat.group):
        raise PermissionDenied(words["forbidden"])
    seat = _locked(seat)
    if seat.status == SeatStatus.CANCELLED:
        return seat
    if seat.status != SeatStatus.BOOKED:
        raise ValidationError(words["attended_cancel"])
    before = _snapshot(seat)
    seat.status = SeatStatus.CANCELLED
    seat.save(update_fields=["status", "updated_at"])
    _log(user, "cancel_makeup_seat", seat, before)
    return seat


@transaction.atomic
def mark(seat, status, user, *, today=None, lang="ar"):
    """The teacher of the target group (or a manager) marks the guest ``attended`` or ``missed``; either can be corrected to the other.

    Refused: anyone else; a cancelled seat; a class still to come; a class that was cancelled on the register. It writes the seat and one
    audit row and nothing else: no ``Attendance`` row and no ``ClassSession``, so a per-class fee and a teacher's pay never move."""

    words = _words(lang)
    if status not in (SeatStatus.ATTENDED, SeatStatus.MISSED):
        raise ValidationError(words["bad_status"])
    if not can_mark(user, seat.group):
        raise PermissionDenied(words["forbidden_mark"])
    seat = _locked(seat)
    if seat.status == SeatStatus.CANCELLED:
        raise ValidationError(words["gone"])
    if seat.day > (today or timezone.localdate()):
        raise ValidationError(words["future"])
    if _cancelled_class(seat.group, seat.day):
        raise ValidationError(words["cancelled_class"])
    if seat.status != status:
        before = _snapshot(seat)
        seat.status = status
        seat.save(update_fields=["status", "updated_at"])
        _log(user, "attend_makeup_seat" if status == SeatStatus.ATTENDED else "miss_makeup_seat", seat, before)
    return seat


# ---- the card scan (called by ``scan.mark`` inside its transaction and under its group lock) ----

def seats_today(student, day, user):
    """The student's live seats on ``day`` in running groups of this entity that ``user`` may take attendance for, soonest class first."""

    rows = (live_seats().filter(student=student, day=day, group__in=services.scoped(StudyGroup.objects.filter(active=True)))
            .select_related("group__course", "group__teacher", "home_group").order_by("group__start_time", "group__name", "pk"))
    return [seat for seat in rows if can_mark(user, seat.group) and register.meets_on(seat.group, day)]


def attend_by_scan(seat, user, local, at_iso, lang="ar"):
    """Mark the guest attended because their card was scanned at the door, inside the scan's transaction and under the target group's lock.

    Returns ``(state, seat)``: ``"cancelled"`` when the class was cancelled (nothing written), ``"again"`` when they were already marked
    attended, else ``"ok"`` (and ``seat.was_missed`` says a «missed» mark was corrected). It writes the seat and its own audit row, whose
    action is ``scan_makeup_seat`` on a ``MakeupSeat``: never ``scan_attendance`` on a ``ClassSession``, which would flip the register into
    its «not scanned» mode, and never a ``ClassSession`` (a held one is what per-class teacher pay counts)."""

    seat = MakeupSeat.objects.select_for_update().select_related("student", "home_group", "group").get(pk=seat.pk)
    seat.was_missed = False
    if seat.status == SeatStatus.CANCELLED or seat.day != local.date():
        return "cancelled", seat
    if _cancelled_class(seat.group, seat.day):
        return "cancelled", seat
    if seat.status == SeatStatus.ATTENDED:
        return "again", seat
    before = _snapshot(seat)
    seat.was_missed = seat.status == SeatStatus.MISSED
    seat.status = SeatStatus.ATTENDED
    seat.save(update_fields=["status", "updated_at"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="education", action="scan_makeup_seat", object_type="education.MakeupSeat",
                            object_id=str(seat.pk), before_data=before, after_data={**_snapshot(seat), "at": at_iso})
    return "ok", seat


# ---- what a guardian may read ----

def for_children(kids, scope, today=None):
    """``{student id: [row, …]}`` for the guardian portal, inside ``scope`` (the link's entity), from two reads for all the children together.

    A booked seat from today onward (in a running group) and an attended or missed one of the last 30 days; never a cancelled seat, never
    another family's, never a note. A booked seat whose class was cancelled on the register is still listed, flagged ``cancelled``, so the
    guardian is not told to come to a class that will not happen. Booked seats come first, soonest first, then the past ones, newest first.
    Reads only: nothing here changes a seat (a past booked seat is left as it is, never turned into «missed» by being looked at)."""

    today = today or timezone.localdate()
    ids = [kid.pk for kid in kids]
    out = {kid.pk: [] for kid in kids}
    if not ids:
        return out
    coming = Q(status=SeatStatus.BOOKED, day__gte=today, group__active=True)
    past = Q(status__in=(SeatStatus.ATTENDED, SeatStatus.MISSED), day__gte=today - timedelta(days=PAST_DAYS - 1), day__lte=today)
    seats = list(MakeupSeat.objects.filter(student_id__in=ids, group__in=scope).filter(coming | past).select_related("group"))
    booked = [seat for seat in seats if seat.status == SeatStatus.BOOKED]
    cancelled = set(ClassSession.objects.filter(status=SessionStatus.CANCELLED, group_id__in={seat.group_id for seat in booked},
                                                date__in={seat.day for seat in booked}).values_list("group_id", "date")) if booked else set()
    rows = {pk: [] for pk in ids}
    for seat in seats:
        rows[seat.student_id].append({"day": seat.day, "time": seat.group.start_time.strftime("%H:%M") if seat.group.start_time else "", "group": seat.group.name,
                                      "status": seat.status, "cancelled": (seat.group_id, seat.day) in cancelled})
    for pk, items in rows.items():
        upcoming = sorted((item for item in items if item["status"] == SeatStatus.BOOKED), key=lambda item: (item["day"], item["time"], item["group"]))
        earlier = sorted((item for item in items if item["status"] != SeatStatus.BOOKED), key=lambda item: (item["day"], item["time"], item["group"]), reverse=True)
        out[pk] = upcoming + earlier
    return out
