from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.services import get_cashbox_balance
from cashboxes.models import CashboxOperationType
from cashboxes.services import create_cashbox_operation
from closing.models import Period, PeriodStatus
from hesba_testing.factories import (
    make_cashbox,
    make_customer,
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    stock_in,
)
from inventory.services import get_item_location_stock_quantity
from permissions.models import RoleCode
from reports.selectors import profit_report, profit_totals

from .models import CustomerLedgerEntry, SalesReturnStatus
from .services import (
    cancel_posted_sales_invoice,
    cancel_sales_return,
    create_sales_draft,
    create_sales_return,
    post_sales_invoice,
)


class SalesReturnTests(TestCase):
    def setUp(self):
        self.when = date(2026, 6, 10)
        self.period = Period.objects.create(
            period_code="2026-SAL-RET",
            name="2026 sales returns",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.owner = make_user("sales_return_owner")
        make_user_profile(self.owner, make_seeded_role(RoleCode.OWNER))
        self.cashier = make_user("sales_return_cashier")
        make_user_profile(self.cashier, make_seeded_role(RoleCode.CASHIER))
        self.customer = make_customer()
        self.location = make_location()
        self.cashbox = make_cashbox(opening_balance=Decimal("100.00"))
        self.item_1 = make_item(item_code="RET-S-1", item_name="Return S1")
        self.item_2 = make_item(item_code="RET-S-2", item_name="Return S2")
        stock_in(self.item_1, self.location, 1, "0.25")
        stock_in(self.item_2, self.location, 1, "0.50")
        self.invoice = create_sales_draft(
            {
                "invoice_number": "SI-RETURN-1",
                "invoice_date": self.when,
                "customer": self.customer,
                "selling_location": self.location,
                "cashbox": self.cashbox,
                "discount_amount": Decimal("0.01"),
                "tax_amount": Decimal("0"),
                "paid_now": Decimal("1.00"),
            },
            [
                {"item": self.item_1, "quantity": Decimal("1"), "unit_sale_price": Decimal("1.00")},
                {"item": self.item_2, "quantity": Decimal("1"), "unit_sale_price": Decimal("1.00")},
            ],
            self.owner,
        )
        post_sales_invoice(self.invoice.pk, self.owner)
        self.invoice.refresh_from_db()

    def create_return(self, number, source_line, **kwargs):
        return create_sales_return(
            return_number=number,
            return_date=self.when,
            source_invoice_id=self.invoice.pk,
            lines=[{"source_line": source_line, "quantity": kwargs.pop("quantity", 1)}],
            reason=kwargs.pop("reason", "Customer returned goods"),
            user=kwargs.pop("user", self.owner),
        )

    def test_independent_returns_reverse_stock_party_cash_and_rounding_residuals(self):
        first, second = list(self.invoice.lines.all())
        first_return = self.create_return("SR-1", first)
        second_return = self.create_return("SR-2", second)
        self.assertEqual(first_return.total_amount, Decimal("1.00"))
        self.assertEqual(second_return.total_amount, Decimal("0.99"))
        self.assertEqual(first_return.cash_amount + second_return.cash_amount, Decimal("1.00"))
        self.assertEqual(first_return.due_amount + second_return.due_amount, Decimal("0.99"))
        self.assertEqual(first_return.cost_amount + second_return.cost_amount, Decimal("0.75"))
        self.assertEqual(get_item_location_stock_quantity(self.item_1, self.location), 1)
        self.assertEqual(get_item_location_stock_quantity(self.item_2, self.location), 1)
        self.assertEqual(CustomerLedgerEntry.objects.filter(sales_return__isnull=False).count(), 2)
        totals = profit_totals()
        self.assertEqual(totals["sales"], Decimal("0.00"))
        self.assertEqual(totals["cost"], Decimal("0.00"))
        self.assertEqual(sum(row["sales_amount"] for row in profit_report()), Decimal("0.00"))

    def test_return_cannot_exceed_remaining_quantity(self):
        line = self.invoice.lines.first()
        self.create_return("SR-LIMIT-1", line)
        with self.assertRaisesMessage(ValidationError, "remaining quantity"):
            self.create_return("SR-LIMIT-2", line)

    def test_cancellation_reverses_return_without_deleting_document(self):
        line = self.invoice.lines.first()
        sales_return = self.create_return("SR-REV", line)
        balance_after_return = get_cashbox_balance(self.cashbox)
        cancel_sales_return(sales_return.pk, self.when, "Return entered twice", self.owner)
        sales_return.refresh_from_db()
        self.assertEqual(sales_return.status, SalesReturnStatus.CANCELLED)
        self.assertEqual(sales_return.stock_movements.count(), 2)
        self.assertEqual(get_item_location_stock_quantity(self.item_1, self.location), 0)
        self.assertEqual(
            get_cashbox_balance(self.cashbox), balance_after_return + sales_return.cash_amount
        )
        self.assertEqual(sales_return.customer_ledger_entries.count(), 2)

    def test_posted_return_blocks_full_invoice_cancellation(self):
        self.create_return("SR-BLOCK-CANCEL", self.invoice.lines.first())
        with self.assertRaisesMessage(ValidationError, "return documents"):
            cancel_posted_sales_invoice(self.invoice.pk, self.owner, "Cancel invoice")

    def test_permission_closed_period_and_cash_guard(self):
        line = self.invoice.lines.first()
        with self.assertRaises(PermissionDenied):
            self.create_return("SR-DENIED", line, user=self.cashier)
        self.cashbox.opening_balance = Decimal("0")
        self.cashbox.save(update_fields=["opening_balance"])
        create_cashbox_operation(
            "SPEND-INVOICE-CASH",
            self.when,
            CashboxOperationType.DIRECT_OUT,
            Decimal("1.00"),
            "Empty the cashbox for refund guard",
            self.owner,
            source_cashbox=self.cashbox,
        )
        with self.assertRaisesMessage(ValidationError, "cannot become negative"):
            self.create_return("SR-NO-CASH", line)
        self.period.status = PeriodStatus.CLOSED
        self.period.closed_at = timezone.now()
        self.period.save(update_fields=["status", "closed_at"])
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            self.create_return("SR-CLOSED", line)

    def test_bilingual_ui_posts_independent_return_and_hides_actions_from_cashier(self):
        self.client.force_login(self.owner)
        line = self.invoice.lines.first()
        response = self.client.get(
            reverse("sales:return_create", args=[self.invoice.pk]), {"lang": "ar"}
        )
        self.assertContains(response, "سبب المرتجع")
        response = self.client.post(
            reverse("sales:return_create", args=[self.invoice.pk]),
            {
                "lang": "en",
                "return_number": "SR-UI",
                "return_date": "2026-06-10",
                "reason": "Customer exchange",
                "lines-TOTAL_FORMS": "5",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "20",
                "lines-0-source_line": str(line.pk),
                "lines-0-quantity": "1.000",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.invoice.returns.filter(return_number="SR-UI").exists())

        self.client.force_login(self.cashier)
        response = self.client.get(reverse("sales:detail", args=[self.invoice.pk]))
        self.assertNotContains(response, reverse("sales:return_create", args=[self.invoice.pk]))
        self.assertNotContains(response, reverse("sales:cancel", args=[self.invoice.pk]))
