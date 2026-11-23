"""Watch round 1 fixes on the reports screens (SH-05 title, SH-04, MT-06, CL-05, SH-03 note)."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .tests_dashboard import prepared_client


def owner_client(test, username="watch1_owner"):
    prepared_client()
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
    test.client.force_login(user)
    return user


class DailySummaryTitleTests(TestCase):
    def setUp(self):
        owner_client(self)

    def test_the_title_writes_the_day_the_way_the_app_does_and_never_breaks_it(self):
        ar = self.client.get(reverse("reports:daily"), {"date": "2026-11-10"}).content.decode()
        self.assertIn("<bdi>10 نوفمبر 2026</bdi>", ar)
        self.assertNotIn("2026-11-10", ar.split("</h1>")[0])
        en = self.client.get(reverse("reports:daily"), {"date": "2026-11-10", "lang": "en"}).content.decode()
        self.assertIn("<bdi>10 November 2026</bdi>", en)

    def test_a_title_date_stays_in_one_piece_in_the_stylesheet(self):
        from reports.tests_ux_wave3 import read_static

        css = read_static("hesba/css/report_tools.css")
        self.assertIn(".hs-report-title bdi{white-space:nowrap}", css)
        self.assertIn(".hs-report-title{flex-wrap:wrap}", css)  # an unbreakable date must wrap as a whole, not overflow the phone


class ProfitReportPeriodTests(TestCase):
    def setUp(self):
        owner_client(self)

    def line(self, **params):
        import re

        html = self.client.get(reverse("reports:profit"), params).content.decode()
        return re.search(r"<p data-period-state>(.*?)</p>", html).group(1)

    def test_the_default_view_says_it_is_all_time(self):
        self.assertEqual(self.line(), "الفترة المعروضة: كل الفترة")
        self.assertEqual(self.line(lang="en"), "Showing: all time")

    def test_a_period_is_written_as_dates_in_both_languages(self):
        self.assertEqual(self.line(date_from="2026-11-01", date_to="2026-11-11"),
                         "الفترة المعروضة: من <bdi>1 نوفمبر 2026</bdi> لحد <bdi>11 نوفمبر 2026</bdi>")
        self.assertEqual(self.line(date_from="2026-11-01", date_to="2026-11-11", lang="en"),
                         "Showing: from <bdi>1 November 2026</bdi> to <bdi>11 November 2026</bdi>")

    def test_one_day_one_open_end_and_an_unreadable_date(self):
        self.assertEqual(self.line(date_from="2026-11-11", date_to="2026-11-11", lang="en"), "Showing: <bdi>11 November 2026</bdi>")
        self.assertEqual(self.line(date_from="2026-11-01"), "الفترة المعروضة: من <bdi>1 نوفمبر 2026</bdi> وبعده")
        self.assertEqual(self.line(date_to="2026-11-11", lang="en"), "Showing: up to <bdi>11 November 2026</bdi>")
        self.assertEqual(self.line(date_from="garbage"), "الفترة المعروضة: كل الفترة")  # the report ignores it too

    def test_the_month_chip_shows_the_month_it_filled_in(self):
        from django.utils import timezone

        from reports.analytics import period_bounds

        start, end = period_bounds("month", timezone.localdate())[:2]
        text = self.line(date_from=start.isoformat(), date_to=end.isoformat(), lang="en")
        self.assertIn(f"{start.day} {start.strftime('%B %Y')}", text)


class HugeTrendTests(TestCase):
    """MT-06: «▲ 4900%» is arithmetically right and useless; above 300% the dashboard says it in words."""

    def setUp(self):
        from datetime import timedelta
        from decimal import Decimal

        from closing.models import Period
        from hesba_testing.factories import make_cashbox, make_item, make_location, stock_in
        from inventory.services import recalculate_item_average_cost
        from django.utils import timezone

        self.today = timezone.localdate()
        self.owner = owner_client(self)
        Period.objects.create(period_code="W1", name="w1", start_date=self.today - timedelta(days=90), end_date=self.today + timedelta(days=5))
        self.location, self.cashbox = make_location(), make_cashbox()
        self.item = make_item(item_code="TEA", item_name="Tea", default_sale_price=Decimal("100.00"))
        stock_in(self.item, self.location, 500, "10.00", movement_date=self.today - timedelta(days=80))
        recalculate_item_average_cost(self.item)

    def sell(self, number, quantity, price, days_ago):
        from datetime import timedelta
        from decimal import Decimal

        from .tests_dashboard import sell

        paid = Decimal(quantity) * Decimal(price)
        sell(self.item, self.location, self.cashbox, quantity, price, str(paid), self.owner, number=number, when=self.today - timedelta(days=days_ago))

    def page(self, lang):
        return self.client.get("/dashboard/", {"period": "30d", "lang": lang}).content.decode()

    def test_a_rise_above_three_times_is_told_in_words(self):
        self.sell("SI-W1", 1, "10.00", 31)   # previous 30 days: 10
        self.sell("SI-W2", 5, "100.00", 0)   # this period: 500, a change of 4900%
        ar, en = self.page("ar"), self.page("en")
        self.assertIn("زيادة كبيرة جدًا عن الفترة اللي قبلها", ar)
        self.assertIn("far above the previous period", en)
        self.assertNotIn("4900%", ar)
        self.assertNotIn("4900%", en)

    def test_a_normal_change_keeps_its_number(self):
        self.sell("SI-W1", 4, "100.00", 31)  # previous 30 days: 400
        self.sell("SI-W2", 5, "100.00", 0)   # this period: 500, +25%
        ar = self.page("ar")
        self.assertIn("<bdi>25%</bdi>", ar)
        self.assertNotIn("زيادة كبيرة جدًا", ar)


class HugeTrendRuleTests(TestCase):
    def test_the_bound_is_three_hundred_percent_and_a_fall_has_its_own_words(self):
        from reports.dashboard_views import STRINGS, _shown_delta

        for delta, huge in ((300.0, False), (300.4, False), (300.5, True), (1396.0, True), (-300.0, False), (-650.0, True), (50.0, False)):
            with self.subTest(delta=delta):
                self.assertEqual(_shown_delta({"key": "net_profit", "delta": delta})["huge"], huge)
        self.assertEqual(STRINGS["ar"]["huge_down"], "هبوط حاد عن الفترة اللي قبلها")
        self.assertEqual(STRINGS["en"]["huge_down"], "a sharp fall vs the previous period")
