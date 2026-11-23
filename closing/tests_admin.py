from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from closing.admin import PeriodSummaryAdmin
from closing.models import PeriodSummary
from closing.services import complete_period_closing, reopen_period
from closing.tests_services import SUMMARY_CODES, make_period


class PeriodSummaryAdminTests(TestCase):
    """The summary list has to stay readable once a period has been reopened.

    Every closing run writes its own set of summaries, so a re-closed period
    shows one set per run. The run column is what keeps those sets apart.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user = get_user_model().objects.create_superuser(
            username="period_summary_admin_tester",
            email="",
            password="admin-tests-only",
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def changelist(self):
        return self.client.get(reverse("admin:closing_periodsummary_changelist"))

    def displayed_runs(self, response):
        """The run column as the admin actually renders it, per row."""
        model_admin = PeriodSummaryAdmin(PeriodSummary, admin.site)
        return [model_admin.run_number(row) for row in response.context["cl"].result_list]

    def test_a_single_run_is_labelled_with_its_run_number(self):
        period = make_period()
        complete_period_closing(period.pk)

        response = self.changelist()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(self.displayed_runs(response)), {1})

    def test_both_runs_of_a_reclosed_period_are_distinguishable(self):
        period = make_period()
        complete_period_closing(period.pk)
        reopen_period(period.pk, reason="late invoice")
        complete_period_closing(period.pk)

        response = self.changelist()
        runs = self.displayed_runs(response)

        self.assertEqual(len(runs), len(SUMMARY_CODES) * 2)
        self.assertEqual(sorted(set(runs)), [1, 2])
        self.assertEqual(runs.count(1), len(SUMMARY_CODES))
        self.assertEqual(runs.count(2), len(SUMMARY_CODES))

    def test_the_list_can_be_sorted_by_the_run_column(self):
        period = make_period()
        complete_period_closing(period.pk)
        reopen_period(period.pk, reason="late invoice")
        complete_period_closing(period.pk)
        # 1-based index into list_display, so reordering the columns cannot
        # silently point this at a different one.
        column = PeriodSummaryAdmin.list_display.index("run_number") + 1

        response = self.client.get(
            reverse("admin:closing_periodsummary_changelist"), {"o": f"-{column}"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.displayed_runs(response)[0], 2)

    def test_showing_a_second_run_costs_no_extra_queries_per_row(self):
        """The run column must not turn the list into one query per row.

        Nothing here configures that: the changelist joins the foreign keys
        because `period` is a real related field in list_display, which makes
        Django select_related() the lot. That is easy to lose by accident, so
        comparing two row counts locks it in without hardcoding a number."""
        period = make_period()
        complete_period_closing(period.pk)
        with CaptureQueriesContext(connection) as one_run:
            self.changelist()

        reopen_period(period.pk, reason="late invoice")
        complete_period_closing(period.pk)
        with CaptureQueriesContext(connection) as two_runs:
            self.changelist()

        self.assertEqual(len(two_runs), len(one_run))
