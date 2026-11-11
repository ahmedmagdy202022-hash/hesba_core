"""Watch round 1 fix CL-05: the clinic report says what its revenue counts."""

from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from hesba_testing.factories import make_cashbox, make_item, make_location
from reports.tests_dashboard import sell

from . import insights
from . import tests as report_tests

SCOPE_AR = "الإيراد هنا بيعدّ فواتير المواعيد المخلّصة وخطوات العلاج بس، مش كل فواتير البيع؛ علشان كده ممكن يبقى أقل من مبيعات لوحة القيادة."
SCOPE_EN = "Revenue here counts only the invoices of completed appointments and treatment steps, not every sales invoice, so it can be lower than the dashboard's sales."


class ClinicReportScopeTests(TestCase):
    setUp = report_tests.ClinicReportTests.setUp  # the report tests' clinic: two doctors, two services, two patients
    billed = report_tests.ClinicReportTests.billed

    def test_a_plain_sale_is_not_clinic_revenue_and_the_page_says_so(self):
        self.billed(self.mona, self.sara, self.checkup, "300")
        shop = make_item(item_code="SHOP-1", item_name="مستلزمات", default_sale_price="1000", is_stock_tracked=False)
        sell(shop, make_location(location_code="SHOP"), make_cashbox(), 1, "1000.00", "1000.00", self.owner, number="SI-WATCH-1")
        self.assertEqual(insights.period_report(self.first, self.today)["totals"]["revenue"], Decimal("300.00"))  # the 1,000 sale is not a visit
        self.client.force_login(self.owner)
        ar = self.client.get(reverse("medical:report")).content.decode()
        self.assertIn(f"<p data-report-scope>{SCOPE_AR}</p>", ar)
        self.assertIn("<strong data-report-revenue>300.00</strong>", ar)
        en = self.client.get(reverse("medical:report"), {"lang": "en"}).content.decode()
        self.assertIn(f"<p data-report-scope>{escape(SCOPE_EN)}</p>", en)

    def test_without_the_revenue_figure_there_is_nothing_to_explain(self):
        self.client.force_login(self.owner)
        with mock.patch("permissions.services.user_has_permission", side_effect=lambda user, code: code != "reports.view_all_sales_report"):
            self.assertNotContains(self.client.get(reverse("medical:report")), "data-report-scope")
