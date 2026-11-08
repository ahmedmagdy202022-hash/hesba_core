"""HG-069: the shared settlement table and its ``applied`` helper, which the four "due" totals subtract."""

from decimal import Decimal as D

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import cancel_customer_payment, create_sales_draft, post_sales_invoice, record_customer_payment

from . import services
from .models import InvoiceSettlement, SettlementSource


class AppliedTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = make_user(username="set_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))
        self.customer = make_customer(customer_code="C-SET")
        self.cashbox = make_cashbox(cashbox_code="SET-CASH")
        self.location = make_location(location_code="SET", is_selling_location=True)
        self.service = make_item(item_code="SET-SRV", item_name="خدمة", is_stock_tracked=False)
        self.today = timezone.localdate()

    def invoice(self, number, price):
        invoice = create_sales_draft({"invoice_number": number, "invoice_date": self.today, "customer": self.customer, "selling_location": self.location,
                                      "cashbox": None, "paid_now": D("0")}, [{"item": self.service, "quantity": D("1"), "unit_sale_price": D(price)}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        return invoice

    def payment(self, number, amount):
        return record_customer_payment(number, self.today, self.customer, self.cashbox, D(amount), self.owner)

    def test_applied_sums_posted_payments_per_invoice_for_any_shape_of_input(self):
        first, second, third = self.invoice("S-1", "300"), self.invoice("S-2", "200"), self.invoice("S-3", "50")
        services.settle(first, self.payment("P-1", "100"), D("100"), SettlementSource.DEPOSIT)
        services.settle(first, self.payment("P-2", "40"), D("40"), SettlementSource.PAYMENT_LINK)
        services.settle(second, self.payment("P-3", "200"), D("200"), SettlementSource.GIFT_CARD)
        expected = {first.pk: D("140"), second.pk: D("200")}
        self.assertEqual(services.applied(SalesInvoice.objects.filter(status="posted")), expected)
        self.assertEqual(services.applied([first, second, third]), expected)
        self.assertEqual(services.applied([first.pk, third.pk]), {first.pk: D("140")})
        self.assertEqual(services.applied_total(SalesInvoice.objects.all()), D("340"))
        self.assertEqual((services.applied_to(first), services.applied_to(third)), (D("140"), D("0.00")))
        self.assertEqual(services.applied([]), {})

    def test_one_settlement_per_payment_and_a_cancelled_payment_no_longer_counts(self):
        invoice = self.invoice("S-4", "300")
        payment = self.payment("P-4", "100")
        services.settle(invoice, payment, D("100"), SettlementSource.PAYMENT_LINK)
        with self.assertRaises(IntegrityError):
            services.settle(self.invoice("S-5", "10"), payment, D("10"), SettlementSource.PAYMENT_LINK)

    def test_a_cancelled_payment_drops_out_of_applied(self):
        invoice = self.invoice("S-6", "300")
        payment = self.payment("P-6", "100")
        services.settle(invoice, payment, D("100"), SettlementSource.PAYMENT_LINK)
        cancel_customer_payment(payment.pk, self.owner, "غلط")
        self.assertEqual(services.applied([invoice]), {})

    def test_release_removes_the_rows_and_returns_their_payments(self):
        invoice = self.invoice("S-7", "300")
        payment = self.payment("P-7", "100")
        services.settle(invoice, payment, D("100"), SettlementSource.DEPOSIT)
        self.assertEqual(services.release(invoice), [payment])
        self.assertFalse(InvoiceSettlement.objects.filter(invoice=invoice).exists())
