"""AN1: every list and report shows a page at a time, and the reader picks how many rows (10/20/50/100)."""

import re

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_customer, make_draft_sales_invoice, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


def rows(html, marker):
    return len(re.findall(marker, html))


class PagerTests(TestCase):
    def setUp(self):
        prepared_client()
        owner = make_user(username="pg_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        location = make_location()
        self.customer = make_customer()
        for n in range(25):
            make_draft_sales_invoice(customer=self.customer, location=location, invoice_number=f"SI-PG-{n:03d}")

    def page(self, name, **params):
        return self.client.get(reverse(name), {"lang": "ar", **params}).content.decode()

    def test_the_sales_list_reaches_every_invoice_a_page_at_a_time(self):
        first = self.page("sales:list")
        self.assertEqual(rows(first, r">SI-PG-\d{3}<"), 20)  # 20 by default
        self.assertIn("data-pager", first)
        self.assertIn("1–20 من 25 سطر", first)
        self.assertIn("page=2", first)  # there was no way to the 21st invoice before
        second = self.page("sales:list", page=2)
        self.assertEqual(rows(second, r">SI-PG-\d{3}<"), 5)

    def test_the_reader_picks_the_size_and_it_is_remembered(self):
        ten = self.page("sales:list", per=10)
        self.assertEqual(rows(ten, r">SI-PG-\d{3}<"), 10)
        self.assertIn("1 / 3", ten)
        self.assertIn('<b aria-current="true">10</b>', ten)
        self.assertEqual(rows(self.page("sales:list"), r">SI-PG-\d{3}<"), 10)  # the next visit keeps 10
        self.assertEqual(rows(self.page("sales:list", per=7), r">SI-PG-\d{3}<"), 10)  # a size not offered is ignored

    def test_the_links_keep_the_screen_filters(self):
        html = self.page("sales:list", q="SI-PG", status="draft", per=10)
        nxt = re.search(r'href="([^"]*page=2[^"]*)"', html).group(1).replace("&amp;", "&")
        for part in ("q=SI-PG", "status=draft", "lang=ar", "page=2"):
            self.assertIn(part, nxt)

    def test_a_report_pages_its_rows_and_still_totals_all_of_them(self):
        for n in range(14):
            make_customer(customer_code=f"C-PG-{n:02d}", name=f"عميل صفحة {n:02d}")
        report = self.page("reports:customers", per=10)
        self.assertEqual(rows(report, r"data-party-link>"), 10)  # the filter list above still names everyone
        self.assertIn("1–10 من 15 سطر", report)
