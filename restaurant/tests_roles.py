"""HG-048: the waiter works the floor and never takes money."""

from decimal import Decimal

from django.urls import reverse

from hesba_testing.factories import make_customer
from permissions.models import RoleCode
from permissions.services import user_has_permission

from . import services as floor
from .models import Order, OrderStatus
from .tests import RestaurantSetup, person


class WaiterTests(RestaurantSetup):
    def setUp(self):
        super().setUp()
        self.waiter = person(RoleCode.WAITER, "floor_waiter")
        self.client.force_login(self.waiter)

    def post(self, order, **data):
        return self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", **data})

    def test_the_role_holds_the_floor_and_nothing_on_the_money_side(self):
        for code in ("restaurant.take_orders",):
            self.assertTrue(user_has_permission(self.waiter, code))
        for code in ("sales.create_sales_invoice", "sales.view_sales_invoices", "cashboxes.view_cashboxes", "inventory.view_cost",
                     "reports.view_sales_report", "restaurant.prepare_at_payment", "inventory.adjust_stock", "master_data.view_master_data"):
            self.assertFalse(user_has_permission(self.waiter, code), code)

    def test_a_waiter_opens_a_table_adds_dishes_sends_them_and_moves_the_table(self):
        board = self.client.get(reverse("restaurant:board"))
        self.assertContains(board, 'data-table-state="free"')
        opened = self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "dine_in", "table": self.t1.pk})
        order = Order.objects.get()
        self.assertRedirects(opened, f"{reverse('restaurant:order', args=[order.pk])}?lang=ar", fetch_redirect_response=False)
        self.post(order, action="add", item=self.koshary.pk, quantity="2")
        self.post(order, action="add", item=self.tea.pk, quantity="1")
        tea = order.lines.get(item=self.tea)
        self.post(order, action="void", line=tea.pk)
        self.post(order, action="send", print="0")
        self.assertEqual(order.tickets.count(), 1)
        self.post(order, action="move", table=self.t2.pk)
        order.refresh_from_db()
        self.assertEqual((order.table, order.status), (self.t2, OrderStatus.OPEN))
        page = self.client.get(reverse("restaurant:order", args=[order.pk]))
        self.assertContains(page, "data-move")
        self.assertNotContains(page, "data-pay-form")  # no bill, no cashbox, no cancel
        self.assertNotContains(page, 'name="cashbox"')
        self.assertNotContains(page, "data-cancel-order")

    def test_a_waiter_cannot_bill_cancel_or_dispatch_an_order_even_by_hand(self):
        order = floor.open_order(self.owner, table=self.t1, guests=2)
        floor.add_item(order, self.koshary, self.owner, quantity=1)
        for action, extra in (("pay", {"cashbox": self.cashbox.pk, "tendered": "100"}), ("cancel", {"reason": "x"}), ("dispatch", {})):
            self.assertEqual(self.post(order, action=action, **extra).status_code, 403, action)
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice), (OrderStatus.OPEN, None))

    def test_a_waiter_sees_no_invoice_screen_and_no_paid_invoice_number(self):
        order = floor.open_order(self.owner, table=self.t1, guests=2, customer=make_customer(customer_code="C-R1"))
        floor.add_item(order, self.koshary, self.owner, quantity=1)
        invoice, _ = floor.pay(order, self.owner, cashbox=self.cashbox, tendered=Decimal("100"))
        self.assertEqual(self.client.get(reverse("sales:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("sales:detail", args=[invoice.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("restaurant:order", args=[order.pk])), "data-order-invoice")

    def test_the_waiter_lands_on_the_floor_and_the_menu_offers_it(self):
        landed = self.client.get(reverse("after_login"))
        self.assertRedirects(landed, reverse("restaurant:board"), fetch_redirect_response=False)
        menu = self.client.get(reverse("dashboard_snapshot")).content.decode()
        self.assertIn(reverse("restaurant:board"), menu)
        self.assertNotIn(reverse("sales:list"), menu)
        self.assertNotIn(reverse("master_data:items"), menu)

    def test_a_cashier_still_lands_on_the_dashboard_and_bills(self):
        cashier = person(RoleCode.CASHIER, "floor_cashier")
        self.client.force_login(cashier)
        self.assertRedirects(self.client.get(reverse("after_login")), reverse("dashboard_snapshot"), fetch_redirect_response=False)
        order = floor.open_order(cashier, table=self.t1, guests=2)
        floor.add_item(order, self.tea, cashier, quantity=2)
        self.post(order, action="pay", cashbox=self.cashbox.pk, tendered="30", discount="0")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PAID)
