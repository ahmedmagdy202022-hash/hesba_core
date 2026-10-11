"""MED-008: medical insurance (HG-042, design A) — two ordinary invoices per insured visit, the monthly claim, rejections."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, make_location
from permissions.models import RoleCode
from permissions.services import user_has_permission
from reports.tests_dashboard import prepared_client

from . import insurance
from .models import InsuranceContract, InsuredBill, PatientCover
from .tests import CLINIC, person


def balance(customer):
    from parties.services import balance as party_balance

    return party_balance("customer", customer)


def ledger_ok(test):
    from ledger.reports import reconciliation

    result = reconciliation()
    test.assertTrue(result["balanced"])
    test.assertTrue(result["ok"], [row for row in result["rows"] if row["difference"]])


class InsuranceSetup(TestCase):
    def setUp(self):
        from staff.services import save_employee

        prepared_client("medical", "clinic", CLINIC)
        self.owner = person(RoleCode.OWNER, "ins_owner")
        make_location(location_code="CLINIC", is_default=True, is_selling_location=True)
        self.today = timezone.localdate()
        self.doctor = save_employee({"name": "د. سارة"}, self.owner)
        self.checkup = make_item(item_code="SRV-CHK", item_name="كشف", is_stock_tracked=False, default_sale_price="300")
        self.xray = make_item(item_code="SRV-XR", item_name="أشعة", is_stock_tracked=False, default_sale_price="500")
        self.mona = make_customer(customer_code="P-1", name="منى", phone="01001234567")
        self.contract = insurance.save_contract({"name": "شركة الأمان للتأمين", "coverage_percent": "80", "claim_day": "5"}, self.owner)
        self.insurer = self.contract.insurer
        insurance.set_cover(self.mona, {"contract": self.contract, "member_number": "AM-7781"}, self.owner)

    def appointment(self, patient=None, service=None, price="300", status="done"):
        from appointments.models import Appointment

        return Appointment.objects.create(number=f"AP-{Appointment.objects.count() + 1}", customer=patient or self.mona, employee=self.doctor,
                                          service=service or self.checkup, price=price, starts_at=timezone.now(), status=status, created_by=self.owner)

    def post(self, bill):
        from sales.services import post_sales_invoice

        for invoice in (bill.patient_invoice, bill.insurer_invoice):
            if invoice is not None:
                post_sales_invoice(invoice.pk, self.owner)
        bill.refresh_from_db()
        return bill


class SplitTests(TestCase):
    def test_the_shares_add_up_to_the_price_to_the_piastre(self):
        for price, percent in ((D("333.33"), D("70")), (D("100.01"), D("12.5")), (D("999.99"), D("33.33")), (D("0.05"), D("50")), (D("300"), D("100"))):
            insurer, patient = insurance.split(price, percent)
            self.assertEqual(insurer + patient, price, (price, percent))
            self.assertEqual(patient, (price * (100 - percent) / 100).quantize(D("0.01"), rounding=ROUND_HALF_UP))  # half up, like all money

    def test_the_rounding_piastre_goes_to_the_insurer(self):
        self.assertEqual(insurance.split(D("333.33"), D("70")), (D("233.33"), D("100.00")))  # the patient's 99.999 rounds to 100.00


class BillingTests(InsuranceSetup):
    def test_an_insured_visit_makes_two_draft_invoices_one_per_share(self):
        appointment = self.appointment(status="arrived")
        bill = insurance.bill_insured(appointment, self.owner)
        appointment.refresh_from_db()
        self.assertEqual((bill.insurer_share, bill.patient_share, bill.percent), (D("240.00"), D("60.00"), D("80.00")))
        self.assertEqual(bill.insurer_invoice.customer, self.insurer)
        self.assertEqual(bill.patient_invoice.customer, self.mona)
        self.assertEqual((bill.insurer_invoice.total_amount, bill.patient_invoice.total_amount), (D("240.00"), D("60.00")))
        self.assertEqual((bill.insurer_invoice.status, bill.patient_invoice.status), ("draft", "draft"))
        self.assertEqual(appointment.invoice, bill.patient_invoice)
        self.assertEqual(appointment.status, "done")
        self.assertEqual(bill.member_number, "AM-7781")
        self.assertIn("AM-7781", bill.insurer_invoice.lines.get().description)
        self.assertTrue(AuditLog.objects.filter(action="bill_insured_appointment", object_id=str(bill.pk)).exists())

    def test_a_billed_appointment_is_never_billed_again(self):
        from appointments.services import bill as ordinary_bill

        appointment = self.appointment()
        bill = insurance.bill_insured(appointment, self.owner)
        with self.assertRaises(ValidationError):
            insurance.bill_insured(appointment, self.owner)
        self.assertEqual(ordinary_bill(appointment, self.owner), bill.patient_invoice)  # the ordinary bill returns what is there
        self.assertEqual(InsuredBill.objects.count(), 1)

    def test_posting_moves_each_customer_by_exactly_its_share(self):
        before_patient, before_insurer = balance(self.mona), balance(self.insurer)
        self.post(insurance.bill_insured(self.appointment(), self.owner))
        self.assertEqual(balance(self.mona) - before_patient, D("60.00"))
        self.assertEqual(balance(self.insurer) - before_insurer, D("240.00"))
        self.assertEqual(insurance.outstanding(self.contract), D("240.00"))
        ledger_ok(self)

    def test_the_insurer_pays_through_an_ordinary_collection(self):
        from hesba_testing.factories import make_cashbox
        from sales.services import record_customer_payment

        self.post(insurance.bill_insured(self.appointment(), self.owner))
        cashbox = make_cashbox(cashbox_code="MAIN", is_default=True)
        record_customer_payment("CP-INS-1", self.today, self.insurer, cashbox, D("240.00"), self.owner)
        self.assertEqual(insurance.outstanding(self.contract), D("0"))
        ledger_ok(self)

    def test_full_cover_makes_only_the_insurers_invoice(self):
        self.contract.coverage_percent = D("100")
        self.contract.save()
        appointment = self.appointment()
        bill = insurance.bill_insured(appointment, self.owner)
        appointment.refresh_from_db()
        self.assertIsNone(bill.patient_invoice)
        self.assertEqual(appointment.invoice, bill.insurer_invoice)
        self.assertEqual(bill.insurer_invoice.total_amount, D("300.00"))

    def test_a_service_can_have_its_own_share_or_none(self):
        insurance.set_service_percent(self.contract, self.xray, "50", self.owner)
        bill = insurance.bill_insured(self.appointment(service=self.xray, price="500"), self.owner)
        self.assertEqual((bill.insurer_share, bill.patient_share), (D("250.00"), D("250.00")))
        insurance.set_service_percent(self.contract, self.xray, "0", self.owner)
        with self.assertRaises(ValidationError):
            insurance.bill_insured(self.appointment(service=self.xray, price="500"), self.owner)
        insurance.set_service_percent(self.contract, self.xray, "", self.owner)  # cleared: the contract's 80% again
        self.assertEqual(insurance.percent_for(self.contract, self.xray), D("80.00"))

    def test_no_cover_an_expired_cover_or_an_inactive_insurer_is_refused(self):
        ali = make_customer(customer_code="P-2", name="علي")
        with self.assertRaises(ValidationError):
            insurance.bill_insured(self.appointment(patient=ali), self.owner)
        PatientCover.objects.filter(patient=self.mona).update(expires_on=self.today - timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, (self.today - timedelta(days=1)).isoformat()):
            insurance.bill_insured(self.appointment(), self.owner)
        PatientCover.objects.filter(patient=self.mona).update(expires_on=None)
        InsuranceContract.objects.filter(pk=self.contract.pk).update(active=False)
        with self.assertRaises(ValidationError):
            insurance.bill_insured(self.appointment(), self.owner)
        self.assertFalse(InsuredBill.objects.exists())

    def test_a_booked_appointment_is_not_billed(self):
        with self.assertRaises(ValidationError):
            insurance.bill_insured(self.appointment(status="booked"), self.owner)

    def test_contract_and_cover_rules(self):
        with self.assertRaises(ValidationError):
            insurance.save_contract({"name": "صفر", "coverage_percent": "0"}, self.owner)
        with self.assertRaises(ValidationError):
            insurance.save_contract({"name": "كتير", "coverage_percent": "101"}, self.owner)
        with self.assertRaises(ValidationError):
            insurance.save_contract({"name": "يوم", "coverage_percent": "50", "claim_day": "31"}, self.owner)
        with self.assertRaises(ValidationError):
            insurance.save_contract({"insurer": self.insurer, "coverage_percent": "50"}, self.owner)  # one contract per company
        with self.assertRaises(ValidationError):
            insurance.set_cover(self.insurer, {"contract": self.contract}, self.owner)  # the insurer is never its own patient
        insurance.set_cover(self.mona, {"contract": None}, self.owner)
        self.assertFalse(PatientCover.objects.filter(patient=self.mona).exists())


class ClaimTests(InsuranceSetup):
    def test_the_claim_lists_only_this_insurers_posted_invoices_in_the_month(self):
        first = self.today.replace(day=1)
        posted = self.post(insurance.bill_insured(self.appointment(), self.owner))
        insurance.bill_insured(self.appointment(), self.owner)  # a draft: not claimed yet
        other = insurance.save_contract({"name": "مصر للتأمين", "coverage_percent": "90"}, self.owner)
        ali = make_customer(customer_code="P-2", name="علي")
        insurance.set_cover(ali, {"contract": other}, self.owner)
        self.post(insurance.bill_insured(self.appointment(patient=ali), self.owner))  # another insurer
        last_month = insurance.bill_insured(self.appointment(), self.owner)
        last_month.insurer_invoice.invoice_date = first - timedelta(days=3)
        last_month.insurer_invoice.save(update_fields=["invoice_date"])
        self.post(last_month)
        data = insurance.claim(self.contract, *insurance.month_bounds(""))
        self.assertEqual([row["invoice"].pk for row in data["rows"]], [posted.insurer_invoice_id])
        self.assertEqual((data["totals"]["count"], data["totals"]["claimed"], data["totals"]["net"], data["totals"]["patient"]),
                         (1, D("240.00"), D("240.00"), D("60.00")))
        previous = insurance.claim(self.contract, *insurance.month_bounds((first - timedelta(days=3)).strftime("%Y-%m")))
        self.assertEqual([row["invoice"].pk for row in previous["rows"]], [last_month.insurer_invoice_id])

    def test_a_rejection_is_a_sales_return_on_the_insurers_invoice(self):
        from sales.services import create_sales_return

        bill = self.post(insurance.bill_insured(self.appointment(), self.owner))
        before = balance(self.insurer)
        line = bill.insurer_invoice.lines.get()
        create_sales_return("SR-INS-1", self.today, bill.insurer_invoice.pk, [{"source_line": line, "quantity": D("1")}], "رفض الشركة", self.owner)
        self.assertEqual(before - balance(self.insurer), D("240.00"))
        first, last = insurance.month_bounds("")
        totals = insurance.claim(self.contract, first, last)["totals"]
        self.assertEqual((totals["claimed"], totals["rejected"], totals["net"]), (D("240.00"), D("240.00"), D("0.00")))
        self.assertEqual(balance(self.mona), D("60.00"))  # the patient's share is untouched
        ledger_ok(self)

    def test_a_visit_with_one_invoice_cancelled_is_flagged(self):
        from sales.services import cancel_posted_sales_invoice

        bill = self.post(insurance.bill_insured(self.appointment(), self.owner))
        self.assertEqual(insurance.unpaired(), [])
        cancel_posted_sales_invoice(bill.insurer_invoice.pk, self.owner, "غلط")
        self.assertEqual([row.pk for row in insurance.unpaired()], [bill.pk])
        cancel_posted_sales_invoice(bill.patient_invoice.pk, self.owner, "غلط")
        self.assertEqual(insurance.unpaired(), [])

    def test_next_claim_day(self):
        from datetime import date

        self.assertEqual(insurance.next_claim_day(self.contract, date(2026, 10, 3)), date(2026, 10, 5))
        self.assertEqual(insurance.next_claim_day(self.contract, date(2026, 10, 6)), date(2026, 11, 5))
        self.assertEqual(insurance.next_claim_day(self.contract, date(2026, 12, 20)), date(2027, 1, 5))


class ReportTests(InsuranceSetup):
    def test_revenue_per_doctor_and_service_adds_both_invoices(self):
        from appointments.services import performance

        from . import insights

        self.post(insurance.bill_insured(self.appointment(), self.owner))
        data = insights.period_report(self.today.replace(day=1), self.today)
        self.assertEqual({row["doctor"].pk: row["revenue"] for row in data["doctors"]}, {self.doctor.pk: D("300.00")})
        self.assertEqual({row["service"].pk: (row["count"], row["revenue"]) for row in data["services"]}, {self.checkup.pk: (1, D("300.00"))})
        rows = {row["employee"].pk: row["sales"] for row in performance(self.today.replace(day=1), self.today)}
        self.assertEqual(rows[self.doctor.pk], D("300.00"))

    def test_only_the_posted_invoice_counts_while_the_other_is_a_draft(self):
        from sales.services import post_sales_invoice

        from . import insights

        bill = insurance.bill_insured(self.appointment(), self.owner)
        post_sales_invoice(bill.insurer_invoice.pk, self.owner)  # the patient's is still a draft
        data = insights.period_report(self.today.replace(day=1), self.today)
        self.assertEqual(data["totals"]["revenue"], D("240.00"))


class InsuranceScreenTests(InsuranceSetup):
    def test_the_pages_open_and_a_new_company_is_a_customer(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("medical:insurance"))
        self.assertContains(page, "شركة الأمان للتأمين")
        self.assertContains(page, "data-tab-insurance")
        response = self.client.post(reverse("medical:insurance"), {"name": "بوبا", "coverage_percent": "75", "claim_day": "10"})
        contract = InsuranceContract.objects.get(insurer__name="بوبا")
        self.assertRedirects(response, f"{reverse('medical:insurance_contract', args=[contract.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertTrue(contract.insurer.customer_code.startswith("C-"))
        self.assertEqual(self.client.get(reverse("medical:insurance_contract", args=[contract.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("medical:insurance_claim", args=[contract.pk]) + "?month=2026-01").status_code, 200)
        self.assertNotContains(self.client.get(reverse("medical:patients")), "بوبا")  # an insurer is not a patient

    def test_the_contract_page_sets_a_services_share(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("medical:insurance_contract", args=[self.contract.pk]), {"action": "service", "service": self.xray.pk, "percent": "40"})
        self.assertEqual(insurance.percent_for(self.contract, self.xray), D("40.00"))

    def test_the_patient_file_sets_the_cover_and_the_appointment_bills_with_it(self):
        self.client.force_login(self.owner)
        ali = make_customer(customer_code="P-2", name="علي")
        file_page = self.client.get(reverse("medical:file", args=[ali.pk]))
        self.assertContains(file_page, "data-cover-form")
        self.client.post(reverse("medical:patient_cover", args=[ali.pk]), {"contract": self.contract.pk, "member_number": "AM-1"})
        self.assertEqual(PatientCover.objects.get(patient=ali).member_number, "AM-1")
        appointment = self.appointment(patient=ali)
        detail = self.client.get(reverse("appointments:detail", args=[appointment.pk]))
        self.assertContains(detail, "data-appointment-bill-insured")
        self.assertContains(detail, "240.00")
        self.client.post(reverse("appointments:detail", args=[appointment.pk]), {"action": "bill_insured"})
        bill = InsuredBill.objects.get(appointment=appointment)
        detail = self.client.get(reverse("appointments:detail", args=[appointment.pk]))
        self.assertContains(detail, bill.insurer_invoice.invoice_number)
        self.assertContains(detail, bill.patient_invoice.invoice_number)
        self.assertNotContains(detail, "data-appointment-bill-insured")

    def test_without_the_sales_permission_nothing_opens_or_changes(self):
        keeper = person(RoleCode.STOCK_KEEPER, "ins_keeper")
        self.assertFalse(user_has_permission(keeper, insurance.VIEW))
        self.assertFalse(user_has_permission(keeper, insurance.MANAGE))
        self.client.force_login(keeper)
        self.assertEqual(self.client.get(reverse("medical:insurance")).status_code, 403)
        self.assertEqual(self.client.get(reverse("medical:insurance_contract", args=[self.contract.pk])).status_code, 403)
        with self.assertRaises(PermissionDenied):
            insurance.save_contract({"name": "x", "coverage_percent": "50"}, keeper)
        with self.assertRaises(PermissionDenied):
            insurance.bill_insured(self.appointment(), keeper)
        with self.assertRaises(PermissionDenied):
            insurance.set_service_percent(self.contract, self.xray, "10", keeper)

    def test_a_vet_or_a_shop_has_no_insurance(self):
        from settings_core.models import ClientProfile

        self.client.force_login(self.owner)
        ClientProfile.objects.update(sub_activity_slug="vet")
        self.assertEqual(self.client.get(reverse("medical:insurance")).status_code, 404)
        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.assertEqual(self.client.get(reverse("medical:insurance")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("appointments:detail", args=[self.appointment().pk])), "data-appointment-bill-insured")
