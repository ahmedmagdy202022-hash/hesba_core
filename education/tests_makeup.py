"""E8 (HG-105): makeup classes and seat booking: the rules in ``education.makeup``, the money line, the screens, the scan and the portal.

Hygiene: the clock is frozen at a fixed Wednesday noon and every group meets every day (Friday included), so nothing depends on the time of
day or the weekday; every page assertion reads its own element (a card, a row, a section), never "is not in" over a whole page that also holds
ids, tokens or hashes; markers are long Arabic phrases; lists are asserted in an explicit order.

The hard line of the item is pinned first (``MoneyLineTests``): a per-class course whose two groups are paid per class held and by a share of
the fees, a month billed and a teacher paid, and then seats booked, attended, missed and cancelled on days where the target group has no
class recorded yet. Every figure the billing, pay and insights modules compute, and every row count, is the same before and after, and a
negative control (one real mark in the target group) shows the same comparison does notice a change.
"""

import pathlib
import re
import threading
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import IntegrityError
from django.test import Client, SimpleTestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement
from entities.models import Entity
from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_location
from ledger.models import JournalEntry
from messaging.models import OutboxMessage
from permissions.models import RoleCode
from sales.models import SalesInvoice

from . import attendance, billing, insights, makeup, pay, scan, services
from .models import Attendance, ClassSession, Enrollment, FeeCharge, GroupPayTerm, MakeupSeat, SeatStatus, Student, StudyGroup, TeacherPayment
from .tests import EducationSetup, person
from .tests_portal import MARIAM, YOUSEF, PortalSetup, day, normalized

FIXED = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.get_current_timezone())  # a Wednesday, at noon
EVERY_DAY = "sun,mon,tue,wed,thu,fri,sat"
MONTH = FIXED.date().replace(day=1)
NOTE = "ملاحظة الحجز الداخلية للمركز"


def ahead(n):
    """The frozen day plus ``n`` days (negative: before it)."""

    return FIXED.date() + timedelta(days=n)


def at(day, hour, minute):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class MakeupSetup(EducationSetup):
    """A per-class course (50.00 a class) with two groups on a frozen Wednesday, both meeting every day:

    ``home`` (teacher A, 16:00, paid 10% of its fees) with Sara and Tala, and ``other`` (teacher B, 18:00, room for 3, paid 80.00 a class
    held) with Nour. So ``other`` has two free places, and a class held in ``other`` is a figure teacher B's pay reads."""

    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=FIXED)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        from staff.services import save_employee

        make_location(location_code="CENTER", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="CASH-EDU", is_default=True)
        make_cashbox_movement(self.cashbox, CashboxDirection.IN, "5000.00")
        self.teacher_b = save_employee({"name": "أ. هالة"}, self.owner)
        self.teacher_user = person(RoleCode.CASHIER, "mk_teacher_a")  # a cashier account linked to an employee: a teacher, nothing more
        self.teacher.user = self.teacher_user
        self.teacher.save()
        self.teacher_b_user = person(RoleCode.CASHIER, "mk_teacher_b")
        self.teacher_b.user = self.teacher_b_user
        self.teacher_b.save()
        self.stranger_teacher = save_employee({"name": "أ. سلمى"}, self.owner)
        self.stranger_user = person(RoleCode.CASHIER, "mk_teacher_c")
        self.stranger_teacher.user = self.stranger_user
        self.stranger_teacher.save()
        self.cashier = person(RoleCode.CASHIER, "mk_cashier")
        self.review = services.save_course({"name": "مراجعة بالحصة", "fee": "50", "basis": "session"}, self.owner)
        self.science = services.save_course({"name": "علوم بالحصة", "fee": "60", "basis": "session"}, self.owner)
        self.home = self.make_group("مجموعة المراجعة الأولى", self.teacher, time(16), "قاعة أ", "0", "percent", "10")
        self.other = self.make_group("مجموعة المراجعة الثانية", self.teacher_b, time(18), "قاعة ب", "3", "session", "80")
        self.sara, self.tala = self.enrolled("سارة", self.home), self.enrolled("تالا", self.home)
        self.nour = self.enrolled("نور", self.other)

    def make_group(self, name, teacher, start, room, capacity, pay_basis="none", rate="0", course=None, days=EVERY_DAY):
        return services.save_group({"course": course or self.review, "name": name, "teacher": teacher, "days": days, "start_time": start, "duration_minutes": "90",
                                    "capacity": capacity, "room": room, "teacher_pay": pay_basis, "teacher_rate": rate}, self.owner)

    def enrolled(self, name, group, start=None):
        student = services.save_student({"name": name, "payer_name": f"ولي أمر {name}", "payer_phone": f"0100{abs(hash(name)) % 10**7:07d}"}, self.owner)
        services.enroll(student, group, self.owner, start_date=start or ahead(-60))
        return student

    def book(self, student=None, group=None, day=None, home=None, user=None, **extra):
        return makeup.book(student or self.sara, home or self.home, group or self.other, day if day is not None else ahead(1), user or self.owner, **extra)

    def seats(self, **filters):
        return list(MakeupSeat.objects.filter(**filters).order_by("pk"))

    def classes(self):
        """Some regular classes: Sara and Tala at home (Sara absent on the 4th, the class she makes up), Nour at the other group."""

        for n, marks in ((2, {}), (3, {}), (4, {self.sara.pk: "absent"})):
            attendance.take(self.home, self.owner, MONTH + timedelta(days=n), {str(pk): v for pk, v in marks.items()})
            attendance.take(self.other, self.owner, MONTH + timedelta(days=n), {})

    def figures(self):
        due = [(l["enrollment"].student.name, l["enrollment"].group.name, l["quantity"], l["price"], l["discount"], l["amount"]) for l in billing.due_lines(MONTH)]
        preview = [(r["payer"].pk, [(l["enrollment"].pk, l["amount"]) for l in r["lines"]], r["net"], r["tax"], r["total"]) for r in billing.preview(MONTH)]
        teachers = [(r["teacher"].pk, r["due"], r["paid"], r["remaining"], [(g["group"].pk, g["units"], g["due"]) for g in r["groups"]]) for r in pay.teacher_rows(MONTH)]
        rows, totals = insights.group_report(MONTH)
        report = ([(r["group"].pk, r["students"], r["held"], r["rate"], r["billed"], r["teacher"], r["left"]) for r in rows], totals)
        people = {s.pk: (attendance.student_summary(s)["rate"], len(attendance.student_summary(s)["absences"])) for s in (self.sara, self.tala, self.nour)}
        rates = {g.pk: attendance.group_rates(g) for g in (self.home, self.other)}
        billed = [(row["invoice"].pk, row["students"]) for row in billing.billed(MONTH)]
        return {"due": due, "preview": preview, "teachers": teachers, "report": report, "people": people, "rates": rates, "billed": billed, "counts": self.counts(),
                "dues": [(row["party"].pk, row["total"]) for row in billing.dues(FIXED.date())]}

    def counts(self):
        return {model.__name__: model.objects.count() for model in (Attendance, ClassSession, FeeCharge, SalesInvoice, CashboxMovement, JournalEntry, TeacherPayment,
                                                                     GroupPayTerm, OutboxMessage, Enrollment, Student, StudyGroup)}


# ---- the rules ----

class RulesTests(MakeupSetup):
    def test_a_seat_is_booked_with_its_audit_and_writes_no_attendance(self):
        seat = self.book(day=ahead(2), missed_on=ahead(-1), note=NOTE)
        self.assertEqual((seat.student, seat.home_group, seat.group, seat.day, seat.missed_on, seat.status, seat.note, seat.created_by),
                         (self.sara, self.home, self.other, ahead(2), ahead(-1), "booked", NOTE, self.owner))
        log = AuditLog.objects.get(action="book_makeup_seat")
        self.assertEqual((log.object_type, log.object_id, log.before_data), ("education.MakeupSeat", str(seat.pk), {}))
        self.assertEqual((log.after_data["student"], log.after_data["home"], log.after_data["group"], log.after_data["day"], log.after_data["status"]),
                         (self.sara.code, self.home.code, self.other.code, ahead(2).isoformat(), "booked"))
        self.assertEqual((Attendance.objects.count(), ClassSession.objects.count()), (0, 0))  # a seat is not attendance, and opens no class

    def test_who_may_book(self):
        self.assertEqual(self.book(day=ahead(1), user=self.teacher_user).status, "booked")  # the home group's teacher
        self.assertEqual(self.book(self.tala, day=ahead(1), user=self.teacher_b_user).status, "booked")  # the target group's teacher
        for who in (self.cashier, self.stranger_user):
            with self.subTest(user=who.username), self.assertRaises(PermissionDenied):
                self.book(day=ahead(3), user=who)
        self.assertEqual(MakeupSeat.objects.count(), 2)

    def test_each_refusal_says_why_and_writes_nothing(self):
        science_group = self.make_group("مجموعة العلوم", self.teacher, time(10), "قاعة ج", "0", course=self.science)
        saturday_only = self.make_group("مجموعة السبت فقط", self.teacher_b, time(12), "قاعة د", "0", days="sat")
        closed = self.make_group("مجموعة مقفولة", self.teacher_b, time(14), "قاعة هـ", "0")
        StudyGroup.objects.filter(pk=closed.pk).update(active=False)
        branch = Entity.objects.create(code="E-MK", name_ar="فرع الحصص", activity_slug="education", sub_activity_slug="tutoring_center")
        elsewhere = self.make_group("مجموعة الفرع", self.teacher_b, time(8), "قاعة و", "0")
        StudyGroup.objects.filter(pk=elsewhere.pk).update(entity=branch)
        stopped = self.enrolled("ليلى", self.home)
        services.stop(Enrollment.objects.get(student=stopped, group=self.home), self.owner, end_date=ahead(-1))
        ClassSession.objects.create(group=self.other, date=ahead(4), status="cancelled", taken_by=self.owner)
        inactive = self.enrolled("عمر", self.home)
        Student.objects.filter(pk=inactive.pk).update(active=False)
        inactive.refresh_from_db()
        both = self.enrolled("منى", self.home)
        services.enroll(both, self.other, self.owner, start_date=ahead(-60))
        cases = (
            ("same group", dict(group=self.home), "مجموعة تانية"),
            ("another course", dict(group=science_group), "نفس الكورس"),
            ("another entity", dict(group=elsewhere), "نفس الكيان"),
            ("not enrolled in home", dict(student=self.nour, home=self.home), "مش مسجّل"),
            ("enrolment stopped", dict(student=stopped), "مش مسجّل"),
            ("inactive student", dict(student=inactive), "موقوف"),
            ("already in the target", dict(student=both), "مسجّل في المجموعة دي"),
            ("closed group", dict(group=closed), "مقفولة"),
            ("a day in the past", dict(day=ahead(-1)), "يوم عدّى"),
            ("not a day the group meets", dict(group=saturday_only, day=ahead(0)), "مالهاش حصة"),
            ("a cancelled class", dict(day=ahead(4)), "اتلغت"),
            ("a missed day the home group does not have", dict(missed_on=ahead(2), home=self.make_group("مجموعة أيام محدودة", self.stranger_teacher, time(7), "قاعة ز", "0", days="sat")), "أيام المجموعة الأصلية"),
            ("a bad date", dict(day="2026-13-45"), "التاريخ"),
            ("a long note", dict(note="ن" * 256), "255"),
        )
        for label, extra, message in cases:
            with self.subTest(label), self.assertRaisesMessage(ValidationError, message):
                if label.startswith("a missed day"):  # the home group of this case must hold the student
                    other_home = extra["home"]
                    services.enroll(self.sara, other_home, self.owner, start_date=ahead(-60))
                self.book(**extra)
        self.assertEqual(MakeupSeat.objects.count(), 0)
        self.assertEqual(AuditLog.objects.filter(action="book_makeup_seat").count(), 0)

    def test_the_english_refusals_are_in_english(self):
        with self.assertRaises(ValidationError) as caught:
            self.book(group=self.home, lang="en")
        self.assertEqual(caught.exception.messages, [makeup.MESSAGES["en"]["same_group"]])
        self.assertTrue(set(makeup.MESSAGES["ar"]) == set(makeup.MESSAGES["en"]))

    def test_today_can_be_booked_and_the_window_is_fourteen_days_from_today(self):
        self.assertEqual(self.book(day=ahead(0)).day, ahead(0))
        days = sorted({row["day"] for row in makeup.classes_for(self.tala, self.home, self.owner)})
        self.assertEqual((days[0], days[-1], len(days)), (ahead(0), ahead(13), 14))  # today and the 13 days after it; a 15th day is not offered

    def test_one_student_cannot_hold_two_live_seats_in_one_class_but_may_after_cancelling(self):
        first = self.book(day=ahead(1))
        with self.assertRaisesMessage(ValidationError, "حاجز مكان"):
            self.book(day=ahead(1))
        self.assertEqual(self.book(day=ahead(2)).day, ahead(2))  # another day is another class
        makeup.cancel(first, self.owner)
        self.assertEqual(self.book(day=ahead(1)).status, "booked")  # the cancelled seat no longer counts
        self.assertEqual(MakeupSeat.objects.filter(student=self.sara, group=self.other, day=ahead(1)).count(), 2)

    def test_the_database_itself_refuses_a_second_live_seat_and_allows_a_second_cancelled_one(self):
        self.book(day=ahead(1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            MakeupSeat.objects.create(student=self.sara, home_group=self.home, group=self.other, day=ahead(1), created_by=self.owner)
        for status in ("cancelled", "cancelled"):
            MakeupSeat.objects.create(student=self.tala, home_group=self.home, group=self.other, day=ahead(1), status=status, created_by=self.owner)
        for status in ("attended", "missed"):  # a marked seat still holds its place
            with self.assertRaises(IntegrityError), transaction.atomic():
                MakeupSeat.objects.create(student=self.sara, home_group=self.home, group=self.other, day=ahead(1), status=status, created_by=self.owner)


class CapacityTests(MakeupSetup):
    def test_capacity_counts_the_enrolled_and_the_live_seats_and_a_cancel_frees_a_place(self):
        self.assertEqual(makeup.free_seats(self.other, ahead(1)), 2)  # capacity 3, Nour is enrolled
        first = self.book(self.sara, day=ahead(1))
        self.assertEqual(makeup.free_seats(self.other, ahead(1)), 1)
        self.assertEqual(makeup.free_seats(self.other, ahead(2)), 2)  # seats are per class day
        self.book(self.tala, day=ahead(1))
        self.assertEqual(makeup.free_seats(self.other, ahead(1)), 0)
        third = self.enrolled("هدى", self.home)
        with self.assertRaisesMessage(ValidationError, "مكتملة"):
            self.book(third, day=ahead(1))
        makeup.cancel(first, self.owner)
        self.assertEqual(makeup.free_seats(self.other, ahead(1)), 1)
        self.assertEqual(self.book(third, day=ahead(1)).status, "booked")
        self.assertEqual(MakeupSeat.objects.filter(group=self.other, day=ahead(1)).exclude(status="cancelled").count(), 2)

    def test_a_marked_seat_still_holds_its_place_for_that_day(self):
        seat = self.book(self.sara, day=ahead(0))
        self.book(self.tala, day=ahead(0))
        makeup.mark(seat, "missed", self.owner)
        self.assertEqual(makeup.free_seats(self.other, ahead(0)), 0)

    def test_a_group_with_no_capacity_is_unlimited(self):
        unlimited = self.make_group("مجموعة مفتوحة", self.teacher_b, time(20), "قاعة ح", "0")
        self.assertIsNone(makeup.free_seats(unlimited, ahead(1)))
        for name in ("أ", "ب", "ج", "د", "هـ"):
            self.book(self.enrolled(f"طالب {name}", self.home), group=unlimited, day=ahead(1))
        self.assertEqual(MakeupSeat.objects.filter(group=unlimited).count(), 5)

    def test_the_form_and_the_booking_use_the_same_count(self):
        self.book(self.sara, day=ahead(1))
        self.book(self.tala, day=ahead(1))
        rows = {(row["day"], row["group"].pk): row for row in makeup.classes_for(self.enrolled("هدى", self.home), self.home, self.owner)}
        row = rows[(ahead(1), self.other.pk)]
        self.assertEqual((row["free"], row["bookable"]), (0, False))
        self.assertEqual((rows[(ahead(2), self.other.pk)]["free"], rows[(ahead(2), self.other.pk)]["bookable"]), (2, True))

    def test_the_candidates_are_other_running_groups_of_the_same_course_not_cancelled_and_not_the_students_own(self):
        self.make_group("مجموعة علوم", self.teacher, time(10), "قاعة ج", "0", course=self.science)
        closed = self.make_group("مجموعة مقفولة", self.teacher_b, time(14), "قاعة هـ", "0")
        StudyGroup.objects.filter(pk=closed.pk).update(active=False)
        ClassSession.objects.create(group=self.other, date=ahead(2), status="cancelled", taken_by=self.owner)
        rows = makeup.classes_for(self.sara, self.home, self.owner)
        self.assertEqual({row["group"].pk for row in rows}, {self.other.pk})
        self.assertNotIn(ahead(2), [row["day"] for row in rows])
        self.assertEqual(rows[0]["time"], "18:00")
        self.assertEqual(makeup.classes_for(self.nour, self.other, self.owner)[0]["group"], self.home)
        self.assertEqual(makeup.classes_for(self.sara, self.home, self.stranger_user), [])  # a teacher of neither group is offered nothing
        self.assertEqual({row["group"].pk for row in makeup.classes_for(self.sara, self.home, self.teacher_b_user)}, {self.other.pk})  # the target's teacher sees theirs


class StatusTests(MakeupSetup):
    def test_marking_is_for_the_target_groups_teacher_and_a_manager(self):
        seat = self.book(day=ahead(0))
        with self.assertRaises(PermissionDenied):
            makeup.mark(seat, "attended", self.teacher_user)  # the home teacher does not run that class
        with self.assertRaises(PermissionDenied):
            makeup.mark(seat, "attended", self.cashier)
        self.assertEqual(makeup.mark(seat, "attended", self.teacher_b_user).status, "attended")
        self.assertEqual(makeup.mark(seat, "missed", self.owner).status, "missed")  # corrected by a manager
        self.assertEqual(makeup.mark(seat, "attended", self.teacher_b_user).status, "attended")
        self.assertEqual([log.action for log in AuditLog.objects.filter(module="education", object_type="education.MakeupSeat").order_by("pk")],
                         ["book_makeup_seat", "attend_makeup_seat", "miss_makeup_seat", "attend_makeup_seat"])
        last = AuditLog.objects.filter(action="attend_makeup_seat").latest("pk")
        self.assertEqual((last.before_data["status"], last.after_data["status"], last.actor), ("missed", "attended", self.teacher_b_user))

    def test_marking_the_same_status_again_changes_and_writes_nothing(self):
        seat = self.book(day=ahead(0))
        makeup.mark(seat, "attended", self.owner)
        logs = AuditLog.objects.count()
        makeup.mark(seat, "attended", self.owner)
        self.assertEqual(AuditLog.objects.count(), logs)

    def test_a_class_still_to_come_cannot_be_marked_and_a_cancelled_seat_or_class_cannot_either(self):
        future = self.book(day=ahead(1))
        with self.assertRaisesMessage(ValidationError, "لسه مجاتش"):
            makeup.mark(future, "attended", self.owner)
        today = self.book(self.tala, day=ahead(0))
        makeup.cancel(today, self.owner)
        with self.assertRaisesMessage(ValidationError, "اتلغى"):
            makeup.mark(today, "attended", self.owner)
        live = self.book(self.enrolled("هدى", self.home), day=ahead(0))
        ClassSession.objects.create(group=self.other, date=ahead(0), status="cancelled", taken_by=self.owner)
        with self.assertRaisesMessage(ValidationError, "اتلغت"):
            makeup.mark(live, "attended", self.owner)
        with self.assertRaises(ValidationError):
            makeup.mark(live, "cancelled", self.owner)  # only attended and missed can be marked
        self.assertEqual(MakeupSeat.objects.get(pk=live.pk).status, "booked")

    def test_cancelling_frees_the_seat_is_idempotent_and_only_a_booked_seat_can_be_cancelled(self):
        seat = self.book(day=ahead(0))
        self.assertEqual(makeup.cancel(seat, self.teacher_user).status, "cancelled")  # the home teacher may cancel
        logs = AuditLog.objects.filter(action="cancel_makeup_seat").count()
        makeup.cancel(seat, self.owner)
        self.assertEqual(AuditLog.objects.filter(action="cancel_makeup_seat").count(), logs)  # twice is not an error and not a second row
        seat = self.book(day=ahead(0))
        makeup.mark(seat, "attended", self.owner)
        with self.assertRaisesMessage(ValidationError, "مينفعش يتلغى"):
            makeup.cancel(seat, self.owner)
        again = self.book(self.tala, day=ahead(0))
        for who in (self.cashier, self.stranger_user):
            with self.assertRaises(PermissionDenied):
                makeup.cancel(again, who)
        self.assertEqual(MakeupSeat.objects.get(pk=again.pk).status, "booked")
        self.assertTrue(MakeupSeat.objects.filter(pk=seat.pk).exists())  # nothing is ever deleted

    def test_a_seat_never_changes_by_being_looked_at(self):
        seat = self.book(day=ahead(0))
        later = at(ahead(5), 12, 0)
        with mock.patch("django.utils.timezone.now", return_value=later):
            makeup.for_children([self.sara], services.scoped(StudyGroup.objects), ahead(5))
            makeup.guests_of([self.other], ahead(0))
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "booked")


# ---- the money line ----

class MoneyLineTests(MakeupSetup):
    """A seat is not attendance: nothing billing, pay or insights compute, and no row count, moves when seats are booked, marked or cancelled."""

    def makeup_operations(self):
        """Book, attend, miss, correct and cancel, on days where ``other`` has no class recorded at all (so a wrongly created class would show)."""

        today_seat = makeup.book(self.sara, self.home, self.other, ahead(0), self.owner, missed_on=ahead(-7))
        later = makeup.book(self.tala, self.home, self.other, ahead(1), self.owner)
        makeup.mark(today_seat, "attended", self.teacher_b_user)
        makeup.mark(today_seat, "missed", self.owner)
        makeup.mark(today_seat, "attended", self.owner)
        makeup.cancel(later, self.owner)
        makeup.book(self.tala, self.home, self.other, ahead(1), self.owner)
        marked = makeup.book(self.tala, self.home, self.other, ahead(0), self.owner)
        makeup.mark(marked, "missed", self.owner)

    def test_billing_pay_and_insights_are_identical_around_a_whole_run_of_seats(self):
        hoda = self.enrolled("هدى", self.home)  # enrolled before the first figures are taken: she has no class, so she is billed nothing
        self.classes()
        before = self.figures()
        self.assertEqual([row[:2] for row in before["due"]], [("تالا", "مجموعة المراجعة الأولى"), ("سارة", "مجموعة المراجعة الأولى"), ("نور", "مجموعة المراجعة الثانية"), ("هدى", "مجموعة المراجعة الأولى")])
        self.assertEqual([(row[2], row[5]) for row in before["due"]], [(D("3"), D("150.00")), (D("2"), D("100.00")), (D("3"), D("150.00")), (D("3"), D("150.00"))])  # Sara was absent on one of the three
        self.assertEqual(before["teachers"][1][4], [(self.other.pk, D("3"), D("240.00"))])  # teacher B: 3 classes held at 80.00 each
        self.makeup_operations()
        self.assertEqual(MakeupSeat.objects.count(), 4)
        self.assertEqual(self.figures(), before)
        invoices = billing.bill_month(MONTH, self.owner, post=True)
        self.assertEqual(sum((invoice.total_amount for invoice in invoices), D("0")), D("550.00"))
        billed = self.figures()
        self.assertEqual((billed["due"], billed["counts"]["FeeCharge"]), ([], 4))
        self.assertEqual(billed["teachers"][0][4], [(self.home.pk, D("400.00"), D("40.00"))])  # teacher A: 10% of the 400.00 billed at home (Sara 100.00, Tala 150.00, Huda 150.00)
        makeup.book(hoda, self.home, self.other, ahead(2), self.owner)
        extra = makeup.book(self.nour, self.other, self.home, ahead(0), self.owner)
        makeup.mark(extra, "attended", self.owner)
        makeup.cancel(self.seats(group=self.other, day=ahead(1), status="booked")[0], self.owner)
        self.assertEqual(self.figures(), billed)
        for teacher, amount in ((self.teacher, "40"), (self.teacher_b, "240")):
            pay.pay(teacher, MONTH, amount, self.cashbox, self.owner)
        paid = self.figures()
        self.assertEqual([row[3] for row in paid["teachers"]], [D("0"), D("0")])
        makeup.book(self.tala, self.home, self.other, ahead(3), self.owner)
        makeup.mark(self.seats(student=self.tala, group=self.other, day=ahead(0))[0], "attended", self.owner)
        self.assertEqual(self.figures(), paid)

    def test_a_makeup_seat_creates_no_class_and_no_mark_in_the_target_group(self):
        seat = self.book(day=ahead(0))
        makeup.mark(seat, "attended", self.owner)
        self.assertFalse(ClassSession.objects.filter(group=self.other, date=ahead(0)).exists())
        self.assertFalse(Attendance.objects.filter(student=self.sara).exists())
        self.assertEqual(pay.group_due(self.other, MONTH)["due"], D("0.00"))  # a class "held" for pay would be 80.00

    def test_the_comparison_notices_a_real_change_in_the_target_group(self):
        self.classes()
        before = self.figures()
        attendance.take(self.other, self.owner, MONTH + timedelta(days=5), {})  # one real class held: the figures must differ
        self.assertNotEqual(self.figures(), before)
        self.assertEqual(self.figures()["teachers"][1][4], [(self.other.pk, D("4"), D("320.00"))])

    def test_the_money_modules_never_read_the_makeup_table_and_the_service_writes_no_money(self):
        for name in ("billing", "pay", "insights"):
            source = pathlib.Path(settings.BASE_DIR, "education", f"{name}.py").read_text(encoding="utf-8")
            self.assertNotIn("MakeupSeat", source, name)
            self.assertNotIn("makeup", source, name)
        source = pathlib.Path(settings.BASE_DIR, "education", "makeup.py").read_text(encoding="utf-8")
        code = re.sub(r"#.*", "", re.sub(r'""".*?"""', "", source, flags=re.S))  # the docstrings say why; the code never names these
        imported = " ".join(re.findall(r"^from \.(?:models)? ?import (.+)$", code, flags=re.M))
        for forbidden in ("Attendance", "FeeCharge", "TeacherPayment", "GroupPayTerm", "billing", "pay", "insights"):
            self.assertNotRegex(imported, rf"\b{forbidden}\b", forbidden)  # what it imports from the models and the package
        for forbidden in (r"\bAttendance[.(]", r"\bFeeCharge[.(]", r"\bTeacherPayment[.(]", r"record_expense", r"post_sales_invoice", r"\bCashbox", r"\bSalesInvoice[.(]"):
            self.assertNotRegex(code, forbidden)
        self.assertEqual(len(re.findall(r"ClassSession\.objects\.\w+", source)), len(re.findall(r"ClassSession\.objects\.filter", source)))  # read only

    def test_no_money_stock_or_message_row_is_written_by_any_of_it(self):
        counts = self.counts()
        outbox = OutboxMessage.objects.count()
        others = AuditLog.objects.exclude(module="education").count()
        self.makeup_operations()
        after = self.counts()
        self.assertEqual(after, counts)
        self.assertEqual(OutboxMessage.objects.count(), outbox)
        self.assertEqual(AuditLog.objects.exclude(module="education").count(), others)



# ---- the screens ----

class ScreenSetup(MakeupSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def as_(self, user):
        client = Client()
        client.force_login(user)
        return client

    def get(self, name, *args, lang="ar", client=None, query=""):
        return (client or self.client).get(reverse(f"education:{name}", args=args) + f"?lang={lang}{query}")

    def post(self, name, *args, client=None, **data):
        return (client or self.client).post(reverse(f"education:{name}", args=args), {"lang": "ar", **data})

    def section(self, response, marker):
        """The one element a test reads: from ``marker`` to the end of its ``<section>``."""

        html = response.content.decode()
        found = re.search(rf"<section[^>]*{marker}.*?</section>", html, re.S)
        self.assertIsNotNone(found, marker)
        return found.group(0)

    def row(self, html, attr, pk):
        found = re.search(rf'<li[^>]*{attr}="{pk}".*?</li>', html, re.S)
        self.assertIsNotNone(found, f"{attr} {pk}")
        return found.group(0)

    def flashes(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class StudentCardTests(ScreenSetup):
    def test_the_card_lists_the_seats_and_a_booked_one_can_be_cancelled(self):
        booked = self.book(day=ahead(2), missed_on=ahead(-1), note="ملاحظة الحجز الأولى")
        attended = self.book(day=ahead(0))
        makeup.mark(attended, "attended", self.owner)
        gone = self.book(day=ahead(3))
        makeup.cancel(gone, self.owner)
        page = self.get("student", self.sara.pk)
        card = self.section(page, "data-makeup-card")
        self.assertIn("حصص تعويضية", card)
        self.assertEqual(re.findall(r'data-makeup="(\d+)"', card), [str(gone.pk), str(booked.pk), str(attended.pk)])  # newest class first
        first = self.row(card, "data-makeup", booked.pk)
        self.assertIn("محجوز", first)
        self.assertIn("مجموعة المراجعة الثانية", first)
        self.assertIn("مجموعة المراجعة الأولى", first)
        self.assertIn("ملاحظة الحجز الأولى", first)
        self.assertIn(reverse("education:makeup_cancel", args=[booked.pk]), first)
        self.assertIn("حضور", self.row(card, "data-makeup", attended.pk))  # a noun, so it reads the same beside a boy's and a girl's name
        self.assertNotIn("data-makeup-cancel", self.row(card, "data-makeup", attended.pk))  # a marked seat cannot be cancelled
        self.assertNotIn("data-makeup-cancel", self.row(card, "data-makeup", gone.pk))
        self.assertIn("اتلغى", self.row(card, "data-makeup", gone.pk))
        self.assertIn(reverse("education:makeup_new", args=[self.sara.pk]), card)

    def test_the_date_and_the_time_of_a_seat_are_joined_by_a_comma_not_a_dot(self):
        seat = self.book(day=ahead(2))
        row = self.row(self.section(self.get("student", self.sara.pk), "data-makeup-card"), "data-makeup", seat.pk)
        self.assertRegex(row, r"</bdi>، <bdi dir=\"ltr\">18:00</bdi>")
        self.assertNotIn(" · ", row)
        english = self.row(self.section(self.get("student", self.sara.pk, lang="en"), "data-makeup-card"), "data-makeup", seat.pk)
        self.assertRegex(english, r"</bdi>, <bdi dir=\"ltr\">18:00</bdi>")
        self.assertIn("Booked", english)

    def test_the_note_is_escaped(self):
        seat = self.book(day=ahead(2), note="<script>alert(1)</script>")
        row = self.row(self.section(self.get("student", self.sara.pk), "data-makeup-card"), "data-makeup", seat.pk)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", row)
        self.assertNotIn("<script>", row)

    def test_a_student_with_no_seats_shows_the_card_with_the_book_link_and_a_stopped_student_has_no_book_link(self):
        card = self.section(self.get("student", self.tala.pk), "data-makeup-card")
        self.assertIn("data-makeup-none", card)
        self.assertIn("data-makeup-book", card)
        Student.objects.filter(pk=self.tala.pk).update(active=False)
        stopped = self.get("student", self.tala.pk)
        self.assertNotContains(stopped, "data-makeup-card")  # nothing to show and nobody to book for


class BookingPageTests(ScreenSetup):
    def options(self, response):
        return re.findall(r'<label class="mk-option[^"]*" data-class-option data-free="([^"]*)">(.*?)</label>', response.content.decode(), re.S)

    def test_the_page_lists_the_other_groups_classes_with_their_free_seats(self):
        self.book(self.tala, day=ahead(1))
        self.book(self.enrolled("هدى", self.home), day=ahead(1))
        page = self.get("makeup_new", self.sara.pk, query=f"&home={self.home.pk}")
        self.assertEqual(page.status_code, 200)
        options = self.options(page)
        self.assertEqual(len(options), 14)  # one class of the other group on each of the next 14 days
        today, tomorrow, after = options[0], options[1], options[2]
        self.assertEqual((today[0], tomorrow[0], after[0]), ("2", "0", "2"))
        self.assertIn("فاضل مقعدين", today[1])
        self.assertIn("مكتمل", tomorrow[1])
        self.assertIn("disabled", tomorrow[1])  # a full class cannot be chosen
        self.assertNotIn("disabled", today[1])
        self.assertIn("18:00", today[1])
        self.assertIn("مجموعة المراجعة الثانية", today[1])
        self.assertRegex(today[1], r"</bdi>، <bdi dir=\"ltr\">18:00</bdi>")
        self.assertNotIn("مجموعة المراجعة الأولى", "".join(text for _, text in options))  # the student's own group is not offered

    def test_one_seat_left_and_a_class_the_student_already_holds(self):
        self.book(self.tala, day=ahead(0))
        held = self.book(self.sara, day=ahead(1))
        options = self.options(self.get("makeup_new", self.sara.pk, query=f"&home={self.home.pk}"))
        self.assertIn("فاضل مقعد واحد", options[0][1])
        self.assertIn("محجوزة بالفعل", options[1][1])
        self.assertIn("disabled", options[1][1])
        self.assertEqual(held.status, "booked")

    def test_an_unlimited_group_says_open_and_the_english_page_reads_in_english(self):
        StudyGroup.objects.filter(pk=self.other.pk).update(capacity=0)
        page = self.get("makeup_new", self.sara.pk, lang="en", query=f"&home={self.home.pk}")
        options = self.options(page)
        self.assertEqual(options[0][0], "open")
        self.assertIn("Open", options[0][1])
        self.assertIn("Book a makeup class", page.content.decode())
        StudyGroup.objects.filter(pk=self.other.pk).update(capacity=3)
        self.book(self.tala, day=ahead(0))
        english = self.options(self.get("makeup_new", self.sara.pk, lang="en", query=f"&home={self.home.pk}"))
        self.assertIn("1 seat left", english[0][1])

    def test_the_only_home_group_is_chosen_for_you_and_with_two_you_choose(self):
        self.assertTrue(self.options(self.get("makeup_new", self.sara.pk)))  # one running group: no choice to make
        services.enroll(self.sara, self.make_group("مجموعة المراجعة الثالثة", self.stranger_teacher, time(9), "قاعة ج", "0"), self.owner, start_date=ahead(-60))
        page = self.get("makeup_new", self.sara.pk)
        self.assertEqual(self.options(page), [])
        self.assertContains(page, "data-choose-home")

    def test_a_student_in_no_running_group_has_nothing_to_make_up(self):
        loner = services.save_student({"name": "وحيد", "payer_name": "ولي أمر وحيد", "payer_phone": "01009990000"}, self.owner)
        self.assertContains(self.get("makeup_new", loner.pk), "data-no-home")

    def test_booking_by_the_form_makes_the_seat_and_goes_back_to_the_student(self):
        response = self.post("makeup_book", self.sara.pk, home=self.home.pk, missed=ahead(-1).isoformat(), slot=f"{self.other.pk}|{ahead(2).isoformat()}", note=NOTE)
        self.assertRedirects(response, f"{reverse('education:student', args=[self.sara.pk])}?lang=ar#makeup", fetch_redirect_response=False)
        (seat,) = self.seats()
        self.assertEqual((seat.student, seat.home_group, seat.group, seat.day, seat.missed_on, seat.note), (self.sara, self.home, self.other, ahead(2), ahead(-1), NOTE))
        self.assertEqual(self.flashes(response), ["المقعد اتحجز في الحصة التعويضية."])

    def test_a_refused_booking_goes_back_to_the_page_with_the_reason_and_writes_nothing(self):
        self.book(self.tala, day=ahead(1))
        self.book(self.enrolled("هدى", self.home), day=ahead(1))
        response = self.post("makeup_book", self.sara.pk, home=self.home.pk, slot=f"{self.other.pk}|{ahead(1).isoformat()}")
        self.assertRedirects(response, f"{reverse('education:makeup_new', args=[self.sara.pk])}?lang=ar&home={self.home.pk}&missed=", fetch_redirect_response=False)
        self.assertEqual(self.flashes(response), ["الحصة دي مكتملة (3 مكان)."])
        self.assertEqual(MakeupSeat.objects.filter(student=self.sara).count(), 0)
        for slot in ("", "x|2026-11-12", f"{self.other.pk}|not-a-date"):
            refused = self.post("makeup_book", self.sara.pk, home=self.home.pk, slot=slot)
            self.assertEqual(refused.status_code, 302, slot)
        self.assertEqual(MakeupSeat.objects.filter(student=self.sara).count(), 0)

    def test_a_missed_day_with_odd_characters_cannot_add_parameters_to_the_way_back(self):
        response = self.post("makeup_book", self.sara.pk, home=self.home.pk, missed="2026-11-01&home=9999", slot="")
        self.assertNotIn("&home=9999", response["Location"])


class RegisterGuestTests(ScreenSetup):
    def sheet(self, day=None, group=None, lang="ar", client=None):
        return self.get("attendance", (group or self.other).pk, lang=lang, client=client, query=f"&date={(day or ahead(0)).isoformat()}")

    def test_the_guest_section_lists_the_booked_guests_with_their_two_buttons(self):
        seat = self.book(day=ahead(0), note=NOTE)
        page = self.sheet()
        guests = self.section(page, "data-makeup-guests")
        self.assertIn("ضيوف الحصة (تعويضي)", guests)
        row = self.row(guests, "data-guest", seat.pk)
        self.assertIn("سارة", row)
        self.assertIn("مجموعة المراجعة الأولى", row)
        self.assertIn(NOTE, row)
        self.assertIn(reverse("education:makeup_attended", args=[seat.pk]), row)
        self.assertIn(reverse("education:makeup_missed", args=[seat.pk]), row)
        self.assertRegex(row, r">حضر</button>")
        self.assertRegex(row, r">ماحضرش</button>")

    def test_the_guest_is_not_a_roster_row_and_the_section_sits_outside_the_registers_form(self):
        self.book(day=ahead(0))
        html = self.sheet().content.decode()
        self.assertEqual(re.findall(r'data-mark="(\d+)"', html), [str(self.nour.pk)])  # the roster: the group's own student only
        form = re.search(r"<form[^>]*data-attendance-form.*?</form>", html, re.S).group(0)
        self.assertNotIn("data-makeup-guests", form)
        self.assertEqual(sorted(set(re.findall(r'name="p_(\d+)"', form))), [str(self.nour.pk)])  # the register's own fields: the group's student, never the guest
        self.assertEqual(html.count("<form"), html.count("</form>"))
        self.assertGreater(html.index("data-makeup-guests"), html.index("</form>", html.index("data-attendance-form")))  # after the register's form, not nested in it

    def test_a_group_with_no_guests_has_no_section_at_all(self):
        self.assertNotContains(self.sheet(), "data-makeup-guests")
        self.assertNotContains(self.sheet(group=self.home), "data-makeup-guests")  # and a seat is on the target's sheet only
        self.book(day=ahead(0))
        self.assertNotContains(self.sheet(group=self.home), "data-makeup-guests")

    def test_the_registers_digest_and_save_do_not_change_with_a_booking(self):
        attendance.take(self.other, self.owner, ahead(-1), {})
        before = re.search(r'name="sheet_version" value="([^"]*)"', self.sheet(ahead(-1)).content.decode()).group(1)
        today_before = re.search(r'name="sheet_version" value="([^"]*)"', self.sheet().content.decode()).group(1)
        seat = self.book(day=ahead(0))
        makeup.mark(seat, "attended", self.owner)
        self.assertEqual(re.search(r'name="sheet_version" value="([^"]*)"', self.sheet(ahead(-1)).content.decode()).group(1), before)
        self.assertEqual(re.search(r'name="sheet_version" value="([^"]*)"', self.sheet().content.decode()).group(1), today_before)
        saved = self.post("attendance", self.other.pk, date=ahead(0).isoformat(), sheet_version=today_before, **{f"p_{self.nour.pk}": "present"})
        self.assertEqual(saved.status_code, 302)
        session = ClassSession.objects.get(group=self.other, date=ahead(0))
        self.assertEqual(list(session.marks.values_list("student__name", "presence")), [("نور", "present")])  # the register saved its own roster only
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "attended")

    def test_the_two_buttons_mark_the_guest_and_come_back_to_the_sheet(self):
        seat = self.book(day=ahead(0))
        response = self.post("makeup_attended", seat.pk, back="attendance")
        self.assertRedirects(response, f"{reverse('education:attendance', args=[self.other.pk])}?lang=ar&date={ahead(0).isoformat()}", fetch_redirect_response=False)
        self.assertEqual(self.flashes(response), ["اتسجّل الحضور."])
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "attended")
        self.assertIn("data-guest-status=\"attended\"", self.sheet().content.decode())
        self.post("makeup_missed", seat.pk, back="attendance")
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "missed")
        self.assertEqual((Attendance.objects.count(), ClassSession.objects.count()), (0, 0))

    def test_the_target_teacher_marks_from_the_sheet_and_the_home_teacher_cannot(self):
        seat = self.book(day=ahead(0))
        self.assertEqual(self.sheet(client=self.as_(self.teacher_b_user)).status_code, 200)
        self.assertEqual(self.post("makeup_attended", seat.pk, back="attendance", client=self.as_(self.teacher_b_user)).status_code, 302)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "attended")
        for who in (self.teacher_user, self.cashier, self.stranger_user):
            with self.subTest(user=who.username):
                self.assertEqual(self.post("makeup_missed", seat.pk, client=self.as_(who)).status_code, 403)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "attended")

    def test_a_class_cancelled_on_the_register_shows_the_guest_without_buttons(self):
        seat = self.book(day=ahead(0))
        attendance.take(self.other, self.owner, ahead(0), {}, cancelled=True)
        guests = self.section(self.sheet(), "data-makeup-guests")
        row = self.row(guests, "data-guest", seat.pk)
        self.assertIn("data-guest-cancelled-class", row)
        self.assertNotIn("data-guest-attended", row)
        self.assertEqual(self.post("makeup_attended", seat.pk, back="attendance").status_code, 302)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "booked")  # refused with a message; the seat stays as it was

    def test_today_lists_each_classs_guests_for_who_may_mark_them(self):
        seat = self.book(day=ahead(0))
        home_seat = self.book(self.nour, group=self.home, home=self.other, day=ahead(0))
        page = self.get("today", query=f"&date={ahead(0).isoformat()}")
        html = page.content.decode()
        other_card = re.search(rf'<article[^>]*data-class="{self.other.pk}".*?</article>', html, re.S).group(0)
        home_card = re.search(rf'<article[^>]*data-class="{self.home.pk}".*?</article>', html, re.S).group(0)
        self.assertEqual(re.findall(r'data-guest="(\d+)"', other_card), [str(seat.pk)])
        self.assertEqual(re.findall(r'data-guest="(\d+)"', home_card), [str(home_seat.pk)])
        self.assertIn("ضيوف الحصة (تعويضي)", other_card)
        teacher = self.get("today", client=self.as_(self.teacher_b_user), query=f"&date={ahead(0).isoformat()}").content.decode()
        self.assertEqual(re.findall(r'data-guest="(\d+)"', teacher), [str(seat.pk)])  # teacher B sees the guest of their class only
        marked = self.post("makeup_attended", seat.pk, back="today")
        self.assertRedirects(marked, f"{reverse('education:today')}?lang=ar&date={ahead(0).isoformat()}", fetch_redirect_response=False)

    def test_the_english_sheet_reads_in_english_and_the_note_is_escaped(self):
        self.book(day=ahead(0), note="<b>x</b>")
        guests = self.section(self.sheet(lang="en"), "data-makeup-guests")
        self.assertIn("Class guests (makeup)", guests)
        self.assertIn(">Attended</button>", guests)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", guests)
        self.assertNotIn("<b>x</b>", guests)


class GuardTests(ScreenSetup):
    """The exam and content screens' order: not an education install (404), the record in the entity being worked in (404), then the permission (403)."""

    def urls(self, seat):
        return [("get", reverse("education:makeup_new", args=[self.sara.pk])), ("post", reverse("education:makeup_book", args=[self.sara.pk])),
                ("post", reverse("education:makeup_cancel", args=[seat.pk])), ("post", reverse("education:makeup_attended", args=[seat.pk])),
                ("post", reverse("education:makeup_missed", args=[seat.pk]))]

    def call(self, client, method, url):
        form = {"lang": "ar", "home": self.home.pk, "slot": f"{self.other.pk}|{ahead(5).isoformat()}"}
        return client.get(url) if method == "get" else client.post(url, form)

    def test_not_an_education_install_is_a_404_before_anything_else(self):
        from settings_core.models import ClientProfile

        seat = self.book(day=ahead(0))
        profile = ClientProfile.get_active()
        profile.activity_slug = "commercial"
        profile.save()
        for user in (self.owner, self.cashier):  # the cashier would be a 403 on an education install
            client = self.as_(user)
            for method, url in self.urls(seat):
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.call(client, method, url).status_code, 404)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "booked")

    def test_a_record_of_another_entity_is_a_404_even_for_who_would_get_a_403(self):
        from entities.services import main_entity

        seat = self.book(day=ahead(0))
        branch = Entity.objects.create(code="E-MKG", name_ar="فرع الحجز", activity_slug="education", sub_activity_slug="tutoring_center")
        for user in (self.owner, self.cashier):
            client = self.as_(user)
            session = client.session
            session["hesba_entity"] = branch.pk
            session.save()
            for method, url in self.urls(seat):  # from the branch the main entity's student, group and seat do not exist
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.call(client, method, url).status_code, 404)
        foreign_student = services.save_student({"name": "طالب الفرع", "payer_name": "ولي أمر الفرع", "payer_phone": "01005551111"}, self.owner)
        Student.objects.filter(pk=foreign_student.pk).update(entity=branch)
        client = self.as_(self.owner)
        session = client.session
        session["hesba_entity"] = main_entity().pk
        session.save()
        self.assertEqual(client.get(reverse("education:makeup_new", args=[foreign_student.pk])).status_code, 404)  # and the other way round
        elsewhere = self.make_group("مجموعة الفرع التاني", self.teacher_b, time(8), "قاعة ك", "0")
        StudyGroup.objects.filter(pk=elsewhere.pk).update(entity=branch)
        refused = client.post(reverse("education:makeup_book", args=[self.sara.pk]), {"lang": "ar", "home": self.home.pk, "slot": f"{elsewhere.pk}|{ahead(1).isoformat()}"})
        self.assertEqual(refused.status_code, 404)
        self.assertEqual(MakeupSeat.objects.count(), 1)

    def test_a_person_who_may_not_is_a_403_on_every_screen_and_nothing_changes(self):
        seat = self.book(day=ahead(0))
        for user in (self.cashier, self.stranger_user):  # a cashier; the teacher of another course's group
            client = self.as_(user)
            for method, url in self.urls(seat):
                with self.subTest(user=user.username, url=url, method=method):
                    self.assertEqual(self.call(client, method, url).status_code, 403)
        self.assertEqual((MakeupSeat.objects.count(), MakeupSeat.objects.get(pk=seat.pk).status), (1, "booked"))

    def test_the_home_teacher_may_cancel_but_not_mark_and_the_target_teacher_may_do_both(self):
        seat = self.book(day=ahead(0))
        self.assertEqual(self.post("makeup_attended", seat.pk, client=self.as_(self.teacher_user)).status_code, 403)
        self.assertEqual(self.post("makeup_cancel", seat.pk, client=self.as_(self.teacher_user)).status_code, 302)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "cancelled")
        again = self.book(day=ahead(0))
        self.assertEqual(self.post("makeup_cancel", again.pk, client=self.as_(self.teacher_b_user)).status_code, 302)
        self.assertEqual(MakeupSeat.objects.get(pk=again.pk).status, "cancelled")

    def test_every_change_is_post_only_and_csrf_protected(self):
        seat = self.book(day=ahead(0))
        for name, args in (("makeup_book", [self.sara.pk]), ("makeup_cancel", [seat.pk]), ("makeup_attended", [seat.pk]), ("makeup_missed", [seat.pk])):
            url = reverse(f"education:{name}", args=args)
            for verb in ("get", "put", "delete", "patch"):
                with self.subTest(name=name, verb=verb):
                    self.assertEqual(getattr(self.client, verb)(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for name, args in (("makeup_book", [self.sara.pk]), ("makeup_cancel", [seat.pk]), ("makeup_attended", [seat.pk]), ("makeup_missed", [seat.pk])):
            with self.subTest(csrf=name):
                self.assertEqual(client.post(reverse(f"education:{name}", args=args), {"lang": "ar", "home": self.home.pk}).status_code, 403)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "booked")

    def test_an_unknown_record_is_a_404(self):
        self.assertEqual(self.client.get(reverse("education:makeup_new", args=[999999])).status_code, 404)
        for name in ("makeup_cancel", "makeup_attended", "makeup_missed"):
            self.assertEqual(self.client.post(reverse(f"education:{name}", args=[999999]), {"lang": "ar"}).status_code, 404)


class TeacherPathTests(ScreenSetup):
    """A teacher with no access to the student file (review finding): the booking page is reachable from the register of their own group,
    and a booking or a cancellation returns to a page they may open, never to a 403."""

    def setUp(self):
        super().setUp()
        self.plain = person(RoleCode.WAITER, "mk_teacher_plain")  # a role with no master-data view, linked to the home group's teacher
        self.teacher.user = self.plain
        self.teacher.save()
        self.t = self.as_(self.plain)
        self.assertEqual(self.get("student", self.sara.pk, client=self.t).status_code, 403)  # the premise: no student file for this teacher
        self.assertTrue(attendance.can_take(self.plain, self.home))

    def test_the_register_of_the_home_group_offers_the_booking_page_for_each_student(self):
        page = self.get("attendance", self.home.pk, client=self.t)
        self.assertEqual(page.status_code, 200)
        entry = re.search(r"<details[^>]*data-makeup-entry.*?</details>", page.content.decode(), re.S)
        self.assertIsNotNone(entry)
        self.assertIn(reverse("education:makeup_new", args=[self.sara.pk]) + f"?lang=ar&amp;home={self.home.pk}", entry.group(0))
        self.assertGreater(page.content.decode().index("data-makeup-entry"), page.content.decode().index("</form>"))  # outside the register's own form

    def test_the_booking_page_goes_back_to_the_register_not_to_the_student_file(self):
        page = self.get("makeup_new", self.sara.pk, client=self.t, query=f"&home={self.home.pk}")
        self.assertEqual(page.status_code, 200)
        back = re.search(r'<a class="op-back" href="([^"]+)"', page.content.decode()).group(1).replace("&amp;", "&")
        self.assertNotIn(reverse("education:student", args=[self.sara.pk]), back)
        self.assertEqual(self.t.get(back).status_code, 200)

    def test_a_booking_and_a_cancellation_return_to_a_page_the_teacher_may_open(self):
        response = self.post("makeup_book", self.sara.pk, client=self.t, home=self.home.pk, slot=f"{self.other.pk}|{ahead(5).isoformat()}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.t.get(response["Location"]).status_code, 200)
        seat = MakeupSeat.objects.get()
        response = self.post("makeup_cancel", seat.pk, client=self.t)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.t.get(response["Location"]).status_code, 200)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "cancelled")

    def test_whoever_may_open_the_student_file_still_returns_to_it(self):
        response = self.post("makeup_book", self.sara.pk, home=self.home.pk, slot=f"{self.other.pk}|{ahead(5).isoformat()}")
        self.assertEqual(response["Location"], reverse("education:student", args=[self.sara.pk]) + "?lang=ar#makeup")


@skipUnless(connection.vendor == "postgresql", "FOR UPDATE OF is PostgreSQL's: SQLite has no row locks")
class SeatLockTests(MakeupSetup):
    """Review finding: a seat's change locks the seat row only. With the joined rows locked too, two seats with opposite home and target
    groups (A to B, B to A) could each hold their target group and wait for the other's home group: a deadlock."""

    def test_a_change_locks_the_seat_row_and_never_the_joined_groups_or_student(self):
        from django.test.utils import CaptureQueriesContext

        seat = self.book(day=ahead(0))
        with CaptureQueriesContext(connection) as queries:
            makeup.mark(seat, SeatStatus.MISSED, self.owner)
            scan.mark(self.sara.code, self.teacher_b_user, at(ahead(0), 18, 5))
            makeup.mark(seat, SeatStatus.ATTENDED, self.owner)
        locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"] and 'FROM "education_makeupseat"' in q["sql"]]
        self.assertGreaterEqual(len(locks), 3)
        for sql in locks:
            self.assertTrue(sql.rstrip().endswith('FOR UPDATE OF "education_makeupseat"'), sql)
        other = self.book(day=ahead(5))
        with CaptureQueriesContext(connection) as queries:
            makeup.cancel(other, self.owner)
        locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"] and 'FROM "education_makeupseat"' in q["sql"]]
        self.assertEqual(len(locks), 1)
        self.assertTrue(locks[0].rstrip().endswith('FOR UPDATE OF "education_makeupseat"'), locks[0])


# ---- the card scan at the door ----

class ScanTests(MakeupSetup):
    """Teacher B (or a manager) scans the card of a student with a booked seat in B's class today. 18:05 is inside the class's window (17:30 to 19:30)."""

    NOW = at(ahead(0), 18, 5)

    def test_a_scan_with_a_booked_seat_answers_makeup_and_marks_the_seat_attended(self):
        seat = self.book(day=ahead(0))
        result = scan.mark(self.sara.code, self.teacher_b_user, self.NOW)
        self.assertEqual((result.status, result.presence, result.at, result.changed, result.student, result.group), ("ok", "makeup", "18:05", True, self.sara, self.other))
        self.assertEqual(result.as_json()["presence_label"], "حصة تعويضية")
        self.assertIn("حضور سارة", result.message)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "attended")
        log = AuditLog.objects.get(action="scan_makeup_seat")
        self.assertEqual((log.object_type, log.object_id, log.actor, log.before_data["status"], log.after_data["status"]), ("education.MakeupSeat", str(seat.pk), self.teacher_b_user, "booked", "attended"))

    def test_the_scan_writes_no_mark_opens_no_class_and_does_not_flip_the_register_into_scanned_mode(self):
        self.book(day=ahead(0))
        scan.mark(self.sara.code, self.teacher_b_user, self.NOW)
        self.assertEqual((Attendance.objects.count(), ClassSession.objects.count()), (0, 0))
        self.assertFalse(AuditLog.objects.filter(action="scan_attendance").exists())
        self.assertEqual(scan.touched([1, 2, 3]), set())
        self.assertEqual(AuditLog.objects.filter(object_type="education.ClassSession").count(), 0)

    def test_a_rescan_says_again_and_writes_nothing(self):
        self.book(day=ahead(0))
        scan.mark(self.sara.code, self.teacher_b_user, self.NOW)
        logs = AuditLog.objects.count()
        again = scan.mark(self.sara.code, self.teacher_b_user, at(ahead(0), 18, 40))
        self.assertEqual((again.status, again.presence, again.changed), ("again", "makeup", False))
        self.assertEqual(AuditLog.objects.count(), logs)

    def test_a_missed_mark_is_corrected_by_the_scan(self):
        seat = self.book(day=ahead(0))
        makeup.mark(seat, "missed", self.owner)
        result = scan.mark(self.sara.code, self.owner, self.NOW)
        self.assertEqual((result.status, result.changed), ("ok", True))
        self.assertIn("اتصحّح لحضور", result.message)
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "attended")

    def test_without_a_seat_the_scan_is_refused_as_before_and_so_are_the_other_days_and_cancelled_seats(self):
        self.assertEqual(scan.mark(self.sara.code, self.teacher_b_user, self.NOW).status, "refused")  # the old answer, word for word
        self.assertEqual(scan.mark(self.sara.code, self.teacher_b_user, self.NOW).message, scan.MESSAGES["ar"]["refused"])
        self.book(day=ahead(1))
        self.assertEqual(scan.mark(self.sara.code, self.teacher_b_user, self.NOW).status, "refused")  # a seat for tomorrow is not a seat today
        gone = self.book(self.tala, day=ahead(0))
        makeup.cancel(gone, self.owner)
        self.assertEqual(scan.mark(self.tala.code, self.teacher_b_user, self.NOW).status, "refused")
        self.assertEqual(MakeupSeat.objects.filter(status="attended").count(), 0)

    def test_a_seat_opens_only_its_own_group_to_the_teacher_who_may_take_it(self):
        self.book(day=ahead(0))
        self.assertEqual(scan.mark(self.sara.code, self.stranger_user, self.NOW).status, "refused")  # a teacher of neither group
        self.assertEqual(scan.mark(self.sara.code, self.teacher_user, self.NOW).status, "no_class")  # the home teacher runs the 16:00 class, not that one
        with self.assertRaises(PermissionDenied):
            scan.mark(self.sara.code, self.cashier, self.NOW)  # not someone who scans at all
        self.assertEqual(MakeupSeat.objects.get().status, "booked")

    def test_outside_the_window_it_says_there_is_no_class_now_and_lists_the_classes(self):
        self.book(day=ahead(0))
        early = scan.mark(self.sara.code, self.owner, at(ahead(0), 12, 0))
        self.assertEqual(early.status, "no_class")
        self.assertEqual({group.pk for group in early.groups}, {self.home.pk, self.other.pk})
        self.assertEqual(MakeupSeat.objects.get().status, "booked")

    def test_a_student_with_a_class_of_their_own_and_a_seat_is_marked_in_whichever_window_holds_the_time(self):
        seat = self.book(day=ahead(0))
        own = scan.mark(self.sara.code, self.owner, at(ahead(0), 16, 5))  # her own group's class: the old path, a real mark
        self.assertEqual((own.status, own.presence, own.group), ("ok", "present", self.home))
        self.assertEqual(Attendance.objects.filter(student=self.sara, session__group=self.home).count(), 1)
        guest = scan.mark(self.sara.code, self.owner, self.NOW)
        self.assertEqual((guest.status, guest.presence, guest.group), ("ok", "makeup", self.other))
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "attended")
        self.assertEqual(Attendance.objects.filter(student=self.sara).count(), 1)  # the guest visit added no mark
        self.assertEqual(ClassSession.objects.filter(group=self.other).count(), 0)

    def test_two_classes_at_once_ask_which_one_and_the_answer_picks_the_seat(self):
        late = self.make_group("مجموعة المراجعة المتأخرة", self.stranger_teacher, time(17), "قاعة ص", "0")
        services.enroll(self.sara, late, self.owner, start_date=ahead(-60))
        self.book(day=ahead(0))
        choose = scan.mark(self.sara.code, self.owner, at(ahead(0), 17, 40))  # her 17:00 class (to 18:30) and the guest class (from 17:30)
        self.assertEqual(choose.status, "choose")
        self.assertEqual({group.pk for group in choose.choices}, {late.pk, self.other.pk})
        picked = scan.mark(self.sara.code, self.owner, at(ahead(0), 17, 40), group=self.other.pk)
        self.assertEqual((picked.status, picked.presence), ("ok", "makeup"))

    def test_a_cancelled_class_is_answered_as_cancelled_and_the_seat_stays_booked(self):
        self.book(day=ahead(0))
        ClassSession.objects.create(group=self.other, date=ahead(0), status="cancelled", taken_by=self.owner)
        result = scan.mark(self.sara.code, self.teacher_b_user, self.NOW)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(MakeupSeat.objects.get().status, "booked")
        self.assertFalse(AuditLog.objects.filter(action="scan_makeup_seat").exists())

    def test_a_scan_into_a_class_whose_register_is_already_saved_leaves_the_registers_digest_alone(self):
        attendance.take(self.other, self.owner, ahead(0), {str(self.nour.pk): "present"})
        session = ClassSession.objects.get(group=self.other, date=ahead(0))
        digest = attendance.sheet_version(session)
        self.book(day=ahead(0))
        scan.mark(self.sara.code, self.teacher_b_user, self.NOW)
        self.assertEqual(attendance.sheet_version(ClassSession.objects.get(pk=session.pk)), digest)
        self.assertEqual(session.marks.count(), 1)

    def test_a_scan_of_a_guest_first_creates_no_class_so_the_teachers_pay_does_not_move(self):
        self.classes()
        before = self.figures()
        self.book(day=ahead(0))
        self.book(self.tala, day=ahead(0))
        scan.mark(self.sara.code, self.teacher_b_user, self.NOW)
        scan.mark(self.tala.code, self.teacher_b_user, at(ahead(0), 18, 30))
        scan.mark(self.sara.code, self.teacher_b_user, at(ahead(0), 18, 40))
        self.assertEqual(MakeupSeat.objects.filter(status="attended").count(), 2)
        self.assertEqual(self.figures(), before)

    def test_the_scan_screen_answers_in_json_in_both_languages(self):
        self.book(day=ahead(0))
        self.client.force_login(self.teacher_b_user)
        with mock.patch("django.utils.timezone.now", return_value=self.NOW):
            arabic = self.client.post(reverse("education:scan_mark"), {"code": self.sara.code, "lang": "ar"})
        data = arabic.json()
        self.assertEqual((arabic.status_code, data["status"], data["presence"], data["presence_label"], data["group"]["name"]), (200, "ok", "makeup", "حصة تعويضية", self.other.name))
        self.assertEqual(data["student"]["name"], "سارة")
        with mock.patch("django.utils.timezone.now", return_value=self.NOW):
            english = self.client.post(reverse("education:scan_mark"), {"code": self.sara.code, "lang": "en"}).json()
        self.assertEqual((english["status"], english["presence_label"]), ("again", "Makeup class"))

    def test_the_scan_pages_script_needs_no_change_to_draw_the_answer(self):
        script = pathlib.Path(settings.BASE_DIR, "static", "hesba", "js", "edu_scan.js").read_text(encoding="utf-8")
        self.assertIn("data.presence_label", script)  # the chip is drawn from the label the server sends
        template = pathlib.Path(settings.BASE_DIR, "templates", "education", "scan.html").read_text(encoding="utf-8")
        self.assertIn("education_scan.css' %}?v=e1-2", template)


# ---- the guardian portal ----

class PortalSeatTests(PortalSetup):
    """Each child's block lists their own seats (E8). The portal only reads: a visit still writes exactly two rows."""

    SECRET_NOTE = "ملاحظة حجز داخلية سرية جدًا"
    THIRD = "مجموعة الرياضيات الثانية الخاصة"
    SCIENCE2 = "مجموعة العلوم الثانية الخاصة"

    def setUp(self):
        super().setUp()
        self.group_a2 = self.make_group(self.THIRD, self.course_a, self.teacher_b, EVERY_DAY, "none", "0", "19:00", "قاعة رياضيات 2")
        self.group_b2 = self.make_group(self.SCIENCE2, self.course_b, self.teacher_a, EVERY_DAY, "none", "0", "13:00", "قاعة علوم 2")

    def seat(self, student, home, group, ahead_days, status="booked", note=""):
        seat = makeup.book(student, home, group, FIXED.date() + timedelta(days=max(ahead_days, 0)), self.owner, note=note)
        if ahead_days < 0 or status != "booked":
            MakeupSeat.objects.filter(pk=seat.pk).update(day=FIXED.date() + timedelta(days=ahead_days), status=status)
        return MakeupSeat.objects.get(pk=seat.pk)

    def child_block(self, html, name):
        for found in re.finditer(r'<article class="gp-child".*?</article>', html, re.S):
            if name in found.group(0).split("</h2>")[0]:
                return found.group(0)
        self.fail(f"no block for {name}")

    def makeup_of(self, html, name):
        found = re.search(r'<section[^>]*data-section="makeup".*?</section>', self.child_block(html, name), re.S)
        return found.group(0) if found else ""

    def items(self, section):
        return re.findall(r'data-makeup-item data-status="(\w+)".*?<strong><bdi>([^<]*)</bdi>', section, re.S)

    def test_a_childs_block_lists_the_coming_seats_first_and_the_last_month_of_attended_and_missed(self):
        self.seat(self.yousef, self.group_a, self.group_a2, 3, note=self.SECRET_NOTE)
        self.seat(self.yousef, self.group_a, self.group_a2, 1)
        self.seat(self.yousef, self.group_a, self.group_a2, -5, "attended")
        self.seat(self.yousef, self.group_a, self.group_a2, -29, "missed")
        client, token, page = self.open_portal()
        section = self.makeup_of(page.content.decode(), YOUSEF)
        self.assertIn("حصص تعويضية", section)
        statuses = [status for status, _ in self.items(section)]
        self.assertEqual(statuses, ["booked", "booked", "attended", "missed"])  # coming ones soonest first, then the past, newest first
        days = [text for _, text in self.items(section)]
        self.assertEqual(days, ["12 نوفمبر 2026", "14 نوفمبر 2026", "6 نوفمبر 2026", "13 أكتوبر 2026"])
        self.assertEqual(section.count(self.THIRD), 4)
        self.assertRegex(section, r"</bdi>، <bdi dir=\"ltr\">19:00</bdi>")
        self.assertNotIn(" · ", section)

    def test_what_is_not_shown(self):
        self.seat(self.yousef, self.group_a, self.group_a2, 2, "cancelled")
        self.seat(self.yousef, self.group_a, self.group_a2, -30, "attended")  # 31 days ago is outside the last 30 days: only 29 days back counts as day one
        self.seat(self.yousef, self.group_a, self.group_a2, -3, "booked")  # a past seat nobody marked: not listed, and not turned into «missed» by being looked at
        self.seat(self.yousef, self.group_a, self.group_a2, 4, note=self.SECRET_NOTE)
        client, token, page = self.open_portal()
        html = page.content.decode()
        section = self.makeup_of(html, YOUSEF)
        self.assertEqual([status for status, _ in self.items(section)], ["booked"])
        self.assertEqual(MakeupSeat.objects.filter(status="booked", day=day(3)).count(), 1)
        self.assertNotIn(self.SECRET_NOTE, normalized(page, token))  # a seat's note is the centre's own
        self.assertNotIn("البيانات الداخلية", section)

    def test_a_childs_block_has_no_makeup_section_when_there_is_nothing_to_show(self):
        client, token, page = self.open_portal()
        self.assertEqual(self.makeup_of(page.content.decode(), YOUSEF), "")

    def test_each_seat_is_under_its_own_child_and_another_payers_child_never_appears(self):
        self.seat(self.yousef, self.group_a, self.group_a2, 2)
        self.seat(self.stranger, self.group_a, self.group_a2, 2)  # another family, in the same group
        mine = makeup.book(self.mariam, self.group_b, self.group_b2, FIXED.date() + timedelta(days=3), self.owner)
        client, token, page = self.open_portal()
        html = page.content.decode()
        self.assertEqual([status for status, _ in self.items(self.makeup_of(html, YOUSEF))], ["booked"])
        self.assertEqual(self.items(self.makeup_of(html, MARIAM)), [("booked", "14 نوفمبر 2026")])
        self.assertIn(self.SCIENCE2, self.makeup_of(html, MARIAM))
        self.assertNotIn(self.SCIENCE2, self.makeup_of(html, YOUSEF))
        self.assertEqual(html.count("data-makeup-item"), 2)  # the stranger's seat is nowhere on the page
        self.assertEqual(mine.status, "booked")
        client2, token2, page2 = self.open_portal(self.stranger, answer="9/9/2009")
        self.assertEqual(page2.content.decode().count("data-makeup-item"), 1)

    def test_a_coming_seat_whose_class_was_cancelled_says_so(self):
        seat = self.seat(self.yousef, self.group_a, self.group_a2, 2)
        ClassSession.objects.create(group=self.group_a2, date=seat.day, status="cancelled", taken_by=self.owner)
        client, token, page = self.open_portal()
        section = self.makeup_of(page.content.decode(), YOUSEF)
        self.assertIn("الحصة اتلغت", section)
        self.assertIn("gp-chip--late", section)

    def test_a_seat_of_another_entity_is_never_shown(self):
        seat = self.seat(self.yousef, self.group_a, self.group_a2, 2)
        branch = Entity.objects.create(code="E-MKP", name_ar="فرع البوابة", activity_slug="education", sub_activity_slug="tutoring_center")
        StudyGroup.objects.filter(pk=self.group_a2.pk).update(entity=branch)
        client, token, page = self.open_portal()
        self.assertEqual(self.makeup_of(page.content.decode(), YOUSEF), "")
        self.assertEqual(MakeupSeat.objects.get(pk=seat.pk).status, "booked")

    def test_nothing_is_shown_before_the_second_factor(self):
        self.seat(self.yousef, self.group_a, self.group_a2, 2, note=self.SECRET_NOTE)
        token = self.token()
        asked = self.anon().get(self.url(token))
        body = normalized(asked, token)
        self.assertContains(asked, 'data-guardian-portal="ask"')
        self.assertNotContains(asked, "data-section")
        self.assertNotContains(asked, "data-makeup-item")
        for secret in (self.THIRD, self.SECRET_NOTE, "حصص تعويضية", YOUSEF):
            self.assertNotIn(secret, body)
        wrong = self.post(self.anon(), token, WRONG_ANSWER)
        self.assertNotContains(wrong, "data-makeup-item", status_code=400)
        self.assertNotIn(self.THIRD, normalized(wrong, token))

    def test_the_visit_still_writes_exactly_two_rows_and_reads_the_seats_once(self):
        self.seat(self.yousef, self.group_a, self.group_a2, 2)
        self.seat(self.yousef, self.group_a, self.group_a2, 3)
        client, token, page = self.open_portal()
        client.get(self.url(token))
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            self.assertContains(client.get(self.url(token)), "data-makeup-item")
        statements = [q["sql"] for q in queries]
        writes = [sql for sql in statements if sql.split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]
        self.assertEqual(len(writes), 2, writes)  # last_opened_at and the audit row, as before
        self.assertFalse(any("makeupseat" in sql for sql in writes))
        reads = [sql for sql in statements if "education_makeupseat" in sql]
        self.assertEqual(len(reads), 1, reads)  # one read for every child together

    def test_the_visit_changes_no_seat_and_no_number(self):
        self.seat(self.yousef, self.group_a, self.group_a2, -3, "booked")
        before = (self.counts(), list(MakeupSeat.objects.order_by("pk").values_list("pk", "status", "updated_at")))
        client, token, page = self.open_portal()
        client.get(self.url(token, "en"))
        self.assertEqual((self.counts(), list(MakeupSeat.objects.order_by("pk").values_list("pk", "status", "updated_at"))), before)

    def test_the_english_portal_reads_in_english(self):
        self.seat(self.yousef, self.group_a, self.group_a2, 2)
        self.seat(self.yousef, self.group_a, self.group_a2, -4, "missed")
        client, token, page = self.open_portal(lang="en")
        section = self.makeup_of(page.content.decode(), YOUSEF)
        self.assertIn("Makeup classes", section)
        self.assertIn(">Booked<", section)
        self.assertIn(">Absent<", section)
        self.assertRegex(section, r"</bdi>, <bdi dir=\"ltr\">19:00</bdi>")


WRONG_ANSWER = "1/1/2000"


# ---- words and the stylesheet ----

class WordingTests(SimpleTestCase):
    def flat(self, value):
        return [text for item in value.values() for text in self.flat(item)] if isinstance(value, dict) else [value]

    def test_every_new_arabic_line_is_plain_premium_and_gender_neutral_where_it_names_a_child(self):
        from reports.tests_customer_voice import GENDERED, _problems

        from . import portal, scan, views_makeup

        sources = {"views_makeup": views_makeup.WORDS["ar"], "makeup": makeup.MESSAGES["ar"], "portal": {k: v for k, v in portal.WORDS["ar"].items() if k.startswith("makeup")},
                   "scan": {k: v for k, v in scan.MESSAGES["ar"].items() if k.startswith("makeup")}}
        for where, words in sources.items():
            for text in self.flat(words):
                with self.subTest(where=where, text=text):
                    for banned in ("ورشة", "الأسطى", "بتاع", "دوس", "لينك"):
                        self.assertNotIn(banned, text)
                    self.assertEqual(_problems(text), [])
                    for gendered in GENDERED + ("الابن", "ابنك", "ابنة", "بنتك"):
                        self.assertNotIn(gendered, text)
                    stripped = re.sub(r"[\s.،:؛!؟?()«»\-–—{}]+", " ", re.sub(r"\{\w+\}", "", text)).strip()
                    if stripped:
                        self.assertNotRegex(stripped[:1], r"[A-Za-z]")
                        self.assertNotRegex(stripped[-1:], r"[A-Za-z]")

    def test_the_status_chips_beside_a_childs_name_are_nouns_and_only_the_buttons_use_the_verbs(self):
        from . import views_makeup

        chips = views_makeup.WORDS["ar"]["status"]
        self.assertEqual((chips["attended"], chips["missed"]), ("حضور", "غياب"))
        self.assertEqual((views_makeup.WORDS["ar"]["attended"], views_makeup.WORDS["ar"]["missed"]), ("حضر", "ماحضرش"))  # the two buttons, as the plan words them
        for text in self.flat(chips) + [views_makeup.WORDS["ar"]["attended_ok"], views_makeup.WORDS["ar"]["missed_ok"]]:
            self.assertNotIn("حضر ", text + " ")
            self.assertNotIn("ماحضرش", text)

    def test_the_two_languages_have_the_same_words(self):
        from . import portal, scan, views_makeup

        self.assertEqual(set(views_makeup.WORDS["ar"]), set(views_makeup.WORDS["en"]))
        self.assertEqual(set(views_makeup.WORDS["ar"]["status"]), set(views_makeup.WORDS["en"]["status"]))
        for key in ("makeup_h", "makeup_note", "makeup_status", "makeup_cancelled"):
            self.assertIn(key, portal.WORDS["ar"])
            self.assertIn(key, portal.WORDS["en"])
        self.assertEqual(set(portal.WORDS["ar"]["makeup_status"]), set(portal.WORDS["en"]["makeup_status"]))
        for key in ("makeup", "makeup_note", "makeup_was_missed"):
            self.assertIn(key, scan.MESSAGES["en"])
        self.assertEqual(views_makeup.WORDS["ar"]["guests_h"], "ضيوف الحصة (تعويضي)")

    def test_no_template_joins_a_date_and_a_time_with_a_dot_and_the_guests_section_is_not_in_the_registers_form(self):
        base = pathlib.Path(settings.BASE_DIR, "templates", "education")
        for name in ("_makeup_card.html", "_makeup_guests.html", "makeup_new.html"):
            self.assertNotIn("·", (base / name).read_text(encoding="utf-8"), name)
        portal = (base / "portal.html").read_text(encoding="utf-8")
        section = portal[portal.index('data-section="makeup"'):portal.index('data-section="attendance"')]
        self.assertNotIn("·", section)
        attendance = (base / "attendance.html").read_text(encoding="utf-8")
        self.assertGreater(attendance.index("_makeup_guests.html"), attendance.index("</form>"))


class StylesheetTests(SimpleTestCase):
    def test_the_stylesheet_takes_its_colours_from_the_tokens_and_keeps_every_tap_target_at_44px(self):
        css = pathlib.Path(settings.BASE_DIR, "static", "hesba", "css", "education_makeup.css").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css), [])
        self.assertRegex(css, r"\.mk-btn\{[^}]*min-height:44px")
        self.assertRegex(css, r"\.mk-option\{[^}]*min-height:56px")
        for name in ("student.html", "attendance.html", "today.html", "makeup_new.html"):
            template = pathlib.Path(settings.BASE_DIR, "templates", "education", name).read_text(encoding="utf-8")
            self.assertIn("education_makeup.css' %}?v=e8-2", template, name)
        portal = pathlib.Path(settings.BASE_DIR, "static", "hesba", "css", "education_portal.css").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", portal), [])


# ---- two bookings at once ----

class RaceSetup(TransactionTestCase):
    """Committed data (a TransactionTestCase) so threads can see it: a group with room for 3 and one enrolled student, and eight students at home."""

    serialized_rollback = True

    def setUp(self):
        from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
        from staff.services import save_employee

        self.owner = make_user(username="mk_race_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))
        course = services.save_course({"name": "مراجعة السباق", "fee": "50", "basis": "session"}, self.owner)
        data = {"course": course, "days": EVERY_DAY, "start_time": time(16), "duration_minutes": "90"}
        self.home = services.save_group({**data, "name": "مجموعة السباق الأولى", "teacher": save_employee({"name": "أ. أولى"}, self.owner), "room": "ر1", "capacity": "0"}, self.owner)
        self.target = services.save_group({**data, "name": "مجموعة السباق الثانية", "teacher": save_employee({"name": "أ. ثانية"}, self.owner), "room": "ر2", "capacity": "3"}, self.owner)
        self.day = timezone.localdate() + timedelta(days=1)
        self.students = []
        for n in range(8):
            student = services.save_student({"name": f"طالب السباق {n}", "payer_name": f"ولي أمر {n}", "payer_phone": f"01007{n:06d}"}, self.owner)
            services.enroll(student, self.home, self.owner, start_date=timezone.localdate() - timedelta(days=30))
            self.students.append(student)
        services.enroll(services.save_student({"name": "المسجّل في المجموعة", "payer_name": "ولي أمر مسجّل", "payer_phone": "01008000000"}, self.owner), self.target, self.owner)

    def run_threads(self, tasks):
        if connection.vendor != "postgresql":
            # SQLite's in-memory test database cannot take concurrent writers from several connections («database table is locked»), and it has no
            # row locks: here the same tasks run one after the other, which is what its writers do anyway. The race itself is proven on PostgreSQL.
            results = []
            for task in tasks:
                try:
                    results.append(task())
                except ValidationError as error:
                    results.append("refused: " + " ".join(error.messages))
            return results
        results, errors, barrier = [], [], threading.Barrier(len(tasks), timeout=30)

        def work(task):
            try:
                barrier.wait()
                results.append(task())
            except ValidationError as error:
                results.append("refused: " + " ".join(error.messages))
            except Exception as error:  # noqa: BLE001  (reported below, never swallowed)
                errors.append(repr(error))
            finally:
                connection.close()

        threads = [threading.Thread(target=work, args=(task,)) for task in tasks]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        return results


class SimultaneousBookingTests(RaceSetup):
    """On PostgreSQL the target group's row lock serialises the threads (real concurrent connections). On SQLite there are no row locks and writers
    take turns, so the same eight threads run one writer at a time: the count must still never pass the room."""

    def book_all(self):
        return self.run_threads([lambda s=s: makeup.book(s, self.home, self.target, self.day, self.owner) for s in self.students])

    def test_eight_bookings_at_once_never_exceed_the_room(self):
        results = self.book_all()
        booked = [r for r in results if not isinstance(r, str)]
        refused = [r for r in results if isinstance(r, str)]
        self.assertEqual(len(results), 8)
        self.assertEqual((len(booked), len(refused)), (2, 6), results)  # capacity 3, one student enrolled: two places
        self.assertTrue(all("مكتملة" in r for r in refused), refused)
        self.assertEqual(MakeupSeat.objects.filter(group=self.target, day=self.day).exclude(status="cancelled").count(), 2)
        self.assertEqual(makeup.free_seats(self.target, self.day), 0)

    def test_a_cancel_among_bookings_at_once_never_overfills_the_room(self):
        first = makeup.book(self.students[0], self.home, self.target, self.day, self.owner)
        tasks = [lambda: makeup.cancel(first, self.owner)] + [lambda s=s: makeup.book(s, self.home, self.target, self.day, self.owner) for s in self.students[1:5]]
        self.run_threads(tasks)
        live = MakeupSeat.objects.filter(group=self.target, day=self.day).exclude(status="cancelled").count()
        self.assertTrue(1 <= live <= 2, live)  # whichever ran first, the room ends within its two places, never over


@skipUnless(connection.vendor == "postgresql", "row locks are PostgreSQL's: SQLite has no select_for_update, its writers already take turns")
class PostgresLockTests(RaceSetup):
    def test_the_booking_takes_the_target_group_row_lock_first(self):
        statements = []
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            makeup.book(self.students[0], self.home, self.target, self.day, self.owner)
        statements = [q["sql"] for q in queries]
        locked = [i for i, sql in enumerate(statements) if "FOR UPDATE" in sql and "education_studygroup" in sql]
        inserted = [i for i, sql in enumerate(statements) if sql.startswith("INSERT") and "education_makeupseat" in sql]
        self.assertTrue(locked and inserted and locked[0] < inserted[0], statements)


# ---- the migration ----

class MigrationTests(TransactionTestCase):
    """0012 only creates the one table, after 0011 (E9's); the groups that already exist are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0011_creates_one_table_and_leaves_existing_data_alone(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("education", "0012_makeup_seats")]
        self.assertIn(("education", "0011_group_content"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["CreateModel"])
        executor.migrate([("education", "0011_group_content")])
        self.assertNotIn("education_makeupseat", connection.introspection.table_names())
        leaves = [n for n in executor.loader.graph.leaf_nodes() if n[0] != "education"] + [("education", "0011_group_content")]
        old = executor.loader.project_state(leaves).apps
        user = old.get_model("auth", "User").objects.create(username="mig_e8")
        item = old.get_model("master_data", "Item").objects.create(item_code="I-E8", item_name="خدمة قديمة", is_stock_tracked=False)
        course = old.get_model("education", "Course").objects.create(name="مادة قديمة", fee="100", item=item)
        group = old.get_model("education", "StudyGroup").objects.create(code="G-OLD8", course=course, name="مجموعة قديمة")
        payer = old.get_model("master_data", "Customer").objects.create(customer_code="C-E8", name="ولي أمر قديم", phone="01001234567")
        student = old.get_model("education", "Student").objects.create(code="ST-OLD8", name="طالب قديم", payer=payer, created_by=user)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("education", "0012_makeup_seats")])
        self.assertIn("education_makeupseat", connection.introspection.table_names())
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "education_makeupseat")
        self.assertIn("edu_makeup_group_day_idx", constraints)
        self.assertIn("education_one_live_makeup_seat_per_class", constraints)
        self.assertEqual(StudyGroup.objects.get(pk=group.pk).name, "مجموعة قديمة")
        first = MakeupSeat.objects.create(student_id=student.pk, home_group_id=group.pk, group_id=group.pk, day="2026-12-01", created_by_id=user.pk)
        self.assertEqual((first.status, first.missed_on), ("booked", None))
        with self.assertRaises(IntegrityError), transaction.atomic():
            MakeupSeat.objects.create(student_id=student.pk, home_group_id=group.pk, group_id=group.pk, day="2026-12-01", created_by_id=user.pk)
