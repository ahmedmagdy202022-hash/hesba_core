"""MED-009: medical insurance on a patient's done dental steps and on a physio package (two ordinary invoices, as for a visit)."""

from decimal import Decimal as D
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_customer, make_item, make_location
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import insurance
from .models import InsuredBill
from .tests import CLINIC, person
from .tests_insurance import balance, ledger_ok


def post(*invoices):
    from sales.services import post_sales_invoice

    for invoice in invoices:
        if invoice is not None:
            post_sales_invoice(invoice.pk, invoices[0].created_by)


class Insured(TestCase):
    sub = "dental"

    def setUp(self):
        prepared_client("medical", self.sub, CLINIC)
        self.owner = person(RoleCode.OWNER, f"ins_{self.sub}_owner")
        make_location(location_code="CLINIC", is_default=True, is_selling_location=True)
        self.patient = make_customer(customer_code="P-1", name="منى", phone="01001234567")
        self.contract = insurance.save_contract({"name": "شركة الأمان للتأمين", "coverage_percent": "80", "claim_day": "5"}, self.owner)
        self.insurer = self.contract.insurer
        insurance.set_cover(self.patient, {"contract": self.contract, "member_number": "AM-7781"}, self.owner)
        self.client.force_login(self.owner)


class DentalTests(Insured):
    def setUp(self):
        from . import dental

        super().setUp()
        self.dental = dental
        self.filling = make_item(item_code="SRV-FIL", item_name="حشو عصب", is_stock_tracked=False, default_sale_price="800")
        self.whitening = make_item(item_code="SRV-WHT", item_name="تبييض", is_stock_tracked=False, default_sale_price="1000")
        self.url = reverse("medical:dental", args=[self.patient.pk])

    def done(self, service, tooth=None, price=None):
        step = self.dental.plan_step(self.patient, {"service": service, "tooth": tooth, "price": price}, self.owner)
        return self.dental.complete_step(step, self.owner)

    def test_done_steps_make_the_insurers_invoice_and_the_patients_share(self):
        insurance.set_service_percent(self.contract, self.whitening, "0", self.owner)  # whitening is not covered
        first, second = self.done(self.filling, 16), self.done(self.whitening)
        bill = self.dental.bill_done_insured(self.patient, self.owner)
        self.assertEqual((bill.kind, bill.patient, bill.appointment, bill.member_number), ("dental", self.patient, None, "AM-7781"))
        self.assertEqual((bill.price, bill.insurer_share, bill.patient_share, bill.percent), (D("1800.00"), D("640.00"), D("1160.00"), D("80.00")))
        insurer_lines = list(bill.insurer_invoice.lines.values_list("unit_sale_price", flat=True))
        patient_lines = sorted(bill.patient_invoice.lines.values_list("unit_sale_price", flat=True))
        self.assertEqual((bill.insurer_invoice.customer, insurer_lines), (self.insurer, [D("640.00")]))  # only the covered step is claimed
        self.assertEqual((bill.patient_invoice.customer, patient_lines), (self.patient, [D("160.00"), D("1000.00")]))
        self.assertEqual({bill.insurer_invoice.status, bill.patient_invoice.status}, {"draft"})
        first.refresh_from_db(), second.refresh_from_db()
        self.assertEqual({first.invoice_id, second.invoice_id}, {bill.patient_invoice_id})
        self.assertFalse(self.dental.unbilled(self.patient).exists())
        with self.assertRaises(ValidationError):  # a second click bills nothing again
            self.dental.bill_done_insured(self.patient, self.owner)
        post(bill.insurer_invoice, bill.patient_invoice)
        self.assertEqual((balance(self.insurer), balance(self.patient)), (D("640.00"), D("1160.00")))
        ledger_ok(self)
        first_day, last_day = insurance.month_bounds("")
        rows = insurance.claim(self.contract, first_day, last_day)["rows"]
        self.assertEqual([(row["patient"], row["claimed"]) for row in rows], [(self.patient, D("640.00"))])
        self.assertIn("16", rows[0]["what"])

    def test_at_full_cover_there_is_no_patients_invoice(self):
        insurance.save_contract({"coverage_percent": "100", "claim_day": "5"}, self.owner, contract=self.contract)
        step = self.done(self.filling, 21)
        bill = self.dental.bill_done_insured(self.patient, self.owner)
        step.refresh_from_db()
        self.assertEqual((bill.patient_invoice, bill.patient_share, step.invoice_id), (None, D("0.00"), bill.insurer_invoice_id))

    def test_nothing_covered_or_no_insurance_makes_nothing(self):
        from sales.models import SalesInvoice

        insurance.set_service_percent(self.contract, self.whitening, "0", self.owner)
        self.done(self.whitening)
        with self.assertRaisesMessage(ValidationError, "مش مغطاة"):
            self.dental.bill_done_insured(self.patient, self.owner)
        insurance.set_cover(self.patient, {"contract": self.contract, "expires_on": "2020-01-01"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "2020-01-01"):
            self.dental.bill_done_insured(self.patient, self.owner)
        insurance.set_cover(self.patient, {}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مش عليه تأمين"):
            self.dental.bill_done_insured(self.patient, self.owner)
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertTrue(self.dental.unbilled(self.patient).exists())  # the ordinary invoice is still there to make

    def test_a_cancelled_patients_share_never_claims_from_the_insurer_twice(self):
        from sales.services import cancel_posted_sales_invoice

        self.done(self.filling, 16)
        bill = self.dental.bill_done_insured(self.patient, self.owner)
        post(bill.patient_invoice, bill.insurer_invoice)
        cancel_posted_sales_invoice(bill.patient_invoice.pk, self.owner, "غلط")
        self.assertFalse(self.dental.unbilled(self.patient).exists())  # the insurer's invoice still stands
        self.assertEqual(insurance.unpaired(), [bill])
        cancel_posted_sales_invoice(bill.insurer_invoice.pk, self.owner, "غلط")
        self.assertEqual(self.dental.unbilled(self.patient).count(), 1)  # both cancelled: it can be billed again

    def test_billing_through_insurance_needs_the_invoice_permission(self):
        self.done(self.filling, 16)
        keeper = person(RoleCode.STOCK_KEEPER, "dn_keeper")
        with self.assertRaises(PermissionDenied):
            self.dental.bill_done_insured(self.patient, keeper)
        self.assertFalse(InsuredBill.objects.exists())

    def test_the_chart_offers_the_insured_invoice_and_makes_it(self):
        self.done(self.filling, 16)
        page = self.client.get(self.url)
        self.assertContains(page, "data-bill-insured")
        self.assertContains(page, "فاتورة على التأمين (شركة الأمان للتأمين)")
        response = self.client.post(self.url, {"action": "bill_insured"}, follow=True)
        bill = InsuredBill.objects.get()
        self.assertContains(response, bill.insurer_invoice.invoice_number)
        self.assertContains(response, bill.patient_invoice.invoice_number)
        insurance.set_cover(self.patient, {}, self.owner)
        self.done(self.filling, 17)
        self.assertNotContains(self.client.get(self.url), "data-bill-insured")  # no insurance, no button

    def test_bills_made_before_kept_their_patient(self):
        """The migration fills the patient of the visits' bills made before MED-009."""

        from importlib import import_module

        from django.apps import apps

        from appointments.models import Appointment

        appointment = Appointment.objects.create(number="AP-1", customer=self.patient, service=self.filling, price="300",
                                                 starts_at=timezone.now(), status="done", created_by=self.owner)
        bill = insurance.bill_insured(appointment, self.owner)
        InsuredBill.objects.filter(pk=bill.pk).update(patient=None)
        import_module("medical.migrations.0009_insured_bill_kinds").fill_patients(apps, SimpleNamespace(connection=connection))
        bill.refresh_from_db()
        self.assertEqual((bill.kind, bill.patient), ("visit", self.patient))


class PhysioTests(Insured):
    sub = "physio"

    def setUp(self):
        from . import physio

        super().setUp()
        self.physio = physio
        self.session = make_item(item_code="SRV-PHY", item_name="جلسة علاج طبيعي", is_stock_tracked=False, default_sale_price="300")
        self.url = reverse("medical:packages", args=[self.patient.pk])

    def test_an_insured_package_rides_on_the_patients_share(self):
        package = self.physio.sell_insured(self.patient, {"service": self.session, "sessions": "10"}, self.owner)
        bill = InsuredBill.objects.get()
        self.assertEqual((bill.kind, bill.patient, bill.insurer_share, bill.patient_share), ("package", self.patient, D("2400.00"), D("600.00")))
        self.assertEqual(package.invoice, bill.patient_invoice)
        self.assertEqual(package.price, D("3000.00"))
        self.assertEqual(self.physio.state(package)["status"], "draft")
        post(bill.patient_invoice, bill.insurer_invoice)
        package.refresh_from_db()
        self.assertEqual(self.physio.state(package), {"used": 0, "left": 10, "status": "active"})
        self.assertEqual((balance(self.insurer), balance(self.patient)), (D("2400.00"), D("600.00")))
        ledger_ok(self)
        first_day, last_day = insurance.month_bounds("")
        self.assertEqual(insurance.claim(self.contract, first_day, last_day)["totals"]["net"], D("2400.00"))

    def test_without_insurance_nothing_is_sold(self):
        from agreements.models import Package

        insurance.set_cover(self.patient, {}, self.owner)
        with self.assertRaises(ValidationError):
            self.physio.sell_insured(self.patient, {"service": self.session, "sessions": "10"}, self.owner)
        self.assertFalse(Package.objects.exists())

    def test_the_package_page_sells_through_insurance_when_ticked(self):
        page = self.client.get(self.url)
        self.assertContains(page, "data-sell-insured")
        response = self.client.post(self.url, {"action": "sell", "service": self.session.pk, "sessions": "10", "insured": "1"}, follow=True)
        bill = InsuredBill.objects.get()
        self.assertContains(response, bill.insurer_invoice.invoice_number)
        self.client.post(self.url, {"action": "sell", "service": self.session.pk, "sessions": "5"})  # not ticked: the ordinary package
        self.assertEqual(InsuredBill.objects.count(), 1)
