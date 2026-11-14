"""E2 screens: a group's exams, the mark sheet, and a student's report card.

Every page and every POST here is for whoever may take the group's attendance (a manager, or the group's own teacher); the report card
is for a manager or a teacher of one of the student's groups. The order of the guards is the attendance sheet's: not an education
install (404), then the record in the entity being worked in (404), then the permission (403). Nothing here sends a message.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from printing.company import company_details

from . import attendance as register
from . import exams, nursery, services
from .models import ExamKind, Student, StudyGroup
from .views import _base, _lang

WORDS = {
    "ar": {"exams": "الامتحانات", "new_exam": "امتحان جديد", "all_exams": "كل الامتحانات", "no_exams": "لسه مفيش امتحانات للمجموعة دي.",
           "title": "اسم الامتحان", "kind": "النوع", "held_on": "يوم الامتحان", "out_of": "الدرجة النهائية", "notes": "ملاحظات", "save_exam": "احفظ الامتحان",
           "exam_saved": "الامتحان اتحفظ.", "marks_saved": "الدرجات اتحفظت.", "marks": "الدرجات", "of_roster": "من", "published": "منشور", "draft": "غير منشور",
           "publish": "انشر الدرجات", "unpublish": "ارجّع الامتحان غير منشور",
           "publish_hint": "المنشور بس هو اللي هيظهر لولي الأمر لما بوابته تشتغل. الدرجات مبتتبعتش في رسائل.", "sheet": "كشف الدرجات", "score": "الدرجة",
           "absent": "غايب", "note": "ملاحظة", "save_marks": "احفظ الدرجات", "entered_of": "اتسجّل {n} من {m}", "no_roster": "مفيش طلاب في المجموعة في يوم الامتحان.",
           "future": "الامتحان لسه ماجاش يومه؛ الدرجات بتتسجّل من يومه.", "edit_exam": "تعديل بيانات الامتحان", "back_group": "رجوع للمجموعة",
           "back_exams": "رجوع للامتحانات", "back_student": "رجوع للطالب", "date": "اليوم", "exam": "الامتحان", "percent": "النسبة", "latest": "آخر الامتحانات",
           "print_report": "اطبع تقرير", "no_marks": "لسه مفيش امتحانات للطالب ده.", "students": "الطلاب", "open": "افتح الكشف", "group": "المجموعة",
           "pending": "لسه ماتسجّلتش", "kinds": {"quiz": "كويز", "monthly": "امتحان الشهر", "midterm": "نص الترم", "final": "نهاية الترم", "other": "تاني"},
           "report": "تقرير الطالب", "period": "الفترة", "from": "من", "to": "إلى", "show": "اعرض", "print": "اطبع", "this_month": "الشهر ده",
           "last_month": "الشهر اللي فات", "last_30": "آخر 30 يوم", "attendance": "الحضور", "held": "حصص اتعملت", "present": "حاضر", "late": "متأخر",
           "excused": "بعذر", "rate": "نسبة الحضور", "average": "متوسط النسب", "average_note": "متوسط نسب الامتحانات اللي الطالب دخلها بس. الغايب واللي لسه ماتسجّلتش مش محسوبين.",
           "sat": "امتحانات دخلها", "absent_exams": "امتحانات غاب عنها", "not_entered": "لسه ماتسجّلتش", "no_exams_period": "مفيش امتحانات في الفترة دي.",
           "no_classes": "مفيش حصص اتسجّلت في الفترة دي.", "pdf_hint": "من نافذة الطباعة تقدر تحفظه PDF.", "code": "الكود", "stage": "السنة",
           "note_staff": "تقرير من المركز للطباعة أو الحفظ PDF.", "back": "رجوع", "printed_on": "اتطبع يوم", "teacher": "المدرس"},
    "en": {"exams": "Exams", "new_exam": "New exam", "all_exams": "All exams", "no_exams": "No exams for this group yet.",
           "title": "Exam name", "kind": "Kind", "held_on": "Exam day", "out_of": "Full mark", "notes": "Notes", "save_exam": "Save the exam",
           "exam_saved": "Exam saved.", "marks_saved": "Marks saved.", "marks": "Marks", "of_roster": "of", "published": "Published", "draft": "Not published",
           "publish": "Publish the marks", "unpublish": "Take it back to unpublished",
           "publish_hint": "Only published exams will show to the guardian once their portal exists. Marks are never sent in messages.", "sheet": "Mark sheet",
           "score": "Mark", "absent": "Absent", "note": "Note", "save_marks": "Save marks", "entered_of": "{n} of {m} entered",
           "no_roster": "No students were in the group on the exam's day.", "future": "This exam's day has not come; marks are entered from that day.",
           "edit_exam": "Edit the exam's details", "back_group": "Back to the group", "back_exams": "Back to the exams", "back_student": "Back to the student",
           "date": "Day", "exam": "Exam", "percent": "Percent", "latest": "Latest exams", "print_report": "Print a report", "no_marks": "No exams for this student yet.",
           "students": "Students", "open": "Open the sheet", "group": "Group", "pending": "Not entered yet",
           "kinds": {"quiz": "Quiz", "monthly": "Monthly exam", "midterm": "Midterm", "final": "Final", "other": "Other"},
           "report": "Student report", "period": "Period", "from": "From", "to": "To", "show": "Show", "print": "Print", "this_month": "This month",
           "last_month": "Last month", "last_30": "Last 30 days", "attendance": "Attendance", "held": "Classes held", "present": "Present", "late": "Late",
           "excused": "Excused", "rate": "Attendance rate", "average": "Average", "average_note": "The average of the percentages of the exams the student sat. Absences and marks not entered yet are left out.",
           "sat": "Exams sat", "absent_exams": "Exams missed", "not_entered": "Not entered yet", "no_exams_period": "No exams in this period.",
           "no_classes": "No classes were recorded in this period.", "pdf_hint": "In the print window you can save it as a PDF.", "code": "Code", "stage": "Stage",
           "note_staff": "A report from the centre, to print or save as a PDF.", "back": "Back", "printed_on": "Printed on", "teacher": "Teacher"},
}


def _install():
    if not services.is_education_install():
        raise Http404("Exams are for education activities.")


def _pk(raw):
    return int(raw) if str(raw or "").isdigit() else 0


def _group(request, pk):
    _install()
    group = get_object_or_404(services.scoped(StudyGroup.objects).select_related("course", "teacher"), pk=pk)
    if not exams.can_see(request.user, group):
        raise PermissionDenied("Exams and marks are for managers and the group's own teacher.")
    return group


def _exam(request, pk):
    _install()
    exam = get_object_or_404(exams.scoped_exams().select_related("group__course", "group__teacher"), pk=pk)
    if not exams.can_see(request.user, exam.group):
        raise PermissionDenied("Exams and marks are for managers and the group's own teacher.")
    return exam


def _page(request, lang, **extra):
    """The shared context. These screens carry no section tabs (a teacher who may not open the school's lists reaches them from today's classes)."""

    return _base(request, lang, "groups", xw=WORDS[lang], kinds=[(value, WORDS[lang]["kinds"][value]) for value in ExamKind.values],
                 can_publish=exams.can_publish(request.user), today=timezone.localdate(), **extra)


def _labelled(rows, lang):
    for row in rows:
        row.kind_label = WORDS[lang]["kinds"][row.kind]
    return rows


def group_card(request, group, lang, limit=5):
    """The context the group page adds for its exams card: nothing at all (no card) unless this user may see this group's marks."""

    if not exams.can_see(request.user, group):
        return {}
    return {"xw": WORDS[lang], "exam_rows": _labelled(exams.group_exams(group, request.user, limit), lang), "exams_card": True}


def student_card(request, student, lang, limit=5):
    """The context the student page adds for its marks card: nothing (no card) unless a manager, or a teacher of one of the student's groups."""

    if nursery.is_nursery() or not exams.can_see_student(request.user, student):  # a nursery has no exams: no marks card on a child's file
        return {}
    return {"xw": WORDS[lang], "marks_rows": exams.student_marks(student, request.user, limit), "marks_card": True}


def group_exams(request, pk):
    group = _group(request, pk)
    lang = _lang(request)
    return render(request, "education/exams.html", _page(request, lang, group=group, rows=_labelled(exams.group_exams(group, request.user), lang),
                                                           page_title=WORDS[lang]["exams"]))


def exam_new(request, pk):
    group = _group(request, pk)
    lang = _lang(request)
    error = ""
    if request.method == "POST":
        try:
            exam = exams.save_exam(group, request.POST, request.user, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, WORDS[lang]["exam_saved"])
            return redirect(f"{reverse('education:exam', args=[exam.pk])}?lang={lang}")
    return render(request, "education/exam_form.html", _page(request, lang, group=group, exam=None, post=request.POST, error=error,
                                                               page_title=WORDS[lang]["new_exam"]))


def _entries(post):
    """The sheet's boxes as ``record_marks`` takes them: one entry for every student the page posted, whoever that is (the service refuses
    anyone off the roster; a ticked box and a typed score never both survive: the service refuses that too, nothing is dropped here)."""

    ids = {int(key[2:]) for key in post if key[:2] in ("s_", "a_", "n_") and key[2:].isdigit()}
    return {pk: {"score": post.get(f"s_{pk}", ""), "absent": post.get(f"a_{pk}") == "1", "note": post.get(f"n_{pk}", "")} for pk in ids}


def exam_sheet(request, pk):
    exam = _exam(request, pk)
    lang = _lang(request)
    words = WORDS[lang]
    error, typed, edited = "", None, None
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "marks":
                entries = _entries(request.POST)
                exams.record_marks(exam, entries, request.user, version=request.POST.get("sheet_version", ""), lang=lang)
                messages.success(request, words["marks_saved"])
            elif action == "edit":
                exams.save_exam(exam.group, request.POST, request.user, exam, lang)
                messages.success(request, words["exam_saved"])
            elif action in ("publish", "unpublish"):
                exams.set_published(exam, action == "publish", request.user, lang)
                messages.success(request, words["exam_saved"])
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = " ".join(exc.messages)
            # a stale page shows the database again, never its own old boxes; a refused edit of the details keeps what was typed there
            typed = request.POST if action == "marks" and getattr(exc, "code", "") != "stale" else None
            edited = request.POST if action == "edit" else None
            exam.refresh_from_db()
        else:
            return redirect(f"{reverse('education:exam', args=[exam.pk])}?lang={lang}")
    marks = {mark.student_id: mark for mark in exam.marks.all()}
    roster = list(exams.roster_of(exam))
    for row in roster:
        mark = marks.get(row.student_id)
        if typed is not None:  # a refused save comes back with what was typed
            row.score_text, row.absent, row.note = typed.get(f"s_{row.student_id}", ""), typed.get(f"a_{row.student_id}") == "1", typed.get(f"n_{row.student_id}", "")
        else:
            row.score_text = "" if mark is None or mark.score is None else f"{mark.score.normalize():f}"
            row.absent, row.note = bool(mark and mark.absent), mark.note if mark else ""
    entered = sum(1 for mark in marks.values() if mark.score is not None or mark.absent)
    return render(request, "education/marks.html", _page(request, lang, exam=exam, group=exam.group, roster=roster, error=error, entered=entered, seats=len(roster),
                                                           version=exams.sheet_version(exam, marks.values()), future=exam.held_on > timezone.localdate(),
                                                           out_text=f"{exam.out_of.normalize():f}", page_title=exam.title, post=edited,
                                                           kind_label=words["kinds"][exam.kind], count_text=words["entered_of"].format(n=entered, m=len(roster))))


def _period(request):
    """``?first=&last=`` (the report's period): the last 30 days unless chosen."""

    today = timezone.localdate()
    try:
        first = date.fromisoformat(request.GET.get("first") or "")
        last = date.fromisoformat(request.GET.get("last") or "")
    except ValueError:
        return today - timedelta(days=29), today
    return first, last


def report_card(request, pk):
    _install()
    student = get_object_or_404(services.scoped(Student.objects), pk=pk)
    if not exams.can_see_student(request.user, student):
        raise PermissionDenied("The report card is for managers and the teacher of one of the student's groups.")
    lang = _lang(request)
    words = WORDS[lang]
    first, last = _period(request)
    error = ""
    try:
        card = exams.report_card(student, first, last, request.user, lang)
    except ValidationError as exc:
        error = " ".join(exc.messages)
        first, last = timezone.localdate() - timedelta(days=29), timezone.localdate()
        card = exams.report_card(student, first, last, request.user, lang)
    today = timezone.localdate()
    this_first = today.replace(day=1)
    last_last = this_first - timedelta(days=1)
    quick = [(words["this_month"], this_first, today), (words["last_month"], last_last.replace(day=1), last_last),
             (words["last_30"], today - timedelta(days=29), today)]
    back = f"{reverse('education:student', args=[student.pk])}?lang={lang}" if services.can_view(request.user) else f"{reverse('education:today')}?lang={lang}"
    groups = sorted({row["exam"].group.name for row in card["rows"]})
    return render(request, "education/exam_report.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "xw": words, "page_title": f"{words['report']}: {student.name}",
                                                          "card": card, "student": student, "company": company_details(), "error": error, "quick": quick,
                                                          "back_url": back, "today": today, "groups": groups})
