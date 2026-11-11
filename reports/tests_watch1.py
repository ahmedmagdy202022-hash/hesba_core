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

        self.assertIn(".hs-report-title bdi{white-space:nowrap}", read_static("hesba/css/report_tools.css"))


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
