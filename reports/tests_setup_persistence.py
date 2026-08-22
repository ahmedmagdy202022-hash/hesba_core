from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.models import AuditLog
from reports.test_utils import AuthenticatedTestCase
from settings_core import setup_catalog as catalog
from settings_core.models import ActivityType, ClientProfile, FeatureFlag
from settings_core.setup_services import (
    complete_setup,
    enabled_modules,
    module_flag_code,
    module_is_enabled,
    usable_modules,
)


def make_profile(**kwargs):
    defaults = {"client_code": "DEMO", "legal_name": "Demo Legal", "display_name": "Demo Store"}
    defaults.update(kwargs)
    return ClientProfile.objects.create(**defaults)


class SetupCatalogTests(TestCase):
    def test_it_knows_the_twelve_modules_in_wizard_order(self):
        self.assertEqual(len(catalog.MODULE_SLUGS), 12)
        self.assertEqual(catalog.MODULE_SLUGS[0], "customers")
        self.assertEqual(catalog.MODULE_SLUGS[-1], "employees_technicians")

    def test_presets_match_the_documented_counts(self):
        for activity, required, suggested in (
            (catalog.COMMERCIAL, 4, 6),
            (catalog.SERVICES, 4, 2),
        ):
            with self.subTest(activity=activity):
                self.assertEqual(len(catalog.required_modules(activity)), required)
                self.assertEqual(len(catalog.default_modules(activity)), required + suggested)

    def test_sales_operations_is_not_preset_for_a_services_install(self):
        self.assertEqual(catalog.preset_state(catalog.SERVICES, "sales_operations"), catalog.OPTIONAL)
        self.assertEqual(catalog.preset_state(catalog.COMMERCIAL, "sales_operations"), catalog.REQUIRED)

    def test_every_activity_offers_eight_sub_activities(self):
        for activity in (catalog.COMMERCIAL, catalog.SERVICES):
            with self.subTest(activity=activity):
                self.assertEqual(len(catalog.SUB_ACTIVITY_LABELS[activity]), 8)

    def test_it_validates_activities_and_sub_activities(self):
        self.assertTrue(catalog.is_valid_activity("commercial"))
        self.assertFalse(catalog.is_valid_activity("manufacturing"))
        self.assertTrue(catalog.is_valid_sub_activity("commercial", "retail"))
        self.assertFalse(catalog.is_valid_sub_activity("services", "retail"))

    def test_cleaning_drops_unknown_slugs_and_keeps_required_ones(self):
        cleaned = catalog.clean_module_slugs("commercial", "customers,not_a_module")

        self.assertIn("customers", cleaned)
        self.assertNotIn("not_a_module", cleaned)
        for slug in catalog.required_modules("commercial"):
            self.assertIn(slug, cleaned)

    def test_cleaning_returns_wizard_order_regardless_of_input_order(self):
        cleaned = catalog.clean_module_slugs("commercial", "inventory,customers,purchases")
        self.assertEqual(list(cleaned), [s for s in catalog.MODULE_SLUGS if s in set(cleaned)])

    def test_labels_fall_back_to_the_slug_when_unknown(self):
        self.assertEqual(catalog.module_label("customers", "en"), "Customers")
        self.assertEqual(catalog.module_label("customers", "ar"), "العملاء")
        self.assertEqual(catalog.module_label("mystery_module", "en"), "mystery module")


class CompleteSetupServiceTests(TestCase):
    def setUp(self):
        super().setUp()
        self.profile = make_profile()

    def test_it_records_the_activity_and_stamps_completion(self):
        complete_setup(self.profile, "commercial", "retail", "customers,reports")

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.activity_slug, "commercial")
        self.assertEqual(self.profile.sub_activity_slug, "retail")
        self.assertIsNotNone(self.profile.setup_completed_at)
        self.assertTrue(self.profile.setup_is_complete)

    def test_it_derives_activity_type_from_the_wizard_slug(self):
        complete_setup(self.profile, "commercial", "retail", "")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.activity_type, ActivityType.STORE)

        complete_setup(self.profile, "services", "clinic", "")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.activity_type, ActivityType.SERVICES)

    def test_it_writes_a_flag_for_every_module_on_or_off(self):
        complete_setup(self.profile, "commercial", "retail", "customers")

        self.assertEqual(
            FeatureFlag.objects.filter(code__startswith="module.").count(),
            len(catalog.MODULE_SLUGS),
        )
        self.assertTrue(module_is_enabled("customers"))
        self.assertFalse(module_is_enabled("appointments_visits"))

    def test_required_modules_survive_a_request_that_left_them_out(self):
        complete_setup(self.profile, "commercial", "retail", "customers")

        for slug in catalog.required_modules("commercial"):
            with self.subTest(slug=slug):
                self.assertTrue(module_is_enabled(slug))

    def test_enabled_modules_reads_back_in_wizard_order(self):
        complete_setup(self.profile, "commercial", "retail", "inventory,customers")

        enabled = enabled_modules()
        self.assertEqual(list(enabled), [s for s in catalog.MODULE_SLUGS if s in set(enabled)])
        self.assertIn("customers", enabled)
        self.assertIn("inventory", enabled)

    def test_usable_modules_hides_the_ones_with_no_backend(self):
        complete_setup(self.profile, "commercial", "retail", "customers,expenses,pdf_printing")

        self.assertIn("customers", usable_modules())
        self.assertNotIn("expenses", usable_modules())
        self.assertNotIn("pdf_printing", usable_modules())

    def test_it_refuses_an_unknown_activity(self):
        with self.assertRaises(ValueError):
            complete_setup(self.profile, "manufacturing", "retail", "")

    def test_it_refuses_a_sub_activity_from_the_wrong_activity(self):
        with self.assertRaises(ValueError):
            complete_setup(self.profile, "services", "retail", "")

    def test_a_refused_request_changes_nothing(self):
        with self.assertRaises(ValueError):
            complete_setup(self.profile, "manufacturing", "retail", "customers")

        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.setup_completed_at)
        self.assertEqual(FeatureFlag.objects.count(), 0)

    def test_running_it_again_keeps_the_original_completion_time(self):
        complete_setup(self.profile, "commercial", "retail", "customers")
        self.profile.refresh_from_db()
        first = self.profile.setup_completed_at

        complete_setup(self.profile, "services", "clinic", "customers")
        self.profile.refresh_from_db()

        self.assertEqual(self.profile.setup_completed_at, first)
        self.assertEqual(self.profile.activity_slug, "services")

    def test_rerunning_it_switches_modules_off_again(self):
        complete_setup(self.profile, "commercial", "retail", "inventory")
        self.assertTrue(module_is_enabled("inventory"))

        complete_setup(self.profile, "commercial", "retail", "")
        self.assertFalse(module_is_enabled("inventory"))

    def test_it_records_an_audit_entry(self):
        user = get_user_model().objects.create_user(username="setup_tester", password="x")
        complete_setup(self.profile, "commercial", "retail", "customers", user=user)

        log = AuditLog.objects.get(action="complete_setup")
        self.assertEqual(log.actor, user)
        self.assertEqual(log.module, "settings")
        self.assertEqual(log.after_data["activity_slug"], "commercial")
        self.assertIn("customers", log.after_data["modules"])
        self.assertIsNone(log.before_data["setup_completed_at"])

    def test_the_flag_code_is_namespaced(self):
        self.assertEqual(module_flag_code("customers"), "module.customers")


class SetupCompleteRouteTests(AuthenticatedTestCase):
    def test_posting_the_wizard_saves_and_redirects_to_confirmation(self):
        make_profile()

        response = self.client.post(
            "/setup/complete/",
            {"lang": "ar", "activity": "commercial", "sub_activity": "retail", "modules": "customers,inventory"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/setup/complete/?lang=ar")

        profile = ClientProfile.get_active()
        self.assertEqual(profile.activity_slug, "commercial")
        self.assertEqual(profile.sub_activity_slug, "retail")
        self.assertTrue(module_is_enabled("inventory"))

    def test_the_confirmation_page_offers_a_way_to_the_dashboard(self):
        profile = make_profile()
        complete_setup(profile, "commercial", "retail", "customers")

        response = self.client.get("/setup/complete/?lang=en")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/dashboard/"')
        self.assertContains(response, "Go to the dashboard")

    def test_a_tampered_activity_returns_to_review_without_saving(self):
        make_profile()

        response = self.client.post(
            "/setup/complete/",
            {"lang": "ar", "activity": "manufacturing", "sub_activity": "retail", "modules": "customers"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/setup/review/"))
        self.assertIsNone(ClientProfile.get_active().setup_completed_at)

    def test_posting_before_bootstrap_returns_to_review(self):
        response = self.client.post(
            "/setup/complete/",
            {"lang": "ar", "activity": "commercial", "sub_activity": "retail", "modules": "customers"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/setup/review/"))
        self.assertEqual(FeatureFlag.objects.count(), 0)

    def test_the_route_rejects_other_methods(self):
        self.assertEqual(self.client.put("/setup/complete/").status_code, 405)


class AfterLoginGateTests(AuthenticatedTestCase):
    def test_it_sends_an_unprepared_database_to_setup(self):
        response = self.client.get("/start/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/setup/")

    def test_it_sends_an_unfinished_setup_to_setup(self):
        make_profile()

        response = self.client.get("/start/")

        self.assertEqual(response["Location"], "/setup/")

    def test_it_sends_a_finished_setup_to_the_dashboard(self):
        profile = make_profile()
        complete_setup(profile, "commercial", "retail", "customers")

        response = self.client.get("/start/")

        self.assertEqual(response["Location"], "/dashboard/")

    def test_login_lands_on_the_gate_rather_than_setup(self):
        from django.conf import settings

        self.assertEqual(settings.LOGIN_REDIRECT_URL, "/start/")

    def test_the_root_redirect_routes_an_authenticated_user_through_the_gate(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/start/")
