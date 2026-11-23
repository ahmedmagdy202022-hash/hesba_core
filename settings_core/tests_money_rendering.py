"""Rendered pages route money and cost through the shared display filters.

tests_format.py proves the filters themselves. These tests prove the screens
actually use them: every page here renders under the default Arabic locale,
where a bare {{ value }} shows Decimal("1234.50") as "1234,50" — a decimal
comma that the rest of the application reads as a thousands separator.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from cashboxes.models import CashboxDirection
from hesba_testing.factories import (
    add_purchase_line,
    add_sales_line,
    make_cashbox,
    make_cashbox_movement,
    make_customer,
    make_draft_purchase_invoice,
    make_draft_sales_invoice,
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    posted_invoice_ready,
    stock_in,
)
from master_data.models import Item
from permissions.models import RoleCode
from sales.services import post_sales_invoice


GROUPED = "1,234.50"
# What the same amount looks like when it escapes the filter: the Arabic
# locale's decimal comma, or the raw ungrouped Decimal.
BARE = ("1234,50", "1234.50", "1234,5", "1234.5<")


class MoneyRenderingTests(TestCase):
    def login_as(self, role_code=RoleCode.OWNER, username="money_owner"):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def assertGrouped(self, response, expected=GROUPED):
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(expected, content)
        for bare in BARE:
            self.assertNotIn(bare, content)
        self.assertNotIn("-0.00", content)

    def test_sales_invoice_detail_groups_totals_and_line_amounts(self):
        self.login_as()
        invoice = make_draft_sales_invoice()
        add_sales_line(invoice, make_item(), quantity=3, unit_sale_price="411.50")
        response = self.client.get(reverse("sales:detail", args=[invoice.pk]))
        self.assertGrouped(response)
        self.assertContains(response, "411.50")

    def test_sales_list_groups_totals(self):
        self.login_as()
        invoice = make_draft_sales_invoice()
        add_sales_line(invoice, make_item(), quantity=1, unit_sale_price="1234.5")
        self.assertGrouped(self.client.get(reverse("sales:list")))

    def test_purchase_list_and_detail_group_totals(self):
        self.login_as()
        invoice = make_draft_purchase_invoice()
        add_purchase_line(invoice, make_item(), quantity=1, unit_purchase_price="1234.5")
        self.assertGrouped(self.client.get(reverse("purchases:list")))
        self.assertGrouped(self.client.get(reverse("purchases:detail", args=[invoice.pk])))

    def test_cashbox_detail_and_list_group_balances(self):
        self.login_as()
        cashbox = make_cashbox()
        make_cashbox_movement(cashbox, CashboxDirection.IN, "1234.50")
        self.assertGrouped(self.client.get(reverse("cashboxes:detail", args=[cashbox.pk])))
        self.assertGrouped(self.client.get(reverse("cashboxes:list")))
        self.assertGrouped(self.client.get(reverse("cashboxes:movements")))

    def test_customer_report_groups_opening_balance(self):
        self.login_as()
        make_customer(opening_balance=Decimal("1234.50"))
        self.assertGrouped(self.client.get(reverse("reports:customers")))

    def test_cashbox_report_groups_balances(self):
        self.login_as()
        make_cashbox_movement(make_cashbox(), CashboxDirection.IN, "1234.50")
        self.assertGrouped(self.client.get(reverse("reports:cashboxes")))

    def test_profit_report_groups_sales_cost_and_profit(self):
        self.login_as()
        invoice, *_ = posted_invoice_ready(stock_quantity=10, unit_cost="5.00")
        line = invoice.lines.get()
        line.unit_sale_price = Decimal("617.25")
        line.line_total_amount = Decimal("1234.50")
        line.save()
        invoice.subtotal = invoice.total_amount = invoice.remaining_due = Decimal("1234.50")
        invoice.save()
        post_sales_invoice(invoice.pk)
        response = self.client.get(reverse("reports:profit"))
        self.assertGrouped(response)
        # Sales 1,234.50 less a cost of 2 x 5.00.
        self.assertContains(response, "1,224.50")

    def test_master_data_lists_use_the_shared_filter(self):
        self.login_as()
        make_customer(credit_limit=Decimal("2500.50"))
        make_item(default_sale_price=Decimal("1234.50"), default_purchase_price=Decimal("1000.00"))
        self.assertGrouped(self.client.get(reverse("master_data:customers")), "2,500.50")
        response = self.client.get(reverse("master_data:items"))
        self.assertGrouped(response)
        self.assertContains(response, "1,000.00")

    def test_unit_cost_keeps_four_decimal_precision(self):
        self.login_as()
        location = make_location()
        item = make_item()
        stock_in(item, location, 4, unit_cost="70.1250")
        Item.objects.filter(pk=item.pk).update(average_cost=Decimal("1234.5000"))
        detail = self.client.get(reverse("inventory:item_detail", args=[item.pk]))
        self.assertGrouped(detail)
        self.assertContains(detail, "70.125")
        self.assertNotContains(detail, "70,1250")
        self.assertGrouped(self.client.get(reverse("inventory:stock")))

    def test_cost_column_stays_hidden_without_cost_permission(self):
        self.login_as(RoleCode.STOCK_KEEPER, "money_keeper")
        location = make_location()
        item = make_item()
        stock_in(item, location, 4, unit_cost="70.1250")
        response = self.client.get(reverse("inventory:movements"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "70.125")
