"""DEMO-RESTART: the showcase starts over from choosing the activity."""

from django.test import TestCase, override_settings
from django.urls import reverse

from settings_core.models import ClientProfile


def seeded_counts():
    from entities.models import Entity
    from expenses.models import ExpenseCategory
    from permissions.models import Permission, Role, RolePermission
    from taxes.models import TaxRate

    return {model.__name__: model.objects.count() for model in (Permission, Role, RolePermission, TaxRate, ExpenseCategory, Entity)}


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
class DemoRestartTests(TestCase):
    def test_restart_empties_the_business_and_restores_every_seed(self):
        from django.core.management import call_command

        from master_data.models import Item
        from sales.models import SalesInvoice

        seeds = seeded_counts()
        self.assertTrue(all(seeds.values()), seeds)
        call_command("prepare_demo", verbosity=0)
        self.assertTrue(SalesInvoice.objects.exists())
        self.assertTrue(ClientProfile.get_active().setup_is_complete)

        response = self.client.post(reverse("demo_restart"), {"lang": "ar"})
        self.assertRedirects(response, "/setup/activity/?lang=ar", fetch_redirect_response=False)
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertFalse(Item.objects.exists())
        self.assertEqual(seeded_counts(), seeds)
        profile = ClientProfile.get_active()
        self.assertFalse(profile.setup_is_complete)
        # Signed in as the owner, straight onto the first wizard step.
        page = self.client.get("/setup/activity/?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.client.get(reverse("after_login")).url, reverse("setup_gate"))
        # The demo logins still work with the demo password.
        self.client.logout()
        self.assertTrue(self.client.login(username="cashier", password="Demo-pass-1"))

    def test_the_login_page_offers_it_and_get_does_nothing(self):
        from django.core.management import call_command

        call_command("prepare_demo", verbosity=0)
        self.assertContains(self.client.get(reverse("login")), "data-demo-restart")
        self.client.get(reverse("demo_restart"))
        self.assertTrue(ClientProfile.get_active().setup_is_complete)

    def test_after_choosing_an_activity_sample_data_is_one_click(self):
        from sales.models import SalesInvoice
        from settings_core import setup_catalog as catalog
        from settings_core.setup_services import complete_setup

        self.client.post(reverse("demo_restart"), {"lang": "ar"})
        complete_setup(ClientProfile.get_active(), "medical", "clinic", ",".join(catalog.MODULE_SLUGS))
        self.assertContains(self.client.get(reverse("setup_complete")), "data-demo-sample")
        response = self.client.post(reverse("demo_sample"), {"lang": "ar"})
        self.assertRedirects(response, "/dashboard/?lang=ar", fetch_redirect_response=False)
        self.assertTrue(SalesInvoice.objects.filter(status="posted").exists())
        self.assertEqual(ClientProfile.get_active().activity_slug, "medical")
        self.assertEqual(self.client.get("/dashboard/?lang=ar").status_code, 200)


class DemoRestartOffTests(TestCase):
    def test_a_real_install_cannot_be_wiped(self):
        response = self.client.post(reverse("demo_restart"))
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(self.client.get(reverse("login")), "data-demo-restart")


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
class DemoMenuFollowsActivityTests(TestCase):
    """NAV-ACT: flipping the demo's activity switches its modules the way the
    wizard would, so the menu only shows what that business uses."""

    def test_switching_the_activity_switches_the_menu(self):
        from django.core.management import call_command

        from reports.tests_shell import sidebar_links

        call_command("prepare_demo", verbosity=0)
        self.client.login(username="owner", password="Demo-pass-1")
        shop = sidebar_links(self.client.get("/dashboard/?lang=ar"))
        self.assertIn(reverse("purchases:list"), shop)
        self.assertNotIn(reverse("restaurant:board"), shop)
        self.assertNotIn(reverse("manufacturing:home"), shop)

        self.client.post(reverse("demo_activity"), {"activity": "medical:clinic", "lang": "ar"})
        clinic = sidebar_links(self.client.get("/dashboard/?lang=ar"))
        self.assertIn(reverse("appointments:agenda"), clinic)
        for hidden in ("purchases:list", "restaurant:board", "manufacturing:home", "projects:list", "inventory:stock"):
            with self.subTest(hidden=hidden):
                self.assertNotIn(reverse(hidden), clinic)

        self.client.post(reverse("demo_activity"), {"activity": "manufacturing:food", "lang": "ar"})
        self.assertIn(reverse("manufacturing:home"), sidebar_links(self.client.get("/dashboard/?lang=ar")))
        self.client.post(reverse("demo_activity"), {"activity": "restaurants:restaurant", "lang": "ar"})
        self.assertIn(reverse("restaurant:board"), sidebar_links(self.client.get("/dashboard/?lang=ar")))
