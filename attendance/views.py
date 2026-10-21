"""T11 screens: today's attendance, my own check-in, the month's sheet, leave and holidays, and the fingerprint file."""

from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from permissions.decorators import require_any_permission
from reports.navigation import STAFF_AUDIENCE
from staff.models import Employee

from . import services
from .models import LeaveKind, LeaveRequest, LeaveStatus

WORDS = {
    "ar": {
        "title": "الحضور والانصراف", "intro": "مين حضر النهارده ومين اتأخر ومين في إجازة. الحضور بيتسجل من هنا، أو الموظف يسجّله بنفسه من «حضوري»، أو من ملف جهاز البصمة.",
        "tab_staff": "الموظفون", "tab_today": "النهارده", "tab_month": "كشف الشهر", "tab_leaves": "الإجازات", "tab_import": "ملف البصمة", "tab_me": "حضوري",
        "employee": "الموظف", "hours": "مواعيد الشغل", "state": "الحالة", "in": "حضر", "out": "انصرف", "late": "تأخير", "early": "خروج بدري",
        "over": "وقت إضافي", "minutes": "دقيقة", "check_in": "سجّل حضوره دلوقتي", "check_out": "سجّل انصرافه دلوقتي", "absent": "غايب",
        "present_now": "موجودين", "missing": "لسه ماجوش", "on_leave": "في إجازة", "none": "مفيش موظفين نشطين.", "saved": "اتحفظ.",
        "month": "الشهر", "show": "اعرض", "present_days": "أيام حضور", "late_days": "مرات تأخير", "late_total": "دقايق تأخير",
        "absent_days": "أيام غياب", "leave_days": "أيام إجازة", "unpaid_days": "منها من غير مرتب", "over_total": "دقايق إضافي", "off_days": "راحة وعطلات",
        "days": "يوم بيوم", "edit": "عدّل", "date": "التاريخ", "day": "اليوم", "note": "ملاحظة", "save": "حفظ", "clear_hint": "سيب الوقتين فاضيين ومن غير «غايب» علشان تمسح اليوم.",
        "schedule": "مواعيد الموظف", "start_time": "بيبدأ الساعة", "end_time": "بيخلص الساعة", "days_off": "أيام الراحة", "grace": "سماح التأخير (دقيقة)",
        "annual": "رصيد الإجازة في السنة", "device_id": "رقم البصمة", "device_hint": "الرقم اللي جهاز البصمة عارف الموظف بيه.",
        "payroll_hint": "الكشف ده بيعدّ الأيام والدقايق بس؛ المرتب والخصم بيتحسبوا لما شاشة المرتبات تتفعّل.", "open": "افتح", "all_staff": "كل الموظفين",
        "leaves_title": "الإجازات", "pending": "طلبات مستنية الموافقة", "approve": "وافق", "reject": "ارفض", "cancel": "الغي", "kind": "النوع",
        "from": "من", "to": "لحد", "count": "عدد الأيام", "reason": "السبب", "status": "الحالة", "no_pending": "مفيش طلبات مستنية.",
        "new_leave": "طلب إجازة", "approve_now": "وافق عليها على طول", "request": "قدّم الطلب", "balances": "رصيد الإجازات السنة دي",
        "entitled": "الرصيد", "taken": "اتاخد", "asked": "مطلوب", "left": "الباقي", "balance_hint": "الاعتيادي والعارضة بيتخصموا من الرصيد؛ المرضي ومن غير مرتب لأ.",
        "history": "كل الإجازات", "no_leave": "مفيش إجازات.", "holidays": "العطلات الرسمية", "holiday_name": "اسم العطلة", "add": "ضيف",
        "no_holidays": "مفيش عطلات متسجلة.", "remove": "شيل",
        "import_title": "ملف جهاز البصمة", "import_intro": "صدّر الحركات من جهاز البصمة كملف CSV وارفعه هنا. أول بصمة في اليوم بتبقى الحضور وآخر بصمة الانصراف. رفع نفس الملف تاني مش بيكرر حاجة.",
        "import_columns": "الملف لازم يكون فيه عمود لرقم الموظف (No. أو ID أو رقم البصمة) وعمود للتاريخ والوقت، أو عمود للتاريخ وعمود للوقت.",
        "file": "الملف", "upload": "ارفع وسجّل", "result": "{punches} بصمة اتقرت، واتسجل {days} يوم لـ {employees} موظف.",
        "unmatched": "أرقام مش متربطة بموظف (اكتبها في «رقم البصمة» عند الموظف وارفع الملف تاني): {ids}", "bad": "{n} سطر مااتقراش.",
        "me_title": "حضوري وانصرافي", "me_in": "سجّل حضوري", "me_out": "سجّل انصرافي", "me_checked_in": "حضرت الساعة {time}.",
        "me_checked_out": "انصرفت الساعة {time}.", "me_today": "النهارده", "me_month": "الشهر ده", "my_leave": "إجازاتي",
        "me_hint": "الزرار بيسجّل الساعة دلوقتي. لو نسيت، المدير يقدر يعدّل اليوم.", "done_in": "اتسجل حضورك الساعة {time}.", "done_out": "اتسجل انصرافك الساعة {time}.",
        "back": "الحضور والانصراف",
    },
    "en": {
        "title": "Attendance", "intro": "Who came in today, who was late, who is on leave. Record it here, let each employee check in from “My attendance”, or load the fingerprint device's file.",
        "tab_staff": "Employees", "tab_today": "Today", "tab_month": "Monthly sheet", "tab_leaves": "Leave", "tab_import": "Fingerprint file", "tab_me": "My attendance",
        "employee": "Employee", "hours": "Working hours", "state": "State", "in": "In", "out": "Out", "late": "Late", "early": "Left early",
        "over": "Overtime", "minutes": "min", "check_in": "Check in now", "check_out": "Check out now", "absent": "Absent",
        "present_now": "In", "missing": "Not in yet", "on_leave": "On leave", "none": "No active employees.", "saved": "Saved.",
        "month": "Month", "show": "Show", "present_days": "Days present", "late_days": "Times late", "late_total": "Minutes late",
        "absent_days": "Days absent", "leave_days": "Days on leave", "unpaid_days": "of them unpaid", "over_total": "Overtime minutes", "off_days": "Days off and holidays",
        "days": "Day by day", "edit": "Edit", "date": "Date", "day": "Day", "note": "Note", "save": "Save", "clear_hint": "Leave both times empty and not absent to clear the day.",
        "schedule": "Working hours", "start_time": "Starts at", "end_time": "Ends at", "days_off": "Days off", "grace": "Grace before late (min)",
        "annual": "Leave days a year", "device_id": "Fingerprint number", "device_hint": "The number the fingerprint device knows this employee by.",
        "payroll_hint": "This sheet counts days and minutes only; pay and deductions come with the payroll screen.", "open": "Open", "all_staff": "All employees",
        "leaves_title": "Leave", "pending": "Requests waiting for approval", "approve": "Approve", "reject": "Reject", "cancel": "Cancel", "kind": "Kind",
        "from": "From", "to": "To", "count": "Days", "reason": "Reason", "status": "Status", "no_pending": "No requests waiting.",
        "new_leave": "Ask for leave", "approve_now": "Approve it now", "request": "Send the request", "balances": "Leave balance this year",
        "entitled": "Balance", "taken": "Taken", "asked": "Asked", "left": "Left", "balance_hint": "Annual and casual leave come off the balance; sick and unpaid do not.",
        "history": "All leave", "no_leave": "No leave.", "holidays": "Public holidays", "holiday_name": "Holiday", "add": "Add",
        "no_holidays": "No holidays recorded.", "remove": "Remove",
        "import_title": "Fingerprint device file", "import_intro": "Export the punches from the fingerprint device as a CSV file and upload it here. The first punch of a day is the time in, the last the time out. Uploading the same file again repeats nothing.",
        "import_columns": "The file needs an employee number column (No., ID or fingerprint number) and a date-and-time column, or a date and a time column.",
        "file": "File", "upload": "Upload and record", "result": "{punches} punches read; {days} days recorded for {employees} employees.",
        "unmatched": "Numbers not linked to an employee (enter them as the employee's “Fingerprint number” and upload again): {ids}", "bad": "{n} lines could not be read.",
        "me_title": "My attendance", "me_in": "Check in", "me_out": "Check out", "me_checked_in": "You came in at {time}.",
        "me_checked_out": "You left at {time}.", "me_today": "Today", "me_month": "This month", "my_leave": "My leave",
        "me_hint": "The button records the time now. If you forgot, the manager can correct the day.", "done_in": "Checked in at {time}.", "done_out": "Checked out at {time}.",
        "back": "Attendance",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(getattr(exc, "messages", [str(exc)])), lang)


def _guard():
    if not services.enabled():
        raise Http404("Employees are switched off.")


def _base(request, lang, tab, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"], "tab": tab,
            "labels": services.STATE_LABELS[lang], "kinds": services.KIND_LABELS[lang], "leave_labels": services.LEAVE_STATUS_LABELS[lang],
            "weekdays": services.WEEKDAYS[lang], "can_manage": services.can_manage(request.user), "can_view": services.can_view(request.user),
            "me": services.own_employee(request.user), **extra}


def _done(request, lang, url, message=None):
    messages.success(request, message or WORDS[lang]["saved"])
    return redirect(f"{url}{'&' if '?' in url else '?'}lang={lang}")


def _manage(request):
    if not services.can_manage(request.user):
        raise PermissionDenied("Recording attendance needs master_data.manage_parties.")


@require_any_permission(*STAFF_AUDIENCE)
def today(request):
    _guard()
    lang = _lang(request)
    error = ""
    if request.method == "POST":
        _manage(request)
        employee = get_object_or_404(Employee, pk=request.POST.get("employee") or 0, active=True)
        try:
            if request.POST.get("action") == "check":
                services.check(employee, request.user, lang, source="manual")
            elif request.POST.get("action") == "absent":
                services.set_day(employee, timezone.localdate(), {"absent": "1"}, request.user, lang)
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            return _done(request, lang, reverse("attendance:today"))
    rows, counts = services.today_board()
    return render(request, "attendance/today.html", _base(request, lang, "today", rows=rows, counts=counts, error=error, today=timezone.localdate()))


def me(request):
    _guard()
    lang = _lang(request)
    words = WORDS[lang]
    employee = services.own_employee(request.user)
    if employee is None:
        raise PermissionDenied("My attendance is for a login linked to an employee.")
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") == "check":
                record, action = services.check(employee, request.user, lang)
                message = words["done_in" if action == "check_in" else "done_out"].format(time=(record.check_out or record.check_in).strftime("%H:%M"))
            elif request.POST.get("action") == "leave":
                services.request_leave(employee, {key: request.POST.get(key) for key in request.POST}, request.user, lang)
                message = words["saved"]
            elif request.POST.get("action") == "cancel_leave":
                services.cancel_leave(get_object_or_404(LeaveRequest, pk=request.POST.get("leave") or 0, employee=employee), request.user, lang)
                message = words["saved"]
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            return _done(request, lang, reverse("attendance:me"), message)
    today_ = timezone.localdate()
    [sheet] = services.month_sheet([employee], today_.year, today_.month, today_)
    today_row = sheet["days"][-1] if sheet["days"] and sheet["days"][-1]["date"] == today_ else None
    return render(request, "attendance/me.html", _base(
        request, lang, "me", employee=employee, sheet=sheet, today_row=today_row, error=error, balance=services.balance(employee),
        leaves=LeaveRequest.objects.filter(employee=employee).order_by("-start_date")[:20], kind_choices=LeaveKind.values, post=request.POST))


def _month(request):
    raw = request.GET.get("month") or request.POST.get("month") or ""
    try:
        year, month = (int(part) for part in raw.split("-"))
        return date(year, month, 1)
    except (ValueError, TypeError):
        return timezone.localdate().replace(day=1)


@require_any_permission(*STAFF_AUDIENCE)
def month(request):
    _guard()
    lang = _lang(request)
    first = _month(request)
    employee = None
    if request.GET.get("employee") or request.POST.get("employee"):
        employee = get_object_or_404(Employee, pk=request.GET.get("employee") or request.POST.get("employee"))
    error = ""
    if request.method == "POST":
        _manage(request)
        try:
            if request.POST.get("action") == "day":
                services.set_day(employee, request.POST.get("date"), {key: request.POST.get(key) for key in request.POST}, request.user, lang)
            elif request.POST.get("action") == "schedule":
                data = {key: request.POST.get(key) for key in request.POST}
                data["days_off"] = request.POST.getlist("days_off")
                services.save_schedule(employee, data, request.user, lang)
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            return _done(request, lang, f"{reverse('attendance:month')}?month={first:%Y-%m}&employee={employee.pk}")
    employees = [employee] if employee else Employee.objects.filter(active=True).select_related("schedule").order_by("name")
    sheets = services.month_sheet(employees, first.year, first.month)
    today_ = timezone.localdate()
    months = []
    cursor = today_.replace(day=1)
    for _n in range(12):
        months.append(cursor)
        cursor = (cursor.replace(day=1) - timezone.timedelta(days=1)).replace(day=1)
    return render(request, "attendance/month.html", _base(request, lang, "month", sheets=sheets, employee=employee, first=first, months=months,
                                                          error=error, schedule=services.schedule_for(employee) if employee else None,
                                                          balance=services.balance(employee, first.year) if employee else None))


@require_any_permission(*STAFF_AUDIENCE)
def leaves(request):
    _guard()
    lang = _lang(request)
    error = ""
    if request.method == "POST":
        _manage(request)
        action = request.POST.get("action")
        try:
            if action in ("approve", "reject"):
                services.decide(get_object_or_404(LeaveRequest, pk=request.POST.get("leave") or 0), action == "approve", request.user, lang)
            elif action == "cancel":
                services.cancel_leave(get_object_or_404(LeaveRequest, pk=request.POST.get("leave") or 0), request.user, lang)
            elif action == "new":
                employee = get_object_or_404(Employee, pk=request.POST.get("employee") or 0, active=True)
                services.request_leave(employee, {key: request.POST.get(key) for key in request.POST}, request.user, lang)
            elif action == "holiday":
                services.add_holiday({key: request.POST.get(key) for key in request.POST}, request.user, lang)
            elif action == "remove_holiday":
                from .models import Holiday

                holiday = get_object_or_404(Holiday, pk=request.POST.get("holiday") or 0)
                services._audit(request.user, "remove_holiday", holiday, {"date": str(holiday.date), "name": holiday.name})
                holiday.delete()
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            return _done(request, lang, reverse("attendance:leaves"))
    from .models import Holiday

    employees = list(Employee.objects.filter(active=True).order_by("name"))
    today_ = timezone.localdate()
    return render(request, "attendance/leaves.html", _base(
        request, lang, "leaves", error=error, employees=employees, kind_choices=LeaveKind.values, post=request.POST, today=today_,
        pending=LeaveRequest.objects.filter(status=LeaveStatus.PENDING).select_related("employee").order_by("start_date"),
        history=LeaveRequest.objects.exclude(status=LeaveStatus.PENDING).select_related("employee").order_by("-start_date")[:100],
        balances=[{"employee": employee, **services.balance(employee, today_.year)} for employee in employees],
        holidays=Holiday.objects.filter(date__gte=today_.replace(month=1, day=1))))


@require_any_permission(*STAFF_AUDIENCE)
def device_import(request):
    _guard()
    lang = _lang(request)
    words = WORDS[lang]
    error, result = "", None
    if request.method == "POST":
        _manage(request)
        upload = request.FILES.get("file")
        try:
            if upload is None:
                raise ValidationError(services.MESSAGES[lang]["file"])
            if upload.size > services.MAX_IMPORT_BYTES:
                raise ValidationError(services.MESSAGES[lang]["big"])
            result = services.import_punches(upload.read(), request.user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            result["text"] = words["result"].format(**{key: result[key] for key in ("punches", "days", "employees")})
            if result["unmatched"]:
                result["unmatched_text"] = words["unmatched"].format(ids="، ".join(result["unmatched"][:30]) if lang == "ar" else ", ".join(result["unmatched"][:30]))
            if result["bad"]:
                result["bad_text"] = words["bad"].format(n=result["bad"])
    return render(request, "attendance/import.html", _base(request, lang, "import", error=error, result=result))
