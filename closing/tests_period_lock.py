"""HG-011: invoices and payments respect closed periods, like returns already did."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from cashboxes.models import CashboxMovement
from hesba_testing.factories import (
    DEFAULT_DATE,
    make_cashbox,
    make_customer,
    make_supplier,
    posted_invoice_ready,
    purchase_ready,
)
from inventory.models import StockMovement
from purchases.models import SupplierLedgerEntry
from purchases.services import (
    cancel_posted_purchase_invoice,
    cancel_supplier_payment,
    post_purchase_invoice,
    record_supplier_payment,
)
from sales.models import CustomerLedgerEntry
from sales.services import (
    cancel_customer_payment,
    cancel_posted_sales_invoice,
    post_sales_invoice,
    record_customer_payment,
)

from .models import Period, PeriodStatus
from .services import reopen_period


def period_around(day, status=PeriodStatus.OPEN):
    return Period.objects.create(
        period_code=f"LOCK-{day:%Y%m}",
        name="lock test",
        start_date=day.replace(day=1),
        end_date=day.replace(day=28),
        status=status,
        closed_at=timezone.now() if status == PeriodStatus.CLOSED else None,
    )


def close(period):
    period.status = PeriodStatus.CLOSED
    period.closed_at = timezone.now()
    period.save(update_fields=["status", "closed_at"])


def counts():
    return (
        CashboxMovement.objects.count(),
        StockMovement.objects.count(),
        CustomerLedgerEntry.objects.count(),
        SupplierLedgerEntry.objects.count(),
    )


class SalesLockTests(TestCase):
    def setUp(self):
        self.period = period_around(DEFAULT_DATE)

    def test_posting_into_a_closed_month_is_refused_and_writes_nothing(self):
        invoice, *_ = posted_invoice_ready(paid_now="20.00")
        close(self.period)
        before = counts()
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            post_sales_invoice(invoice.pk)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")
        self.assertEqual(counts(), before)

    def test_cancelling_a_sale_of_a_closed_month_is_refused(self):
        invoice, *_ = posted_invoice_ready(paid_now="20.00")
        post_sales_invoice(invoice.pk)
        close(self.period)
        before = counts()
        with self.assertRaises(ValidationError):
            cancel_posted_sales_invoice(invoice.pk, reason="late")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(counts(), before)

    def test_a_reopened_month_takes_the_late_invoice(self):
        invoice, *_ = posted_invoice_ready(paid_now="20.00")
        close(self.period)
        reopen_period(self.period.pk, reason="late invoice")
        post_sales_invoice(invoice.pk)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "posted")

    def test_customer_payment_into_a_closed_month_is_refused(self):
        customer, cashbox = make_customer(), make_cashbox()
        close(self.period)
        with self.assertRaises(ValidationError):
            record_customer_payment("CP-LOCK", DEFAULT_DATE, customer, cashbox, D("10.00"))
        self.assertEqual(counts(), (0, 0, 0, 0))

    def test_cancelling_a_customer_payment_of_a_closed_month_is_refused(self):
        payment = record_customer_payment("CP-LOCK2", DEFAULT_DATE, make_customer(), make_cashbox(), D("10.00"))
        close(self.period)
        before = counts()
        with self.assertRaises(ValidationError):
            cancel_customer_payment(payment.pk, reason="late")
        self.assertEqual(counts(), before)


class PurchaseLockTests(TestCase):
    def setUp(self):
        self.period = period_around(DEFAULT_DATE)

    def test_posting_a_purchase_into_a_closed_month_is_refused(self):
        invoice, *_ = purchase_ready(paid_now="0.00")
        close(self.period)
        with self.assertRaises(ValidationError):
            post_purchase_invoice(invoice.pk)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")
        self.assertEqual(counts(), (0, 0, 0, 0))

    def test_cancelling_a_purchase_of_a_closed_month_is_refused(self):
        invoice, *_ = purchase_ready(paid_now="0.00")
        post_purchase_invoice(invoice.pk)
        close(self.period)
        before = counts()
        with self.assertRaises(ValidationError):
            cancel_posted_purchase_invoice(invoice.pk, reason="late")
        self.assertEqual(counts(), before)

    def test_supplier_payment_both_ways(self):
        payment = record_supplier_payment("SP-LOCK", DEFAULT_DATE, make_supplier(), make_cashbox(), D("0.01"))
        close(self.period)
        before = counts()
        with self.assertRaises(ValidationError):
            record_supplier_payment("SP-LOCK2", DEFAULT_DATE, make_supplier(), make_cashbox(), D("5.00"))
        with self.assertRaises(ValidationError):
            cancel_supplier_payment(payment.pk, reason="late")
        self.assertEqual(counts(), before)
