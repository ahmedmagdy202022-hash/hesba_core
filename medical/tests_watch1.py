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
from . import tests_access as report_tests_access

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


class EmptyMineTemplatesTests(report_tests_access.Clinic):
    """CL-07: «قوالبي» opens empty for a new doctor while ready templates sit under «المشتركة»: the empty state points there."""

    def get(self, who, url, **params):
        self.client.force_login(self.users[who])
        return self.client.get(url, params)

    def test_the_empty_mine_tab_points_to_the_shared_tab_in_both_languages(self):
        from .models import VisitTemplate

        ar = self.get("doctor", reverse("medical:visit_templates")).content.decode()
        self.assertTrue(VisitTemplate.objects.filter(owner__isnull=True).exists())  # the starting set was seeded by this visit
        self.assertFalse(VisitTemplate.objects.filter(owner__isnull=False).exists())
        self.assertIn("<b>مفيش قوالب بتاعتك لسه، بس فيه قوالب جاهزة في «المشتركة».</b>", ar)
        self.assertIn('<a href="?lang=ar&amp;scope=shared" data-open-shared>افتح المشتركة</a>', ar)
        en = self.get("doctor", reverse("medical:visit_templates"), lang="en").content.decode()
        self.assertIn("<b>You have no templates of your own yet, but ready templates are waiting under «Shared».</b>", en)
        self.assertIn('<a href="?lang=en&amp;scope=shared" data-open-shared>Open Shared</a>', en)
        shared = self.get("doctor", reverse("medical:visit_templates"), scope="shared").content.decode()
        self.assertNotIn("data-open-shared", shared)  # the shared tab itself lists them

    def test_with_nothing_shared_it_keeps_its_old_plain_sentence(self):
        from .models import VisitTemplate

        self.get("doctor", reverse("medical:visit_templates"))
        VisitTemplate.objects.filter(owner__isnull=True).delete()
        html = self.get("doctor", reverse("medical:visit_templates")).content.decode()
        self.assertIn("<b>مفيش قوالب هنا لسه.</b>", html)
        self.assertNotIn("data-open-shared", html)
