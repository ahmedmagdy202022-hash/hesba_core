"""MED-001: the patient file — who sees it, what it holds, how a visit is recorded."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
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


class DentalChartTests(TestCase):
    """MED-003: the dental chart and the treatment plan in stages (HG-043)."""

    def setUp(self):
        from hesba_testing.factories import make_item, make_location
        from staff.services import save_employee

        prepared_client("medical", "dental", CLINIC)
        self.owner = person(RoleCode.OWNER, "dn_owner")
        make_location(location_code="CLINIC", is_default=True, is_selling_location=True)
        self.doctor = save_employee({"name": "د. هالة"}, self.owner)
        self.filling = make_item(item_code="SRV-FIL", item_name="حشو عصب", is_stock_tracked=False, default_sale_price="1500")
        self.cleaning = make_item(item_code="SRV-CLN", item_name="تنظيف جير", is_stock_tracked=False, default_sale_price="600")
        self.patient = make_customer(customer_code="P-1", name="منى", phone="01001234567")
        self.client.force_login(self.owner)
        self.url = reverse("medical:dental", args=[self.patient.pk])

    def test_a_tooth_is_recorded_and_shown_on_the_chart(self):
        from .models import ToothRecord

        self.assertContains(self.client.get(reverse("medical:file", args=[self.patient.pk])), "data-dental-link")
        self.client.post(self.url, {"action": "tooth", "tooth": "16", "condition": "caries", "notes": "عميق"})
        self.client.post(self.url, {"action": "tooth", "tooth": "16", "condition": "filled"})  # the same tooth again: updated, not doubled
        record = ToothRecord.objects.get()
        self.assertEqual((record.tooth, record.condition, record.notes), (16, "filled", ""))
        log = AuditLog.objects.filter(action="set_tooth").latest("pk")
        self.assertEqual((log.before_data["condition"], log.after_data["condition"]), ("caries", "filled"))
        page = self.client.get(self.url)
        self.assertContains(page, 'data-tooth="16" data-condition="filled"')
        self.assertContains(page, 'data-tooth="48" data-condition="healthy"')
        self.assertContains(self.client.get(self.url + "?primary=1"), 'data-tooth="55"')
        self.assertEqual(self.client.get(self.url + "?lang=en").status_code, 200)

    def test_billed_steps_count_in_the_clinic_report_and_a_free_step_can_be_billed(self):
        from django.utils import timezone

        from sales.services import post_sales_invoice

        from . import dental, insights

        step = dental.plan_step(self.patient, {"service": self.filling, "tooth": "16", "doctor": self.doctor}, self.owner)
        free = dental.plan_step(self.patient, {"service": self.cleaning, "price": "0"}, self.owner)
        dental.complete_step(free, self.owner)
        self.assertContains(self.client.get(self.url), "data-bill-steps")  # a done step at 0 still needs its invoice
        dental.complete_step(step, self.owner)
        invoice = dental.bill_done(self.patient, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        today = timezone.localdate()
        report = insights.period_report(today.replace(day=1), today)
        self.assertEqual([(row["doctor"], row["revenue"]) for row in report["doctors"] if row["revenue"]], [(self.doctor, Decimal("1500.00"))])
        self.assertEqual(report["totals"]["revenue"], Decimal("1500.00"))

    def test_bad_teeth_and_conditions_are_refused(self):
        from . import dental

        for tooth in ("19", "0", "abc", "", "90"):
            with self.subTest(tooth=tooth), self.assertRaises(ValidationError):
                dental.set_tooth(self.patient, tooth, "caries", self.owner)
        with self.assertRaises(ValidationError):
            dental.set_tooth(self.patient, "11", "purple", self.owner)
        response = self.client.post(self.url, {"action": "tooth", "tooth": "99", "condition": "caries"})
        self.assertContains(response, "ترقيم FDI")

    def test_a_plan_in_stages_billed_on_one_ordinary_invoice(self):
        from parties.services import balance
        from sales.services import post_sales_invoice

        from . import dental
        from .models import TreatmentStep

        self.client.post(self.url, {"action": "plan", "service": self.cleaning.pk, "stage": "1", "doctor": self.doctor.pk})
        self.client.post(self.url, {"action": "plan", "service": self.filling.pk, "tooth": "16", "stage": "2", "price": "1,400"})
        self.client.post(self.url, {"action": "plan", "service": self.filling.pk, "tooth": "26", "stage": "2"})
        cleaning, first, second = TreatmentStep.objects.order_by("pk")
        self.assertEqual((cleaning.tooth, cleaning.price, first.price, second.price), (None, Decimal("600.00"), Decimal("1400.00"), Decimal("1500.00")))
        self.assertEqual(dental.plan_totals(self.patient)["planned"], Decimal("3500.00"))
        self.client.post(self.url, {"action": "done", "step": cleaning.pk})
        self.client.post(self.url, {"action": "done", "step": first.pk, "doctor": self.doctor.pk})
        self.client.post(self.url, {"action": "cancel", "step": second.pk})
        totals = dental.plan_totals(self.patient)
        self.assertEqual((totals["planned"], totals["done"], totals["unbilled"]), (Decimal("0"), Decimal("2000.00"), Decimal("2000.00")))
        self.assertEqual(balance("customer", self.patient), Decimal("0"))  # nothing touches the account before an invoice is posted

        self.client.post(self.url, {"action": "bill"})
        invoice = TreatmentStep.objects.get(pk=first.pk).invoice
        self.assertEqual((invoice.status, invoice.total_amount, invoice.customer_id), ("draft", Decimal("2000.00"), self.patient.pk))
        self.assertEqual(sorted(invoice.lines.values_list("item__item_code", flat=True)), ["SRV-CLN", "SRV-FIL"])
        self.assertEqual(dental.plan_totals(self.patient)["unbilled"], Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "مفيش خطوات"):
            dental.bill_done(self.patient, self.owner)  # never billed twice
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual(balance("customer", self.patient), Decimal("2000.00"))
        self.assertContains(self.client.get(self.url), invoice.invoice_number)

    def test_a_cancelled_invoice_frees_its_steps_and_a_done_step_stays_done(self):
        from sales.services import cancel_posted_sales_invoice, post_sales_invoice

        from . import dental

        step = dental.plan_step(self.patient, {"service": self.cleaning}, self.owner)
        dental.complete_step(step, self.owner)
        invoice = dental.bill_done(self.patient, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط")
        self.assertEqual(dental.plan_totals(self.patient)["unbilled"], Decimal("600.00"))
        self.assertNotEqual(dental.bill_done(self.patient, self.owner).pk, invoice.pk)
        for action in (dental.complete_step, dental.cancel_step):
            with self.subTest(action=action.__name__), self.assertRaisesMessage(ValidationError, "مش مخططة"):
                action(step, self.owner)
        with self.assertRaisesMessage(ValidationError, "يوم لسه مجاش"):
            dental.complete_step(dental.plan_step(self.patient, {"service": self.cleaning}, self.owner), self.owner,
                                 done_on=timezone.localdate() + timedelta(days=1))

    def test_who_may_do_what(self):
        from . import dental

        cashier = person(RoleCode.CASHIER, "dn_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        doctor_user = person(RoleCode.STOCK_KEEPER, "dn_doctor")
        self.doctor.user = doctor_user
        self.doctor.save()
        self.client.force_login(doctor_user)
        self.client.post(self.url, {"action": "tooth", "tooth": "21", "condition": "caries"})
        self.assertTrue(self.patient.teeth.filter(tooth=21).exists())  # a doctor charts their patients
        step = dental.plan_step(self.patient, {"service": self.cleaning}, self.owner)
        dental.complete_step(step, self.owner)
        page = self.client.get(self.url)
        self.assertNotContains(page, "data-bill-steps")  # billing is the counter's
        self.client.post(self.url, {"action": "bill"})
        self.assertIsNone(self.patient.treatment_steps.get().invoice)
        with self.assertRaises(PermissionDenied):
            dental.bill_done(self.patient, doctor_user)

    def test_only_for_a_dental_clinic(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(sub_activity_slug="clinic")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("medical:file", args=[self.patient.pk])), "data-dental-link")


class LabResultTests(TestCase):
    """MED-004: tests with normal ranges and the patient's results (HG-043)."""

    def setUp(self):
        prepared_client("medical", "lab", CLINIC)
        self.owner = person(RoleCode.OWNER, "lab_owner")
        self.patient = make_customer(customer_code="P-1", name="منى", phone="01001234567")
        self.client.force_login(self.owner)
        from . import lab

        self.lab = lab
        self.hb = lab.save_test({"name": "هيموجلوبين", "unit": "g/dL", "low": "12", "high": "16"}, self.owner)
        self.sugar = lab.save_test({"name": "سكر صايم", "unit": "mg/dL", "low": "70", "high": "100"}, self.owner)
        self.group = lab.save_test({"name": "فصيلة الدم"}, self.owner)
        self.url = reverse("medical:lab", args=[self.patient.pk])

    def test_review_fixes_commas_corrections_and_removal(self):
        from datetime import timedelta

        from django.utils import timezone

        from .models import LabResult

        self.assertEqual(self.lab.number("12,5"), Decimal("12.500"))  # a decimal comma, not 125
        self.assertEqual(self.lab.number("1,250.5"), Decimal("1250.500"))
        self.assertEqual(self.lab.number("0,75"), Decimal("0.750"))
        for raw in ("1,2,3", "12,34,5", "999999999.9996"):  # no way to read them, or too big once rounded
            with self.subTest(raw=raw), self.assertRaises(Exception):
                self.lab.number(raw)
        yesterday = timezone.localdate() - timedelta(days=1)
        self.lab.record_results(self.patient, yesterday, {self.sugar: ("105", "")}, self.owner)
        self.lab.save_test({"name": "سكر صايم", "unit": "mg/dL", "low": "70", "high": "110"}, self.owner, self.sugar)
        self.lab.record_results(self.patient, yesterday, {self.sugar: ("105", "")}, self.owner)  # a correction of that day
        result = LabResult.objects.get(test=self.sugar)
        self.assertEqual((result.high, result.flag), (Decimal("100.000"), "high"))  # judged by that day's range, not today's
        self.client.force_login(self.owner)
        self.client.post(self.url, {"action": "remove", "result": result.pk, "reason": "مريض تاني"})
        self.assertFalse(LabResult.objects.filter(pk=result.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="remove_lab_result", after_data__reason="مريض تاني").exists())
        self.assertEqual(self.client.get(reverse("medical:lab_tests") + "?edit=abc").status_code, 404)
        log = AuditLog.objects.filter(action="save_lab_test", object_id=str(self.sugar.pk)).latest("pk")
        self.assertEqual(log.before_data["high"], "100.000")

    def test_flags_at_the_boundaries(self):
        flag = self.lab.flag
        cases = ((Decimal("11.999"), "low"), (Decimal("12"), "normal"), (Decimal("16"), "normal"), (Decimal("16.001"), "high"), (None, ""))
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(flag(value, Decimal("12"), Decimal("16")), expected)
        self.assertEqual(flag(Decimal("5"), None, Decimal("3")), "high")  # only an upper limit
        self.assertEqual(flag(Decimal("5"), Decimal("6"), None), "low")
        self.assertEqual(flag(Decimal("5"), None, None), "")
        for typed, expected in (("١٢٫٥", Decimal("12.500")), ("7,500", Decimal("7500.000")), (" 9 ", Decimal("9.000")), ("", None)):
            with self.subTest(typed=typed):
                self.assertEqual(self.lab.number(typed), expected)

    def test_results_are_entered_flagged_corrected_and_printed(self):
        from .models import LabResult

        day = (timezone.localdate() - timedelta(days=1)).isoformat()
        response = self.client.post(self.url, {"taken_on": day, f"value_{self.hb.pk}": "11.2", f"value_{self.sugar.pk}": "95",
                                               f"text_{self.group.pk}": "A+"})
        self.assertRedirects(response, self.url + "?lang=ar", fetch_redirect_response=False)
        flags = dict(LabResult.objects.values_list("test__name", "flag"))
        self.assertEqual(flags, {"هيموجلوبين": "low", "سكر صايم": "normal", "فصيلة الدم": ""})
        self.client.post(self.url, {"taken_on": day, f"value_{self.hb.pk}": "12.4"})  # a correction of the same day
        hb = LabResult.objects.get(test=self.hb)
        self.assertEqual((LabResult.objects.count(), hb.value, hb.flag), (3, Decimal("12.400"), "normal"))
        log = AuditLog.objects.filter(action="record_lab_result", object_id=str(hb.pk)).latest("pk")
        self.assertEqual((log.before_data["flag"], log.after_data["flag"]), ("low", "normal"))
        page = self.client.get(self.url)
        self.assertContains(page, f'data-lab-result="{hb.pk}" data-flag="normal"')
        sheet = self.client.get(reverse("medical:lab_sheet", args=[self.patient.pk, day]))
        self.assertContains(sheet, "data-lab-sheet")
        self.assertContains(sheet, "12.4")
        self.assertContains(sheet, "A+")
        self.assertEqual(self.client.get(reverse("medical:lab_sheet", args=[self.patient.pk, "2020-01-01"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("medical:lab_sheet", args=[self.patient.pk, "nope"])).status_code, 404)

    def test_a_changed_range_does_not_rewrite_an_old_result(self):
        from .models import LabResult

        old = timezone.localdate() - timedelta(days=10)
        self.lab.record_results(self.patient, old, {self.sugar: ("105", "")}, self.owner)
        self.client.post(reverse("medical:lab_tests"), {"test": self.sugar.pk, "name": "سكر صايم", "unit": "mg/dL", "low": "70", "high": "110", "active": "1"})
        self.sugar.refresh_from_db()
        self.assertEqual(self.sugar.high, Decimal("110.000"))
        first = LabResult.objects.get()
        self.assertEqual((first.flag, first.high), ("high", Decimal("100.000")))  # flagged against the range of its day
        self.lab.record_results(self.patient, timezone.localdate(), {self.sugar: ("105", "")}, self.owner)
        latest = LabResult.objects.get(taken_on=timezone.localdate())
        self.assertEqual(latest.flag, "normal")
        self.assertEqual(self.lab.previous(latest), first)

    def test_bad_input_is_refused_with_what_was_typed(self):
        response = self.client.post(self.url, {"taken_on": timezone.localdate().isoformat(), f"value_{self.hb.pk}": "عالي"})
        self.assertContains(response, "مش رقم")
        self.assertContains(response, 'value="عالي"')
        self.assertContains(self.client.post(self.url, {"taken_on": timezone.localdate().isoformat()}), "نتيجة تحليل واحد")
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertContains(self.client.post(self.url, {"taken_on": tomorrow, f"value_{self.hb.pk}": "13"}), "لسه مجاش")
        for data, message in (({"name": ""}, "اسم التحليل"), ({"name": "هيموجلوبين"}, "موجود"), ({"name": "X", "low": "10", "high": "5"}, "أكبر"),
                              ({"name": "Y", "low": "abc"}, "مش رقم")):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, message):
                self.lab.save_test(data, self.owner)

    def test_who_may_do_what_and_only_for_a_lab(self):
        from settings_core.models import ClientProfile

        self.assertContains(self.client.get(reverse("medical:file", args=[self.patient.pk])), "data-lab-link")
        self.assertContains(self.client.get(reverse("medical:patients")), reverse("medical:lab_tests"))
        cashier = person(RoleCode.CASHIER, "lab_cashier")
        self.client.force_login(cashier)
        for url in (self.url, reverse("medical:lab_tests")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url + "?lang=en").status_code, 200)
        ClientProfile.objects.update(sub_activity_slug="clinic")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse("medical:lab_tests")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("medical:file", args=[self.patient.pk])), "data-lab-link")


class SessionPackageTests(TestCase):
    """MED-005: physiotherapy session packages (HG-043)."""

    def setUp(self):
        from hesba_testing.factories import make_item, make_location
        from staff.services import save_employee

        from . import physio

        prepared_client("medical", "physio", CLINIC)
        self.physio = physio
        self.owner = person(RoleCode.OWNER, "ph_owner")
        make_location(location_code="CLINIC", is_default=True, is_selling_location=True)
        self.therapist = save_employee({"name": "د. ياسمين"}, self.owner)
        self.session = make_item(item_code="SRV-PHY", item_name="جلسة علاج طبيعي", is_stock_tracked=False, default_sale_price="350")
        self.patient = make_customer(customer_code="P-1", name="منى", phone="01001234567")
        self.client.force_login(self.owner)
        self.url = reverse("medical:packages", args=[self.patient.pk])

    def sold(self, sessions="3", price=""):
        from sales.services import post_sales_invoice

        package = self.physio.sell(self.patient, {"service": self.session, "sessions": sessions, "price": price}, self.owner)
        post_sales_invoice(package.invoice.pk, self.owner)
        package.invoice.refresh_from_db()
        return package

    def test_a_package_is_one_ordinary_invoice_and_its_sessions_never_go_below_zero(self):
        from parties.services import balance

        from .models import SessionPackage

        self.assertContains(self.client.get(reverse("medical:file", args=[self.patient.pk])), "data-packages-link")
        self.client.post(self.url, {"action": "sell", "service": self.session.pk, "sessions": "10", "price": "3,000"})
        package = SessionPackage.objects.get()
        self.assertEqual((package.invoice.status, package.invoice.total_amount, package.sessions), ("draft", Decimal("3000.00"), 10))
        self.assertEqual(balance("customer", self.patient), Decimal("0"))  # nothing on the account until it is posted
        with self.assertRaisesMessage(ValidationError, "مسودة"):
            self.physio.use(package, self.owner)
        from sales.services import post_sales_invoice

        post_sales_invoice(package.invoice.pk, self.owner)
        self.assertEqual(balance("customer", self.patient), Decimal("3000.00"))
        small = self.sold("2")
        self.assertEqual(small.price, Decimal("700.00"))  # blank price = sessions × the session's price
        for _ in range(2):
            self.client.post(self.url, {"action": "use", "package": small.pk, "therapist": self.therapist.pk})
        self.assertEqual(self.physio.state(small), {"used": 2, "left": 0, "status": "finished"})
        self.assertContains(self.client.post(self.url, {"action": "use", "package": small.pk}), "الباكدج خلص")
        self.assertEqual(self.physio.used(small), 2)
        self.assertEqual(balance("customer", self.patient), Decimal("3700.00"))  # using sessions moves no money

    def test_a_mistaken_session_is_cancelled_and_given_back(self):
        package = self.sold("1")
        session = self.physio.use(package, self.owner, therapist=self.therapist)
        self.client.post(self.url, {"action": "cancel", "session": session.pk})
        session.refresh_from_db()
        self.assertTrue(session.cancelled)
        self.assertEqual(self.physio.state(package)["left"], 1)
        with self.assertRaisesMessage(ValidationError, "اتلغت قبل كده"):
            self.physio.cancel_use(session, self.owner)
        self.assertTrue(AuditLog.objects.filter(action="cancel_package_session").exists())
        self.assertContains(self.client.get(self.url), f'data-session="{session.pk}" data-cancelled')

    def test_a_cancelled_invoice_voids_the_package(self):
        from sales.services import cancel_posted_sales_invoice

        package = self.sold("5")
        cancel_posted_sales_invoice(package.invoice.pk, self.owner, "غلط")
        package.invoice.refresh_from_db()
        self.assertEqual(self.physio.state(package)["status"], "void")
        with self.assertRaisesMessage(ValidationError, "اتلغت"):
            self.physio.use(package, self.owner)

    def test_a_returned_invoice_voids_the_package(self):
        from django.utils import timezone

        from sales.services import create_sales_return

        package = self.sold("5")
        line = package.invoice.lines.get()
        create_sales_return("SR-PK1", timezone.localdate(), package.invoice.pk, [{"source_line": line, "quantity": line.quantity}], "رجع فلوسه", self.owner)
        self.assertEqual(self.physio.state(package)["status"], "void")
        with self.assertRaisesMessage(ValidationError, "اترجعت"):
            self.physio.use(package, self.owner)

    def test_bad_input_and_who_may_do_what(self):
        for data, message in (({"service": None, "sessions": "3"}, "الخدمة"), ({"service": self.session, "sessions": "0"}, "عدد الجلسات"),
                              ({"service": self.session, "sessions": "x"}, "عدد الجلسات"), ({"service": self.session, "sessions": "3", "price": "-1"}, "سعر")):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, message):
                self.physio.sell(self.patient, data, self.owner)
        package = self.sold("2")
        with self.assertRaisesMessage(ValidationError, "لسه مجاش"):
            self.physio.use(package, self.owner, used_on=timezone.localdate() + timedelta(days=1))
        therapist_user = person(RoleCode.STOCK_KEEPER, "ph_therapist")
        self.therapist.user = therapist_user
        self.therapist.save()
        self.client.force_login(therapist_user)
        self.client.post(self.url, {"action": "use", "package": package.pk})
        self.assertEqual(self.physio.used(package), 1)  # a therapist records sessions
        self.assertNotContains(self.client.get(self.url), "data-sell-package")  # selling is the counter's
        self.assertEqual(self.client.post(self.url, {"action": "sell", "service": self.session.pk, "sessions": "3"}).status_code, 403)
        self.client.force_login(person(RoleCode.CASHIER, "ph_cashier"))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_only_for_physiotherapy(self):
        from settings_core.models import ClientProfile

        self.assertEqual(self.client.get(self.url + "?lang=en").status_code, 200)
        ClientProfile.objects.update(sub_activity_slug="clinic")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("medical:file", args=[self.patient.pk])), "data-packages-link")
