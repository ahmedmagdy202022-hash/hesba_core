from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.services import get_cashbox_balance
from closing.models import Period, PeriodStatus
from hesba_testing.factories import (
    make_cashbox,
    make_item,
    make_location,
    make_seeded_role,
    make_supplier,
    make_user,
    make_user_profile,
)
from inventory.models import StockAdjustmentDirection
from inventory.services import adjust_stock, get_item_location_stock_quantity
from permissions.models import RoleCode

from .models import PurchaseReturnStatus, SupplierLedgerEntry
from .services import (
    cancel_posted_purchase_invoice,
    cancel_purchase_return,
    create_purchase_draft,
    create_purchase_return,
    post_purchase_invoice,
)


class PurchaseReturnTests(TestCase):
    def setUp(self):
        self.when = date(2026, 5, 10)
        self.period = Period.objects.create(
            period_code="2026-PUR-RET",
            name="2026 purchase returns",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.owner = make_user("purchase_return_owner")
        make_user_profile(self.owner, make_seeded_role(RoleCode.OWNER))
        self.cashier = make_user("purchase_return_cashier")
        make_user_profile(self.cashier, make_seeded_role(RoleCode.CASHIER))
        self.supplier = make_supplier()
        self.location = make_location()
        self.cashbox = make_cashbox(opening_balance=Decimal("100.00"))
        self.item_1 = make_item(item_code="RET-P-1", item_name="Return P1")
        self.item_2 = make_item(item_code="RET-P-2", item_name="Return P2")
        self.invoice = create_purchase_draft(
            {
                "invoice_number": "PI-RETURN-1",
                "invoice_date": self.when,
                "supplier": self.supplier,
                "receiving_location": self.location,
                "cashbox": self.cashbox,
                "discount_amount": Decimal("0.01"),
                "tax_amount": Decimal("0"),
                "paid_now": Decimal("1.00"),
            },
            [
                {
                    "item": self.item_1,
                    "quantity": Decimal("1"),
                    "unit_purchase_price": Decimal("1.00"),
                },
                {
                    "item": self.item_2,
                    "quantity": Decimal("1"),
                    "unit_purchase_price": Decimal("1.00"),
                },
            ],
            self.owner,
        )
        post_purchase_invoice(self.invoice.pk, self.owner)
        self.invoice.refresh_from_db()

    def create_return(self, number, source_line, **kwargs):
        return create_purchase_return(
            return_number=number,
            return_date=self.when,
            source_invoice_id=self.invoice.pk,
            lines=[{"source_line": source_line, "quantity": kwargs.pop("quantity", 1)}],
            reason=kwargs.pop("reason", "Goods rejected"),
            user=kwargs.pop("user", self.owner),
        )

    def test_independent_returns_use_last_line_residual_and_exact_payment_residual(self):
        first, second = list(self.invoice.lines.all())
        first_return = self.create_return("PR-1", first)
        second_return = self.create_return("PR-2", second)
        self.assertEqual(first_return.total_amount, Decimal("1.00"))
        self.assertEqual(second_return.total_amount, Decimal("0.99"))
        self.assertEqual(first_return.cash_amount + second_return.cash_amount, Decimal("1.00"))
        self.assertEqual(first_return.due_amount + second_return.due_amount, Decimal("0.99"))
        self.assertEqual(get_item_location_stock_quantity(self.item_1, self.location), 0)
        self.assertEqual(get_item_location_stock_quantity(self.item_2, self.location), 0)
        self.assertEqual(
            SupplierLedgerEntry.objects.filter(purchase_return__isnull=False).count(), 2
        )

    def test_return_cannot_exceed_remaining_quantity(self):
        line = self.invoice.lines.first()
        self.create_return("PR-LIMIT-1", line)
        with self.assertRaisesMessage(ValidationError, "remaining quantity"):
            self.create_return("PR-LIMIT-2", line)

    def test_return_stock_guard_aggregates_repeated_item_lines(self):
        repeated_item = make_item(item_code="RET-P-REPEATED", item_name="Repeated P")
        invoice = create_purchase_draft(
            {
                "invoice_number": "PI-RETURN-REPEATED",
                "invoice_date": self.when,
                "supplier": self.supplier,
                "receiving_location": self.location,
                "cashbox": self.cashbox,
                "paid_now": Decimal("0"),
            },
            [
                {
                    "item": repeated_item,
                    "quantity": Decimal("1"),
                    "unit_purchase_price": Decimal("1.00"),
                },
                {
                    "item": repeated_item,
                    "quantity": Decimal("1"),
                    "unit_purchase_price": Decimal("1.00"),
                },
            ],
            self.owner,
        )
        post_purchase_invoice(invoice.pk, self.owner)
        adjust_stock(
            "ADJ-RETURN-REPEATED",
            self.when,
            repeated_item,
            self.location,
            StockAdjustmentDirection.OUT,
            Decimal("0.5"),
            "Stock used before return",
            self.owner,
        )

        lines = list(invoice.lines.order_by("line_number"))
        with self.assertRaisesMessage(ValidationError, "Not enough stock"):
            create_purchase_return(
                return_number="PR-REPEATED-GUARD",
                return_date=self.when,
                source_invoice_id=invoice.pk,
                lines=[
                    {"source_line": lines[0], "quantity": Decimal("1")},
                    {"source_line": lines[1], "quantity": Decimal("1")},
                ],
                reason="Return both repeated lines",
                user=self.owner,
            )

        self.assertFalse(invoice.returns.exists())
        self.assertEqual(
            get_item_location_stock_quantity(repeated_item, self.location),
            Decimal("1.5"),
        )

    def test_cancellation_reverses_stock_due_and_cash_without_deleting(self):
        line = self.invoice.lines.first()
        purchase_return = self.create_return("PR-REV", line)
        balance_after_return = get_cashbox_balance(self.cashbox)
        cancel_purchase_return(purchase_return.pk, self.when, "Return entered twice", self.owner)
        purchase_return.refresh_from_db()
        self.assertEqual(purchase_return.status, PurchaseReturnStatus.CANCELLED)
        self.assertEqual(purchase_return.stock_movements.count(), 2)
        self.assertEqual(get_item_location_stock_quantity(self.item_1, self.location), 1)
        self.assertEqual(
            get_cashbox_balance(self.cashbox), balance_after_return - purchase_return.cash_amount
        )
        self.assertEqual(purchase_return.supplier_ledger_entries.count(), 2)

    def test_posted_return_blocks_full_invoice_cancellation(self):
        self.create_return("PR-BLOCK-CANCEL", self.invoice.lines.first())
        with self.assertRaisesMessage(ValidationError, "return documents"):
            cancel_posted_purchase_invoice(self.invoice.pk, self.owner, "Cancel invoice")

    def test_permission_and_closed_period_are_enforced_in_service(self):
        line = self.invoice.lines.first()
        with self.assertRaises(PermissionDenied):
            self.create_return("PR-DENIED", line, user=self.cashier)
        self.period.status = PeriodStatus.CLOSED
        self.period.closed_at = timezone.now()
        self.period.save(update_fields=["status", "closed_at"])
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            self.create_return("PR-CLOSED", line)

    def test_bilingual_ui_posts_independent_return_and_hides_actions_from_viewer(self):
        self.client.force_login(self.owner)
        line = self.invoice.lines.first()
        response = self.client.get(
            reverse("purchases:return_create", args=[self.invoice.pk]), {"lang": "ar"}
        )
        self.assertContains(response, "سبب المرتجع")
        response = self.client.post(
            reverse("purchases:return_create", args=[self.invoice.pk]),
            {
                "lang": "en",
                "return_number": "PR-UI",
                "return_date": "2026-05-10",
                "reason": "Damaged delivery",
                "lines-TOTAL_FORMS": "5",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "20",
                "lines-0-source_line": str(line.pk),
                "lines-0-quantity": "1.000",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.invoice.returns.filter(return_number="PR-UI").exists())

        accountant = make_user("purchase_return_viewer")
        make_user_profile(accountant, make_seeded_role(RoleCode.ACCOUNTANT))
        self.client.force_login(accountant)
        response = self.client.get(reverse("purchases:detail", args=[self.invoice.pk]))
        self.assertNotContains(response, reverse("purchases:return_create", args=[self.invoice.pk]))
        self.assertNotContains(response, reverse("purchases:cancel", args=[self.invoice.pk]))
