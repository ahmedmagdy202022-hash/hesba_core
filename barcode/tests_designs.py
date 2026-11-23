"""LABEL-003: shop-made label designs and the PDF download data."""

import json

from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_item
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import designs
from .label_templates import choices, spec
from .tests_label_templates import person

GOOD = {"name": "رول 60×40", "kind": "roll", "width": "60", "height": "40", "name_pt": "9", "price_pt": "14", "small_pt": "6",
        "barcode_pct": "45", "show_name": "1", "show_price": "1", "barcode_text": "1"}


class CleanTests(TestCase):
    def test_a_roll_design_ignores_sheet_fields(self):
        design, errors = designs.clean(dict(GOOD, columns="4", top="9"))
        self.assertEqual(errors, [])
        self.assertEqual((design["width"], design["height"], design["columns"], design["top"]), (60.0, 40.0, 1, 0))
        self.assertTrue(design["show_price"])
        self.assertFalse(design["show_code"])

    def test_an_a4_grid_must_fit_the_sheet(self):
        _, errors = designs.clean({"name": "x", "kind": "a4", "width": "70", "height": "37", "columns": "3", "rows": "8", "side": "0", "top": "0.5", "gap_x": "0", "gap_y": "0"})
        self.assertEqual(errors, [])
        _, errors = designs.clean({"name": "x", "kind": "a4", "width": "70", "height": "37", "columns": "3", "rows": "8", "side": "5", "gap_x": "2"}, "en")
        self.assertTrue(any("wider than an A4" in e for e in errors))
        _, errors = designs.clean({"name": "x", "kind": "a4", "width": "50", "height": "40", "columns": "3", "rows": "8", "top": "5", "gap_y": "2"}, "en")
        self.assertTrue(any("taller than an A4" in e for e in errors))

    def test_bad_numbers_and_missing_name(self):
        _, errors = designs.clean({"name": "", "width": "abc", "height": "500"}, "en")
        self.assertEqual(len(errors), 3)


class DesignScreenTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ld_owner")
        self.client.force_login(self.owner)
        self.item = make_item(item_code="LD-1", item_name="قميص", barcode="5901234123457", default_sale_price="199.00")

    def test_create_use_print_and_delete_a_design(self):
        page = self.client.get(reverse("barcode:design_new"))
        self.assertContains(page, "data-designer")
        response = self.client.post(reverse("barcode:design_new"), GOOD)
        design = designs.all_designs()[0]
        self.assertRedirects(response, f"/barcode/labels/?lang=ar&template=d{design['id']}", fetch_redirect_response=False)
        self.assertTrue(AuditLog.objects.filter(action="create_label_design").exists())
        self.assertIn((f"d{design['id']}",), [(key,) for key, _ in choices("ar")])
        labels = self.client.get(reverse("barcode:labels"), {"template": f"d{design['id']}"}).content.decode()
        self.assertIn(f'value="d{design["id"]}"', labels)
        self.assertIn("selected", labels[labels.index(f'value="d{design["id"]}"'):][:300])

        body = self.client.get(reverse("barcode:labels_print"), {f"copies_{self.item.pk}": "3", "template": f"d{design['id']}"}).content.decode()
        self.assertIn("@page roll{size:60.0mm 40.0mm", body)
        self.assertIn(".lb-label .lb-price{font-size:14.0pt", body)
        self.assertIn("data-label-pdf", body)
        self.assertIn("hesba/js/label_pdf.js", body)
        payload = json.loads(body.split('<script id="label-pdf-data" type="application/json">')[1].split("</script>")[0])
        self.assertEqual(len(payload["labels"]), 3)
        self.assertEqual(payload["spec"]["kind"], "roll")
        self.assertEqual(payload["labels"][0]["text"], "5901234123457")
        self.assertEqual(len(payload["labels"][0]["modules"]), 95)  # EAN-13
        self.assertNotIn("cost", json.dumps(payload))

        self.client.post(reverse("barcode:design_delete", args=[design["id"]]))
        self.assertEqual(designs.all_designs(), [])
        self.assertEqual(spec(f"d{design['id']}")["key"], "a4_24")  # a deleted design falls back

    def test_an_invalid_design_is_not_saved(self):
        response = self.client.post(reverse("barcode:design_new"), dict(GOOD, kind="a4", width="120", columns="3"))
        self.assertContains(response, "data-design-errors")
        self.assertEqual(designs.all_designs(), [])

    def test_built_in_print_has_the_pdf_button_too(self):
        body = self.client.get(reverse("barcode:labels_print"), {f"copies_{self.item.pk}": "1", "template": "a4_24"}).content.decode()
        self.assertIn("data-label-pdf", body)
        self.assertIn('"columns": 3', body)

    def test_only_item_managers_design(self):
        self.client.force_login(person(RoleCode.CASHIER, "ld_cashier"))
        self.assertEqual(self.client.post(reverse("barcode:design_new"), GOOD).status_code, 403)
        self.assertEqual(designs.all_designs(), [])


class WarehouseLinkTests(TestCase):
    def test_the_stock_screen_links_to_warehouses(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "wh_owner"))
        page = self.client.get(reverse("inventory:stock"))
        self.assertContains(page, "data-locations-link")
        self.assertContains(page, "data-add-location")
        self.assertContains(page, "+ مخزن جديد")
