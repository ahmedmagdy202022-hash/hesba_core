"""E2: exams, marks and the report card: the rules in ``education.exams``, with explicit numbers.

"Today" comes from ``timezone.localdate()`` and every day is built from it; no clock is patched. Nothing here may move money: the
counts of invoices, cashbox movements and journal entries are pinned around a whole run of the feature.
"""

import pathlib
from datetime import time, timedelta
from decimal import Decimal as D

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from ledger.models import JournalEntry
from messaging.models import OutboxMessage
from permissions.models import RoleCode
from sales.models import SalesInvoice

from . import attendance, exams, services
from .models import Exam, ExamMark
from .tests import EducationSetup, person

TODAY = timezone.localdate()
EVERY_DAY = "sun,mon,tue,wed,thu,fri,sat"


def day(n):
    return TODAY - timedelta(days=n)


class ExamSetup(EducationSetup):
    """Group A (the teacher's) with Adam, Basma and Gad; group B (another teacher's) with Basma and Hana; an owner, a teacher user and a cashier."""

    def setUp(self):
        super().setUp()
        from staff.services import save_employee

        self.teacher_user = person(RoleCode.CASHIER, "exam_teacher")  # a cashier account linked to an employee: a teacher, nothing more
        self.teacher.user = self.teacher_user
        self.teacher.save()
        self.other_teacher = save_employee({"name": "أ. هالة"}, self.owner)
        self.other_user = person(RoleCode.CASHIER, "exam_other_teacher")
        self.other_teacher.user = self.other_user
        self.other_teacher.save()
        self.cashier = person(RoleCode.CASHIER, "exam_cashier")
        self.a = self.make_group("مجموعة أ", self.teacher, time(16))
        self.b = self.make_group("مجموعة ب", self.other_teacher, time(18))
        self.adam, self.basma, self.gad, self.hana = (self.enrolled("آدم", self.a), self.enrolled("بسمة", self.a), self.enrolled("جاد", self.a),
                                                      self.enrolled("هنا", self.b))
        services.enroll(self.basma, self.b, self.owner, start_date=day(60))

    def make_group(self, name, teacher, start):
        return services.save_group({"course": self.maths, "name": name, "teacher": teacher, "days": EVERY_DAY, "start_time": start, "duration_minutes": "90",
                                    "capacity": "0"}, self.owner)

    def enrolled(self, name, group):
        student = services.save_student({"name": name, "payer_name": f"ولي أمر {name}", "payer_phone": f"0100{abs(hash(name)) % 10**7:07d}"}, self.owner)
        services.enroll(student, group, self.owner, start_date=day(60))
        return student

    def exam(self, group=None, title="اختبار", held=3, out_of="20", kind="quiz", **extra):
        return exams.save_exam(group or self.a, {"title": title, "kind": kind, "held_on": day(held), "out_of": out_of, **extra}, self.owner)

    def mark(self, exam, student, score="", absent=False, note=""):
        return exams.record_marks(exam, {student.pk: {"score": score, "absent": absent, "note": note}}, self.owner)

    def marks_of(self, exam):
        return {mark.student.name: (mark.score, mark.absent, mark.note) for mark in exam.marks.select_related("student")}


class ExamRulesTests(ExamSetup):
    def test_an_exam_is_saved_unpublished_with_its_audit(self):
        exam = self.exam(title="امتحان الشهر", kind="monthly", out_of="50", notes="الباب الأول")
        self.assertEqual((exam.group, exam.title, exam.kind, exam.held_on, exam.out_of, exam.published, exam.notes, exam.created_by),
                         (self.a, "امتحان الشهر", "monthly", day(3), D("50.00"), False, "الباب الأول", self.owner))
        log = AuditLog.objects.get(action="save_exam")
        self.assertEqual((log.object_id, log.before_data, log.after_data["out_of"], log.after_data["published"]), (str(exam.pk), {}, "50.00", False))
        changed = exams.save_exam(self.a, {"title": "امتحان الشهر (معدّل)", "kind": "monthly", "held_on": day(3), "out_of": "60"}, self.owner, exam)
        self.assertEqual((changed.pk, changed.out_of), (exam.pk, D("60.00")))
        update = AuditLog.objects.filter(action="save_exam").latest("pk")
        self.assertEqual((update.before_data["out_of"], update.after_data["out_of"], update.before_data["title"]), ("50.00", "60.00", "امتحان الشهر"))

    def test_bad_exam_input_is_refused(self):
        base = {"title": "x", "kind": "quiz", "held_on": day(1), "out_of": "10"}
        for patch, message in (({"title": "  "}, "اسم الامتحان"), ({"kind": "oral"}, "نوع الامتحان"), ({"held_on": "2026-13-45"}, "التاريخ"),
                               ({"out_of": "0"}, "الدرجة النهائية"), ({"out_of": "-5"}, "الدرجة النهائية"), ({"out_of": "abc"}, "الدرجة النهائية"),
                               ({"out_of": "NaN"}, "الدرجة النهائية"), ({"out_of": "10.005"}, "الدرجة النهائية"), ({"out_of": "100000"}, "الدرجة النهائية")):
            with self.subTest(patch=patch), self.assertRaisesMessage(ValidationError, message):
                exams.save_exam(self.a, {**base, **patch}, self.owner)
        self.assertFalse(Exam.objects.exists())
        services.save_group({"course": self.maths, "name": "مجموعة أ", "teacher": self.teacher, "days": EVERY_DAY, "start_time": time(16), "duration_minutes": "90",
                             "capacity": "0", "active_present": "1"}, self.owner, self.a)  # switched off
        with self.assertRaisesMessage(ValidationError, "مقفولة"):
            exams.save_exam(self.a, base, self.owner)

    def test_who_may_save_an_exam(self):
        own = exams.save_exam(self.a, {"title": "كويز", "kind": "quiz", "held_on": day(1), "out_of": "10"}, self.teacher_user)  # the group's teacher
        self.assertEqual(own.created_by, self.teacher_user)
        for user in (self.other_user, self.cashier):  # another group's teacher; a cashier
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                exams.save_exam(self.a, {"title": "كويز", "kind": "quiz", "held_on": day(1), "out_of": "10"}, user)
        self.assertEqual(Exam.objects.count(), 1)

    def test_once_marks_exist_the_day_and_the_full_mark_are_held(self):
        exam = self.exam(out_of="20")
        self.mark(exam, self.adam, "18")
        fields = {"title": "اختبار", "kind": "quiz", "held_on": day(3), "out_of": "20"}
        with self.assertRaisesMessage(ValidationError, "يوم الامتحان"):
            exams.save_exam(self.a, {**fields, "held_on": day(2)}, self.owner, exam)
        with self.assertRaisesMessage(ValidationError, "18"):
            exams.save_exam(self.a, {**fields, "out_of": "15"}, self.owner, exam)
        self.assertEqual(exams.save_exam(self.a, {**fields, "out_of": "18"}, self.owner, exam).out_of, D("18.00"))  # equal to the top mark is fine
        free = self.exam(title="من غير درجات", held=5)
        self.assertEqual(exams.save_exam(self.a, {**fields, "held_on": day(4), "out_of": "5"}, self.owner, free).held_on, day(4))

    def test_marks_are_kept_as_typed_with_arabic_digits_and_a_decimal_comma(self):
        exam = self.exam(out_of="20")
        result = exams.record_marks(exam, {self.adam.pk: {"score": "18"}, self.basma.pk: {"score": "١٥٫٥"}, self.gad.pk: {"score": "7,25"}}, self.owner)
        self.assertEqual(result, {"changed": 3, "entered": 3, "seats": 3})
        self.assertEqual(self.marks_of(exam), {"آدم": (D("18.00"), False, ""), "بسمة": (D("15.50"), False, ""), "جاد": (D("7.25"), False, "")})

    def test_a_mark_outside_zero_to_the_full_mark_or_not_a_number_is_refused_and_nothing_is_written(self):
        exam = self.exam(out_of="20")
        self.mark(exam, self.adam, "10")
        for typed, message in (("20.01", "من صفر لحد 20"), ("-1", "من صفر لحد 20"), ("abc", "من صفر"), ("NaN", "من صفر"), ("Infinity", "من صفر"),
                               ("12.345", "كسرين عشريين")):
            with self.subTest(typed=typed), self.assertRaisesMessage(ValidationError, message):
                exams.record_marks(exam, {self.adam.pk: {"score": "12"}, self.basma.pk: {"score": typed}}, self.owner)
        self.assertEqual(self.marks_of(exam), {"آدم": (D("10.00"), False, "")})  # Adam's valid 12 was not saved either: all or nothing
        self.assertEqual(exams.record_marks(exam, {self.basma.pk: {"score": "20"}}, self.owner)["entered"], 2)  # the full mark itself is allowed
        self.assertEqual(exams.record_marks(exam, {self.gad.pk: {"score": "0"}}, self.owner)["entered"], 3)  # and zero is a mark, not "not entered"

    def test_absent_and_a_score_never_go_together(self):
        exam = self.exam()
        with self.assertRaisesMessage(ValidationError, "غايب ومكتوب له درجة"):
            self.mark(exam, self.adam, "12", absent=True)
        self.assertFalse(exam.marks.exists())
        self.mark(exam, self.adam, absent=True, note="سافر")
        self.assertEqual(self.marks_of(exam), {"آدم": (None, True, "سافر")})
        self.mark(exam, self.adam, "9")  # came after all: absent goes off with the score
        self.assertEqual(self.marks_of(exam), {"آدم": (D("9.00"), False, "")})

    def test_a_blank_score_is_not_entered_yet(self):
        exam = self.exam()
        self.assertEqual(self.mark(exam, self.adam, ""), {"changed": 0, "entered": 0, "seats": 3})
        self.assertFalse(exam.marks.exists())  # no row for "nothing"
        self.mark(exam, self.adam, "11")
        self.assertEqual(self.mark(exam, self.adam, "")["entered"], 0)  # cleared again
        self.assertFalse(exam.marks.exists())
        self.assertEqual(self.mark(exam, self.adam, "", note="يراجع")["entered"], 0)  # a note alone keeps its row but is still not entered
        self.assertEqual(self.marks_of(exam), {"آدم": (None, False, "يراجع")})

    def test_only_the_roster_of_the_exams_day_has_marks(self):
        later = services.save_student({"name": "جديد", "payer_name": "ولي جديد", "payer_phone": "01012345678"}, self.owner)
        services.enroll(later, self.a, self.owner, start_date=day(1))  # joined after the exam
        left = self.enrolled("غادر", self.a)
        services.stop(left.enrollments.get(), self.owner, end_date=day(5))  # left before it
        exam = self.exam(held=3)
        for student in (later, left, self.hana):  # joined after; left before; another group's student
            with self.subTest(student=student.name), self.assertRaisesMessage(ValidationError, "مش في المجموعة"):
                self.mark(exam, student, "5")
        with self.assertRaisesMessage(ValidationError, "مش في المجموعة"):
            exams.record_marks(exam, {"not-a-number": {"score": "5"}}, self.owner)
        self.assertFalse(exam.marks.exists())
        stays = self.enrolled("لسه", self.a)
        services.stop(stays.enrollments.get(), self.owner, end_date=day(3))  # stopped on the day itself: still on that day's roster
        self.mark(exam, stays, "6")
        earlier = self.exam(title="قبل ما يدخل", held=2)  # the same student the day after he stopped is off the roster
        with self.assertRaisesMessage(ValidationError, "مش في المجموعة"):
            self.mark(earlier, stays, "6")

    def test_marks_wait_for_the_day(self):
        exam = exams.save_exam(self.a, {"title": "جاي", "kind": "final", "held_on": TODAY + timedelta(days=2), "out_of": "100"}, self.owner)
        self.assertEqual(exam.held_on, TODAY + timedelta(days=2))  # announced ahead
        with self.assertRaisesMessage(ValidationError, "لسه ماجاش يومه"):
            self.mark(exam, self.adam, "50")

    def test_who_may_enter_marks(self):
        exam = self.exam()
        exams.record_marks(exam, {self.adam.pk: {"score": "5"}}, self.teacher_user)  # its own teacher
        for user in (self.other_user, self.cashier):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                exams.record_marks(exam, {self.adam.pk: {"score": "1"}}, user)
        self.assertEqual(self.marks_of(exam), {"آدم": (D("5.00"), False, "")})
        self.assertEqual(exam.marks.get().entered_by, self.teacher_user)

    def test_a_correction_is_audited_with_the_mark_before_and_after(self):
        exam = self.exam()
        self.mark(exam, self.adam, "12")
        first = AuditLog.objects.get(action="record_marks")
        self.assertEqual((first.event_type, first.before_data, first.after_data["marks"]), ("create", {"marks": {self.adam.code: None}},
                                                                                         {self.adam.code: {"score": "12.00", "absent": False, "note": ""}}))
        exams.record_marks(exam, {self.adam.pk: {"score": "15", "note": "أعيد التصحيح"}, self.basma.pk: {"score": "8"}}, self.teacher_user)
        fix = AuditLog.objects.filter(action="record_marks").latest("pk")
        self.assertEqual(fix.actor, self.teacher_user)
        self.assertEqual(fix.before_data["marks"], {self.adam.code: {"score": "12.00", "absent": False, "note": ""}, self.basma.code: None})
        self.assertEqual(fix.after_data["marks"], {self.adam.code: {"score": "15.00", "absent": False, "note": "أعيد التصحيح"},
                                                   self.basma.code: {"score": "8.00", "absent": False, "note": ""}})
        self.assertEqual((fix.after_data["entered"], fix.after_data["seats"]), (2, 3))
        self.mark(exam, self.adam, "15", note="أعيد التصحيح")  # nothing changed: nothing is audited
        self.assertEqual(AuditLog.objects.filter(action="record_marks").count(), 2)

    def test_a_stale_sheet_is_refused_and_nothing_is_written(self):
        exam = self.exam()
        opened = exams.sheet_version(exam)  # two people open the same empty sheet
        exams.record_marks(exam, {self.adam.pk: {"score": "10"}}, self.owner, version=opened)  # the first saves
        with self.assertRaisesMessage(ValidationError, "حد حفظ قبلك"):
            exams.record_marks(exam, {self.adam.pk: {"score": "1"}, self.basma.pk: {"score": "2"}}, self.teacher_user, version=opened)
        self.assertEqual(self.marks_of(exam), {"آدم": (D("10.00"), False, "")})
        current = exams.sheet_version(exam)
        self.assertNotEqual(current, opened)
        exams.record_marks(exam, {self.basma.pk: {"score": "2"}}, self.teacher_user, version=current)  # a page opened afterwards saves
        # a page that carried no digest is stale as soon as a mark exists, and fresh before any
        with self.assertRaisesMessage(ValidationError, "حد حفظ قبلك"):
            exams.record_marks(exam, {self.gad.pk: {"score": "3"}}, self.owner, version="")
        other = self.exam(title="تاني")
        self.assertEqual(exams.record_marks(other, {self.gad.pk: {"score": "3"}}, self.owner, version="")["entered"], 1)
        # the digest also moves with a note, an absence and the full mark
        base = exams.sheet_version(other)
        self.mark(other, self.gad, "3", note="ملاحظة")
        self.assertNotEqual(exams.sheet_version(other), base)

    def test_publishing_is_the_managers_and_changes_no_mark(self):
        exam = self.exam()
        self.mark(exam, self.adam, "12")
        with self.assertRaises(PermissionDenied):
            exams.set_published(exam, True, self.teacher_user)  # not the teacher (HG-091, open question 2)
        self.assertTrue(exams.set_published(exam, True, self.owner).published)
        self.assertTrue(AuditLog.objects.filter(action="publish_exam", object_id=str(exam.pk)).exists())
        exams.set_published(exam, True, self.owner)  # again: nothing new
        self.assertEqual(AuditLog.objects.filter(action="publish_exam").count(), 1)
        self.assertFalse(exams.set_published(exam, False, self.owner).published)
        self.assertTrue(AuditLog.objects.filter(action="unpublish_exam").exists())
        self.assertEqual(self.marks_of(exam), {"آدم": (D("12.00"), False, "")})


class ReportCardTests(ExamSetup):
    def held(self, student_marks):
        """Eight classes in the last ten days; ``student_marks``: {student: {days ago: presence}}, anyone else is present."""

        for ago in range(9, 1, -1):
            attendance.take(self.a, self.owner, day(ago), {str(s.pk): marks[ago] for s, marks in student_marks.items() if ago in marks})

    def test_percentages_the_average_and_what_is_left_out(self):
        quiz = self.exam(title="كويز 1", held=8, out_of="20")
        monthly = self.exam(title="شهري", held=5, out_of="10", kind="monthly")
        skipped = self.exam(title="كويز غاب فيه", held=4, out_of="30")
        waiting = self.exam(title="لسه ماتصححش", held=3, out_of="40")
        old = self.exam(title="من شهر", held=40, out_of="10")
        self.mark(quiz, self.adam, "18")
        self.mark(monthly, self.adam, "7.5")
        self.mark(skipped, self.adam, absent=True)
        self.mark(old, self.adam, "1")
        self.mark(quiz, self.basma, "2")  # someone else's marks, never in Adam's card
        card = exams.report_card(self.adam, day(10), TODAY, self.owner)
        by_title = {row["exam"].title: row for row in card["rows"]}
        self.assertEqual(list(by_title), ["كويز 1", "شهري", "كويز غاب فيه", "لسه ماتصححش"])  # oldest first; the old one is out of the range
        self.assertEqual((by_title["كويز 1"]["status"], by_title["كويز 1"]["score"], by_title["كويز 1"]["percent"]), ("scored", D("18.00"), D("90.0")))
        self.assertEqual((by_title["شهري"]["status"], by_title["شهري"]["score"], by_title["شهري"]["percent"]), ("scored", D("7.50"), D("75.0")))
        self.assertEqual((by_title["كويز غاب فيه"]["status"], by_title["كويز غاب فيه"]["percent"]), ("absent", None))
        self.assertEqual((by_title["لسه ماتصححش"]["status"], by_title["لسه ماتصححش"]["percent"]), ("pending", None))
        self.assertEqual(card["average"], D("82.5"))  # (90 + 75) / 2 over the two exams he sat; not (18 + 7.5) / (20 + 10) = 85
        self.assertEqual((card["sat"], card["absent_exams"], card["not_entered"]), (2, 1, 1))
        self.assertEqual({row["mark"].student for row in card["rows"] if row["mark"]}, {self.adam})
        self.assertNotIn("rank", " ".join(card))
        wide = exams.report_card(self.adam, day(60), TODAY, self.owner)
        self.assertEqual((wide["sat"], wide["average"]), (3, D("58.3")))  # the old exam is 1 of 10 = 10%: (90 + 75 + 10) / 3 = 58.33
        self.assertEqual(waiting.marks.count(), 0)

    def test_the_average_uses_exact_percentages_and_rounds_once_half_up(self):
        self.assertEqual((exams.percent(D("1"), D("16")), exams.percent(D("1"), D("3")), exams.percent(D("2"), D("3")), exams.percent(None, D("3"))),
                         (D("6.3"), D("33.3"), D("66.7"), None))  # 6.25 goes up, never to the even 6.2
        one, two = self.exam(title="أ", held=4, out_of="3"), self.exam(title="ب", held=3, out_of="3")
        self.mark(one, self.adam, "1")
        self.mark(two, self.adam, "2")
        card = exams.report_card(self.adam, day(10), TODAY, self.owner)
        self.assertEqual([row["percent"] for row in card["rows"]], [D("33.3"), D("66.7")])
        self.assertEqual(card["average"], D("50.0"))  # (33.333... + 66.666...) / 2, not (33.3 + 66.7) / 2 rounded twice
        empty = exams.report_card(self.gad, day(10), TODAY, self.owner)
        self.assertEqual((empty["average"], empty["sat"], empty["not_entered"]), (None, 0, 2))

    def test_attendance_in_the_range(self):
        # 8 classes held: Adam is present 6 times, late once and absent once; Basma is absent three times and excused once.
        self.held({self.adam: {9: "late", 5: "absent"}, self.basma: {9: "absent", 8: "absent", 7: "absent", 6: "excused"}})
        card = exams.report_card(self.adam, day(10), TODAY, self.owner)["attendance"]
        self.assertEqual((card["held"], card["present"], card["late"], card["absent"], card["excused"]), (8, 6, 1, 1, 0))
        self.assertEqual(card["rate"], 88)  # 7 of 8 attended: 87.5, which attendance.rate rounds (to even) to 88
        basma = exams.report_card(self.basma, day(10), TODAY, self.owner)["attendance"]
        self.assertEqual((basma["held"], basma["absent"], basma["excused"], basma["rate"]), (8, 3, 1, 50))  # 4 of 8
        narrow = exams.report_card(self.adam, day(5), TODAY, self.owner)["attendance"]  # days 5..2 ago: 4 classes, the absence among them
        self.assertEqual((narrow["held"], narrow["absent"], narrow["rate"]), (4, 1, 75))
        none = exams.report_card(self.adam, day(1), TODAY, self.owner)["attendance"]
        self.assertEqual((none["held"], none["rate"]), (0, None))

    def test_a_cancelled_class_is_not_held(self):
        attendance.take(self.a, self.owner, day(2), {}, cancelled=True)
        attendance.take(self.a, self.owner, day(3), {str(self.adam.pk): "absent"})
        card = exams.report_card(self.adam, day(10), TODAY, self.owner)["attendance"]
        self.assertEqual((card["held"], card["absent"], card["rate"]), (1, 1, 0))

    def test_a_reversed_range_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "قبل تاريخ النهاية"):
            exams.report_card(self.adam, TODAY, day(5), self.owner)


class ExamVisibilityTests(ExamSetup):
    def setUp(self):
        super().setUp()
        self.in_a = self.exam(self.a, "امتحان أ", held=4, out_of="20")
        self.in_b = self.exam(self.b, "امتحان ب", held=3, out_of="10")
        exams.record_marks(self.in_a, {self.basma.pk: {"score": "15"}}, self.owner)
        exams.record_marks(self.in_b, {self.basma.pk: {"score": "9"}}, self.owner)

    def titles(self, user):
        return [row["exam"].title for row in exams.student_marks(self.basma, user)]

    def test_a_manager_sees_both_groups_and_a_teacher_only_their_own(self):
        # Basma is in group A and group B; the teacher of A sees A's exam and nothing of B's, not even in the report card.
        self.assertEqual(self.titles(self.owner), ["امتحان ب", "امتحان أ"])
        self.assertEqual(self.titles(self.teacher_user), ["امتحان أ"])
        self.assertEqual(self.titles(self.other_user), ["امتحان ب"])
        card = exams.report_card(self.basma, day(10), TODAY, self.teacher_user)
        self.assertEqual([row["exam"].title for row in card["rows"]], ["امتحان أ"])
        self.assertEqual(card["average"], D("75.0"))  # 15 of 20 only; B's 90% never enters a teacher of A's card
        self.assertEqual(exams.report_card(self.basma, day(10), TODAY, self.owner)["average"], D("82.5"))  # (75 + 90) / 2

    def test_attendance_in_a_card_is_the_viewers_groups_only(self):
        attendance.take(self.a, self.owner, day(2), {str(self.basma.pk): "absent"})
        attendance.take(self.b, self.owner, day(2), {})
        self.assertEqual(exams.report_card(self.basma, day(5), TODAY, self.teacher_user)["attendance"]["held"], 1)
        self.assertEqual(exams.report_card(self.basma, day(5), TODAY, self.owner)["attendance"]["held"], 2)

    def test_a_cashier_and_a_teacher_of_none_of_the_students_groups_see_nothing(self):
        for user in (self.cashier, self.other_user):
            with self.subTest(user=user.username):
                if user is self.other_user:
                    self.assertEqual(self.titles(user), ["امتحان ب"])  # B's teacher does see B's exam of Basma...
                    self.assertEqual(exams.student_marks(self.adam, user), [])  # ...but none of Adam, who is in A only
                    with self.assertRaises(PermissionDenied):
                        exams.report_card(self.adam, day(10), TODAY, user)
                else:
                    self.assertEqual(exams.student_marks(self.basma, user), [])
                    with self.assertRaises(PermissionDenied):
                        exams.report_card(self.basma, day(10), TODAY, user)
        self.assertFalse(exams.can_see_student(self.cashier, self.basma))
        self.assertEqual(exams.group_exams(self.a, self.cashier), [])
        self.assertEqual(exams.group_exams(self.a, self.other_user), [])
        self.assertEqual([e.title for e in exams.group_exams(self.a, self.teacher_user)], ["امتحان أ"])

    def test_the_group_list_counts_entered_marks_out_of_the_roster(self):
        exams.record_marks(self.in_a, {self.adam.pk: {"absent": True}, self.gad.pk: {"note": "لسه"}}, self.owner)
        row = exams.group_exams(self.a, self.owner)[0]
        self.assertEqual((row.title, row.entered, row.seats), ("امتحان أ", 2, 3))  # Basma's score and Adam's absence; Gad's note is not a mark

    def test_a_group_of_another_entity_is_not_seen(self):
        from entities.current import working_in
        from entities.models import Entity

        branch = Entity.objects.create(code="E-EXAM", name_ar="فرع", activity_slug="education", sub_activity_slug="tutoring_center")
        with working_in(branch):
            self.assertEqual(list(exams.scoped_exams()), [])
            self.assertFalse(exams.groups_for(self.owner).exists())
            self.assertEqual(exams.student_marks(self.basma, self.owner), [])
            self.assertEqual(exams.group_exams(self.a, self.owner), [])


class NoMoneyNoMessageTests(ExamSetup):
    def test_the_whole_feature_moves_no_money_and_writes_no_message(self):
        counts = lambda: (SalesInvoice.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count(), OutboxMessage.objects.count())  # noqa: E731
        before = counts()
        exam = self.exam(out_of="20")
        exams.record_marks(exam, {self.adam.pk: {"score": "18", "note": "ممتاز"}, self.basma.pk: {"absent": True}}, self.owner)
        exams.record_marks(exam, {self.adam.pk: {"score": "17"}}, self.teacher_user)
        exams.set_published(exam, True, self.owner)
        exams.set_published(exam, False, self.owner)
        exams.student_marks(self.adam, self.owner)
        exams.report_card(self.adam, day(10), TODAY, self.owner)
        exams.save_exam(self.a, {"title": "x", "kind": "final", "held_on": day(3), "out_of": "20"}, self.owner, exam)
        self.assertEqual(counts(), before)
        # no mark value is in any outbox row (there are none), and the module does not even reach for messaging, posting or the books
        self.assertFalse(OutboxMessage.objects.exists())
        source = (pathlib.Path(settings.BASE_DIR) / "education/exams.py").read_text(encoding="utf-8")
        imports = " ".join(line for line in source.splitlines() if line.startswith(("import ", "from ")))
        for word in ("messaging", "sales", "ledger", "cashboxes", "expenses", "billing", "pay", "urllib"):
            self.assertNotIn(word, imports)
        for word in ("wa.me", "post_sales_invoice", "record_expense", "OutboxMessage"):
            self.assertNotIn(word, source)


class MigrationTests(TransactionTestCase):
    """0008 adds the two tables after 0007 and leaves the rows already there as they were."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0007_and_adds_two_tables_beside_existing_rows(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("education", "0008_exams_and_marks")]
        self.assertIn(("education", "0007_language_levels"), node.dependencies)
        executor.migrate([("education", "0007_language_levels")])
        old = executor.loader.project_state([n for n in executor.loader.graph.leaf_nodes() if n[0] != "education"] + [("education", "0007_language_levels")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_exam")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="C-MIG", name="ولي أمر")
        item = old.get_model("master_data", "Item").objects.create(item_code="CRS-MIG", item_name="كورس", is_stock_tracked=False)
        course = old.get_model("education", "Course").objects.create(name="رياضيات", item=item)
        group = old.get_model("education", "StudyGroup").objects.create(code="GR-MIG", course=course, name="مجموعة")
        student = old.get_model("education", "Student").objects.create(code="ST-MIG", name="آدم", payer=customer, created_by=user)
        session = old.get_model("education", "ClassSession").objects.create(group=group, date=day(1), taken_by=user)
        old.get_model("education", "Attendance").objects.create(session=session, student=student, presence="late")
        self.assertNotIn("education_exam", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("education", "0008_exams_and_marks")])
        tables = connection.introspection.table_names()
        self.assertIn("education_exam", tables)
        self.assertIn("education_exammark", tables)
        from .models import Attendance, Student, StudyGroup

        self.assertEqual(list(Student.objects.values_list("code", "name")), [("ST-MIG", "آدم")])
        self.assertEqual(list(StudyGroup.objects.values_list("code", flat=True)), ["GR-MIG"])
        self.assertEqual(list(Attendance.objects.values_list("presence", flat=True)), ["late"])
        self.assertEqual((Exam.objects.count(), ExamMark.objects.count()), (0, 0))  # nothing is seeded
