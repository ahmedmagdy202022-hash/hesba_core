"""SETTINGS-001: the owner switches modules from Settings, with audit and no data loss."""

from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from master_data.models import Supplier
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.setup_services import enabled_modules


MODULES = reverse("settings_core:modules")


def sign_in(test, role_code=RoleCode.OWNER, username="modules_owner"):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    test.client.force_login(user)
    return user


class ModuleSettingsTests(TestCase):
    def setUp(self):
        prepared_client()  # commercial / retail, the usual modules on

    def post(self, module, enabled):
        return self.client.post(MODULES, {"module": module, "enabled": "1" if enabled else "0"})

    def test_owner_sees_every_module_with_its_state(self):
        sign_in(self)
        response = self.client.get(MODULES)
        self.assertEqual(response.status_code, 200)
        states = {row["slug"]: row["state"] for row in response.context["rows"]}
        self.assertEqual(states["purchases"], "on")
        self.assertEqual(states["sales_operations"], "required")
        self.assertEqual(states["appointments_visits"], "soon")
        # EXP-001: expenses has a backend now, so it switches like any module.
        self.assertEqual(states["expenses"], "off")
        self.assertContains(response, 'data-module="purchases" data-state="on"')

    def test_switching_off_and_on_keeps_the_data(self):
        sign_in(self)
        Supplier.objects.create(supplier_code="SUP-KEEP", name="Kept Supplier")
        self.post("suppliers", False)
        self.assertNotIn("suppliers", enabled_modules())
        self.assertEqual(self.client.get(reverse("master_data:suppliers")).status_code, 403)
        self.assertTrue(Supplier.objects.filter(supplier_code="SUP-KEEP").exists())
        self.post("suppliers", True)
        self.assertIn("suppliers", enabled_modules())
        self.assertContains(self.client.get(reverse("master_data:suppliers")), "Kept Supplier")

    def test_each_switch_is_audited(self):
        user = sign_in(self)
        self.post("purchases", False)
        log = AuditLog.objects.filter(action="disable_module").latest("pk")
        self.assertEqual(log.actor, user)
        self.assertEqual(log.object_id, "module.purchases")
        self.assertEqual((log.before_data, log.after_data), ({"enabled": True}, {"enabled": False}))

    def test_required_and_unavailable_modules_cannot_change(self):
        sign_in(self)
        self.post("sales_operations", False)
        self.assertIn("sales_operations", enabled_modules())
        self.post("appointments_visits", True)
        self.assertNotIn("appointments_visits", enabled_modules())
        page = self.client.get(MODULES)
        self.assertNotContains(page, 'name="module" value="sales_operations"')
        self.assertNotContains(page, 'name="module" value="appointments_visits"')

    def test_manager_can_view_but_not_change(self):
        sign_in(self, RoleCode.MANAGER, "modules_manager")
        self.assertEqual(self.client.get(MODULES).status_code, 200)
        self.assertEqual(self.post("purchases", False).status_code, 403)
        self.assertIn("purchases", enabled_modules())

    def test_cashier_is_refused(self):
        sign_in(self, RoleCode.CASHIER, "modules_cashier")
        self.assertEqual(self.client.get(MODULES).status_code, 403)

    def test_disabled_module_page_links_to_this_screen(self):
        sign_in(self)
        self.post("purchases", False)
        self.assertContains(self.client.get(reverse("purchases:list")), MODULES, status_code=403)

    def test_settings_overview_links_here(self):
        sign_in(self)
        self.assertContains(self.client.get(reverse("settings_core:overview")), MODULES)


class ModuleSettingsBeforeSetupTests(TestCase):
    def test_nothing_changes_before_setup(self):
        sign_in(self, username="modules_fresh")
        self.client.post(MODULES, {"module": "purchases", "enabled": "1"})
        self.assertEqual(enabled_modules(), ())
