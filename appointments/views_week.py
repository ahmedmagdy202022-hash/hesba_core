"""S10 UI: the week calendar screen. A thin page; the data and the moves go through week.json and move/ (see views.py)."""

from datetime import timedelta

from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from permissions.decorators import require_permission
from permissions.services import user_has_permission
from staff.models import Employee

from .views import BOOK, VIEW, _base, _day, _lang


#: Egypt's week starts on Saturday (Python: Monday is 0, Saturday is 5).
WEEK_STARTS_ON = 5

WORDS = {
    "ar": {
        "week": "الأسبوع", "day": "اليوم", "prev": "الأسبوع اللي فات", "next": "الأسبوع الجاي", "today": "النهارده", "new": "ميعاد جديد",
        "help_move": "اسحب الميعاد لوقت تاني أو لفني تاني. لو الفني عنده ميعاد تاني الحركة بتتلغي.",
        "help_phone": "اضغط «انقل» لتغيير الوقت أو الفني.", "help_read": "العرض للقراءة بس؛ نقل المواعيد لصاحب الصلاحية.",
        "legend": "الألوان", "booked": "محجوز", "confirmed": "متأكد", "arrived": "جاري", "done": "خلص", "locked": "مقفول: مش بيتحرك",
        "cancelled": "اتلغى", "no_show": "مجاش",
        "week_days": "أيام الأسبوع", "grid": "جدول الفنيين", "all": "الكل", "staff": "الفني", "unassigned": "من غير موظف", "loading": "بنحمّل المواعيد…",
        "load_failed": "مقدرناش نحمّل المواعيد. جرّب تاني.", "retry": "جرّب تاني", "empty_day": "مفيش مواعيد في اليوم ده.", "off_count": "مفيش مواعيد",
        "count_one": "ميعاد", "count_many": "مواعيد", "move": "انقل", "move_title": "انقل الميعاد", "now": "دلوقتي",
        "time": "الساعة", "duration_fixed": "المدة ثابتة {n} دقيقة زي الميعاد.", "keep": "سيب", "moving": "بينقل…", "moved": "اتنقل الميعاد.",
        "moved_to": "هينتقل لـ {staff} · {time}", "back": "الميعاد رجع مكانه.", "closed": "خلص — مقفول", "details": "فتح الميعاد",
        "drag_handle": "اسحب أو اضغط لنقل الميعاد", "network": "حصلت مشكلة في الاتصال. الميعاد رجع مكانه.", "no_staff": "من غير",
        "bad_time": "اكتب ساعة صحيحة.", "current": "دلوقتي", "calendar": "تقويم المواعيد", "agenda": "قائمة اليوم", "week_of": "أسبوع {a} – {b}",
    },
    "en": {
        "week": "Week", "day": "Day", "prev": "Previous week", "next": "Next week", "today": "Today", "new": "New appointment",
        "help_move": "Drag an appointment to another time or another employee. If that employee already has one, the move is cancelled.",
        "help_phone": "Tap “Move” to change the time or the employee.", "help_read": "Read-only view; moving appointments needs the permission.",
        "legend": "Colours", "booked": "Booked", "confirmed": "Confirmed", "arrived": "In progress", "done": "Done", "locked": "Locked: cannot move",
        "cancelled": "Cancelled", "no_show": "No show",
        "week_days": "Days of the week", "grid": "Employees' schedule", "all": "All", "staff": "Employee", "unassigned": "Unassigned", "loading": "Loading appointments…",
        "load_failed": "Could not load the appointments. Try again.", "retry": "Try again", "empty_day": "No appointments on this day.", "off_count": "No appointments",
        "count_one": "appointment", "count_many": "appointments", "move": "Move", "move_title": "Move the appointment", "now": "Now",
        "time": "Time", "duration_fixed": "The duration stays {n} minutes.", "keep": "Keep", "moving": "Moving…", "moved": "Appointment moved.",
        "moved_to": "Moving to {staff} · {time}", "back": "The appointment snapped back.", "closed": "Done: locked", "details": "Open the appointment",
        "drag_handle": "Drag, or press, to move the appointment", "network": "Connection problem. The appointment snapped back.", "no_staff": "None",
        "bad_time": "Enter a valid time.", "current": "Now", "calendar": "Appointments calendar", "agenda": "Day list", "week_of": "Week {a} – {b}",
    },
}


def week_start_of(day):
    return day - timedelta(days=(day.weekday() - WEEK_STARTS_ON) % 7)


@require_permission(VIEW)
def week(request):
    lang = _lang(request)
    day = _day(request.GET.get("day"))
    now = timezone.localtime()
    staff = [{"id": employee.pk, "name": employee.name} for employee in Employee.objects.filter(active=True)]
    return render(request, "appointments/week.html", _base(
        request, day=day, week_start=week_start_of(day), today=timezone.localdate(), now_minutes=now.hour * 60 + now.minute,
        can_move=user_has_permission(request.user, BOOK), staff=staff, week_words=WORDS[lang],
        agenda_url=reverse("appointments:agenda"), week_json_url=reverse("appointments:week_json"),
        move_url_template=reverse("appointments:move", args=[0]), detail_url_template=reverse("appointments:detail", args=[0]),
    ))
