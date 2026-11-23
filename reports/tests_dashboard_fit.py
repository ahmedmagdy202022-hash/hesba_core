"""DASH-FIT: the dashboard follows the activity, the modules switched on and the options chosen in setup."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_item, make_location, stock_in
from permissions.models import RoleCode
from settings_core import capabilities
from settings_core.models import ClientProfile

from . import dashboard_fit
from .dashboard_kpis import DASHBOARD_KPIS
from .tests_analytics import TODAY, person
from .tests_dashboard import prepared_client, sell

EVERY_MODULE = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports"


class FitSetup(TestCase):
    activity, sub, modules = "commercial", "retail", EVERY_MODULE

    def setUp(self):
        prepared_client(self.activity, self.sub, self.modules)
        Period.objects.create(period_code="DF", name="df", start_date=TODAY - timedelta(days=120), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "df_owner")
        self.location, self.cashbox, self.item = make_location(), make_cashbox(cashbox_code="DF-CASH"), make_item(item_code="DF-1")
        stock_in(self.item, self.location, 100, "6.00", movement_date=TODAY - timedelta(days=60))
        self.client.force_login(self.owner)

    def page(self):
        return self.client.get(reverse("dashboard_snapshot"))

    def switch(self, slug, on=True):
        capabilities.set_capability_enabled(ClientProfile.get_active(), slug, on)


class RuleTableTests(TestCase):
    def test_every_rule_names_real_modules_cards_and_metrics(self):
        from settings_core.setup_catalog import MODULE_SLUGS

        from .analytics import METRIC_WORDS

        cards = {kpi.key for kpi in DASHBOARD_KPIS}
        metrics = set(METRIC_WORDS["ar"])
        for key, rule in dashboard_fit.NEEDS.items():
            self.assertTrue(rule.modules <= set(MODULE_SLUGS), key)
            kind, name = key.split(".", 1)
            if kind == "kpi":
                self.assertIn(name, cards)
            elif kind == "metric":
                self.assertIn(name, metrics)
            for fact in (rule.fact, rule.or_fact):
                if fact:
                    self.assertTrue(hasattr(dashboard_fit.Fit, f"_fact_{fact}"), key)


class CreditSplitTests(FitSetup):
    def test_a_cash_only_shop_has_no_paid_or_credit_chart_until_it_sells_on_credit(self):
        sell(self.item, self.location, self.cashbox, 2, "10.00", "20.00", self.owner, number="DF-S1", when=TODAY)  # paid in full
        self.assertNotContains(self.page(), "data-split")
        sell(self.item, self.location, self.cashbox, 2, "10.00", "5.00", self.owner, number="DF-S2", when=TODAY)  # 15 left on credit
        self.assertContains(self.page(), "data-split")

    def test_instalments_switched_on_shows_the_split_and_its_own_tile(self):
        sell(self.item, self.location, self.cashbox, 2, "10.00", "20.00", self.owner, number="DF-S1", when=TODAY)
        self.switch("installments")
        page = self.page()
        self.assertContains(page, "data-split")
        self.assertContains(page, 'data-tile="opt_instalments"')
        self.assertContains(page, "مفيش أقساط متأخرة")


class ModuleTests(FitSetup):
    modules = "customers,items_services,sales_operations,cashboxes,reports"  # no purchases, suppliers, inventory or expenses

    def test_switched_off_modules_leave_no_card_metric_list_or_alert(self):
        sell(self.item, self.location, self.cashbox, 2, "10.00", "20.00", self.owner, number="DF-S1", when=TODAY)
        page = self.page()
        context = page.context
        kpis = {card["key"] for card in context["cards"]}
        self.assertFalse(kpis & {"purchases_today", "supplier_dues", "supplier_payments_today", "low_stock_count", "out_of_stock_count"})
        self.assertIn("customer_dues", kpis)
        metrics = {metric["key"] for metric in context["analytics"]["metrics"]}
        self.assertFalse(metrics & {"payables", "expenses", "net_profit"})
        self.assertIsNone(context["analytics"]["stock"])
        self.assertFalse([alert for alert in context["alerts"] if alert["key"] in ("out_of_stock", "low_stock")])


class OwedMoneyTests(FitSetup):
    """TRACK-ACTS W15: a restaurant without the customers module still bills company lunches on credit."""

    activity, sub, modules = "restaurants", "restaurant", "items_services,sales_operations,cashboxes,reports"

    def test_what_customers_owe_shows_once_anything_is_sold_on_credit(self):
        sell(self.item, self.location, self.cashbox, 2, "10.00", "20.00", self.owner, number="DF-R1", when=TODAY)  # paid in full
        context = self.page().context
        self.assertNotIn("customer_dues", {card["key"] for card in context["cards"]})
        self.assertNotIn("receivables", {metric["key"] for metric in context["analytics"]["metrics"]})
        sell(self.item, self.location, self.cashbox, 2, "10.00", "5.00", self.owner, number="DF-R2", when=TODAY)  # 15.00 owed
        context = self.page().context
        self.assertIn("customer_dues", {card["key"] for card in context["cards"]})
        self.assertIn("receivables", {metric["key"] for metric in context["analytics"]["metrics"]})


class ActivityTests(FitSetup):
    activity, sub = "contracting", "general"
    modules = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,projects"

    def test_a_contractor_has_no_sales_by_hour_or_weekday(self):
        sell(self.item, self.location, self.cashbox, 1, "1000.00", "1000.00", self.owner, number="DF-S1", when=TODAY)
        page = self.page()
        self.assertNotContains(page, 'data-tab="hours"')
        self.assertNotContains(page, 'data-tab="weekdays"')
        self.assertContains(page, 'data-tab="sales"')

    def test_a_shop_keeps_them(self):
        fit = dashboard_fit.Fit(activity="commercial", modules=frozenset(), capabilities=frozenset(), today=TODAY)
        self.assertTrue(fit.shows("chart.hours") and fit.shows("chart.weekdays"))


class OptionTileTests(FitSetup):
    def test_each_option_switched_on_brings_its_tile_and_off_takes_it_away(self):
        for slug in capabilities.enabled_capabilities():
            self.switch(slug, False)
        self.assertNotContains(self.page(), "data-option-panel")
        for slug in ("batches_expiry", "vat", "fixed_assets", "serials"):
            self.switch(slug)
        page = self.page()
        for key in ("opt_batches_expired", "opt_batches_soon", "opt_vat", "opt_assets", "opt_serials"):
            self.assertContains(page, f'data-tile="{key}"')
        self.switch("vat", False)
        self.assertNotContains(self.page(), 'data-tile="opt_vat"')

    def test_a_viewer_without_the_screen_permission_gets_no_tile(self):
        self.switch("fixed_assets")
        keeper = person(RoleCode.STOCK_KEEPER, "df_keeper")
        tiles = {tile["key"] for tile in dashboard_fit.option_tiles(dashboard_fit.Fit.current(TODAY), keeper)}
        self.assertNotIn("opt_assets", tiles)
