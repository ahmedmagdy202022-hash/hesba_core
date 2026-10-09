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
        self.assertEqual(first["progress"], 20)  # one posted bill of 20% of the contract

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
