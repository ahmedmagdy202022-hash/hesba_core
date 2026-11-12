"""E2: exams, marks and the report card for the guardian.

An exam belongs to one study group and one day; its marks are kept for the students on the group's roster *on that day*
(``attendance.roster_on``, the same rule attendance uses). Nothing here moves money, stock or a ledger.

Who: whoever may take the group's attendance (``attendance.can_take``: a manager, or the group's own teacher) enters and sees marks,
and a teacher sees the exams of their own groups only. A user who may merely look at student files (a cashier) sees no mark anywhere.
There is no rank and no function that reads another student's marks next to this one's: every read here is for one student.
No mark or rate is ever put in a message (this module does not import ``messaging``; a test scans it).
"""

import hashlib
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import attendance as register
from . import services
from .models import Attendance, Exam, ExamKind, ExamMark, Presence, SessionStatus, StudyGroup

OUT_OF_LIMIT = Decimal("100000")  # the column holds 5 digits before the point and 2 after

MESSAGES = {
    "ar": {"forbidden": "الامتحانات والدرجات لإدارة المركز ولمدرس المجموعة بس.", "title": "اكتب اسم الامتحان.", "kind": "اختار نوع الامتحان.",
           "date": "التاريخ مش مظبوط.", "out_of": "الدرجة النهائية لازم تكون رقم أكبر من صفر وأقل من 100000، وبكسرين عشريين على الأكتر.",
           "closed": "المجموعة دي مقفولة.", "day_locked": "مينفعش تغيّر يوم الامتحان بعد ما اتسجّلت عليه درجات.",
           "out_of_low": "فيه درجة مسجّلة {top}، أعلى من الدرجة النهائية الجديدة.", "future": "مينفعش تسجّل درجات لامتحان لسه ماجاش يومه.",
           "stale": "الدرجات اتغيّرت بعد ما فتحت الكشف (حد حفظ قبلك). راجع الكشف واحفظ تاني.",
           "not_on_roster": "الطالب ده مش في المجموعة في يوم الامتحان.", "score": "الدرجة لازم تكون رقم من صفر لحد {out_of}.",
           "decimals": "الدرجة بكسرين عشريين على الأكتر.", "absent_score": "الطالب غايب ومكتوب له درجة. امسح الدرجة أو شيل علامة غايب.",
           "range": "تاريخ البداية لازم يكون قبل تاريخ النهاية.", "no_range": "اختار فترة التقرير."},
    "en": {"forbidden": "Exams and marks are for the centre's managers and the group's own teacher only.", "title": "Enter the exam's name.",
           "kind": "Choose the kind of exam.", "date": "The date is not valid.",
           "out_of": "The full mark must be a number above zero and below 100000, with at most two decimals.", "closed": "This group is closed.",
           "day_locked": "The exam's day cannot be changed once marks are recorded on it.",
           "out_of_low": "A mark of {top} is already recorded; the new full mark cannot be lower.",
           "future": "Marks cannot be recorded for an exam whose day has not come.",
           "stale": "The marks changed after you opened the sheet (someone saved before you). Review the sheet and save again.",
           "not_on_roster": "This student was not in the group on the exam's day.", "score": "The mark must be a number from zero up to {out_of}.",
           "decimals": "The mark has at most two decimals.", "absent_score": "The student is marked absent but has a mark. Clear the mark or untick absent.",
           "range": "The start date must be before the end date.", "no_range": "Choose the report's period."},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


# ---- who ----

def can_see(user, group):
    """Marks of ``group``: a manager, or its own teacher. The general read permission on student files is not enough."""

    return register.can_take(user, group)


def can_publish(user):
    """Releasing marks to guardians is the centre's decision, not the teacher's (HG-091, open question 2)."""

    return services.can_manage(user)


def groups_for(user):
    """The groups (of the entity being worked in) whose marks ``user`` may see."""

    groups = services.scoped(StudyGroup.objects)
    if services.can_manage(user):
        return groups
    teacher = register.teacher_of(user)
    return groups.filter(teacher=teacher) if teacher else groups.none()


def can_see_student(user, student):
    """The marks card and the report card of one student: a manager, or a teacher of at least one of the student's groups."""

    if services.can_manage(user):
        return True
    return groups_for(user).filter(enrollments__student=student).exists()


def scoped_exams():
    return Exam.objects.filter(group__in=services.scoped(StudyGroup.objects))


# ---- numbers ----

def percent(score, out_of):
    """``score`` out of ``out_of`` as a percentage with one decimal, half-up (90.0, 75.0, 33.3): None without a score."""

    exact = _exact(score, out_of)
    return None if exact is None else exact.quantize(Decimal("0.1"), ROUND_HALF_UP)


def _exact(score, out_of):
    return None if score is None else Decimal(score) * 100 / Decimal(out_of)


def _parse_number(raw):
    try:
        value = Decimal(services._plain_number(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None  # "NaN" and "Infinity" parse but are not numbers here


def _out_of(raw, words):
    value = _parse_number(raw) if raw not in (None, "") else None
    if value is None or not Decimal("0") < value < OUT_OF_LIMIT or value != value.quantize(Decimal("0.01")):
        raise ValidationError(words["out_of"])
    return value.quantize(Decimal("0.01"))


def _score(raw, exam, name, words):
    """A typed mark: blank is "not entered", otherwise a number from 0 to the exam's full mark with at most two decimals."""

    if raw is None or str(raw).strip() == "":
        return None
    value = _parse_number(raw)
    full = f"{exam.out_of.normalize():f}"
    if value is None or value < 0 or value > exam.out_of:
        raise ValidationError(f"{name}: {words['score'].format(out_of=full)}")
    if value != value.quantize(Decimal("0.01")):
        raise ValidationError(f"{name}: {words['decimals']}")
    return value.quantize(Decimal("0.01"))


def _day(raw, words):
    if hasattr(raw, "year"):
        return raw
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError:
        raise ValidationError(words["date"])


# ---- the exam ----

def _snapshot(exam):
    return {"group": exam.group.code, "title": exam.title, "kind": exam.kind, "held_on": exam.held_on.isoformat(),
            "out_of": f"{exam.out_of:f}", "published": exam.published, "notes": exam.notes}


@transaction.atomic
def save_exam(group, data, user, exam=None, lang="ar"):
    """Create an exam of ``group``, or change ``exam``. ``data``: title, kind, held_on, out_of, notes.

    Whoever may take the group's attendance. A new exam needs a running group; the day may be one still to come (the paper is announced
    before it is sat), but marks wait for the day. Once marks exist the day is fixed (it decides the roster) and the full mark cannot
    fall below a mark already given."""

    words = _words(lang)
    if not register.can_take(user, group):
        raise PermissionDenied(words["forbidden"])
    title = (data.get("title") or "").strip()
    if not title:
        raise ValidationError(words["title"])
    kind = data.get("kind") or ExamKind.QUIZ
    if kind not in ExamKind.values:
        raise ValidationError(words["kind"])
    held_on = _day(data.get("held_on"), words)
    out_of = _out_of(data.get("out_of"), words)
    notes = (data.get("notes") or "").strip()[:255]
    if exam is None:
        if not group.active:
            raise ValidationError(words["closed"])
        exam = Exam(group=group, created_by=user)
        before = {}
    else:
        exam = Exam.objects.select_for_update().select_related("group").get(pk=exam.pk)
        before = _snapshot(exam)
        marks = exam.marks.all()
        if marks.exists():
            if held_on != exam.held_on:
                raise ValidationError(words["day_locked"])
            top = marks.exclude(score=None).order_by("-score").values_list("score", flat=True).first()
            if top is not None and out_of < top:
                raise ValidationError(words["out_of_low"].format(top=f"{top.normalize():f}"))
    exam.title, exam.kind, exam.held_on, exam.out_of, exam.notes = title[:160], kind, held_on, out_of, notes
    created = exam.pk is None
    exam.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if created else AuditEventType.UPDATE, actor=user, module="education", action="save_exam",
                            object_type="education.Exam", object_id=str(exam.pk), before_data=before, after_data=_snapshot(exam))
    return exam


@transaction.atomic
def set_published(exam, published, user, lang="ar"):
    """Release (or take back) an exam's marks for guardians. Only the centre's managers; it changes no mark."""

    if not can_publish(user):
        raise PermissionDenied(_words(lang)["forbidden"])
    exam = Exam.objects.select_for_update().select_related("group").get(pk=exam.pk)
    if exam.published != bool(published):
        before = _snapshot(exam)
        exam.published = bool(published)
        exam.save(update_fields=["published"])
        AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="education", action="publish_exam" if published else "unpublish_exam",
                                object_type="education.Exam", object_id=str(exam.pk), before_data=before, after_data=_snapshot(exam))
    return exam


def roster_of(exam):
    """The enrolments that were running on the exam's day: the only students it can have marks for."""

    return register.roster_on(exam.group, exam.held_on)


def _state(mark):
    return None if mark is None else {"score": None if mark.score is None else f"{mark.score:f}", "absent": mark.absent, "note": mark.note}


def sheet_version(exam, marks=None):
    """A short digest of everything saving the sheet would overwrite: the exam's full mark and every mark with its absence and note.

    The sheet carries it as a hidden field; a save whose digest no longer matches the database, read under the exam's lock, was made from a
    stale page (someone saved meanwhile) and is refused, never merged. ``marks``: the rows the page was drawn from, so the digest describes
    exactly what it shows; the save passes nothing and reads the database."""

    rows = exam.marks.all() if marks is None else marks
    body = sorted((m.student_id, None if m.score is None else f"{m.score.normalize():f}", m.absent, m.note) for m in rows)
    return hashlib.sha256(json.dumps([f"{exam.out_of.normalize():f}", body], ensure_ascii=False).encode()).hexdigest()[:16]


@transaction.atomic
def record_marks(exam, entries, user, *, version=None, lang="ar"):
    """Save marks for students of the exam's roster. ``entries``: ``{student id: {"score": typed text, "absent": bool, "note": text}}``.

    Everything is checked before anything is written. A blank score with absent off is "not entered yet" (any earlier row is removed, a
    note alone is kept); a correction is audited with the mark before and after. ``version``: the digest the page was drawn from; None
    skips the check (services and the demo), "" is a page that carried none, which is stale as soon as any mark exists.
    Returns ``{"changed", "entered", "seats"}``."""

    words = _words(lang)
    if not register.can_take(user, exam.group):
        raise PermissionDenied(words["forbidden"])
    exam = Exam.objects.select_for_update().select_related("group").get(pk=exam.pk)
    if exam.held_on > timezone.localdate():
        raise ValidationError(words["future"])
    existing = {mark.student_id: mark for mark in exam.marks.all()}
    if version is not None:
        current = sheet_version(exam, existing.values())
        if (current != sheet_version(exam, [])) if not version else (version != current):
            raise ValidationError(words["stale"], code="stale")
    roster = {row.student_id: row.student for row in roster_of(exam)}
    plan = []
    for raw_id, entry in entries.items():
        student = roster.get(int(raw_id)) if str(raw_id).isdigit() else None
        if student is None:
            raise ValidationError(words["not_on_roster"])
        score = _score(entry.get("score"), exam, student.name, words)
        absent = bool(entry.get("absent"))
        if absent and score is not None:
            raise ValidationError(f"{student.name}: {words['absent_score']}")
        plan.append((student, score, absent, (entry.get("note") or "").strip()[:255]))
    before, after = {}, {}
    for student, score, absent, note in plan:
        mark = existing.get(student.pk)
        wanted = {"score": None if score is None else f"{score:f}", "absent": absent, "note": note} if (score is not None or absent or note) else None
        if _state(mark) == wanted:
            continue
        before[student.code], after[student.code] = _state(mark), wanted
        if wanted is None:
            mark.delete()
        elif mark is None:
            ExamMark.objects.create(exam=exam, student=student, score=score, absent=absent, note=note, entered_by=user)
        else:
            mark.score, mark.absent, mark.note, mark.entered_by = score, absent, note, user
            mark.save()
    entered = exam.marks.filter(Q(score__isnull=False) | Q(absent=True)).count()
    if after:
        AuditLog.objects.create(event_type=AuditEventType.UPDATE if any(before.values()) else AuditEventType.CREATE, actor=user, module="education",
                                action="record_marks", object_type="education.Exam", object_id=str(exam.pk), before_data={"marks": before},
                                after_data={"marks": after, "exam": exam.title, "group": exam.group.code, "entered": entered, "seats": len(roster)})
    return {"changed": len(after), "entered": entered, "seats": len(roster)}


# ---- reading ----

def group_exams(group, user, limit=None):
    """The group's exams, newest first, each with how many marks are entered out of the roster on its day. Nothing for who may not see them."""

    if not can_see(user, group):
        return []
    rows = list(scoped_exams().filter(group=group).annotate(entered=Count("marks", filter=Q(marks__score__isnull=False) | Q(marks__absent=True))))
    rows = rows[:limit] if limit else rows
    for exam in rows:
        exam.seats = len(roster_of(exam))
    return rows


def _status(mark):
    if mark is not None and mark.absent:
        return "absent"
    return "scored" if mark is not None and mark.score is not None else "pending"


def _student_rows(student, user, first=None, last=None):
    """One row per exam this student sat or was to sit: an exam of a group they were on the roster of that day, in a group ``user`` may see."""

    own = Q(group__enrollments__student=student, group__enrollments__start_date__lte=F("held_on")) & (
        Q(group__enrollments__end_date__isnull=True) | Q(group__enrollments__end_date__gte=F("held_on")))
    exams = scoped_exams().filter(own, group__in=groups_for(user)).select_related("group__course").distinct()
    if first is not None:
        exams = exams.filter(held_on__gte=first)
    if last is not None:
        exams = exams.filter(held_on__lte=last)
    exams = list(exams)
    marks = {mark.exam_id: mark for mark in ExamMark.objects.filter(student=student, exam__in=exams)}
    rows = []
    for exam in exams:
        mark = marks.get(exam.pk)
        exact = _exact(mark.score, exam.out_of) if mark is not None else None
        rows.append({"exam": exam, "mark": mark, "status": _status(mark), "score": mark.score if mark is not None else None,
                     "percent": None if exact is None else exact.quantize(Decimal("0.1"), ROUND_HALF_UP), "exact": exact,
                     "score_text": "" if mark is None or mark.score is None else f"{mark.score.normalize():f}", "out_text": f"{exam.out_of.normalize():f}"})
    return rows


def student_marks(student, user, limit=None):
    """The student's exams, newest first, for ``user`` (a manager: all; a teacher: their own groups'). Empty for anyone else."""

    rows = _student_rows(student, user)
    rows.sort(key=lambda row: (row["exam"].held_on, row["exam"].pk), reverse=True)
    return rows[:limit] if limit else rows


def report_card(student, first, last, user, lang="ar"):
    """One student's period: attendance, each exam with its score and percentage, and the average of the percentages.

    The average is the mean of the exams' percentages over the exams the student sat (a score entered), rounded once half-up to one
    decimal; an absent exam is listed and left out; an exam with no score entered is left out and counted in ``not_entered``. For a
    teacher, only their own groups' exams and classes. Attendance uses ``attendance.rate`` (late counts as attended, whole percent)."""

    words = _words(lang)
    if not can_see_student(user, student):
        raise PermissionDenied(words["forbidden"])
    if first > last:
        raise ValidationError(words["range"])
    held = Attendance.objects.filter(student=student, session__date__range=(first, last), session__group__in=groups_for(user), session__status=SessionStatus.HELD)
    counts = dict(held.values_list("presence").annotate(n=Count("id")))
    attendance = {"held": sum(counts.values()), "present": counts.get(Presence.PRESENT, 0), "late": counts.get(Presence.LATE, 0),
                  "absent": counts.get(Presence.ABSENT, 0), "excused": counts.get(Presence.EXCUSED, 0), "rate": register.rate(held)}
    rows = _student_rows(student, user, first, last)
    rows.sort(key=lambda row: (row["exam"].held_on, row["exam"].pk))
    sat = [row["exact"] for row in rows if row["status"] == "scored"]
    average = (sum(sat) / len(sat)).quantize(Decimal("0.1"), ROUND_HALF_UP) if sat else None
    return {"student": student, "first": first, "last": last, "attendance": attendance, "rows": rows, "average": average, "sat": len(sat),
            "absent_exams": sum(1 for row in rows if row["status"] == "absent"), "not_entered": sum(1 for row in rows if row["status"] == "pending")}
