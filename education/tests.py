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
