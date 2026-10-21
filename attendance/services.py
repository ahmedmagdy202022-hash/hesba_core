"""T11 (MARKET-PARITY): who came, who was late, who is on leave (HG-063).

Nothing here pays anyone or posts anything. A day is read from the employee's
schedule, the public holidays, their approved leave and the times recorded
for them (by the manager, by their own button, or from the fingerprint
device's file); what is not recorded for a past working day counts as absent.
The monthly sheet adds the days up for whoever pays salaries.
"""

import csv
import io
import re
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission
from staff.models import Employee

from .models import AttendanceDay, DayStatus, Holiday, LeaveKind, LeaveRequest, LeaveStatus, WorkSchedule

MODULE = "employees_technicians"
MANAGE = "master_data.manage_parties"
#: Leave that comes off the yearly balance (Egyptian practice: casual days are part of the 21).
FROM_BALANCE = (LeaveKind.ANNUAL, LeaveKind.CASUAL)
#: The biggest fingerprint file read in one go.
MAX_IMPORT_BYTES = 2 * 1024 * 1024

WEEKDAYS = {
    "ar": ("الاتنين", "التلات", "الأربع", "الخميس", "الجمعة", "السبت", "الحد"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
KIND_LABELS = {
    "ar": {"annual": "اعتيادي", "casual": "عارضة", "sick": "مرضي", "unpaid": "من غير مرتب", "other": "تانية"},
    "en": {"annual": "Annual", "casual": "Casual", "sick": "Sick", "unpaid": "Unpaid", "other": "Other"},
}
LEAVE_STATUS_LABELS = {
    "ar": {"pending": "مستنية الموافقة", "approved": "اتوافق عليها", "rejected": "اترفضت", "cancelled": "اتلغت"},
    "en": {"pending": "Waiting for approval", "approved": "Approved", "rejected": "Rejected", "cancelled": "Cancelled"},
}
STATE_LABELS = {
    "ar": {"present": "حاضر", "late": "متأخر", "absent": "غايب", "leave": "إجازة", "off": "راحة", "holiday": "عطلة رسمية",
           "missing": "لسه ماجاش", "out": "انصرف", "none": "—"},
    "en": {"present": "Present", "late": "Late", "absent": "Absent", "leave": "On leave", "off": "Day off", "holiday": "Public holiday",
           "missing": "Not in yet", "out": "Left", "none": "—"},
}

MESSAGES = {
    "ar": {"module": "الموظفون مقفولين في الإعدادات.", "time": "الوقت مش مظبوط؛ اكتبه زي 09:30.", "date": "التاريخ مش مظبوط.",
           "order": "وقت الانصراف لازم يكون بعد وقت الحضور.", "future": "مينفعش تسجّل حضور ليوم لسه ماجاش.",
           "no_employee": "حسابك مش مربوط بموظف؛ اطلب من المدير يربطه من شاشة الموظفين.", "already_in": "سجّلت حضورك النهارده الساعة {time}.",
           "not_in": "سجّل حضورك الأول.", "range": "تاريخ النهاية لازم يكون بعد البداية أو نفس اليوم.", "no_days": "الفترة دي كلها أيام راحة أو عطلات.",
           "overlap": "فيه إجازة تانية في نفس الأيام.", "decided": "الطلب ده اتقرر قبل كده.", "balance": "الرصيد مش مكفي: فاضل {left} يوم.",
           "hours": "ميعاد الانصراف لازم يكون بعد ميعاد الحضور.", "grace": "السماح من 0 لـ 240 دقيقة.", "leave_days": "أيام الإجازة من 0 لـ 366.",
           "holiday": "اكتب اسم العطلة وتاريخها.", "file": "ارفع ملف CSV من جهاز البصمة.", "big": "الملف أكبر من 2 ميجا؛ قسّمه على شهور.",
           "columns": "مش لاقي عمود رقم الموظف وعمود التاريخ والوقت في الملف.", "kind": "اختار نوع الإجازة."},
    "en": {"module": "Employees are switched off in Settings.", "time": "The time is not valid; write it like 09:30.", "date": "The date is not valid.",
           "order": "The time out must be after the time in.", "future": "Attendance cannot be recorded for a day that has not come.",
           "no_employee": "Your login is not linked to an employee; ask the manager to link it on the employees screen.", "already_in": "You checked in today at {time}.",
           "not_in": "Check in first.", "range": "The end date must be on or after the start.", "no_days": "That period is all days off or holidays.",
           "overlap": "Another leave covers some of those days.", "decided": "This request was already decided.", "balance": "Not enough balance: {left} days left.",
           "hours": "The end of the day must be after its start.", "grace": "The grace is 0 to 240 minutes.", "leave_days": "Leave days are 0 to 366.",
           "holiday": "Enter the holiday's name and date.", "file": "Upload a CSV file from the fingerprint device.", "big": "The file is over 2 MB; split it by month.",
           "columns": "Could not find the employee number and the date and time columns in the file.", "kind": "Choose the kind of leave."},
}


# ---- who may do what ----

def enabled():
    from settings_core.setup_services import module_is_enabled

    return module_is_enabled(MODULE)


def can_view(user):
    from reports.navigation import sees_staff

    return sees_staff(user)


def can_manage(user):
    return user_has_permission(user, MANAGE)


def own_employee(user):
    if not getattr(user, "is_authenticated", False):
        return None
    return Employee.objects.filter(user=user, active=True).first()


def is_linked_employee(user):
    """The menu: "my attendance" shows for anyone whose login is an employee's."""

    return own_employee(user) is not None


def _require_manage(user, words):
    if not enabled():
        raise ValidationError(words["module"])
    if not can_manage(user):
        raise PermissionDenied("Attendance needs master_data.manage_parties.")


def _audit(user, action, obj, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="attendance", action=action, object_type=f"attendance.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data={}, after_data=after)


# ---- reading values ----

def _time(raw, words):
    if raw in (None, ""):
        return None
    if isinstance(raw, time):
        return raw
    text = str(raw).strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?::\d{2})?\s*([AaPp][Mm]|ص|م)?", text)
    if not match:
        raise ValidationError(words["time"])
    hour, minute, half = int(match.group(1)), int(match.group(2)), (match.group(3) or "").lower()
    if half in ("pm", "م") and hour < 12:
        hour += 12
    if half in ("am", "ص") and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise ValidationError(words["time"])
    return time(hour, minute)


def _date(raw, words, default=None):
    if raw in (None, ""):
        return default
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise ValidationError(words["date"])


def minutes_between(start, end):
    return int((datetime.combine(date.min, end) - datetime.combine(date.min, start)).total_seconds() // 60)


def now_time():
    return timezone.localtime().time().replace(second=0, microsecond=0)


# ---- the schedule ----

def schedule_for(employee):
    """The employee's schedule, or the default one (09:00-17:00, Friday off) when none was saved."""

    try:
        return employee.schedule
    except WorkSchedule.DoesNotExist:
        return WorkSchedule(employee=employee)


@transaction.atomic
def save_schedule(employee, data, user, lang="ar"):
    words = MESSAGES[lang]
    _require_manage(user, words)
    start, end = _time(data.get("start_time"), words) or time(9, 0), _time(data.get("end_time"), words) or time(17, 0)
    if end <= start:
        raise ValidationError(words["hours"])
    try:
        grace = int(str(data.get("grace_minutes") or "15").strip())
    except ValueError:
        raise ValidationError(words["grace"])
    try:
        leave_days = int(str(data.get("annual_leave_days") or "21").strip())
    except ValueError:
        raise ValidationError(words["leave_days"])
    if not 0 <= grace <= 240:
        raise ValidationError(words["grace"])
    if not 0 <= leave_days <= 366:
        raise ValidationError(words["leave_days"])
    days_off = data.get("days_off")
    if isinstance(days_off, (list, tuple)):
        days_off = ",".join(sorted({str(int(day)) for day in days_off if str(day).isdigit() and int(day) <= 6}))
    schedule, _created = WorkSchedule.objects.update_or_create(employee=employee, defaults={
        "start_time": start, "end_time": end, "days_off": days_off or "", "grace_minutes": grace, "annual_leave_days": leave_days,
        "device_id": (data.get("device_id") or "").strip()[:30]})
    _audit(user, "save_schedule", schedule, {"employee": employee.code, "start": str(start), "end": str(end), "days_off": schedule.days_off})
    return schedule


# ---- recording the day ----

@transaction.atomic
def check(employee, user, lang="ar", source="self", at=None):
    """The check-in / check-out button: the first press of the day is the time in, the next ones move the time out."""

    words = MESSAGES[lang]
    if not enabled():
        raise ValidationError(words["module"])
    today = timezone.localdate()
    at = at or now_time()
    record = AttendanceDay.objects.select_for_update().filter(employee=employee, date=today).first()
    if record is None or record.check_in is None:
        record = record or AttendanceDay(employee=employee, date=today)
        record.status, record.check_in, record.source, record.recorded_by = DayStatus.PRESENT, at, source, user
        action = "check_in"
    else:
        if at < record.check_in:
            raise ValidationError(words["order"])
        record.check_out, record.recorded_by = at, user
        action = "check_out"
    record.save()
    _audit(user, action, record, {"employee": employee.code, "date": str(today), "time": at.strftime("%H:%M"), "source": source})
    return record, action


@transaction.atomic
def set_day(employee, day, data, user, lang="ar"):
    """The manager's own entry for one day: the times, or absent, with a note. Empty times and not absent clear the day."""

    words = MESSAGES[lang]
    _require_manage(user, words)
    day = _date(day, words)
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    absent = data.get("absent") in (True, "1", "on", "true")
    check_in, check_out = _time(data.get("check_in"), words), _time(data.get("check_out"), words)
    if check_in and check_out and check_out < check_in:
        raise ValidationError(words["order"])
    record = AttendanceDay.objects.select_for_update().filter(employee=employee, date=day).first()
    if not absent and check_in is None and check_out is None:
        if record is not None:
            _audit(user, "clear_day", record, {"employee": employee.code, "date": str(day)})
            record.delete()
        return None
    if check_out and not check_in and not absent:
        raise ValidationError(words["not_in"])
    record = record or AttendanceDay(employee=employee, date=day)
    record.status = DayStatus.ABSENT if absent else DayStatus.PRESENT
    record.check_in, record.check_out = (None, None) if absent else (check_in, check_out)
    record.note, record.source, record.recorded_by = (data.get("note") or "").strip()[:255], "manual", user
    record.save()
    _audit(user, "set_day", record, {"employee": employee.code, "date": str(day), "status": record.status,
                                     "in": str(record.check_in or ""), "out": str(record.check_out or "")})
    return record


# ---- what each day was ----

def holidays_between(start, end):
    return {holiday.date: holiday for holiday in Holiday.objects.filter(date__gte=start, date__lte=end)}


def _approved_leave(employees, start, end):
    """{(employee pk, date): leave} for the approved leave touching [start, end]."""

    covered = {}
    for leave in LeaveRequest.objects.filter(employee__in=employees, status=LeaveStatus.APPROVED, start_date__lte=end, end_date__gte=start):
        day = max(leave.start_date, start)
        while day <= min(leave.end_date, end):
            covered[(leave.employee_id, day)] = leave
            day += timedelta(days=1)
    return covered


def day_state(schedule, day, today, record=None, leave=None, holiday=None, started=None, moment=None):
    """One employee's day as the sheet shows it: its kind and the minutes late, early and over.

    ``moment`` is the time of day now, for today's row ("not in yet" turns into "late" once the grace has passed)."""

    row = {"date": day, "state": "none", "late": 0, "early": 0, "over": 0, "check_in": None, "check_out": None, "record": record,
           "leave": leave, "holiday": holiday}
    if record is not None and record.status == DayStatus.PRESENT and record.check_in is not None:
        row.update(check_in=record.check_in, check_out=record.check_out, state="present")
        if minutes_between(schedule.start_time, record.check_in) > schedule.grace_minutes:
            row.update(state="late", late=minutes_between(schedule.start_time, record.check_in))
        if record.check_out is not None:
            if record.check_out > schedule.end_time:
                row["over"] = minutes_between(schedule.end_time, record.check_out)
            elif record.check_out < schedule.end_time:
                row["early"] = minutes_between(record.check_out, schedule.end_time)
        return row
    if holiday is not None:
        row["state"] = "holiday"
    elif day.weekday() in schedule.off_weekdays():
        row["state"] = "off"
    elif leave is not None:
        row["state"] = "leave"
    elif record is not None and record.status == DayStatus.ABSENT:
        row["state"] = "absent"
    elif started is not None and day < started:
        pass  # before they joined: nothing to count
    elif day < today:
        row["state"] = "absent"
    elif day == today:
        row["state"] = "missing"
    return row


def _started(employee):
    return timezone.localtime(employee.created_at).date() if employee.created_at else None


def today_board(today=None):
    """Every active employee and how their day stands now."""

    today = today or timezone.localdate()
    moment = now_time()
    employees = list(Employee.objects.filter(active=True).select_related("schedule").order_by("name"))
    records = {row.employee_id: row for row in AttendanceDay.objects.filter(employee__in=employees, date=today)}
    leave = _approved_leave(employees, today, today)
    holiday = holidays_between(today, today).get(today)
    rows = []
    for employee in employees:
        schedule = schedule_for(employee)
        row = day_state(schedule, today, today, records.get(employee.pk), leave.get((employee.pk, today)), holiday, moment=moment)
        if row["state"] == "missing" and minutes_between(schedule.start_time, moment) > schedule.grace_minutes:
            row["overdue"] = True
        row.update(employee=employee, schedule=schedule)
        rows.append(row)
    counts = {key: sum(1 for row in rows if row["state"] == key) for key in ("present", "late", "missing", "absent", "leave", "off", "holiday")}
    counts["in"] = counts["present"] + counts["late"]
    return rows, counts


def month_days(year, month, today=None):
    today = today or timezone.localdate()
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    return first, min(last, today) if first <= today else first - timedelta(days=1)


def month_sheet(employees, year, month, today=None):
    """For each employee: the month day by day (up to today) and the totals the payroll needs."""

    today = today or timezone.localdate()
    first, last = month_days(year, month, today)
    employees = list(employees)
    records = {(row.employee_id, row.date): row for row in AttendanceDay.objects.filter(employee__in=employees, date__gte=first, date__lte=last)}
    leave = _approved_leave(employees, first, last)
    holidays = holidays_between(first, last)
    sheets = []
    for employee in employees:
        schedule, started = schedule_for(employee), _started(employee)
        days = []
        day = first
        while day <= last:
            days.append(day_state(schedule, day, today, records.get((employee.pk, day)), leave.get((employee.pk, day)), holidays.get(day), started))
            day += timedelta(days=1)
        totals = {"present": sum(1 for row in days if row["state"] in ("present", "late")), "late_days": sum(1 for row in days if row["state"] == "late"),
                  "late": sum(row["late"] for row in days), "early": sum(row["early"] for row in days), "over": sum(row["over"] for row in days),
                  "absent": sum(1 for row in days if row["state"] == "absent"), "leave": sum(1 for row in days if row["state"] == "leave"),
                  "off": sum(1 for row in days if row["state"] in ("off", "holiday"))}
        totals["unpaid"] = sum(1 for row in days if row["state"] == "leave" and row["leave"].kind == LeaveKind.UNPAID)
        sheets.append({"employee": employee, "schedule": schedule, "days": days, "totals": totals})
    return sheets


# ---- leave ----

def working_days(schedule, start, end, holidays=None):
    holidays = holidays if holidays is not None else holidays_between(start, end)
    off = schedule.off_weekdays()
    count, day = 0, start
    while day <= end:
        if day.weekday() not in off and day not in holidays:
            count += 1
        day += timedelta(days=1)
    return count


def balance(employee, year=None):
    """The yearly leave: what the schedule gives, what approved annual and casual leave took, what is asked for, and what is left."""

    year = year or timezone.localdate().year
    schedule = schedule_for(employee)
    rows = LeaveRequest.objects.filter(employee=employee, kind__in=FROM_BALANCE, start_date__year=year)
    taken = sum(row.days for row in rows.filter(status=LeaveStatus.APPROVED))
    asked = sum(row.days for row in rows.filter(status=LeaveStatus.PENDING))
    return {"year": year, "entitled": schedule.annual_leave_days, "taken": taken, "pending": asked, "left": schedule.annual_leave_days - taken}


def _overlaps(employee, start, end, exclude=None):
    rows = LeaveRequest.objects.filter(employee=employee, status__in=(LeaveStatus.PENDING, LeaveStatus.APPROVED), start_date__lte=end, end_date__gte=start)
    if exclude is not None:
        rows = rows.exclude(pk=exclude.pk)
    return rows.exists()


@transaction.atomic
def request_leave(employee, data, user, lang="ar"):
    """A leave asked for: by the employee for themselves, or by the manager for anyone (the manager's own requests are approved at once)."""

    words = MESSAGES[lang]
    if not enabled():
        raise ValidationError(words["module"])
    manager = can_manage(user)
    if not manager and (own_employee(user) is None or own_employee(user).pk != employee.pk):
        raise PermissionDenied("An employee asks for their own leave; the manager for anyone.")
    kind = data.get("kind") or ""
    if kind not in LeaveKind.values:
        raise ValidationError(words["kind"])
    start = _date(data.get("start_date"), words)
    end = _date(data.get("end_date"), words, start)
    if start is None or end < start:
        raise ValidationError(words["range"])
    days = working_days(schedule_for(employee), start, end)
    if days <= 0:
        raise ValidationError(words["no_days"])
    Employee.objects.select_for_update().filter(pk=employee.pk).first()  # one request at a time per employee
    if _overlaps(employee, start, end):
        raise ValidationError(words["overlap"])
    leave = LeaveRequest.objects.create(employee=employee, kind=kind, start_date=start, end_date=end, days=days,
                                        reason=(data.get("reason") or "").strip()[:255], created_by=user)
    _audit(user, "request_leave", leave, {"employee": employee.code, "kind": kind, "from": str(start), "to": str(end), "days": days},
           AuditEventType.CREATE)
    if manager and data.get("approve") in (True, "1", "on"):
        decide(leave, True, user, lang)
        leave.refresh_from_db()
    return leave


@transaction.atomic
def decide(leave, approve, user, lang="ar"):
    words = MESSAGES[lang]
    _require_manage(user, words)
    leave = LeaveRequest.objects.select_for_update().get(pk=leave.pk)
    if leave.status != LeaveStatus.PENDING:
        raise ValidationError(words["decided"])
    if approve:
        if _overlaps(leave.employee, leave.start_date, leave.end_date, exclude=leave):
            raise ValidationError(words["overlap"])
        if leave.kind in FROM_BALANCE:
            left = balance(leave.employee, leave.start_date.year)["left"]
            if leave.days > left:
                raise ValidationError(words["balance"].format(left=left))
    leave.status = LeaveStatus.APPROVED if approve else LeaveStatus.REJECTED
    leave.decided_by, leave.decided_at = user, timezone.now()
    leave.save(update_fields=["status", "decided_by", "decided_at"])
    _audit(user, "approve_leave" if approve else "reject_leave", leave, {"employee": leave.employee.code, "days": leave.days})
    return leave


@transaction.atomic
def cancel_leave(leave, user, lang="ar"):
    """The employee takes back a request still waiting; the manager can cancel any leave."""

    words = MESSAGES[lang]
    leave = LeaveRequest.objects.select_for_update().select_related("employee").get(pk=leave.pk)
    own = own_employee(user)
    if not can_manage(user) and not (own is not None and own.pk == leave.employee_id and leave.status == LeaveStatus.PENDING):
        raise PermissionDenied("Only the manager cancels a decided leave.")
    if leave.status in (LeaveStatus.REJECTED, LeaveStatus.CANCELLED):
        raise ValidationError(words["decided"])
    leave.status = LeaveStatus.CANCELLED
    leave.save(update_fields=["status"])
    _audit(user, "cancel_leave", leave, {"employee": leave.employee.code})
    return leave


@transaction.atomic
def add_holiday(data, user, lang="ar"):
    words = MESSAGES[lang]
    _require_manage(user, words)
    day, name = _date(data.get("date"), words), (data.get("name") or "").strip()[:120]
    if day is None or not name:
        raise ValidationError(words["holiday"])
    holiday, _created = Holiday.objects.update_or_create(date=day, defaults={"name": name})
    _audit(user, "add_holiday", holiday, {"date": str(day), "name": name})
    return holiday


# ---- the fingerprint device's file ----

_ID_HEADERS = ("acno", "ac-no", "no", "id", "userid", "enrollno", "enrollnumber", "employeeid", "empid", "employeeno", "personid", "badgenumber",
               "code", "employee", "رقم", "الرقم", "كود", "الكود", "رقمالبصمه", "رقمالموظف", "كودالموظف")
_STAMP_HEADERS = ("time", "datetime", "checktime", "punchtime", "timestamp", "logtime", "attendancetime", "التاريخوالوقت", "الوقت", "وقتالبصمه")
_DATE_HEADERS = ("date", "التاريخ", "اليوم")
_STAMP_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                  "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
                  "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y")


def _key(header):
    return re.sub(r"[\s_.\-/]+", "", str(header or "").replace("﻿", "").strip().lower()).replace("ة", "ه").replace("أ", "ا").replace("إ", "ا")


def _stamp(text):
    text = str(text or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    for fmt in _STAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _day(text):
    text = str(text or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def read_punches(raw, lang="ar"):
    """[(device id, datetime)] from a fingerprint export: an id column and one date-time column, or a date and a time column."""

    words = MESSAGES[lang]
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValidationError(words["big"])
    for encoding in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if len(rows) < 2:
        raise ValidationError(words["columns"])
    headers = [_key(cell) for cell in rows[0]]
    id_col = next((index for index, header in enumerate(headers) if header in _ID_HEADERS), None)
    stamp_col = next((index for index, header in enumerate(headers) if header in _STAMP_HEADERS and index != id_col
                      and any(_stamp(row[index]) for row in rows[1:6] if index < len(row))), None)
    date_col = time_col = None
    if stamp_col is None:
        date_col = next((index for index, header in enumerate(headers) if header in _DATE_HEADERS), None)
        time_col = next((index for index, header in enumerate(headers) if header in _STAMP_HEADERS and index != id_col), None)
    if id_col is None or (stamp_col is None and (date_col is None or time_col is None)):
        raise ValidationError(words["columns"])
    punches, bad = [], 0
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        try:
            device_id = row[id_col].strip()
            if stamp_col is not None:
                moment = _stamp(row[stamp_col])
            else:
                day, at = _day(row[date_col]), _time(row[time_col], words)
                moment = datetime.combine(day, at) if day and at else None
        except (IndexError, ValidationError):
            moment = None
        if not device_id or moment is None:
            bad += 1
            continue
        punches.append((device_id, moment))
    return punches, bad


def _employee_by_device():
    found = {}
    for employee in Employee.objects.filter(active=True).select_related("schedule"):
        schedule = schedule_for(employee)
        for key in (schedule.device_id if schedule.pk else "", employee.code, employee.phone):
            if key:
                found.setdefault(key.strip().lower(), employee)
                if key.isdigit():
                    found.setdefault(str(int(key)), employee)  # "0007" on the device is employee "7"
    return found


@transaction.atomic
def import_punches(raw, user, lang="ar"):
    """Apply a fingerprint file: each employee's first punch of a day is the time in, the last one the time out.

    Running the same file twice changes nothing; a day already recorded keeps its earliest in and latest out."""

    words = MESSAGES[lang]
    _require_manage(user, words)
    punches, bad = read_punches(raw, lang)
    people = _employee_by_device()
    days, unmatched = {}, set()
    today = timezone.localdate()
    for device_id, moment in punches:
        employee = people.get(device_id.lower()) or (people.get(str(int(device_id))) if device_id.isdigit() else None)
        if employee is None:
            unmatched.add(device_id)
            continue
        if moment.date() > today:
            bad += 1
            continue
        at = moment.time().replace(second=0, microsecond=0)
        first, last = days.get((employee.pk, moment.date()), (at, at))
        days[(employee.pk, moment.date())] = (min(first, at), max(last, at))
    by_pk = {employee.pk: employee for employee in people.values()}
    updated = 0
    for (employee_pk, day), (first, last) in sorted(days.items()):
        record = AttendanceDay.objects.select_for_update().filter(employee_id=employee_pk, date=day).first()
        record = record or AttendanceDay(employee_id=employee_pk, date=day)
        check_in = min(record.check_in, first) if record.check_in and record.status == DayStatus.PRESENT else first
        check_out = last if last > check_in else None
        if record.check_out and record.status == DayStatus.PRESENT:
            check_out = max(record.check_out, check_out) if check_out else record.check_out
        record.status, record.check_in, record.check_out, record.source, record.recorded_by = DayStatus.PRESENT, check_in, check_out, "device", user
        record.save()
        updated += 1
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="attendance", action="import_punches", object_type="attendance.AttendanceDay",
                            object_id="", before_data={}, after_data={"punches": len(punches), "days": updated, "unmatched": sorted(unmatched)[:50]})
    return {"punches": len(punches), "days": updated, "employees": len({pk for pk, _day in days}), "unmatched": sorted(unmatched), "bad": bad,
            "names": sorted({by_pk[pk].name for pk, _day in days if pk in by_pk})}
