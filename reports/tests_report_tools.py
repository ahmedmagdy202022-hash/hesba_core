"""FB-REPORTS: every reading screen, in every activity, offers print / PDF and a download for Excel."""

from django.test import TestCase
from django.urls import resolve, reverse

from permissions.models import RoleCode

from .report_tools import shows_tools
from .tests_crawl_matrix import person
from .tests_dashboard import prepared_client


class ReportToolsTests(TestCase):
    def setUp(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "rt_owner"))

    def test_reading_screens_get_the_toolbar_and_forms_do_not(self):
        reading = ("/reports/sales/", "/reports/profit/", "/reports/aging/", "/reports/insights/", "/sales/", "/purchases/",
                   "/master-data/customers/", "/master-data/items/", "/inventory/", "/inventory/movements/", "/cashboxes/",
                   "/expenses/", "/accounting/trial-balance/", "/taxes/report/", "/restaurant/report/", "/projects/report/",
                   "/medical/report/", "/education/report/", "/staff/")
        for path in reading:
            with self.subTest(path=path):
                self.assertTrue(shows_tools(resolve(path)), path)
        for path in ("/sales/new/", "/purchases/new/", "/sales/pos/", "/dashboard/", "/settings/users/new/", "/master-data/customers/new/"):
            with self.subTest(path=path):
                self.assertFalse(shows_tools(resolve(path)), path)

    def test_the_page_carries_the_toolbar_its_script_and_print_styles(self):
        page = self.client.get(reverse("reports:sales"), {"lang": "ar"})
        self.assertContains(page, "data-report-tools")
        self.assertContains(page, "data-report-excel")
        self.assertContains(page, "اطبع / PDF")
        self.assertContains(page, "hesba/js/report_tools.js")
        self.assertContains(page, "hesba/css/report_tools.css")
        english = self.client.get(reverse("master_data:customers"), {"lang": "en"})
        self.assertContains(english, "Download for Excel")  # master data has its own shell, and gets it too
        form = self.client.get(reverse("sales:create"), {"lang": "ar"})
        self.assertNotContains(form, "data-report-tools")
        self.assertNotContains(form, "report_tools.js")
