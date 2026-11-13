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
