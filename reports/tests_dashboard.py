from decimal import Decimal

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import (
    add_sales_line,
    make_cashbox,
    make_customer,
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    recalculate_invoice_totals,
    stock_in,
)
from permissions.models import RoleCode
from reports import selectors
from reports.dashboard_data import (
    DashboardFigures,
    build_alerts,
    has_any_business_data,
    health_band,
    health_score,
    onboarding_progress,
)
from reports.dashboard_kpis import (
    COUNT,
    CURRENCY,
    DASHBOARD_KPIS,
    LEVEL,
    SCOPE_ALL,
    SCOPE_OWN,
    SENSITIVE_KPI_KEYS,
    Kpi,
    visible_kpis,
)
from reports.dashboard_views import _format_value
from sales.models import SalesInvoice, SalesPaymentStatus
from sales.services import post_sales_invoice
from settings_core.models import ClientProfile
from settings_core.setup_services import complete_setup


DASHBOARD = "/dashboard/"
D = Decimal

# The per-role card sets from docs/dashboard_kpis.md, reconciled with the seeded
# matrix. Two deliberate departures, both because the matrix is what is enforced:
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


def user_with_role(role_code, username=None):
    user = make_user(username=username or f"dash_{role_code}")
    make_user_profile(user=user, role=make_seeded_role(role_code), display_name=str(role_code))
    return user


def sign_in_as(test, role_code, username=None):
    user = user_with_role(role_code, username)
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


def sell(item, location, cashbox, quantity, price, paid_now, creator, number="SI-T1", when=None):
    """Post one sale so the dashboard has something real to add up."""

    invoice = SalesInvoice.objects.create(
        invoice_number=number,
        invoice_date=when or timezone.localdate(),
        customer=make_customer(),
        selling_location=location,
        cashbox=cashbox,
        paid_now=D(paid_now),
        payment_status=SalesPaymentStatus.PARTIAL,
        created_by=creator,
    )
    add_sales_line(invoice, item, quantity, price)
    recalculate_invoice_totals(invoice, paid_now=D(paid_now))
    post_sales_invoice(invoice.id, user=creator)
    return invoice


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


class DashboardRealFigureTests(TestCase):
    """The numbers are computed, and the protected ones never reach the page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.item = make_item(min_stock=D("5"), average_cost=D("5.00"))
        cls.location = make_location()
        cls.cashbox = make_cashbox(opening_balance=D("1000.00"))
        cls.seller = user_with_role(RoleCode.OWNER, "figure_owner")
        stock_in(cls.item, cls.location, "20", unit_cost="5.00")
        sell(cls.item, cls.location, cls.cashbox, "4", "50.00", "120.00", cls.seller)

    def card(self, response, key):
        return next(c for c in response.context["cards"] if c["key"] == key)

    def test_sales_today_totals_the_posted_invoices(self):
        self.client.force_login(self.seller)
        response = self.client.get(DASHBOARD)

        self.assertEqual(self.card(response, "sales_today")["raw"], D("200.00"))
        self.assertEqual(self.card(response, "invoice_count_today")["raw"], 1)

    def test_profit_today_is_sales_minus_captured_cost(self):
        self.client.force_login(self.seller)
        response = self.client.get(DASHBOARD)

        # 4 units at 50 sold, cost captured at 5 each.
        self.assertEqual(self.card(response, "profit_today")["raw"], D("180.00"))

    def test_cashbox_balance_moves_by_the_amount_actually_paid(self):
        self.client.force_login(self.seller)
        response = self.client.get(DASHBOARD)

        self.assertEqual(self.card(response, "cashbox_balance")["raw"], D("1120.00"))

    def test_customer_dues_report_only_what_is_still_owed(self):
        self.client.force_login(self.seller)
        response = self.client.get(DASHBOARD)

        self.assertEqual(self.card(response, "customer_dues")["raw"], D("80.00"))

    def test_a_draft_invoice_is_left_out_of_the_money_figures(self):
        """sales_report includes drafts by design; a money card must not."""

        from hesba_testing.factories import make_draft_sales_invoice

        draft = make_draft_sales_invoice(
            invoice_number="SI-DRAFT", location=self.location, cashbox=self.cashbox
        )
        add_sales_line(draft, self.item, "3", "50.00")
        recalculate_invoice_totals(draft)

        self.client.force_login(self.seller)
        response = self.client.get(DASHBOARD)

        self.assertEqual(self.card(response, "sales_today")["raw"], D("200.00"))

    def test_a_sale_on_another_day_is_left_out_of_today(self):
        sell(
            self.item, self.location, self.cashbox, "2", "50.00", "0.00",
            self.seller, number="SI-OLD", when=timezone.localdate().replace(day=1),
        )

        self.client.force_login(self.seller)
        response = self.client.get(DASHBOARD)

        self.assertLessEqual(self.card(response, "sales_today")["raw"], D("200.00"))

    def test_the_profit_figure_never_reaches_a_cashier(self):
        """docs/permissions_map.md: menu hiding is not enough."""

        sign_in_as(self, RoleCode.CASHIER, "figure_cashier")
        body = self.client.get(DASHBOARD).content.decode()

        self.assertNotIn("180", body)
        self.assertNotIn("صافي الربح", body)

    def test_the_owner_does_see_the_profit_figure(self):
        self.client.force_login(self.seller)
        body = self.client.get(DASHBOARD).content.decode()

        self.assertIn("180", body)
        self.assertIn("صافي الربح", body)

    def test_a_zero_figure_is_marked_so_it_does_not_look_broken(self):
        sign_in_as(self, RoleCode.OWNER, "zero_owner")
        response = self.client.get(DASHBOARD)

        # Nothing was purchased today, so that card is a real zero.
        self.assertTrue(self.card(response, "purchases_today")["is_zero"])
        self.assertFalse(self.card(response, "sales_today")["is_zero"])


class DashboardScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.item = make_item()
        cls.location = make_location()
        cls.cashbox = make_cashbox()
        cls.owner = user_with_role(RoleCode.OWNER, "scope_owner")
        cls.cashier = user_with_role(RoleCode.CASHIER, "scope_cashier")
        stock_in(cls.item, cls.location, "40", unit_cost="5.00")
        sell(cls.item, cls.location, cls.cashbox, "10", "10.00", "0.00", cls.owner, number="SI-OWNER")
        sell(cls.item, cls.location, cls.cashbox, "3", "10.00", "0.00", cls.cashier, number="SI-CASHIER")

    def card(self, response, key):
        return next(c for c in response.context["cards"] if c["key"] == key)

    def test_a_cashier_sees_only_their_own_sales(self):
        self.client.force_login(self.cashier)
        response = self.client.get(DASHBOARD)

        self.assertEqual(self.card(response, "sales_today")["scope"], SCOPE_OWN)
        self.assertEqual(self.card(response, "sales_today")["raw"], D("30.00"))
        self.assertEqual(self.card(response, "invoice_count_today")["raw"], 1)

    def test_the_owner_sees_every_invoice(self):
        self.client.force_login(self.owner)
        response = self.client.get(DASHBOARD)

        self.assertEqual(self.card(response, "sales_today")["scope"], SCOPE_ALL)
        self.assertEqual(self.card(response, "sales_today")["raw"], D("130.00"))
        self.assertEqual(self.card(response, "invoice_count_today")["raw"], 2)

    def test_a_cashier_card_is_labelled_as_their_own(self):
        self.client.force_login(self.cashier)
        response = self.client.get(f"{DASHBOARD}?lang=en")

        self.assertContains(response, "My sales today")
        self.assertNotContains(response, ">Sales today<")

    def test_scope_is_all_for_a_card_with_no_scope_permission(self):
        kpi = next(k for k in DASHBOARD_KPIS if not k.scope_permission)
        self.assertEqual(kpi.scope_for(frozenset()), SCOPE_ALL)

    def test_visible_kpis_is_empty_without_permissions(self):
        self.assertEqual(visible_kpis(frozenset()), ())


class StockAlertSelectorTests(TestCase):
    def test_an_item_sold_out_counts_as_out_of_stock(self):
        item = make_item(min_stock=D("5"))
        location = make_location()
        cashbox = make_cashbox()
        owner = user_with_role(RoleCode.OWNER, "stock_owner")
        stock_in(item, location, "6", unit_cost="1.00")
        sell(item, location, cashbox, "6", "2.00", "0.00", owner)

        counts = selectors.stock_alert_counts()

        self.assertEqual(counts["out_of_stock"], 1)
        self.assertEqual(counts["low_stock"], 0)

    def test_an_item_at_or_below_its_minimum_counts_as_low(self):
        item = make_item(min_stock=D("10"))
        location = make_location()
        stock_in(item, location, "8", unit_cost="1.00")

        counts = selectors.stock_alert_counts()

        self.assertEqual(counts["low_stock"], 1)
        self.assertEqual(counts["out_of_stock"], 0)

    def test_an_item_never_stocked_is_out_of_stock_not_invisible(self):
        """stock_report drops zero rows; the shortage count must not."""

        make_item(min_stock=D("3"))
        make_location()

        self.assertEqual(selectors.stock_alert_counts()["out_of_stock"], 1)
        self.assertEqual(selectors.stock_report(), [])

    def test_an_item_above_its_minimum_is_neither(self):
        item = make_item(min_stock=D("2"))
        stock_in(item, make_location(), "20", unit_cost="1.00")

        self.assertEqual(selectors.stock_alert_counts(), {"low_stock": 0, "out_of_stock": 0})

    def test_untracked_items_are_ignored(self):
        make_item(item_code="SERVICE-1", is_stock_tracked=False, min_stock=D("5"))
        make_location()

        self.assertEqual(selectors.stock_alert_counts()["out_of_stock"], 0)


class HealthScoreTests(TestCase):
    ALL = frozenset(
        {
            "reports.view_inventory_report",
            "cashboxes.view_finance",
            "reports.view_customer_report",
            "reports.view_sales_report",
            "reports.view_profit_report",
        }
    )

    def test_a_quiet_day_with_no_problems_still_loses_the_no_sales_penalty(self):
        result = health_score(timezone.localdate(), self.ALL)

        self.assertIn("no_sales_today", result["reasons"])
        self.assertLess(result["score"], 100)

    def test_a_shortage_lowers_the_score(self):
        item = make_item(min_stock=D("5"))
        make_location()
        stock_in(item, make_location(), "0", unit_cost="1.00")

        result = health_score(timezone.localdate(), self.ALL)

        self.assertIn("out_of_stock", result["reasons"])

    def test_only_risks_the_viewer_may_see_are_priced_in(self):
        """A score built on figures the viewer cannot open reads as arbitrary."""

        make_item(min_stock=D("5"))
        make_location()

        held = frozenset(
            {
                "reports.view_inventory_report",
                "reports.view_customer_report",
                "reports.view_sales_report",
            }
        )
        partial = health_score(timezone.localdate(), held)

        self.assertIn("out_of_stock", partial["reasons"])
        self.assertNotIn("negative_cashbox", partial["reasons"])
        self.assertNotIn("loss_today", partial["reasons"])
        self.assertTrue(partial["available"])

    def test_it_is_unavailable_when_the_viewer_can_see_nothing(self):
        result = health_score(timezone.localdate(), frozenset())

        self.assertFalse(result["available"])
        self.assertEqual(result["reasons"], [])

    def test_one_visible_input_is_not_enough_to_score_a_business(self):
        """Otherwise a cashier reads 100% while stock is out."""

        make_item(min_stock=D("5"))
        make_location()

        single = health_score(timezone.localdate(), frozenset({"reports.view_sales_report"}))

        self.assertEqual(single["inputs_seen"], 1)
        self.assertFalse(single["available"])

    def test_the_score_never_leaves_its_range(self):
        for held in (frozenset(), self.ALL):
            with self.subTest(held=len(held)):
                score = health_score(timezone.localdate(), held)["score"]
                self.assertGreaterEqual(score, 0)
                self.assertLessEqual(score, 100)

    def test_bands_read_the_score_in_words(self):
        self.assertEqual(health_band(95)[0], "steady")
        self.assertEqual(health_band(60)[0], "watch")
        self.assertEqual(health_band(20)[0], "risk")

    def test_the_ring_is_hidden_from_a_viewer_with_no_inputs(self):
        user = make_user(username="ringless")
        self.client.force_login(user)

        response = self.client.get(DASHBOARD)

        self.assertFalse(response.context["show_health"])
        self.assertNotContains(response, "dash-health__ring")

    def test_a_stock_keeper_gets_no_ring_because_they_see_one_input(self):
        sign_in_as(self, RoleCode.STOCK_KEEPER, "ring_keeper")
        response = self.client.get(DASHBOARD)

        self.assertFalse(response.context["show_health"])

    def test_a_cashier_gets_no_ring_either(self):
        sign_in_as(self, RoleCode.CASHIER, "ring_cashier")

        self.assertFalse(self.client.get(DASHBOARD).context["show_health"])

    def test_a_manager_does_get_a_ring(self):
        sign_in_as(self, RoleCode.MANAGER, "ring_manager")
        response = self.client.get(DASHBOARD)

        self.assertTrue(response.context["show_health"])
        # A manager cannot see profit, so a loss must never move their score.
        self.assertNotIn("loss_today", response.context["health_reasons"])


class DashboardAlertTests(TestCase):
    def test_a_shortage_raises_an_alert_for_whoever_may_see_stock(self):
        make_item(min_stock=D("5"))
        make_location()

        sign_in_as(self, RoleCode.STOCK_KEEPER, "alert_keeper")
        keys = {a["key"] for a in self.client.get(DASHBOARD).context["alerts"]}

        self.assertIn("out_of_stock", keys)

    def test_a_cashier_gets_no_alerts_at_all(self):
        make_item(min_stock=D("5"))
        make_location()

        sign_in_as(self, RoleCode.CASHIER, "alert_cashier")

        self.assertEqual(self.client.get(DASHBOARD).context["alerts"], [])

    def test_a_customer_past_their_limit_raises_an_alert(self):
        item = make_item()
        location = make_location()
        cashbox = make_cashbox()
        owner = user_with_role(RoleCode.OWNER, "limit_owner")
        customer = make_customer(credit_limit=D("50.00"))
        stock_in(item, location, "20", unit_cost="1.00")

        invoice = SalesInvoice.objects.create(
            invoice_number="SI-LIMIT",
            invoice_date=timezone.localdate(),
            customer=customer,
            selling_location=location,
            cashbox=cashbox,
            paid_now=D("0.00"),
            payment_status=SalesPaymentStatus.CREDIT,
            created_by=owner,
        )
        add_sales_line(invoice, item, "10", "40.00")
        recalculate_invoice_totals(invoice, paid_now=D("0.00"))
        post_sales_invoice(invoice.id, user=owner)

        self.client.force_login(owner)
        keys = {a["key"] for a in self.client.get(DASHBOARD).context["alerts"]}

        self.assertIn(f"customer_over_limit_{customer.id}", keys)

    def test_urgent_alerts_come_first(self):
        item = make_item(min_stock=D("5"))
        make_location()
        stock_in(item, make_location(), "3", unit_cost="1.00")
        make_item(item_code="GONE-1", min_stock=D("2"))

        sign_in_as(self, RoleCode.OWNER, "order_owner")
        severities = [a["severity"] for a in self.client.get(DASHBOARD).context["alerts"]]

        self.assertEqual(severities, sorted(severities, key=lambda s: {"urgent": 0, "soon": 1, "watch": 2}[s]))

    def test_no_cheque_alerts_are_invented(self):
        """No cheque model exists; 120_DASHBOARD_CORE_PLAN forbids empty noise."""

        sign_in_as(self, RoleCode.OWNER, "cheque_owner")
        body = self.client.get(DASHBOARD).content.decode()

        self.assertNotIn("شيك", body)


class DashboardOnboardingTests(TestCase):
    def test_a_fresh_install_is_offered_the_four_steps(self):
        sign_in_as(self, RoleCode.OWNER, "fresh_owner")
        response = self.client.get(f"{DASHBOARD}?lang=en")

        self.assertTrue(response.context["show_onboarding"])
        self.assertFalse(response.context["has_business_data"])
        self.assertContains(response, "Start using Hesba in 4 steps")

    def test_master_data_alone_does_not_count_as_being_up_and_running(self):
        make_cashbox()
        make_customer()
        make_item()

        self.assertFalse(has_any_business_data())
        self.assertEqual(onboarding_progress(), [True, True, True, False])

    def test_the_steps_already_done_are_marked_off(self):
        make_cashbox()
        make_customer()

        sign_in_as(self, RoleCode.OWNER, "progress_owner")
        steps = self.client.get(DASHBOARD).context["onboarding_steps"]

        self.assertEqual([step["done"] for step in steps], [True, True, False, False])

    def test_a_trading_business_is_not_shown_the_steps(self):
        item = make_item()
        location = make_location()
        owner = user_with_role(RoleCode.OWNER, "trading_owner")
        stock_in(item, location, "10", unit_cost="1.00")
        sell(item, location, make_cashbox(), "2", "5.00", "5.00", owner)

        self.client.force_login(owner)
        response = self.client.get(DASHBOARD)

        self.assertTrue(response.context["has_business_data"])
        self.assertFalse(response.context["show_onboarding"])


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


class DashboardShellTests(TestCase):
    def setUp(self):
        super().setUp()
        sign_in_as(self, RoleCode.OWNER, "shell_owner")

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

    def test_the_date_follows_the_page_language(self):
        arabic = self.client.get(DASHBOARD).context["now_parts"]["date"]
        english = self.client.get(f"{DASHBOARD}?lang=en").context["now_parts"]["date"]

        self.assertNotEqual(arabic, english)

    def test_it_greets_the_signed_in_person(self):
        response = self.client.get(DASHBOARD)

        self.assertIn(
            response.context["greeting"],
            ["صباح الخير", "نهارك سعيد", "مساء الخير", "أهلًا"],
        )

    def test_quick_actions_only_navigate(self):
        """business_rules.md keeps dashboards read-only."""

        response = self.client.get(DASHBOARD)
        body = response.content.decode()
        actions = body[body.index("dash-actions") :].split("</section>")[0]

        self.assertNotIn("<form", actions)
        self.assertContains(response, 'data-action="close_day"')

    def test_closing_the_day_is_the_primary_action(self):
        response = self.client.get(DASHBOARD)
        primary = [a for a in response.context["quick_actions"] if a["primary"]]

        self.assertEqual([a["key"] for a in primary], ["close_day"])

    def test_it_does_not_offer_a_cashbox_count_as_the_main_action(self):
        self.assertNotContains(self.client.get(DASHBOARD), "جرد خزنة")

    def test_the_dashboard_excludes_module_settings_controls(self):
        """docs/120A lists these as explicit exclusions."""

        response = self.client.get(DASHBOARD)

        self.assertNotContains(response, "إعدادات الموديولات")
        self.assertNotContains(response, "module-toggle")

    def test_no_placeholder_notice_remains(self):
        self.assertNotContains(self.client.get(DASHBOARD), "تجريبية للمراجعة البصرية")


class DashboardModuleAwarenessTests(TestCase):
    def setUp(self):
        super().setUp()
        sign_in_as(self, RoleCode.OWNER, "module_owner")

    def test_navigation_follows_the_enabled_modules(self):
        prepared_client(modules="customers,items_services,cashboxes,reports,sales_operations")

        keys = {i["key"] for i in self.client.get(DASHBOARD).context["nav_items"]}

        self.assertIn("customers", keys)
        self.assertNotIn("suppliers", keys)

    def test_a_services_install_can_drop_the_supplier_shortcut(self):
        prepared_client(
            activity="services", sub_activity="clinic",
            modules="customers,items_services,cashboxes,reports",
        )

        keys = {a["key"] for a in self.client.get(DASHBOARD).context["quick_actions"]}

        self.assertNotIn("new_supplier", keys)
        self.assertIn("new_customer", keys)

    def test_a_module_with_no_backend_never_reaches_the_navigation(self):
        prepared_client(modules="customers,expenses,appointments_visits,cashboxes,reports,items_services")

        keys = {i["key"] for i in self.client.get(DASHBOARD).context["nav_items"]}

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
        sign_in_as(self, RoleCode.OWNER, "route_owner")
        response = self.client.get(DASHBOARD)

        self.assertNotContains(response, "094_FOUNDATION_DASHBOARD_SNAPSHOT")
        self.assertNotIn("reports/home.html", [t.name for t in response.templates])


@override_settings(DEBUG=True)
class SeedDemoBusinessTests(TestCase):
    """The seed exists so a local dashboard has something real to show."""

    def seed(self, **kwargs):
        options = {"verbosity": 0}
        options.update(kwargs)
        call_command("seed_demo_business", **options)

    def setUp(self):
        super().setUp()
        self.owner = make_user(username="seed_owner", is_superuser=True)
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))

    def test_it_posts_real_trade_rather_than_writing_figures(self):
        self.seed()

        self.assertTrue(SalesInvoice.objects.filter(invoice_number__startswith="DEMO-SI-").exists())
        # Money on the dashboard comes from movements, so posting is what matters.
        self.assertGreater(selectors.profit_totals()["sales"], 0)
        self.assertNotEqual(selectors.cashbox_report(), [])

    def test_it_leaves_the_dashboard_with_figures_to_show(self):
        self.seed()
        self.client.force_login(self.owner)

        cards = {c["key"]: c for c in self.client.get(DASHBOARD).context["cards"]}

        self.assertGreater(cards["sales_today"]["raw"], 0)
        self.assertGreater(cards["customer_dues"]["raw"], 0)
        self.assertGreater(cards["supplier_dues"]["raw"], 0)

    def test_it_plants_shortages_so_the_alerts_have_real_causes(self):
        self.seed()

        counts = selectors.stock_alert_counts()

        self.assertGreater(counts["out_of_stock"], 0)
        self.assertGreater(counts["low_stock"], 0)

    def test_it_plants_a_customer_over_their_limit(self):
        self.seed()
        self.client.force_login(self.owner)

        keys = {a["key"] for a in self.client.get(DASHBOARD).context["alerts"]}

        self.assertTrue(any(k.startswith("customer_over_limit_") for k in keys))

    def test_the_health_score_reflects_the_planted_problems(self):
        self.seed()

        result = health_score(timezone.localdate(), HealthScoreTests.ALL)

        self.assertLess(result["score"], 100)
        self.assertIn("out_of_stock", result["reasons"])

    def test_running_it_twice_does_not_double_the_business(self):
        self.seed()
        before = SalesInvoice.objects.count()
        self.seed()

        self.assertEqual(SalesInvoice.objects.count(), before)

    def test_a_cashier_sees_their_own_share_of_it(self):
        cashier = user_with_role(RoleCode.CASHIER, "cashier")
        self.seed()
        self.client.force_login(cashier)

        cards = {c["key"]: c for c in self.client.get(DASHBOARD).context["cards"]}

        self.assertEqual(cards["sales_today"]["scope"], SCOPE_OWN)
        self.assertGreater(cards["sales_today"]["raw"], 0)

    def test_the_cashiers_share_is_smaller_than_the_whole_day(self):
        cashier = user_with_role(RoleCode.CASHIER, "cashier")
        self.seed()

        self.client.force_login(cashier)
        own = next(
            c for c in self.client.get(DASHBOARD).context["cards"] if c["key"] == "sales_today"
        )["raw"]

        self.client.force_login(self.owner)
        everything = next(
            c for c in self.client.get(DASHBOARD).context["cards"] if c["key"] == "sales_today"
        )["raw"]

        self.assertLess(own, everything)

    @override_settings(DEBUG=False)
    def test_it_refuses_to_post_demo_trade_outside_debug(self):
        from django.core.management.base import CommandError

        with self.assertRaisesMessage(CommandError, "Refusing to write demo transactions"):
            self.seed()

        self.assertFalse(SalesInvoice.objects.exists())

    def test_it_tops_up_today_so_the_demo_does_not_go_flat(self):
        """Seeded once, the dashboard has to still work tomorrow.

        Without this the balances survive but every "today" card reads zero the
        next morning, which is the dead-looking dashboard the seed exists to
        avoid.
        """

        from datetime import timedelta
        from unittest import mock

        from django.utils import timezone as tz

        self.seed()
        self.client.force_login(self.owner)
        first_day = next(
            c for c in self.client.get(DASHBOARD).context["cards"] if c["key"] == "sales_today"
        )["raw"]

        tomorrow = tz.localdate() + timedelta(days=1)
        with mock.patch("django.utils.timezone.localdate", lambda *a, **k: tomorrow):
            self.seed()
            figures = DashboardFigures(self.owner, tomorrow)
            self.assertEqual(figures.value_for("sales_today", SCOPE_ALL), first_day)

    def test_the_shortages_survive_the_daily_top_up(self):
        from datetime import timedelta
        from unittest import mock

        from django.utils import timezone as tz

        self.seed()
        before = selectors.stock_alert_counts()

        # Resolve the date before patching, or the replacement calls itself.
        later = tz.localdate() + timedelta(days=3)
        with mock.patch("django.utils.timezone.localdate", lambda *a, **k: later):
            self.seed()

        self.assertEqual(selectors.stock_alert_counts(), before)
        self.assertGreater(before["out_of_stock"], 0)

    def test_a_same_day_rerun_adds_nothing(self):
        self.seed()
        before = SalesInvoice.objects.count()
        self.seed()

        self.assertEqual(SalesInvoice.objects.count(), before)


class _StubShared:
    """Stands in for SharedReads so alert formatting can be asserted directly.

    build_alerts only ever asks these four questions, and driving it with fixed
    answers keeps the assertions about the rendered string rather than about
    how a balance came to exist.
    """

    def __init__(self, credit_limits=None, over_limit=None, cashboxes=None, stock=None):
        self._credit_limits = credit_limits or {}
        self._over_limit = over_limit or []
        self._cashboxes = cashboxes or []
        self._stock = stock or {"low_stock": 0, "out_of_stock": 0}

    def credit_limits(self):
        return self._credit_limits

    def customers_over_limit(self):
        return self._over_limit

    def cashboxes(self):
        return self._cashboxes

    def stock_alerts(self):
        return self._stock


class DashboardMoneyPrecisionTests(SimpleTestCase):
    """Money on the dashboard must match what is stored, to the piastre.

    The screen used to render every currency figure with zero decimals, so a
    stored 1234.56 appeared as "1,235" — more than the business actually had,
    with nothing marking it as rounded.
    """

    def _money_kpi(self):
        return Kpi(key="sales_today", label_ar="مبيعات", label_en="Sales", permission="p", unit=CURRENCY)

    def _count_kpi(self):
        return Kpi(key="invoice_count_today", label_ar="عدد", label_en="Count", permission="p", unit=COUNT)

    def test_money_kpi_keeps_its_fractional_part(self):
        self.assertEqual(_format_value(self._money_kpi(), D("1234.56"), "en"), "1,234.56")

    def test_money_kpi_shows_two_decimals_on_a_whole_amount(self):
        self.assertEqual(_format_value(self._money_kpi(), D("1234.00"), "en"), "1,234.00")

    def test_money_kpi_never_rounds_to_whole_units(self):
        """The exact defect: 1234.56 must not be shown as more than it is."""

        rendered = _format_value(self._money_kpi(), D("1234.56"), "en")
        self.assertNotEqual(rendered, "1,235")
        self.assertIn(".56", rendered)

    def test_money_kpi_ignores_the_incoming_exponent(self):
        wide = _format_value(self._money_kpi(), D("5260.0000"), "en")
        narrow = _format_value(self._money_kpi(), D("5260"), "en")
        self.assertEqual(wide, narrow)
        self.assertEqual(wide, "5,260.00")

    def test_count_kpi_stays_a_plain_integer(self):
        rendered = _format_value(self._count_kpi(), 1234, "en")
        self.assertEqual(rendered, "1,234")
        self.assertNotIn(".", rendered)

    def test_level_kpi_is_unchanged(self):
        kpi = Kpi(key="usage_status", label_ar="الحالة", label_en="Status", permission="p", unit=LEVEL)
        self.assertEqual(_format_value(kpi, "green", "en"), "Normal")
        self.assertEqual(_format_value(kpi, "green", "ar"), "طبيعي")

    def test_missing_money_renders_an_em_dash_and_does_not_raise(self):
        self.assertEqual(_format_value(self._money_kpi(), None, "en"), "—")


class DashboardAlertMoneyTests(SimpleTestCase):
    """Alert figures, including the credit limit quoted inside the sentence."""

    def _customer_alert(self, balance, limit):
        shared = _StubShared(
            credit_limits={7: limit},
            over_limit=[{"customer_id": 7, "customer_name": "عميل", "balance": balance}],
        )
        alerts = build_alerts({"reports.view_customer_report"}, timezone.localdate(), shared=shared)
        self.assertEqual(len(alerts), 1)
        return alerts[0]

    def test_customer_alert_amount_keeps_two_decimals(self):
        alert = self._customer_alert(D("8750.25"), D("5000.00"))
        self.assertEqual(alert["amount"], "8,750.25")

    def test_quoted_credit_limit_is_exact_in_both_languages(self):
        """A rounded threshold can claim a line was crossed that was not."""

        alert = self._customer_alert(D("13000.00"), D("12390.50"))
        self.assertIn("12,390.50", alert["detail_ar"])
        self.assertIn("12,390.50", alert["detail_en"])
        self.assertNotIn("12,391", alert["detail_ar"])
        self.assertNotIn("12,391", alert["detail_en"])

    def test_quoted_credit_limit_keeps_the_surrounding_wording(self):
        alert = self._customer_alert(D("13000.00"), D("12390.50"))
        self.assertEqual(alert["detail_ar"], "الحد المسموح 12,390.50.")
        self.assertEqual(alert["detail_en"], "Limit is 12,390.50.")

    def test_negative_cashbox_alert_amount_keeps_two_decimals(self):
        shared = _StubShared(
            cashboxes=[{"cashbox_id": 3, "cashbox_name": "الخزنة", "balance": D("-120.75")}]
        )
        alerts = build_alerts({"cashboxes.view_finance"}, timezone.localdate(), shared=shared)

        self.assertEqual(alerts[0]["key"], "cashbox_negative_3")
        self.assertEqual(alerts[0]["amount"], "-120.75")

    def test_low_cashbox_alert_amount_keeps_two_decimals(self):
        shared = _StubShared(
            cashboxes=[{"cashbox_id": 4, "cashbox_name": "الخزنة", "balance": D("120.25")}]
        )
        alerts = build_alerts({"cashboxes.view_finance"}, timezone.localdate(), shared=shared)

        self.assertEqual(alerts[0]["key"], "cashbox_low_4")
        self.assertEqual(alerts[0]["amount"], "120.25")

    def test_stock_alerts_still_carry_no_amount(self):
        """An empty amount must stay empty, so the template hides the badge."""

        shared = _StubShared(stock={"low_stock": 2, "out_of_stock": 1})
        alerts = build_alerts({"reports.view_inventory_report"}, timezone.localdate(), shared=shared)

        self.assertEqual(len(alerts), 2)
        for alert in alerts:
            with self.subTest(key=alert["key"]):
                self.assertEqual(alert["amount"], "")
