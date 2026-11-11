"""Watch round 1, SH-03: the expenses register says what the dashboard's «المصروفات» counts that it does not list."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxDirection, CashboxKind, CashboxOperationType
from cashboxes.services import create_cashbox_operation
from hesba_testing.factories import make_cashbox, make_cashbox_movement
from permissions.models import RoleCode

from .models import ExpenseCategory
from .services import bank_fee_total, expense_total, record_expense
from .tests import funded_cashbox, person

NOTE_AR = "الإجمالي ده لسجل المصروفات بس. بتتحسب في «المصروفات» في لوحة القيادة وتقرير الأرباح ومش ظاهرة هنا: مصاريف بنك <bdi>35.00</bdi>."
NOTE_EN = "This total is the expenses register only. Counted in «Expenses» on the dashboard and the profit report but not listed here: bank fees <bdi>35.00</bdi>."


class RegisterVersusDashboardTests(TestCase):
    def setUp(self):
        self.owner = person(RoleCode.OWNER, "w1_exp_owner")
        self.cashbox = funded_cashbox()
        self.today = timezone.localdate()
        record_expense(category=ExpenseCategory.objects.get(code="rent"), cashbox=self.cashbox, amount=D("250.00"), expense_date=self.today,
                       description="إيجار", user=self.owner)
        self.client.force_login(self.owner)

    def add_fee(self):
        bank = make_cashbox(cashbox_code="BANK-W1", name_ar="البنك", kind=CashboxKind.BANK)
        make_cashbox_movement(bank, CashboxDirection.IN, "500.00")
        create_cashbox_operation("FEE-W1", self.today, CashboxOperationType.BANK_FEE, D("35.00"), "مصاريف كشف حساب", self.owner, source_cashbox=bank)

    def test_the_gap_is_exactly_the_bank_fee_and_the_page_names_it(self):
        self.add_fee()
        page = self.client.get(reverse("expenses:list"))
        self.assertEqual(page.context["total"], D("250.00"))  # the register: Expense rows only, unchanged
        self.assertEqual(expense_total(self.today, self.today), D("285.00"))  # what the dashboard and the profit report use
        self.assertEqual(bank_fee_total(self.today, self.today), D("35.00"))
        profit = self.client.get(reverse("reports:profit"), {"date_from": self.today.isoformat(), "date_to": self.today.isoformat()})
        self.assertEqual(profit.context["totals"]["expenses"], D("285.00"))  # the profit report takes the same 285 as the dashboard
        self.assertEqual(expense_total(self.today, self.today) - page.context["total"], bank_fee_total(self.today, self.today))
        self.assertContains(page, f'<p class="op-notice" data-expense-outside>{NOTE_AR}</p>')
        en = self.client.get(reverse("expenses:list"), {"lang": "en"})
        self.assertContains(en, f'<p class="op-notice" data-expense-outside>{NOTE_EN}</p>')

    def test_no_note_without_bank_fees_or_payroll(self):
        self.assertNotContains(self.client.get(reverse("expenses:list")), "data-expense-outside")

    def test_the_note_follows_the_date_filter(self):
        self.add_fee()
        yesterday = (self.today - timedelta(days=3)).isoformat()
        before = self.client.get(reverse("expenses:list"), {"date_to": yesterday})
        self.assertNotContains(before, "data-expense-outside")
