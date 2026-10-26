"""T13 (MARKET-PARITY): the online store by file — the catalogue out in the store's own CSV, its orders in as drafts."""

import csv
import io
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from master_data.models import Customer
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from variants.models import Variant, VariantGroup

from . import services
from .models import StoreOrder

TODAY = timezone.localdate()

SHOPIFY_ORDERS = """Name,Email,Financial Status,Paid at,Fulfillment Status,Currency,Subtotal,Shipping,Taxes,Total,Discount Code,Discount Amount,Created at,Lineitem quantity,Lineitem name,Lineitem price,Lineitem sku,Billing Name,Billing Phone,Shipping Address1,Shipping City
#1001,sara@example.com,pending,,unfulfilled,EGP,700.00,50.00,0.00,700.00,EID,50.00,2026-10-20 14:03:11 +0300,1,Dress,500.00,DR-1,Sara Ahmed,+20 100 123 4567,12 Tahrir St,Cairo
#1001,,,,,,,,,,,,,2,Scarf,100.00,SC-1,,,,
#1002,omar@example.com,paid,,,EGP,500.00,0.00,0.00,500.00,,0.00,2026-10-21 10:00:00 +0300,1,Dress,500.00,DR-1,Omar Ali,01112223334,5 Nile St,Giza
#1003,x@example.com,refunded,,,EGP,100.00,0.00,0.00,100.00,,0.00,2026-10-21 11:00:00 +0300,1,Scarf,100.00,SC-1,Refunded One,01000000000,,
#1004,y@example.com,paid,,,EGP,100.00,0.00,0.00,100.00,,0.00,2026-10-21 12:00:00 +0300,1,Unknown,100.00,NOPE-9,Someone,01000000001,,
"""


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


class StoreSetup(TestCase):
    def setUp(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        prepared_client("commercial", "fashion", modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports")
        caps.set_capability_enabled(ClientProfile.get_active(), "online_store", True)
        self.owner = person(RoleCode.OWNER, "st_owner")
        self.shop = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.store_room = make_location(location_code="BACK", is_selling_location=False)
        self.dress = make_item(item_code="DR-1", item_name="فستان", default_sale_price="500.00")
        self.scarf = make_item(item_code="SC-1", item_name="إيشارب", default_sale_price="100.00", barcode="6221000000011")
        stock_in(self.dress, self.shop, D("3.5"), "200.00", movement_date=TODAY)
        stock_in(self.dress, self.store_room, 10, "200.00", movement_date=TODAY)  # not for sale: not offered online


class ExportTests(StoreSetup):
    def test_shopify_gets_its_own_columns_with_the_selling_stock(self):
        text = services.export_csv("shopify", self.owner)
        self.assertTrue(text.startswith("Handle,Title,Body (HTML),Vendor,"))  # no BOM before the first heading
        dress = next(row for row in rows(text) if row["Variant SKU"] == "DR-1")
        self.assertEqual((dress["Handle"], dress["Title"], dress["Variant Price"], dress["Variant Inventory Qty"]), ("dr-1", "فستان", "500.00", "3"))
        self.assertEqual((dress["Option1 Name"], dress["Option1 Value"], dress["Variant Inventory Policy"]), ("Title", "Default Title", "deny"))
        scarf = next(row for row in rows(text) if row["Variant SKU"] == "SC-1")
        self.assertEqual((scarf["Variant Inventory Qty"], scarf["Variant Barcode"]), ("0", "6221000000011"))

    def test_woocommerce_gets_its_importer_columns(self):
        text = services.export_csv("woocommerce", self.owner)
        dress = next(row for row in rows(text) if row["SKU"] == "DR-1")
        self.assertEqual((dress["Type"], dress["Name"], dress["Regular price"], dress["Stock"], dress["In stock?"], dress["Published"]),
                         ("simple", "فستان", "500.00", "3", "1", "1"))
        self.assertEqual(next(row for row in rows(text) if row["SKU"] == "SC-1")["In stock?"], "0")

    def test_a_size_and_colour_group_is_one_product_with_its_options(self):
        group = VariantGroup.objects.create(code="TEE", name="تيشيرت", sizes=["S", "M"], colors=["Red"], default_sale_price=D("150"))
        for size in ("S", "M"):
            item = make_item(item_code=f"TEE-{size}", item_name=f"تيشيرت {size}", default_sale_price="150.00")
            Variant.objects.create(group=group, item=item, size=size, color="Red")
        shopify = [row for row in rows(services.export_csv("shopify", self.owner)) if row["Handle"] == "tee"]
        self.assertEqual([row["Variant SKU"] for row in shopify], ["TEE-M", "TEE-S"])
        self.assertEqual((shopify[0]["Title"], shopify[0]["Option1 Name"], shopify[0]["Option2 Name"]), ("تيشيرت", "Size", "Color"))
        self.assertEqual((shopify[1]["Title"], shopify[1]["Option1 Value"], shopify[1]["Option2 Value"]), ("", "S", "Red"))
        woo = [row for row in rows(services.export_csv("woocommerce", self.owner)) if row["SKU"] == "TEE" or row["Parent"] == "TEE"]
        self.assertEqual(woo[0]["Type"], "variable")
        self.assertEqual((woo[0]["Attribute 1 name"], woo[0]["Attribute 1 value(s)"]), ("Size", "M, S"))
        self.assertEqual({row["Type"] for row in woo[1:]}, {"variation"})

    def test_handles_never_collide(self):
        make_item(item_code="DR_1", item_name="فستان تاني", default_sale_price="400.00")  # cleans to dr-1 like DR-1
        handles = [row["Handle"] for row in rows(services.export_csv("shopify", self.owner)) if row["Variant SKU"] in ("DR-1", "DR_1")]
        self.assertEqual(len(set(handles)), 2)

    def test_a_group_never_shares_its_woocommerce_sku_with_an_item(self):
        make_item(item_code="TEE", item_name="تيشيرت لوحده", default_sale_price="90.00")
        group = VariantGroup.objects.create(code="TEE", name="تيشيرت", sizes=["S"], colors=[])
        Variant.objects.create(group=group, item=make_item(item_code="TEE-S", item_name="تيشيرت S", default_sale_price="150.00"), size="S")
        woo = rows(services.export_csv("woocommerce", self.owner))
        parent = next(row for row in woo if row["Type"] == "variable")
        self.assertEqual(parent["SKU"], f"TEE-group-{group.pk}")
        self.assertEqual(next(row for row in woo if row["SKU"] == "TEE-S")["Parent"], parent["SKU"])
        self.assertEqual([row["SKU"] for row in woo].count("TEE"), 1)

    def test_the_export_needs_the_items_permission(self):
        with self.assertRaises(PermissionDenied):
            services.export_csv("shopify", person(RoleCode.WAITER, "st_waiter"))


class OrderImportTests(StoreSetup):
    def upload(self, text=SHOPIFY_ORDERS, user=None):
        return services.import_orders(text.encode("utf-8"), self.shop, user or self.owner)

    def test_shopify_orders_become_drafts_once(self):
        known = make_customer(customer_code="C-77", name="عمر", phone="011 1222 3334")
        result = self.upload()
        self.assertEqual([row["number"] for row in result["made"]], ["1001", "1002"])
        self.assertEqual({row["number"]: row["reason"][:20] for row in result["skipped"]}.keys(), {"1003", "1004"})
        first = StoreOrder.objects.get(order_number="1001").invoice
        self.assertEqual(first.status, "draft")  # nothing posted: stock and cash move only when it is posted
        self.assertEqual(list(first.lines.order_by("line_number").values_list("item__item_code", "quantity", "unit_sale_price")),
                         [("DR-1", D("1.000"), D("500.00")), ("SC-1", D("2.000"), D("100.00"))])
        self.assertEqual((first.discount_amount, first.total_amount, first.invoice_date.isoformat()), (D("0"), D("650.00"), "2026-10-20"))
        self.assertEqual(sum(line.line_discount_amount for line in first.lines.all()), D("50.00"))  # on the lines, before VAT
        self.assertIn("50.00", first.notes)  # the shipping the store charged
        self.assertEqual((first.customer.name, first.customer.phone), ("Sara Ahmed", "+20 100 123 4567"))
        self.assertTrue(first.invoice_number.startswith("WEB-"))
        self.assertIn("1001", first.notes)  # the store's own number
        self.assertEqual(StoreOrder.objects.get(order_number="1002").invoice.customer, known)  # found by phone
        self.assertEqual(StoreOrder.objects.get(order_number="1001").store_total, D("700.00"))
        again = self.upload()
        self.assertEqual((again["made"], again["again"]), ([], ["1001", "1002"]))
        self.assertEqual(SalesInvoice.objects.filter(invoice_number__startswith="WEB-").count(), 2)

    def test_the_template_works_in_arabic_and_by_barcode(self):
        text = "رقم الطلب,التاريخ,العميل,التليفون,الكود,الكمية,السعر\n55,2026-10-22,منى,01000000055,6221000000011,٣,90\n"
        result = self.upload(text)
        self.assertEqual(result["source"], "template")
        invoice = StoreOrder.objects.get(order_number="55").invoice
        self.assertEqual(list(invoice.lines.values_list("item", "quantity", "unit_sale_price")), [(self.scarf.pk, D("3.000"), D("90.00"))])

    def test_without_the_customers_permission_the_order_goes_to_the_walk_in_customer(self):
        cashier = person(RoleCode.CASHIER, "st_cashier")
        before = Customer.objects.count()
        result = services.import_orders(SHOPIFY_ORDERS.encode("utf-8"), self.shop, cashier)
        invoice = result["made"][0]["invoice"]
        self.assertEqual(invoice.customer.customer_code, "WALK-IN")
        self.assertIn("Sara Ahmed", invoice.notes)
        self.assertEqual(Customer.objects.exclude(customer_code="WALK-IN").count(), before)

    def test_numbers_keep_their_decimal_point(self):
        words = services.MESSAGES["en"]
        for text, value in (("١٢٫٥٠", "12.50"), ("12,50", "12.50"), ("1,234.50", "1234.50"), ("1,234", "1234"), ("EGP 99.90", "99.90"), ("٣", "3")):
            self.assertEqual(services._decimal(text, words), D(value), text)
        from django.core.exceptions import ValidationError

        for bad in ("12.5.0", "1-2", "abc"):
            with self.assertRaises(ValidationError, msg=bad):
                services._decimal(bad, words)

    def test_order_numbers_never_collapse(self):
        text = "order,sku,quantity,price\nA_1,DR-1,1,500\nA1,DR-1,1,500\n"
        self.assertEqual([row["number"] for row in self.upload(text)["made"]], ["A_1", "A1"])
        self.assertEqual(StoreOrder.objects.values("invoice__invoice_number").distinct().count(), 2)

    def test_shipping_on_a_service_line_vat_inclusive_prices_and_replacing_a_draft(self):
        fee = make_item(item_code="SHIP", item_name="شحن", default_sale_price="0", is_stock_tracked=False)
        result = services.import_orders(SHOPIFY_ORDERS.encode("utf-8"), self.shop, self.owner, shipping_item=fee)
        first = result["made"][0]["invoice"]
        self.assertEqual(first.lines.get(item=fee).unit_sale_price, D("50.00"))
        self.assertEqual(first.total_amount, D("700.00"))  # 500 + 200 + 50 shipping − 50 discount: the store's total
        again = services.import_orders(SHOPIFY_ORDERS.encode("utf-8"), self.shop, self.owner, replace=True)
        self.assertEqual({row["number"] for row in again["made"]}, {"1001", "1002"})
        self.assertTrue(all(row["replaced"] for row in again["made"]))
        self.assertFalse(SalesInvoice.objects.filter(pk=first.pk).exists())
        self.assertEqual(StoreOrder.objects.count(), 2)
        posted = StoreOrder.objects.get(order_number="1002").invoice
        type(posted).objects.filter(pk=posted.pk).update(status="posted")
        third = services.import_orders(SHOPIFY_ORDERS.encode("utf-8"), self.shop, self.owner, replace=True)
        self.assertIn("1002", {row["number"] for row in third["skipped"]})  # a posted one is never replaced

    def test_prices_that_include_vat_are_taken_back_to_before_tax(self):
        from unittest import mock

        text = "order,sku,quantity,price\n77,DR-1,1,570\n"
        with mock.patch("taxes.services.rates_by_item", return_value={str(self.dress.pk): "14.00"}):
            invoice = services.import_orders(text.encode("utf-8"), self.shop, self.owner, with_vat=True)["made"][0]["invoice"]
        self.assertEqual(invoice.lines.get().unit_sale_price, D("500.00"))

    def test_shipping_that_includes_vat_is_taken_back_too(self):
        from unittest import mock

        fee = make_item(item_code="SHIP", item_name="شحن", default_sale_price="0", is_stock_tracked=False)
        text = "order,sku,quantity,price\n78,DR-1,1,570\n"
        orders = services.read_orders(text.encode("utf-8"))[1]
        orders["78"]["shipping"] = "114"
        with mock.patch("onlinestore.services.read_orders", return_value=("template", orders)), \
             mock.patch("taxes.services.rates_by_item", return_value={str(self.dress.pk): "14.00", str(fee.pk): "14.00"}):
            invoice = services.import_orders(b"x", self.shop, self.owner, shipping_item=fee, with_vat=True)["made"][0]["invoice"]
        self.assertEqual(invoice.lines.get(item=fee).unit_sale_price, D("100.00"))

    def test_another_entitys_order_is_never_replaced(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import main_entity

        main_entity()
        branch = Entity.objects.create(code="BR-ST", name_ar="الفرع")
        branch_shop = make_location(location_code="SHOP-BR", is_selling_location=True, entity=branch)
        with working_in(branch):
            services.import_orders(SHOPIFY_ORDERS.encode("utf-8"), branch_shop, self.owner)
        theirs = StoreOrder.objects.get(order_number="1001").invoice_id
        with working_in(main_entity()):
            result = services.import_orders(SHOPIFY_ORDERS.encode("utf-8"), self.shop, self.owner, replace=True)
        self.assertIn("1001", {row["number"] for row in result["skipped"]})
        self.assertEqual(StoreOrder.objects.get(order_number="1001").invoice_id, theirs)

    def test_a_vat_inclusive_discount_comes_off_before_tax(self):
        from unittest import mock

        text = "order,sku,quantity,price,discount\n79,DR-1,1,114,11.40\n"
        with mock.patch("taxes.services.rates_by_item", return_value={str(self.dress.pk): "14.00"}):
            invoice = services.import_orders(text.encode("utf-8"), self.shop, self.owner, with_vat=True)["made"][0]["invoice"]
        line = invoice.lines.get()
        self.assertEqual((line.unit_sale_price, line.line_discount_amount, invoice.discount_amount), (D("100.00"), D("10.00"), D("0")))

    def test_a_vat_inclusive_line_keeps_the_stores_total_whatever_the_quantity(self):
        from unittest import mock

        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile
        from taxes.services import rate_for

        caps.set_capability_enabled(ClientProfile.get_active(), "vat", True, user=self.owner)
        rate = str(rate_for(self.dress).rate)
        self.assertGreater(D(rate), 0)
        text = "order,sku,quantity,price\n81,DR-1,1000,1.00\n82,DR-1,3,1.00\n"
        with mock.patch("taxes.services.rates_by_item", return_value={str(self.dress.pk): rate}):
            made = services.import_orders(text.encode("utf-8"), self.shop, self.owner, with_vat=True)["made"]
        # Codex #273: a rounded unit price times the quantity used to drift (1,003.20 and 3.01); the few piastres over
        # come off as the line's discount, so each invoice comes to what the store charged.
        self.assertEqual([row["invoice"].total_amount for row in made], [D("1000.00"), D("3.00")])

    def test_an_order_taken_by_an_upload_at_the_same_time_counts_as_imported(self):
        from unittest import mock

        self.upload()
        customers = Customer.objects.count()
        # Codex #273: both uploads looked before either saved; the second meets the unique order and goes on with the file.
        with mock.patch.object(StoreOrder.objects, "filter", return_value=StoreOrder.objects.none()):
            result = self.upload()
        self.assertEqual((result["made"], result["again"]), ([], ["1001", "1002"]))
        self.assertEqual(SalesInvoice.objects.filter(invoice_number__startswith="WEB-").count(), 2)  # no draft left behind
        self.assertEqual(Customer.objects.count(), customers)

    def test_an_order_in_another_currency_is_refused(self):
        text = SHOPIFY_ORDERS.replace(",EGP,", ",USD,", 1)
        result = self.upload(text)
        reason = next(row["reason"] for row in result["skipped"] if row["number"] == "1001")
        self.assertIn("USD", reason)
        self.assertFalse(StoreOrder.objects.filter(order_number="1001").exists())

    def test_a_serial_tracked_item_needs_its_serials(self):
        from serials.models import SerialSetting
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        caps.set_capability_enabled(ClientProfile.get_active(), "serials", True)
        SerialSetting.objects.create(item=self.dress, tracked=True)
        result = self.upload()
        self.assertIn("1001", {row["number"] for row in result["skipped"]})
        self.assertFalse(StoreOrder.objects.filter(order_number="1001").exists())

    def test_another_file_is_refused_with_a_reason(self):
        from django.core.exceptions import ValidationError

        with self.assertRaisesMessage(ValidationError, "قالب حسبة"):
            self.upload("a,b\n1,2\n")


class ScreenTests(StoreSetup):
    def test_the_board_downloads_and_uploads(self):
        self.client.force_login(self.owner)
        url = reverse("onlinestore:board")
        for lang in ("ar", "en"):
            page = self.client.get(url, {"lang": lang})
            self.assertContains(page, "data-store-import")
            self.assertContains(page, 'data-export="woocommerce"')
        download = self.client.get(url, {"export": "shopify"})
        self.assertEqual(download["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("DR-1", download.content.decode())
        self.assertTrue(self.client.get(url, {"template": "1"}).content.decode().startswith("﻿order,"))
        page = self.client.post(url, {"lang": "ar", "location": self.shop.pk, "file": SimpleUploadedFile("orders.csv", SHOPIFY_ORDERS.encode("utf-8"))})
        self.assertContains(page, "data-store-made")
        self.assertContains(page, "data-store-skipped")
        self.assertContains(page, "NOPE-9")
        self.assertContains(self.client.get(url, {"lang": "ar"}), StoreOrder.objects.get(order_number="1001").invoice.invoice_number)

    def test_the_imported_orders_show_only_to_who_sees_sales(self):
        services.import_orders(SHOPIFY_ORDERS.encode("utf-8"), self.shop, self.owner)
        keeper = person(RoleCode.STOCK_KEEPER, "st_keeper")
        self.client.force_login(keeper)
        page = self.client.get(reverse("onlinestore:board"), {"lang": "ar"})
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-store-recent")
        self.assertNotContains(page, StoreOrder.objects.get(order_number="1001").invoice.invoice_number)
        self.assertContains(page, 'data-export="shopify"')  # the catalogue stays theirs

    def test_the_menu_shows_it_only_when_switched_on(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("onlinestore:board")).status_code, 200)
        caps.set_capability_enabled(ClientProfile.get_active(), "online_store", False)
        self.assertNotEqual(self.client.get(reverse("onlinestore:board")).status_code, 200)
