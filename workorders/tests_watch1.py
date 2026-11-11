"""Watch round 1, MT-04: the technicians' report writes its month in words and its average like the app's other small numbers."""

from django.urls import reverse

from . import tests_technician_report as base


class TechnicianReportDisplayTests(base.ReportSetup):
    def page(self, **params):
        self.client.force_login(self.owner)
        return self.client.get(reverse("workorders:report"), params).content.decode()

    def test_the_average_days_has_no_decimal_comma_and_no_trailing_zero(self):
        self.delivered(days_in_shop=3)
        self.assertEqual(self.row()["avg_days"], 3.0)
        self.assertIn("<td data-tech-avg>3</td>", self.page())
        self.assertNotIn("3,0", self.page())
        self.delivered(days_in_shop=0)  # (3 + 0) / 2
        self.assertIn("<td data-tech-avg>1.5</td>", self.page())

    def test_a_same_day_delivery_is_zero_days_not_a_dash(self):
        self.delivered(days_in_shop=0)
        self.assertIn("<td data-tech-avg>0</td>", self.page())

    def test_with_nothing_delivered_the_table_shows_its_empty_row(self):
        self.assertIn("<tbody>\n<tr><td colspan=\"8\"", self.page())

    def test_the_title_names_the_month_in_both_languages(self):
        self.assertIn("<bdi>نوفمبر 2026</bdi></h1>", self.page(month="2026-11"))
        self.assertIn("<bdi>November 2026</bdi></h1>", self.page(month="2026-11", lang="en"))
        self.assertNotIn("2026-11</bdi>", self.page(month="2026-11"))
        self.assertIn('value="2026-11"', self.page(month="2026-11"))  # the month box keeps its own format
