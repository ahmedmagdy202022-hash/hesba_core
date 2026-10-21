"""T11 (MARKET-PARITY): who came in, who was late, leave and its balance, and the fingerprint file (HG-063)."""

from datetime import date, time, timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from staff.models import Employee
from staff.services import save_employee

from . import services
from .models import AttendanceDay, DayStatus, Holiday, LeaveRequest, LeaveStatus

MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,employees_technicians"
#: A Wednesday in a past month, so every day of the test is in the past.
WEDNESDAY = date(2026, 9, 16)


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class AttendanceSetup(TestCase):
    def setUp(self):
        prepared_client("services", "maintenance", MODULES)
        self.owner = person(RoleCode.OWNER, "at_owner")
        self.cashier = person(RoleCode.CASHIER, "at_cashier")
        self.ali = save_employee({"name": "علي حسن", "title": "فني"}, self.owner)
        self.mona = save_employee({"name": "منى سعيد", "title": "استقبال", "user": str(self.cashier.pk)}, self.owner)
        Employee.objects.update(created_at=timezone.make_aware(timezone.datetime(2026, 1, 1)))
        self.ali.refresh_from_db()
        self.mona.refresh_from_db()
        services.save_schedule(self.ali, {"start_time": "09:00", "end_time": "17:00", "days_off": ["4"], "grace_minutes": "15",
                                          "annual_leave_days": "21", "device_id": "7"}, self.owner)

    def day(self, employee, day, check_in, check_out=None):
        return AttendanceDay.objects.create(employee=employee, date=day, check_in=check_in, check_out=check_out)


class DayStateTests(AttendanceSetup):
    def test_late_overtime_absent_off_holiday_and_leave(self):
        self.day(self.ali, WEDNESDAY, time(9, 40), time(18, 30))  # 40 late, 90 over
        self.day(self.ali, WEDNESDAY + timedelta(days=1), time(9, 10), time(16, 30))  # within grace, 30 early
        Holiday.objects.create(date=WEDNESDAY + timedelta(days=4), name="عطلة")  # Sunday
        services.request_leave(self.ali, {"kind": "annual", "start_date": WEDNESDAY + timedelta(days=5), "end_date": WEDNESDAY + timedelta(days=6),
                                          "approve": "1"}, self.owner)
        today = WEDNESDAY + timedelta(days=7)
        with mock.patch("attendance.services.timezone.localdate", return_value=today):
            [sheet] = services.month_sheet([self.ali], 2026, 9, today)
        by_day = {row["date"]: row for row in sheet["days"]}
        self.assertEqual((by_day[WEDNESDAY]["state"], by_day[WEDNESDAY]["late"], by_day[WEDNESDAY]["over"]), ("late", 40, 90))
        thursday = by_day[WEDNESDAY + timedelta(days=1)]
        self.assertEqual((thursday["state"], thursday["late"], thursday["early"]), ("present", 0, 30))
        self.assertEqual(by_day[WEDNESDAY + timedelta(days=2)]["state"], "off")  # Friday
        self.assertEqual(by_day[WEDNESDAY + timedelta(days=3)]["state"], "absent")  # Saturday, nothing recorded
        self.assertEqual(by_day[WEDNESDAY + timedelta(days=4)]["state"], "holiday")
        self.assertEqual(by_day[WEDNESDAY + timedelta(days=5)]["state"], "leave")
        totals = sheet["totals"]
        self.assertEqual((totals["late_days"], totals["late"], totals["over"], totals["leave"]), (1, 40, 90, 2))

    def test_the_manager_corrects_and_clears_a_day(self):
        services.set_day(self.ali, WEDNESDAY, {"check_in": "9:05 ص", "check_out": "5:30 م"}, self.owner)
        record = AttendanceDay.objects.get(employee=self.ali, date=WEDNESDAY)
        self.assertEqual((record.check_in, record.check_out), (time(9, 5), time(17, 30)))
        services.set_day(self.ali, WEDNESDAY, {"absent": "1", "note": "من غير إذن"}, self.owner)
        record.refresh_from_db()
        self.assertEqual((record.status, record.check_in), (DayStatus.ABSENT, None))
        services.set_day(self.ali, WEDNESDAY, {}, self.owner)
        self.assertFalse(AttendanceDay.objects.filter(employee=self.ali, date=WEDNESDAY).exists())
        with self.assertRaises(ValidationError):
            services.set_day(self.ali, WEDNESDAY, {"check_in": "17:00", "check_out": "09:00"}, self.owner)
        with self.assertRaises(ValidationError):
            services.set_day(self.ali, timezone.localdate() + timedelta(days=1), {"check_in": "09:00"}, self.owner)
        with self.assertRaises(PermissionDenied):
            services.set_day(self.ali, WEDNESDAY, {"check_in": "09:00"}, self.cashier)


class CheckInTests(AttendanceSetup):
    def test_an_employee_checks_in_and_out_from_their_own_page(self):
        self.client.force_login(self.cashier)
        page = self.client.get(reverse("attendance:me") + "?lang=ar")
        self.assertContains(page, "data-self-check")
        with mock.patch("attendance.services.now_time", return_value=time(8, 55)):
            self.client.post(reverse("attendance:me"), {"lang": "ar", "action": "check"})
        with mock.patch("attendance.services.now_time", return_value=time(17, 10)):
            self.client.post(reverse("attendance:me"), {"lang": "ar", "action": "check"})
        record = AttendanceDay.objects.get(employee=self.mona, date=timezone.localdate())
        self.assertEqual((record.check_in, record.check_out, record.source), (time(8, 55), time(17, 10), "self"))
        self.assertEqual(self.client.get(reverse("attendance:today")).status_code, 403)  # the team's page is the manager's

    def test_the_menu_shows_my_attendance_only_to_employees_who_do_not_manage(self):
        self.client.force_login(self.cashier)
        self.assertContains(self.client.get(reverse("dashboard_snapshot") + "?lang=ar"), 'href="/staff/attendance/me/')
        self.client.force_login(self.owner)
        home = self.client.get(reverse("dashboard_snapshot") + "?lang=ar")
        self.assertContains(home, 'href="/staff/attendance/?lang=ar"')
        self.assertNotContains(home, 'href="/staff/attendance/me/')

    def test_a_login_with_no_employee_has_no_own_page(self):
        stranger = person(RoleCode.CASHIER, "at_stranger")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse("attendance:me")).status_code, 403)

    def test_the_manager_records_today_from_the_board(self):
        self.client.force_login(self.owner)
        with mock.patch("attendance.services.now_time", return_value=time(9, 30)):
            self.client.post(reverse("attendance:today"), {"lang": "ar", "action": "check", "employee": self.ali.pk})
            board = self.client.get(reverse("attendance:today") + "?lang=ar")
        self.client.post(reverse("attendance:today"), {"lang": "ar", "action": "absent", "employee": self.mona.pk})
        self.assertContains(board, 'data-state="late"')
        self.assertEqual(AttendanceDay.objects.get(employee=self.mona, date=timezone.localdate()).status, DayStatus.ABSENT)


class LeaveTests(AttendanceSetup):
    def test_leave_counts_working_days_and_comes_off_the_balance(self):
        leave = services.request_leave(self.ali, {"kind": "annual", "start_date": WEDNESDAY, "end_date": WEDNESDAY + timedelta(days=6)}, self.owner)
        self.assertEqual(leave.days, 6)  # the Friday in between is not counted
        self.assertEqual(services.balance(self.ali, 2026)["pending"], 6)
        services.decide(leave, True, self.owner)
        self.assertEqual(services.balance(self.ali, 2026)["left"], 15)
        sick = services.request_leave(self.ali, {"kind": "sick", "start_date": WEDNESDAY + timedelta(days=14)}, self.owner)
        services.decide(sick, True, self.owner)
        self.assertEqual(services.balance(self.ali, 2026)["left"], 15)  # sick leave does not come off the balance

    def test_overlaps_a_short_balance_and_a_decided_request_are_refused(self):
        services.request_leave(self.ali, {"kind": "annual", "start_date": WEDNESDAY, "end_date": WEDNESDAY + timedelta(days=1)}, self.owner)
        with self.assertRaises(ValidationError):
            services.request_leave(self.ali, {"kind": "casual", "start_date": WEDNESDAY + timedelta(days=1)}, self.owner)
        services.save_schedule(self.ali, {"start_time": "09:00", "end_time": "17:00", "days_off": ["4"], "annual_leave_days": "1"}, self.owner)
        leave = LeaveRequest.objects.get(employee=self.ali)
        with self.assertRaises(ValidationError):  # two days asked, one left
            services.decide(leave, True, self.owner)
        services.decide(leave, False, self.owner)
        with self.assertRaises(ValidationError):
            services.decide(leave, True, self.owner)
        with self.assertRaises(ValidationError):  # a Friday alone is no working day
            services.request_leave(self.ali, {"kind": "annual", "start_date": WEDNESDAY + timedelta(days=2)}, self.owner)

    def test_an_employee_asks_for_their_own_leave_only_and_the_manager_decides(self):
        self.client.force_login(self.cashier)
        self.client.post(reverse("attendance:me"), {"lang": "ar", "action": "leave", "kind": "casual", "start_date": WEDNESDAY.isoformat()})
        leave = LeaveRequest.objects.get(employee=self.mona)
        self.assertEqual(leave.status, LeaveStatus.PENDING)
        with self.assertRaises(PermissionDenied):
            services.request_leave(self.ali, {"kind": "annual", "start_date": WEDNESDAY}, self.cashier)
        with self.assertRaises(PermissionDenied):
            services.decide(leave, True, self.cashier)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("attendance:leaves") + "?lang=ar")
        self.assertContains(page, f'data-approve="{leave.pk}"')
        self.client.post(reverse("attendance:leaves"), {"lang": "ar", "action": "approve", "leave": leave.pk})
        leave.refresh_from_db()
        self.assertEqual((leave.status, leave.decided_by), (LeaveStatus.APPROVED, self.owner))


class CodexFixesTests(AttendanceSetup):
    """Codex on #263: leave from the balance stays in one year, and the demo's waiting leave falls on a working day."""

    def test_annual_leave_across_new_year_is_split_but_sick_leave_is_not(self):
        with self.assertRaises(ValidationError):
            services.request_leave(self.ali, {"kind": "annual", "start_date": date(2026, 12, 30), "end_date": date(2027, 1, 3)}, self.owner)
        sick = services.request_leave(self.ali, {"kind": "sick", "start_date": date(2026, 12, 30), "end_date": date(2027, 1, 3)}, self.owner)
        self.assertEqual(sick.days, 4)  # Friday 1 January is the day off

    def test_the_demo_leave_lands_on_a_working_day_whatever_the_weekday(self):
        from settings_core.management.commands.prepare_demo import Command

        tuesday = date(2026, 10, 20)  # + 3 days is a Friday, the day off
        for employee in Employee.objects.all():
            employee.attendance_days.all().delete()
        with mock.patch("django.utils.timezone.localdate", return_value=tuesday):
            Command()._attendance(self.owner)
        waiting = LeaveRequest.objects.get(status=LeaveStatus.PENDING)
        self.assertNotIn(waiting.start_date.weekday(), services.schedule_for(waiting.employee).off_weekdays())


class DeviceImportTests(AttendanceSetup):
    FILE = ("No.,Name,Time,State\n"
            "7,Ali,2026-09-16 08:58:10,C/In\n"
            "7,Ali,2026-09-16 13:02:00,C/Out\n"
            "7,Ali,2026-09-16 17:21:44,C/Out\n"
            "0007,Ali,2026-09-17 09:31:00,C/In\n"
            "99,Ghost,2026-09-16 09:00:00,C/In\n"
            "7,Ali,not a time,C/In\n")

    def test_first_punch_in_last_punch_out_and_twice_changes_nothing(self):
        result = services.import_punches(self.FILE.encode(), self.owner)
        self.assertEqual((result["days"], result["employees"], result["unmatched"], result["bad"]), (2, 1, ["99"], 1))
        first = AttendanceDay.objects.get(employee=self.ali, date=WEDNESDAY)
        self.assertEqual((first.check_in, first.check_out, first.source), (time(8, 58), time(17, 21), "device"))
        second = AttendanceDay.objects.get(employee=self.ali, date=WEDNESDAY + timedelta(days=1))
        self.assertEqual((second.check_in, second.check_out), (time(9, 31), None))  # one punch: in only
        services.import_punches(self.FILE.encode(), self.owner)
        self.assertEqual(AttendanceDay.objects.filter(employee=self.ali).count(), 2)
        first.refresh_from_db()
        self.assertEqual((first.check_in, first.check_out), (time(8, 58), time(17, 21)))

    def test_a_date_and_a_time_column_in_arabic_also_work(self):
        text = "رقم البصمة;التاريخ;الوقت\n7;16/09/2026;09:02\n7;16/09/2026;17:00\n"
        services.import_punches(text.encode("utf-8"), self.owner)
        record = AttendanceDay.objects.get(employee=self.ali, date=WEDNESDAY)
        self.assertEqual((record.check_in, record.check_out), (time(9, 2), time(17, 0)))
        with self.assertRaises(ValidationError):
            services.import_punches(b"name,amount\nx,1\n", self.owner)

    def test_the_upload_screen(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("attendance:import"), {"lang": "ar", "file": SimpleUploadedFile("punches.csv", self.FILE.encode())})
        self.assertContains(response, "data-import-result")
        self.assertContains(response, "data-unmatched")


class ScreensTests(AttendanceSetup):
    def test_the_screens_in_both_languages(self):
        self.day(self.ali, WEDNESDAY, time(9, 40), time(17, 0))
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            for name in ("attendance:today", "attendance:leaves", "attendance:import"):
                self.assertEqual(self.client.get(reverse(name) + f"?lang={lang}").status_code, 200, name)
            sheet = self.client.get(reverse("attendance:month") + f"?lang={lang}&month=2026-09")
            self.assertContains(sheet, f'data-sheet="{self.ali.pk}"')
            mine = self.client.get(reverse("attendance:month") + f"?lang={lang}&month=2026-09&employee={self.ali.pk}")
            self.assertContains(mine, 'data-day="2026-09-16"')
            self.assertContains(mine, "data-schedule-form")
        self.assertContains(self.client.get(reverse("staff:list") + "?lang=ar"), "data-attendance-link")

    def test_the_schedule_is_saved_from_the_sheet(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("attendance:month"), {"lang": "ar", "action": "schedule", "employee": self.mona.pk, "month": "2026-09",
                                                       "start_time": "10:00", "end_time": "18:00", "days_off": ["4", "5"], "grace_minutes": "10",
                                                       "annual_leave_days": "15", "device_id": "12"})
        schedule = services.schedule_for(Employee.objects.get(pk=self.mona.pk))
        self.assertEqual((schedule.start_time, schedule.off_weekdays(), schedule.device_id), (time(10, 0), {4, 5}, "12"))

    def test_switching_the_employees_module_off_closes_the_pages(self):
        from settings_core.models import ClientProfile
        from settings_core.setup_services import set_module_enabled

        self.client.force_login(self.owner)
        set_module_enabled(ClientProfile.objects.get(), "employees_technicians", False, self.owner)
        self.assertIn(self.client.get(reverse("attendance:today")).status_code, (403, 404))
