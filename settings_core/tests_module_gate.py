"""GATE-001: a switched-off module is closed at its URLs, not just hidden."""

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice
from reports.tests_dashboard import prepared_client
from settings_core.module_gate import module_for_path
from settings_core.setup_services import enabled_modules, module_flag_code
from settings_core.models import FeatureFlag


WITHOUT_PURCHASES = "customers,suppliers,items_services,sales_operations,inventory,cashboxes,reports"


def sign_in(test, role_code=RoleCode.OWNER, username="gate_owner"):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    test.client.force_login(user)
    return user


class ModuleForPathTests(SimpleTestCase):
    def test_paths_map_to_their_module(self):
        self.assertEqual(module_for_path("/purchases/"), "purchases")
        self.assertEqual(module_for_path("/purchases/12/post/"), "purchases")
        self.assertEqual(module_for_path("/sales/collections/new/"), "sales_operations")
        self.assertEqual(module_for_path("/master-data/suppliers/3/edit/"), "suppliers")
        self.assertEqual(module_for_path("/master-data/categories/"), "items_services")

    def test_shared_screens_are_never_gated(self):
        for path in ("/dashboard/", "/reports/purchases/", "/settings/", "/profile/", "/closing/", "/master-data/", "/master-data/locations/"):
            with self.subTest(path=path):
                self.assertIsNone(module_for_path(path))


class ModuleGateTests(TestCase):
    def setUp(self):
        prepared_client(modules=WITHOUT_PURCHASES)
        self.assertNotIn("purchases", enabled_modules())

    def test_a_switched_off_module_shows_the_disabled_page(self):
        sign_in(self)
        for name in ("purchases:list", "purchases:create", "purchases:payments"):
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 403)
                self.assertContains(response, 'data-module-disabled="purchases"', status_code=403)
                self.assertContains(response, "المشتريات", status_code=403)
                # The page keeps the app shell, so the user is not stranded.
                self.assertContains(response, 'class="hs-sidebar"', status_code=403)

    def test_posting_into_a_switched_off_module_changes_nothing(self):
        sign_in(self)
        before = PurchaseInvoice.objects.count()
        response = self.client.post(reverse("purchases:create"), {"invoice_number": "PI-GATE"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(PurchaseInvoice.objects.count(), before)

    def test_enabled_modules_and_reports_stay_open(self):
        sign_in(self)
        for name in ("sales:list", "inventory:stock", "master_data:suppliers", "reports:purchases", "report_hub", "dashboard_snapshot"):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_switching_the_module_back_on_reopens_it(self):
        sign_in(self)
        FeatureFlag.objects.filter(code=module_flag_code("purchases")).update(enabled=True)
        self.assertEqual(self.client.get(reverse("purchases:list")).status_code, 200)

    def test_owner_gets_a_settings_button_and_cashier_a_hint(self):
        sign_in(self)
        owner_page = self.client.get(reverse("purchases:list"))
        self.assertContains(owner_page, reverse("settings_core:overview"), status_code=403)
        self.client.logout()
        sign_in(self, RoleCode.CASHIER, "gate_cashier")
        cashier_page = self.client.get(reverse("purchases:list"))
        self.assertContains(cashier_page, "اطلب من صاحب الحساب", status_code=403)
        self.assertNotContains(cashier_page, "فتح الإعدادات", status_code=403)

    def test_english_page(self):
        sign_in(self)
        response = self.client.get(reverse("purchases:list"), {"lang": "en"})
        self.assertContains(response, "The “Purchases” module is not enabled", status_code=403)

    def test_anonymous_users_still_go_to_login_first(self):
        response = self.client.get(reverse("purchases:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_master_data_sections_follow_their_own_module(self):
        FeatureFlag.objects.filter(code=module_flag_code("suppliers")).update(enabled=False)
        sign_in(self)
        self.assertEqual(self.client.get(reverse("master_data:suppliers")).status_code, 403)
        self.assertEqual(self.client.get(reverse("master_data:customers")).status_code, 200)


class BeforeSetupTests(TestCase):
    def test_nothing_is_gated_before_setup_is_complete(self):
        sign_in(self, username="gate_fresh")
        self.assertEqual(self.client.get(reverse("purchases:list")).status_code, 200)
