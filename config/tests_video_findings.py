"""Services video findings: the demo opens the doors a services visitor is walked through (f02, f02b, f09), and real installs are untouched."""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from config.demo_templates import complete_demo_setup, demo_capabilities, services_demo_extras
from settings_core import capabilities as caps

SERVICES_DEMO = ("gift_cards", "deposits", "online_booking", "loyalty")
#: What setup pre-ticks for a real services install, taken from develop before this change: it must not move.
REAL_DEFAULTS = {"maintenance": ("serials",), "beauty": ("pos", "shift_required", "loyalty")}


class ServicesDemoCapabilitiesTests(TestCase):
    def setUp(self):
        from reports.tests_dashboard import prepared_client

        prepared_client()

    def test_the_workshop_and_the_salon_demo_switch_the_walked_through_doors_on(self):
        for sub in ("maintenance", "beauty"):
            with self.subTest(sub=sub):
                chosen = demo_capabilities("services", sub, list(caps.default_selection("services", sub)))
                for slug in SERVICES_DEMO:
                    self.assertIn(slug, chosen)
                self.assertNotIn("payment_links", chosen)  # the mock gateway is refused while DEBUG is off (payments.E001)

    def test_complete_demo_setup_leaves_them_on_and_the_pay_link_off(self):
        for sub in ("maintenance", "beauty"):
            with self.subTest(sub=sub):
                complete_demo_setup("services", sub)
                on = caps.switched_on()
                for slug in SERVICES_DEMO:
                    self.assertIn(slug, on)
                self.assertNotIn("payment_links", on)
                self.assertTrue(caps.capability_enabled("gift_cards"))

    def test_no_other_business_gets_them(self):
        self.assertEqual(services_demo_extras("commercial", "retail"), ())
        self.assertEqual(services_demo_extras("medical", "clinic"), ())
        self.assertEqual(services_demo_extras("services", "something-else"), ())
        self.assertNotIn("gift_cards", demo_capabilities("medical", "clinic", []))
        self.assertNotIn("deposits", demo_capabilities("commercial", "retail", []))

    @override_settings(DEMO_MODE=True)
    def test_switching_to_a_services_business_inside_the_demo_does_the_same(self):
        from config.demo import switch_activity

        owner = get_user_model().objects.create_superuser("vf_demo_root", password="x-Pass-12345")
        request = RequestFactory().post("/demo/switch/", {"activity": "services:beauty"})
        request.user = owner
        request.session = {}
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        switch_activity(request)
        for slug in SERVICES_DEMO:
            self.assertIn(slug, caps.switched_on())
        self.assertNotIn("payment_links", caps.switched_on())


class RealInstallsAreUntouchedTests(TestCase):
    def setUp(self):
        from reports.tests_dashboard import prepared_client

        self.profile = prepared_client()

    def test_what_setup_preticks_for_services_is_what_it_was(self):
        for sub, expected in REAL_DEFAULTS.items():
            with self.subTest(sub=sub):
                self.assertEqual(caps.default_selection("services", sub), expected)
                self.assertEqual(caps.suggested("services", sub), expected)
                for slug in ("gift_cards", "deposits", "online_booking", "payment_links"):
                    self.assertEqual(caps.preset_state("services", sub, slug), caps.OPTIONAL)

    def test_a_fresh_non_demo_services_profile_has_the_same_capability_set_as_before(self):
        from settings_core import setup_interview as interview
        from settings_core.setup_services import complete_setup

        for sub, expected in REAL_DEFAULTS.items():
            with self.subTest(sub=sub):
                modules, features = interview.resolve("services", sub, interview.suggested_answers("services", sub))
                complete_setup(self.profile, "services", sub, ",".join(modules), capabilities_raw=",".join(features))
                self.assertEqual(caps.switched_on(), expected)
