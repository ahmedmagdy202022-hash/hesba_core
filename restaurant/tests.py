"""RESTO-001: tables, orders, the kitchen ticket, and billing through the cashier's own checkout."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from settings_core import setup_catalog as catalog
from settings_core.models import ActivityType

from . import services
from .models import DiningTable, KitchenTicket, Order, OrderKind, OrderStatus


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class RestaurantSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="restaurants", sub_activity="cafe",
                                       modules="items_services,sales_operations,cashboxes,reports,inventory,pdf_printing,tables_orders")
        self.owner = person(RoleCode.OWNER, "resto_owner")
        self.location = make_location(location_code="HALL", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.koshary = make_item(item_code="D-KOSH", item_name="كشري كبير", default_sale_price="60.00", is_stock_tracked=False)
        self.tea = make_item(item_code="D-TEA", item_name="شاي", default_sale_price="15.00", is_stock_tracked=False)
        self.cola = make_item(item_code="B-COLA", item_name="كولا كانز", default_sale_price="20.00", is_stock_tracked=True)
        stock_in(self.cola, self.location, 10, "9.00")
        self.t1 = services.save_table({"name": "1", "seats": 4}, self.owner)
        self.t2 = services.save_table({"name": "2", "area": "تراس", "seats": 2}, self.owner)


class ActivityTests(RestaurantSetup):
    def test_the_activity_maps_to_a_store_and_locks_tables_and_orders_on(self):
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.activity_slug, self.profile.activity_type), ("restaurants", ActivityType.STORE))
        self.assertIn("tables_orders", catalog.required_modules("restaurants"))
        self.assertNotIn("tables_orders", catalog.default_modules("commercial"))
        self.assertEqual(catalog.clean_module_slugs("restaurants", "customers"),
                         ("customers", "items_services", "sales_operations", "cashboxes", "reports", "tables_orders"))

    def test_the_wizard_offers_restaurants(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("setup_activity"))
        self.assertContains(page, 'data-activity="restaurants" data-next="/setup/activity/restaurants/"')
        sub = self.client.get(reverse("setup_activity_restaurants"))
        for slug in catalog.SUB_ACTIVITY_LABELS["restaurants"]:
            self.assertContains(sub, f'data-sub-activity="{slug}"')
        self.assertContains(sub, "url.searchParams.set('activity','restaurants')")
        modules = self.client.get("/setup/modules/?lang=ar&activity=restaurants&sub_activity=cafe")
        self.assertContains(modules, 'data-module="tables_orders" data-preset-state="required"')
        self.assertNotContains(modules, 'data-module="projects"')  # ACT-FIT: a café has no projects
        self.assertContains(modules, 'data-activity-feature="tables_board"')


class OrderTests(RestaurantSetup):
    def test_a_table_holds_one_open_order_and_opening_it_again_joins(self):
        order = services.open_order(self.owner, table=self.t1, guests=3)
        self.assertEqual((order.number[:2], order.kind, order.guests), ("R-", OrderKind.DINE_IN, 3))
        self.assertEqual(services.open_order(self.owner, table=self.t1), order)
        self.assertEqual(Order.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(number="R-X", table=self.t1, opened_by=self.owner)  # the database refuses too
        with self.assertRaisesMessage(ValidationError, "اختار الطاولة"):
            services.open_order(self.owner, table=None)
        with self.assertRaisesMessage(ValidationError, "الدليفري محتاج عنوان"):
            services.open_order(self.owner, kind=OrderKind.DELIVERY)
        delivery = services.open_order(self.owner, kind=OrderKind.DELIVERY, table=self.t2, address="12 ش النصر")
        self.assertIsNone(delivery.table)  # takeaway and delivery never hold a table

    def test_lines_merge_until_sent_then_can_only_be_voided(self):
        order = services.open_order(self.owner, table=self.t1)
        services.add_item(order, self.koshary, self.owner)
        line = services.add_item(order, self.koshary, self.owner, 2)
        self.assertEqual((order.lines.count(), line.quantity), (1, D("3.000")))
        spicy = services.add_item(order, self.koshary, self.owner, 1, "سبايسي")  # a different note is a different plate
        self.assertEqual(order.lines.count(), 2)
        with self.assertRaisesMessage(ValidationError, "الكمية"):
            services.add_item(order, self.tea, self.owner, "0")
        ticket = services.send_to_kitchen(order, self.owner)
        self.assertEqual((ticket.sequence, ticket.lines.count()), (1, 2))
        self.assertIsNone(services.send_to_kitchen(order, self.owner))  # nothing new
        with self.assertRaisesMessage(ValidationError, "راح المطبخ"):
            services.change_line(spicy, self.owner, quantity="5")
        services.void_line(spicy, self.owner)
        spicy.refresh_from_db()
        self.assertTrue(spicy.voided)
        self.assertTrue(AuditLog.objects.filter(action="void_order_line").exists())
        tea = services.add_item(order, self.tea, self.owner, 2)
        self.assertEqual(services.send_to_kitchen(order, self.owner).lines.get(), tea)  # the second ticket has only the tea
        self.assertEqual(order.total, D("210.00"))  # 3 × 60 + 2 × 15; the void is out

    def test_unsent_lines_change_or_go_quietly(self):
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY)
        line = services.add_item(order, self.tea, self.owner)
        services.change_line(line, self.owner, quantity="4", note="سكر برة")
        line.refresh_from_db()
        self.assertEqual((line.quantity, line.note), (D("4.000"), "سكر برة"))
        services.change_line(line, self.owner, quantity="0")
        self.assertFalse(order.lines.exists())

    def test_move_between_tables_only_to_a_free_one(self):
        order = services.open_order(self.owner, table=self.t1)
        other = services.open_order(self.owner, table=self.t2)
        with self.assertRaisesMessage(ValidationError, "الطاولة 2 عليها طلب مفتوح"):
            services.move_table(order, self.t2, self.owner)
        services.cancel_order(other, self.owner, "مشيوا")
        services.move_table(order, self.t2, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.table, self.t2)
        self.assertEqual(services.open_order(self.owner, table=self.t1).table, self.t1)  # table 1 is free again
        with self.assertRaisesMessage(ValidationError, "اتقفل"):
            services.add_item(other, self.tea, self.owner)


class PaymentTests(RestaurantSetup):
    def test_paying_posts_one_invoice_through_checkout_with_stock_and_cash(self):
        order = services.open_order(self.owner, table=self.t1)
        services.add_item(order, self.koshary, self.owner, 2)
        services.add_item(order, self.cola, self.owner, 3)
        voided = services.add_item(order, self.tea, self.owner)
        services.send_to_kitchen(order, self.owner)
        services.void_line(voided, self.owner)
        invoice, change = services.pay(order, self.owner, cashbox=self.cashbox, discount=D("10"), tendered=D("200"))
        order.refresh_from_db()
        self.assertEqual((invoice.status, invoice.total_amount, invoice.paid_now, change), ("posted", D("170.00"), D("170.00"), D("30.00")))
        self.assertEqual((order.status, order.invoice), (OrderStatus.PAID, invoice))
        self.assertEqual(invoice.lines.count(), 2)  # the void never reaches the invoice
        self.assertEqual(StockMovement.objects.get(item=self.cola, movement_type="sale_out").quantity, D("3.000"))
        self.assertFalse(StockMovement.objects.filter(item=self.koshary).exists())  # a dish is a service item
        self.assertEqual(CashboxMovement.objects.get(sales_invoice=invoice).amount, D("170.00"))
        self.assertIn(order.number, invoice.notes)
        self.assertTrue(invoice.notes.startswith("طلب مطعم"))  # TRACK-ACTS W11: no English on an Arabic invoice
        self.assertNotIn("Restaurant", invoice.notes)
        with self.assertRaisesMessage(ValidationError, "اتقفل"):
            services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("200"))
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertEqual(services.open_order(self.owner, table=self.t1).status, OrderStatus.OPEN)  # the table is free

    def test_refusals_leave_the_order_open_and_nothing_posted(self):
        order = services.open_order(self.owner, table=self.t1)
        with self.assertRaisesMessage(ValidationError, "الطلب فاضي"):
            services.pay(order, self.owner, cashbox=self.cashbox)
        services.add_item(order, self.koshary, self.owner)
        with self.assertRaises(ValidationError):  # a walk-in cannot leave owing
            services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("10"))
        services.add_item(order, self.cola, self.owner, 11)
        with self.assertRaises(ValidationError):  # only 10 cans in stock
            services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("1000"))
        order.refresh_from_db()
        self.assertEqual((order.status, SalesInvoice.objects.count(), CashboxMovement.objects.filter(sales_invoice__isnull=False).count()), (OrderStatus.OPEN, 0, 0))

    def test_a_named_customer_can_pay_later(self):
        customer = make_customer(customer_code="C-9", name="شركة النور")
        order = services.open_order(self.owner, kind=OrderKind.DELIVERY, customer=customer, address="المعادي")
        services.add_item(order, self.koshary, self.owner)
        invoice, _ = services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("0"))
        self.assertEqual((invoice.customer, invoice.remaining_due), (customer, D("60.00")))


class TableTests(RestaurantSetup):
    def test_table_rules(self):
        for data, text in (({"name": ""}, "اسم أو رقم"), ({"name": "1"}, "بنفس الاسم"), ({"name": "9", "seats": "0"}, "الكراسي")):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, text):
                services.save_table(data, self.owner)
        services.open_order(self.owner, table=self.t1)
        with self.assertRaisesMessage(ValidationError, "طلبات مفتوحة"):
            services.save_table({"name": "1", "active": False}, self.owner, self.t1)
        services.save_table({"name": "2", "active": False}, self.owner, self.t2)
        with self.assertRaisesMessage(ValidationError, "مش شغالة"):
            services.open_order(self.owner, table=DiningTable.objects.get(name="2"))


class ScreenTests(RestaurantSetup):
    def test_take_an_order_send_it_and_pay_from_the_screens(self):
        cashier = person(RoleCode.CASHIER, "resto_cashier")
        self.client.force_login(cashier)
        board = self.client.get(reverse("restaurant:board"))
        self.assertContains(board, 'data-table="1" data-table-state="free"')
        opened = self.client.post(reverse("restaurant:board"), {"kind": "dine_in", "table": str(self.t1.pk)})
        order = Order.objects.get()
        self.assertRedirects(opened, f"{reverse('restaurant:order', args=[order.pk])}?lang=ar", fetch_redirect_response=False)
        url = reverse("restaurant:order", args=[order.pk])
        self.client.post(url, {"action": "add", "item": str(self.koshary.pk)})
        self.client.post(url, {"action": "add", "item": str(self.tea.pk), "quantity": "2"})
        page = self.client.get(url)
        self.assertContains(page, 'data-order-line="D-KOSH" data-line-state="new"')
        self.assertContains(page, "90.00")
        sent = self.client.post(url, {"action": "send"})
        ticket = KitchenTicket.objects.get()
        self.assertRedirects(sent, f"{reverse('restaurant:ticket', args=[ticket.pk])}?lang=ar&autoprint=1", fetch_redirect_response=False)
        printed = self.client.get(reverse("restaurant:ticket", args=[ticket.pk]))
        self.assertContains(printed, 'data-ticket-line="D-TEA"')
        self.assertContains(printed, "الطاولة 1")
        self.assertContains(self.client.get(reverse("restaurant:board")), 'data-table="1" data-table-state="busy"')
        paid = self.client.post(url, {"action": "pay", "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "100", "print": "1"})
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertRedirects(paid, f"/print/sales/{order.invoice.pk}/?lang=ar&format=receipt&autoprint=1", fetch_redirect_response=False)

    def test_an_engine_refusal_shows_in_arabic_on_the_order(self):
        self.client.force_login(self.owner)
        order = services.open_order(self.owner, table=self.t1)
        services.add_item(order, self.cola, self.owner, 11)
        page = self.client.post(reverse("restaurant:order", args=[order.pk]), {"action": "pay", "cashbox": str(self.cashbox.pk), "tendered": "1000"}, follow=True)
        self.assertContains(page, "الكمية مش كفاية")

    def test_permissions_and_module_gate(self):
        cashier = person(RoleCode.CASHIER, "resto_cashier2")
        self.client.force_login(cashier)
        self.assertEqual(self.client.post(reverse("restaurant:tables"), {"name": "9"}).status_code, 403)
        self.assertFalse(DiningTable.objects.filter(name="9").exists())
        keeper = person(RoleCode.STOCK_KEEPER, "resto_keeper")
        self.client.force_login(keeper)
        self.assertIn(self.client.post(reverse("restaurant:board"), {"kind": "dine_in", "table": str(self.t1.pk)}).status_code, (302, 403))
        self.assertFalse(Order.objects.exists())
        self.client.force_login(self.owner)
        self.client.post(reverse("restaurant:tables"), {"name": "9", "seats": "6", "active": "on"})
        self.assertTrue(DiningTable.objects.filter(name="9", seats=6).exists())
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), reverse("restaurant:board"))
        from settings_core.setup_services import ModuleChangeRefused, set_module_enabled

        with self.assertRaises(ModuleChangeRefused):  # required for a restaurant
            set_module_enabled(self.profile, "tables_orders", False, self.owner)


class RestaurantInsightTests(RestaurantSetup):
    """RESTO-002: dish costs and margins, and the restaurant report."""

    def setUp(self):
        super().setUp()
        self.rice = make_item(item_code="R-RICE", item_name="أرز", unit="كجم", is_stock_tracked=True)
        self.lentil = make_item(item_code="R-LENT", item_name="عدس", unit="كجم", is_stock_tracked=True)
        stock_in(self.rice, self.location, 20, "30.00")
        stock_in(self.lentil, self.location, 10, "50.00")
        from inventory.services import recalculate_item_average_cost

        for item in (self.rice, self.lentil):
            recalculate_item_average_cost(item)
        self.client.force_login(self.owner)

    def paid(self, *, table=None, items=(), guests=2, kind="dine_in"):
        from . import services as floor

        order = floor.open_order(self.owner, kind=kind, table=table, guests=guests)
        for item in items:
            floor.add_item(order, item, self.owner)
        floor.pay(order, self.owner, cashbox=self.cashbox, tendered=D("100000"))
        order.refresh_from_db()
        return order

    def test_a_dish_costs_its_ingredients_and_shows_its_margin(self):
        from . import insights

        url = reverse("restaurant:dish", args=[self.koshary.pk])
        self.client.post(url, {"ingredient": [self.rice.pk, self.lentil.pk, ""], "quantity": ["0.200", "0.100", ""]})
        self.assertEqual(insights.plate_cost(self.koshary), D("11.00"))  # 0.2 × 30 + 0.1 × 50
        rows = {row["dish"].pk: row for row in insights.menu_costing([self.koshary, self.tea])}
        self.assertEqual((rows[self.koshary.pk]["margin"], rows[self.koshary.pk]["margin_pct"]), (D("49.00"), 82))
        self.assertIsNone(rows[self.tea.pk]["cost"])
        page = self.client.get(reverse("restaurant:costing"))
        self.assertContains(page, 'data-margin-pct="82"')
        self.assertContains(self.client.get(reverse("restaurant:board")), "data-resto-costing")
        log = AuditLog.objects.filter(action="save_dish_ingredients").latest("pk")
        self.assertEqual(len(log.after_data["ingredients"]), 2)
        before = StockMovement.objects.count()
        self.paid(table=self.t1, items=[self.koshary])
        self.assertEqual(StockMovement.objects.count(), before)  # selling a dish takes no ingredient out of stock (HG-044)

    def test_bad_ingredients_are_refused(self):
        from . import insights

        for lines, message in (([(self.tea, "1")], "صنف مخزني"), ([(self.rice, "0")], "أكبر من صفر"), ([(self.rice, "abc")], "أكبر من صفر"),
                               ([(self.rice, "1"), (self.rice, "2")], "متكرر"), ([(self.rice, "0.0004")], "أكبر من صفر")):
            with self.subTest(lines=lines), self.assertRaisesMessage(ValidationError, message):
                insights.save_ingredients(self.koshary, lines, self.owner)
        response = self.client.post(reverse("restaurant:dish", args=[self.koshary.pk]), {"ingredient": [self.rice.pk], "quantity": ["x"]})
        self.assertContains(response, 'value="x"')

    def test_a_deactivated_ingredient_still_saves_and_a_future_end_is_today(self):
        from . import insights

        insights.save_ingredients(self.koshary, [(self.rice, "0.2"), (self.lentil, "0.1")], self.owner)
        self.rice.active = False
        self.rice.save()
        url = reverse("restaurant:dish", args=[self.koshary.pk])
        self.assertContains(self.client.get(url), f'value="{self.rice.pk}" selected')
        self.client.post(url, {"ingredient": [self.rice.pk, self.lentil.pk], "quantity": ["0.3", "0.1"]})
        self.assertEqual(self.koshary.dish_ingredients.get(ingredient=self.rice).quantity, D("0.300"))
        from datetime import timedelta

        from django.utils import timezone

        page = self.client.get(reverse("restaurant:report") + f"?to={(timezone.localdate() + timedelta(days=60)).isoformat()}")
        self.assertEqual(page.context["last"], timezone.localdate())

    def test_the_report_counts_paid_orders_by_hour_dish_table_and_waiter(self):
        from datetime import datetime, time, timedelta

        from django.utils import timezone

        from . import insights

        first = self.paid(table=self.t1, items=[self.koshary, self.koshary, self.tea], guests=3)  # 135
        second = self.paid(table=self.t1, items=[self.tea])  # 15
        third = self.paid(kind="takeaway", items=[self.koshary])  # 60
        today = timezone.localdate()
        noon = timezone.make_aware(datetime.combine(today, time(13, 10)))
        Order.objects.filter(pk=first.pk).update(opened_at=noon - timedelta(minutes=50), closed_at=noon)
        Order.objects.filter(pk=second.pk).update(opened_at=noon - timedelta(minutes=10), closed_at=noon + timedelta(minutes=30))
        Order.objects.filter(pk=third.pk).update(closed_at=timezone.make_aware(datetime.combine(today, time(20, 5))))
        cancelled = services.open_order(self.owner, table=self.t2)
        services.add_item(cancelled, self.koshary, self.owner)
        services.cancel_order(cancelled, self.owner, "غلط")
        data = insights.period_report(today.replace(day=1), today)
        totals = data["totals"]
        self.assertEqual((totals["orders"], totals["sales"], totals["average"]), (3, D("210.00"), D("70.00")))
        self.assertEqual([(row["hour"], row["orders"]) for row in data["hours"]], [(13, 2), (20, 1)])
        self.assertEqual(data["peak"]["hour"], 13)
        self.assertEqual([(row["item"].pk, row["quantity"]) for row in data["dishes"]], [(self.koshary.pk, D("3.000")), (self.tea.pk, D("2.000"))])
        table = data["tables"][0]
        self.assertEqual((table["table"], table["orders"], table["average"], table["minutes"]), (self.t1, 2, D("75.00"), 45))
        self.assertEqual({row["kind"]: row["orders"] for row in data["kinds"]}, {"dine_in": 2, "takeaway": 1})
        page = self.client.get(reverse("restaurant:report"))
        self.assertContains(page, 'data-r-hour="13"')
        self.assertContains(page, "210.00")
        self.assertNotContains(page, "0,1")  # no locale decimal comma in the per-day figure
        self.assertNotContains(page, "data-r-others")  # every sale here is a restaurant order
        # TRACK-ACTS W14: a sale made outside the floor is named, so the report agrees with the dashboard.
        from sales.pos import checkout, walk_in_customer

        checkout(lines=[{"item": self.tea, "quantity": D("1"), "unit_sale_price": D("15.00"), "line_discount_amount": D("0")}],
                 customer=walk_in_customer(), location=self.location, cashbox=self.cashbox, discount=D("0"), tendered=D("15.00"), user=self.owner)
        others = self.client.get(reverse("restaurant:report")).content.decode()
        self.assertIn("data-r-others", others)
        self.assertIn("15.00", others)
        self.assertIn("225.00", others)  # 210.00 of orders + 15.00 at the counter
        self.assertEqual(self.client.get(reverse("restaurant:report") + "?lang=en&from=bad").status_code, 200)

    def test_waste_lists_stock_taken_out_by_adjustment(self):
        from django.utils import timezone

        from inventory.services import adjust_stock, cancel_stock_operation

        from . import insights

        today = timezone.localdate()
        adjust_stock("W-1", today, self.rice, self.location, "out", "2", "هالك — اتحرق", self.owner)
        mistake = adjust_stock("W-2", today, self.lentil, self.location, "out", "1", "غلط", self.owner)
        cancel_stock_operation(mistake.pk, today, "غلط في التسجيل", self.owner)
        rows, total = insights.waste(today.replace(day=1), today)
        self.assertEqual([(row["movement"].item_id, row["reason"]) for row in rows], [(self.rice.pk, "هالك — اتحرق")])
        self.assertEqual(total, D("60.00"))
        self.assertContains(self.client.get(reverse("restaurant:report")), "هالك — اتحرق")

    def test_who_sees_the_report_and_the_costs(self):
        from permissions.services import user_has_permission

        cashier = person(RoleCode.CASHIER, "resto_cashier2")
        self.client.force_login(cashier)
        if user_has_permission(cashier, "reports.view_all_sales_report") or user_has_permission(cashier, "inventory.view_cost"):
            self.skipTest("the seeded cashier's rights changed")
        self.assertEqual(self.client.get(reverse("restaurant:report")).status_code, 403)
        self.assertEqual(self.client.get(reverse("restaurant:costing")).status_code, 403)
        board = self.client.get(reverse("restaurant:board"))
        self.assertNotContains(board, "data-resto-report")
        self.assertNotContains(board, "data-resto-costing")


class DeliveryTests(RestaurantSetup):
    """RESTO-003: the driver, when it left, and the delivery fee on the same invoice."""

    def setUp(self):
        super().setUp()
        from staff.services import save_employee

        self.driver = save_employee({"name": "محمود الطيار"}, self.owner)
        self.client.force_login(self.owner)

    def test_a_delivery_carries_its_fee_and_driver_to_one_invoice(self):
        from cashboxes.services import get_cashbox_balance

        self.client.post(reverse("restaurant:board"), {"kind": "delivery", "address": "شارع النصر 5", "phone": "01001234567", "delivery_fee": "15"})
        order = Order.objects.get(kind=OrderKind.DELIVERY)
        self.assertEqual(order.delivery_fee, D("15.00"))
        services.add_item(order, self.koshary, self.owner, 2)
        url = reverse("restaurant:order", args=[order.pk])
        self.assertContains(self.client.get(url), "data-dispatch")
        self.client.post(url, {"action": "dispatch", "driver": self.driver.pk, "delivery_fee": "20"})
        order.refresh_from_db()
        self.assertEqual((order.driver, order.delivery_fee, order.dispatched_at is not None, order.total), (self.driver, D("20.00"), True, D("140.00")))
        before = get_cashbox_balance(self.cashbox)
        invoice, _ = services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("140"))
        self.assertEqual(invoice.total_amount, D("140.00"))
        fee = invoice.lines.get(item__item_code="DELIVERY-FEE")
        self.assertEqual((fee.line_total_amount, fee.item.is_stock_tracked), (D("20.00"), False))
        self.assertEqual(get_cashbox_balance(self.cashbox) - before, D("140.00"))
        second = services.open_order(self.owner, kind="delivery", address="x", delivery_fee="10")
        services.add_item(second, self.tea, self.owner)
        services.pay(second, self.owner, cashbox=self.cashbox, tendered=D("100"))
        from master_data.models import Item

        self.assertEqual(Item.objects.filter(item_code="DELIVERY-FEE").count(), 1)  # made once

    def test_a_delivery_without_a_fee_has_no_fee_line_and_the_rules(self):
        order = services.open_order(self.owner, kind="delivery", address="x")
        services.add_item(order, self.tea, self.owner)
        invoice, _ = services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("15"))
        self.assertFalse(invoice.lines.filter(item__item_code="DELIVERY-FEE").exists())
        with self.assertRaisesMessage(ValidationError, "رسوم التوصيل"):
            services.open_order(self.owner, kind="delivery", address="x", delivery_fee="-5")
        hall = services.open_order(self.owner, table=self.t1, delivery_fee="30")
        self.assertEqual(hall.delivery_fee, D("0"))  # only deliveries carry a fee
        with self.assertRaisesMessage(ValidationError, "مش طلب دليفري"):
            services.dispatch(hall, self.driver, self.owner)
        open_one = services.open_order(self.owner, kind="delivery", address="y")
        with self.assertRaisesMessage(ValidationError, "الطيار"):
            services.dispatch(open_one, None, self.owner)
        services.dispatch(open_one, self.driver, self.owner)
        self.assertEqual(AuditLog.objects.filter(action="dispatch_delivery").count(), 1)

    def test_the_fee_item_stays_off_the_menu_and_never_moves_stock(self):
        from master_data.models import Item

        order = services.open_order(self.owner, kind="delivery", address="x", delivery_fee="15")
        services.add_item(order, self.tea, self.owner)
        services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("100"))
        fee = Item.objects.get(item_code=services.FEE_ITEM_CODE)
        other = services.open_order(self.owner, kind="delivery", address="y")
        page = self.client.get(reverse("restaurant:order", args=[other.pk]))
        self.assertContains(page, f'value="{self.tea.pk}"')
        self.assertNotContains(page, f'value="{fee.pk}"')  # not a dish to add
        self.assertNotContains(self.client.get(reverse("restaurant:costing")), "رسوم توصيل")
        Item.objects.filter(pk=fee.pk).update(is_stock_tracked=True)
        third = services.open_order(self.owner, kind="delivery", address="z", delivery_fee="10")
        services.add_item(third, self.tea, self.owner)
        with self.assertRaisesMessage(ValidationError, "صنف مخزني"):
            services.pay(third, self.owner, cashbox=self.cashbox, tendered=D("100"))

    def test_emptying_the_fee_on_the_dispatch_form_waives_it(self):
        order = services.open_order(self.owner, kind="delivery", address="x", delivery_fee="15")
        response = self.client.post(reverse("restaurant:order", args=[order.pk]), {"action": "dispatch", "driver": self.driver.pk, "delivery_fee": ""},
                                    follow=True)
        self.assertContains(response, "الطلب خرج مع الطيار.")
        order.refresh_from_db()
        self.assertEqual(order.delivery_fee, D("0.00"))

    def test_the_report_counts_deliveries_per_driver(self):
        from datetime import timedelta

        from django.utils import timezone

        from . import insights

        order = services.open_order(self.owner, kind="delivery", address="x", delivery_fee="15")
        services.add_item(order, self.koshary, self.owner)
        services.dispatch(order, self.driver, self.owner)
        services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("75"))
        order.refresh_from_db()
        Order.objects.filter(pk=order.pk).update(dispatched_at=order.closed_at - timedelta(minutes=25))
        today = timezone.localdate()
        rows = insights.deliveries(today.replace(day=1), today)
        self.assertEqual([(row["driver"], row["orders"], row["fees"], row["minutes"]) for row in rows], [(self.driver, 1, D("15.00"), 25)])
        self.assertContains(self.client.get(reverse("restaurant:report")), f'data-r-driver="{self.driver.pk}"')
