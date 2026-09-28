"""USAGE-002: the storage level comes from the database's real size."""

import unittest
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import LoginFailure
from audit.models import AuditLog
from hesba_testing.factories import DEFAULT_DATE, make_cashbox, make_cashbox_movement, make_seeded_role, make_user, make_user_profile
from cashboxes.models import CashboxDirection
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import storage
from .models import UsageStatusLevel


class LevelTests(TestCase):
    def test_levels_follow_the_share_of_the_limit(self):
        limit = 1000
        for used, level in ((0, "green"), (499, "green"), (500, "yellow"), (749, "yellow"), (750, "orange"), (900, "red"), (1500, "red")):
            with self.subTest(used=used):
                self.assertEqual(storage.level_for(used, limit), level)
        self.assertEqual(storage.worse(UsageStatusLevel.GREEN, UsageStatusLevel.ORANGE, UsageStatusLevel.YELLOW), UsageStatusLevel.ORANGE)

    def test_the_size_is_measured_from_the_database(self):
        used = storage.database_size_bytes()
        self.assertGreater(used, 0)
        with override_settings(DATABASE_SIZE_LIMIT_MB=500):
            info = storage.status()
        self.assertEqual((info["used"] > 0, info["limit_mb"]), (True, 500))
        self.assertEqual(info["percent"], int(used * 100 / (500 * storage.MB)))

    def test_months_left_needs_a_month_of_history_and_reads_the_pace(self):
        today = DEFAULT_DATE + timedelta(days=10)
        self.assertIsNone(storage.months_left(100, 1000, today))  # no business yet
        make_cashbox_movement(make_cashbox(), CashboxDirection.IN, "10.00", movement_date=DEFAULT_DATE)
        self.assertIsNone(storage.months_left(100, 1000, today))  # ten days say nothing about the pace
        # 100 used in ~2 months -> 50 a month -> 900 left lasts ~18 months.
        self.assertEqual(storage.months_left(100, 1000, DEFAULT_DATE + timedelta(days=61)), 18)
        self.assertEqual(storage.months_left(1000, 1000, today), 0)

    @unittest.skipUnless(connection.vendor == "postgresql", "table sizes come from PostgreSQL")
    def test_biggest_tables_on_postgresql(self):
        tables = storage.biggest_tables()
        self.assertTrue(tables and all(size > 0 for _, size in tables))


class CleanupTests(TestCase):
    def test_only_throw_away_rows_are_removed(self):
        Session.objects.create(session_key="old", session_data="x", expire_date=timezone.now() - timedelta(days=1))
        Session.objects.create(session_key="live", session_data="x", expire_date=timezone.now() + timedelta(days=1))
        old = LoginFailure.objects.create(username="u", ip_address="1.1.1.1")
        LoginFailure.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
        LoginFailure.objects.create(username="u", ip_address="1.1.1.1")
        audit_before = AuditLog.objects.count()
        self.assertEqual(storage.clean_temporary(), 2)
        self.assertEqual(list(Session.objects.values_list("session_key", flat=True)), ["live"])
        self.assertEqual(LoginFailure.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), audit_before + 1)  # the audit trail only grows


class ScreenTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = make_user(username="st_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))

    def test_owner_sees_the_gauge_and_can_clean(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("settings_core:storage"))
        self.assertContains(page, 'data-storage-level="green"')
        self.assertContains(page, "data-storage-used")
        self.assertContains(self.client.get(reverse("settings_core:overview")), reverse("settings_core:storage"))
        done = self.client.post(reverse("settings_core:storage"), follow=True)
        self.assertContains(done, "الفواتير والحسابات والسجل ما اتلمسوش")
        tiny = max(1, storage.database_size_bytes() // storage.MB)  # the limit the database already fills
        with override_settings(DATABASE_SIZE_LIMIT_MB=tiny):
            self.assertContains(self.client.get(reverse("settings_core:storage"), {"lang": "en"}), 'data-storage-level="red"')

    def test_the_dashboard_card_turns_red_when_the_database_is_full(self):
        self.client.force_login(self.owner)
        tiny = max(1, storage.database_size_bytes() // storage.MB)
        with override_settings(DATABASE_SIZE_LIMIT_MB=tiny):
            self.assertContains(self.client.get(reverse("dashboard_snapshot")), "يحتاج تصرف")

    def test_cashier_cannot_clean(self):
        cashier = make_user(username="st_cashier")
        make_user_profile(user=cashier, role=make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(cashier)
        self.assertIn(self.client.post(reverse("settings_core:storage")).status_code, (302, 403))
