from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from settings_core.models import FeatureFlag
from settings_core.setup_catalog import MODULE_SLUGS
from settings_core.setup_services import module_flag_code


class DashboardFunctionalLinkTests(TestCase):
    def setUp(self):
        user = make_user(username="dashboard_functional_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)
        for slug in MODULE_SLUGS:
            FeatureFlag.objects.create(code=module_flag_code(slug), name=slug, enabled=True)

    def test_quick_actions_target_completed_flows(self):
        actions = {row["key"]: row["url_name"] for row in self.client.get(reverse("dashboard_snapshot")).context["quick_actions"]}
        self.assertEqual(actions["record_sale"], "sales:create")
        self.assertEqual(actions["record_purchase"], "purchases:create")
        self.assertEqual(actions["collect"], "sales:payment_create")
        self.assertEqual(actions["pay_supplier"], "purchases:payment_create")
        self.assertEqual(actions["open_reports"], "report_hub")
        self.assertEqual(actions["close_day"], "closing:list")
        self.assertNotIn("home", actions.values())

    def test_navigation_targets_completed_permission_safe_routes(self):
        navigation = {row["key"]: row["url_name"] for row in self.client.get(reverse("dashboard_snapshot")).context["nav_items"]}
        self.assertEqual(navigation["operations"], "sales:list")
        self.assertEqual(navigation["purchases"], "purchases:list")
        self.assertEqual(navigation["inventory"], "inventory:stock")
        self.assertEqual(navigation["cashboxes"], "cashboxes:list")
        self.assertEqual(navigation["reports"], "report_hub")
        self.assertEqual(navigation["closing"], "closing:list")
        self.assertEqual(navigation["profile"], "accounts:profile")
        self.assertEqual(navigation["settings"], "settings_core:overview")
        self.assertNotIn("status_counts_report", navigation.values())

    def test_every_dashboard_destination_resolves_and_opens(self):
        response = self.client.get(reverse("dashboard_snapshot"))
        destinations = {row["url_name"] for row in response.context["nav_items"] + response.context["quick_actions"]}
        for url_name in destinations:
            with self.subTest(url_name=url_name):
                destination = self.client.get(reverse(url_name))
                self.assertEqual(destination.status_code, 200)


class DashboardPermissionSafeNavigationTests(TestCase):
    def test_roleless_user_is_not_offered_restricted_destinations(self):
        self.client.force_login(make_user(username="dashboard_roleless_links"))
        response = self.client.get(reverse("dashboard_snapshot"))
        self.assertEqual([row["key"] for row in response.context["nav_items"]], ["dashboard", "profile"])
        self.assertEqual(response.context["quick_actions"], [])

