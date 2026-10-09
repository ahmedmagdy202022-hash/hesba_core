"""R2-10: the settings home is grouped and speaks plainly."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


class SettingsHomeTests(TestCase):
    def setUp(self):
        prepared_client("commercial", "pharmacy")
        self.owner = make_user(username="set_home_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(self.owner)

    def test_grouped_and_plain(self):
        page = self.client.get(reverse("settings_core:overview") + "?lang=ar")
        for group in ("business", "features", "people", "taxes", "data", "alerts"):
            self.assertContains(page, f'data-group="{group}"')
        self.assertContains(page, "data-company-card")
        self.assertContains(page, "صيدلية")          # the activity in words
        self.assertContains(page, "العربية")
        self.assertContains(page, "يناير")
        self.assertNotContains(page, "Africa/Cairo")
        # An owner who is not a superuser, with no system settings, sees no technical table.
        self.assertNotContains(page, "إعدادات متقدمة")
        english = self.client.get(reverse("settings_core:overview") + "?lang=en")
        self.assertContains(english, "People and permissions")
        self.assertContains(english, "January")

    def test_bad_fiscal_month_is_flagged_not_wrapped(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.filter(pk=ClientProfile.get_active().pk).update(fiscal_year_start_month=13)
        page = self.client.get(reverse("settings_core:overview") + "?lang=en")
        self.assertContains(page, "data-fiscal-bad")
        self.assertContains(page, "month 13")
        self.assertNotContains(page, "starts in</span> January")
        self.assertNotContains(page, "January ·")


class SettingsHomeLinksFollowPermissionsTests(TestCase):
    """A custom role that may view settings sees only the links it can open."""

    def setUp(self):
        from hesba_testing.factories import grant, make_role
        from permissions.models import Permission
        from settings_core.capabilities import _write

        prepared_client("commercial", "pharmacy")
        _write("vat", True)
        _write("e_invoice", True)
        role = make_role(code="SETTINGS-VIEW-ONLY")
        grant(role, Permission.objects.get(code="settings.view_settings"))
        self.viewer = make_user(username="set_home_viewer")
        make_user_profile(user=self.viewer, role=role)

    def test_unopenable_links_hidden(self):
        from django.test import Client

        browser = Client()
        browser.force_login(self.viewer)
        page = browser.get(reverse("settings_core:overview") + "?lang=en")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-taxes-link")
        self.assertEqual(browser.get(reverse("taxes:settings")).status_code, 403)
        # Every link the groups do show opens for this role.
        import re

        groups = page.content.decode().split("data-settings-groups", 1)[1].split("</div>", 1)[0]
        links = re.findall(r'href="([^"?]+)', groups)
        self.assertTrue(links)
        for url in links:
            self.assertNotEqual(browser.get(url).status_code, 403, url)

    def test_owner_still_sees_them(self):
        owner = make_user(username="set_home_owner2")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        page = self.client.get(reverse("settings_core:overview") + "?lang=en")
        self.assertContains(page, "data-taxes-link")
        self.assertContains(page, "data-einvoice-link")
        self.assertContains(page, reverse("settings_core:users"))
