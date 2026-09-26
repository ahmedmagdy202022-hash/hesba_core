"""MOBILE-002: phone-width tables become labelled cards.

The layout itself runs in the browser (verified with the mobile audit, see the
PR). These tests pin the server-side half: every signed-in screen ships the
script and stylesheet, and no template pre-fills a cell label the script
would then refuse to overwrite.
"""

import pathlib
import re

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from hesba_testing.factories import make_customer, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode


class TableCardAssetsTests(SimpleTestCase):
    def test_script_targets_both_table_families(self):
        with open(finders.find("hesba/js/table_cards.js"), encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("table.op-table", script)
        self.assertIn("table.md-table", script)

    def test_card_layout_is_phone_only(self):
        with open(finders.find("hesba/css/table_cards.css"), encoding="utf-8") as handle:
            css = handle.read()
        self.assertIn("@media (max-width: 700px)", css)
        # Every rule sits inside that one media block: tablet and desktop keep the table.
        self.assertEqual(css.count("@media"), 1)
        self.assertTrue(css.rstrip().endswith("}"))

    def test_no_template_prefills_cell_labels(self):
        # A pre-filled data-label blocks the column header the script assigns;
        # master_data/list.html used to stamp the whole column list on every cell.
        root = pathlib.Path(settings.BASE_DIR) / "templates"
        offenders = [str(p.relative_to(root)) for p in root.rglob("*.html") if re.search(r"<td[^>]*data-label=", p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])


class TableCardWiringTests(TestCase):
    def setUp(self):
        user = make_user(username="cards_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)

    def test_list_screens_ship_the_card_script_and_styles(self):
        make_customer()
        for name in ("sales:list", "inventory:stock", "master_data:customers", "reports:profit"):
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, "hesba/js/table_cards.js")
                self.assertContains(response, "hesba/css/table_cards.css")

    def test_master_data_rows_carry_plain_cells(self):
        make_customer()
        response = self.client.get(reverse("master_data:customers"))
        self.assertNotContains(response, "data-label=\"[")
