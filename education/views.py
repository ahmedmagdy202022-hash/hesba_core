"""EDU-001 screens: students, study groups and courses, and enrolling students in groups."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer

from . import attendance as register
from . import services
from .models import Attendance, ClassSession, Course, Enrollment, EnrollmentStatus, FeeBasis, Presence, SessionStatus, Student, StudyGroup, Weekday

WORDS = {
    "ar": {
        "title": "الطلاب والمجموعات", "students": "الطلاب", "groups": "المجموعات", "courses": "الكورسات والمواد",
        "intro": "كل طالب له ملف، ومين بيدفع عنه (ولي أمر أو هو نفسه)، والمجموعات اللي مسجّل فيها.",
        "search": "دوّر بالاسم أو التليفون أو الكود", "find": "بحث", "code": "الكود", "name": "الاسم", "phone": "التليفون", "stage": "السنة / المستوى",
        "school": "المدرسة", "payer": "بيدفع عنه", "relation": "صلة القرابة", "groups_count": "المجموعات", "open": "افتح", "new_student": "+ طالب جديد",
        "no_students": "مفيش طلاب لسه. ابدأ بـ «طالب جديد».", "dob": "تاريخ الميلاد", "notes": "ملاحظات", "save": "حفظ", "active": "نشط",
        "payer_existing": "ولي أمر موجود", "choose": "اختار", "payer_new": "أو ولي أمر جديد", "payer_name": "اسم ولي الأمر", "payer_phone": "تليفون ولي الأمر",
        "self_pays": "الطالب بيدفع لنفسه (كورسات الكبار)", "back_students": "الطلاب", "edit": "تعديل البيانات", "account": "حساب ولي الأمر والفواتير",
        "enrollments": "المجموعات المسجّل فيها", "group": "المجموعة", "course": "الكورس", "schedule": "المواعيد", "teacher": "المدرس",
        "fee": "المصروفات", "discount": "الخصم %", "net": "بعد الخصم", "since": "من", "until": "لحد", "status": "الحالة", "stop": "إيقاف",
        "enroll": "سجّله في مجموعة", "enroll_btn": "تسجيل", "start_date": "تاريخ البداية", "no_enrollments": "مش مسجّل في أي مجموعة.",
        "status_active": "مستمر", "status_stopped": "متوقف", "new_group": "+ مجموعة جديدة", "no_groups": "مفيش مجموعات لسه. اعمل كورس الأول وبعدين مجموعة.",
        "room": "القاعة", "capacity": "السعة (0 = مفتوح)", "seats": "الطلاب", "days": "الأيام", "start_time": "الميعاد", "duration": "مدة الحصة (دقيقة)",
        "group_fee": "مصروفات المجموعة (فاضي = سعر الكورس)", "starts_on": "تبدأ", "ends_on": "تنتهي", "back_groups": "المجموعات", "roster": "الطلاب في المجموعة",
        "no_roster": "مفيش طلاب في المجموعة دي لسه.", "add_student": "ضيف طالب للمجموعة", "student": "الطالب", "new_course": "كورس / مادة جديدة",
        "basis": "طريقة الحساب", "description": "وصف", "no_courses": "مفيش كورسات لسه.", "edit_course": "تعديل", "full": "كاملة",
        "saved": "اتحفظ.", "enrolled_ok": "اتسجل الطالب.", "stopped_ok": "اتوقف التسجيل.", "per": "/", "no_teacher": "—",
        "add_course_first": "اعمل كورس الأول من «الكورسات والمواد».", "group_title": "مجموعة", "item": "صنف الفاتورة",
        "free_seats": "متاح", "total_monthly": "إجمالي المصروفات الشهرية للمجموعة",
        "today": "حصص النهارده", "classes_on": "الحصص يوم", "no_classes": "مفيش حصص في اليوم ده.", "take": "سجّل الحضور", "taken": "اتسجّل",
        "change": "تعديل الحضور", "present": "حاضر", "late": "متأخر", "absent": "غايب", "excused": "بعذر", "topic": "اتشرح إيه (اختياري)",
        "cancelled_class": "الحصة اتلغت", "save_attendance": "حفظ الحضور", "attendance_saved": "اتسجّل الحضور.", "attendance": "الحضور",
        "rate": "نسبة الحضور (30 يوم)", "recent_absences": "آخر غياب", "no_absences": "مفيش غياب في آخر 30 يوم.", "sessions": "آخر الحصص",
        "no_sessions": "لسه متسجّلش حضور للمجموعة دي.", "tell_parent": "ابعت لولي الأمر واتساب", "all_present": "الكل حاضر",
        "go": "عرض", "date": "التاريخ", "off_day": "المجموعة دي مالهاش حصة في اليوم ده. اختار يوم من أيامها من «حصص النهارده».", "counts": "حاضر {present} · متأخر {late} · غايب {absent}", "notify": "بلّغ أولياء الأمور",
    },
    "en": {
        "title": "Students & groups", "students": "Students", "groups": "Groups", "courses": "Courses & subjects",
        "intro": "Each student has a file, who pays for them (a parent or themselves), and the groups they are enrolled in.",
        "search": "Search by name, phone or code", "find": "Search", "code": "Code", "name": "Name", "phone": "Phone", "stage": "Year / level",
        "school": "School", "payer": "Paid by", "relation": "Relation", "groups_count": "Groups", "open": "Open", "new_student": "+ New student",
        "no_students": "No students yet. Start with “New student”.", "dob": "Date of birth", "notes": "Notes", "save": "Save", "active": "Active",
        "payer_existing": "Existing parent", "choose": "Choose", "payer_new": "or a new parent", "payer_name": "Parent's name", "payer_phone": "Parent's phone",
        "self_pays": "The student pays for themselves (adult courses)", "back_students": "Students", "edit": "Edit details", "account": "Parent's account & invoices",
        "enrollments": "Enrolled groups", "group": "Group", "course": "Course", "schedule": "Schedule", "teacher": "Teacher",
        "fee": "Fee", "discount": "Discount %", "net": "After discount", "since": "From", "until": "Until", "status": "Status", "stop": "Stop",
        "enroll": "Enrol in a group", "enroll_btn": "Enrol", "start_date": "Start date", "no_enrollments": "Not enrolled in any group.",
        "status_active": "Active", "status_stopped": "Stopped", "new_group": "+ New group", "no_groups": "No groups yet. Add a course first, then a group.",
        "room": "Room", "capacity": "Capacity (0 = open)", "seats": "Students", "days": "Days", "start_time": "Time", "duration": "Class length (minutes)",
        "group_fee": "Group fee (blank = the course's price)", "starts_on": "Starts", "ends_on": "Ends", "back_groups": "Groups", "roster": "Students in this group",
        "no_roster": "No students in this group yet.", "add_student": "Add a student to the group", "student": "Student", "new_course": "New course / subject",
        "basis": "Charged", "description": "Description", "no_courses": "No courses yet.", "edit_course": "Edit", "full": "full",
        "saved": "Saved.", "enrolled_ok": "Student enrolled.", "stopped_ok": "Enrolment stopped.", "per": "/", "no_teacher": "—",
        "add_course_first": "Add a course first under “Courses & subjects”.", "group_title": "Group", "item": "Invoice item",
        "free_seats": "free", "total_monthly": "Monthly fees of the group",
        "today": "Today's classes", "classes_on": "Classes on", "no_classes": "No classes on that day.", "take": "Take attendance", "taken": "Taken",
        "change": "Change attendance", "present": "Present", "late": "Late", "absent": "Absent", "excused": "Excused", "topic": "What was covered (optional)",
        "cancelled_class": "Class cancelled", "save_attendance": "Save attendance", "attendance_saved": "Attendance saved.", "attendance": "Attendance",
        "rate": "Attendance (30 days)", "recent_absences": "Recent absences", "no_absences": "No absences in the last 30 days.", "sessions": "Recent classes",
        "no_sessions": "No attendance taken for this group yet.", "tell_parent": "WhatsApp the parent", "all_present": "All present",
        "go": "Show", "date": "Date", "off_day": "This group has no class on that day. Pick one of its days from Today's classes.", "counts": "Present {present} · Late {late} · Absent {absent}", "notify": "Tell the parents",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _guard(request, write=False):
    if not services.is_education_install():
        raise Http404("Students and groups are for education activities.")
    if not services.can_view(request.user):
        raise PermissionDenied("Students need master_data.view_master_data.")
    if write and not services.can_manage(request.user):
        raise PermissionDenied("Editing students needs master_data.manage_parties.")


def _base(request, lang, tab, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"], "section": "education",
            "tab": tab, "can_manage": services.can_manage(request.user), **extra}


def _back(name, lang, *args):
    return redirect(f"{reverse(name, args=args)}?lang={lang}")


def _payers():
    return Customer.objects.filter(active=True).exclude(customer_code="WALK-IN").order_by("name")


def _teachers():
    from staff.models import Employee

    return Employee.objects.filter(active=True).order_by("name")


def _status_label(status, words):
    return words["status_active"] if status == EnrollmentStatus.ACTIVE else words["status_stopped"]


# ---- students ----

def students(request):
    _guard(request)
    lang = _lang(request)
    query = (request.GET.get("q") or "").strip()
    rows = services.scoped(Student.objects).select_related("payer").annotate(
        groups_count=Count("enrollments", filter=Q(enrollments__status=EnrollmentStatus.ACTIVE)))
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(code__icontains=query) | Q(payer__name__icontains=query))
    return render(request, "education/students.html", _base(request, lang, "students", rows=rows[:300], query=query))


def student_new(request):
    _guard(request, write=True)
    lang = _lang(request)
    error = ""
    if request.method == "POST":
        data = request.POST.dict()
        data["payer"] = _payers().filter(pk=request.POST.get("payer") or 0).first()
        try:
            student = services.save_student(data, request.user, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, WORDS[lang]["saved"])
            return _back("education:student", lang, student.pk)
    return render(request, "education/student_form.html", _base(request, lang, "students", payers=_payers(), post=request.POST, error=error))


def student_detail(request, pk):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    student = get_object_or_404(services.scoped(Student.objects).select_related("payer"), pk=pk)
    error = ""
    if request.method == "POST":
        _guard(request, write=True)
        action = request.POST.get("action")
        try:
            if action == "edit":
                data = request.POST.dict()
                data["payer"] = _payers().filter(pk=request.POST.get("payer") or 0).first() or student.payer
                services.save_student(data, request.user, student, lang)
                message = words["saved"]
            elif action == "enroll":
                group = get_object_or_404(services.scoped(StudyGroup.objects), pk=request.POST.get("group") or 0)
                services.enroll(student, group, request.user, start_date=request.POST.get("start_date"),
                                discount_percent=request.POST.get("discount_percent"), lang=lang)
                message = words["enrolled_ok"]
            elif action == "stop":
                enrollment = get_object_or_404(Enrollment, pk=request.POST.get("enrollment") or 0, student=student)
                services.stop(enrollment, request.user, end_date=request.POST.get("end_date"), lang=lang)
                message = words["stopped_ok"]
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, message)
            return _back("education:student", lang, student.pk)
    enrollments = list(student.enrollments.select_related("group__course", "group__teacher"))
    for row in enrollments:
        row.schedule = services.schedule_text(row.group, lang)
        row.net = services.monthly_fee(row)
        row.basis_label = services.BASIS_LABELS[lang][row.group.course.basis]  # a month, a class or the whole course
        row.status_label = _status_label(row.status, words)
    enrolled = {row.group_id for row in enrollments if row.status == EnrollmentStatus.ACTIVE}
    open_groups = services.scoped(StudyGroup.objects).filter(active=True).exclude(pk__in=enrolled).select_related("course")
    summary = register.student_summary(student)
    return render(request, "education/student.html", _base(request, lang, "students", student=student, enrollments=enrollments, open_groups=open_groups,
                                                           payers=_payers(), error=error, today=timezone.localdate(), summary=summary))


# ---- groups ----

def _group_data(request):
    data = request.POST.copy()
    data["course"] = services.scoped(Course.objects).filter(pk=request.POST.get("course") or 0).first()
    data["teacher"] = _teachers().filter(pk=request.POST.get("teacher") or 0).first()
    return data


def groups(request):
    _guard(request)
    lang = _lang(request)
    rows = list(services.scoped(StudyGroup.objects).select_related("course", "teacher").annotate(
        seats=Count("enrollments", filter=Q(enrollments__status=EnrollmentStatus.ACTIVE))).order_by("-active", "course__name", "name"))
    for row in rows:
        row.schedule = services.schedule_text(row, lang)
    return render(request, "education/groups.html", _base(request, lang, "groups", rows=rows, has_courses=services.scoped(Course.objects).filter(active=True).exists()))


def _group_form_context(request, lang, **extra):
    return _base(request, lang, "groups", courses=services.scoped(Course.objects).filter(active=True), teachers=_teachers(),
                 weekdays=[(day, services.DAY_LABELS[lang][day]) for day in Weekday.values], **extra)


def group_new(request):
    _guard(request, write=True)
    lang = _lang(request)
    error = ""
    if request.method == "POST":
        try:
            group = services.save_group(_group_data(request), request.user, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, WORDS[lang]["saved"])
            return _back("education:group", lang, group.pk)
    chosen = request.POST.getlist("days") if request.method == "POST" else []
    return render(request, "education/group_form.html", _group_form_context(request, lang, group=None, post=request.POST, chosen_days=chosen, error=error,
                                                                             preset_course=request.GET.get("course", "")))


def group_detail(request, pk):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    group = get_object_or_404(services.scoped(StudyGroup.objects).select_related("course", "teacher"), pk=pk)
    error = ""
    if request.method == "POST":
        _guard(request, write=True)
        action = request.POST.get("action")
        try:
            if action == "edit":
                services.save_group(_group_data(request), request.user, group, lang)
                message = words["saved"]
            elif action == "enroll":
                student = get_object_or_404(services.scoped(Student.objects), pk=request.POST.get("student") or 0)
                services.enroll(student, group, request.user, start_date=request.POST.get("start_date"),
                                discount_percent=request.POST.get("discount_percent"), lang=lang)
                message = words["enrolled_ok"]
            elif action == "stop":
                enrollment = get_object_or_404(Enrollment, pk=request.POST.get("enrollment") or 0, group=group)
                services.stop(enrollment, request.user, end_date=request.POST.get("end_date"), lang=lang)
                message = words["stopped_ok"]
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = " ".join(exc.messages)
            group.refresh_from_db()
        else:
            messages.success(request, message)
            return _back("education:group", lang, group.pk)
    roster = list(group.enrollments.filter(status=EnrollmentStatus.ACTIVE).select_related("student__payer").order_by("student__name"))
    rates = register.group_rates(group)
    for row in roster:
        row.net = services.monthly_fee(row)
        row.rate = rates.get(row.student_id)
    sessions = list(group.sessions.annotate(
        present=Count("marks", filter=Q(marks__presence=Presence.PRESENT)), late=Count("marks", filter=Q(marks__presence=Presence.LATE)),
        absent=Count("marks", filter=Q(marks__presence=Presence.ABSENT)))[:8])
    others = services.scoped(Student.objects).filter(active=True).exclude(pk__in=[row.student_id for row in roster]).order_by("name")
    # A monthly total only means something for a monthly course; a whole course or a per-class fee is not a month.
    total = sum((row.net for row in roster), start=group.effective_fee * 0) if group.course.basis == FeeBasis.MONTHLY else None
    return render(request, "education/group.html", _group_form_context(
        request, lang, group=group, roster=roster, others=others, schedule=services.schedule_text(group, lang), error=error, post=None,
        chosen_days=group.weekdays, total=total, basis_label=services.BASIS_LABELS[lang][group.course.basis],
        free=(group.capacity - len(roster)) if group.capacity else None, today=timezone.localdate(),
        sessions=sessions, can_take=register.can_take(request.user, group), last_class=register.last_class_day(group)))


# ---- courses ----

def courses(request):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    editing = None
    if request.method == "POST":
        _guard(request, write=True)
        editing = services.scoped(Course.objects).filter(pk=request.POST.get("course") or 0).first()
        try:
            services.save_course(request.POST, request.user, editing, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return _back("education:courses", lang)
    elif request.GET.get("edit"):
        editing = services.scoped(Course.objects).filter(pk=request.GET.get("edit")).first()
    rows = list(services.scoped(Course.objects).select_related("item").annotate(group_count=Count("groups", filter=Q(groups__active=True))))
    for row in rows:
        row.basis_label = services.BASIS_LABELS[lang][row.basis]
    bases = [(value, services.BASIS_LABELS[lang][value]) for value in FeeBasis.values]
    return render(request, "education/courses.html", _base(request, lang, "courses", rows=rows, editing=editing, bases=bases, error=error, post=request.POST))


# ---- attendance (EDU-002) ----

def _see(request, group=None):
    if not services.is_education_install():
        raise Http404("Attendance is for education activities.")
    if not register.can_see(request.user, group):
        raise PermissionDenied("Attendance needs master_data.view_master_data, or teaching the group.")


def _day(request):
    from datetime import date

    try:
        return date.fromisoformat(request.GET.get("date") or "")
    except ValueError:
        return timezone.localdate()


def today(request):
    _see(request)
    lang = _lang(request)
    day = _day(request)
    groups_today = register.groups_on(day, request.user)
    taken = {session.group_id: session for session in ClassSession.objects.filter(date=day, group__in=groups_today).annotate(
        present=Count("marks", filter=Q(marks__presence=Presence.PRESENT)), late=Count("marks", filter=Q(marks__presence=Presence.LATE)),
        absent=Count("marks", filter=Q(marks__presence=Presence.ABSENT)))}
    rows = []
    for group in groups_today:
        rows.append({"group": group, "schedule": services.schedule_text(group, lang), "session": taken.get(group.pk),
                     "seats": len(register.roster_on(group, day)), "can_take": register.can_take(request.user, group)})
    return render(request, "education/today.html", _base(request, lang, "today", rows=rows, day=day, today=timezone.localdate(),
                                                         weekday=services.DAY_LABELS[lang][register.weekday(day)]))


def take_attendance(request, pk):
    group = get_object_or_404(services.scoped(StudyGroup.objects).select_related("course", "teacher"), pk=pk)
    _see(request, group)
    lang = _lang(request)
    words = WORDS[lang]
    if not register.can_take(request.user, group):
        raise PermissionDenied("Taking attendance needs managing students, or teaching the group.")
    day = (_day(request) if request.GET.get("date") else (register.last_class_day(group) or timezone.localdate())) if request.method == "GET" else None
    error = ""
    if request.method == "POST":
        from datetime import date

        try:
            day = date.fromisoformat(request.POST.get("date") or "")
        except ValueError:
            day = timezone.localdate()
        marks = {key[len("p_"):]: value for key, value in request.POST.items() if key.startswith("p_")}
        marks.update({key: value for key, value in request.POST.items() if key.startswith("note_")})
        try:
            register.take(group, request.user, day, marks, topic=request.POST.get("topic", ""), cancelled=request.POST.get("cancelled") == "1", lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["attendance_saved"])
            return redirect(f"{reverse('education:attendance', args=[group.pk])}?lang={lang}&date={day.isoformat()}&saved=1")
    session = ClassSession.objects.filter(group=group, date=day).first()
    marks = {mark.student_id: mark for mark in session.marks.select_related("student__payer", "session__group")} if session else {}
    roster = list(register.roster_on(group, day))
    for row in roster:
        mark = marks.get(row.student_id)
        row.presence = mark.presence if mark else Presence.PRESENT
        row.note = mark.note if mark else ""
        row.notify = register.parent_message(mark, lang) if mark and request.GET.get("saved") else ""
    choices = [(Presence.PRESENT, words["present"]), (Presence.LATE, words["late"]), (Presence.ABSENT, words["absent"]), (Presence.EXCUSED, words["excused"])]
    return render(request, "education/attendance.html", _base(request, lang, "today", group=group, day=day, session=session, roster=roster, choices=choices,
                                                              schedule=services.schedule_text(group, lang), error=error, today=timezone.localdate(),
                                                              saved=bool(request.GET.get("saved")), notify=[row for row in roster if row.notify],
                                                              scheduled=register.meets_on(group, day)))
