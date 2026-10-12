"""EDU-006: a language centre's levels — the placement test, and moving a group's students up a level."""

from datetime import date, time, timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import levels, services
from .models import Enrollment, PlacementTest
from .tests import CENTER, person

TODAY = timezone.localdate()


def first_of_next_month():
    return (TODAY.replace(day=28) + timedelta(days=4)).replace(day=1)


class LevelSetup(TestCase):
    sub = "languages"

    def setUp(self):
        prepared_client("education", self.sub, CENTER)
        self.owner = person(RoleCode.OWNER, "lv_owner")
        self.english = services.save_course({"name": "إنجليزي", "fee": "600", "basis": "monthly"}, self.owner)
        self.one = self.group("إنجليزي 1 — السبت", "1", days="sat,tue")
        self.two = self.group("إنجليزي 2 — الأحد", "2", days="sun,wed")
        self.two_full = self.group("إنجليزي 2 — الاتنين", "2", days="mon,thu", capacity="1")
        self.sara = services.save_student({"name": "سارة علي", "payer_name": "علي محمود", "payer_phone": "01001234501"}, self.owner)
        self.omar = services.save_student({"name": "عمر حسن", "payer_name": "حسن سيد", "payer_phone": "01001234502"}, self.owner)

    def group(self, name, level, days, capacity="0", start=time(17)):
        return services.save_group({"course": self.english, "name": name, "days": days, "start_time": start, "duration_minutes": "90",
                                    "capacity": capacity, "level": level}, self.owner)


class PlacementTests(LevelSetup):
    def test_a_placement_points_to_the_groups_at_its_level_with_a_seat(self):
        self.assertEqual((self.one.level, self.two.level), (1, 2))
        services.enroll(self.omar, self.two_full, self.owner)  # the Monday level-2 group is now full
        test = levels.record_placement(self.sara, {"course": self.english, "level": "٢", "score": "34", "out_of": "50", "notes": "محادثة كويسة"}, self.owner)
        self.assertEqual((test.level, test.score, test.out_of, test.taken_on), (2, D("34"), D("50"), TODAY))
        self.assertEqual([group.pk for group in levels.groups_at(self.english, test.level, self.sara)], [self.two.pk])
        self.assertTrue(AuditLog.objects.filter(action="placement_test").exists())

    def test_a_wrong_level_mark_or_day_is_refused(self):
        for data in ({"level": "0"}, {"level": "21"}, {"level": "2.5"}, {"level": "2", "score": "60", "out_of": "50"},
                     {"level": "2", "taken_on": (TODAY + timedelta(days=1)).isoformat()}, {"level": "2", "taken_on": "امبارح"}):
            with self.assertRaises(ValidationError, msg=data):
                levels.record_placement(self.sara, {"course": self.english, **data}, self.owner)
        with self.assertRaises(ValidationError):
            levels.record_placement(self.sara, {"level": "2"}, self.owner)
        self.assertFalse(PlacementTest.objects.exists())

    def test_the_level_must_be_a_number_on_the_group(self):
        with self.assertRaisesMessage(ValidationError, "المستوى"):
            self.group("غلط", "اتنين", days="fri")
        services.save_group({"course": self.english, "name": self.one.name, "days": "sat,tue", "start_time": time(17), "duration_minutes": "90",
                             "level": ""}, self.owner, self.one)
        self.one.refresh_from_db()
        self.assertIsNone(self.one.level)


class MoveUpTests(LevelSetup):
    def setUp(self):
        super().setUp()
        start = TODAY.replace(day=1) - timedelta(days=60)
        self.first = services.enroll(self.sara, self.one, self.owner, start_date=start, discount_percent="10")
        services.enroll(self.omar, self.one, self.owner, start_date=start)
        self.day = first_of_next_month()

    def test_the_class_moves_up_with_its_discount_and_no_month_billed_twice(self):
        from education import billing

        moved = levels.move_up(self.one, [self.sara, self.omar], self.two, self.owner, on_date=self.day)
        self.assertEqual(len(moved), 2)
        self.first.refresh_from_db()
        self.assertEqual((self.first.status, self.first.end_date), ("stopped", self.day - timedelta(days=1)))
        new = Enrollment.objects.get(student=self.sara, group=self.two)
        self.assertEqual((new.start_date, new.discount_percent, new.status), (self.day, D("10.00"), "active"))
        lines = billing.due_lines(self.day)
        self.assertEqual(sorted((line["enrollment"].group_id, line["enrollment"].student_id) for line in lines),
                         sorted([(self.two.pk, self.sara.pk), (self.two.pk, self.omar.pk)]))  # next month: only the new level
        this_month = billing.due_lines(TODAY.replace(day=1))
        self.assertTrue(all(line["enrollment"].group_id == self.one.pk for line in this_month))  # this month: only the old one
        self.assertTrue(AuditLog.objects.filter(action="move_up_level").exists())

    def test_a_move_that_cannot_happen_moves_nobody(self):
        with self.assertRaisesMessage(ValidationError, "أول الشهر"):
            levels.move_up(self.one, [self.sara], self.two, self.owner, on_date=self.day + timedelta(days=3))
        with self.assertRaisesMessage(ValidationError, "مكان بس"):
            levels.move_up(self.one, [self.sara, self.omar], self.two_full, self.owner, on_date=self.day)
        with self.assertRaisesMessage(ValidationError, "مش في مستوى أعلى"):
            levels.move_up(self.two, [self.sara], self.one, self.owner, on_date=self.day)
        stranger = services.save_student({"name": "ليلى", "payer_name": "أحمد", "payer_phone": "01001234503"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مش في المجموعة"):
            levels.move_up(self.one, [self.sara, stranger], self.two, self.owner, on_date=self.day)
        with self.assertRaises(ValidationError):
            levels.move_up(self.one, [], self.two, self.owner, on_date=self.day)
        self.assertEqual(Enrollment.objects.filter(group=self.one, status="active").count(), 2)  # all or nothing
        self.assertFalse(Enrollment.objects.filter(group=self.two).exists())

    def test_moving_up_needs_managing_students(self):
        teacher = person(RoleCode.CASHIER, "lv_cashier")
        with self.assertRaises(PermissionDenied):
            levels.move_up(self.one, [self.sara], self.two, teacher, on_date=self.day)
        with self.assertRaises(PermissionDenied):
            levels.record_placement(self.sara, {"course": self.english, "level": "1"}, teacher)


class ScreenTests(LevelSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_students_file_records_a_placement_and_shows_the_groups(self):
        url = reverse("education:student", args=[self.sara.pk])
        self.assertContains(self.client.get(url), "data-placement-form")
        self.client.post(url, {"action": "placement", "course": self.english.pk, "level": "2", "score": "40", "out_of": "50"})
        page = self.client.get(url)
        self.assertContains(page, "data-suggested-groups")
        self.assertContains(page, self.two.name)

    def test_the_group_page_moves_the_ticked_students_up(self):
        start = TODAY.replace(day=1) - timedelta(days=60)
        services.enroll(self.sara, self.one, self.owner, start_date=start)
        services.enroll(self.omar, self.one, self.owner, start_date=start)
        url = reverse("education:group", args=[self.one.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-move-up")
        self.assertContains(page, first_of_next_month().isoformat())
        self.client.post(url, {"action": "move_up", "students": [self.sara.pk], "target": self.two.pk, "on_date": first_of_next_month().isoformat()})
        self.assertEqual(list(Enrollment.objects.filter(group=self.two).values_list("student", flat=True)), [self.sara.pk])
        self.assertTrue(Enrollment.objects.filter(group=self.one, student=self.omar, status="active").exists())  # not ticked: stays


class NoLevelsTests(LevelSetup):
    sub = "tutoring_center"

    def test_a_tutoring_centre_has_no_level_screens(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("education:student", args=[self.sara.pk])), "data-placement")
        self.assertNotContains(self.client.get(reverse("education:group", args=[self.one.pk])), "data-move-up")
