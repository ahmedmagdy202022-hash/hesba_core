"""M8 (HG-088): the demo's beds — two wards, a patient in a bed, and a discharged stay with its draft bill, all through the services."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from medical import inpatient
from medical.models import Admission, AdmissionState, Bed, ChargeKind, Ward
from reports.demo_catalogs import catalog_for
from sales.models import SalesInvoice
from settings_core.capabilities import capability_enabled


class DemoInpatientTests(TestCase):
    def fill(self, activity, sub):
        """A demo template of one kind of business, built as ``prepare_demo_templates`` builds it."""

        from config.demo_templates import complete_demo_setup
        from settings_core.management.commands.prepare_demo import Command as Prepare

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_demo_setup(activity, sub)
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        prepare = Prepare()
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._spread_sale_times()
        prepare._extras()
        return catalog_for(activity, sub)

    def assert_beds(self):
        today = timezone.localdate()
        self.assertTrue(capability_enabled("inpatient"))
        self.assertEqual(list(Ward.objects.values_list("name_ar", flat=True)), ["الدور الأول", "رعاية اليوم الواحد"])
        self.assertEqual((Bed.objects.count(), Bed.objects.filter(active=False).count()), (5, 1))
        current = Admission.objects.get(state=AdmissionState.ADMITTED)
        self.assertEqual((current.bed.code, timezone.localtime(current.admitted_at).date(), inpatient.stay_day(current)), ("101", today - timedelta(days=2), 3))
        self.assertGreaterEqual(current.charges.count(), 1)  # a couple of charges so far, none billed, no bed-day written yet
        self.assertFalse(current.charges.filter(invoice__isnull=False).exists())
        self.assertEqual(len(inpatient.pending_bed_days(current)), 2)
        ended = Admission.objects.get(state=AdmissionState.DISCHARGED)
        invoice = inpatient.invoices(ended).get()
        # Two nights in the private room (2 × 1200) and the operation (3500); a draft, nothing posted for it.
        self.assertEqual((invoice.status, invoice.subtotal, invoice.paid_now), ("draft", D("5900.00"), D("0")))
        self.assertEqual(sorted(ended.charges.values_list("kind", "price")), [("bed_day", D("1200.00")), ("bed_day", D("1200.00")), ("operation", D("3500.00"))])
        self.assertEqual(ended.charges.filter(kind=ChargeKind.BED_DAY, invoice=invoice).count(), 2)
        self.assertFalse(invoice.customer.ledger_entries.filter(sales_invoice=invoice).exists())
        return current, ended, invoice

    def test_the_demo_clinic_has_beds_a_patient_in_one_and_a_stay_with_its_draft_bill(self):
        from master_data.models import Customer, Item
        from settings_core import demo_inpatient

        catalog = self.fill("medical", "clinic")
        current, ended, invoice = self.assert_beds()
        drafts = SalesInvoice.objects.filter(status="draft").count()
        master = {"customers": list(Customer.objects.order_by("pk")[:3]), "items": list(Item.objects.all())}
        self.assertTrue(demo_inpatient.applies(catalog))
        self.assertFalse(demo_inpatient.seed(master, current.doctor, current.created_by, catalog, {"patients"}))  # a second run adds nothing
        self.assertEqual((Ward.objects.count(), Admission.objects.count(), SalesInvoice.objects.filter(status="draft").count()), (2, 2, drafts))

    def test_a_medical_centre_gets_them_from_its_own_preset(self):
        self.fill("medical", "medical_center")
        self.assert_beds()

    def test_a_business_without_beds_gets_none(self):
        self.fill("medical", "dental")
        self.assertFalse(capability_enabled("inpatient"))
        self.assertEqual((Ward.objects.count(), Admission.objects.count()), (0, 0))

    @override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
    def test_the_owner_opens_the_board_the_stay_and_the_tour_step(self):
        from django.contrib.auth import get_user_model

        self.fill("medical", "clinic")
        owner = get_user_model().objects.get(username="owner")
        self.client.force_login(owner)
        board = self.client.get(reverse("medical:beds"))
        self.assertContains(board, 'data-state="occupied"')
        self.assertContains(board, 'data-state="out"')
        current = Admission.objects.get(state=AdmissionState.ADMITTED)
        page = self.client.get(reverse("medical:stay", args=[current.pk]) + "?review=1")
        self.assertContains(page, "data-review")
        ended = Admission.objects.get(state=AdmissionState.DISCHARGED)
        self.assertContains(self.client.get(reverse("medical:stay", args=[ended.pk])), "data-open-invoice")
        dashboard = self.client.get("/dashboard/?lang=ar").content.decode()
        self.assertEqual(dashboard.count("data-demo-tour-step="), 4)
        self.assertIn(reverse("medical:beds"), dashboard)
        self.assertIn("fr-steps--4", dashboard)
