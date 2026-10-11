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
from .models import Attendance, Course, Enrollment, Student, StudyGroup

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
        from .models import Attendance, ClassSession

        self.assertGreaterEqual(ClassSession.objects.count(), 8)  # four weeks of classes
        self.assertTrue(Attendance.objects.filter(presence="absent").exists())
        self.assertTrue(self.client.login(username="owner", password="Demo-pass-1"))
        self.assertContains(self.client.get(reverse("education:groups")), "مجموعة السبت والتلات")


class AttendanceTests(EducationSetup):
    def setUp(self):
        super().setUp()
        from . import attendance

        self.register = attendance
        self.class_day = TODAY - timedelta(days=(TODAY.weekday() - 5) % 7)  # the latest Saturday, today included
        self.saturday = self.group(days="sat")
        self.yousef, self.mariam = self.student(), self.student("مريم محمد")
        services.enroll(self.yousef, self.saturday, self.owner, start_date=self.class_day - timedelta(days=30))
        services.enroll(self.mariam, self.saturday, self.owner, start_date=self.class_day - timedelta(days=30))

    def test_take_change_and_read_attendance(self):
        session = self.register.take(self.saturday, self.owner, self.class_day, {str(self.mariam.pk): "absent", f"note_{self.mariam.pk}": "تعبانة"})
        marks = dict(session.marks.values_list("student__name", "presence"))
        self.assertEqual(marks, {"يوسف محمد": "present", "مريم محمد": "absent"})  # unmarked counts as present
        self.assertEqual(session.marks.get(student=self.mariam).note, "تعبانة")
        # Taking it again for the same day corrects it, never doubles it.
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.mariam.pk): "late"})
        self.assertEqual(self.saturday.sessions.count(), 1)
        self.assertEqual(self.register.student_summary(self.mariam, today=self.class_day)["rate"], 100)  # late still attended
        self.assertEqual(self.register.group_rates(self.saturday, today=self.class_day), {self.yousef.pk: 100, self.mariam.pk: 100})
        self.assertTrue(AuditLog.objects.filter(action="take_attendance").exists())

    def test_rates_count_held_classes_only(self):
        earlier = self.class_day - timedelta(days=7)
        self.register.take(self.saturday, self.owner, earlier, {str(self.mariam.pk): "absent"})
        self.register.take(self.saturday, self.owner, self.class_day, {})
        self.assertEqual(self.register.student_summary(self.mariam, today=self.class_day)["rate"], 50)
        self.register.take(self.saturday, self.owner, earlier, {}, cancelled=True)  # the class turned out cancelled
        summary = self.register.student_summary(self.mariam, today=self.class_day)
        self.assertEqual((summary["rate"], summary["absences"]), (100, []))

    def test_the_roster_is_who_was_enrolled_that_day(self):
        late_joiner = self.student("عمر خالد")
        services.enroll(late_joiner, self.saturday, self.owner, start_date=self.class_day)
        week_before = self.class_day - timedelta(days=7)
        session = self.register.take(self.saturday, self.owner, week_before, {})
        self.assertNotIn(late_joiner.pk, session.marks.values_list("student", flat=True))
        stopped = Enrollment.objects.get(student=self.yousef, group=self.saturday)
        services.stop(stopped, self.owner, end_date=week_before)
        self.assertNotIn(self.yousef.pk, self.register.take(self.saturday, self.owner, self.class_day, {}).marks.values_list("student", flat=True))

    def test_no_future_days_and_only_ones_own_groups_for_a_teacher(self):
        from django.core.exceptions import PermissionDenied

        with self.assertRaisesMessage(ValidationError, "لسه مجاش"):
            self.register.take(self.saturday, self.owner, TODAY + timedelta(days=1), {})
        teacher_user = person(RoleCode.CASHIER, "edu_teacher")
        self.teacher.user = teacher_user
        self.teacher.save()
        self.assertTrue(self.register.can_take(teacher_user, self.saturday))
        from staff.services import save_employee

        other = self.group("مجموعة تانية", days="sun", teacher=save_employee({"name": "أ. نادية"}, self.owner))
        self.assertFalse(self.register.can_take(teacher_user, other))
        with self.assertRaises(PermissionDenied):
            self.register.take(other, teacher_user, self.class_day, {})

    def test_todays_classes_and_the_attendance_screen(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("education:today") + f"?date={self.class_day.isoformat()}")
        self.assertContains(page, f'data-class="{self.saturday.pk}"')
        self.assertContains(page, f'data-take="{self.saturday.pk}"')
        url = reverse("education:attendance", args=[self.saturday.pk])
        form = self.client.get(url + f"?date={self.class_day.isoformat()}")
        self.assertContains(form, f'data-mark="{self.yousef.pk}"')
        from master_data.models import Customer as C

        C.objects.filter(pk=self.mariam.payer_id).update(phone="01001234501")
        response = self.client.post(url, {"date": self.class_day.isoformat(), f"p_{self.yousef.pk}": "present", f"p_{self.mariam.pk}": "absent",
                                          "topic": "المعادلات", "lang": "ar"})
        self.assertEqual(response.status_code, 302)
        done = self.client.get(response["Location"])
        self.assertContains(done, "https://wa.me/201001234501")  # tell the parent in one tap
        self.assertContains(self.client.get(reverse("education:group", args=[self.saturday.pk])), "المعادلات")
        self.assertContains(self.client.get(reverse("education:student", args=[self.mariam.pk])), "data-absences")
        self.assertContains(self.client.get(reverse("education:today") + f"?date={self.class_day.isoformat()}&lang=en"), "Taken")
class ReviewOneTests(EducationSetup):
    """Codex on #185: entity scope, terms that never meet, amounts that are not numbers."""

    def test_each_entity_sees_and_changes_only_its_own_education_records(self):
        from entities.current import working_in
        from entities.models import Entity

        mine = self.student()
        group = self.group()
        branch = Entity.objects.create(code="E-EDU", name_ar="فرع التجمع", activity_slug="education", sub_activity_slug="tutoring_center")
        with working_in(branch):
            theirs = services.save_student({"name": "طالب الفرع", "self_pays": "1"}, self.owner)
            self.assertEqual(list(services.scoped(Student.objects)), [theirs])
            self.assertFalse(services.scoped(StudyGroup.objects).exists())
            with self.assertRaisesMessage(ValidationError, "نفس الكيان"):
                services.enroll(theirs, group, self.owner)  # another entity's group
            with self.assertRaisesMessage(ValidationError, "نفس الكيان"):
                services.save_group({"course": self.maths, "name": "x"}, self.owner)  # the main entity's course
        # The whole group sees both; the main entity keeps its own (and older unowned ones).
        self.assertEqual(set(services.scoped(Student.objects)), {mine, theirs})
        from entities.services import main_entity

        with working_in(main_entity()):
            self.assertEqual(list(services.scoped(Student.objects)), [mine])
        # The screens follow: from the branch, the main entity's student is not found.
        self.client.force_login(self.owner)
        session = self.client.session
        session["hesba_entity"] = branch.pk
        session.save()
        self.assertEqual(self.client.get(reverse("education:student", args=[mine.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("education:student", args=[mine.pk]), {"action": "edit", "name": "x"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("education:student", args=[theirs.pk])).status_code, 200)

    def test_groups_in_terms_that_never_meet_share_a_teacher_and_a_room(self):
        from datetime import date

        services.save_group({"course": self.maths, "name": "ترم أول", "teacher": self.teacher, "room": "قاعة 1", "days": "sat", "start_time": time(16),
                             "starts_on": date(2026, 2, 1), "ends_on": date(2026, 6, 30)}, self.owner)
        later = services.save_group({"course": self.maths, "name": "ترم تاني", "teacher": self.teacher, "room": "قاعة 1", "days": "sat",
                                     "start_time": time(16), "starts_on": date(2026, 9, 1)}, self.owner)
        self.assertTrue(later.pk)
        with self.assertRaisesMessage(ValidationError, "أ. محمود"):
            services.save_group({"course": self.maths, "name": "متداخل", "teacher": self.teacher, "days": "sat", "start_time": time(16),
                                 "starts_on": date(2026, 6, 1), "ends_on": date(2026, 9, 15)}, self.owner)

    def test_nan_and_infinity_are_refused_not_a_server_error(self):
        for bad in ("NaN", "Infinity", "-inf"):
            with self.subTest(bad=bad), self.assertRaisesMessage(ValidationError, "صفر أو أكبر"):
                services.save_course({"name": "x", "fee": bad}, self.owner)
        with self.assertRaisesMessage(ValidationError, "الخصم"):
            services.enroll(self.student(), self.group(), self.owner, discount_percent="NaN")
        self.client.force_login(self.owner)
        page = self.client.post(reverse("education:courses"), {"name": "x", "fee": "NaN", "lang": "ar"})
        self.assertContains(page, "صفر أو أكبر")


class ReviewTwoTests(EducationSetup):
    """Codex on #186: one mark per student, classes on the group's own days only, the class date in the parent's message."""

    def setUp(self):
        super().setUp()
        from . import attendance

        self.register = attendance
        self.class_day = TODAY - timedelta(days=(TODAY.weekday() - 5) % 7)  # the latest Saturday
        self.saturday = self.group(days="sat")
        self.yousef = self.student()

    def test_stopped_and_enrolled_again_the_same_day_is_one_mark(self):
        first = services.enroll(self.yousef, self.saturday, self.owner, start_date=self.class_day - timedelta(days=14))
        services.stop(first, self.owner, end_date=self.class_day)
        services.enroll(self.yousef, self.saturday, self.owner, start_date=self.class_day)
        self.assertEqual(len(self.register.roster_on(self.saturday, self.class_day)), 1)
        session = self.register.take(self.saturday, self.owner, self.class_day, {})
        self.assertEqual(session.marks.count(), 1)

    def test_no_class_outside_the_groups_days_or_dates(self):
        services.enroll(self.yousef, self.saturday, self.owner, start_date=self.class_day - timedelta(days=30))
        with self.assertRaisesMessage(ValidationError, "مالهاش حصة"):
            self.register.take(self.saturday, self.owner, self.class_day - timedelta(days=1), {})  # a Friday
        StudyGroup.objects.filter(pk=self.saturday.pk).update(starts_on=self.class_day)
        self.saturday.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "مالهاش حصة"):
            self.register.take(self.saturday, self.owner, self.class_day - timedelta(days=7), {})  # before the group began
        # The group page offers the latest class day, never an off-day.
        self.client.force_login(self.owner)
        page = self.client.get(reverse("education:group", args=[self.saturday.pk]))
        self.assertContains(page, f"date={self.class_day.isoformat()}")
        off = self.client.get(reverse("education:attendance", args=[self.saturday.pk]) + f"?date={(self.class_day - timedelta(days=1)).isoformat()}")
        self.assertContains(off, "data-off-day")
        self.assertNotContains(off, "data-save-attendance")

    def test_an_older_class_tells_the_parent_its_date(self):
        from urllib.parse import unquote

        earlier = self.class_day - timedelta(days=7)
        services.enroll(self.yousef, self.saturday, self.owner, start_date=earlier - timedelta(days=7))
        session = self.register.take(self.saturday, self.owner, earlier, {str(self.yousef.pk): "absent"})
        text = unquote(self.register.parent_message(session.marks.get()))
        self.assertIn(f"يوم {earlier.isoformat()}", text)
        self.assertNotIn("النهارده", text)
        if self.class_day == TODAY:
            today_text = unquote(self.register.parent_message(self.register.take(self.saturday, self.owner, TODAY, {str(self.yousef.pk): "late"}).marks.get()))
            self.assertIn("النهارده", today_text)
class ReviewThreeTests(EducationSetup):
    """Codex on #185, second round."""

    def test_capacity_cannot_drop_below_the_students_already_in(self):
        group = self.group(capacity="3")
        for name in ("أ", "ب"):
            services.enroll(self.student(name), group, self.owner)
        data = {"course": self.maths, "name": group.name, "teacher": self.teacher, "room": group.room, "days": "sat,tue", "start_time": time(16),
                "duration_minutes": "90", "capacity": "1"}
        with self.assertRaisesMessage(ValidationError, "2 طالب"):
            services.save_group(data, self.owner, group)
        services.save_group({**data, "capacity": "2"}, self.owner, group)  # down to the roster is fine

    def test_overlapping_dates_without_a_shared_meeting_day_do_not_clash(self):
        from datetime import date

        # Both meet on Sunday; the only day their dates share is a Monday.
        services.save_group({"course": self.maths, "name": "قديمة", "teacher": self.teacher, "days": "sun", "start_time": time(16),
                             "ends_on": date(2026, 10, 12)}, self.owner)  # Monday 12 October
        self.assertTrue(services.save_group({"course": self.maths, "name": "جديدة", "teacher": self.teacher, "days": "sun", "start_time": time(16),
                                             "starts_on": date(2026, 10, 12)}, self.owner).pk)
        with self.assertRaisesMessage(ValidationError, "أ. محمود"):
            services.save_group({"course": self.maths, "name": "متداخلة", "teacher": self.teacher, "days": "sun", "start_time": time(16),
                                 "starts_on": date(2026, 10, 4), "ends_on": date(2026, 10, 11)}, self.owner)  # shares Sunday 11 October

    def test_a_whole_course_fee_is_never_shown_as_a_monthly_total(self):
        course = services.save_course({"name": "كورس Excel", "fee": "1500", "basis": "course"}, self.owner)
        group = services.save_group({"course": course, "name": "Excel", "days": "fri"}, self.owner)
        services.enroll(self.student(), group, self.owner)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("education:group", args=[group.pk]))
        self.assertNotContains(page, "data-group-total")
        self.assertContains(page, "الكورس كله")

    def test_a_short_parent_phone_says_so_in_the_screens_language(self):
        with self.assertRaisesMessage(ValidationError, "رقم التليفون قصير"):
            services.save_student({"name": "يوسف", "payer_name": "محمد", "payer_phone": "123"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "phone number is too short"):
            services.save_student({"name": "Omar", "self_pays": "1", "phone": "12"}, self.owner, lang="en")


class ReviewFourTests(EducationSetup):
    """Codex on #186, second round."""

    def setUp(self):
        super().setUp()
        from . import attendance

        self.register = attendance
        self.class_day = TODAY - timedelta(days=(TODAY.weekday() - 5) % 7)
        self.saturday = self.group(days="sat")
        self.yousef = self.student()
        services.enroll(self.yousef, self.saturday, self.owner, start_date=self.class_day - timedelta(days=30))

    def test_a_recorded_class_can_be_corrected_after_the_group_changed(self):
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.yousef.pk): "absent"})
        StudyGroup.objects.filter(pk=self.saturday.pk).update(days="sun", active=False)
        session = self.register.take(self.saturday, self.owner, self.class_day, {str(self.yousef.pk): "excused"})
        self.assertEqual(session.marks.get().presence, "excused")
        self.client.force_login(self.owner)
        page = self.client.get(reverse("education:attendance", args=[self.saturday.pk]) + f"?date={self.class_day.isoformat()}")
        self.assertContains(page, "data-save-attendance")
        with self.assertRaisesMessage(ValidationError, "مقفولة"):
            self.register.take(self.saturday, self.owner, self.class_day - timedelta(days=7), {})  # but no new class on a closed group

    def test_a_malformed_date_is_refused_not_taken_as_today(self):
        self.client.force_login(self.owner)
        url = reverse("education:attendance", args=[self.saturday.pk])
        for bad in ("", "2026-13-40", "yesterday"):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.post(url, {"date": bad, f"p_{self.yousef.pk}": "absent"}).status_code, 404)
        self.assertFalse(self.saturday.sessions.exists())


class GroupRoundThreeTests(EducationSetup):
    """Codex on #185, third round."""

    def test_a_record_is_offered_only_partners_from_its_own_entity_when_the_whole_group_is_open(self):
        from entities.current import working_in
        from entities.models import Entity

        mine, group = self.student(), self.group()
        branch = Entity.objects.create(code="E-EDU2", name_ar="فرع المعادي", activity_slug="education", sub_activity_slug="tutoring_center")
        with working_in(branch):
            course = services.save_course({"name": "علوم", "fee": "300"}, self.owner)
            theirs_group = services.save_group({"course": course, "name": "علوم 1", "days": "mon"}, self.owner)
            theirs = services.save_student({"name": "طالب الفرع", "self_pays": "1"}, self.owner)
        self.client.force_login(self.owner)  # no entity chosen: the whole group
        page = self.client.get(reverse("education:student", args=[mine.pk])).content.decode()
        self.assertIn(group.name, page)
        self.assertNotIn(theirs_group.name, page)
        page = self.client.get(reverse("education:group", args=[group.pk])).content.decode()
        self.assertNotIn(theirs.name, page)
        page = self.client.get(reverse("education:group", args=[theirs_group.pk])).content.decode()
        self.assertIn(theirs.name, page)
        self.assertNotIn(mine.name, page)

    def test_a_fee_too_big_for_the_books_is_a_message_not_a_server_error(self):
        for fee in ("10000000000", "9999999999.999"):
            with self.assertRaisesMessage(ValidationError, "المبلغ"):
                services.save_course({"name": f"كورس {fee}", "fee": fee}, self.owner)
        self.assertEqual(services.save_course({"name": "أكبر مبلغ", "fee": "9999999999.99"}, self.owner).fee, D("9999999999.99"))
        self.client.force_login(self.owner)
        response = self.client.post(reverse("education:courses"), {"name": "ضخم", "fee": "10000000000"})
        self.assertLess(response.status_code, 500)

    def test_editing_the_capacity_takes_the_lock_enrolling_takes(self):
        from unittest import mock

        group = self.group(capacity="3")
        data = {"course": self.maths, "name": group.name, "days": "sat,tue", "start_time": time(16), "capacity": "2"}
        with mock.patch("education.models.StudyGroup.objects.select_for_update", wraps=StudyGroup.objects.select_for_update) as lock:
            services.save_group(data, self.owner, group)
        self.assertTrue(lock.called)


class AttendanceRoundThreeTests(EducationSetup):
    """Codex on #186, third round."""

    def setUp(self):
        super().setUp()
        from . import attendance

        self.register = attendance
        self.class_day = TODAY - timedelta(days=(TODAY.weekday() - 5) % 7)
        self.saturday = self.group(days="sat")
        self.yousef, self.mariam = self.student(), self.student("مريم محمد")
        for student in (self.yousef, self.mariam):
            services.enroll(student, self.saturday, self.owner, start_date=self.class_day - timedelta(days=30))

    def test_a_teacher_without_the_schools_records_finds_their_classes_in_the_menu(self):
        from permissions.services import user_has_permission

        from hesba_testing.factories import make_role

        teacher_user = make_user(username="edu_teacher_only")
        make_user_profile(user=teacher_user, role=make_role("ROLE-TEACHER"))  # a role with none of the school's records
        self.assertFalse(user_has_permission(teacher_user, "master_data.view_master_data"))
        self.teacher.user = teacher_user
        self.teacher.save()
        self.client.force_login(teacher_user)
        today_url = reverse("education:today")
        page = self.client.get(reverse("dashboard_snapshot")).content.decode()
        self.assertIn(f'href="{today_url}?lang=ar"', page)
        self.assertNotIn(f'href="{reverse("education:students")}?lang=ar"', page)
        screen = self.client.get(today_url + f"?date={self.class_day.isoformat()}")
        self.assertContains(screen, f'data-take="{self.saturday.pk}"')
        for blocked in ("education:students", "education:groups", "education:courses"):
            self.assertNotContains(screen, f'href="{reverse(blocked)}?lang=ar"')  # no tab leads to a closed door
        # The owner still sees the school's records, and not the teacher's own entry.
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("dashboard_snapshot")), f'href="{today_url}?lang=ar" title="حصص مجموعاتي"')

    def test_an_unknown_mark_is_refused_and_changes_nothing(self):
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.mariam.pk): "absent"})
        with self.assertRaisesMessage(ValidationError, "مش معروفة"):
            self.register.take(self.saturday, self.owner, self.class_day, {str(self.mariam.pk): "here"})
        self.assertEqual(self.saturday.sessions.get().marks.get(student=self.mariam).presence, "absent")
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.mariam.pk): ""})  # left unmarked: present
        self.assertEqual(self.saturday.sessions.get().marks.get(student=self.mariam).presence, "present")

    def test_excused_students_are_counted_on_the_class_summaries(self):
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.yousef.pk): "excused", str(self.mariam.pk): "excused"})
        self.client.force_login(self.owner)
        today = self.client.get(reverse("education:today") + f"?date={self.class_day.isoformat()}")
        self.assertContains(today, "بعذر 2")
        self.assertContains(self.client.get(reverse("education:group", args=[self.saturday.pk])), "بعذر 2")


class GroupRoundFourTests(EducationSetup):
    """Codex on #185, fourth round."""

    def test_editing_a_student_keeps_a_switched_off_payer(self):
        other_parent = make_customer(customer_code="CUS-OTHER", name="أ ولي أمر تاني")
        student = self.student()
        Customer.objects.filter(pk=student.payer_id).update(active=False)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("education:student", args=[student.pk])).content.decode()
        self.assertIn(f'<option value="{student.payer_id}" selected>', page)
        self.client.post(reverse("education:student", args=[student.pk]), {"action": "edit", "name": "يوسف محمد علي", "payer": student.payer_id})
        student.refresh_from_db()
        self.assertNotEqual(student.payer_id, other_parent.pk)
        self.assertEqual(student.name, "يوسف محمد علي")

    def test_a_late_class_running_past_midnight_clashes_with_the_next_morning(self):
        services.save_group({"course": self.maths, "name": "سهرة السبت", "teacher": self.teacher, "days": "sat", "start_time": time(23),
                             "duration_minutes": "120"}, self.owner)  # Saturday 23:00 – Sunday 01:00
        with self.assertRaisesMessage(ValidationError, "أ. محمود"):
            services.save_group({"course": self.maths, "name": "فجر الأحد", "teacher": self.teacher, "days": "sun", "start_time": time(0, 30),
                                 "duration_minutes": "60"}, self.owner)
        self.assertTrue(services.save_group({"course": self.maths, "name": "ضهر الأحد", "teacher": self.teacher, "days": "sun", "start_time": time(12),
                                             "duration_minutes": "60"}, self.owner).pk)
        # Friday night into Saturday wraps round the week.
        services.save_group({"course": self.maths, "name": "سهرة الجمعة", "room": "قاعة 9", "days": "fri", "start_time": time(23, 30),
                             "duration_minutes": "90"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "قاعة 9"):
            services.save_group({"course": self.maths, "name": "بدري السبت", "room": "قاعة 9", "days": "sat", "start_time": time(0, 15),
                                 "duration_minutes": "30"}, self.owner)

    def test_the_clash_check_takes_the_teacher_and_entity_locks(self):
        from unittest import mock

        from staff.models import Employee

        with mock.patch.object(Employee.objects, "select_for_update", wraps=Employee.objects.select_for_update) as teacher_lock:
            self.group()
        self.assertTrue(teacher_lock.called)

    def test_a_capacity_too_big_for_the_column_is_a_message(self):
        with self.assertRaisesMessage(ValidationError, "السعة"):
            services.save_group({"course": self.maths, "name": "ضخمة", "days": "sat", "capacity": "2147483648"}, self.owner)

    def test_edits_record_what_changed(self):
        student = self.student()
        old_payer = student.payer.customer_code
        new_parent = make_customer(customer_code="CUS-NEW", name="ولي أمر جديد")
        services.save_student({"name": student.name, "payer": new_parent}, self.owner, student)
        log = AuditLog.objects.filter(action="edit_student").latest("pk")
        self.assertEqual((log.before_data["payer"], log.after_data["payer"]), (old_payer, "CUS-NEW"))
        group = self.group(capacity="10")
        services.save_group({"course": self.maths, "name": group.name, "teacher": self.teacher, "room": "قاعة 5", "days": "sat,tue",
                             "start_time": time(16), "duration_minutes": "90", "capacity": "12"}, self.owner, group)
        log = AuditLog.objects.filter(action="edit_group").latest("pk")
        self.assertEqual((log.before_data["room"], log.after_data["room"]), ("قاعة 1", "قاعة 5"))
        self.assertEqual((log.before_data["capacity"], log.after_data["capacity"]), (10, 12))


class AttendanceRoundFourTests(AttendanceRoundThreeTests):
    """Codex on #186, fourth round."""

    def test_a_teacher_can_still_reach_a_recorded_class_after_its_days_change(self):
        from hesba_testing.factories import make_role

        teacher_user = make_user(username="edu_teacher_history")
        make_user_profile(user=teacher_user, role=make_role("ROLE-TEACHER-H"))
        self.teacher.user = teacher_user
        self.teacher.save()
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.mariam.pk): "absent"})
        StudyGroup.objects.filter(pk=self.saturday.pk).update(days="sun", active=False)  # moved and closed afterwards
        self.client.force_login(teacher_user)
        page = self.client.get(reverse("education:today") + f"?date={self.class_day.isoformat()}")
        self.assertContains(page, f'data-take="{self.saturday.pk}"')
        form = self.client.get(reverse("education:attendance", args=[self.saturday.pk]) + f"?date={self.class_day.isoformat()}")
        self.assertEqual(form.status_code, 200)
        self.client.post(reverse("education:attendance", args=[self.saturday.pk]),
                         {"date": self.class_day.isoformat(), f"p_{self.mariam.pk}": "excused", f"p_{self.yousef.pk}": "present"})
        self.assertEqual(self.saturday.sessions.get().marks.get(student=self.mariam).presence, "excused")


class GroupRoundFiveTests(EducationSetup):
    """Codex on #185, fifth round."""

    def test_a_decimal_comma_is_a_decimal_point_and_thousands_still_group(self):
        for typed, fee in (("400,50", "400.50"), ("1,250", "1250.00"), ("1,250.75", "1250.75"), ("٤٠٠٫٥٠", "400.50"), ("12,500,000", "12500000.00")):
            with self.subTest(typed=typed):
                self.assertEqual(services.save_course({"name": f"كورس {typed}", "fee": typed}, self.owner).fee, D(fee))

    def test_editing_a_group_keeps_its_switched_off_teacher_and_course(self):
        from staff.models import Employee

        group = self.group()
        Employee.objects.filter(pk=self.teacher.pk).update(active=False)
        Course.objects.filter(pk=self.maths.pk).update(active=False)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("education:group", args=[group.pk])).content.decode()
        self.assertIn(f'<option value="{self.teacher.pk}" selected>', page)
        self.assertIn(f'<option value="{self.maths.pk}" selected>', page)
        self.client.post(reverse("education:group", args=[group.pk]), {"action": "edit", "course": self.maths.pk, "name": "اسم جديد", "teacher": self.teacher.pk,
                                                                       "days": ["sat", "tue"], "start_time": "16:00", "duration_minutes": "90", "capacity": "0"})
        group.refresh_from_db()
        self.assertEqual((group.name, group.teacher_id, group.course_id), ("اسم جديد", self.teacher.pk, self.maths.pk))

    def test_a_failed_course_form_keeps_the_chosen_basis(self):
        self.client.force_login(self.owner)
        page = self.client.post(reverse("education:courses"), {"name": "كورس Excel", "fee": "abc", "basis": "course"}).content.decode()
        self.assertIn('<option value="course" selected>', page)

    def test_an_edit_of_a_course_out_of_reach_is_not_a_new_course(self):
        self.client.force_login(self.owner)
        before = Course.objects.count()
        self.assertEqual(self.client.post(reverse("education:courses"), {"course": "99999", "name": "x", "fee": "1"}).status_code, 404)
        self.assertEqual(Course.objects.count(), before)

    def test_the_student_card_counts_groups_not_enrolment_history(self):
        student, group = self.student(), self.group()
        first = services.enroll(student, group, self.owner, start_date=TODAY - timedelta(days=30))
        services.stop(first, self.owner, end_date=TODAY - timedelta(days=10))
        services.enroll(student, group, self.owner, start_date=TODAY - timedelta(days=5))
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("education:student", args=[student.pk])), "<strong data-active-groups>1</strong>")


class AttendanceRoundFiveTests(AttendanceRoundThreeTests):
    """Codex on #186, fifth round."""

    def test_a_correction_records_each_mark_before_and_after(self):
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.yousef.pk): "absent", str(self.mariam.pk): "present"})
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.yousef.pk): "present", str(self.mariam.pk): "absent"})
        log = AuditLog.objects.filter(action="take_attendance").latest("pk")
        self.assertEqual(log.before_data["marks"], {self.yousef.code: ["absent", ""], self.mariam.code: ["present", ""]})
        self.assertEqual(log.after_data["marks"], {self.yousef.code: ["present", ""], self.mariam.code: ["absent", ""]})  # same totals, swapped marks


class GroupRoundSixTests(EducationSetup):
    """Codex on #185, sixth round."""

    def test_a_birth_date_in_the_future_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "تاريخ الميلاد"):
            self.student(date_of_birth=(TODAY + timedelta(days=1)).isoformat())

    def test_a_localized_discount_is_read(self):
        group = self.group()
        for typed in ("10,5", "١٠٫٥"):
            with self.subTest(typed=typed):
                student = self.student(f"طالب {typed}")
                self.assertEqual(services.enroll(student, group, self.owner, discount_percent=typed).discount_percent, D("10.50"))

    def test_fees_round_half_up_like_the_books(self):
        self.assertEqual(services.save_course({"name": "نص قرش", "fee": "1.005"}, self.owner).fee, D("1.01"))

    def test_a_failed_group_edit_keeps_what_was_typed(self):
        group = self.group()
        page = self.client
        page.force_login(self.owner)
        response = page.post(reverse("education:group", args=[group.pk]), {"action": "edit", "course": self.maths.pk, "name": "اسم جديد",
                                                                            "room": "قاعة 7", "days": ["sun"], "start_time": "16:00",
                                                                            "duration_minutes": "90", "capacity": "abc"})
        html = response.content.decode()
        self.assertIn('value="اسم جديد"', html)
        self.assertIn('value="قاعة 7"', html)
        self.assertIn('value="sun" checked', html)


class AttendanceRoundSixTests(AttendanceRoundThreeTests):
    """Codex on #186, sixth round."""

    def test_a_note_only_correction_keeps_the_old_note(self):
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.mariam.pk): "absent", f"note_{self.mariam.pk}": "تعبانة"})
        self.register.take(self.saturday, self.owner, self.class_day, {str(self.mariam.pk): "absent", f"note_{self.mariam.pk}": "مسافرة"})
        log = AuditLog.objects.filter(action="take_attendance").latest("pk")
        self.assertEqual(log.before_data["marks"][self.mariam.code], ["absent", "تعبانة"])
        self.assertEqual(log.after_data["marks"][self.mariam.code], ["absent", "مسافرة"])

    def test_thirty_days_means_thirty_calendar_days(self):
        from .models import ClassSession

        old = ClassSession.objects.create(group=self.saturday, date=TODAY - timedelta(days=30), taken_by=self.owner)
        Attendance.objects.create(session=old, student=self.mariam, presence="absent")
        self.assertIsNone(self.register.student_summary(self.mariam, today=TODAY)["rate"])  # 30 days ago is outside the last 30
