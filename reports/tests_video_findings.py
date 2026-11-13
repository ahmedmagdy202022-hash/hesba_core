"""Services video findings: the daily summary says in its heading which day it opened on, and offers today in one tap (f07)."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .tests_watch1 import owner_client


class DailySummaryOpensOnYesterdayTests(TestCase):
    """Opening on yesterday is deliberate (UI-TRACK F22: a day is summarised after it ends, the morning send); #371 (reports/tests_watch1.py)
    fixed only the title's date format, so this is the same screen. The heading must say so."""

    def setUp(self):
        owner_client(self, "vf_daily_owner")
        self.today = timezone.localdate()

    def page(self, **params):
        return self.client.get(reverse("reports:daily"), params).content.decode()

    def test_with_no_date_it_opens_on_yesterday_and_the_heading_says_yesterday(self):
        ar = self.page()
        self.assertIn('<span data-digest-heading>ملخص امبارح</span>', ar)
        self.assertIn(f'href="?lang=ar&date={self.today.isoformat()}" data-digest-today', ar)  # the Today button carries today's date
        self.assertIn("<span data-digest-heading>Yesterday's summary</span>", self.page(lang="en"))

    def test_today_is_one_tap_away_from_yesterday_and_from_any_other_day(self):
        self.assertIn("data-digest-today>النهارده</a>", self.page())
        self.assertIn("data-digest-today>Today</a>", self.page(lang="en"))
        old = (self.today - timedelta(days=5)).isoformat()
        self.assertIn("data-digest-today", self.page(date=old))
        self.assertIn('<span data-digest-heading>ملخص اليوم</span>', self.page(date=old))  # any other day keeps the plain name

    def test_on_today_the_heading_says_today_and_there_is_no_today_button(self):
        body = self.page(date=self.today.isoformat())
        self.assertIn('<span data-digest-heading>ملخص النهارده</span>', body)
        self.assertNotIn("data-digest-today", body)
