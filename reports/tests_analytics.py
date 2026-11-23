"""DASH-002: dashboard analytics with stated formulas (replaces the activity score)."""

from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period
from expenses.models import ExpenseCategory
from expenses.services import record_expense
from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode

from .analytics import build_analytics, change, period_bounds, stock_health
from .tests_dashboard import prepared_client, sell


TODAY = timezone.localdate()
EVERYTHING = frozenset({
    "reports.view_sales_report", "reports.view_all_sales_report", "reports.view_profit_report",
    "reports.view_customer_report", "reports.view_supplier_report", "cashboxes.view_finance",
    "cashboxes.view_expenses", "inventory.view_cost",
})


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def by_key(result):
    return {metric["key"]: metric for metric in result["metrics"]}


class PeriodTests(SimpleTestCase):
    def test_month_to_date_compares_with_the_same_number_of_days(self):
        start, end, prev_start, prev_end = period_bounds("month", date(2026, 3, 10))
        self.assertEqual((start, end), (date(2026, 3, 1), date(2026, 3, 10)))
        self.assertEqual((prev_start, prev_end), (date(2026, 2, 19), date(2026, 2, 28)))

    def test_rolling_windows(self):
        self.assertEqual(period_bounds("7d", date(2026, 3, 10))[:2], (date(2026, 3, 4), date(2026, 3, 10)))
        self.assertEqual(period_bounds("today", date(2026, 3, 10))[2:], (date(2026, 3, 9), date(2026, 3, 9)))

    def test_change(self):
        self.assertIsNone(change(D("100"), D("0")))
        self.assertAlmostEqual(change(D("150"), D("100")), 50.0)
        self.assertAlmostEqual(change(D("50"), D("100")), -50.0)


class AnalyticsFigureTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        Period.objects.create(period_code="AN", name="an", start_date=TODAY - timedelta(days=120), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "an_owner")
        self.location = make_location()
        self.cashbox = make_cashbox(cashbox_code="AN-CASH")
        self.item = make_item(item_code="AN-1")
        stock_in(self.item, self.location, 100, "6.00", movement_date=TODAY - timedelta(days=100))
        recalculate_item_average_cost(self.item)
        # This week: 5 x 10 = 50 (30 paid); last week: 2 x 10 = 20.
        sell(self.item, self.location, self.cashbox, 5, "10.00", "30.00", self.owner, number="AN-S1", when=TODAY)
        sell(self.item, self.location, self.cashbox, 2, "10.00", "20.00", self.owner, number="AN-S0", when=TODAY - timedelta(days=8))
        record_expense(category=ExpenseCategory.objects.get(code="utilities"), cashbox=self.cashbox, amount=D("12.00"), expense_date=TODAY, description="كهرباء", user=self.owner)

    def test_every_metric_with_its_formula(self):
        result = build_analytics(EVERYTHING, "7d", TODAY)
        metrics = by_key(result)
        self.assertEqual(metrics["net_sales"]["value"], D("50.00"))
        self.assertEqual(metrics["net_sales"]["previous"], D("20.00"))
        self.assertAlmostEqual(metrics["net_sales"]["delta"], 150.0)
        self.assertTrue(metrics["net_sales"]["good"])
        self.assertEqual((metrics["avg_invoice"]["value"], metrics["avg_invoice"]["count"]), (D("50.00"), 1))
        self.assertEqual(metrics["gross_profit"]["value"], D("20.00"))  # 5 x (10 - 6)
        self.assertAlmostEqual(metrics["gross_profit"]["margin"], 40.0)
        self.assertEqual(metrics["expenses"]["value"], D("12.00"))
        self.assertFalse(metrics["expenses"]["good"] is True)
        self.assertEqual(metrics["net_profit"]["value"], D("8.00"))
        self.assertEqual(metrics["cash"]["value"], D("38.00"))  # 30 + 20 received - 12 paid out
        self.assertEqual(metrics["receivables"]["value"], D("20.00"))  # 50 - 30 unpaid
        for metric in result["metrics"]:
            with self.subTest(metric=metric["key"]):
                self.assertTrue(metric["how"])

    def test_daily_chart_and_top_items(self):
        result = build_analytics(EVERYTHING, "7d", TODAY)
        self.assertEqual(len(result["daily"]["bars"]), 7)
        self.assertEqual(result["daily"]["best"]["value"], D("50.00"))
        self.assertTrue(result["daily"]["previous_points"])
        self.assertEqual(result["top"][0]["code"], "AN-1")
        self.assertTrue(result["top_by_profit"])
        self.assertEqual(sum(cell["count"] for cell in result["hours"]["cells"]), 2)

    def test_what_a_manager_and_a_cashier_get(self):
        manager = frozenset({"reports.view_sales_report", "reports.view_all_sales_report", "reports.view_customer_report"})
        keys = set(by_key(build_analytics(manager, "month", TODAY)))
        self.assertEqual(keys, {"net_sales", "avg_invoice", "receivables"})
        self.assertFalse(build_analytics(frozenset({"reports.view_sales_report"}), "month", TODAY)["available"])
        self.assertNotIn("stock", build_analytics(manager, "month", TODAY))

    def test_stock_health(self):
        idle = make_item(item_code="IDLE", item_name="Idle", average_cost="5.0000")
        stock_in(idle, self.location, 8, "5.00", movement_date=TODAY - timedelta(days=90))
        recalculate_item_average_cost(idle)
        fast = make_item(item_code="FAST", item_name="Fast")
        stock_in(fast, self.location, 35, "1.00", movement_date=TODAY - timedelta(days=40))
        recalculate_item_average_cost(fast)
        sell(fast, self.location, self.cashbox, 30, "2.00", "60.00", self.owner, number="AN-F1", when=TODAY - timedelta(days=3))
        health = stock_health(TODAY)
        slow = {row["code"]: row for row in health["slow"]}
        self.assertEqual(slow["IDLE"]["value"], D("40.00"))
        self.assertIsNone(slow["IDLE"]["idle_days"])
        running = {row["code"]: row for row in health["running_out"]}
        self.assertEqual(running["FAST"]["cover_days"], 5.0)  # 5 left at 1 a day


class DashboardPageTests(TestCase):
    def test_owner_sees_analytics_not_the_old_score(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "page_owner"))
        page = self.client.get(reverse("dashboard_snapshot"), {"period": "30d"})
        self.assertContains(page, "أداء النشاط")
        self.assertContains(page, "اتحسب إزاي؟")
        self.assertContains(page, 'data-period="30d"')
        self.assertNotContains(page, "مؤشر النشاط")
        self.assertNotContains(page, "dash-health__ring")
        self.assertEqual(self.client.get(reverse("dashboard_snapshot"), {"period": "bogus"}).context["analytics"]["period"], "month")

    def test_cashier_gets_no_analytics_section(self):
        prepared_client()
        self.client.force_login(person(RoleCode.CASHIER, "page_cashier"))
        page = self.client.get(reverse("dashboard_snapshot"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "dash-analytics")


class ChartMarkupTests(TestCase):
    def test_svg_coordinates_never_use_a_decimal_comma(self):
        # Under LANGUAGE_CODE="ar" a float renders as "44,3", which SVG reads as
        # two numbers and scatters every glyph of the axis labels.
        import re

        prepared_client()
        owner = person(RoleCode.OWNER, "svg_owner")
        location, cashbox, item = make_location(), make_cashbox(cashbox_code="SVG"), make_item(item_code="SVG-1")
        Period.objects.create(period_code="SVG", name="svg", start_date=TODAY - timedelta(days=60), end_date=TODAY + timedelta(days=2))
        stock_in(item, location, 50, "3.00", movement_date=TODAY - timedelta(days=40))
        recalculate_item_average_cost(item)
        sell(item, location, cashbox, 3, "1234.50", "0.00", owner, number="SVG-S1", when=TODAY - timedelta(days=2))
        self.client.force_login(owner)
        body = self.client.get(reverse("dashboard_snapshot"), {"period": "30d"}).content.decode()
        svg = body[body.index('<svg class="dash-bars"'): body.index("</svg>", body.index('<svg class="dash-bars"'))]
        for attribute, value in re.findall(r'\b(x|y|x1|x2|y1|y2|width|height)="([^"]*)"', svg):
            with self.subTest(attribute=attribute, value=value):
                self.assertRegex(value, r"^-?\d+(\.\d+)?$")
        for value in re.findall(r'\b(?:d|points)="([^"]*)"', svg):
            with self.subTest(path=value[:40]):
                # A pair is "x.x,y.y"; a localized float would give "44,3,192,0".
                self.assertIsNone(re.search(r"(^|[\sMVHQ])\d+,\d+,", value))
        self.assertRegex(svg, r">\d+(\.\d)?K<")
