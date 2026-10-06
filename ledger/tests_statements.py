"""FS-001: the three statements, on the month the reports already pin."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from reports.tests_month_acceptance import OneTradingMonthTests

from . import statements
from .projector import Projector


class StatementsOnTradingMonthTests(OneTradingMonthTests):
    test_cash_in_the_till = test_stock_on_the_shelf = test_who_owes_whom = None
    test_profit_for_the_month = test_month_end_close_freezes_the_same_figures = None

    def setUp(self):
        Projector().rebuild()

    def test_income_statement_matches_the_profit_report(self):
        income = statements.income_statement(self.start, self.end)
        self.assertEqual(income["sections"]["revenue"]["total"], D("4150.00"))
        self.assertEqual(income["gross_profit"], D("1432.07"))
        self.assertEqual(income["sections"]["operating_expenses"]["total"], D("1250.00"))
        self.assertEqual(income["net_profit"], D("182.07"))
        # Nothing traded the month before: the comparison is empty, not wrong.
        self.assertEqual(income["comparisons"]["previous"]["net_profit"], D("0.00"))
        self.assertEqual(income["comparisons"]["last_year"]["sections"]["revenue"]["total"], D("0.00"))

    def test_balance_sheet_balances_and_carries_the_profit(self):
        sheet = statements.balance_sheet(self.end)
        self.assertTrue(sheet["balanced"])
        self.assertEqual(sheet["total_assets"], sheet["total_liabilities_and_equity"])
        self.assertEqual(sheet["unclosed_earnings"], D("182.07"))
        # A day before trading began, the books are empty and still balance.
        before = statements.balance_sheet(self.start - timedelta(days=1))
        self.assertTrue(before["balanced"])

    def test_cash_flow_reconciles_to_the_cashboxes(self):
        flow = statements.cash_flow(self.start, self.end)
        self.assertTrue(flow["reconciles"])
        self.assertEqual(flow["net_profit"], D("182.07"))
        self.assertEqual(flow["cash_opening"] + flow["net_change"], flow["cash_closing"])
        from .reports import control_balance

        self.assertEqual(flow["cash_closing"], control_balance("cash", self.end) + control_balance("bank", self.end))
        # Mid-month window still reconciles.
        mid = self.start.replace(day=12)
        self.assertTrue(statements.cash_flow(mid, self.end)["reconciles"])

    def test_screens_and_export(self):
        from reports.tests_dashboard import prepared_client

        prepared_client()
        self.client.force_login(self.owner)
        window = f"?from={self.start:%Y-%m-%d}&to={self.end:%Y-%m-%d}"
        income = self.client.get(reverse("ledger:income_statement") + window)
        self.assertContains(income, 'data-net-profit>182.07<')
        self.assertContains(income, "مجمل الربح")
        sheet = self.client.get(reverse("ledger:balance_sheet") + f"?to={self.end:%Y-%m-%d}")
        self.assertContains(sheet, 'data-balanced="1"')
        flow = self.client.get(reverse("ledger:cash_flow") + window + "&lang=en")
        self.assertContains(flow, 'data-reconciles="1"')
        self.assertContains(flow, "Operating activities")
        csv = self.client.get(reverse("ledger:income_statement") + window + "&format=csv")
        self.assertEqual(csv["Content-Type"], "text/csv; charset=utf-8")
        body = csv.content.decode("utf-8-sig")
        self.assertIn("182.07", body)
        self.assertIn("attachment;", csv["Content-Disposition"])


class StatementRulesTests(TestCase):
    def test_comparison_windows(self):
        from datetime import date

        windows = statements.comparison_windows(date(2028, 3, 1), date(2028, 3, 31))
        self.assertEqual(windows["previous"], (date(2028, 1, 30), date(2028, 2, 29)))
        self.assertEqual(windows["last_year"], (date(2027, 3, 1), date(2027, 3, 31)))
        leap = statements.comparison_windows(date(2028, 2, 29), date(2028, 2, 29))
        self.assertEqual(leap["last_year"], (date(2027, 2, 28), date(2027, 2, 28)))
        self.assertEqual(statements.comparison_windows(None, None), {})

    def test_only_ledger_readers_open_the_statements(self):
        from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
        from permissions.models import RoleCode
        from reports.tests_dashboard import prepared_client

        prepared_client()
        for role, status in ((RoleCode.CASHIER, 403), (RoleCode.ACCOUNTANT, 200)):
            user = make_user(username=f"fs_{role}")
            make_user_profile(user=user, role=make_seeded_role(role))
            self.client.force_login(user)
            for name in ("ledger:income_statement", "ledger:balance_sheet", "ledger:cash_flow"):
                with self.subTest(role=role, page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, status)


class GroupStatementsTests(TestCase):
    """A factory entity selling into the shop's cashbox: each entity's books
    balance alone, and the group's equal their sum with the due-between
    balances cancelling out."""

    def test_each_entity_balances_and_the_group_eliminates(self):
        from entities.services import main_entity, save_entity
        from hesba_testing.factories import make_cashbox, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
        from permissions.models import RoleCode
        from reports.tests_dashboard import sell

        from .reports import control_balance

        owner = make_user(username="fs_group_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        shop = main_entity()
        factory = save_entity({"code": "FAC", "name_ar": "المصنع"}, owner)
        till = make_cashbox(cashbox_code="SHOP-TILL", opening_balance=D("1000"))
        store = make_location(location_code="FAC-1", entity=factory)
        item = make_item(item_code="BOX")
        stock_in(item, store, D("10"), "30.00")
        sell(item, store, till, D("4"), D("50.00"), "200.00", owner, number="FAC-SI-1")
        Projector().rebuild()

        group = statements.balance_sheet()
        self.assertTrue(group["balanced"])
        self.assertEqual(control_balance("intercompany"), D("0.00"))
        profits = []
        for entity in (shop, factory):
            with self.subTest(entity=entity.code):
                sheet = statements.balance_sheet(entity=entity)
                self.assertTrue(sheet["balanced"])
                self.assertTrue(statements.cash_flow(entity=entity)["reconciles"])
                profits.append(statements.income_statement(entity=entity, compare=False)["net_profit"])
        self.assertNotEqual(control_balance("intercompany", entity=factory), D("0.00"))
        self.assertEqual(sum(profits), statements.income_statement(compare=False)["net_profit"])
        # The sale's profit (4 x 50 - 4 x 30) belongs to the factory that sold the stock.
        self.assertEqual(statements.income_statement(entity=factory, compare=False)["gross_profit"], D("80.00"))
