"""T13 (live part): a connected Shopify or WooCommerce store's orders pulled from its API, into draft invoices once."""

import os
from decimal import Decimal as D
from unittest import mock

from django.core.management import call_command

from . import connect
from .models import StoreOrder
from .tests import StoreSetup

SHOPIFY_ENV = {"SHOPIFY_STORE": "my-shop.myshopify.com", "SHOPIFY_ACCESS_TOKEN": "shpat_x", "WOOCOMMERCE_URL": "", "WOOCOMMERCE_KEY": "", "WOOCOMMERCE_SECRET": ""}
WOO_ENV = {"SHOPIFY_STORE": "", "SHOPIFY_ACCESS_TOKEN": "", "WOOCOMMERCE_URL": "https://shop.example.com", "WOOCOMMERCE_KEY": "ck_1", "WOOCOMMERCE_SECRET": "cs_1"}

SHOPIFY_ORDER = {
    "id": 5001, "name": "#1001", "created_at": "2026-10-20T14:03:11+03:00", "email": "sara@example.com", "financial_status": "paid",
    "currency": "EGP", "total_price": "700.00", "total_discounts": "50.00", "taxes_included": False, "cancelled_at": None, "phone": None,
    "billing_address": {"name": "Sara Ahmed", "phone": "+20 100 123 4567"}, "shipping_address": {"address1": "12 Tahrir St", "city": "Cairo"},
    "shipping_lines": [{"price": "50.00"}],
    "line_items": [{"sku": "DR-1", "title": "Dress", "quantity": 1, "price": "500.00"}, {"sku": "SC-1", "title": "Scarf", "quantity": 2, "price": "100.00"}],
}
WOO_ORDER = {
    "id": 77, "number": "2044", "status": "processing", "currency": "EGP", "date_created": "2026-10-22T09:00:00", "total": "550.00",
    "discount_total": "50.00", "shipping_total": "0.00", "prices_include_tax": False,
    "billing": {"first_name": "Omar", "last_name": "Ali", "phone": "01112223334", "email": "omar@example.com", "address_1": "5 Nile St", "city": "Giza"},
    "shipping": {},
    "line_items": [{"sku": "DR-1", "name": "Dress", "quantity": 1, "subtotal": "500.00", "total": "450.00", "price": 450},
                   {"sku": "", "name": "SC-1", "quantity": 1, "subtotal": "100.00", "total": "100.00", "price": 100}],
}


class FakeStore:
    def __init__(self, status=200):
        self.status, self.calls = status, []

    def __call__(self, url, headers):
        self.calls.append({"url": url, "headers": headers})
        if self.status != 200:
            return self.status, {"errors": "Unauthorized"}
        if "myshopify.com" in url:
            return 200, {"orders": [SHOPIFY_ORDER]}
        return 200, [WOO_ORDER]


class PullTests(StoreSetup):
    def pull(self, env, store, fake=None):
        with mock.patch.dict(os.environ, env), mock.patch.object(connect, "_http", fake or FakeStore()):
            return connect.pull(store, self.shop, self.owner)

    def test_nothing_is_connected_by_default(self):
        with mock.patch.dict(os.environ, {key: "" for key in SHOPIFY_ENV}):
            self.assertEqual(connect.connected(), [])

    def test_shopify_orders_come_in_as_drafts_once(self):
        fake = FakeStore()
        result = self.pull(SHOPIFY_ENV, "shopify", fake)
        self.assertEqual([row["number"] for row in result["made"]], ["1001"])
        call = fake.calls[0]
        self.assertTrue(call["url"].startswith("https://my-shop.myshopify.com/admin/api/2024-10/orders.json?status=any"))
        self.assertEqual(call["headers"]["X-Shopify-Access-Token"], "shpat_x")
        invoice = StoreOrder.objects.get(order_number="1001").invoice
        self.assertEqual((invoice.status, invoice.total_amount, invoice.customer.name), ("draft", D("650.00"), "Sara Ahmed"))
        self.assertEqual(StoreOrder.objects.get(order_number="1001").store_total, D("700.00"))
        again = self.pull(SHOPIFY_ENV, "shopify")
        self.assertEqual((again["made"], again["again"]), ([], ["1001"]))

    def test_woocommerce_orders_come_in_with_the_coupon_as_a_discount(self):
        fake = FakeStore()
        result = self.pull(WOO_ENV, "woocommerce", fake)
        self.assertEqual([row["number"] for row in result["made"]], ["2044"])
        self.assertTrue(fake.calls[0]["url"].startswith("https://shop.example.com/wp-json/wc/v3/orders?"))
        self.assertTrue(fake.calls[0]["headers"]["Authorization"].startswith("Basic "))
        invoice = StoreOrder.objects.get(order_number="2044").invoice
        self.assertEqual(list(invoice.lines.order_by("line_number").values_list("item__item_code", "unit_sale_price")), [("DR-1", D("500.00")), ("SC-1", D("100.00"))])
        self.assertEqual(invoice.total_amount, D("550.00"))  # 600 before the 50 coupon

    def test_a_refused_call_says_so_and_makes_nothing(self):
        from django.core.exceptions import ValidationError

        with self.assertRaisesMessage(ValidationError, "shopify:401"):
            self.pull(SHOPIFY_ENV, "shopify", FakeStore(status=401))
        self.assertFalse(StoreOrder.objects.exists())

    def test_the_board_button_and_the_command(self):
        self.client.force_login(self.owner)
        with mock.patch.dict(os.environ, SHOPIFY_ENV), mock.patch.object(connect, "_http", FakeStore()):
            page = self.client.get("/store/")
            self.assertContains(page, 'data-pull="shopify"')
            done = self.client.post("/store/", {"pull": "shopify", "location": self.shop.pk, "lang": "ar"})
            self.assertContains(done, "data-store-made")
        self.assertTrue(StoreOrder.objects.filter(order_number="1001").exists())
        with mock.patch.dict(os.environ, WOO_ENV), mock.patch.object(connect, "_http", FakeStore()):
            call_command("pull_store_orders", location="SHOP", user=self.owner.username, verbosity=0)
        self.assertTrue(StoreOrder.objects.filter(order_number="2044").exists())
