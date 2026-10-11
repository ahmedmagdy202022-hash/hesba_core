"""EDU-004: how the school is doing: the dashboard panel and the month's report per group.

Read-only. Every figure comes from records already kept: enrolments,
attendance, the fee charges of EDU-003 (their invoices are ordinary sales) and
the teacher pay of ``pay.py``. Only the entity being worked in is counted (HG-034).
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from . import attendance, services
from .billing import _live, month_end
from .models import Attendance, ClassSession, Enrollment, EnrollmentStatus, FeeCharge, SessionStatus, Student, StudyGroup
from .pay import group_due

ZERO = Decimal("0")

WORDS = {
    "ar": {"school": "المركز النهارده", "students": "طلاب مستمرين", "classes": "حصص النهارده", "taken": "اتسجّل حضورها", "rate": "نسبة الحضور (30 يوم)",
           "billed": "مصروفات الشهر ده", "owing": "أولياء أمور عليهم فلوس", "of": "من", "none": "مفيش حصص النهارده", "open": "افتح"},
    "en": {"school": "The centre today", "students": "Active students", "classes": "Classes today", "taken": "attendance taken", "rate": "Attendance (30 days)",
           "billed": "Fees billed this month", "owing": "Parents who owe", "of": "of", "none": "No classes today", "open": "Open"},
}


def _scoped_marks():
    return Attendance.objects.filter(session__group__in=services.scoped(StudyGroup.objects))


def _billed(first):
    charges = _live(FeeCharge.objects.filter(month=first, enrollment__student__in=services.scoped(Student.objects)))
    return charges.aggregate(total=Sum("amount"))["total"] or ZERO


def panel_tiles(lang="ar", today=None, user=None):
    """The dashboard tiles for an education install: (tiles, lists)."""

    from permissions.services import user_has_permission

    from reports.activity_panel import _tile

    words = WORDS["en" if lang == "en" else "ar"]
    today = today or timezone.localdate()
    students = services.scoped(Student.objects).filter(active=True, enrollments__status=EnrollmentStatus.ACTIVE).distinct().count()
    groups = attendance.groups_on(today, user)
    taken = ClassSession.objects.filter(date=today, group__in=groups).count()
    rate = attendance.rate(_scoped_marks().filter(session__date__gte=today - timedelta(days=29), session__date__lte=today))
    tiles = [
        _tile("students", words["students"], students, tone="lead"),
        _tile("classes", words["classes"], f"{taken} {words['of']} {len(groups)}" if groups else "0", "text",
              tone="bad" if groups and taken < len(groups) else "", sub=words["taken"] if groups else words["none"]),
        _tile("attendance", words["rate"], f"{rate}%" if rate is not None else "—", "text", tone="bad" if rate is not None and rate < 75 else ""),
    ]
    if user is None or user_has_permission(user, "reports.view_all_sales_report"):  # the whole school's fees, not one cashier's (Codex on #189)
        tiles.append(_tile("billed", words["billed"], _billed(today.replace(day=1)), "money"))
    if user is None or user_has_permission(user, "reports.view_customer_report"):
        from .billing import dues

        from settings_core.templatetags.hesba_format import money

        owing = dues(today)
        tiles.append(_tile("owing", words["owing"], len(owing), tone="bad" if owing else "good",
                           sub=money(sum((row["total"] for row in owing), ZERO)) if owing else ""))
    return tiles, []


def group_report(first, with_pay=True):
    """The month per group: students, classes, attendance, fees billed, the teacher's pay and what is left."""

    last = month_end(first)
    rows = []
    groups = (services.scoped(StudyGroup.objects).filter(Q(starts_on__isnull=True) | Q(starts_on__lte=last))
              .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=first)).select_related("course", "teacher").order_by("course__name", "name"))
    for group in groups:
        running = Enrollment.objects.filter(group=group, start_date__lte=last).filter(Q(end_date__isnull=True) | Q(end_date__gte=first))
        students = running.values("student").distinct().count()
        held = ClassSession.objects.filter(group=group, status=SessionStatus.HELD, date__gte=first, date__lte=last).count()
        if not students and not held:
            continue
        billed = _live(FeeCharge.objects.filter(enrollment__group=group, month=first)).aggregate(total=Sum("amount"))["total"] or ZERO
        pay = group_due(group, first)
        teacher = pay["due"] if with_pay else ZERO
        rows.append({"group": group, "teacher_name": pay["teacher"].name if pay["teacher"] else (group.teacher.name if group.teacher else ""), "students": students, "held": held,
                     "rate": attendance.rate(Attendance.objects.filter(session__group=group, session__date__gte=first, session__date__lte=last)),
                     "billed": billed, "teacher": teacher, "left": billed - teacher})
    totals = {key: sum((row[key] for row in rows), ZERO) for key in ("billed", "teacher", "left")}
    totals["students"] = sum(row["students"] for row in rows)
    totals["held"] = sum(row["held"] for row in rows)
    totals["rate"] = attendance.rate(_scoped_marks().filter(session__date__gte=first, session__date__lte=last))
    return rows, totals
