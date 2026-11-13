"""E8 (HG-105) screens: a student's makeup seats, the booking page, and the one-tap changes (book, cancel, the guest attended or missed).

Who: whoever may take attendance for the home group or the target group may book and cancel; whoever may take the TARGET group's
attendance marks the guest (``education.makeup``; no permission was added). The order of the guards is the exam and content screens':
not an education install (404), then the record in the entity being worked in (404), then the permission (403). Every change is POST only
(a GET is a 405), CSRF protected, and goes through ``education.makeup``, which owns the rules. The redirect after a change is built here
from the seat, never from the request. Nothing here touches the register's own form or the way it saves, and nothing moves money.
"""

from datetime import date
from urllib.parse import quote

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import makeup, services
from .models import Enrollment, EnrollmentStatus, SeatStatus, Student, StudyGroup
from .views import _base, _lang

WORDS = {
    "ar": {"title": "حصص تعويضية", "book": "احجز حصة تعويضية", "none": "لسه مفيش حجوزات تعويضية.",
           "intro": "حجز مكان في حصة مجموعة تانية من نفس الكورس بدل حصة من المجموعة الأصلية. الحجز مش بيتحسب حضور ولا مصروفات ولا أجر مدرس.",
           "home": "المجموعة الأصلية", "missed_on": "يوم الحصة اللي بتتعوّض (اختياري)", "show": "اعرض الحصص", "pick_h": "اختار الحصة", "none_classes": "مفيش حصص في المجموعات التانية في الأسبوعين الجايين.",
           "no_home": "الطالب مش مسجّل في أي مجموعة شغالة، فمفيش حصة يتعوّضها.", "choose_home": "اختار المجموعة الأصلية الأول عشان تظهر الحصص.",
           "note": "ملاحظة (اختياري)", "save": "احجز المقعد", "booked_ok": "المقعد اتحجز في الحصة التعويضية.", "cancelled_ok": "الحجز اتلغى.", "attended_ok": "اتسجّل حضور الضيف.",
           "missed_ok": "اتسجّل غياب الضيف.", "cancel": "إلغاء الحجز", "attended": "حضر", "missed": "ماحضرش", "guests_h": "ضيوف الحصة (تعويضي)",
           "from_group": "من مجموعة", "class_cancelled": "حصة المجموعة اتلغت.", "held": "محجوزة بالفعل", "full": "مكتمل", "open": "مفتوحة", "back": "رجوع للطالب",
           "status": {"booked": "محجوز", "attended": "حضر", "missed": "ماحضرش", "cancelled": "اتلغى"}, "seats_n": "فاضل {n} مقاعد", "seats_1": "فاضل مقعد واحد",
           "seats_2": "فاضل مقعدين", "for": "حصة تعويضية"},
    "en": {"title": "Makeup classes", "book": "Book a makeup class", "none": "No makeup bookings yet.",
           "intro": "A seat in a class of another group of the same course, in place of a class of the home group. A booking is not attendance, fees or teacher pay.",
           "home": "Home group", "missed_on": "The class day being made up (optional)", "show": "Show the classes", "pick_h": "Choose the class",
           "none_classes": "No classes in the other groups in the next two weeks.", "no_home": "This student is not in any running group, so there is no class to make up.",
           "choose_home": "Choose the home group first so the classes appear.", "note": "Note (optional)", "save": "Book the seat", "booked_ok": "The seat was booked in the makeup class.",
           "cancelled_ok": "The booking was cancelled.", "attended_ok": "The guest was marked attended.", "missed_ok": "The guest was marked absent.", "cancel": "Cancel the booking",
           "attended": "Attended", "missed": "Absent", "guests_h": "Class guests (makeup)", "from_group": "from", "class_cancelled": "This group's class was cancelled.",
           "held": "Already booked", "full": "Full", "open": "Open", "back": "Back to the student",
           "status": {"booked": "Booked", "attended": "Attended", "missed": "Absent", "cancelled": "Cancelled"}, "seats_n": "{n} seats left", "seats_1": "1 seat left",
           "seats_2": "2 seats left", "for": "Makeup class"},
}


def _install():
    if not services.is_education_install():
        raise Http404("Makeup classes are for education activities.")


def _student(request, pk):
    _install()
    student = get_object_or_404(services.scoped(Student.objects).select_related("payer"), pk=pk)
    if not makeup.can_use(request.user, student):
        raise PermissionDenied(makeup.MESSAGES["en"]["forbidden"])
    return student


def _seat(request, pk, marking=False):
    _install()
    seat = get_object_or_404(makeup.scoped_seats().select_related("student", "home_group__course", "group__course", "group__teacher"), pk=pk)
    allowed = makeup.can_mark(request.user, seat.group) if marking else makeup.can_book(request.user, seat.home_group, seat.group)
    if not allowed:
        raise PermissionDenied(makeup.MESSAGES["en"]["forbidden_mark" if marking else "forbidden"])
    return seat


def _page(request, lang, **extra):
    return _base(request, lang, "students", mw=WORDS[lang], **extra)


def free_text(free, lang):
    """«فاضل 3 مقاعد» / «مكتمل»; a class with no capacity says it is open."""

    words = WORDS[lang]
    if free is None:
        return words["open"]
    return words["full"] if free == 0 else words["seats_1"] if free == 1 else words["seats_2"] if free == 2 else words["seats_n"].format(n=free)


def _labelled(seats, lang, user):
    words = WORDS[lang]
    for seat in seats:
        seat.status_label = words["status"][seat.status]
        seat.can_cancel = seat.status == SeatStatus.BOOKED and makeup.can_book(user, seat.home_group, seat.group)
    return seats


def student_card(request, student, lang):
    """The context the student page adds for its «حصص تعويضية» card: nothing (no card) unless there is a seat to show or this user may book one."""

    rows = makeup.for_student(student)
    can_book = makeup.can_use(request.user, student) and student.active
    if not rows and not can_book:
        return {}
    return {"mw": WORDS[lang], "makeup_card": True, "makeup_rows": _labelled(rows, lang, request.user), "makeup_can_book": can_book}


def label_guests(seats, lang):
    """The words for the seats a register lists as guests (the attendance sheet, or one class of today's list)."""

    for seat in seats:
        seat.status_label = WORDS[lang]["status"][seat.status]
    return seats


def _back(seat, lang, where):
    if where == "attendance":
        return redirect(f"{reverse('education:attendance', args=[seat.group_id])}?lang={lang}&date={seat.day.isoformat()}")
    if where == "today":
        return redirect(f"{reverse('education:today')}?lang={lang}&date={seat.day.isoformat()}")
    return redirect(f"{reverse('education:student', args=[seat.student_id])}?lang={lang}#makeup")


def _lines(student, lang):
    """The student's running groups (home choices), each with its course."""

    rows = (Enrollment.objects.filter(student=student, status=EnrollmentStatus.ACTIVE, group__active=True, group__in=services.scoped(StudyGroup.objects))
            .select_related("group__course", "group__teacher").order_by("group__course__name", "group__name", "pk"))
    return [row.group for row in rows]


def makeup_new(request, pk):
    """The booking page: choose the home group (and the missed day), then the class from the other groups' next 14 days."""

    student = _student(request, pk)
    lang = _lang(request)
    homes = _lines(student, lang)
    chosen = next((group for group in homes if str(group.pk) == request.GET.get("home")), homes[0] if len(homes) == 1 else None)
    missed = request.GET.get("missed") or ""
    try:
        date.fromisoformat(missed) if missed else None
    except ValueError:
        missed = ""
    rows = makeup.classes_for(student, chosen, request.user) if chosen is not None else []
    for row in rows:
        row["free_text"] = free_text(row["free"], lang)
    return render(request, "education/makeup_new.html", _page(request, lang, student=student, homes=homes, home=chosen, missed=missed, rows=rows, today=timezone.localdate(),
                                                              page_title=WORDS[lang]["book"]))


@require_POST
def makeup_book(request, pk):
    student = _student(request, pk)
    lang = _lang(request)
    home = get_object_or_404(services.scoped(StudyGroup.objects), pk=request.POST.get("home") or 0)
    where = f"{reverse('education:makeup_new', args=[student.pk])}?lang={lang}&home={home.pk}&missed={quote(request.POST.get('missed') or '')}"
    slot = (request.POST.get("slot") or "").partition("|")
    try:
        if not slot[0].isdecimal():
            raise ValidationError(makeup.MESSAGES[lang]["choose_class"])
        group = get_object_or_404(services.scoped(StudyGroup.objects), pk=int(slot[0]))
        makeup.book(student, home, group, slot[2], request.user, missed_on=request.POST.get("missed") or None, note=request.POST.get("note", ""), lang=lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(where)
    messages.success(request, WORDS[lang]["booked_ok"])
    return redirect(f"{reverse('education:student', args=[student.pk])}?lang={lang}#makeup")


def _change(request, pk, do, done, marking=False):
    seat = _seat(request, pk, marking=marking)
    lang = _lang(request)
    try:
        do(seat, request.user, lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, WORDS[lang][done])
    where = request.POST.get("back")
    return _back(seat, lang, where if where in ("attendance", "today") else "student")


@require_POST
def makeup_cancel(request, pk):
    return _change(request, pk, makeup.cancel, "cancelled_ok")


@require_POST
def makeup_attended(request, pk):
    return _change(request, pk, lambda seat, user, lang: makeup.mark(seat, SeatStatus.ATTENDED, user, lang=lang), "attended_ok", marking=True)


@require_POST
def makeup_missed(request, pk):
    return _change(request, pk, lambda seat, user, lang: makeup.mark(seat, SeatStatus.MISSED, user, lang=lang), "missed_ok", marking=True)
