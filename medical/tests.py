"""MED-001: the patient file — who sees it, what it holds, how a visit is recorded."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from reports.tests_shell import sidebar_links

from . import services
from .models import ClinicalVisit, PatientFile

CLINIC = "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians"


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class PatientFileTests(TestCase):
    def setUp(self):
        prepared_client("medical", "clinic", CLINIC)
        self.owner = person(RoleCode.OWNER, "med_owner")
        self.patient = make_customer(customer_code="P-1", name="منى علي", phone="01001234567")
        self.client.force_login(self.owner)

    def test_the_owner_opens_a_file_records_allergies_and_a_visit(self):
        self.assertIn(reverse("medical:patients"), sidebar_links(self.client.get(reverse("dashboard_snapshot"))))
        listing = self.client.get(reverse("medical:patients") + "?q=منى")
        self.assertContains(listing, f'data-patient="{self.patient.pk}"')
        page = self.client.get(reverse("medical:file", args=[self.patient.pk]))
        self.assertContains(page, "P-00001")
        born = timezone.localdate().replace(year=timezone.localdate().year - 30) - timedelta(days=1)
        self.client.post(reverse("medical:file", args=[self.patient.pk]), {
            "date_of_birth": born.isoformat(), "gender": "female", "blood_type": "O+", "allergies": "بنسلين", "chronic_conditions": "ضغط"})
        page = self.client.get(reverse("medical:file", args=[self.patient.pk]))
        self.assertContains(page, "data-allergy-alert")
        self.assertContains(page, "بنسلين")
        self.assertContains(page, "30 سنة")

        response = self.client.post(reverse("medical:visit_new", args=[self.patient.pk]), {
            "visit_date": timezone.localdate().isoformat(), "complaint": "صداع", "blood_pressure": "150/95", "pulse": "88",
            "temperature": "37.2", "weight": "70", "diagnosis": "ارتفاع ضغط", "treatment": "كونكور 5 مجم مرة يوميًا",
            "follow_up_date": (timezone.localdate() + timedelta(days=14)).isoformat()})
        self.assertRedirects(response, reverse("medical:file", args=[self.patient.pk]) + "?lang=ar", fetch_redirect_response=False)
        visit = ClinicalVisit.objects.get()
        self.assertEqual((visit.pulse, str(visit.temperature), visit.blood_pressure), (88, "37.2", "150/95"))
        page = self.client.get(reverse("medical:file", args=[self.patient.pk]))
        self.assertContains(page, "ارتفاع ضغط")
        self.assertContains(page, f'data-visit="{visit.pk}"')
        rx = self.client.get(reverse("medical:prescription", args=[visit.pk]))
        self.assertContains(rx, "كونكور 5 مجم")
        self.assertContains(rx, "بنسلين")
        self.assertTrue(AuditLog.objects.filter(module="medical", action="create_clinical_visit").exists())
        english = self.client.get(reverse("medical:file", args=[self.patient.pk]) + "?lang=en")
        self.assertContains(english, "Allergy alert")

    def test_rules(self):
        cases = (
            ({"complaint": ""}, "اكتب الشكوى"),
            ({"complaint": "x", "pulse": "500"}, "النبض"),
            ({"complaint": "x", "temperature": "50"}, "الحرارة"),
            ({"complaint": "x", "follow_up_date": timezone.localdate().isoformat()}, "المتابعة"),
            ({"complaint": "x", "visit_date": "not-a-date"}, "تاريخ الكشف"),
        )
        for data, message in cases:
            with self.subTest(data=data):
                with self.assertRaises(ValidationError) as caught:
                    services.save_visit(self.patient, data, self.owner)
                self.assertIn(message, " ".join(caught.exception.messages))
        file = services.file_for(self.patient)
        with self.assertRaises(ValidationError):
            services.save_profile(file, {"date_of_birth": (timezone.localdate() + timedelta(days=1)).isoformat()}, self.owner)
        self.assertEqual(services.file_for(self.patient).pk, file.pk)
        second = make_customer(customer_code="P-2", name="أحمد")
        self.assertEqual(services.file_for(second).file_number, "P-00002")

    def test_a_visit_from_an_appointment_carries_the_doctor(self):
        from appointments.models import Appointment
        from staff.services import save_employee

        doctor = save_employee({"name": "د. سارة", "title": "طبيبة"}, self.owner)
        appointment = Appointment.objects.create(number="AP-1", customer=self.patient, employee=doctor, starts_at=timezone.now(), created_by=self.owner)
        detail = self.client.get(reverse("appointments:detail", args=[appointment.pk]))
        self.assertContains(detail, "data-appointment-visit")
        form = self.client.get(reverse("medical:visit_new", args=[self.patient.pk]) + f"?appointment={appointment.pk}")
        self.assertContains(form, f'<option value="{doctor.pk}" selected>')
        self.client.post(reverse("medical:visit_new", args=[self.patient.pk]), {"appointment": appointment.pk, "doctor": doctor.pk, "diagnosis": "التهاب حلق"})
        visit = ClinicalVisit.objects.get()
        self.assertEqual((visit.appointment, visit.doctor), (appointment, doctor))
        self.assertContains(self.client.get(reverse("parties:card", args=["customer", self.patient.pk])), "data-party-medical")


class MedicalPrivacyTests(TestCase):
    def setUp(self):
        prepared_client("medical", "clinic", CLINIC)
        self.owner = person(RoleCode.OWNER, "priv_owner")
        self.patient = make_customer(customer_code="P-9", name="مريض")

    def test_reception_cannot_read_but_a_doctor_can(self):
        from staff.services import save_employee

        cashier = person(RoleCode.CASHIER, "priv_cashier")
        self.client.force_login(cashier)
        for url in (reverse("medical:patients"), reverse("medical:file", args=[self.patient.pk]), reverse("medical:visit_new", args=[self.patient.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotIn(reverse("medical:patients"), sidebar_links(self.client.get(reverse("dashboard_snapshot"))))

        doctor_user = person(RoleCode.CASHIER, "priv_doctor")
        doctor = save_employee({"name": "د. كريم"}, self.owner)
        doctor.user = doctor_user
        doctor.save()
        self.client.force_login(doctor_user)
        self.assertEqual(self.client.get(reverse("medical:file", args=[self.patient.pk])).status_code, 200)
        visit = services.save_visit(self.patient, {"diagnosis": "برد"}, doctor_user)
        other = services.save_visit(self.patient, {"diagnosis": "حساسية"}, self.owner)
        with self.assertRaises(ValidationError):
            services.save_visit(self.patient, {"diagnosis": "تعديل"}, doctor_user, other)
        services.save_visit(self.patient, {"diagnosis": "برد شديد"}, doctor_user, visit)
        services.save_visit(self.patient, {"diagnosis": "حساسية موسمية"}, self.owner, visit)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("medical:patients")).status_code, 302)

    def test_only_on_a_medical_install(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("medical:patients")).status_code, 404)
        self.assertNotIn(reverse("medical:patients"), sidebar_links(self.client.get(reverse("dashboard_snapshot"))))
        self.assertNotContains(self.client.get(reverse("parties:card", args=["customer", self.patient.pk])), "data-party-medical")
        self.assertFalse(PatientFile.objects.exists())
