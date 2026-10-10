"""R2-8: the insights report — the quick read, the charts and the activity's own section."""

from django.test import TestCase, override_settings
from django.urls import reverse

from settings_core import capabilities as caps
from settings_core.models import FeatureFlag

from . import tests_demo_catalogs


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class InsightsTests(TestCase):
    """Built on the sample business of each activity (R2-5)."""

    fill = tests_demo_catalogs.SampleBusinessPerActivityTests.fill

    def page(self, lang="ar"):
        self.assertTrue(self.client.login(username="owner", password="Demo-pass-1"))
        response = self.client.get(reverse("reports:insights") + f"?lang={lang}")
        self.assertEqual(response.status_code, 200)
        return response

    def test_a_shop_reads_its_month_in_words_and_charts(self):
        self.fill("commercial", "retail")
        page = self.page()
        data = page.context["data"]
        self.assertTrue(data["read"][0].startswith("المبيعات في آخر 30 يوم"))
        self.assertTrue(any("هامش الربح" in line for line in data["read"]))
        for chart in ('data-chart="daily"', 'data-chart="top"', 'data-chart="categories"', 'data-chart="customers"', 'data-chart="weekdays"'):
            self.assertContains(page, chart)
        self.assertFalse(data["daily"]["empty"])
        self.assertEqual(data["categories"][0]["share"], 100)  # one category in the sample shop
        self.assertContains(self.page("en"), "Quick read")

    def test_a_restaurant_sees_its_dishes(self):
        self.fill("restaurants", "restaurant")
        section = self.page().context["data"]["section"]
        self.assertEqual(section["kind"], "dishes")
        self.assertEqual(section["rows"][0]["label"], "مكرونة بشاميل")  # 8 a day, the most ordered

    def test_a_clinic_sees_its_people_and_services(self):
        self.fill("medical", "clinic")
        page = self.page()
        section = page.context["data"]["section"]
        self.assertEqual(section["kind"], "people")
        self.assertIn("كشف", [row["label"] for row in section["services"]])
        self.assertContains(page, 'data-activity-section="people"')

    def test_a_builder_sees_contract_against_billed(self):
        self.fill("contracting", "general")
        section = self.page().context["data"]["section"]
        self.assertEqual(section["kind"], "projects")
        first = next(row for row in section["rows"] if row["project"].name.startswith("عمارة"))
        # CONTRACT-002: the posted certificate is a quarter of the first two bill-of-quantities
        # items, 412,400 of a 2,830,000 contract.
        self.assertEqual(first["progress"], 14)

    def test_a_factory_sees_cost_per_unit(self):
        self.fill("manufacturing", "garments")
        section = self.page().context["data"]["section"]
        self.assertEqual(section["kind"], "production")
        tee = next(row for row in section["rows"] if row["label"] == "تيشيرت قطن")
        self.assertEqual(str(tee["unit_cost"]), "81.00")  # 1.2 m cotton x 65 + 0.2 thread x 15
        self.assertGreater(tee["margin"], 0)

    def test_a_pharmacy_sees_expiry_dates(self):
        FeatureFlag.objects.update_or_create(code=caps.flag_code("batches_expiry"), defaults={"name": "batches", "enabled": True})
        self.fill("commercial", "pharmacy")
        section = self.page().context["data"]["section"]
        self.assertEqual(section["kind"], "expiry")
        self.assertEqual(len(section["soon"]), 2)

    def test_the_report_is_listed_first_and_kept_from_a_cashier(self):
        self.fill("commercial", "retail")
        self.page()
        hub = self.client.get(reverse("report_hub") + "?lang=ar")
        self.assertEqual(hub.context["cards"][0]["url_name"], "reports:insights")
        self.client.logout()
        self.assertTrue(self.client.login(username="cashier", password="Demo-pass-1"))
        self.assertEqual(self.client.get(reverse("reports:insights")).status_code, 403)

    def test_costs_stay_with_the_cost_and_profit_permissions(self):
        self.fill("manufacturing", "garments")
        self.assertTrue(self.client.login(username="manager", password="Demo-pass-1"))
        page = self.client.get(reverse("reports:insights") + "?lang=ar")
        self.assertEqual(page.status_code, 200)  # a manager reads the report...
        self.assertFalse(page.context["data"]["can_cost"])  # ...without production cost
        self.assertEqual(page.context["data"]["section"]["kind"], "production")
        self.assertNotContains(page, "تكلفة الخامات")
        self.assertNotContains(page, "81.00")

    def test_a_returned_sale_leaves_the_breakdowns(self):
        from django.utils import timezone

        from sales.models import SalesInvoice
        from sales.services import create_sales_return

        self.fill("commercial", "retail")
        before = self.page().context["data"]
        invoice = SalesInvoice.objects.filter(status="posted", invoice_number__startswith="DEMO-SI-").order_by("-invoice_date").first()
        line = invoice.lines.first()
        create_sales_return("R2-RET-1", timezone.localdate(), invoice.pk, [{"source_line": line.pk, "quantity": line.quantity}], "رجّعه", self.owner_user())
        after = self.page().context["data"]
        returned = before["now"]["net"] - after["now"]["net"]
        self.assertGreater(returned, 0)
        # The category chart drops by what the headline dropped by (one category, no tax in the sample).
        self.assertEqual(before["categories"][0]["value"] - after["categories"][0]["value"], returned)

    def owner_user(self):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.get(username="owner")
