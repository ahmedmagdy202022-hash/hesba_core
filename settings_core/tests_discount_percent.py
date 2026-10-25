"""FB-25: every discount takes a percent or an amount; only the amount is sent, so posting is as it was."""

from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import SimpleTestCase


class DiscountPercentTests(SimpleTestCase):
    def test_every_signed_in_page_offers_it(self):
        self.assertIn("hesba/js/discount_percent.js", get_template("base_app.html").template.source)

    def test_it_covers_each_discount_and_never_sends_the_percent(self):
        with open(finders.find("hesba/js/discount_percent.js"), encoding="utf-8") as handle:
            js = handle.read()
        for needed in ('line_discount_amount', 'name="discount_amount"', "data-pos-discount", "data-pos-subtotal",  # the line, the invoice, the till
                       "'pct-detached'",  # the percent box belongs to no form
                       "unit_sale_price", "unit_purchase_price", "MutationObserver", "Math.min(Math.max(pct, 0), 100)"):
            with self.subTest(needed=needed):
                self.assertIn(needed, js)
        with open(finders.find("hesba/css/form_controls.css"), encoding="utf-8") as handle:
            self.assertIn(".hs-pct-toggle", handle.read())
