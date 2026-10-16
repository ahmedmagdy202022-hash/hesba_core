"""FB2-PRICE: a margin % beside the purchase price fills the selling price (in the browser; nothing new is stored)."""

from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

STATIC = Path(settings.BASE_DIR) / "static" / "hesba"


class PriceMarkupTests(TestCase):
    def setUp(self):
        prepared_client()
        owner = make_user(username="pm_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)

    def test_the_helper_is_on_the_item_form_and_inside_the_invoices(self):
        for url in (reverse("master_data:item_create"), reverse("purchases:create"), reverse("sales:create")):
            page = self.client.get(url).content.decode()
            self.assertIn("hesba/js/price_markup.js", page, url)
            self.assertIn('name="default_purchase_price"', page, url)

    def test_the_rounding_rules_are_the_ones_offered(self):
        script = (STATIC / "js" / "price_markup.js").read_text(encoding="utf-8")
        for rule in ("rounding === '1'", "rounding === '5'", "rounding === '10'", "rounding === '99'", "Math.ceil"):
            self.assertIn(rule, script)
        # The helper's own inputs carry no name: they are never sent, so the item keeps only its two prices.
        self.assertNotIn('name="margin', script)

    def test_a_closed_quick_add_box_stays_hidden(self):
        css = (STATIC / "css" / "form_controls.css").read_text(encoding="utf-8")
        self.assertIn(".hs-quick-option[hidden]{display:none}", css)
