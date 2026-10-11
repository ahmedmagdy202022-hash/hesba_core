"""MED-001: the patient file — who sees it, what it holds, how a visit is recorded."""

from datetime import timedelta
from decimal import Decimal

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


class ClinicReportTests(TestCase):
    """MED-002: the clinic's report and its follow-ups."""

    def setUp(self):
        from hesba_testing.factories import make_item, make_location
        from staff.services import save_employee

        prepared_client("medical", "clinic", CLINIC)
        self.owner = person(RoleCode.OWNER, "rep_owner")
        make_location(location_code="CLINIC", is_default=True, is_selling_location=True)
        self.today = timezone.localdate()
        self.first = self.today.replace(day=1)
        self.sara = save_employee({"name": "د. سارة"}, self.owner)
        self.karim = save_employee({"name": "د. كريم"}, self.owner)
        self.checkup = make_item(item_code="SRV-CHK", item_name="كشف", is_stock_tracked=False, default_sale_price="300")
        self.review = make_item(item_code="SRV-REV", item_name="استشارة", is_stock_tracked=False, default_sale_price="150")
        self.mona = make_customer(customer_code="P-1", name="منى", phone="01001234567")
        self.ali = make_customer(customer_code="P-2", name="علي", phone="01112223334")

    def visit(self, patient, day, doctor, **extra):
        return services.save_visit(patient, {"visit_date": day.isoformat(), "doctor": doctor, "diagnosis": "x", **extra}, self.owner)

    def billed(self, patient, doctor, service, price, when=None):
        from appointments.models import Appointment
        from appointments.services import bill
        from sales.services import post_sales_invoice

        appointment = Appointment.objects.create(number=f"AP-{Appointment.objects.count() + 1}", customer=patient, employee=doctor, service=service,
                                                 price=price, starts_at=when or timezone.now(), status="done", created_by=self.owner)
        invoice = bill(appointment, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        appointment.refresh_from_db()
        return appointment

    def test_first_visits_follow_ups_and_new_patients(self):
        from . import insights

        self.visit(self.mona, self.first - timedelta(days=40), self.sara)  # Mona first came before the period
        self.visit(self.mona, self.today, self.sara)
        self.visit(self.ali, self.today, self.karim)
        self.visit(self.ali, self.today, self.karim)  # same day, entered second: a follow-up
        data = insights.period_report(self.first, self.today)
        totals = data["totals"]
        self.assertEqual((totals["visits"], totals["first"], totals["follow_up"]), (3, 1, 2))
        self.assertEqual((totals["patients"], totals["new"], totals["returning"]), (2, 1, 1))
        rows = {row["doctor"].pk: row for row in data["doctors"]}
        self.assertEqual((rows[self.sara.pk]["visits"], rows[self.sara.pk]["first"], rows[self.sara.pk]["follow_up"]), (1, 0, 1))
        self.assertEqual((rows[self.karim.pk]["visits"], rows[self.karim.pk]["first"], rows[self.karim.pk]["patients"]), (2, 1, 1))

    def test_revenue_per_doctor_and_service_comes_from_posted_invoices(self):
        from appointments.models import Appointment

        from . import insights

        self.billed(self.mona, self.sara, self.checkup, "300")
        self.billed(self.ali, self.sara, self.review, "150")
        self.billed(self.ali, self.karim, self.checkup, "250")
        Appointment.objects.create(number="AP-DRAFT", customer=self.mona, employee=self.karim, service=self.checkup, price="999",
                                   starts_at=timezone.now(), status="done", created_by=self.owner)  # not billed: earns nothing
        data = insights.period_report(self.first, self.today)
        doctors = {row["doctor"].pk: row["revenue"] for row in data["doctors"]}
        self.assertEqual(doctors, {self.sara.pk: Decimal("450.00"), self.karim.pk: Decimal("250.00")})
        services_ = {row["service"].pk: (row["count"], row["revenue"], row["average"]) for row in data["services"]}
        self.assertEqual(services_, {self.checkup.pk: (2, Decimal("550.00"), Decimal("275.00")), self.review.pk: (1, Decimal("150.00"), Decimal("150.00"))})
        self.assertEqual(data["totals"]["revenue"], Decimal("700.00"))
        self.assertEqual([row["doctor"].pk for row in data["doctors"]], [self.sara.pk, self.karim.pk])  # most earned first

    def test_a_cancelled_invoice_does_not_count(self):
        from sales.services import cancel_posted_sales_invoice

        from . import insights

        appointment = self.billed(self.mona, self.sara, self.checkup, "300")
        cancel_posted_sales_invoice(appointment.invoice.pk, self.owner, "غلط")
        self.assertEqual(insights.period_report(self.first, self.today)["totals"]["revenue"], Decimal("0.00"))

    def test_missed_and_coming_follow_ups(self):
        from appointments.models import Appointment

        from . import insights

        self.visit(self.mona, self.today - timedelta(days=20), self.sara, follow_up_date=(self.today - timedelta(days=5)).isoformat())
        self.visit(self.ali, self.today - timedelta(days=20), self.karim, follow_up_date=(self.today - timedelta(days=3)).isoformat())
        self.visit(self.ali, self.today - timedelta(days=2), self.karim)  # Ali came back: nothing missed
        nour = make_customer(customer_code="P-3", name="نور", phone="01223334445")
        self.visit(nour, self.today - timedelta(days=10), self.sara, follow_up_date=(self.today + timedelta(days=3)).isoformat())
        hala = make_customer(customer_code="P-4", name="هالة")
        self.visit(hala, self.today - timedelta(days=10), self.sara, follow_up_date=(self.today - timedelta(days=1)).isoformat())
        Appointment.objects.create(number="AP-HALA", customer=hala, employee=self.sara, starts_at=timezone.now() + timedelta(days=1), created_by=self.owner)
        old = make_customer(customer_code="P-5", name="قديم")
        self.visit(old, self.today - timedelta(days=200), self.sara, follow_up_date=(self.today - timedelta(days=150)).isoformat())  # too old to chase
        missed = insights.missed_follow_ups(self.today)
        self.assertEqual([(row["patient"].pk, row["late"]) for row in missed], [(self.mona.pk, 5)])
        self.assertEqual([row["patient"].pk for row in insights.coming_follow_ups(self.today)], [nour.pk])
        self.client.force_login(self.owner)
        page = self.client.get(reverse("medical:follow_ups"))
        self.assertContains(page, f'data-missed-patient="{self.mona.pk}"')
        self.assertContains(page, "https://wa.me/201001234567?text=")
        self.assertContains(page, f'data-coming-patient="{nour.pk}"')
        self.assertNotContains(page, f'data-missed-patient="{hala.pk}"')
        self.mona.active = False  # a patient made inactive is never chased again
        self.mona.save()
        self.assertEqual(insights.missed_follow_ups(self.today), [])

    def test_the_report_page_and_who_sees_what(self):
        from permissions.services import user_has_permission

        self.billed(self.mona, self.sara, self.checkup, "300")
        self.visit(self.mona, self.today, self.sara)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("medical:report"))
        self.assertContains(page, "data-report-revenue")
        self.assertContains(page, "300.00")
        self.assertContains(page, f'data-report-service="{self.checkup.pk}"')
        self.assertContains(self.client.get(reverse("medical:patients")), f'href="{reverse("medical:report")}?lang=ar"')
        self.assertContains(self.client.get(reverse("medical:report") + "?from=2026-13-01"), "الفترة مش مظبوطة")
        self.assertEqual(self.client.get(reverse("medical:report") + "?lang=en").status_code, 200)

        doctor_user = person(RoleCode.CASHIER, "rep_doctor")
        self.assertFalse(user_has_permission(doctor_user, services.VIEW))
        self.sara.user = doctor_user
        self.sara.save()
        self.client.force_login(doctor_user)
        self.assertEqual(self.client.get(reverse("medical:report")).status_code, 403)  # the whole clinic is not a doctor's to read
        self.assertNotContains(self.client.get(reverse("medical:patients")), f'href="{reverse("medical:report")}?lang=ar"')
        self.visit(self.mona, self.today - timedelta(days=30), self.karim, follow_up_date=(self.today - timedelta(days=4)).isoformat())
        self.visit(self.ali, self.today - timedelta(days=30), self.sara, follow_up_date=(self.today - timedelta(days=4)).isoformat())
        page = self.client.get(reverse("medical:follow_ups"))
        self.assertContains(page, f'data-missed-patient="{self.ali.pk}"')  # her own patient
        self.assertNotContains(page, f'data-missed-patient="{self.mona.pk}"')  # Mona was seen today anyway

        cashier = person(RoleCode.CASHIER, "rep_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("medical:follow_ups")).status_code, 403)
        self.assertEqual(self.client.get(reverse("medical:report")).status_code, 403)

    def test_money_is_hidden_without_the_sales_report(self):
        from unittest import mock

        self.billed(self.mona, self.sara, self.checkup, "300")
        self.client.force_login(self.owner)
        with mock.patch("permissions.services.user_has_permission", side_effect=lambda user, code: code != "reports.view_all_sales_report"):
            page = self.client.get(reverse("medical:report"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-report-revenue")
        self.assertNotContains(page, "300.00")

    def test_only_on_a_medical_install(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("medical:report")).status_code, 404)
        self.assertEqual(self.client.get(reverse("medical:follow_ups")).status_code, 404)
