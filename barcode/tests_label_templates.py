"""LABEL-002: label sizes, what goes on each label, and printer calibration."""

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from hesba_testing.factories import make_item, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from .label_templates import TEMPLATES, offset, resolve, spec


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class TemplateSpecTests(SimpleTestCase):
    def test_sizes_fit_their_sheet_and_legacy_names_still_work(self):
        for key, (kind, width, height, columns, rows, top, *_labels) in TEMPLATES.items():
            with self.subTest(key=key):
                if kind == "a4":
                    self.assertLessEqual(width * columns, 210)
                    self.assertLessEqual(top + height * rows, 297.5)
                self.assertLess(spec(key)["barcode_width"], width)
        self.assertEqual((resolve("a4"), resolve("roll"), resolve("nonsense")), ("a4_24", "roll_50x25", "a4_24"))
        self.assertEqual((offset("2.25"), offset("-9"), offset("abc")), (2.2, -5, 0.0))


class LabelPrintTests(TestCase):
    def setUp(self):
        prepared_client()
        self.item = make_item(item_code="TS-M-BLK", item_name="T-shirt", size="M", color="Black", barcode="5901234123457", default_sale_price="199.00")
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "label_keeper"))

    def sheet(self, **params):
        return self.client.get(reverse("barcode:labels_print"), dict({f"copies_{self.item.pk}": "2"}, **params))

    def test_the_chosen_size_and_fields_reach_the_print_page(self):
        page = self.sheet(template="roll_38x25", show_code="1", show_shop="1", show_price="0", offset_x="1.5")
        body = page.content.decode()
        self.assertIn('data-label-template="roll_38x25"', body)
        self.assertIn("@page roll{size:38mm 25mm", body)
        self.assertIn("--lb-dx:1.5mm", body)
        self.assertIn("data-label-small", body)
        self.assertEqual(body.count("data-label-code"), 2)
        self.assertEqual(body.count("data-label-shop"), 2)
        self.assertIn("Demo Store", body)
        self.assertNotIn('class="lb-price"', body)
        self.assertIn("T-shirt · M · Black", body)

    def test_a4_40_grid_and_defaults(self):
        body = self.sheet(template="a4_40").content.decode()
        self.assertIn("--lb-cols:4", body)
        self.assertIn("--lb-w:52.5mm", body)
        self.assertIn('class="lb-price"', body)
        self.assertNotIn("data-label-code", body)
        self.assertNotIn("data-label-small", body)
        page = self.client.get(reverse("barcode:labels"))
        self.assertContains(page, 'name="template"')
        self.assertContains(page, "رول 38×25 مم")
