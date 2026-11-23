from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


def registered_models():
    """Every model registered with the default admin site."""
    return sorted(admin.site._registry, key=lambda m: m._meta.label)


class AdminSmokeTests(TestCase):
    """Every registered admin screen must load, so a misconfigured
    ModelAdmin (bad list_display, stale field name, broken FK) fails here
    rather than in production. Registering a new model adds it to these
    checks automatically."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user = get_user_model().objects.create_superuser(
            username="admin_smoke_tester",
            email="",
            password="smoke-tests-only",
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_admin_registry_is_not_empty(self):
        self.assertGreater(len(registered_models()), 0)

    def test_admin_index_loads(self):
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)

    def test_every_changelist_loads(self):
        for model in registered_models():
            meta = model._meta
            url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")

            with self.subTest(model=meta.label):
                response = self.client.get(url)

                self.assertEqual(
                    response.status_code,
                    200,
                    f"{meta.label} changelist returned {response.status_code}",
                )

    def test_every_add_form_loads_or_is_deliberately_blocked(self):
        for model in registered_models():
            meta = model._meta
            url = reverse(f"admin:{meta.app_label}_{meta.model_name}_add")

            with self.subTest(model=meta.label):
                response = self.client.get(url)

                # 403 is legitimate for admins that disable creation on
                # purpose; anything else means the form itself is broken.
                self.assertIn(
                    response.status_code,
                    (200, 403),
                    f"{meta.label} add form returned {response.status_code}",
                )
