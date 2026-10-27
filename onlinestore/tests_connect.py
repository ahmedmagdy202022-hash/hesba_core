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


class PullReviewTests(StoreSetup):
    """Codex #283: VAT is each order's own, WooCommerce amounts are before tax, and a long pull carries on, never cut."""

    def setUp(self):
        super().setUp()
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        caps.set_capability_enabled(ClientProfile.get_active(), "vat", True, user=self.owner)

    def pull_rows(self, env, store, rows):
        def fake(url, headers):
            return 200, ({"orders": rows} if "myshopify.com" in url else rows)

        with mock.patch.dict(os.environ, env), mock.patch.object(connect, "_http", fake), \
             mock.patch("taxes.services.rates_by_item", return_value={str(self.dress.pk): "14.00", str(self.scarf.pk): "14.00"}):
            return connect.pull(store, self.shop, self.owner)

    def test_each_order_says_whether_its_own_prices_include_vat(self):
        inclusive = dict(SHOPIFY_ORDER, id=6001, name="#2001", taxes_included=True, total_discounts="0", shipping_lines=[],
                         line_items=[{"sku": "DR-1", "quantity": 1, "price": "570.00"}])
        exclusive = dict(SHOPIFY_ORDER, id=6002, name="#2002", taxes_included=False, total_discounts="0", shipping_lines=[],
                         line_items=[{"sku": "DR-1", "quantity": 1, "price": "500.00"}])
        result = self.pull_rows(SHOPIFY_ENV, "shopify", [inclusive, exclusive])
        self.assertEqual([row["number"] for row in result["made"]], ["2001", "2002"])
        prices = {row["number"]: row["invoice"].lines.get().unit_sale_price for row in result["made"]}
        self.assertEqual(prices, {"2001": D("500.00"), "2002": D("500.00")})  # the VAT came out of the inclusive one only

    def test_woocommerce_amounts_are_before_tax_whatever_the_shop_setting(self):
        row = dict(WOO_ORDER, prices_include_tax=True)
        invoice = self.pull_rows(WOO_ENV, "woocommerce", [row])["made"][0]["invoice"]
        prices = list(invoice.lines.order_by("line_number").values_list("unit_sale_price", flat=True))
        self.assertEqual(prices, [D("500.00"), D("100.00")])  # not cut down as if the VAT were inside
        self.assertGreater(invoice.total_amount, D("550.00"))  # the VAT goes on top of the 550 before tax

    def test_a_shopify_pull_past_the_page_limit_carries_on_from_where_it_stopped(self):
        calls = []

        def fake(url, headers):
            calls.append(url)
            if "since_id=0" in url:
                return 200, {"orders": [{"id": 1000 + n, "name": f"#{n}", "line_items": []} for n in range(250)]}
            return 200, {"orders": [{"id": 9000, "name": "#9000", "line_items": []}]}

        with mock.patch.dict(os.environ, SHOPIFY_ENV), mock.patch.object(connect, "_http", fake):
            _source, first, more = connect.fetch("shopify", max_pages=1)
            self.assertEqual((len(first), more.split("|")[1]), (250, "1249"))
            _source, rest, done = connect.fetch("shopify", more=more, max_pages=1)
        self.assertEqual((list(rest), done), (["9000"], ""))
        window = [part for part in calls[0].split("&") if part.startswith("created_at_min=")]
        self.assertIn(window[0], calls[1])  # the same window, from the next order
        self.assertIn("since_id=1249", calls[1])

    def test_a_woocommerce_pull_carries_on_from_the_next_page(self):
        def fake(url, headers):
            if "page=1&" in url:
                return 200, [{"id": n, "number": str(n), "line_items": []} for n in range(100)]
            return 200, [{"id": 500, "number": "500", "line_items": []}]

        with mock.patch.dict(os.environ, WOO_ENV), mock.patch.object(connect, "_http", fake):
            _source, first, more = connect.fetch("woocommerce", max_pages=1)
            self.assertEqual((len(first), more.split("|")[1]), (100, "2"))
            self.assertEqual(list(connect.fetch("woocommerce", more=more)[1]), ["500"])
            _source, every, done = connect.fetch("woocommerce", max_pages=None)
        self.assertEqual((len(every), done), (101, ""))

    def test_the_board_offers_to_carry_on_and_the_command_reads_every_page(self):
        from collections import OrderedDict

        self.client.force_login(self.owner)
        with mock.patch.dict(os.environ, SHOPIFY_ENV), \
             mock.patch.object(connect, "fetch", return_value=("shopify", OrderedDict(), "2026-10-20T00:00:00Z|77")) as fetch:
            page = self.client.post("/store/", {"pull": "shopify", "location": self.shop.pk, "lang": "ar"})
            self.assertContains(page, 'data-pull-more="shopify"')
            self.assertContains(page, 'name="more" value="2026-10-20T00:00:00Z|77"')
            self.client.post("/store/", {"pull": "shopify", "location": self.shop.pk, "more": "2026-10-20T00:00:00Z|77"})
            self.assertEqual(fetch.call_args.args[2], "2026-10-20T00:00:00Z|77")
        with mock.patch.dict(os.environ, SHOPIFY_ENV), mock.patch.object(connect, "pull", return_value={"made": [], "again": [], "skipped": [], "more": ""}) as pull:
            call_command("pull_store_orders", location="SHOP", user=self.owner.username, verbosity=0)
        self.assertIsNone(pull.call_args.kwargs["max_pages"])


class WooLineTests(StoreSetup):
    """Codex #283: a WooCommerce line keeps its exact amount and its own coupon; carrying a pull on keeps its settings."""

    def pull(self, rows):
        with mock.patch.dict(os.environ, WOO_ENV), mock.patch.object(connect, "_http", lambda url, headers: (200, rows)):
            return connect.pull("woocommerce", self.shop, self.owner)

    def test_a_subtotal_that_does_not_divide_evenly_is_kept(self):
        row = dict(WOO_ORDER, discount_total="0", total="100.49", line_items=[{"sku": "SC-1", "quantity": 100, "subtotal": "100.49", "total": "100.49"}])
        invoice = self.pull([row])["made"][0]["invoice"]
        self.assertEqual(invoice.total_amount, D("100.49"))

    def test_a_coupon_stays_on_the_lines_it_was_given_to(self):
        row = dict(WOO_ORDER, line_items=[{"sku": "DR-1", "quantity": 1, "subtotal": "500.00", "total": "500.00"},
                                          {"sku": "SC-1", "quantity": 1, "subtotal": "100.00", "total": "50.00"}])
        invoice = self.pull([row])["made"][0]["invoice"]
        discounts = list(invoice.lines.order_by("line_number").values_list("line_discount_amount", flat=True))
        self.assertEqual(discounts, [D("0"), D("50.00")])  # not 41.67 / 8.33 spread over both
        self.assertEqual(invoice.total_amount, D("550.00"))

    def test_carrying_on_keeps_the_location_shipping_item_and_replace(self):
        from collections import OrderedDict

        from hesba_testing.factories import make_item, make_location

        other = make_location(location_code="SHOP-2", is_selling_location=True)
        fee = make_item(item_code="SHIP", item_name="شحن", default_sale_price="0", is_stock_tracked=False)
        self.client.force_login(self.owner)
        with mock.patch.dict(os.environ, WOO_ENV), mock.patch.object(connect, "fetch", return_value=("template", OrderedDict(), "2026-10-20T00:00:00Z|2")):
            page = self.client.post("/store/", {"pull": "woocommerce", "location": other.pk, "shipping_item": fee.pk, "replace": "1", "lang": "ar"})
        self.assertContains(page, f'<option value="{other.pk}" selected>')
        self.assertContains(page, f'<option value="{fee.pk}" selected>')
        self.assertContains(page, 'name="replace" value="1" checked')


class AllocationTests(StoreSetup):
    """Codex #283: Shopify's own per-line discounts are kept, its shipping is after its discount, and a WooCommerce
    order with extra fees is refused with the reason rather than imported short."""

    def pull(self, env, store, rows):
        def fake(url, headers):
            return 200, ({"orders": rows} if "myshopify.com" in url else rows)

        with mock.patch.dict(os.environ, env), mock.patch.object(connect, "_http", fake):
            return connect.pull(store, self.shop, self.owner)

    def test_a_shopify_coupon_stays_on_the_line_it_was_allocated_to(self):
        from hesba_testing.factories import make_item

        fee = make_item(item_code="SHIP", item_name="شحن", default_sale_price="0", is_stock_tracked=False)
        row = dict(SHOPIFY_ORDER, total_discounts="60.00", total_price="640.00",
                   shipping_lines=[{"price": "50.00", "discounted_price": "40.00"}],
                   line_items=[{"sku": "DR-1", "quantity": 1, "price": "500.00", "discount_allocations": []},
                               {"sku": "SC-1", "quantity": 2, "price": "100.00", "discount_allocations": [{"amount": "50.00"}]}])
        with mock.patch.dict(os.environ, SHOPIFY_ENV), mock.patch.object(connect, "_http", lambda url, headers: (200, {"orders": [row]})):
            invoice = connect.pull("shopify", self.shop, self.owner, shipping_item=fee)["made"][0]["invoice"]
        lines = {line.item.item_code: line for line in invoice.lines.select_related("item")}
        self.assertEqual((lines["DR-1"].line_discount_amount, lines["SC-1"].line_discount_amount), (D("0"), D("50.00")))
        self.assertEqual(lines["SHIP"].unit_sale_price, D("40.00"))
        self.assertEqual(invoice.total_amount, D("690.00"))  # 500 + 200 − 50 + 40

    def test_a_woocommerce_order_with_fees_is_refused_with_the_reason(self):
        row = dict(WOO_ORDER, fee_lines=[{"name": "Handling", "total": "10.00"}])
        result = self.pull(WOO_ENV, "woocommerce", [row])
        self.assertEqual(result["made"], [])
        self.assertIn("10.00", result["skipped"][0]["reason"])
        self.assertFalse(StoreOrder.objects.exists())  # tried again on the next pull, never marked imported
