from datetime import date

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .models import ClosingRun, Period, PeriodStatus, PeriodSummary


class ClosingUiTests(TestCase):
    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def period(self):
        return Period.objects.create(period_code="2026-01", name="January", start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))

    def test_manager_without_closing_permission_is_denied(self):
        self.login_as(RoleCode.MANAGER, "closing_manager")
        self.assertEqual(self.client.get(reverse("closing:list")).status_code, 403)

    def test_owner_closes_period_through_existing_service(self):
        user = self.login_as(RoleCode.OWNER, "closing_owner")
        period = self.period()
        response = self.client.post(reverse("closing:close", args=[period.pk]), {"reason": "Month complete", "lang": "en"})
        self.assertEqual(response.status_code, 302)
        period.refresh_from_db()
        self.assertEqual(period.status, PeriodStatus.CLOSED)
        self.assertEqual(period.closed_by, user)
        self.assertEqual(ClosingRun.objects.count(), 1)
        self.assertEqual(PeriodSummary.objects.filter(closing_run__run_number=1).count(), 7)

    def test_reopen_requires_reason_and_preserves_closed_state_on_error(self):
        self.login_as(RoleCode.OWNER, "closing_reason")
        period = self.period()
        self.client.post(reverse("closing:close", args=[period.pk]))
        self.client.post(reverse("closing:reopen", args=[period.pk]), {"reason": ""})
        period.refresh_from_db()
        self.assertEqual(period.status, PeriodStatus.CLOSED)

    def test_reclose_keeps_run_history_and_separate_summary_sets(self):
        self.login_as(RoleCode.OWNER, "closing_reclose")
        period = self.period()
        self.client.post(reverse("closing:close", args=[period.pk]))
        self.client.post(reverse("closing:reopen", args=[period.pk]), {"reason": "Correction"})
        self.client.post(reverse("closing:close", args=[period.pk]), {"reason": "Reclosed"})
        period.refresh_from_db()
        self.assertEqual(period.status, PeriodStatus.CLOSED)
        self.assertEqual(list(period.closing_runs.order_by("run_number").values_list("run_number", flat=True)), [1, 2])
        self.assertEqual(PeriodSummary.objects.filter(period=period).count(), 14)
        response = self.client.get(reverse("closing:detail", args=[period.pk]), {"lang": "en"})
        self.assertContains(response, "Run #1")
        self.assertContains(response, "Run #2")

    def test_repeated_close_is_rejected_without_extra_run(self):
        self.login_as(RoleCode.OWNER, "closing_repeat")
        period = self.period()
        self.client.post(reverse("closing:close", args=[period.pk]))
        self.client.post(reverse("closing:close", args=[period.pk]))
        self.assertEqual(ClosingRun.objects.count(), 1)

