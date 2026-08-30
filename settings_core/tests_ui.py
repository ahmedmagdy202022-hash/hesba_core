from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .models import ClientProfile, SystemSetting


class SettingsAndProfileUiTests(TestCase):
    def login_as(self, role_code, username, **user_kwargs):
        user = make_user(username=username, **user_kwargs)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def test_any_authenticated_user_can_view_own_profile_context(self):
        user = make_user(username="plain_profile")
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:profile"), {"lang": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "plain_profile")
        self.assertContains(response, "No operational permissions")

    def test_cashier_profile_lists_own_role_but_not_cost_permission(self):
        self.login_as(RoleCode.CASHIER, "cashier_profile")
        response = self.client.get(reverse("accounts:profile"), {"lang": "en"})
        self.assertContains(response, "Cashier")
        self.assertContains(response, "sales.create_sales_invoice")
        self.assertNotContains(response, "inventory.view_cost")

    def test_cashier_cannot_view_operational_settings(self):
        self.login_as(RoleCode.CASHIER, "settings_denied")
        self.assertEqual(self.client.get(reverse("settings_core:overview")).status_code, 403)

    def test_owner_sees_modeled_settings_but_sensitive_value_is_hidden(self):
        self.login_as(RoleCode.OWNER, "settings_owner")
        ClientProfile.objects.create(client_code="UI", legal_name="Hesba UI", display_name="Hesba Store")
        SystemSetting.objects.create(key="public.setting", value="visible", active=True)
        SystemSetting.objects.create(key="secret.setting", value="never-render-this", is_sensitive=True, active=True)
        response = self.client.get(reverse("settings_core:overview"), {"lang": "en"})
        self.assertContains(response, "Hesba Store")
        self.assertContains(response, "visible")
        self.assertContains(response, "Sensitive value hidden")
        self.assertNotContains(response, "never-render-this")

    def test_management_links_require_both_hesba_permission_and_staff(self):
        self.login_as(RoleCode.OWNER, "owner_nonstaff")
        response = self.client.get(reverse("settings_core:roles"), {"lang": "en"})
        self.assertFalse(response.context["can_manage"])
        self.assertNotContains(response, "Manage roles")
        self.client.logout()
        self.login_as(RoleCode.OWNER, "owner_staff", is_staff=True)
        response = self.client.get(reverse("settings_core:roles"), {"lang": "en"})
        self.assertTrue(response.context["can_manage"])
        self.assertContains(response, "Manage roles")
        self.assertContains(response, reverse("admin:permissions_role_changelist"))

