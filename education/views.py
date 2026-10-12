"""EDU-001 screens: students, study groups and courses, and enrolling students in groups."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer
from permissions.services import user_has_permission

from . import levels, nursery, attendance as register
from . import services
from .models import Attendance, ClassSession, Course, Enrollment, EnrollmentStatus, FeeBasis, PickupPerson, Presence, SessionStatus, Student, StudyGroup, Weekday

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
        "fees": "مصروفات الشهر", "dues": "المتأخرات", "month": "الشهر", "to_bill": "هيتفوتر", "billed": "اتفوتر", "lines": "البنود",
        "qty": "الكمية", "price": "السعر", "amount": "المبلغ", "bill_btn": "اعمل فواتير الشهر", "post_now": "رحّلها على طول (تتسجل على حساب ولي الأمر)",
        "invoice_date": "تاريخ الفاتورة", "billed_ok": "اتعملت {count} فاتورة بإجمالي {total}.", "nothing_new": "كل حاجة في الشهر ده اتفوترت.",
        "invoice": "الفاتورة", "total": "الإجمالي", "fees_intro": "فاتورة واحدة لكل ولي أمر بكل مجموعات أولاده. الشهري بسعر الشهر، والحصة بعدد الحصص اللي حضرها، والكورس مرة واحدة.",
        "dues_intro": "أولياء الأمور اللي عليهم مصروفات، والأقدم الأول.", "overdue": "متأخر", "owed": "عليه", "oldest": "من", "last_payment": "آخر دفعة",
        "remind": "فكّره واتساب", "includes_tax": "منها ضريبة", "no_dues": "مفيش متأخرات. 👏", "collect": "تحصيل", "draft": "مسودة", "posted": "مترحّلة",
        "teacher_pay": "أجر المدرس", "teacher_rate": "المبلغ أو النسبة", "teacher_rate_hint": "عن كل حصة، أو في الشهر، أو نسبة % من مصروفات المجموعة.",
        "teachers": "أجور المدرسين", "teachers_intro": "اللي ليه كل مدرس في الشهر حسب طريقة أجره في كل مجموعة. الدفع بيطلع من الخزنة كمصروف عادي.",
        "due": "ليه", "paid": "اتدفع", "remaining": "الباقي", "units": "الأساس", "pay_btn": "ادفع", "cashbox": "من خزنة", "paid_ok": "اتدفع {amount} لـ {teacher}.",
        "no_teachers": "مفيش مجموعات ليها مدرس وطريقة أجر. حددها من صفحة المجموعة.", "classes_held": "حصة", "of_fees": "من مصروفات", "a_month": "شهر",
        "report": "تقرير الشهر", "report_intro": "كل مجموعة في الشهر: الطلاب، والحصص، والحضور، والمصروفات اللي اتفوترت، وأجر المدرس، واللي فاضل للمركز.",
        "held": "حصص اتعملت", "left": "فاضل للمركز", "no_report": "مفيش مجموعات شغالة في الشهر ده.", "month_rate": "الحضور",
        "discard": "احذف المسودة", "discarded": "اتحذفت مسودة {number}؛ الشهر ده يتفوتر تاني لأولياء الأمور دول.",
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
        "fees": "Monthly fees", "dues": "Fees owed", "month": "Month", "to_bill": "To bill", "billed": "Billed", "lines": "Lines",
        "qty": "Qty", "price": "Price", "amount": "Amount", "bill_btn": "Make this month's invoices", "post_now": "Post them now (they go on the parent's account)",
        "invoice_date": "Invoice date", "billed_ok": "{count} invoices made, {total} in all.", "nothing_new": "Everything in this month is billed.",
        "invoice": "Invoice", "total": "Total", "fees_intro": "One invoice per parent with all their children's groups: monthly groups at the month's fee, per-class groups by classes attended, whole courses once.",
        "dues_intro": "Parents who owe fees, oldest first.", "overdue": "Overdue", "owed": "Owes", "oldest": "Since", "last_payment": "Last payment",
        "remind": "Remind on WhatsApp", "includes_tax": "incl. VAT", "no_dues": "Nothing owed. 👏", "collect": "Collect", "draft": "Draft", "posted": "Posted",
        "teacher_pay": "Teacher's pay", "teacher_rate": "Amount or share", "teacher_rate_hint": "Per class, per month, or a % of the group's fees.",
        "teachers": "Teacher pay", "teachers_intro": "What each teacher is owed for the month, by how each of their groups pays. Paying takes the cash out of a cashbox as an ordinary expense.",
        "due": "Owed", "paid": "Paid", "remaining": "Still owed", "units": "Based on", "pay_btn": "Pay", "cashbox": "From cashbox", "paid_ok": "{amount} paid to {teacher}.",
        "no_teachers": "No group has a teacher and a way of paying them. Set it on the group's page.", "classes_held": "classes", "of_fees": "of fees", "a_month": "month",
        "report": "Month report", "report_intro": "Each group in the month: students, classes, attendance, fees billed, the teacher's pay and what is left for the centre.",
        "held": "Classes held", "left": "Left for the centre", "no_report": "No groups ran in this month.", "month_rate": "Attendance",
        "discard": "Discard draft", "discarded": "Draft {number} discarded; this month can be billed again for these parents.",
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
            "tab": tab, "can_manage": services.can_manage(request.user), "can_view": services.can_view(request.user),
            "can_see_fees": user_has_permission(request.user, FEES_VIEW), "can_see_dues": user_has_permission(request.user, DUES_VIEW),
            "can_see_pay": user_has_permission(request.user, PAY_VIEW), "can_see_report": user_has_permission(request.user, REPORT_VIEW),
            "is_nursery": nursery.is_nursery(), "nursery_tab": NURSERY_WORDS[lang]["tab"], **extra}


def _back(name, lang, *args):
    return redirect(f"{reverse(name, args=args)}?lang={lang}")


def _payers(keep=None):
    """Active parents; editing a student keeps its own payer listed even when that account was switched off (Codex on #185)."""

    return Customer.objects.filter(Q(active=True) | Q(pk=keep or 0)).exclude(customer_code="WALK-IN").order_by("name")


def _teachers(keep=None):
    """Active staff; a group's own teacher stays listed even after being switched off (Codex on #185)."""

    from staff.models import Employee

    return Employee.objects.filter(Q(active=True) | Q(pk=keep or 0)).order_by("name")


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
                data["payer"] = _payers(student.payer_id).filter(pk=request.POST.get("payer") or 0).first() or student.payer
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
            elif action == "placement" and levels.is_levels_install():  # EDU-006
                course = get_object_or_404(services.same_entity_as(services.scoped(Course.objects), student), pk=request.POST.get("course") or 0)
                levels.record_placement(student, {**request.POST.dict(), "course": course}, request.user, lang)
                message = LEVEL_WORDS[lang]["placed"]
            elif action in ("pickup_add", "pickup_off") and nursery.is_nursery():  # EDU-005
                if action == "pickup_add":
                    nursery.save_person(student, request.POST.dict(), request.user, lang=lang)
                else:
                    person = get_object_or_404(PickupPerson, pk=request.POST.get("person") or 0, student=student)
                    nursery.save_person(student, {"name": person.name, "relation": person.relation, "phone": person.phone,
                                                  "id_number": person.id_number, "active": False}, request.user, person, lang)
                message = words["saved"]
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
    active_groups = len(enrolled)  # groups the student is in now, not every enrolment ever made
    open_groups = services.same_entity_as(services.scoped(StudyGroup.objects), student).filter(active=True).exclude(pk__in=enrolled).select_related("course")
    summary = register.student_summary(student)
    return render(request, "education/student.html", _base(request, lang, "students", student=student, enrollments=enrollments, open_groups=open_groups,
                                                           payers=_payers(student.payer_id), error=error, today=timezone.localdate(), summary=summary,
                                                           active_groups=active_groups, nw=NURSERY_WORDS[lang],
                                                           pickup_people=list(student.pickup_people.all()) if nursery.is_nursery() else [],
                                                           day_history=nursery.history(student, 10) if nursery.is_nursery() else [],
                                                           **_placement_context(student, lang)))


def _placement_context(student, lang):
    """EDU-006: the student's placement tests, and the groups at each test's level with a seat."""

    if not levels.is_levels_install():
        return {"uses_levels": False}
    tests = list(student.placements.select_related("course"))
    suggestions = [{"test": test, "groups": levels.groups_at(test.course, test.level, student)} for test in levels.latest(student).values()]
    return {"uses_levels": True, "lw": LEVEL_WORDS[lang], "placements": tests, "suggestions": suggestions,
            "level_courses": services.same_entity_as(services.scoped(Course.objects.filter(active=True)), student).order_by("name")}


# ---- groups ----

def _group_data(request, group=None):
    data = request.POST.copy()
    data["course"] = _courses(group).filter(pk=request.POST.get("course") or 0).first()
    data["teacher"] = _teachers(group.teacher_id if group else None).filter(pk=request.POST.get("teacher") or 0).first()
    return data


def _courses(group=None):
    """Active courses; editing a group keeps its own course listed even when that course was switched off."""

    return services.scoped(Course.objects).filter(Q(active=True) | Q(pk=group.course_id if group else 0))


def groups(request):
    _guard(request)
    lang = _lang(request)
    rows = list(services.scoped(StudyGroup.objects).select_related("course", "teacher").annotate(
        seats=Count("enrollments", filter=Q(enrollments__status=EnrollmentStatus.ACTIVE))).order_by("-active", "course__name", "name"))
    for row in rows:
        row.schedule = services.schedule_text(row, lang)
    return render(request, "education/groups.html", _base(request, lang, "groups", rows=rows, has_courses=services.scoped(Course.objects).filter(active=True).exists()))


def _group_form_context(request, lang, **extra):
    group = extra.get("group")
    return _base(request, lang, "groups", courses=_courses(group), teachers=_teachers(group.teacher_id if group else None),
                 pay_choices=list(services.PAY_LABELS[lang].items()), weekdays=[(day, services.DAY_LABELS[lang][day]) for day in Weekday.values],
                 uses_levels=levels.is_levels_install(), lw=LEVEL_WORDS[lang], **extra)


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
    error, failed_edit = "", None
    if request.method == "POST":
        _guard(request, write=True)
        action = request.POST.get("action")
        try:
            if action == "edit":
                services.save_group(_group_data(request, group), request.user, group, lang)
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
            elif action == "move_up" and levels.is_levels_install():  # EDU-006
                from datetime import date

                chosen = [int(pk) for pk in request.POST.getlist("students") if pk.isdigit()]
                students = list(services.scoped(Student.objects).filter(pk__in=chosen))
                target = next((other for other in levels.next_groups(group) if str(other.pk) == request.POST.get("target")), None)
                try:
                    on_date = date.fromisoformat(request.POST.get("on_date") or "")
                except ValueError:
                    on_date = None
                moved = levels.move_up(group, students, target, request.user, on_date=on_date, lang=lang)
                message = LEVEL_WORDS[lang]["moved"].format(n=len(moved), group=target.name)
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = " ".join(exc.messages)
            group.refresh_from_db()
            if action == "edit":  # the form comes back with what was typed, not the saved values (Codex on #185)
                failed_edit = request.POST
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
        absent=Count("marks", filter=Q(marks__presence=Presence.ABSENT)), excused=Count("marks", filter=Q(marks__presence=Presence.EXCUSED)))[:8])
    others = services.same_entity_as(services.scoped(Student.objects), group).filter(active=True).exclude(pk__in=[row.student_id for row in roster]).order_by("name")
    # A monthly total only means something for a monthly course; a whole course or a per-class fee is not a month.
    total = sum((row.net for row in roster), start=group.effective_fee * 0) if group.course.basis == FeeBasis.MONTHLY else None
    return render(request, "education/group.html", _group_form_context(
        request, lang, group=group, roster=roster, others=others, schedule=services.schedule_text(group, lang), error=error, post=failed_edit,
        chosen_days=failed_edit.getlist("days") if failed_edit else group.weekdays, total=total, basis_label=services.BASIS_LABELS[lang][group.course.basis],
        free=(group.capacity - len(roster)) if group.capacity else None, today=timezone.localdate(),
        sessions=sessions, can_take=register.can_take(request.user, group), last_class=register.last_class_day(group),
        next_groups=levels.next_groups(group) if levels.is_levels_install() else [], next_first=_next_first()))


def _next_first():
    """The first of next month: when a monthly course's students move up."""

    from datetime import timedelta

    today = timezone.localdate()
    return today if today.day == 1 else (today.replace(day=28) + timedelta(days=4)).replace(day=1)


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
        if request.POST.get("course") and editing is None:
            raise Http404("That course is not in this entity.")  # an edit never turns into a new course (Codex on #185)
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
        absent=Count("marks", filter=Q(marks__presence=Presence.ABSENT)), excused=Count("marks", filter=Q(marks__presence=Presence.EXCUSED)))}
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
            raise Http404("A class is saved for the date its form was opened on.")  # never silently today
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
                                                              scheduled=bool(session) or (group.active and register.meets_on(group, day))))


# ---- monthly fees and fees owed (EDU-003) ----

#: The month's invoices are sales invoices; who owes what is the customers' aging, behind its own report permission.
FEES_VIEW, DUES_VIEW = "sales.view_sales_invoices", "reports.view_customer_report"
#: EDU-004: teacher pay is an expense; the month report adds up every group's fees, so it needs the business-wide
#: sales report (a cashier's own-sales report is not enough, Codex on #189).
PAY_VIEW, REPORT_VIEW = "cashboxes.view_expenses", "reports.view_all_sales_report"


def _fees_guard(request, write=False, permission=FEES_VIEW):
    if not services.is_education_install():
        raise Http404("Fees are for education activities.")
    if not user_has_permission(request.user, permission):
        raise PermissionDenied(f"This page needs {permission}.")
    if write and not user_has_permission(request.user, "sales.create_sales_invoice"):
        raise PermissionDenied("Billing the month needs sales.create_sales_invoice.")


def fees(request):
    from . import billing

    _fees_guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    posting = request.method == "POST"
    if posting:
        _fees_guard(request, write=True)
    raw = (request.POST.get("month") if posting else request.GET.get("month")) or ""
    try:
        first = billing.month_of(raw)
        if posting and not raw.strip():
            raise ValidationError("")
    except ValidationError:
        if posting:  # a billing run states its month: never bill some other month than the one asked for
            error = billing.MESSAGES[lang]["month"]
        first = billing.month_of(None)
    if posting and not error and request.POST.get("action") == "discard":
        from sales.models import SalesInvoice

        invoice = get_object_or_404(SalesInvoice, pk=request.POST.get("invoice") or 0)
        try:
            billing.discard_draft(invoice, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["discarded"].format(number=invoice.invoice_number))
            return redirect(f"{reverse('education:fees')}?lang={lang}&month={first:%Y-%m}")
    elif posting and not error:
        from datetime import date

        try:
            raw_day = (request.POST.get("invoice_date") or "").strip()
            try:
                day = date.fromisoformat(raw_day) if raw_day else None
            except ValueError:
                raise ValidationError(billing.MESSAGES[lang]["invoice_date"])  # never record the invoices on a date nobody chose
            made = billing.bill_month(first, request.user, post=request.POST.get("post") == "1", invoice_date=day, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            from settings_core.templatetags.hesba_format import money

            messages.success(request, words["billed_ok"].format(count=len(made), total=money(sum(invoice.total_amount for invoice in made))))
            return redirect(f"{reverse('education:fees')}?lang={lang}&month={first:%Y-%m}")
    rows = billing.preview(first)
    done = billing.billed(first)
    return render(request, "education/fees.html", _base(request, lang, "fees", first=first, label=billing.month_label(first, lang), rows=rows, done=done,
                                                        total=sum((row["total"] for row in rows), start=0), error=error,
                                                        can_bill=user_has_permission(request.user, "sales.create_sales_invoice"),
                                                        is_future=first > timezone.localdate().replace(day=1), today=timezone.localdate(),
                                                        needs_entity=billing.MESSAGES[lang]["entity"] if _whole_group() else ""))


def _whole_group():
    from entities.current import current_entity
    from entities.services import is_multi_entity

    return current_entity() is None and is_multi_entity()


def dues(request):
    from printing.company import company_details
    from reports.aging import reminder_link

    from . import billing

    _fees_guard(request, permission=DUES_VIEW)
    lang = _lang(request)
    rows = billing.dues()
    company = company_details()
    for row in rows:
        row["remind"] = reminder_link(row, company["name"], company.get("currency", "EGP"), lang)
    return render(request, "education/dues.html", _base(request, lang, "dues", rows=rows, total=sum((row["total"] for row in rows), start=0),
                                                        overdue=sum((row["overdue"] for row in rows), start=0)))


# ---- EDU-004: teacher pay and the month report ----

def _month(request, lang):
    from . import billing

    try:
        return billing.month_of(request.POST.get("month") if request.method == "POST" else request.GET.get("month"))
    except ValidationError:
        return billing.month_of(None)


def teachers(request):
    from cashboxes.models import Cashbox
    from entities import scope as entity_scope
    from settings_core.templatetags.hesba_format import money
    from settings_core.ui_messages import translate
    from staff.models import Employee

    from . import billing, pay

    _fees_guard(request, permission=PAY_VIEW)
    lang = _lang(request)
    words = WORDS[lang]
    first = _month(request, lang)
    error = ""
    if request.method == "POST":
        _fees_guard(request, permission=pay.PAY_RECORD)
        raw = (request.POST.get("month") or "").strip()
        try:
            if billing.month_of(raw) != first or not raw:
                raise ValidationError(billing.MESSAGES[lang]["month"])  # a payment states its month
            # A teacher switched off since still gets what is owed: the month's own rows decide, not current employment (Codex on #189).
            owed_to = {row["teacher"].pk for row in pay.teacher_rows(first) if row["remaining"] > 0}
            teacher = Employee.objects.filter(pk=request.POST.get("teacher") or 0).filter(pk__in=owed_to).first()
            cashbox = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=request.POST.get("cashbox") or 0).first()
            payment = pay.pay(teacher, first, request.POST.get("amount"), cashbox, request.user, lang=lang)
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            messages.success(request, words["paid_ok"].format(amount=money(payment.amount), teacher=payment.teacher.name))
            return redirect(f"{reverse('education:teachers')}?lang={lang}&month={first:%Y-%m}")
    rows = pay.teacher_rows(first)
    for row in rows:
        for line in row["groups"]:
            line["basis_code"] = line["basis"]
            line["basis"] = services.PAY_LABELS[lang][line["basis"]]
    return render(request, "education/teachers.html", _base(request, lang, "teachers", first=first, label=billing.month_label(first, lang), rows=rows,
                                                            error=error, can_pay=user_has_permission(request.user, pay.PAY_RECORD),
                                                            cashboxes=entity_scope.cashboxes(Cashbox.objects).filter(active=True),
                                                            totals={key: sum((row[key] for row in rows), start=0) for key in ("due", "paid", "remaining")},
                                                            today=timezone.localdate()))


def report(request):
    from . import billing, insights

    _fees_guard(request, permission=REPORT_VIEW)
    lang = _lang(request)
    first = _month(request, lang)
    with_pay = user_has_permission(request.user, PAY_VIEW)  # what teachers are paid is for those who see the expenses
    rows, totals = insights.group_report(first, with_pay=with_pay)
    return render(request, "education/report.html", _base(request, lang, "report", first=first, label=billing.month_label(first, lang), rows=rows,
                                                          totals=totals, with_pay=with_pay, today=timezone.localdate()))


# ---- EDU-005: the nursery's day (check-in, pick-up by an allowed person) ----

NURSERY_WORDS = {
    "ar": {"tab": "الحضور والانصراف", "title": "حضور وانصراف الأطفال", "intro": "سجّل وصول كل طفل الصبح، ووقت المشي سلّمه بس لحد من اللي مسموح لهم يستلموه.",
           "child": "الطفل", "arrived": "وصل", "left": "مشي", "with": "مع", "brought_by": "جه مع (اختياري)", "check_in": "وصل",
           "check_out": "سلّم الطفل", "picker": "هيستلمه", "choose": "اختار", "inside": "جوه دلوقتي", "gone": "مشيوا", "away": "لسه ماجوش",
           "all": "كل الأطفال", "no_children": "مفيش أطفال متسجلين.", "no_people": "مفيش حد مسموح له يستلمه؛ ضيفه من ملف الطفل.",
           "tell_parent": "ابعت لولي الأمر", "checked_in": "اتسجل وصول {student}.", "checked_out": "{student} مشي مع {person}.",
           "pickup": "المسموح لهم يستلموا الطفل", "pickup_intro": "الطفل مش هيمشي غير مع حد من دول. اتأكد من البطاقة عند الباب.",
           "name": "الاسم", "relation": "صلة القرابة", "phone": "التليفون", "id_number": "رقم البطاقة (اختياري)", "add": "ضيف",
           "remove": "شيله من القايمة", "removed": "مش مسموح", "no_pickup": "لسه مفيش حد في القايمة.", "history": "آخر الأيام",
           "day": "اليوم", "view_only": "الحضور والانصراف للإدارة أو المدرّسين بس."},
    "en": {"tab": "Check-in & pick-up", "title": "Children's check-in and pick-up", "intro": "Check each child in in the morning, and at the end of the day hand them only to someone allowed to pick them up.",
           "child": "Child", "arrived": "Arrived", "left": "Left", "with": "with", "brought_by": "Brought by (optional)", "check_in": "Arrived",
           "check_out": "Hand over", "picker": "Picked up by", "choose": "Choose", "inside": "Inside now", "gone": "Gone home", "away": "Not here yet",
           "all": "All children", "no_children": "No children recorded.", "no_people": "Nobody may pick this child up; add someone from the child's file.",
           "tell_parent": "Tell the parent", "checked_in": "{student} checked in.", "checked_out": "{student} left with {person}.",
           "pickup": "Who may pick the child up", "pickup_intro": "The child leaves only with one of these people. Check their ID at the door.",
           "name": "Name", "relation": "Relation", "phone": "Phone", "id_number": "ID number (optional)", "add": "Add",
           "remove": "Take off the list", "removed": "Not allowed", "no_pickup": "Nobody on the list yet.", "history": "Recent days",
           "day": "Day", "view_only": "Check-in and pick-up are for the office or the teachers."},
}


def nursery_day(request):
    _see(request)
    if not nursery.is_nursery():
        raise Http404("Check-in and pick-up are for nurseries.")
    lang = _lang(request)
    words = NURSERY_WORDS[lang]
    error = ""
    tell = ""
    if request.method == "POST":
        if not nursery.can_desk(request.user):
            raise PermissionDenied("Check-in and pick-up need managing students, or working as staff.")
        student = get_object_or_404(services.scoped(Student.objects), pk=request.POST.get("student") or 0)
        try:
            if request.POST.get("action") == "in":
                nursery.check_in(student, request.user, brought_by=request.POST.get("brought_by", ""), lang=lang)
                messages.success(request, words["checked_in"].format(student=student.name))
            elif request.POST.get("action") == "out":
                person = PickupPerson.objects.filter(pk=request.POST.get("person") or 0).first()
                record = nursery.check_out(student, person, request.user, lang=lang)
                messages.success(request, words["checked_out"].format(student=student.name, person=record.picked_by.name))
                tell = nursery.left_message(record, lang)
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            url = f"{reverse('education:nursery')}?lang={lang}"
            return redirect(url + (f"&told={record.pk}" if tell else ""))
    rows, counts = nursery.board()
    told = request.GET.get("told")
    for row in rows:
        record = row["record"]
        row["tell"] = nursery.left_message(record, lang) if record and told and str(record.pk) == told else ""
    return render(request, "education/nursery.html", _base(request, lang, "nursery", nw=words, rows=rows, counts=counts, error=error,
                                                           can_desk=nursery.can_desk(request.user), today=timezone.localdate()))


# ---- EDU-006: language levels ----

LEVEL_WORDS = {
    "ar": {"level": "المستوى", "level_hint": "رقم المستوى في الكورس (1، 2، 3…)؛ سيبه فاضي لو الكورس مالوش مستويات.", "level_n": "المستوى {n}",
           "placement": "امتحان تحديد المستوى", "placement_intro": "سجّل نتيجة الامتحان والمستوى اللي اتحدد؛ هيظهر لك المجموعات اللي في المستوى ده وفيها مكان.",
           "course": "الكورس", "score": "الدرجة (اختياري)", "out_of": "من (اختياري)", "taken_on": "يوم الامتحان", "notes": "ملاحظات", "save": "سجّل",
           "placed": "اتسجل امتحان تحديد المستوى.", "no_tests": "لسه مااتعملش امتحان تحديد مستوى.", "groups_for": "مجموعات {course} — المستوى {n}",
           "no_groups": "مفيش مجموعة في المستوى ده فيها مكان دلوقتي.", "free": "{n} مكان فاضي", "open": "مفتوحة",
           "move_up": "طلّع الطلاب للمستوى الجاي", "move_intro": "علّم على اللي نجحوا، واختار مجموعة المستوى الجاي. اشتراكهم هنا بيقف يوم قبل، وبيبدأ هناك بنفس الخصم.",
           "target": "مجموعة المستوى الجاي", "on_date": "من يوم", "move": "طلّعهم", "moved": "اتنقل {n} طالب لمجموعة {group}.",
           "all": "الكل", "no_next": "مفيش مجموعة في مستوى أعلى من نفس الكورس؛ اعملها الأول.", "monthly_note": "الكورس بالشهر: النقل بيبدأ أول الشهر."},
    "en": {"level": "Level", "level_hint": "The level within the course (1, 2, 3…); leave blank when the course has no levels.", "level_n": "Level {n}",
           "placement": "Placement test", "placement_intro": "Record the test's mark and the level it gives; the groups at that level with a free seat show here.",
           "course": "Course", "score": "Mark (optional)", "out_of": "Out of (optional)", "taken_on": "Test day", "notes": "Notes", "save": "Save",
           "placed": "Placement test recorded.", "no_tests": "No placement test yet.", "groups_for": "{course} groups — level {n}",
           "no_groups": "No group at this level has a free seat now.", "free": "{n} free seats", "open": "Open",
           "move_up": "Move students up a level", "move_intro": "Tick those who passed and choose the next level's group. Their enrolment here stops the day before and starts there with the same discount.",
           "target": "Next level's group", "on_date": "From", "move": "Move them up", "moved": "{n} students moved to {group}.",
           "all": "All", "no_next": "There is no higher-level group in this course; create it first.", "monthly_note": "A monthly course moves up on the first of a month."},
}
