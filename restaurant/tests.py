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
        for slug in catalog.MODULE_SLUGS:
            with self.subTest(slug=slug):
                self.assertContains(modules, f'data-module="{slug}"')
                self.assertRegex(modules.content.decode(), rf'data-module="{slug}"[^>]*data-restaurants-state="{catalog.preset_state("restaurants", slug)}"')


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
