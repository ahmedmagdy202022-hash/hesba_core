"""SETTINGS-DESIGN: every settings screen shares one frame that says what each section holds today.

Ahmed, 20 Oct: "شاشات الإعدادات تقليدية جدا كشكل ومفهاش روح".
"""

from django.test import TestCase
from django.urls import reverse

from permissions.models import RoleCode
from reports.tests_crawl_matrix import person
from reports.tests_dashboard import prepared_client

from . import capabilities as caps
from . import settings_shell
from .setup_services import set_module_enabled


class FrameTests(TestCase):
    def setUp(self):
        self.profile = prepared_client("commercial", "retail")
        self.owner = person(RoleCode.OWNER, "design_owner")
        self.client.force_login(self.owner)

    def test_every_settings_screen_sits_in_the_frame_with_its_own_link_marked(self):
        for name, key in (("settings_core:currency", "currency"), ("settings_core:modules", "modules"),
                          ("settings_core:capabilities", "capabilities"), ("settings_core:storage", "storage"),
                          ("settings_core:roles", "roles"), ("settings_core:users", "users"),
                          ("settings_core:backup_key", "backups"), ("settings_core:daily_email", "daily_email"),
                          ("settings_core:company", "company")):
            page = self.client.get(reverse(name) + "?lang=ar")
            self.assertEqual(page.status_code, 200, name)
            self.assertContains(page, "data-settings-frame", msg_prefix=name)
            self.assertContains(page, "data-settings-identity", msg_prefix=name)
            self.assertRegex(page.content.decode(), rf'is-active[^"]*" href="[^"]+" data-settings-nav-link="{key}" aria-current="page"')

    def test_the_menu_says_what_each_section_holds(self):
        page = self.client.get(reverse("settings_core:overview") + "?lang=ar").content.decode()
        self.assertIn("مفيش مفتاح لسه", page)   # no backup key yet: a thing to do
        self.assertRegex(page, r'is-todo" href="[^"]+" data-settings-nav-link="backups"')
        self.assertRegex(page, r"\d+ من \d+ شغّال")  # modules on of offered

    def test_tax_sections_show_only_while_their_feature_is_on(self):
        rows = lambda: {row["key"] for group in settings_shell.sections(self.owner, "ar") for row in group["rows"]}
        self.assertNotIn("taxes", rows())
        caps.set_capability_enabled(self.profile, "vat", True)
        self.assertIn("taxes", rows())

    def test_paused_features_are_flagged_in_the_menu(self):
        caps.set_capability_enabled(self.profile, "batches_expiry", True)
        set_module_enabled(self.profile, "inventory", False)
        groups = settings_shell.sections(self.owner, "ar")
        row = next(row for group in groups for row in group["rows"] if row["key"] == "capabilities")
        self.assertEqual((row["status"], row["tone"]), ("1 واقفة", "todo"))

    def test_readiness_counts_what_is_done(self):
        ready = settings_shell.readiness(self.owner, "ar")
        self.assertEqual([row["key"] for row in ready["rows"]], ["company", "logo", "team", "backup", "daily_email"])
        self.assertEqual(ready["done"], sum(row["done"] for row in ready["rows"]))
        page = self.client.get(reverse("settings_core:overview") + "?lang=en")
        self.assertContains(page, "data-settings-ready")
        self.assertContains(page, ">Your data<")


class MenuPermissionTests(TestCase):
    def test_a_cashier_only_sees_what_they_may_open(self):
        prepared_client("commercial", "retail")
        cashier = person(RoleCode.CASHIER, "design_cashier")
        keys = {row["key"] for group in settings_shell.sections(cashier, "ar") for row in group["rows"]}
        self.assertNotIn("users", keys)
        self.assertNotIn("modules", keys)
