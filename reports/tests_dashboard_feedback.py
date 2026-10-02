"""DEMO-FEEDBACK: the dashboard Ahmed reviewed live — rotating insights, a working
bell, a language switch in the header, and labels that say what they do."""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_item, make_location, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode

from .analytics import build_analytics, daily_profit, payment_split, weekday_pattern
from .dashboard_views import _alert_path
from .selectors import profit_totals
from .tests_analytics import EVERYTHING, TODAY, person
from .tests_dashboard import prepared_client, sell


class FeedbackSetup(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        Period.objects.create(period_code="FB", name="fb", start_date=TODAY - timedelta(days=120), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "fb_owner")
        self.location, self.cashbox, self.item = make_location(), make_cashbox(cashbox_code="FB-CASH"), make_item(item_code="FB-1")
        stock_in(self.item, self.location, 100, "6.00", movement_date=TODAY - timedelta(days=60))
        recalculate_item_average_cost(self.item)
        # 5 x 10 today (30 paid), 2 x 10 three days ago (all paid).
        sell(self.item, self.location, self.cashbox, 5, "10.00", "30.00", self.owner, number="FB-S1", when=TODAY)
        sell(self.item, self.location, self.cashbox, 2, "10.00", "20.00", self.owner, number="FB-S2", when=TODAY - timedelta(days=3))


class NewFigureTests(FeedbackSetup):
    def test_daily_profit_adds_up_to_the_profit_report(self):
        start = TODAY - timedelta(days=6)
        days = daily_profit(start, TODAY)
        self.assertEqual(len(days), 7)
        self.assertEqual(sum(value for _, value in days), profit_totals(start, TODAY)["profit"])
        self.assertEqual(dict(days)[TODAY], D("20.00"))  # 5 x (10 - 6)

    def test_paid_and_credit_split(self):
        self.assertEqual(payment_split(TODAY - timedelta(days=6), TODAY), {"paid": D("50.00"), "credit": D("20.00")})

    def test_weekday_pattern_is_an_average_over_four_weeks(self):
        rows = dict(weekday_pattern(TODAY))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[TODAY.weekday()], D("12.50"))  # 50 / 4 weeks

    def test_insights_payload(self):
        result = build_analytics(EVERYTHING, "7d", TODAY)
        self.assertFalse(result["profit_daily"]["empty"])
        self.assertEqual((result["split"]["paid_pct"], result["split"]["credit_pct"]), (71, 29))
        self.assertTrue(result["spark"]["net_sales"])
        self.assertTrue(result["daily"]["prev_dots"] == [] or result["daily"]["prev_dots"][0]["x"])
        best = [row for row in result["weekdays"]["rows"] if row["best"]]
        self.assertEqual(best[0]["weekday"], TODAY.weekday())

    def test_no_profit_tab_without_the_profit_permission(self):
        manager = frozenset({"reports.view_sales_report", "reports.view_all_sales_report"})
        self.assertNotIn("profit_daily", build_analytics(manager, "7d", TODAY))


class ScreenTests(FeedbackSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_header_has_the_language_switch_and_a_bell_that_opens(self):
        body = self.client.get(reverse("dashboard_snapshot")).content.decode()
        header = body[body.index('<header class="dash-header">'): body.index("</header>", body.index('<header class="dash-header">'))]
        self.assertIn("data-lang-toggle", header)
        self.assertIn(">English<", header)
        self.assertIn('aria-controls="dash-bell-panel"', header)
        self.assertIn("data-bell-panel", header)
        self.assertIn("hesba/js/dashboard.js", body)
        english = self.client.get(reverse("dashboard_snapshot"), {"lang": "en"}).content.decode()
        self.assertIn(">العربية<", english)

    def test_insights_tabs_and_side_charts(self):
        body = self.client.get(reverse("dashboard_snapshot"), {"period": "7d"}).content.decode()
        for tab in ("sales", "profit", "weekdays", "hours"):
            with self.subTest(tab=tab):
                self.assertIn(f'data-tab="{tab}"', body)
                self.assertIn(f'data-panel="{tab}"', body)
        self.assertIn("dash-donut__paid", body)
        self.assertIn("data-rotate-toggle", body)
        # Every SVG number stays plain under the Arabic locale.
        for value in re.findall(r'\b(?:cx|cy|r|stroke-dasharray|stroke-dashoffset)="([^"]*)"', body):
            with self.subTest(value=value):
                self.assertRegex(value, r"^-?\d+(\.\d+)?( -?\d+(\.\d+)?)?$")

    def test_period_switch_sits_in_the_analytics_section(self):
        body = self.client.get(reverse("dashboard_snapshot")).content.decode()
        hero = body[body.index('<section class="dash-hero"'): body.index("</section>", body.index('<section class="dash-hero"'))]
        self.assertNotIn("dash-periods", hero)
        self.assertIn("dash-hero__art", hero)
        self.assertIn("data-hero-stat", hero)
        self.assertIn('class="dash-section__head"', body)

    def test_closing_says_which_period(self):
        response = self.client.get(reverse("dashboard_snapshot"))
        self.assertContains(response, "إقفال الشهر المحاسبي")
        self.assertContains(response, "dash-action__hint")
        self.assertNotContains(response, ">إقفال الفترة<")


class AlertLinkTests(TestCase):
    def test_each_alert_leads_somewhere(self):
        self.assertEqual(_alert_path("out_of_stock"), "/reports/inventory/")
        self.assertEqual(_alert_path("customer_over_limit_7"), "/parties/customer/7/")
        self.assertEqual(_alert_path("cashbox_low_3"), "/cashboxes/3/")
        self.assertEqual(_alert_path("instalments_overdue"), "/instalments/")
        self.assertEqual(_alert_path("batches_expiring"), "/batches/")
        self.assertEqual(_alert_path("something_new"), "")
