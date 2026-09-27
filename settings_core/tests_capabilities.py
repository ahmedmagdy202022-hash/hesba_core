"""CAP-001: capabilities suggested per activity, chosen at setup, switchable later."""

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core import capabilities as caps
from settings_core.models import ClientProfile, FeatureFlag
from reports.navigation import nav_items
from settings_core.setup_services import complete_setup, usable_modules


CAPS = reverse("settings_core:capabilities")


def sign_in(test, role_code=RoleCode.OWNER, username="caps_owner"):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    test.client.force_login(user)
    return user


class CatalogTests(SimpleTestCase):
    def test_each_commercial_activity_has_its_own_suggestions(self):
        self.assertEqual(caps.suggested("commercial", "pharmacy"), ("pos", "barcode", "units", "batches_expiry"))
        self.assertEqual(caps.suggested("commercial", "fashion"), ("pos", "barcode", "variants"))
        self.assertEqual(caps.suggested("commercial", "electronics"), ("pos", "barcode", "serials", "installments"))
        self.assertIn("price_lists", caps.suggested("commercial", "wholesale"))
        self.assertNotIn("pos", caps.suggested("commercial", "wholesale"))
        self.assertEqual(caps.suggested("services", "clinic"), ())

    def test_only_shipped_capabilities_are_ticked_or_accepted(self):
        self.assertEqual(caps.default_selection("commercial", "pharmacy"), ("pos", "barcode"))
        self.assertEqual(caps.parse("pos, variants, bogus,barcode"), ("pos", "barcode"))
        self.assertTrue(caps.is_available("pos"))
        self.assertFalse(caps.is_available("e_invoice"))

    def test_every_capability_is_described_in_both_languages(self):
        for slug, entry in caps.CAPABILITIES.items():
            with self.subTest(slug=slug):
                for key in ("ar", "en", "about_ar", "about_en"):
                    self.assertTrue(entry[key])


class SetupChoiceTests(TestCase):
    def profile(self):
        return ClientProfile.objects.create(client_code="C", legal_name="L", display_name="D")

    def test_installation_from_before_capabilities_keeps_its_screens(self):
        self.assertEqual(caps.enabled_capabilities(), ("pos", "barcode"))

    def test_setup_applies_the_preset_when_nothing_is_sent(self):
        complete_setup(self.profile(), "commercial", "wholesale", "")
        self.assertEqual(caps.enabled_capabilities(), ("barcode", "price_lists"))
        self.assertFalse(FeatureFlag.objects.get(code="capability.pos").enabled)
        self.assertFalse(FeatureFlag.objects.filter(code="capability.units").exists())  # not shipped yet

    def test_setup_stores_what_the_owner_ticked(self):
        complete_setup(self.profile(), "commercial", "retail", "", capabilities_raw="barcode")
        self.assertEqual(caps.enabled_capabilities(), ("barcode",))
        log = AuditLog.objects.filter(action="complete_setup").latest("pk")
        self.assertEqual(log.after_data["capabilities"], ["barcode"])


class ReviewStepTests(TestCase):
    def test_review_ticks_the_suggestions_and_shows_what_is_coming(self):
        sign_in(self)
        page = self.client.get(reverse("setup_review"), {"activity": "commercial", "sub_activity": "pharmacy", "modules": "inventory"})
        rows = {row["slug"]: row for row in page.context["capability_rows"]}
        self.assertTrue(rows["pos"]["checked"])
        self.assertTrue(rows["batches_expiry"]["suggested"])
        self.assertFalse(rows["batches_expiry"]["checked"])  # suggested, not shipped yet
        self.assertFalse(rows["variants"]["suggested"])
        self.assertContains(page, "قدرات مقترحة لنشاطك")
        self.assertContains(page, 'value="pos" form="setup-complete-form" checked')
        self.assertContains(page, "قريبًا")

    def test_completing_setup_saves_the_ticked_capabilities(self):
        ClientProfile.objects.create(client_code="C", legal_name="L", display_name="D")
        sign_in(self)
        self.client.post(reverse("setup_complete"), {"lang": "ar", "activity": "commercial", "sub_activity": "retail", "modules": "inventory", "capabilities_sent": "1", "capability": ["barcode"]})
        self.assertEqual(caps.enabled_capabilities(), ("barcode",))
        # Nothing ticked is a real answer too: every capability off.
        self.client.post(reverse("setup_complete"), {"lang": "ar", "activity": "commercial", "sub_activity": "retail", "modules": "inventory", "capabilities_sent": "1"})
        self.assertEqual(caps.enabled_capabilities(), ())


class SwitchedOffCapabilityTests(TestCase):
    def setUp(self):
        profile = prepared_client()
        complete_setup(profile, "commercial", "retail", "customers,items_services,sales_operations,inventory,cashboxes,reports", capabilities_raw="")
        self.user = sign_in(self)

    def test_its_screens_close_and_its_links_disappear(self):
        page = self.client.get(reverse("sales:pos"))
        self.assertEqual(page.status_code, 403)
        self.assertContains(page, "الكاشير السريع", status_code=403)
        self.assertEqual(self.client.get(reverse("barcode:labels")).status_code, 403)
        sales = self.client.get(reverse("sales:list"))
        self.assertNotContains(sales, reverse("sales:pos"))
        self.assertNotIn("pos", {item["key"] for item in nav_items(self.user, "ar", set(usable_modules()))})
        self.assertNotContains(self.client.get(reverse("master_data:items")), reverse("barcode:labels"))

    def test_switching_it_back_on_reopens_everything(self):
        self.client.post(CAPS, {"capability": "pos", "enabled": "1"})
        self.assertEqual(self.client.get(reverse("sales:pos")).status_code, 200)
        self.assertContains(self.client.get(reverse("sales:list")), reverse("sales:pos"))


class CapabilitySettingsTests(TestCase):
    def setUp(self):
        prepared_client()  # retail: pos and barcode on

    def test_owner_sees_state_suggestion_and_coming_soon(self):
        sign_in(self)
        page = self.client.get(CAPS)
        states = {row["slug"]: row["state"] for row in page.context["rows"]}
        self.assertEqual((states["pos"], states["barcode"], states["price_lists"], states["units"]), ("on", "on", "off", "soon"))
        self.assertContains(page, "مقترح لنشاطك")
        self.assertContains(self.client.get(reverse("settings_core:overview")), CAPS)

    def test_switch_is_audited_and_unshipped_ones_are_refused(self):
        user = sign_in(self)
        self.client.post(CAPS, {"capability": "barcode", "enabled": "0"})
        self.assertFalse(caps.capability_enabled("barcode"))
        log = AuditLog.objects.filter(action="disable_capability").latest("pk")
        self.assertEqual((log.actor, log.object_id), (user, "capability.barcode"))
        self.client.post(CAPS, {"capability": "e_invoice", "enabled": "1"})
        self.assertFalse(FeatureFlag.objects.filter(code="capability.e_invoice").exists())

    def test_only_settings_managers_switch(self):
        sign_in(self, RoleCode.CASHIER, "caps_cashier")
        self.assertIn(self.client.post(CAPS, {"capability": "pos", "enabled": "0"}).status_code, (403,))
        self.assertTrue(caps.capability_enabled("pos"))
