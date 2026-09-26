"""SETTINGS-002: one company currency, changeable until money is recorded."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from cashboxes.models import Cashbox
from hesba_testing.factories import make_cashbox, make_customer, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.currency import has_financial_records
from settings_core.models import ClientProfile


CURRENCY = reverse("settings_core:currency")


def sign_in(test, role_code=RoleCode.OWNER, username="currency_owner"):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    test.client.force_login(user)
    return user


class CurrencySettingsTests(TestCase):
    def setUp(self):
        prepared_client()

    def test_owner_changes_currency_before_any_money(self):
        user = sign_in(self)
        cashbox = make_cashbox(opening_balance=Decimal("0"))
        self.client.post(CURRENCY, {"currency": "SAR"})
        self.assertEqual(ClientProfile.get_active().default_currency, "SAR")
        cashbox.refresh_from_db()
        self.assertEqual(cashbox.currency, "SAR")
        log = AuditLog.objects.get(action="change_currency")
        self.assertEqual((log.before_data, log.after_data, log.actor), ({"default_currency": "EGP"}, {"default_currency": "SAR"}, user))

    def test_the_new_currency_shows_on_the_dashboard(self):
        sign_in(self)
        self.client.post(CURRENCY, {"currency": "AED"})
        self.assertEqual(self.client.get(reverse("dashboard_snapshot")).context["currency"], "AED")

    def test_currency_locks_once_money_is_recorded(self):
        sign_in(self)
        make_customer(opening_balance=Decimal("50.00"))
        self.assertTrue(has_financial_records())
        page = self.client.get(CURRENCY)
        self.assertContains(page, "data-currency-locked")
        self.assertNotContains(page, 'type="submit">حفظ')
        self.client.post(CURRENCY, {"currency": "USD"})
        self.assertEqual(ClientProfile.get_active().default_currency, "EGP")

    def test_unknown_codes_are_refused(self):
        sign_in(self)
        self.client.post(CURRENCY, {"currency": "XYZ"})
        self.assertEqual(ClientProfile.get_active().default_currency, "EGP")

    def test_manager_views_but_cannot_change(self):
        sign_in(self, RoleCode.MANAGER, "currency_manager")
        self.assertEqual(self.client.get(CURRENCY).status_code, 200)
        self.assertEqual(self.client.post(CURRENCY, {"currency": "SAR"}).status_code, 403)
        self.assertEqual(ClientProfile.get_active().default_currency, "EGP")

    def test_new_cashbox_form_starts_in_company_currency(self):
        sign_in(self)
        self.client.post(CURRENCY, {"currency": "KWD"})
        form = self.client.get(reverse("master_data:create", kwargs={"entity": "cashboxes"})).context["form"]
        self.assertEqual(form.fields["currency"].initial, "KWD")

    def test_english_page(self):
        sign_in(self)
        self.assertContains(self.client.get(CURRENCY, {"lang": "en"}), "Company currency")


class CurrencyWithoutProfileTests(TestCase):
    def test_no_profile_is_explained(self):
        sign_in(self, username="currency_fresh")
        self.assertContains(self.client.get(CURRENCY), "كمّل الإعداد")
        self.assertFalse(Cashbox.objects.exists())
