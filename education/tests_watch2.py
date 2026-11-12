"""Watch round 2 fixes for education: ED-01 (letters are no phone number on a student or a parent), ED-02 (the scan screen
works in the demo at any hour, and an empty submit says what to do), ED-07 (the card sheet's title) and ED-10 (the scan answers)."""

from datetime import time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer
from sales.pos_customers import quick_add_customer

from . import scan, services
from .models import Attendance, Enrollment, Student, StudyGroup
from .tests import EducationSetup
from .tests_scan import TODAY, ScanSetup, at

PHONE_AR = "اكتب رقم تليفون بأرقام بس (ينفع + في الأول ومسافات وشرطات وأقواس)."
PHONE_EN = "Type a phone number in digits only (a leading +, spaces, dashes and brackets are fine)."


class StudentPhoneTests(EducationSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def post_new(self, phone="", payer_phone="01001234501", lang="ar", **extra):
        return self.client.post(reverse("education:student_new"), {"name": "يوسف محمد", "phone": phone, "payer_name": "محمد السيد", "payer_phone": payer_phone,
                                                                  "lang": lang, **extra})

    def test_letters_in_the_students_phone_are_refused_in_both_languages(self):
        self.assertContains(self.post_new(phone="abc123"), PHONE_AR)
        self.assertContains(self.post_new(phone="abc123", lang="en"), PHONE_EN)
        self.assertFalse(Student.objects.exists())
        self.assertFalse(Customer.objects.filter(name="محمد السيد").exists())

    def test_letters_in_the_parents_phone_are_refused_and_no_parent_is_made(self):
        self.assertContains(self.post_new(payer_phone="xyz"), PHONE_AR)
        self.assertContains(self.post_new(payer_phone="0100123abc", lang="en"), PHONE_EN)
        self.assertFalse(Student.objects.exists())
        self.assertFalse(Customer.objects.filter(name="محمد السيد").exists())

    def test_a_student_who_pays_for_themselves_is_checked_once_with_the_same_message(self):
        answer = self.client.post(reverse("education:student_new"), {"name": "هاني سمير", "phone": "call me", "self_pays": "1", "lang": "en"})
        self.assertContains(answer, PHONE_EN)
        self.assertFalse(Student.objects.exists())

    def test_ordinary_ways_of_writing_a_number_still_save(self):
        for index, phone in enumerate(["01012345678", "+20 100 123-4567", "(02) 2345 6789", "٠١٠١٢٣٤٥٦٧٨", ""]):
            answer = self.client.post(reverse("education:student_new"), {"name": f"طالب {index}", "phone": phone, "payer_name": f"ولي {index}",
                                                                         "payer_phone": phone, "lang": "ar"})
            self.assertEqual(answer.status_code, 302, phone)
            self.assertEqual(Student.objects.get(name=f"طالب {index}").phone, phone)

    def test_an_old_student_with_an_odd_phone_still_saves_until_the_phone_is_touched(self):
        old = self.student("طالب قديم")
        Student.objects.filter(pk=old.pk).update(phone="abc123")
        old.refresh_from_db()
        url = reverse("education:student", args=[old.pk])
        self.assertEqual(self.client.post(url, {"action": "edit", "name": "طالب قديم معدّل", "phone": "abc123", "lang": "ar"}).status_code, 302)
        old.refresh_from_db()
        self.assertEqual((old.name, old.phone), ("طالب قديم معدّل", "abc123"))
        self.assertContains(self.client.post(url, {"action": "edit", "name": "طالب قديم معدّل", "phone": "abc1234", "lang": "ar"}), PHONE_AR)
        old.refresh_from_db()
        self.assertEqual(old.phone, "abc123")


class TillQuickAddTests(EducationSetup):
    def test_the_quick_add_refuses_letters_in_a_phone_but_takes_a_plain_one(self):
        with self.assertRaises(ValidationError) as caught:
            quick_add_customer("عميل", "01012abc345678", self.owner)
        self.assertEqual(caught.exception.messages, ["phone"])
        self.assertFalse(Customer.objects.filter(name="عميل").exists())
        customer, created = quick_add_customer("عميل", "+20 101 234-5678", self.owner)
        self.assertTrue(created)
        self.assertEqual(customer.phone, "+20 101 234-5678")
        with self.assertRaises(ValidationError):  # the old rule stays: too few digits
            quick_add_customer("عميل تاني", "123", self.owner)


# ---- ED-02, ED-07, ED-10: the scan screen in the demo, the card sheet's title and the scan answers' wording ----

class DemoScanWorksAtAnyHourTests(TestCase):
    """The training centre's three classes meet at 16:00 to 18:00 on certain days, so the scan screen only ever answered «no class
    now». The demo also has a study hall open every day in three 8-hour periods; a hall student has a class to scan into at any hour."""

    @classmethod
    def setUpTestData(cls):
        from settings_core import setup_catalog
        from settings_core.management.commands.prepare_demo import Command as Prepare
        from settings_core.models import ClientProfile
        from settings_core.setup_services import complete_setup

        call_command("bootstrap_client", display_name="سنتر", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_setup(ClientProfile.get_active(), "education", "tutoring_center", ",".join(setup_catalog.default_modules("education")))
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        prepare = Prepare()
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._extras()
        cls.owner = get_user_model().objects.get(username="owner")
        cls.hall = list(StudyGroup.objects.filter(name__startswith="قاعة المذاكرة").order_by("start_time"))

    def test_the_hall_is_three_free_periods_that_tile_the_day(self):
        self.assertEqual([(group.start_time, group.duration_minutes, group.days, group.effective_fee) for group in self.hall],
                         [(time(h), 480, "sat,sun,mon,tue,wed,thu,fri", 0) for h in (0, 8, 16)])
        self.assertEqual([group.teacher_id for group in self.hall], [None, None, None])

    def test_a_hall_student_has_a_class_in_the_window_at_every_half_hour_of_the_day(self):
        today = timezone.localdate()
        student = Student.objects.get(code="ST-00001")
        groups = scan._today_groups(student, today)
        self.assertTrue(set(self.hall) <= set(groups))
        for hour in range(24):
            for minute in (0, 30):
                now = at(today, hour, minute)
                self.assertTrue([group for group in groups if scan.in_window(group, now)], f"no class at {hour:02d}:{minute:02d}")

    def test_a_scan_at_nine_in_the_morning_and_at_night_marks_the_student(self):
        today = timezone.localdate()
        for code, hour, minute in (("ST-00001", 9, 5), ("ST-00002", 3, 10), ("ST-00003", 23, 50)):
            result = scan.mark(code, self.owner, at(today, hour, minute))
            self.assertEqual(result.status, "ok", (code, hour, result.message))
            self.assertTrue(Attendance.objects.filter(student=Student.objects.get(code=code), session__date=today).exists())


class ScanWordingTests(ScanSetup):
    """ED-02: an empty submit says what to do."""

    def test_the_screen_carries_the_empty_submit_message_in_both_languages_and_the_script_shows_it(self):
        self.assertContains(self.client.get(reverse("education:scan")), 'data-t-empty="امسح الكارت أو اكتب الكود"')
        self.assertContains(self.client.get(reverse("education:scan") + "?lang=en"), 'data-t-empty="Scan the card or type the code"')
        script = (Path(settings.BASE_DIR) / "static" / "hesba" / "js" / "edu_scan.js").read_text()
        self.assertIn("render({ status: 'empty', message: T('empty') })", script)
        self.assertEqual(scan.mark("  ", self.owner).message, "امسح الكارت أو اكتب الكود.")
