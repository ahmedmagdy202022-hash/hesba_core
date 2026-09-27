"""BARCODE-001: symbols, generated codes, label sheets and the scan box."""

import json
import re

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_item, make_seeded_role, make_user, make_user_profile
from master_data.models import Item
from permissions.models import RoleCode

from .services import assign_missing_barcodes, item_catalog
from .symbology import _C128, _code128_values, barcode_svg, code128_modules, ean13_check_digit, ean13_modules, internal_ean13, is_ean13


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def decode_code128(modules):
    """An independent reader: bar widths back to text, checksum verified."""

    widths = [len(run) for run in re.findall(r"1+|0+", modules)]
    patterns = {pattern: value for value, pattern in enumerate(_C128)}
    values = []
    i = 0
    while i < len(widths):
        size = 7 if i + 6 < len(widths) and "".join(map(str, widths[i:i + 7])) == _C128[106] else 6
        values.append(patterns["".join(map(str, widths[i:i + size]))])
        i += size
    assert values[-1] == 106, "missing stop"
    body, check = values[:-2], values[-2]
    assert (body[0] + sum(v * n for n, v in enumerate(body[1:], start=1))) % 103 == check, "bad checksum"
    text, code_set = "", None
    for value in body:
        if value in (104, 100):
            code_set = "B"
        elif value in (105, 99):
            code_set = "C"
        elif code_set == "C":
            text += f"{value:02d}"
        else:
            text += chr(value + 32)
    return text


class SymbologyTests(SimpleTestCase):
    def test_ean13_check_digits_match_published_examples(self):
        self.assertEqual(ean13_check_digit("400638133393"), "1")
        self.assertEqual(ean13_check_digit("590123412345"), "7")
        self.assertTrue(is_ean13("4006381333931"))
        self.assertFalse(is_ean13("4006381333932"))
        self.assertFalse(is_ean13("ABC"))

    def test_ean13_is_95_modules_with_guards(self):
        modules = ean13_modules("5901234123457")
        self.assertEqual(len(modules), 95)
        self.assertTrue(modules.startswith("101"))
        self.assertTrue(modules.endswith("101"))
        self.assertEqual(modules[45:50], "01010")

    def test_code128_checksum_and_round_trip(self):
        self.assertEqual(_code128_values("PJJ123C"), [104, 48, 42, 42, 17, 18, 19, 35, 55, 106])
        for text in ("PJJ123C", "DEMO-ITEM-01", "ITEM-000123456", "12345678", "a b/c", "7"):
            with self.subTest(text=text):
                self.assertEqual(decode_code128(code128_modules(text)), text)

    def test_digit_runs_use_the_compact_set(self):
        self.assertIn(99, _code128_values("ITEM-000123456"))
        self.assertLess(len(code128_modules("ITEM-000123456")), len(code128_modules("ITEM-ABCDEFGHI")))

    def test_internal_codes_are_valid_ean13_in_the_store_range(self):
        code = internal_ean13(42)
        self.assertTrue(is_ean13(code))
        self.assertTrue(code.startswith("20"))

    def test_svg_carries_the_symbology_and_text(self):
        self.assertIn('data-symbology="ean13"', barcode_svg("5901234123457"))
        svg = barcode_svg("DEMO-ITEM-01")
        self.assertIn('data-symbology="code128"', svg)
        self.assertIn(">DEMO-ITEM-01</text>", svg)
        self.assertIn('aria-label="DEMO-ITEM-01"', svg)


class GeneratedBarcodeTests(TestCase):
    def test_items_without_barcode_get_unique_internal_codes(self):
        owner = person(RoleCode.OWNER, "bc_owner")
        kept = make_item(item_code="KEEP", barcode="5901234123457")
        first = make_item(item_code="A1")
        second = make_item(item_code="A2")
        inactive = make_item(item_code="OFF", active=False)
        # Someone typed the code that A1 would get onto another item.
        make_item(item_code="CLASH", barcode=internal_ean13(first.pk))

        self.assertEqual(assign_missing_barcodes(owner), 2)

        for item in (first, second, inactive, kept):
            item.refresh_from_db()
        self.assertTrue(is_ean13(first.barcode) and is_ean13(second.barcode))
        self.assertNotEqual(first.barcode, internal_ean13(first.pk))
        self.assertEqual(len(set(Item.objects.exclude(barcode="").values_list("barcode", flat=True))), 4)
        self.assertEqual(kept.barcode, "5901234123457")
        self.assertEqual(inactive.barcode, "")
        self.assertTrue(AuditLog.objects.filter(module="barcode", action="assign_missing_barcodes").exists())
        self.assertEqual(assign_missing_barcodes(owner), 0)

    def test_catalog_never_carries_cost(self):
        make_item(item_code="C1", barcode="5901234123457", default_sale_price="15.00", default_purchase_price="9.00", average_cost="8.1234")
        sale = item_catalog(sale_prices=True)
        purchase = item_catalog(sale_prices=False, purchase_prices=True)
        self.assertEqual(sale[0]["price"], "15.00")
        self.assertEqual(purchase[0]["price"], "9.00")
        self.assertNotIn("8.1234", json.dumps(sale + purchase))


class LabelScreenTests(TestCase):
    def setUp(self):
        self.item = make_item(item_code="LBL-1", item_name="قميص قطن", barcode="5901234123457", default_sale_price="240.00")
        self.plain = make_item(item_code="LBL-2", item_name="حزام")

    def test_stock_keeper_prints_labels_with_price_and_code(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "bc_keeper"))
        page = self.client.get(reverse("barcode:labels"))
        self.assertContains(page, "قميص قطن")
        self.assertContains(page, 'name="copies_%d"' % self.item.pk)
        sheet = self.client.get(reverse("barcode:labels_print"), {f"copies_{self.item.pk}": "3", f"copies_{self.plain.pk}": "1", "layout": "a4", "show_price": "1"})
        body = sheet.content.decode()
        self.assertEqual(body.count('class="lb-label"'), 4)
        self.assertEqual(body.count('data-symbology="ean13"'), 3)
        self.assertEqual(body.count('data-symbology="code128"'), 1)  # no barcode: the item code is printed
        self.assertIn("240.00", body)
        self.assertIn('class="lb-body lb-a4"', body)

    def test_price_can_be_left_off_and_roll_layout(self):
        self.client.force_login(person(RoleCode.OWNER, "bc_owner2"))
        sheet = self.client.get(reverse("barcode:labels_print"), [(f"copies_{self.item.pk}", "1"), ("layout", "roll"), ("show_price", "0")])
        self.assertContains(sheet, 'class="lb-body lb-roll"')
        self.assertNotContains(sheet, 'class="lb-price"')

    def test_copies_are_capped_and_empty_selection_goes_back(self):
        self.client.force_login(person(RoleCode.OWNER, "bc_owner3"))
        sheet = self.client.get(reverse("barcode:labels_print"), {f"copies_{self.item.pk}": "9999"})
        self.assertEqual(sheet.content.decode().count('class="lb-label"'), 500)
        back = self.client.get(reverse("barcode:labels_print"), {f"copies_{self.item.pk}": "0"})
        self.assertRedirects(back, "/barcode/labels/?lang=ar", fetch_redirect_response=False)

    def test_permissions(self):
        self.client.force_login(person(RoleCode.CASHIER, "bc_cashier"))
        self.assertEqual(self.client.get(reverse("barcode:labels")).status_code, 403)
        self.assertEqual(self.client.post(reverse("barcode:generate")).status_code, 403)
        self.assertEqual(Item.objects.filter(barcode="").count(), 1)

    def test_owner_generates_from_the_screen_and_items_list_links_here(self):
        self.client.force_login(person(RoleCode.OWNER, "bc_owner4"))
        self.client.post(reverse("barcode:generate"))
        self.plain.refresh_from_db()
        self.assertTrue(is_ean13(self.plain.barcode))
        self.assertContains(self.client.get(reverse("master_data:items")), reverse("barcode:labels"))


class ScanBoxTests(TestCase):
    def setUp(self):
        self.client.force_login(person(RoleCode.OWNER, "scan_owner"))
        make_item(item_code="SC-1", barcode="5901234123457", default_sale_price="15.00", default_purchase_price="9.00")

    def test_sales_form_ships_the_scan_box_and_catalog(self):
        page = self.client.get(reverse("sales:create"))
        self.assertContains(page, "data-scan-input")
        self.assertContains(page, 'data-price-field="unit_sale_price"')
        self.assertContains(page, 'id="hs-item-catalog"')
        self.assertContains(page, "5901234123457")
        self.assertContains(page, "hesba/js/invoice_form.js")
        self.assertContains(page, "data-live-total")

    def test_purchase_form_uses_purchase_prices(self):
        page = self.client.get(reverse("purchases:create"))
        self.assertContains(page, 'data-price-field="unit_purchase_price"')
        catalog = json.loads(re.search(r'<script id="hs-item-catalog" type="application/json">(.*?)</script>', page.content.decode(), re.S).group(1))
        self.assertEqual(catalog[0]["price"], "9.00")
