"""OTHER-001: a business that is not in the list says what it is closest to, and works like that activity."""

from django.test import TestCase
from django.urls import reverse

from entities.current import effective_activity
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.demo_catalogs import active_catalog, catalog_for
from reports.tests_dashboard import prepared_client
from settings_core import setup_catalog as catalog
from settings_core.models import ClientProfile
from settings_core.vocabulary import active_vocabulary, vocabulary


class ClosestActivityTests(TestCase):
    def owner(self):
        user = make_user(username="other_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        return user

    def test_the_wizard_asks_one_question(self):
        self.assertEqual(list(catalog.SUB_ACTIVITY_LABELS[catalog.OTHER]), ["products", "services", "mixed"])
        self.client.force_login(self.owner())
        page = self.client.get(reverse("setup_activity_other"))
        self.assertContains(page, "شغلك أقرب لإيه؟")
        self.assertContains(page, "أقرب لبيع منتجات")

    def test_closer_to_products_works_as_a_shop(self):
        prepared_client("other", "products", ",".join(catalog.default_modules("other")))
        self.assertEqual(effective_activity(), ("commercial", "retail"))
        self.assertEqual(active_vocabulary(), vocabulary("commercial", "retail"))
        self.assertEqual(active_catalog(), catalog_for("commercial", "retail"))
        profile = ClientProfile.get_active()
        self.assertEqual((profile.activity_slug, profile.sub_activity_slug), ("other", "products"))  # what they chose is kept

    def test_closer_to_services_works_as_a_services_business(self):
        prepared_client("other", "services", ",".join(catalog.default_modules("other")))
        self.assertEqual(effective_activity(), ("services", "general"))
        self.assertEqual(active_catalog(), catalog_for("services", "general"))
        self.client.force_login(self.owner())
        self.assertEqual(self.client.get(reverse("dashboard_snapshot")).status_code, 200)

    def test_both_works_as_a_business_that_sells_and_services(self):
        """OTHER-002: products and services together gets stock, purchases, work orders and contracts."""

        from agreements.services import is_contract_business
        from settings_core import activity_fit
        from workorders.services import is_workshop

        self.assertEqual(catalog.closest("other", "mixed"), ("services", "maintenance"))
        prepared_client("other", "mixed", ",".join(catalog.default_modules("services")))
        self.assertEqual(effective_activity(), ("services", "maintenance"))
        self.assertEqual(active_catalog(), catalog_for("services", "maintenance"))
        self.assertTrue(any(item.stock for item in active_catalog().items) and any(not item.stock for item in active_catalog().items))
        self.assertTrue(is_workshop() and is_contract_business())
        for slug in ("inventory", "purchases", "employees_technicians"):
            self.assertEqual(activity_fit.module_state("other", "mixed", slug), catalog.SUGGESTED, slug)
        profile = ClientProfile.get_active()
        self.assertEqual((profile.activity_slug, profile.sub_activity_slug), ("other", "mixed"))  # what they chose is kept
        self.client.force_login(self.owner())
        self.assertEqual(self.client.get(reverse("dashboard_snapshot")).status_code, 200)

    def test_older_choices_keep_the_general_setup(self):
        self.assertEqual(catalog.closest("other", "general"), ("other", "general"))
        self.assertEqual(catalog.sub_activity_label("other", "general"), "نشاط عام")  # an older install still reads its own choice


class BehavesAsEverywhereTests(TestCase):
    """Review of #191: every preset follows the closest activity, not the raw "other"."""

    def test_capabilities_modules_and_chart_follow_the_closest_activity(self):
        from ledger import chart
        from ledger.services import _activity
        from settings_core import capabilities

        self.assertEqual(capabilities.default_selection("other", "services"), capabilities.default_selection("services", "general"))
        self.assertEqual(capabilities.preset_state("other", "products", "pos"), capabilities.preset_state("commercial", "retail", "pos"))
        prepared_client(activity="other", sub_activity="services", modules=",".join(catalog.default_modules("services")))
        self.assertEqual(_activity(), "services")
        self.assertEqual(dict((row[0], row[1]) for row in chart.rows_for(_activity())).get("4101"),
                         dict((row[0], row[1]) for row in chart.rows_for("services")).get("4101"))

    def test_an_older_entity_keeps_its_activity_when_edited(self):
        from entities.views import _activity_choices

        self.assertIn("other:general", dict(_activity_choices("ar", "other:general")))
        self.assertNotIn("other:general", dict(_activity_choices("ar")))
