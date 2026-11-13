"""E6: the completion certificate (training and language centres).

A certificate is issued for one enrolment that has finished: it was stopped (a student who left, or moved up a level), or the group's
term has ended. What it says is measured once, at issue, and kept on the row:

* ``hours``: the classes *held* while the student was enrolled, times the group's class length, divided by 60;
* ``attendance_rate``: ``attendance.rate`` over that student's marks in that group and span (late counts as attended);
* ``grade_text``: typed by the manager; offered from the group's **published** final exam when the student has a score on it.

This module only *reads* attendance, sessions and marks (through ``attendance`` and ``exams``); it moves no money, stock or ledger,
sends no message, and adds no permission: issuing, revoking, the list and the print are for whoever manages students
(``master_data.manage_parties``), because the list shows grades and a cashier may not see a mark.

The public side is a signed token with its own salt (``hesba.education.certificate.v1``). It names the certificate's id and number, has
no expiry (paper does not expire) and is checked against the row; nothing in it can be guessed from the printed running number. The
public page (``education.public``) prints only what the certificate prints, and never the reason a certificate was revoked.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import attendance as register
from . import billing, exams, levels, services
from .models import Attendance, Certificate, ClassSession, Enrollment, EnrollmentStatus, ExamKind, SessionStatus, StudyGroup

SALT = "hesba.education.certificate.v1"
ATTENDANCE_WARN = 75  # a rate under this is a warning (a constant: the owner's open question 7, HG-099)
GRADE_LIMIT = 40
REASON_MIN = 3

MESSAGES = {
    "ar": {"forbidden": "الشهادات لإدارة المركز بس.", "not_levels": "الشهادات للمراكز التدريبية ومراكز اللغات بس.",
           "not_finished": "الطالب لسه مكمّل في المجموعة ومدتها ما خلصتش، فمينفعش تتصدر له شهادة دلوقتي. أوقف تسجيله أو استنى لحد ما تخلص.",
           "no_classes": "مفيش حصة اتعملت للطالب في المجموعة دي، فمفيش حاجة تتشهد بيها.",
           "warn_attendance": "نسبة حضور الطالب {rate}%، وده أقل من {limit}%.", "warn_owes": "على ولي الأمر مبلغ مستحق لسه ماتدفعش.",
           "confirm": "فيه تنبيه فوق. لو متأكد إنك عايز تصدر الشهادة، علّم «أصدرها برضو» واضغط إصدار.",
           "grade": "التقدير أطول من {limit} حرف.", "reason": "اكتب سبب الإلغاء (3 حروف على الأقل).",
           "revoked": "الشهادة دي ملغية بالفعل.", "existing": "للتسجيل ده شهادة سارية بالفعل، دي هي.",
           "unknown": "التسجيل ده مش موجود."},
    "en": {"forbidden": "Certificates are for the centre's managers only.", "not_levels": "Certificates are for training and language centres only.",
           "not_finished": "The student is still enrolled and the group's term has not ended, so no certificate can be issued yet. Stop the enrolment, or wait until the term ends.",
           "no_classes": "No class was held for the student in this group, so there is nothing to certify.",
           "warn_attendance": "The student's attendance is {rate}%, below {limit}%.", "warn_owes": "The guardian still owes an unpaid balance.",
           "confirm": "There is a warning above. If you are sure, tick «Issue it anyway» and press Issue.",
           "grade": "The grade is longer than {limit} characters.", "reason": "Enter the reason for revoking (at least 3 characters).",
           "revoked": "This certificate is already revoked.", "existing": "This enrolment already has a live certificate; here it is.",
           "unknown": "This enrolment does not exist."},
}


class NeedsConfirmation(ValidationError):
    """The warnings that stop the first issue: the screen shows them and offers «issue it anyway»."""


def words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


# ---- who and where ----

def enabled():
    """Training and language centres only (the plan's open question 7): a tutoring centre or a nursery has no certificate."""

    return services.is_education_install() and levels.is_levels_install()


def can_manage(user):
    return services.can_manage(user)


def scoped():
    """The certificates of the entity being worked in: those of an enrolment in one of its groups."""

    return Certificate.objects.filter(enrollment__group__in=services.scoped(StudyGroup.objects))


def scoped_enrollments():
    return Enrollment.objects.filter(group__in=services.scoped(StudyGroup.objects))


# ---- what a certificate measures ----

def finished(enrollment, today=None):
    """Stopped, or the group's term has ended (its last day included)."""

    today = today or timezone.localdate()
    return enrollment.status == EnrollmentStatus.STOPPED or (enrollment.group.ends_on is not None and enrollment.group.ends_on <= today)


def span(enrollment, today=None):
    """(first, last) day the student was on the roster: from the later of the enrolment's start and the group's start, to the day the
    enrolment stopped, else the group's last day, never past ``today`` or the group's last day. ``last`` may precede ``first``: nothing held."""

    today = today or timezone.localdate()
    group = enrollment.group
    first = max(enrollment.start_date, group.starts_on) if group.starts_on else enrollment.start_date
    last = enrollment.end_date if enrollment.status == EnrollmentStatus.STOPPED and enrollment.end_date else group.ends_on
    last = min(day for day in (last, group.ends_on, today) if day is not None)
    return first, last


def measure(enrollment, today=None):
    """{"first", "last", "held", "hours", "rate"} for the enrolment: held classes, the hours they make, and the attendance rate."""

    first, last = span(enrollment, today)
    group = enrollment.group
    held = ClassSession.objects.filter(group=group, status=SessionStatus.HELD, date__range=(first, last)).count() if first <= last else 0
    hours = (Decimal(held) * group.duration_minutes / 60).quantize(Decimal("0.01"), ROUND_HALF_UP)
    marks = Attendance.objects.filter(student=enrollment.student, session__group=group, session__date__range=(first, last))
    return {"first": first, "last": last, "held": held, "hours": hours, "rate": register.rate(marks) if held else None}


def suggested_grade(enrollment, user):
    """The grade offered from the group's latest **published** final exam the student has a score on, as ``"85.0%"``; "" when none.

    Only a published exam is offered: an unpublished mark is the centre's own until released, and a certificate's grade is shown on a public
    page. It is read through ``exams.student_marks`` and the manager types the final text, so it is a suggestion, never a condition."""

    rows = [row for row in exams.student_marks(enrollment.student, user)
            if row["exam"].group_id == enrollment.group_id and row["exam"].kind == ExamKind.FINAL and row["exam"].published and row["status"] == "scored"]
    return f"{rows[0]['percent']}%" if rows else ""  # student_marks lists the newest exam first


def owes(enrollment):
    """Whether the student's payer has a balance on the dues list (read from ``billing.dues``, the figure the dues screen shows)."""

    payer = enrollment.student.payer_id
    return any(row["party"].pk == payer for row in billing.dues())


def warnings(enrollment, measured, lang="ar"):
    """The warnings (never a refusal) that need a second look before issuing: attendance under 75%, or a guardian who still owes."""

    text, found = words(lang), []
    if measured["rate"] is not None and measured["rate"] < ATTENDANCE_WARN:
        found.append(text["warn_attendance"].format(rate=measured["rate"], limit=ATTENDANCE_WARN))
    if owes(enrollment):
        found.append(text["warn_owes"])
    return found


def refusal(enrollment, measured, today=None, lang="ar"):
    """The message that stops a certificate, or "": not finished, or nothing held."""

    text = words(lang)
    if not finished(enrollment, today):
        return text["not_finished"]
    if not measured["held"]:
        return text["no_classes"]
    return ""


def live_for(enrollment):
    return Certificate.objects.filter(enrollment=enrollment, revoked_at__isnull=True).first()


def preview(enrollment, user, lang="ar"):
    """What the issue screen shows before anything is written."""

    measured = measure(enrollment)
    live = live_for(enrollment)
    problem = "" if live else refusal(enrollment, measured, lang=lang)
    return {"measure": measured, "live": live, "refusal": problem, "grade": suggested_grade(enrollment, user),
            "warnings": [] if live or problem else warnings(enrollment, measured, lang)}


def ready(group=None, limit=100):
    """Finished enrolments with no live certificate, the most recently finished first: the queue the issue screen starts from."""

    today = timezone.localdate()
    live = Certificate.objects.filter(enrollment=OuterRef("pk"), revoked_at__isnull=True)
    rows = (scoped_enrollments().filter(Q(status=EnrollmentStatus.STOPPED) | Q(group__ends_on__lte=today)).exclude(Exists(live))
            .select_related("student", "group__course"))
    if group is not None:
        rows = rows.filter(group=group)
    return list(rows.annotate(finished_on=Coalesce("end_date", "group__ends_on")).order_by("-finished_on", "student__name", "pk")[:limit])


# ---- issuing and revoking ----

def _audit(user, action, certificate, event, after=None, before=None):
    AuditLog.objects.create(event_type=event, actor=user, module="education", action=action, object_type="education.Certificate",
                            object_id=str(certificate.pk), before_data=before or {}, after_data=after or {})


@transaction.atomic
def issue(enrollment, user, *, grade_text=None, acknowledged=False, lang="ar", today=None):
    """Issue the enrolment's certificate. Returns ``(certificate, created)``; an enrolment with a live certificate gets that one back.

    ``grade_text``: None takes the suggestion from the group's published final exam, a string (even "") is what the manager typed.
    A warning (attendance under 75%, a guardian who owes) stops the first call; ``acknowledged`` issues anyway. Everything is read under
    a lock on the enrolment, so two clicks at once make one certificate."""

    text = words(lang)
    if not can_manage(user):
        raise PermissionDenied(text["forbidden"])
    if not enabled():
        raise ValidationError(text["not_levels"])
    enrollment = Enrollment.objects.select_for_update().filter(pk=enrollment.pk, group__in=services.scoped(StudyGroup.objects)).first()
    if enrollment is None:
        raise ValidationError(text["unknown"])
    live = live_for(enrollment)
    if live is not None:
        return live, False
    today = today or timezone.localdate()
    measured = measure(enrollment, today)
    problem = refusal(enrollment, measured, today, lang)
    if problem:
        raise ValidationError(problem)
    grade = suggested_grade(enrollment, user) if grade_text is None else str(grade_text).strip()
    if len(grade) > GRADE_LIMIT:
        raise ValidationError(text["grade"].format(limit=GRADE_LIMIT))
    found = warnings(enrollment, measured, lang)
    if found and not acknowledged:
        raise NeedsConfirmation([*found, text["confirm"]])
    values = dict(enrollment=enrollment, issued_on=today, hours=measured["hours"], attendance_rate=measured["rate"], grade_text=grade, issued_by=user)
    try:
        certificate = services._create_numbered(Certificate, "number", f"CRT-{today.year}-", **values)
    except IntegrityError:  # the one live certificate per enrolment: a click that lost the race to another gets the winner
        live = live_for(enrollment)
        if live is None:
            raise
        return live, False
    _audit(user, "issue_certificate", certificate, AuditEventType.CREATE,
           after={"number": certificate.number, "student": enrollment.student.code, "student_name": enrollment.student.name,
                  "course": enrollment.group.course.name, "group": enrollment.group.code, "hours": f"{measured['hours']:f}",
                  "attendance_rate": measured["rate"], "grade": grade, "warned": bool(found), "first": measured["first"].isoformat(),
                  "last": measured["last"].isoformat()})
    return certificate, True


@transaction.atomic
def revoke(certificate, reason, user, lang="ar"):
    """Revoke a certificate with a reason. The row stays, so its number answers the verify page with «revoked»; revoking twice is refused."""

    text = words(lang)
    if not can_manage(user):
        raise PermissionDenied(text["forbidden"])
    reason = (reason or "").strip()
    if len(reason) < REASON_MIN:
        raise ValidationError(text["reason"])
    certificate = Certificate.objects.select_for_update().get(pk=certificate.pk)
    if certificate.revoked:
        raise ValidationError(text["revoked"])
    certificate.revoked_at, certificate.revoked_by, certificate.revoke_reason = timezone.now(), user, reason[:255]
    certificate.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
    _audit(user, "revoke_certificate", certificate, AuditEventType.UPDATE, before={"revoked": False},
           after={"number": certificate.number, "revoked": True, "reason": certificate.revoke_reason})
    return certificate


# ---- what the sheet and the verify page print ----

def course_title(certificate):
    """The course as the sheet prints it, and as the verify page repeats it: the course's name, and the group's level when it has one."""

    group = certificate.enrollment.group
    return group.course.name, group.level


def period(certificate):
    """(first, last) the sheet prints, read as of the day it was issued so a later stop of the enrolment cannot move it."""

    return span(certificate.enrollment, certificate.issued_on)


def business(certificate, lang="ar"):
    """The issuing business: the group's own entity when the installation has several, else the company's name."""

    from entities.services import is_multi_entity
    from printing.company import company_details

    entity = certificate.enrollment.group.entity
    return entity.display(lang) if entity is not None and is_multi_entity() else company_details()["name"]


# ---- the signed token on the sheet's QR ----

def make_token(certificate):
    """Signed with the SECRET_KEY and its own salt; names the id and the number; no expiry. Not guessable from the running number."""

    return signing.dumps({"c": int(certificate.pk), "n": certificate.number}, salt=SALT, compress=True)


def read_token(token):
    """The certificate the token names, or None for anything forged, altered, of another salt, or naming a number that is not the row's."""

    try:
        data = signing.loads(token, salt=SALT)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("c"), int) or not isinstance(data.get("n"), str):
        return None
    certificate = Certificate.objects.select_related("enrollment__student", "enrollment__group__course", "enrollment__group__entity").filter(pk=data["c"]).first()
    return certificate if certificate is not None and certificate.number == data["n"] else None


def verify_path(certificate):
    return reverse("certificate_verify", args=[make_token(certificate)])


def verify_url(certificate, lang="ar"):
    """The address the QR carries, from ``settings.PUBLIC_BASE_URL`` only (never the request's host, the standing rule of ``printing.share``);
    None when it is not set: a link with no address is not one to print."""

    base = getattr(settings, "PUBLIC_BASE_URL", "")
    if not base:
        return None
    return f"{base.rstrip('/')}{verify_path(certificate)}?lang={'en' if lang == 'en' else 'ar'}"
