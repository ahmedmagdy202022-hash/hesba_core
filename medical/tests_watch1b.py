"""Watch round 1b on the clinic: the doctor list (CL-06), the dashboard's two daily jobs (CL-09) and the empty screens (CL-07-like)."""

from datetime import datetime, time

from django.urls import reverse
from django.utils import timezone

from staff.tests_watch1b import select_options

from . import tests_access as access
from .models import ClinicalVisit


class VisitFormDoctorsTests(access.Clinic):
    def setUp(self):
        super().setUp()
        from staff.services import save_employee

        self.sameer = save_employee({"name": "سمير المندوب", "title": "مندوب مبيعات"}, self.owner)
        self.hala = save_employee({"name": "هالة المندوبة", "title": "مندوبة"}, self.owner)

    def form(self, **params):
        self.client.force_login(self.owner)
        return self.client.get(reverse("medical:visit_new", args=[self.patient.pk]), params).content.decode()

    def test_the_doctor_list_has_the_clinics_staff_and_no_sales_rep(self):
        names = select_options(self.form(), "doctor")
        self.assertIn("د. سارة", names)
        self.assertIn("منى الاستقبال", names)  # only a rep's own title takes someone out: nobody else disappears
        self.assertNotIn("سمير المندوب", names)
        self.assertNotIn("هالة المندوبة", names)

    def test_a_visit_already_recorded_with_such_a_doctor_keeps_them_on_its_form_and_on_save(self):
        from . import services

        visit = services.save_visit(self.patient, {"complaint": "x", "doctor": self.sameer, "visit_date": timezone.localdate().isoformat()}, self.owner)
        self.client.force_login(self.owner)
        url = reverse("medical:visit_edit", args=[visit.pk])
        html = self.client.get(url).content.decode()
        self.assertIn("سمير المندوب", select_options(html, "doctor"))
        self.assertIn(f'<option value="{self.sameer.pk}" selected>', html)
        self.assertNotIn("هالة المندوبة", select_options(html, "doctor"))  # only the one the record has
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
        self.assertIn("سمير المندوب", select_options(html, "doctor"))
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
