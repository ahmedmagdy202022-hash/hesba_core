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
