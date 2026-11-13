"""Watch round 1b on the clinic: the doctor list (CL-06), the dashboard's two daily jobs (CL-09) and the empty screens (CL-07-like)."""

from datetime import datetime, time

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from staff.tests_watch1b import select_options

from . import tests_access as access
from .models import ClinicalVisit


class VisitFormDoctorsTests(access.Clinic):
    def setUp(self):
        super().setUp()
        from staff.services import save_employee

        self.sameer = save_employee({"name": "سمير عادل", "title": "مندوب مبيعات"}, self.owner)
        self.hala = save_employee({"name": "هالة مصطفى", "title": "مندوبة"}, self.owner)

    def form(self, **params):
        self.client.force_login(self.owner)
        return self.client.get(reverse("medical:visit_new", args=[self.patient.pk]), params).content.decode()

    def test_the_doctor_list_has_the_clinics_staff_and_no_sales_rep(self):
        names = select_options(self.form(), "doctor")
        self.assertIn("د. سارة", names)
        self.assertIn("منى الاستقبال", names)  # only a rep's own title takes someone out: nobody else disappears
        self.assertNotIn("سمير عادل", names)
        self.assertNotIn("هالة مصطفى", names)

    def test_a_visit_already_recorded_with_such_a_doctor_keeps_them_on_its_form_and_on_save(self):
        from . import services

        visit = services.save_visit(self.patient, {"complaint": "x", "doctor": self.sameer, "visit_date": timezone.localdate().isoformat()}, self.owner)
        self.client.force_login(self.owner)
        url = reverse("medical:visit_edit", args=[visit.pk])
        html = self.client.get(url).content.decode()
        self.assertIn("سمير عادل", select_options(html, "doctor"))
        self.assertIn(f'<option value="{self.sameer.pk}" selected>', html)
        self.assertNotIn("هالة مصطفى", select_options(html, "doctor"))  # only the one the record has
        self.client.post(url, {"complaint": "y", "doctor": self.sameer.pk, "visit_date": timezone.localdate().isoformat()})
        visit.refresh_from_db()
        self.assertEqual((visit.complaint, visit.doctor_id), ("y", self.sameer.pk))

    def test_a_new_visit_cannot_be_given_to_a_rep(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("medical:visit_new", args=[self.patient.pk]),
                         {"complaint": "صداع جديد", "doctor": self.hala.pk, "visit_date": timezone.localdate().isoformat()})
        self.assertIsNone(ClinicalVisit.objects.get(complaint="صداع جديد").doctor_id)

    def test_a_visit_started_from_an_appointment_booked_with_such_a_doctor_keeps_them(self):
        from appointments.models import Appointment

        booked = Appointment.objects.create(number="AP-W1B", customer=self.patient, employee=self.sameer, service=self.service, price="300",
                                            starts_at=timezone.make_aware(datetime.combine(timezone.localdate(), time(23, 58))), created_by=self.owner)
        html = self.form(appointment=booked.pk)
        self.assertIn("سمير عادل", select_options(html, "doctor"))
        self.assertIn(f'<option value="{self.sameer.pk}" selected>', html)
        self.client.post(reverse("medical:visit_new", args=[self.patient.pk]),
                         {"complaint": "من الميعاد", "doctor": self.sameer.pk, "appointment": booked.pk, "visit_date": timezone.localdate().isoformat()})
        self.assertEqual(ClinicalVisit.objects.get(complaint="من الميعاد").doctor_id, self.sameer.pk)

    def test_a_rep_who_signs_in_is_not_preselected_as_the_doctor(self):
        self.sameer.user = self.users["manager"]  # a login linked to a rep: the form must not pre-select them
        self.sameer.save()
        self.client.force_login(self.users["manager"])
        html = self.client.get(reverse("medical:visit_new", args=[self.patient.pk])).content.decode()
        self.assertNotIn(f'<option value="{self.sameer.pk}"', html)
        self.assertNotIn(" selected", html.split('name="doctor"')[1].split("</select>")[0])


class DashboardShortcutsTests(access.Clinic):
    """CL-09: «حجز جديد» and «سجّل كشف» on the clinic's dashboard, only for those who may do them."""

    def top(self, who, **params):
        self.client.force_login(self.users[who])
        response = self.client.get(reverse("dashboard_snapshot"), params)
        self.assertEqual(response.status_code, 200)
        return [row["key"] for row in response.context["top_actions"]], response.content.decode()

    def test_each_role_sees_only_the_jobs_it_may_do(self):
        expected = {"owner": ["new_booking", "new_visit", "collect", "new_item"], "manager": ["new_booking", "new_visit", "collect", "new_item"],
                    "doctor": ["new_visit"],  # the doctor records visits and does not book (no sales permission)
                    "desk": ["new_booking", "collect"],  # reception books and collects but holds no clinical permission
                    "keeper": ["new_item"], "nobody": []}
        for who, keys in expected.items():
            with self.subTest(who=who):
                self.assertEqual(self.top(who)[0], keys)

    def test_the_two_chips_say_their_job_and_lead_to_the_screens_that_start_it(self):
        keys, ar = self.top("owner")
        booking = f'href="{reverse("appointments:agenda")}?lang=ar#new-appointment" data-top-action="new_booking"'
        visit = f'href="{reverse("medical:patients")}?lang=ar" data-top-action="new_visit"'
        self.assertIn(booking, ar)
        self.assertIn(visit, ar)
        self.assertIn('<span class="dash-top-action__label">حجز جديد</span>', ar)
        self.assertIn('<span class="dash-top-action__label">سجّل كشف</span>', ar)
        self.assertLess(ar.index(booking), ar.index(visit))
        self.assertLess(ar.index(visit), ar.index('data-top-action="collect"'))  # the clinic's own jobs lead, the others follow in their order
        _, en = self.top("owner", lang="en")
        self.assertIn('<span class="dash-top-action__label">New booking</span>', en)
        self.assertIn('<span class="dash-top-action__label">Record a visit</span>', en)

    def test_the_agenda_is_still_listed_under_start_here(self):
        _, ar = self.top("owner")
        self.assertIn('data-action="bookings"', ar)  # the lead action to the same screen is not hidden by the chip

    def test_the_booking_chip_lands_on_the_form(self):
        self.client.force_login(self.owner)
        agenda = self.client.get(reverse("appointments:agenda")).content.decode()
        self.assertIn('<form class="op-form" id="new-appointment" method="post" data-appointment-form', agenda)

    def test_the_chips_follow_the_modules_that_are_switched_on(self):
        from settings_core.models import FeatureFlag
        from settings_core.setup_services import module_flag_code

        self.assertIn("new_booking", self.top("owner")[0])
        FeatureFlag.objects.filter(code=module_flag_code("appointments_visits")).update(enabled=False)
        after = self.top("owner")[0]
        self.assertNotIn("new_booking", after)
        self.assertIn("new_visit", after)


class OtherActivitiesKeepTheirShortcutsTests(TestCase):
    """The row of a shop, a salon and a lab: only an activity that lists its own chips gets them."""

    def keys_of(self, activity, sub, username):
        from reports.tests_analytics import person
        from reports.tests_dashboard import prepared_client
        from permissions.models import RoleCode
        from settings_core.activity_fit import default_modules
        prepared_client(activity, sub, ",".join(default_modules(activity, sub)))
        self.client.force_login(person(RoleCode.OWNER, username))
        return [row["key"] for row in self.client.get(reverse("dashboard_snapshot")).context["top_actions"]]

    def test_a_shop_has_the_row_it_always_had(self):
        self.assertEqual(self.keys_of("commercial", "retail", "w1b_s_shop"), ["pos", "collect", "new_item", "new_purchase"])

    def test_a_salon_gets_no_clinic_chip(self):
        keys = self.keys_of("services", "beauty", "w1b_s_salon")
        self.assertFalse({"new_booking", "new_visit"} & set(keys))

    def test_a_lab_books_but_records_no_visit(self):
        keys = self.keys_of("medical", "lab", "w1b_s_lab")
        self.assertIn("new_booking", keys)
        self.assertNotIn("new_visit", keys)


class EmptyDailyScreensTests(access.Clinic):
    """The agenda and the queue, when empty, say what to do next; the follow-ups' empty lines are good news and stay as they are."""

    def setUp(self):
        super().setUp()
        from appointments.models import Appointment

        Appointment.objects.all().delete()

    def get(self, who, name, **params):
        self.client.force_login(self.users[who] if isinstance(who, str) else who)
        response = self.client.get(reverse(name), params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_an_empty_agenda_offers_the_booking_form_to_those_who_may_book(self):
        ar = self.get("owner", "appointments:agenda")
        self.assertIn('<span class="hs-empty" data-agenda-empty><b>مفيش مواعيد في اليوم ده.</b><a href="#new-appointment" data-agenda-empty-book>احجز كشف</a></span>', ar)
        en = self.get("owner", "appointments:agenda", lang="en")
        self.assertIn('<b>No appointments on this day.</b><a href="#new-appointment" data-agenda-empty-book>Book a visit</a>', en)

    def test_it_offers_no_link_to_a_form_the_user_does_not_have(self):
        from reports.tests_analytics import person
        from permissions.models import RoleCode

        ar = self.get(person(RoleCode.ACCOUNTANT, "w1b_empty_acc"), "appointments:agenda")  # may view the agenda, may not book
        self.assertIn('<span class="hs-empty" data-agenda-empty><b>مفيش مواعيد في اليوم ده.</b></span>', ar)
        self.assertNotIn("data-agenda-empty-book", ar)

    def test_an_empty_queue_points_reception_to_the_check_in_below(self):
        ar = self.get("owner", "medical:queue")
        self.assertIn('<b>مفيش حد في الدور النهارده.</b><a href="#walk-in" data-queue-empty-check-in>سجّل وصول مريض</a>', ar)
        self.assertIn('<b>مفيش مواعيد مستنية.</b><a href="#walk-in">سجّل وصول مريض من غير ميعاد</a>', ar)
        self.assertIn('<h2 id="walk-in">', ar)
        en = self.get("owner", "medical:queue", lang="en")
        self.assertIn('<b>Nobody in the queue today.</b><a href="#walk-in" data-queue-empty-check-in>Check a patient in</a>', en)

    def test_a_doctor_who_cannot_check_in_is_told_how_the_queue_fills(self):
        ar = self.get("doctor", "medical:queue")
        self.assertIn('<b>مفيش حد في الدور النهارده.</b><span>الدور بيتملى لما الاستقبال يسجّل وصول المرضى.</span>', ar)
        self.assertNotIn("data-queue-empty-check-in", ar)
        self.assertNotIn('id="walk-in"', ar)  # the desk's part of the screen is not theirs

    def test_the_follow_ups_empty_lines_are_good_news_and_stay_plain(self):
        ClinicalVisit.objects.all().delete()
        ar = self.get("owner", "medical:follow_ups")
        self.assertIn('<td colspan="7" class="op-empty">مفيش متابعات فاتت.</td>', ar)
        self.assertIn('<td colspan="6" class="op-empty">مفيش متابعات جاية من غير حجز.</td>', ar)
