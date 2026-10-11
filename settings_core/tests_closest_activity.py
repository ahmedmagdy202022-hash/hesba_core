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

    def test_both_and_older_choices_keep_the_mixed_setup(self):
        self.assertEqual(catalog.closest("other", "mixed"), ("other", "mixed"))
        self.assertEqual(catalog.closest("other", "general"), ("other", "general"))
        self.assertEqual(catalog.sub_activity_label("other", "general"), "نشاط عام")  # an older install still reads its own choice
