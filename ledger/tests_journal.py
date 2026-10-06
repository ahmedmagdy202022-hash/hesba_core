"""GL-002 (HG-032): the journal projected from a real trading month agrees,
to the piastre, with every report Hesba already trusts."""

from decimal import Decimal as D

from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from reports.tests_month_acceptance import OneTradingMonthTests

from . import reports
from .models import Account, JournalEntry, JournalLine
from .projector import Projector, ensure_fresh


def bal(code):
    agg = JournalLine.objects.filter(account__code=code).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


class JournalOnTradingMonthTests(OneTradingMonthTests):
    # Reuse the month's fixture only; its own figure tests run in their own class.
    test_cash_in_the_till = test_stock_on_the_shelf = test_who_owes_whom = None
    test_profit_for_the_month = test_month_end_close_freezes_the_same_figures = None

    def setUp(self):
        Projector().rebuild()

    def test_every_entry_balances_and_nothing_is_left_in_suspense(self):
        for entry in JournalEntry.objects.prefetch_related("lines"):
            with self.subTest(entry=str(entry)):
                self.assertEqual(sum(l.debit for l in entry.lines.all()), sum(l.credit for l in entry.lines.all()))
        self.assertEqual(bal("1199"), 0)

    def test_reconciles_with_the_reports(self):
        result = reports.reconciliation()
        for row in result["rows"]:
            with self.subTest(check=row["key"]):
                self.assertEqual(row["difference"], 0, row)
        self.assertTrue(result["ok"])
        self.assertTrue(result["balanced"])

    def test_the_story_in_accounts(self):
        # Hand figures from the month's story (see reports.tests_month_acceptance).
        self.assertEqual(bal("5202"), D("800.00"))      # rent
        self.assertEqual(bal("5203"), D("450.00"))      # electricity
        self.assertEqual(bal("3102"), D("-20000.00"))   # the owner's capital into the till
        self.assertEqual(bal("1103"), D("0.00"))        # Karim: 800 - 300 - 400 - 100 (return)
        sales = -(bal("4101") + bal("4102"))
        self.assertEqual(sales, D("800") - D("160") + D("135") * 26)
        tb = reports.trial_balance()
        self.assertTrue(tb["balanced"])

    def test_cancelling_a_sale_shows_as_its_own_reversing_entry(self):
        from sales.models import SalesInvoice
        from sales.services import cancel_posted_sales_invoice

        invoice = SalesInvoice.objects.get(invoice_number="W-27")
        cancel_posted_sales_invoice(invoice.pk, self.owner, reason="غلط")
        Projector().rebuild()
        entries = JournalEntry.objects.filter(source_type="sales_invoice", source_id=invoice.pk)
        self.assertGreaterEqual(entries.count(), 1)
        net = JournalLine.objects.filter(entry__in=entries, account__code="4101").aggregate(d=Sum("debit"), c=Sum("credit"))
        self.assertEqual((net["d"] or 0) - (net["c"] or 0), 0)
        self.assertTrue(reports.reconciliation()["ok"])

    def test_screens_show_the_month_in_both_languages(self):
        from reports.tests_dashboard import prepared_client

        prepared_client()
        self.client.force_login(self.owner)
        trial = self.client.get(reverse("ledger:trial_balance"))
        self.assertContains(trial, 'data-balanced="1"')
        tb = reports.trial_balance()
        self.assertTrue(tb["balanced"])
        self.assertContains(trial, f'data-total-debit>{tb["totals"]["debit"]:,.2f}<')
        recon = self.client.get(reverse("ledger:reconciliation"))
        self.assertContains(recon, 'data-recon-ok="1"')
        self.assertContains(recon, 'data-check="inventory"')
        journal = self.client.get(reverse("ledger:journal"))
        self.assertContains(journal, "W-27")
        self.assertContains(journal, "data-entry=")
        cash = Account.objects.get(code="1101")
        ledger = self.client.get(reverse("ledger:account_ledger", args=[cash.pk]))
        self.assertContains(ledger, f'data-closing>{bal("1101"):,.2f}<')
        english = self.client.get(reverse("ledger:trial_balance") + "?lang=en")
        self.assertContains(english, "Trial balance")
        self.assertContains(english, "The trial balance balances")
        # A period that starts mid-month carries the earlier movement as the opening.
        mid = self.start.replace(day=15)
        windowed = reports.trial_balance(date_from=mid, date_to=self.end)
        cash_row = next(row for row in windowed["rows"] if row["account"].code == "1101")
        self.assertEqual(cash_row["closing"], bal("1101"))
        self.assertTrue(windowed["balanced"])
        page = self.client.get(reverse("ledger:account_ledger", args=[cash.pk]) + f"?from={mid:%Y-%m-%d}")
        self.assertContains(page, f'data-closing>{bal("1101"):,.2f}<')


class FreshnessAndScreensTests(TestCase):
    def test_rebuilds_only_when_a_source_changes(self):
        from hesba_testing.factories import make_cashbox

        self.assertTrue(ensure_fresh())
        self.assertFalse(ensure_fresh())
        make_cashbox(cashbox_code="NEW", opening_balance=D("500"))
        self.assertTrue(ensure_fresh())
        self.assertEqual(bal("1101"), D("500.00"))
        self.assertEqual(bal("3103"), D("-500.00"))

    def test_only_ledger_readers_open_the_screens(self):
        from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
        from permissions.models import RoleCode
        from reports.tests_dashboard import prepared_client

        prepared_client()
        names = ("ledger:journal", "ledger:trial_balance", "ledger:reconciliation")
        for role, status in ((RoleCode.CASHIER, 403), (RoleCode.ACCOUNTANT, 200), (RoleCode.MANAGER, 200)):
            user = make_user(username=f"gl2_{role}")
            make_user_profile(user=user, role=make_seeded_role(role))
            self.client.force_login(user)
            for name in names:
                with self.subTest(role=role, page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, status)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("ledger:journal")).status_code, 302)
