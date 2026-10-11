"""ACT-FIT: the modules and capabilities screens fit the chosen activity and sub-activity."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core import activity_fit
from settings_core import setup_catalog as catalog
from settings_core.models import FeatureFlag
from settings_core.setup_services import module_flag_code


def sign_in(test):
    user = make_user(username="fit_owner")
    make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
    test.client.force_login(user)
    return user


class FitCatalogTests(TestCase):
    def every_sub_activity(self):
        for activity, subs in catalog.SUB_ACTIVITY_LABELS.items():
            for sub in subs:
                yield activity, sub

    def test_the_activity_s_required_modules_are_always_offered_and_locked(self):
        for activity, sub in self.every_sub_activity():
            behaves_as = catalog.closest(activity, sub)[0]
            for slug in catalog.required_modules(behaves_as):
                with self.subTest(activity=activity, sub=sub, slug=slug):
                    self.assertEqual(activity_fit.module_state(activity, sub, slug), catalog.REQUIRED)

    def test_every_offered_module_has_words_in_both_languages_and_features_open(self):
        for activity, sub in self.every_sub_activity():
            for lang in ("ar", "en"):
                rows = activity_fit.module_rows(activity, sub, lang)
                with self.subTest(activity=activity, sub=sub, lang=lang):
                    self.assertTrue(rows)
                    self.assertTrue(all(row["label"] and row["about"] for row in rows))
                    states = [row["preset"] for row in rows]
                    self.assertEqual(states, sorted(states, key=activity_fit.ORDER.index))  # most needed first
                    for feature in activity_fit.features(activity, sub, lang):
                        self.assertTrue(feature["url"].startswith("/"), feature)
                        self.assertIn(feature["module"], catalog.MODULE_SLUGS)

    def test_what_does_not_fit_an_activity_is_not_offered(self):
        clinic = {row["slug"] for row in activity_fit.module_rows(catalog.MEDICAL, "clinic")}
        self.assertFalse(clinic & {"tables_orders", "projects", "manufacturing"})
        shop = {row["slug"] for row in activity_fit.module_rows(catalog.COMMERCIAL, "retail")}
        self.assertFalse(shop & {"tables_orders", "projects", "manufacturing"})
        self.assertIn("manufacturing", {row["slug"] for row in activity_fit.module_rows(catalog.RESTAURANTS, "bakery")})
        self.assertNotIn("manufacturing", {row["slug"] for row in activity_fit.module_rows(catalog.RESTAURANTS, "cafe")})

    def test_modules_speak_the_activity_s_words(self):
        clinic = {row["slug"]: row for row in activity_fit.module_rows(catalog.MEDICAL, "clinic")}
        self.assertEqual(clinic["customers"]["label"], "المرضى")
        school = {row["slug"]: row for row in activity_fit.module_rows(catalog.EDUCATION, "tutoring_center", "en")}
        self.assertIn("parent", school["customers"]["about"].lower())
        builder = {row["slug"]: row for row in activity_fit.module_rows(catalog.CONTRACTING, "general")}
        self.assertIn("المستخلصات", builder["sales_operations"]["about"])

    def test_a_sub_activity_suggests_what_it_needs(self):
        self.assertEqual(activity_fit.module_state(catalog.SERVICES, "maintenance", "inventory"), catalog.SUGGESTED)
        self.assertEqual(activity_fit.module_state(catalog.SERVICES, "general", "inventory"), catalog.OPTIONAL)
        self.assertEqual(activity_fit.module_state(catalog.MEDICAL, "lab", "inventory"), catalog.SUGGESTED)
        self.assertEqual(activity_fit.module_state(catalog.SERVICES, "beauty", "appointments_visits"), catalog.SUGGESTED)

    def test_each_specialty_brings_its_own_screens(self):
        def slugs(activity, sub):
            return {feature["slug"] for feature in activity_fit.features(activity, sub)}

        self.assertIn("dental_chart", slugs(catalog.MEDICAL, "dental"))
        self.assertNotIn("dental_chart", slugs(catalog.MEDICAL, "clinic"))
        self.assertIn("lab_results", slugs(catalog.MEDICAL, "lab"))
        self.assertIn("animal_files", slugs(catalog.MEDICAL, "vet"))
        self.assertIn("work_orders", slugs(catalog.SERVICES, "maintenance"))
        self.assertNotIn("work_orders", slugs(catalog.SERVICES, "beauty"))
        self.assertIn("session_packages", slugs(catalog.SERVICES, "beauty"))
        self.assertIn("billable_hours", slugs(catalog.SERVICES, "professional"))
        self.assertIn("service_contracts", slugs(catalog.CONTRACTING, "maintenance_contracts"))
        self.assertIn("attendance", slugs(catalog.EDUCATION, "nursery"))
        self.assertEqual(slugs(catalog.COMMERCIAL, "retail"), set())
        self.assertEqual(slugs(catalog.OTHER, "services"), slugs(catalog.SERVICES, "general"))  # "closer to services" works as services


class WizardFitTests(TestCase):
    def setUp(self):
        sign_in(self)

    def test_the_modules_step_follows_the_sub_activity(self):
        dental = self.client.get("/setup/modules/?lang=ar&activity=medical&sub_activity=dental")
        self.assertContains(dental, 'data-module="appointments_visits" data-preset-state="required"')
        self.assertNotContains(dental, 'data-module="tables_orders"')
        self.assertContains(dental, 'data-activity-feature="dental_chart"')
        self.assertContains(dental, 'data-text-en="Patients"')
        self.assertContains(dental, "عيادة أسنان")
        cafe = self.client.get("/setup/modules/?lang=en&activity=restaurants&sub_activity=cafe")
        self.assertContains(cafe, 'data-module="tables_orders" data-preset-state="required"')
        self.assertNotContains(cafe, 'data-activity-feature="dental_chart"')

    def test_the_review_step_lists_what_comes_with_the_activity(self):
        page = self.client.get("/setup/review/", {"lang": "ar", "activity": "medical", "sub_activity": "lab",
                                                "modules": "customers,items_services,sales_operations,cashboxes,reports,appointments_visits"})
        self.assertContains(page, "data-activity-features")
        self.assertContains(page, "التحاليل ونتايجها")


class SettingsFitTests(TestCase):
    def test_a_clinic_sees_its_own_modules_grouped_and_in_its_words(self):
        prepared_client("medical", "dental", "customers,items_services,sales_operations,cashboxes,reports,appointments_visits")
        sign_in(self)
        page = self.client.get(reverse("settings_core:modules"))
        self.assertContains(page, 'data-module-group="required"')
        self.assertContains(page, "المرضى")
        self.assertContains(page, "عيادة أسنان")
        self.assertNotContains(page, 'data-module="tables_orders"')
        self.assertNotContains(page, 'data-module="projects"')
        self.assertContains(page, 'data-module-feature="dental_chart"')

    def test_a_module_on_that_does_not_fit_still_shows_so_it_can_be_switched_off(self):
        prepared_client("medical", "clinic", "customers,items_services,sales_operations,cashboxes,reports,appointments_visits")
        FeatureFlag.objects.update_or_create(code=module_flag_code("projects"), defaults={"name": "Projects", "enabled": True})
        sign_in(self)
        page = self.client.get(reverse("settings_core:modules"))
        self.assertContains(page, 'data-module-group="extra"')
        self.assertContains(page, 'data-module="projects" data-state="on" data-group="extra"')

    def test_capabilities_show_the_activity_s_screens_and_group_the_rest(self):
        prepared_client("services", "maintenance", "customers,items_services,sales_operations,cashboxes,reports")
        sign_in(self)
        page = self.client.get(reverse("settings_core:capabilities"))
        self.assertContains(page, 'data-activity-feature="work_orders"')
        self.assertContains(page, f'href="{reverse("workorders:list")}?lang=ar"')
        self.assertContains(page, 'data-capability-group="suggested"')  # serial numbers suit a repair shop
        self.assertContains(page, 'data-capability="serials"')
        self.assertNotContains(page, 'data-capability="variants"')  # sizes and colours do not
