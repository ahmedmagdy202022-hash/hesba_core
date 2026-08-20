from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.dashboard_kpis import (
    DASHBOARD_KPIS,
    SCOPE_ALL,
    SCOPE_OWN,
    SENSITIVE_KPI_KEYS,
    visible_kpis,
)
from reports.dashboard_views import MOCK_VALUES
from settings_core.models import ClientProfile
from settings_core.setup_services import complete_setup


DASHBOARD = "/dashboard/"

# What docs/dashboard_kpis.md asks each role to see, reconciled with the seeded
# matrix. Two deliberate departures from that document, both because the matrix
# is what is actually enforced:
#   - a manager gets no supplier_dues card; reports.view_supplier_report is
#     seeded as sensitive finance and withheld from managers
#   - a manager and an accountant see receipts_today, which follows from holding
#     sales.receive_customer_payment
EXPECTED_KPIS = {
    RoleCode.OWNER: {
        "sales_today", "invoice_count_today", "purchases_today", "profit_today",
        "cashbox_balance", "customer_dues", "supplier_dues", "receipts_today",
        "supplier_payments_today", "low_stock_count", "out_of_stock_count", "usage_status",
    },
    RoleCode.MANAGER: {
        "sales_today", "invoice_count_today", "purchases_today", "customer_dues",
        "receipts_today", "low_stock_count", "out_of_stock_count", "usage_status",
    },
    RoleCode.CASHIER: {"sales_today", "invoice_count_today", "receipts_today"},
    RoleCode.STOCK_KEEPER: {"low_stock_count", "out_of_stock_count"},
    RoleCode.ACCOUNTANT: {
        "sales_today", "invoice_count_today", "purchases_today", "cashbox_balance",
        "customer_dues", "supplier_dues", "receipts_today", "supplier_payments_today",
    },
}


def sign_in_as(test, role_code):
    user = make_user(username=f"dash_{role_code}")
    make_user_profile(user=user, role=make_seeded_role(role_code), display_name=str(role_code))
    test.client.force_login(user)
    return user


def prepared_client(activity="commercial", sub_activity="retail", modules=None):
    profile = ClientProfile.objects.create(
        client_code="DEMO", legal_name="Demo Legal", display_name="Demo Store"
    )
    if modules is None:
        modules = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports"
    complete_setup(profile, activity, sub_activity, modules)
    return profile


class DashboardRoleVisibilityTests(TestCase):
    """The documented per-role card sets, produced by the permission matrix."""

    def test_each_role_sees_its_documented_cards(self):
        for role_code, expected in EXPECTED_KPIS.items():
            with self.subTest(role=role_code):
                sign_in_as(self, role_code)
                response = self.client.get(DASHBOARD)

                self.assertEqual(response.status_code, 200)
                shown = {card["key"] for card in response.context["cards"]}
                self.assertEqual(shown, expected)

    def test_only_the_owner_sees_profit(self):
        for role_code in EXPECTED_KPIS:
            with self.subTest(role=role_code):
                sign_in_as(self, role_code)
                shown = {c["key"] for c in self.client.get(DASHBOARD).context["cards"]}
                self.assertEqual("profit_today" in shown, role_code == RoleCode.OWNER)

    def test_a_cashier_never_sees_a_sensitive_figure(self):
        sign_in_as(self, RoleCode.CASHIER)
        shown = {card["key"] for card in self.client.get(DASHBOARD).context["cards"]}

        self.assertEqual(shown & SENSITIVE_KPI_KEYS, set())

    def test_a_stock_keeper_never_sees_a_sensitive_figure(self):
        sign_in_as(self, RoleCode.STOCK_KEEPER)
        shown = {card["key"] for card in self.client.get(DASHBOARD).context["cards"]}

        self.assertEqual(shown & SENSITIVE_KPI_KEYS, set())

    def test_the_profit_figure_is_absent_from_the_page_not_just_hidden(self):
        """Menu hiding is not enough — the number must never reach the response."""

        sign_in_as(self, RoleCode.CASHIER)
        body = self.client.get(DASHBOARD).content.decode()

        self.assertNotIn(MOCK_VALUES["profit_today"], body)
        self.assertNotIn(MOCK_VALUES["cashbox_balance"], body)
        self.assertNotIn("صافي الربح", body)

    def test_the_owner_does_see_the_profit_figure(self):
        sign_in_as(self, RoleCode.OWNER)
        body = self.client.get(DASHBOARD).content.decode()

        self.assertIn(MOCK_VALUES["profit_today"], body)
        self.assertIn("صافي الربح", body)


class DashboardScopeTests(TestCase):
    def test_a_cashier_sees_only_their_own_sales(self):
        sign_in_as(self, RoleCode.CASHIER)
        cards = {c["key"]: c for c in self.client.get(DASHBOARD).context["cards"]}

        self.assertEqual(cards["sales_today"]["scope"], SCOPE_OWN)
        self.assertEqual(cards["invoice_count_today"]["scope"], SCOPE_OWN)

    def test_a_cashier_card_is_labelled_as_their_own(self):
        sign_in_as(self, RoleCode.CASHIER)
        response = self.client.get(f"{DASHBOARD}?lang=en")

        self.assertContains(response, "My sales today")
        self.assertNotContains(response, ">Sales today<")

    def test_a_manager_sees_the_whole_business(self):
        sign_in_as(self, RoleCode.MANAGER)
        cards = {c["key"]: c for c in self.client.get(DASHBOARD).context["cards"]}

        self.assertEqual(cards["sales_today"]["scope"], SCOPE_ALL)

    def test_scope_is_all_for_a_card_with_no_scope_permission(self):
        kpi = next(k for k in DASHBOARD_KPIS if not k.scope_permission)
        self.assertEqual(kpi.scope_for(frozenset()), SCOPE_ALL)

    def test_visible_kpis_is_empty_without_permissions(self):
        self.assertEqual(visible_kpis(frozenset()), ())


class DashboardPermissionlessUserTests(TestCase):
    """A user holding nothing still gets a page, not a wall."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_user(username="roleless_viewer"))

    def test_the_page_still_renders(self):
        self.assertEqual(self.client.get(DASHBOARD).status_code, 200)

    def test_it_says_there_is_nothing_to_show(self):
        response = self.client.get(f"{DASHBOARD}?lang=en")

        self.assertEqual(response.context["cards"], [])
        self.assertContains(response, "No figures are available for your permissions")

    def test_it_offers_the_onboarding_steps(self):
        response = self.client.get(f"{DASHBOARD}?lang=en")

        self.assertTrue(response.context["show_onboarding"])
        self.assertContains(response, "Start using Hesba in 4 steps")


class DashboardShellTests(TestCase):
    def setUp(self):
        super().setUp()
        sign_in_as(self, RoleCode.OWNER)

    def test_it_extends_the_shared_shell(self):
        response = self.client.get(DASHBOARD)
        self.assertIn("base_app.html", [t.name for t in response.templates])

    def test_arabic_is_right_to_left_and_stamped_on_the_body(self):
        response = self.client.get(DASHBOARD)

        self.assertContains(response, 'dir="rtl"')
        # Stylesheets key off body[data-lang], so direction alone is not enough.
        self.assertContains(response, 'data-lang="ar"')

    def test_english_is_left_to_right_and_stamped_on_the_body(self):
        response = self.client.get(f"{DASHBOARD}?lang=en")

        self.assertContains(response, 'dir="ltr"')
        self.assertContains(response, 'data-lang="en"')

    def test_it_greets_the_signed_in_person_by_name(self):
        response = self.client.get(DASHBOARD)

        self.assertContains(response, "owner")
        self.assertIn(response.context["greeting"], ["صباح الخير", "نهارك سعيد", "مساء الخير", "أهلًا"])

    def test_it_carries_the_business_health_score(self):
        response = self.client.get(DASHBOARD)

        self.assertEqual(response.context["health_score"], 82)
        self.assertContains(response, "مؤشر النشاط")

    def test_it_says_the_figures_are_placeholders(self):
        response = self.client.get(DASHBOARD)

        self.assertTrue(response.context["is_mock"])
        self.assertContains(response, "تجريبية")

    def test_quick_actions_only_navigate(self):
        """business_rules.md keeps dashboards read-only."""

        response = self.client.get(DASHBOARD)
        body = response.content.decode()
        actions = body[body.index("dash-actions") :]

        self.assertNotIn("<form", actions.split("</section>")[0])
        self.assertContains(response, 'data-action="close_day"')

    def test_closing_the_day_is_the_primary_action(self):
        response = self.client.get(DASHBOARD)
        primary = [a for a in response.context["quick_actions"] if a["primary"]]

        self.assertEqual([a["key"] for a in primary], ["close_day"])

    def test_it_does_not_offer_a_cashbox_count_as_the_main_action(self):
        response = self.client.get(DASHBOARD)
        self.assertNotContains(response, "جرد خزنة")

    def test_alerts_carry_a_severity(self):
        response = self.client.get(DASHBOARD)

        severities = {alert["severity"] for alert in response.context["alerts"]}
        self.assertTrue(severities <= {"urgent", "soon", "watch"})
        self.assertContains(response, "محتاج انتباهك")

    def test_the_dashboard_excludes_module_settings_controls(self):
        """docs/120A lists these as explicit exclusions."""

        response = self.client.get(DASHBOARD)

        self.assertNotContains(response, "إعدادات الموديولات")
        self.assertNotContains(response, "module-toggle")


class DashboardModuleAwarenessTests(TestCase):
    def setUp(self):
        super().setUp()
        sign_in_as(self, RoleCode.OWNER)

    def test_navigation_follows_the_enabled_modules(self):
        prepared_client(modules="customers,items_services,cashboxes,reports,sales_operations")

        response = self.client.get(DASHBOARD)
        keys = {item["key"] for item in response.context["nav_items"]}

        self.assertIn("customers", keys)
        self.assertNotIn("suppliers", keys)

    def test_a_services_install_can_drop_the_inventory_shortcut(self):
        prepared_client(
            activity="services", sub_activity="clinic",
            modules="customers,items_services,cashboxes,reports",
        )

        response = self.client.get(DASHBOARD)
        action_keys = {action["key"] for action in response.context["quick_actions"]}

        self.assertNotIn("new_supplier", action_keys)
        self.assertIn("new_customer", action_keys)

    def test_a_module_with_no_backend_never_reaches_the_navigation(self):
        prepared_client(modules="customers,expenses,appointments_visits,cashboxes,reports,items_services")

        response = self.client.get(DASHBOARD)
        keys = {item["key"] for item in response.context["nav_items"]}

        self.assertNotIn("expenses", keys)
        self.assertNotIn("appointments_visits", keys)

    def test_it_names_the_client_once_setup_has_run(self):
        prepared_client()

        response = self.client.get(DASHBOARD)

        self.assertEqual(response.context["client_name"], "Demo Store")
        self.assertEqual(response.context["activity_slug"], "commercial")

    def test_it_renders_before_any_client_profile_exists(self):
        response = self.client.get(DASHBOARD)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["client_name"], "")


class DashboardRouteTests(TestCase):
    def test_it_still_answers_to_the_dashboard_route_name(self):
        self.assertEqual(reverse("dashboard_snapshot"), DASHBOARD)

    def test_it_is_no_longer_the_static_navigation_map(self):
        sign_in_as(self, RoleCode.OWNER)
        response = self.client.get(DASHBOARD)

        self.assertNotContains(response, "094_FOUNDATION_DASHBOARD_SNAPSHOT")
        self.assertNotIn("reports/home.html", [t.name for t in response.templates])
