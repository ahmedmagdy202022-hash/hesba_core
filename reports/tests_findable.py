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

    def test_accounting_opens_on_profit_and_loss(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dashboard_snapshot") + "?lang=ar")
        self.assertContains(page, reverse("ledger:income_statement"))
        statement = self.client.get(reverse("ledger:income_statement") + "?lang=ar")
        self.assertEqual(statement.status_code, 200)
        self.assertContains(statement, "الأرباح والخسائر")
