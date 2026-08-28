from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import (
    make_cashbox,
    make_cashbox_movement,
    make_customer,
    make_draft_sales_invoice,
    make_item,
    make_location,
    make_seeded_role,
    make_supplier,
    make_user,
    make_user_profile,
    posted_invoice_ready,
    stock_in,
)
from cashboxes.models import CashboxDirection
from permissions.models import RoleCode
from sales.services import post_sales_invoice


class FunctionalReportUiTests(TestCase):
    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def test_report_hub_stays_available_and_lists_permission_driven_reports(self):
        self.login_as(RoleCode.CASHIER, "report_hub_cashier")
        response = self.client.get(reverse("report_hub"), {"lang": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Read-only report center")
        self.assertTrue(next(card for card in response.context["cards"] if card["title"] == "Sales Report")["allowed"])
        self.assertFalse(next(card for card in response.context["cards"] if card["title"] == "Profit Report")["allowed"])

    def test_cashier_sales_report_is_scoped_to_own_invoices(self):
        cashier = self.login_as(RoleCode.CASHIER, "report_cashier")
        other = make_user(username="other_sales_user")
        make_draft_sales_invoice(invoice_number="OWN-SALE", created_by=cashier)
        make_draft_sales_invoice(invoice_number="OTHER-SALE", customer=make_customer(customer_code="OTHER"), created_by=other)
        response = self.client.get(reverse("reports:sales"), {"lang": "en"})
        numbers = [invoice.invoice_number for invoice in response.context["page"].object_list]
        self.assertEqual(numbers, ["OWN-SALE"])
        self.assertContains(response, "OWN-SALE")
        self.assertNotContains(response, "OTHER-SALE")

    def test_cashier_cannot_open_purchase_or_profit_reports(self):
        self.login_as(RoleCode.CASHIER, "report_denied")
        self.assertEqual(self.client.get(reverse("reports:purchases")).status_code, 403)
        self.assertEqual(self.client.get(reverse("reports:profit")).status_code, 403)

    def test_inventory_report_hides_cost_without_cost_permission(self):
        self.login_as(RoleCode.STOCK_KEEPER, "report_inventory")
        item = make_item(average_cost=Decimal("9876.5432"))
        stock_in(item, make_location(), 2, "9876.5432")
        response = self.client.get(reverse("reports:inventory"), {"lang": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_cost"])
        self.assertNotContains(response, "Stock value")
        self.assertNotContains(response, "19753")

    def test_owner_profit_report_uses_existing_profit_selector(self):
        self.login_as(RoleCode.OWNER, "report_profit")
        invoice, *_ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)
        response = self.client.get(reverse("reports:profit"), {"lang": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["totals"]["sales"], Decimal("60.00"))
        self.assertEqual(response.context["totals"]["cost"], Decimal("10.00"))
        self.assertEqual(response.context["totals"]["profit"], Decimal("50.00"))
        self.assertContains(response, invoice.invoice_number)

    def test_customer_supplier_and_cashbox_reports_use_ledger_selectors(self):
        self.login_as(RoleCode.OWNER, "report_finance")
        customer = make_customer(opening_balance=Decimal("10.00"))
        supplier = make_supplier(opening_balance=Decimal("20.00"))
        cashbox = make_cashbox(opening_balance=Decimal("100.00"))
        make_cashbox_movement(cashbox, CashboxDirection.IN, "30.00")
        customer_response = self.client.get(reverse("reports:customers"), {"customer": customer.pk, "lang": "en"})
        supplier_response = self.client.get(reverse("reports:suppliers"), {"supplier": supplier.pk, "lang": "en"})
        cashbox_response = self.client.get(reverse("reports:cashboxes"), {"cashbox": cashbox.pk, "lang": "en"})
        self.assertEqual(customer_response.context["rows"][0]["balance"], Decimal("10.00"))
        self.assertEqual(supplier_response.context["rows"][0]["balance"], Decimal("20.00"))
        self.assertEqual(cashbox_response.context["rows"][0]["balance"], Decimal("130.00"))

    def test_report_routes_are_read_only_get_forms(self):
        self.login_as(RoleCode.OWNER, "report_read_only")
        for name in ("reports:sales", "reports:purchases", "reports:inventory", "reports:customers", "reports:suppliers", "reports:cashboxes", "reports:profit"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name), {"lang": "en"})
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'method="post"')

