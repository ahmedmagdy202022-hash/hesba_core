"""S5 (HG-069): a deposit at booking, applied to the service invoice, forfeited as a fee or refunded under approval.

Every test asserts the money: the customer's ledger, the cashbox, the ledger's 2105 (customer advances), 1103
(receivables), 4101 (revenue) and 2103 (output VAT), and the four "due" totals the deposit must not double count.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import services as appointments
from appointments.models import AppointmentStatus
from approvals.models import ApprovalRequest, ApprovalStatus
from approvals.services import approve, set_held_actions
from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from closing.models import PeriodStatus
from closing.services import get_period_for_date
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from inventory.models import StockMovement
from ledger import reports as ledger_reports
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.digest import daily_digest
from reports.functional_views import _posted_totals
from reports.reps import rep_performance
from reports.tests_dashboard import prepared_client
from sales.models import CustomerPayment, SalesInvoice
from sales.pos import WALK_IN_CODE, today_summary
from sales.services import cancel_customer_payment, cancel_posted_sales_invoice, post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from settlements.models import InvoiceSettlement
from staff.services import save_employee
from taxes.models import SalesLineTax

from . import services
from .models import Deposit, DepositStatus


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def customer_balance(customer):
    agg = customer.ledger_entries.aggregate(i=Sum("due_increase"), d=Sum("due_decrease"))
    return customer.opening_balance + (agg["i"] or D("0")) - (agg["d"] or D("0"))


def cash_balance(cashbox):
    moves = CashboxMovement.objects.filter(cashbox=cashbox)
    return (moves.filter(direction="in").aggregate(t=Sum("amount"))["t"] or D("0")) - (moves.filter(direction="out").aggregate(t=Sum("amount"))["t"] or D("0"))


def bal(code, day=None):
    """Debit-positive balance of a ledger account up to ``day`` (a liability shows negative)."""

    Projector().rebuild()
    rows = JournalLine.objects.filter(account__code=code)
    if day is not None:
        rows = rows.filter(entry__entry_date__lte=day)
    agg = rows.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


class DepositSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="services", sub_activity="maintenance",
                                       modules="customers,items_services,sales_operations,cashboxes,reports,employees_technicians,appointments_visits,pdf_printing")
        self.owner = person(RoleCode.OWNER, "dep_owner")
        self.cashier = person(RoleCode.CASHIER, "dep_cashier")
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cashbox = make_cashbox(cashbox_code="DEP-CASH", is_default=True)
        self.customer = make_customer(customer_code="C-DEP", name="مدام سعاد", phone="01001234567")
        self.service = make_item(item_code="SRV-AC", item_name="صيانة تكييف", default_sale_price="300.00", is_stock_tracked=False)
        self.tech = save_employee({"name": "أحمد الفني", "commission_percent": "10"}, self.owner)

    def book(self, starts_in=timedelta(days=3), customer=None, **extra):
        data = {"customer": customer or self.customer, "employee": self.tech, "service": self.service,
                "starts_at": timezone.now() + starts_in, "duration_minutes": 60}
        data.update(extra)
        return appointments.book(data, self.owner)

    def take(self, appointment, amount="100.00", user=None, **extra):
        return services.take(appointment, appointment.customer, self.cashbox, D(amount), user or self.owner, **extra)

    def bill_and_post(self, appointment, user=None):
        appointments.set_status(appointment, AppointmentStatus.ARRIVED, self.owner)
        invoice = appointments.bill(appointment, user or self.owner)
        post_sales_invoice(invoice.pk, user or self.owner)
        invoice.refresh_from_db()
        return invoice

    def detail_url(self, appointment, lang="ar"):
        return f"{reverse('appointments:detail', args=[appointment.pk])}?lang={lang}"


class TakeTests(DepositSetup):
    def test_a_deposit_lowers_the_customer_due_and_adds_cash_in_2105(self):
        appointment = self.book()
        deposit = self.take(appointment, "100.00")
        payment = deposit.payment
        self.assertEqual((deposit.status, deposit.target, deposit.amount), (DepositStatus.HELD, appointment, D("100.00")))
        self.assertTrue(payment.payment_number.startswith(f"DEP-{TODAY:%Y%m%d}-"))
        self.assertEqual(payment.status, "posted")
        self.assertEqual(customer_balance(self.customer), D("-100.00"))
        self.assertEqual(cash_balance(self.cashbox), D("100.00"))
        self.assertEqual(bal("2105"), D("-100.00"))  # the customer's money is a liability, not negative receivables
        self.assertEqual(bal("1103"), D("0.00"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assertTrue(AuditLog.objects.filter(module="deposits", action="take_deposit", object_id=str(deposit.pk)).exists())
        self.assertEqual(services.held_amounts([appointment]), {appointment.pk: D("100.00")})

    def test_refusals_in_both_languages(self):
        appointment = self.book()
        with self.assertRaisesMessage(ValidationError, "مبلغ العربون مش صحيح"):
            self.take(appointment, "0")
        with self.assertRaisesMessage(ValidationError, "Invalid deposit amount."):
            self.take(appointment, "-5", lang="en")
        walk_in = make_customer(customer_code=WALK_IN_CODE, name="عميل نقدي")
        with self.assertRaisesMessage(ValidationError, "العميل النقدي"):
            services.take(appointment, walk_in, self.cashbox, D("10"), self.owner)
        with self.assertRaisesMessage(ValidationError, "Choose the cashbox"):
            services.take(appointment, self.customer, None, D("10"), self.owner, lang="en")
        self.take(appointment, "50.00")
        with self.assertRaisesMessage(ValidationError, "عليه عربون بالفعل"):
            self.take(appointment, "50.00")
        done = self.book(starts_in=timedelta(days=4))
        appointments.set_status(done, AppointmentStatus.ARRIVED, self.owner)
        with self.assertRaisesMessage(ValidationError, "still booked or confirmed"):
            self.take(done, "50.00", lang="en")
        self.assertEqual(CustomerPayment.objects.count(), 1)  # nothing moved for any refusal

    def test_taking_a_deposit_needs_the_collection_permission(self):
        appointment = self.book()
        with self.assertRaises(PermissionDenied):
            self.take(appointment, "100.00", user=person(RoleCode.STOCK_KEEPER, "dep_keeper"))
        self.assertEqual(self.take(appointment, "100.00", user=self.cashier).created_by, self.cashier)


class ApplyTests(DepositSetup):
    def test_the_service_invoice_nets_the_deposit_without_touching_its_frozen_fields(self):
        appointment = self.book()
        deposit = self.take(appointment, "100.00")
        invoice = self.bill_and_post(appointment)
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.invoice), (DepositStatus.APPLIED, invoice))
        row = InvoiceSettlement.objects.get(invoice=invoice)
        self.assertEqual((row.customer_payment, row.amount, row.source), (deposit.payment, D("100.00"), "deposit"))
        # The posted document is untouched: credit for the full price, the ledger nets it.
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due, invoice.payment_status), (D("300.00"), D("0.00"), D("300.00"), "credit"))
        self.assertEqual(customer_balance(self.customer), D("200.00"))
        self.assertEqual(cash_balance(self.cashbox), D("100.00"))
        self.assertEqual(bal("2105"), D("0.00"))
        self.assertEqual(bal("1103"), D("200.00"))
        self.assertEqual(bal("2105", TODAY - timedelta(days=1)), D("0.00"))  # taken and applied today
        self.assertFalse(StockMovement.objects.filter(sales_invoice=invoice).exists())
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        # The four "due" totals subtract the deposit once and leave paid_now alone.
        totals = _posted_totals(SalesInvoice.objects.filter(pk=invoice.pk))
        self.assertEqual((totals["total"], totals["paid"], totals["applied"], totals["due"]), (D("300.00"), D("0.00"), D("100.00"), D("200.00")))
        digest = daily_digest(self.owner, TODAY)
        self.assertEqual((digest["sales"]["credit"], digest["sales"]["cash"], digest["sales"]["applied"]), (D("200.00"), D("0.00"), D("100.00")))
        self.assertEqual(digest["customers"]["collected"], D("100.00"))  # the deposit, counted as the payment it is
        summary = today_summary(self.owner)
        self.assertEqual((summary["credit"], summary["cash"], summary["applied"]), (D("200.00"), D("0.00"), D("100.00")))

    def test_the_invoice_page_and_print_show_paid_in_advance(self):
        appointment = self.book()
        self.take(appointment, "100.00")
        invoice = self.bill_and_post(appointment)
        self.client.force_login(self.owner)
        page = self.client.get(f"{reverse('sales:detail', args=[invoice.pk])}?lang=ar")
        self.assertContains(page, "data-invoice-applied")
        self.assertContains(page, "مدفوع مقدم")
        self.assertEqual(page.context["applied"], {"amount": D("100.00"), "remaining": D("200.00")})
        english = self.client.get(f"{reverse('sales:detail', args=[invoice.pk])}?lang=en")
        self.assertContains(english, "Paid in advance")
        printed = self.client.get(f"{reverse('printing:sales_invoice', args=[invoice.pk])}?lang=ar")
        self.assertContains(printed, "data-print-applied")
        self.assertContains(printed, "مدفوع مقدم (عربون)")
        self.assertEqual(printed.context["doc"]["paid"], D("0.00"))  # paid_now stays what it is
        self.assertEqual(printed.context["doc"]["to_collect"], D("200.00"))

    def test_rep_report_and_digest_count_the_deposit_once(self):
        rep = save_employee({"name": "سمير المندوب", "commission_percent": "5"}, self.owner)
        self.customer.sales_rep = rep
        self.customer.save()
        day_one = TODAY - timedelta(days=2)
        appointment = self.book()
        self.take(appointment, "100.00", payment_date=day_one)
        invoice = self.bill_and_post(appointment)
        self.assertEqual(invoice.salesperson, rep)
        whole = {row["rep"]: row for row in rep_performance(day_one, TODAY)[0]}[rep]
        self.assertEqual((whole["collected"], whole["credit_sold"]), (D("100.00"), D("200.00")))  # the deposit once, on day one
        today_only = {row["rep"]: row for row in rep_performance(TODAY, TODAY)[0]}[rep]
        self.assertEqual((today_only["collected"], today_only["credit_sold"]), (D("0.00"), D("200.00")))  # collected on day one, not today
        self.assertEqual(daily_digest(self.owner, day_one)["customers"]["collected"], D("100.00"))
        today_digest = daily_digest(self.owner, TODAY)
        self.assertEqual((today_digest["sales"]["credit"], today_digest["customers"]["collected"]), (D("200.00"), D("0.00")))
        self.assertEqual(bal("2105", day_one), D("-100.00"))  # held on day one...
        self.assertEqual(bal("2105", TODAY), D("0.00"))  # ...and moved to receivables on the invoice date

    def test_a_deposit_larger_than_the_invoice_settles_it_and_leaves_the_rest_on_account(self):
        appointment = self.book(price="80")
        deposit = self.take(appointment, "100.00")
        invoice = self.bill_and_post(appointment)
        deposit.refresh_from_db()
        self.assertEqual(deposit.status, DepositStatus.APPLIED)
        self.assertEqual(InvoiceSettlement.objects.get(invoice=invoice).amount, D("80.00"))
        self.assertEqual(customer_balance(self.customer), D("-20.00"))
        self.assertEqual(_posted_totals(SalesInvoice.objects.filter(pk=invoice.pk))["due"], D("0.00"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_cancelling_the_applied_invoice_returns_the_deposit_to_2105(self):
        appointment = self.book()
        deposit = self.take(appointment, "100.00")
        invoice = self.bill_and_post(appointment)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط")
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.invoice), (DepositStatus.HELD, None))
        self.assertFalse(InvoiceSettlement.objects.filter(invoice=invoice).exists())
        self.assertEqual(customer_balance(self.customer), D("-100.00"))
        self.assertEqual(cash_balance(self.cashbox), D("100.00"))
        self.assertEqual(bal("2105", TODAY), D("-100.00"))
        self.assertEqual(bal("1103", TODAY), D("0.00"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assertTrue(AuditLog.objects.filter(module="deposits", action="release_deposit").exists())

    def test_an_applied_deposit_payment_cannot_be_cancelled_behind_the_invoice(self):
        appointment = self.book()
        deposit = self.take(appointment, "100.00")
        self.bill_and_post(appointment)
        with self.assertRaisesMessage(ValidationError, "cancel that invoice first"):
            cancel_customer_payment(deposit.payment_id, self.owner, "غلط")
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.payment.status), (DepositStatus.APPLIED, "posted"))


class ForfeitTests(DepositSetup):
    def setUp(self):
        super().setUp()
        set_capability_enabled(ClientProfile.get_active(), "vat", True, user=self.owner)

    def forfeit(self, amount, code):
        customer = make_customer(customer_code=code, name=f"عميل {code}")
        appointment = self.book(customer=customer, starts_in=timedelta(hours=1))
        deposit = self.take(appointment, amount)
        appointments.set_status(appointment, AppointmentStatus.CANCELLED, self.owner)
        invoice = services.forfeit(deposit, self.owner)
        deposit.refresh_from_db()
        return customer, deposit, invoice

    def test_fee_line_back_computes_the_tax_inclusive_amount(self):
        self.assertEqual(services.fee_line(D("100.00"), D("14")), (D("87.72"), D("100.00")))
        self.assertEqual(services.fee_line(D("99.99"), D("14")), (D("87.71"), D("99.99")))
        self.assertEqual(services.fee_line(D("100.03"), D("14")), (D("87.74"), D("100.02")))  # no exact line: the nearest below
        self.assertEqual(services.fee_line(D("50.00"), D("0")), (D("50.00"), D("50.00")))

    def test_a_late_cancel_posts_a_fee_invoice_equal_to_the_deposit_with_vat_inside(self):
        for amount, net, tax in (("100.00", "87.72", "12.28"), ("99.99", "87.71", "12.28")):
            with self.subTest(amount=amount):
                customer, deposit, invoice = self.forfeit(amount, f"C-{amount}")
                line = invoice.lines.get()
                line_tax = SalesLineTax.objects.get(line=line)
                self.assertEqual((invoice.status, invoice.total_amount, invoice.tax_amount, invoice.paid_now, invoice.cashbox), ("posted", D(amount), D(tax), D("0.00"), None))
                self.assertEqual((line.item.item_code, line.item.is_stock_tracked, line.unit_sale_price, line_tax.taxable_amount, line_tax.tax_amount), ("CANCEL-FEE", False, D(net), D(net), D(tax)))
                self.assertEqual((deposit.status, deposit.invoice, deposit.rounding_credit), (DepositStatus.FORFEITED, invoice, D("0.00")))
                self.assertEqual(InvoiceSettlement.objects.get(invoice=invoice).amount, D(amount))
                self.assertEqual(customer_balance(customer), D("0.00"))  # the deposit covers the whole fee
                self.assertFalse(StockMovement.objects.filter(sales_invoice=invoice).exists())
        self.assertEqual(cash_balance(self.cashbox), D("199.99"))  # the two deposits; the fees moved no cash
        self.assertEqual(bal("4101"), D("-175.43"))  # revenue 87.72 + 87.71
        self.assertEqual(bal("2103"), D("-24.56"))  # output VAT 12.28 × 2
        self.assertEqual(bal("2105"), D("0.00"))
        self.assertEqual(bal("1103"), D("0.00"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_an_amount_no_line_reaches_exactly_bills_a_piastre_less_and_keeps_the_credit(self):
        customer, deposit, invoice = self.forfeit("100.03", "C-ROUND")
        self.assertEqual((invoice.total_amount, invoice.tax_amount), (D("100.02"), D("12.28")))
        self.assertEqual((deposit.rounding_credit, customer_balance(customer)), (D("0.01"), D("-0.01")))
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_with_vat_off_the_fee_is_the_deposit_with_no_tax_row(self):
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        customer, deposit, invoice = self.forfeit("100.00", "C-NOVAT")
        self.assertEqual((invoice.total_amount, invoice.tax_amount), (D("100.00"), D("0.00")))
        self.assertFalse(SalesLineTax.objects.filter(line__invoice=invoice).exists())
        self.assertEqual(customer_balance(customer), D("0.00"))
        self.assertEqual(bal("4101"), D("-100.00"))

    def test_cancelling_the_fee_invoice_frees_the_deposit_and_a_forfeit_needs_the_sales_permission(self):
        customer, deposit, invoice = self.forfeit("100.00", "C-FREE")
        with self.assertRaisesMessage(ValidationError, "اتصرف فيه خلاص"):
            services.forfeit(deposit, self.owner)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط")
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.invoice, customer_balance(customer)), (DepositStatus.HELD, None, D("-100.00")))
        self.assertEqual(bal("2105"), D("-100.00"))
        with self.assertRaises(PermissionDenied):
            services.forfeit(deposit, person(RoleCode.STOCK_KEEPER, "fee_keeper"))
        again = services.forfeit(deposit, self.cashier)  # the cashier may sell, so may forfeit
        self.assertNotEqual(again.invoice_number, invoice.invoice_number)
        self.assertTrue(ledger_reports.reconciliation()["ok"])


class RefundTests(DepositSetup):
    def test_an_early_cancel_refunds_the_deposit(self):
        appointment = self.book()
        deposit = self.take(appointment, "100.00")
        with self.assertRaisesMessage(ValidationError, "Enter the refund reason"):
            services.refund(deposit, self.owner, "", lang="en")
        services.refund(deposit, self.owner, "العميل لغى بدري")
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.payment.status), (DepositStatus.REFUNDED, "cancelled"))
        self.assertEqual((customer_balance(self.customer), cash_balance(self.cashbox)), (D("0.00"), D("0.00")))
        self.assertEqual((bal("2105"), bal("1103")), (D("0.00"), D("0.00")))
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assertTrue(AuditLog.objects.filter(module="deposits", action="refund_deposit").exists())
        self.assertIsNone(services.held_for(appointment))
        self.take(appointment, "60.00")  # a refunded booking may hold a new deposit

    def test_a_refund_needs_the_collection_permission(self):
        deposit = self.take(self.book(), "100.00")
        with self.assertRaises(PermissionDenied):
            services.refund(deposit, person(RoleCode.STOCK_KEEPER, "ref_keeper"), "x")
        self.assertEqual(deposit.payment.status, "posted")

    def test_a_deposit_from_a_closed_month_cannot_be_refunded_but_can_be_forfeited(self):
        last_month = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=15)
        appointment = self.book()
        deposit = self.take(appointment, "100.00", payment_date=last_month)
        period = get_period_for_date(last_month)
        period.status, period.closed_at = PeriodStatus.CLOSED, timezone.now()
        period.save(update_fields=["status", "closed_at"])
        self.assertIn("اتقفل", services.refund_blocker(deposit, "ar"))
        self.assertIn("has closed", services.refund_blocker(deposit, "en"))
        with self.assertRaisesMessage(ValidationError, "شهر اتقفل"):
            services.refund(deposit, self.owner, "بدري")
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.payment.status, cash_balance(self.cashbox)), (DepositStatus.HELD, "posted", D("100.00")))
        appointments.set_status(appointment, AppointmentStatus.CANCELLED, self.owner)
        invoice = services.forfeit(deposit, self.owner)  # dated today, in the open month
        self.assertEqual((invoice.invoice_date, invoice.total_amount, customer_balance(self.customer)), (TODAY, D("100.00"), D("0.00")))


class ScreenTests(DepositSetup):
    """The minimal UI: take on the detail page, the cancel button runs the policy, the agenda shows the deposit."""

    def test_take_from_the_detail_page_and_see_it_on_the_agenda(self):
        appointment = self.book()
        self.client.force_login(self.cashier)
        page = self.client.get(self.detail_url(appointment))
        self.assertContains(page, "data-deposit-form")
        response = self.client.post(self.detail_url(appointment), {"lang": "ar", "action": "deposit", "amount": "150", "cashbox": str(self.cashbox.pk)}, follow=True)
        self.assertContains(response, "اتسجل عربون")
        self.assertContains(response, 'data-deposit-status="held"')
        self.assertNotContains(response, "data-deposit-form")  # one held deposit per booking
        self.assertEqual(customer_balance(self.customer), D("-150.00"))
        agenda = self.client.get(f"{reverse('appointments:agenda')}?lang=ar&day={timezone.localtime(appointment.starts_at).date().isoformat()}")
        self.assertContains(agenda, "data-agenda-deposit")
        self.assertContains(agenda, "150.00")
        bad = self.client.post(self.detail_url(appointment, "en"), {"lang": "en", "action": "deposit", "amount": "0", "cashbox": str(self.cashbox.pk)})
        self.assertContains(bad, "Invalid deposit amount.")
        again = self.client.post(self.detail_url(appointment, "en"), {"lang": "en", "action": "deposit", "amount": "10", "cashbox": str(self.cashbox.pk)})
        self.assertContains(again, "This booking already holds a deposit.")

    def test_taking_needs_the_collection_permission_and_the_policy_the_owner(self):
        appointment = self.book()
        keeper = person(RoleCode.STOCK_KEEPER, "ui_keeper")
        from permissions.services import user_has_permission

        self.assertFalse(user_has_permission(keeper, "sales.receive_customer_payment"))
        viewer = person(RoleCode.ACCOUNTANT, "ui_viewer")
        self.client.force_login(viewer)
        if user_has_permission(viewer, "sales.view_sales_invoices") and not user_has_permission(viewer, "sales.receive_customer_payment"):
            self.assertEqual(self.client.post(self.detail_url(appointment), {"lang": "ar", "action": "deposit", "amount": "10", "cashbox": str(self.cashbox.pk)}).status_code, 403)
        self.client.force_login(self.cashier)
        agenda = reverse("appointments:agenda")
        self.assertEqual(self.client.post(agenda, {"lang": "ar", "action": "policy", "hours": "48"}).status_code, 403)
        self.assertEqual(services.cancel_hours(), 24)
        self.client.force_login(self.owner)
        self.client.post(agenda, {"lang": "ar", "action": "policy", "hours": "48"})
        self.assertEqual(services.cancel_hours(), 48)
        response = self.client.post(agenda, {"lang": "en", "action": "policy", "hours": "-1"}, follow=True)
        self.assertContains(response, "The hours must be a number")
        self.assertEqual(services.cancel_hours(), 48)
        self.assertTrue(AuditLog.objects.filter(module="deposits", action="set_cancel_hours").exists())

    def test_a_late_cancel_from_the_screen_forfeits(self):
        appointment = self.book(starts_in=timedelta(hours=2))
        set_capability_enabled(ClientProfile.get_active(), "vat", True, user=self.owner)
        self.take(appointment, "100.00")
        self.client.force_login(self.owner)
        response = self.client.post(self.detail_url(appointment), {"lang": "ar", "action": "status", "status": "cancelled"}, follow=True)
        self.assertContains(response, "العميل لغى متأخر")
        self.assertContains(response, 'data-deposit-status="forfeited"')
        self.assertContains(response, "data-deposit-invoice")
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, AppointmentStatus.CANCELLED)
        fee = SalesInvoice.objects.get(invoice_number__startswith="FEE-")
        self.assertEqual((fee.total_amount, fee.tax_amount, customer_balance(self.customer)), (D("100.00"), D("12.28"), D("0.00")))

    def test_a_no_show_forfeits_whatever_the_hours(self):
        appointment = self.book(starts_in=timedelta(days=10))
        self.take(appointment, "100.00")
        self.client.force_login(self.owner)
        response = self.client.post(self.detail_url(appointment, "en"), {"lang": "en", "action": "status", "status": "no_show"}, follow=True)
        self.assertContains(response, "Late cancellation")
        self.assertEqual(customer_balance(self.customer), D("0.00"))

    def test_an_early_cancel_from_the_screen_refunds_or_waits_for_approval(self):
        appointment = self.book(starts_in=timedelta(days=5))
        deposit = self.take(appointment, "100.00")
        self.client.force_login(self.owner)
        response = self.client.post(self.detail_url(appointment), {"lang": "ar", "action": "status", "status": "cancelled"}, follow=True)
        self.assertContains(response, "العربون اترد للعميل")
        self.assertContains(response, 'data-deposit-status="refunded"')
        self.assertEqual((customer_balance(self.customer), cash_balance(self.cashbox)), (D("0.00"), D("0.00")))
        # HG-068: when the owner holds cancellations of collections, a cashier's refund waits for a second person.
        set_held_actions(["sales.cancel_payment"], self.owner)
        second = self.book(starts_in=timedelta(days=5), customer=make_customer(customer_code="C-2", name="تاني"))
        deposit = self.take(second, "70.00", user=self.cashier)
        self.client.force_login(self.cashier)
        response = self.client.post(self.detail_url(second), {"lang": "ar", "action": "status", "status": "cancelled"}, follow=True)
        self.assertContains(response, "محتاجة موافقة")
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.payment.status), (DepositStatus.HELD, "posted"))
        request = ApprovalRequest.objects.get(action="sales.cancel_payment", object_id=deposit.payment_id, status=ApprovalStatus.PENDING)
        approve(request.pk, self.owner)
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.payment.status), (DepositStatus.REFUNDED, "cancelled"))
        self.assertEqual(customer_balance(second.customer), D("0.00"))
        self.assertEqual(cash_balance(self.cashbox), D("0.00"))
        self.assertEqual(bal("2105"), D("0.00"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_a_closed_month_refusal_leaves_the_deposit_held_and_offers_forfeit(self):
        last_month = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=10)
        appointment = self.book(starts_in=timedelta(days=5))
        deposit = self.take(appointment, "100.00", payment_date=last_month)
        period = get_period_for_date(last_month)
        period.status, period.closed_at = PeriodStatus.CLOSED, timezone.now()
        period.save(update_fields=["status", "closed_at"])
        self.client.force_login(self.owner)
        response = self.client.post(self.detail_url(appointment, "en"), {"lang": "en", "action": "status", "status": "cancelled"}, follow=True)
        self.assertContains(response, "month that has closed")
        self.assertContains(response, "data-refund-blocked")
        self.assertContains(response, "data-deposit-forfeit")
        deposit.refresh_from_db()
        self.assertEqual(deposit.status, DepositStatus.HELD)
        response = self.client.post(self.detail_url(appointment, "ar"), {"lang": "ar", "action": "deposit_forfeit"}, follow=True)
        self.assertContains(response, 'data-deposit-status="forfeited"')
        self.assertEqual(customer_balance(self.customer), D("0.00"))
        refused = self.client.post(self.detail_url(appointment, "ar"), {"lang": "ar", "action": "deposit_refund", "reason": "x"})
        self.assertContains(refused, "اتصرف فيه خلاص")

    def test_refund_button_after_a_cancel_elsewhere(self):
        appointment = self.book(starts_in=timedelta(days=5))
        deposit = self.take(appointment, "100.00")
        appointments.set_status(appointment, AppointmentStatus.CANCELLED, self.owner)  # cancelled without the screen: the deposit waits
        self.client.force_login(self.cashier)
        page = self.client.get(self.detail_url(appointment))
        self.assertContains(page, "data-deposit-refund")
        self.assertContains(page, "data-deposit-forfeit")
        response = self.client.post(self.detail_url(appointment), {"lang": "ar", "action": "deposit_refund", "reason": "لغى بدري"}, follow=True)
        self.assertContains(response, 'data-deposit-status="refunded"')
        deposit.refresh_from_db()
        self.assertEqual(deposit.payment.status, "cancelled")
        self.assertEqual(customer_balance(self.customer), D("0.00"))
