from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from cashboxes.models import CashboxMovement
from hesba_testing.factories import (
    add_sales_line,
    make_cashbox,
    make_customer,
    make_draft_sales_invoice,
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    posted_invoice_ready,
    recalculate_invoice_totals,
    stock_in,
)
from inventory.models import StockMovement, StockMovementType
from permissions.models import RoleCode

from .models import CustomerLedgerEntry, SalesInvoice
from .services import post_sales_invoice


class SalesUiTests(TestCase):
    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def draft_payload(self, paid_now="20.00"):
        return {
            "lang": "en",
            "invoice_number": "SI-UI-001",
            "invoice_date": "2026-01-15",
            "customer": make_customer().pk,
            "selling_location": make_location().pk,
            "cashbox": make_cashbox().pk,
            "discount_amount": "5.00",
            "tax_amount": "5.00",
            "paid_now": paid_now,
            "notes": "UI draft",
            "lines-TOTAL_FORMS": "5",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "20",
            "lines-0-item": make_item().pk,
            "lines-0-description": "First line",
            "lines-0-quantity": "2.000",
            "lines-0-unit_sale_price": "25.00",
            "lines-0-line_discount_amount": "0.00",
        }

    def test_cashier_can_create_consistent_sales_draft(self):
        self.login_as(RoleCode.CASHIER, "sales_cashier")
        response = self.client.post(reverse("sales:create"), self.draft_payload())
        self.assertEqual(response.status_code, 302)
        invoice = SalesInvoice.objects.get()
        self.assertEqual(invoice.total_amount, Decimal("50.00"))
        self.assertEqual(invoice.remaining_due, Decimal("30.00"))
        self.assertFalse(StockMovement.objects.exists())

    def test_post_route_enforces_stock_and_creates_no_partial_effects(self):
        self.login_as(RoleCode.CASHIER, "sales_no_stock")
        invoice = make_draft_sales_invoice(cashbox=make_cashbox())
        add_sales_line(invoice, make_item(), 2, "10.00")
        response = self.client.post(reverse("sales:post", args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")
        self.assertFalse(CustomerLedgerEntry.objects.exists())
        self.assertFalse(CashboxMovement.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_post_route_creates_customer_cashbox_stock_and_cost_effects(self):
        user = self.login_as(RoleCode.OWNER, "sales_post")
        invoice, item, location, cashbox = posted_invoice_ready(paid_now="20.00")
        response = self.client.post(reverse("sales:post", args=[invoice.pk]), {"lang": "en"})
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        line = invoice.lines.get()
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(CustomerLedgerEntry.objects.get().due_increase, Decimal("40.00"))
        self.assertEqual(CashboxMovement.objects.get().amount, Decimal("20.00"))
        sale_movement = StockMovement.objects.get(movement_type=StockMovementType.SALE_OUT)
        self.assertEqual(sale_movement.quantity, Decimal("2.000"))
        self.assertEqual(line.line_cost_amount, Decimal("10.00"))
        self.assertEqual(sale_movement.created_by, user)

    def test_repeated_post_does_not_duplicate_effects(self):
        self.login_as(RoleCode.OWNER, "sales_repeat")
        invoice, *_ = posted_invoice_ready(paid_now="20.00")
        post_sales_invoice(invoice.pk)
        self.client.post(reverse("sales:post", args=[invoice.pk]))
        self.assertEqual(CustomerLedgerEntry.objects.count(), 1)
        self.assertEqual(CashboxMovement.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 2)  # opening in + sale out

    def test_cancel_route_reverses_customer_cashbox_and_stock(self):
        self.login_as(RoleCode.OWNER, "sales_cancel")
        invoice, *_ = posted_invoice_ready(paid_now="20.00")
        post_sales_invoice(invoice.pk)
        response = self.client.post(reverse("sales:cancel", args=[invoice.pk]), {"reason": "Wrong", "lang": "en"})
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "cancelled")
        self.assertEqual(CustomerLedgerEntry.objects.count(), 2)
        self.assertEqual(CashboxMovement.objects.count(), 2)
        self.assertEqual(StockMovement.objects.count(), 3)  # opening, sale, return

    def test_manager_detail_hides_cost_and_profit(self):
        self.login_as(RoleCode.MANAGER, "sales_manager")
        invoice, *_ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)
        response = self.client.get(reverse("sales:detail", args=[invoice.pk]), {"lang": "en"})
        self.assertFalse(response.context["can_view_cost"])
        self.assertFalse(response.context["can_view_profit"])
        self.assertNotContains(response, "Unit cost")
        self.assertNotContains(response, "Profit")

    def test_stale_average_cost_behavior_is_characterized_not_silently_fixed(self):
        self.login_as(RoleCode.OWNER, "sales_stale_cost")
        location = make_location()
        item = make_item(average_cost=Decimal("1.0000"))
        stock_in(item, location, 10, "10.00")
        # Deliberately do not call recalculate_item_average_cost: this is HG-003.
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, 2, "30.00")
        recalculate_invoice_totals(invoice)
        self.client.post(reverse("sales:post", args=[invoice.pk]))
        line = invoice.lines.get()
        self.assertEqual(line.unit_cost, Decimal("1.0000"))
        self.assertEqual(line.line_cost_amount, Decimal("2.00"))

    def test_english_sales_list_renders(self):
        self.login_as(RoleCode.CASHIER, "sales_english")
        response = self.client.get(reverse("sales:list"), {"lang": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sales invoices")
