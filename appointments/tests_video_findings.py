"""Services video findings: the invoice link on a booking is the app's link, not the browser's raw blue one (f06)."""

import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


class SummaryCardLinkStyleTests(SimpleTestCase):
    def css(self):
        with open(finders.find("hesba/css/operations.css"), encoding="utf-8") as handle:
            return handle.read()

    def test_a_link_inside_a_summary_card_takes_the_teal_link_style_from_the_tokens(self):
        css = self.css()
        rule = re.search(r"\.op-summary-grid strong a\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("color:var(--hs-teal-deep)", rule.group(1))
        self.assertIn("font-weight:var(--hs-weight-bold)", rule.group(1))
        self.assertIn("text-decoration:none", rule.group(1))
        self.assertNotRegex(rule.group(1), r"#[0-9a-fA-F]{3,8}\b|rgba?\(")

    def test_the_booking_page_puts_both_invoice_links_in_such_a_card(self):
        from django.template.loader import get_template

        source = open(get_template("appointments/detail.html").origin.name, encoding="utf-8").read()
        for marker in ("data-appointment-invoice", "data-deposit-invoice"):
            self.assertRegex(source, r"<strong[^>]*>(?:[^<]|<(?!/strong))*<a [^>]*" + marker, marker)
