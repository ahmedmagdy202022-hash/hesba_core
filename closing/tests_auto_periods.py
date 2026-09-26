"""HG-010: accounting periods open themselves, month by month, where it is safe."""

from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxDirection, CashboxOperationType
from cashboxes.services import create_cashbox_operation, get_cashbox_balance
from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from settings_core.models import ClientProfile
from settings_core.setup_services import complete_setup

from .models import Period, PeriodStatus
from .services import complete_period_closing, ensure_period_is_open, provision_period_for


def owner(username="period_owner"):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
    return user


class AutoPeriodTests(TestCase):
    def test_a_fresh_install_can_record_cash_without_any_period(self):
        user = owner()
        cashbox = make_cashbox(cashbox_code="CASH-AUTO")
        make_cashbox_movement(cashbox, CashboxDirection.IN, "100.00")
        today = timezone.localdate()
        self.assertFalse(Period.objects.exists())

        create_cashbox_operation("AUTO-1", today, CashboxOperationType.DIRECT_OUT, D("40.00"), "نثريات", user, source_cashbox=cashbox)

        self.assertEqual(get_cashbox_balance(cashbox), D("60.00"))
        period = Period.objects.get()
        self.assertEqual(period.start_date, today.replace(day=1))
        self.assertEqual(period.status, PeriodStatus.OPEN)
        self.assertTrue(AuditLog.objects.filter(module="closing", action="auto_open_period").exists())

    def test_the_month_is_opened_once(self):
        first = ensure_period_is_open(date(2026, 3, 3))
        second = ensure_period_is_open(date(2026, 3, 30))
        self.assertEqual(first, second)
        self.assertEqual(Period.objects.count(), 1)
        self.assertEqual(first.end_date, date(2026, 3, 31))

    def test_december_ends_on_the_31st(self):
        period = provision_period_for(date(2025, 12, 10))
        self.assertEqual((period.start_date, period.end_date), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_a_new_month_never_overlaps_an_existing_period(self):
        Period.objects.create(period_code="HALF", name="first half", start_date=date(2026, 5, 1), end_date=date(2026, 5, 15))
        Period.objects.create(period_code="NEXT", name="late", start_date=date(2026, 5, 28), end_date=date(2026, 6, 30))
        period = provision_period_for(date(2026, 5, 20))
        self.assertEqual((period.start_date, period.end_date), (date(2026, 5, 16), date(2026, 5, 27)))

    def test_dates_inside_closed_books_are_refused(self):
        june = provision_period_for(date(2026, 6, 10))
        complete_period_closing(june.pk, owner("closer"), "June done")
        with self.assertRaises(ValidationError):
            ensure_period_is_open(date(2026, 6, 20))
        # A gap before the last closed period is shut too.
        with self.assertRaises(ValidationError):
            ensure_period_is_open(date(2026, 4, 2))
        self.assertEqual(Period.objects.count(), 1)

    def test_future_months_are_not_opened(self):
        with self.assertRaises(ValidationError):
            ensure_period_is_open(timezone.localdate() + timedelta(days=62))
        self.assertFalse(Period.objects.exists())

    def test_completing_setup_opens_the_current_month(self):
        profile = ClientProfile.objects.create(client_code="P", legal_name="P", display_name="P")
        complete_setup(profile, "commercial", "retail", "customers")
        self.assertIsNotNone(Period.objects.filter(start_date__lte=timezone.localdate(), end_date__gte=timezone.localdate()).first())


class OpenMonthScreenTests(TestCase):
    def setUp(self):
        self.client.force_login(owner("screen_period_owner"))

    def test_owner_opens_a_month_from_the_periods_screen(self):
        page = self.client.get(reverse("closing:list"))
        self.assertContains(page, 'type="month"')
        self.assertContains(page, "بتفتح فترة الشهر لوحدها")
        self.client.post(reverse("closing:open_month"), {"month": "2026-02"})
        self.assertTrue(Period.objects.filter(period_code="2026-02", start_date=date(2026, 2, 1), end_date=date(2026, 2, 28)).exists())

    def test_bad_month_is_reported_not_crashed(self):
        response = self.client.post(reverse("closing:open_month"), {"month": "nonsense"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Period.objects.exists())

    def test_cashier_cannot_open_periods(self):
        user = make_user(username="period_cashier")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(user)
        self.assertEqual(self.client.post(reverse("closing:open_month"), {"month": "2026-02"}).status_code, 403)
        self.assertFalse(Period.objects.exists())
