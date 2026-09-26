"""CATALOG-003: modules with no backend are shown as coming soon, never switched on."""

from django.test import TestCase
from django.urls import reverse

from reports.test_utils import AuthenticatedTestCase
from settings_core import setup_catalog as catalog
from settings_core.models import ClientProfile
from settings_core.setup_services import complete_setup, enabled_modules


class ComingSoonCatalogTests(TestCase):
    def test_clean_module_slugs_drops_modules_without_backend(self):
        cleaned = catalog.clean_module_slugs("commercial", "customers,pdf_printing,appointments_visits,employees_technicians")
        self.assertIn("customers", cleaned)
        for slug in catalog.MODULES_WITHOUT_BACKEND:
            with self.subTest(module=slug):
                self.assertNotIn(slug, cleaned)

    def test_setup_never_stores_them_as_enabled(self):
        profile = ClientProfile.objects.create(client_code="SOON", legal_name="Soon Co", display_name="Soon")
        complete_setup(profile, "services", "clinic", "customers,appointments_visits,employees_technicians")
        self.assertNotIn("appointments_visits", enabled_modules())
        self.assertNotIn("employees_technicians", enabled_modules())


class ComingSoonWizardTests(AuthenticatedTestCase):
    def test_modules_step_marks_the_unavailable_cards(self):
        response = self.client.get(reverse("setup_modules"), {"activity": "commercial", "sub_activity": "retail"})
        body = response.content.decode()
        self.assertEqual(body.count('data-soon="true"'), len(catalog.MODULES_WITHOUT_BACKEND))
        for slug in catalog.MODULES_WITHOUT_BACKEND:
            with self.subTest(module=slug):
                tag = body[body.index(f'data-module="{slug}"') - 80: body.index(f'data-module="{slug}"') + 400]
                self.assertIn('data-soon="true"', tag)
        self.assertContains(response, "soon: 'قريبًا'")
        self.assertContains(response, "if(state === 'soon'){return;}")

    def test_review_step_leaves_them_out(self):
        response = self.client.get(reverse("setup_review"), {"activity": "commercial", "sub_activity": "retail", "modules": "customers,pdf_printing"})
        slugs = [row["slug"] for row in response.context["selected_modules"]]
        self.assertEqual(slugs, ["customers"])
