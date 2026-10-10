"""EDU-001: students and who pays for them, courses, study groups and enrolments."""

from datetime import time, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_seeded_role, make_user, make_user_profile
from master_data.models import Customer
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from reports.tests_shell import sidebar_links

from . import services
from .models import Enrollment, Student, StudyGroup

CENTER = "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians"
TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class EducationSetup(TestCase):
    def setUp(self):
        prepared_client("education", "tutoring_center", CENTER)
        self.owner = person(RoleCode.OWNER, "edu_owner")
        from staff.services import save_employee

        self.teacher = save_employee({"name": "أ. محمود"}, self.owner)
        self.maths = services.save_course({"name": "رياضيات تالتة إعدادي", "fee": "400", "basis": "monthly"}, self.owner)

    def group(self, name="مجموعة السبت", days="sat,tue", start=time(16), teacher=None, room="قاعة 1", capacity="0", fee=""):
        return services.save_group({"course": self.maths, "name": name, "teacher": teacher if teacher is not None else self.teacher, "room": room,
                                    "days": days, "start_time": start, "duration_minutes": "90", "capacity": capacity, "fee": fee}, self.owner)

    def student(self, name="يوسف محمد", **extra):
        return services.save_student({"name": name, "payer_name": "محمد السيد", "payer_phone": "01001234501", **extra}, self.owner)


class StudentTests(EducationSetup):
    def test_a_parent_holds_the_account_and_a_second_child_shares_it(self):
        first = self.student(relation="الأب", stage="تالتة إعدادي")
        self.assertEqual((first.code, first.payer.name, first.payer.phone), ("ST-00001", "محمد السيد", "01001234501"))
        # The same phone finds the same parent: brothers and sisters share one account and one statement.
        second = self.student("مريم محمد")
        self.assertEqual(second.payer, first.payer)
        self.assertEqual(Customer.objects.filter(phone="01001234501").count(), 1)
        self.assertTrue(AuditLog.objects.filter(module="education", action="create_student").exists())

    def test_an_adult_learner_pays_for_themselves_or_a_payer_is_required(self):
        adult = services.save_student({"name": "هاني سمير", "phone": "01201112223", "self_pays": "1"}, self.owner)
        self.assertEqual((adult.payer.name, adult.payer.phone), ("هاني سمير", "01201112223"))
        with self.assertRaisesMessage(ValidationError, "مين اللي بيدفع"):
            services.save_student({"name": "من غير ولي أمر"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "اكتب الاسم"):
            services.save_student({"name": " ", "self_pays": "1"}, self.owner)
        existing = make_customer(customer_code="C-PARENT", name="سارة محمود")
        child = services.save_student({"name": "ليلى", "payer": existing}, self.owner)
        self.assertEqual(child.payer, existing)


class CourseAndGroupTests(EducationSetup):
    def test_a_course_is_billed_through_its_own_service_and_keeps_it_in_step(self):
        item = self.maths.item
        self.assertEqual((item.item_code, item.is_stock_tracked, item.default_sale_price), ("CRS-00001", False, D("400.00")))
        services.save_course({"name": "رياضيات تالتة", "fee": "450", "basis": "monthly", "active_present": "1", "active": "1"}, self.owner, self.maths)
        item.refresh_from_db()
        self.assertEqual((item.item_name, item.default_sale_price), ("رياضيات تالتة", D("450.00")))
        with self.assertRaisesMessage(ValidationError, "صفر أو أكبر"):
            services.save_course({"name": "x", "fee": "-5"}, self.owner)

    def test_a_group_keeps_its_schedule_and_its_own_fee_or_the_courses(self):
        group = self.group()
        self.assertEqual((group.code, group.weekdays, group.effective_fee), ("GR-00001", ["sat", "tue"], D("400.00")))
        self.assertEqual(services.schedule_text(group), "السبت، التلات · 16:00")
        cheaper = self.group("مجموعة الأحد", days="sun", fee="350")
        self.assertEqual(cheaper.effective_fee, D("350.00"))

    def test_a_teacher_or_a_room_is_never_in_two_groups_at_once(self):
        self.group()
        with self.assertRaisesMessage(ValidationError, "أ. محمود"):
            self.group("مجموعة تانية", days="tue", start=time(17), room="قاعة 5")  # overlaps 16:00-17:30 on Tuesday
        with self.assertRaisesMessage(ValidationError, "القاعة قاعة 1"):
            self.group("مجموعة تالتة", days="sat", start=time(16, 30), teacher=False)
        # Back to back, another day, or another room and teacher is fine.
        self.group("بعدها على طول", days="sat", start=time(17, 30))
        self.group("يوم تاني", days="mon", start=time(16))

    def test_bad_group_input_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "اختار الكورس"):
            services.save_group({"name": "x"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مدة الحصة"):
            services.save_group({"course": self.maths, "name": "x", "duration_minutes": "5"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "بعد البداية"):
            services.save_group({"course": self.maths, "name": "x", "starts_on": "2026-10-10", "ends_on": "2026-09-01"}, self.owner)


class EnrollmentTests(EducationSetup):
    def test_enrol_with_a_sibling_discount_and_stop(self):
        group, student = self.group(), self.student()
        enrollment = services.enroll(student, group, self.owner, discount_percent="10")
        self.assertEqual(services.monthly_fee(enrollment), D("360.00"))
        with self.assertRaisesMessage(ValidationError, "مسجّل في المجموعة دي بالفعل"):
            services.enroll(student, group, self.owner)
        services.stop(enrollment, self.owner)
        enrollment.refresh_from_db()
        self.assertEqual((enrollment.status, enrollment.end_date), ("stopped", TODAY))
        with self.assertRaisesMessage(ValidationError, "متوقف بالفعل"):
            services.stop(enrollment, self.owner)
        services.enroll(student, group, self.owner)  # coming back later is a new enrolment
        self.assertEqual(Enrollment.objects.filter(student=student, group=group).count(), 2)

    def test_a_full_group_a_closed_group_and_a_bad_discount_refuse(self):
        group = self.group(capacity="1")
        services.enroll(self.student(), group, self.owner)
        with self.assertRaisesMessage(ValidationError, "المجموعة كاملة (1 طالب)"):
            services.enroll(self.student("مريم"), group, self.owner)
        with self.assertRaisesMessage(ValidationError, "الخصم"):
            services.enroll(self.student("عمر"), self.group("أخرى", days="fri"), self.owner, discount_percent="120")
        StudyGroup.objects.filter(pk=group.pk).update(active=False)
        with self.assertRaisesMessage(ValidationError, "مقفولة"):
            services.enroll(self.student("سلمى"), group, self.owner)

    def test_a_stop_date_before_the_start_is_refused(self):
        enrollment = services.enroll(self.student(), self.group(), self.owner, start_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "بعد بداية التسجيل"):
            services.stop(enrollment, self.owner, end_date=TODAY - timedelta(days=1))


class ScreenTests(EducationSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_menu_leads_with_students_and_the_flow_works_from_the_screens(self):
        links = sidebar_links(self.client.get(reverse("dashboard_snapshot")))
        self.assertIn(reverse("education:students"), links)
        self.assertLess(links.index(reverse("education:students")), links.index(reverse("appointments:agenda")))
        response = self.client.post(reverse("education:student_new"), {"name": "يوسف محمد", "payer_name": "محمد السيد", "payer_phone": "01001234501",
                                                                       "relation": "الأب", "lang": "ar"})
        student = Student.objects.get()
        self.assertRedirects(response, reverse("education:student", args=[student.pk]) + "?lang=ar", fetch_redirect_response=False)
        response = self.client.post(reverse("education:group_new"), {"course": self.maths.pk, "name": "مجموعة السبت", "teacher": self.teacher.pk,
                                                                     "days": ["sat", "tue"], "start_time": "16:00", "duration_minutes": "90",
                                                                     "capacity": "12", "lang": "ar"})
        group = StudyGroup.objects.get()
        self.assertEqual(group.days, "sat,tue")
        self.client.post(reverse("education:student", args=[student.pk]), {"action": "enroll", "group": group.pk, "discount_percent": "0", "lang": "ar"})
        page = self.client.get(reverse("education:group", args=[group.pk]))
        self.assertContains(page, f'data-roster-row="{student.pk}"')
        self.assertContains(page, "1 / 12")
        self.assertContains(self.client.get(reverse("education:students") + "?q=يوسف"), f'data-student="{student.pk}"')
        self.assertContains(self.client.get(reverse("education:groups")), "السبت، التلات · 16:00")
        self.assertContains(self.client.get(reverse("education:courses")), "CRS-00001")
        # English too.
        self.assertContains(self.client.get(reverse("education:group", args=[group.pk]) + "?lang=en"), "Students in this group")

    def test_a_refused_action_shows_why_on_the_page(self):
        group = self.group(capacity="1")
        services.enroll(self.student(), group, self.owner)
        other = self.student("مريم")
        page = self.client.post(reverse("education:student", args=[other.pk]), {"action": "enroll", "group": group.pk, "lang": "ar"})
        self.assertContains(page, "المجموعة كاملة")

    def test_who_may_see_and_change(self):
        cashier = person(RoleCode.CASHIER, "edu_cashier")
        self.client.force_login(cashier)
        student = self.student()
        listing = self.client.get(reverse("education:students"))
        if listing.status_code == 200:  # a cashier who sees customers sees students, but cannot change them
            self.assertNotContains(listing, "data-new-student")
            self.assertEqual(self.client.get(reverse("education:student_new")).status_code, 403)
            self.assertEqual(self.client.post(reverse("education:student", args=[student.pk]), {"action": "edit", "name": "x"}).status_code, 403)
        else:
            self.assertEqual(listing.status_code, 403)

    def test_not_on_other_activities(self):
        from settings_core.models import ClientProfile

        profile = ClientProfile.get_active()
        profile.activity_slug = "commercial"
        profile.save()
        self.assertEqual(self.client.get(reverse("education:students")).status_code, 404)
        self.assertNotIn(reverse("education:students"), sidebar_links(self.client.get(reverse("dashboard_snapshot"))))


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class DemoDataTests(TestCase):
    def test_an_education_demo_opens_with_groups_and_enrolled_children(self):
        from django.core.management import call_command

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
        self.assertEqual(StudyGroup.objects.count(), 3)
        self.assertEqual(Student.objects.count(), 7)
        self.assertEqual(Student.objects.values("payer").distinct().count(), 5)  # brothers and sisters share a parent
        self.assertEqual(Enrollment.objects.filter(discount_percent=D("10")).count(), 4)
        self.assertTrue(self.client.login(username="owner", password="Demo-pass-1"))
        self.assertContains(self.client.get(reverse("education:groups")), "مجموعة السبت والتلات")
