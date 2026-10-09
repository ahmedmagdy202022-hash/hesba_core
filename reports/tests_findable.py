"""R2: features Ahmed could not find (tax, e-invoice, assets) have a place in
the menu once on, Settings says in plain words which are on and where, and
Accounting opens on what an owner reads first."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from settings_core import capabilities as caps
from settings_core.models import FeatureFlag

from .navigation import nav_items
from .tests_dashboard import prepared_client


def switch(slug, on):
    FeatureFlag.objects.update_or_create(code=caps.flag_code(slug), defaults={"name": slug, "enabled": on})


class FindableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        prepared_client()
        cls.owner = make_user(username="findable_owner")
        make_user_profile(user=cls.owner, role=make_seeded_role(RoleCode.OWNER))

    def keys(self):
        from settings_core.setup_services import enabled_modules

        return [item["key"] for item in nav_items(self.owner, "ar", enabled_modules())]

    def test_switched_on_features_get_their_place_in_the_menu(self):
        for slug in ("vat", "e_invoice", "fixed_assets"):
            switch(slug, False)
        self.assertFalse({"taxes", "einvoice", "assets"} & set(self.keys()))
        for slug in ("vat", "e_invoice", "fixed_assets"):
            switch(slug, True)
        self.assertTrue({"taxes", "einvoice", "assets"} <= set(self.keys()))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dashboard_snapshot") + "?lang=ar")
        for url_name in ("taxes:settings", "einvoice:issuer", "fixed_assets:list"):
            self.assertContains(page, reverse(url_name))

    def test_settings_lists_every_feature_in_plain_words(self):
        switch("fixed_assets", True)
        switch("vat", False)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("settings_core:overview") + "?lang=ar")
        self.assertContains(page, 'data-feature="fixed_assets"')
        self.assertContains(page, "الأصول الثابتة والإهلاك")
        self.assertContains(page, "/assets/?lang=ar")
        self.assertContains(page, reverse("settings_core:capabilities"))
        self.assertNotContains(page, "module.")  # no raw flag codes any more

    def test_accounting_opens_on_the_simple_summary(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dashboard_snapshot") + "?lang=ar")
        self.assertContains(page, f'href="{reverse("ledger:summary")}')
        summary = self.client.get(reverse("ledger:summary") + "?lang=ar")
        self.assertEqual(summary.status_code, 200)
        self.assertContains(summary, reverse("ledger:income_statement"))
        statement = self.client.get(reverse("ledger:income_statement") + "?lang=ar")
        self.assertEqual(statement.status_code, 200)
        self.assertContains(statement, "الأرباح والخسائر")

    def test_every_open_link_lands_on_a_working_screen(self):
        for slug in caps.CAPABILITIES:
            switch(slug, True)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("settings_core:overview") + "?lang=ar")
        from settings_core.operational_views import _features

        rows = _features("ar", self.owner)
        self.assertTrue(all(row["url"] for row in rows))
        for row in rows:
            self.assertContains(page, f'href="{row["url"]}?lang=ar"')
            self.assertEqual(self.client.get(row["url"] + "?lang=ar").status_code, 200, row["slug"])
        self.assertIn(reverse("barcode:labels"), [row["url"] for row in rows])  # not the bare /barcode/ prefix

    def test_no_open_link_the_viewer_cannot_open(self):
        from settings_core.operational_views import _features

        switch("fixed_assets", True)
        support = make_user(username="findable_support")
        make_user_profile(user=support, role=make_seeded_role(RoleCode.SUPPORT))
        rows = {row["slug"]: row for row in _features("ar", support)}
        self.assertTrue(rows["fixed_assets"]["on"])
        self.assertEqual(rows["fixed_assets"]["url"], "")
        self.client.force_login(support)
        page = self.client.get(reverse("settings_core:overview") + "?lang=ar")
        if page.status_code == 200:
            self.assertNotContains(page, 'href="/assets/?lang=ar"')
