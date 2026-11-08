"""The dashboard hero on the 1st of the month.

Month-to-date is a single day on the 1st, so the hero's trend line (needs 2+ days) and its
"today vs average day" ring used to vanish. The hero alone falls back to the last 7 days there,
says so in a caption, and every other figure keeps the selected period."""

from datetime import date, datetime
from decimal import Decimal as D
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_item, make_location, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode

from .analytics import build_analytics
from .tests_analytics import EVERYTHING, by_key, person
from .tests_dashboard import prepared_client, sell

CAIRO = ZoneInfo("Africa/Cairo")
FIRST = datetime(2026, 12, 1, 1, 30, tzinfo=CAIRO)
MID = datetime(2026, 12, 15, 12, 0, tzinfo=CAIRO)
CAPTION_AR, CAPTION_EN = "آخر ٧ أيام", "Last 7 days"


def frozen(moment):
    return mock.patch("django.utils.timezone.now", return_value=moment)


def hero_html(body):
    start = body.index('<section class="dash-hero')
    return body[start: body.index("</section>", start)]


def trend_title(html):
    return html.split("dash-glass__title")[1].split("</p>")[0]


class HeroFallbackTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        Period.objects.create(period_code="HF", name="hf", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        self.owner = person(RoleCode.OWNER, "hf_owner")
        self.location, self.cashbox, self.item = make_location(), make_cashbox(cashbox_code="HF-CASH"), make_item(item_code="HF-1")
        stock_in(self.item, self.location, 100, "6.00", movement_date=date(2026, 10, 1))
        recalculate_item_average_cost(self.item)

    def get(self, params=None):
        # Log in under the frozen clock too: a session made at the real time is already expired on 1 Dec.
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard_snapshot"), params or {})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def sell(self, number, quantity, when):
        sell(self.item, self.location, self.cashbox, quantity, "10.00", "0.00", self.owner, number=number, when=when)

    def seed_around_the_first(self):
        # 30 on the 1st, 20 the day before, 10 three days before that.
        self.sell("HF-1", 3, date(2026, 12, 1))
        self.sell("HF-2", 2, date(2026, 11, 30))
        self.sell("HF-3", 1, date(2026, 11, 28))

    def test_on_the_first_the_hero_has_its_trend_and_ring_for_the_last_7_days(self):
        with frozen(FIRST):
            self.seed_around_the_first()
            result = build_analytics(EVERYTHING, "month", date(2026, 12, 1))
            body = self.get()
        hero = result["hero"]
        self.assertTrue(hero["fallback"])
        self.assertTrue(hero["line"])
        self.assertEqual((hero["today"], hero["average"], hero["pct"]), (D("30.00"), D("5.00"), 600))  # 30 vs (30 / 6 earlier days)
        html = hero_html(body)
        self.assertIn("dash-glass--trend", html)
        self.assertIn('<path class="dash-glass__line" d="M', html)
        self.assertIn("dash-glass--ring", html)
        self.assertIn("600%", html)
        self.assertIn(CAPTION_AR, trend_title(html))
        self.assertNotIn("صافي مبيعات", trend_title(html))  # not labelled as month to date

    def test_the_english_hero_says_last_7_days(self):
        with frozen(FIRST):
            self.seed_around_the_first()
            body = self.get({"lang": "en"})
        self.assertIn(CAPTION_EN, trend_title(hero_html(body)))

    def test_on_a_normal_day_the_hero_keeps_month_to_date_with_no_caption(self):
        with frozen(MID):
            self.sell("HF-4", 4, date(2026, 12, 14))
            self.sell("HF-5", 1, date(2026, 12, 5))
            result = build_analytics(EVERYTHING, "month", date(2026, 12, 15))
            body = self.get()
        self.assertFalse(result["hero"]["fallback"])
        self.assertTrue(result["hero"]["line"])
        html = hero_html(body)
        self.assertIn("dash-glass--trend", html)
        self.assertNotIn(CAPTION_AR, html)
        self.assertNotIn(CAPTION_EN, html)
        self.assertIn("صافي مبيعات", trend_title(html))

    def test_the_other_figures_keep_the_selected_period_on_the_first(self):
        with frozen(FIRST):
            self.seed_around_the_first()
            month = build_analytics(EVERYTHING, "month", date(2026, 12, 1))
            week = build_analytics(EVERYTHING, "7d", date(2026, 12, 1))
            body = self.get()
        # Month to date on the 1st is the 1st only: 30, not the 60 of the hero's 7-day window.
        self.assertEqual((month["start"], month["end"]), (date(2026, 12, 1), date(2026, 12, 1)))
        self.assertEqual(by_key(month)["net_sales"]["value"], D("30.00"))
        self.assertEqual(len(month["daily"]["bars"]), 1)
        self.assertEqual(by_key(week)["net_sales"]["value"], D("60.00"))
        self.assertFalse(week["hero"]["fallback"])  # an explicit 7-day period is not a fallback
        # The headline pill still names the selected period.
        self.assertIn("صافي مبيعات", body.split("data-hero-stat")[1].split("</div>")[0])
