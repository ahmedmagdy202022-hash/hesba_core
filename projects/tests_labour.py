"""Contracting C5 (HG-130): daily labour on a project and its pay sheets.

The reference week (six days back to yesterday):
* 2 masons x 6 days x 400.00 = 4,800.00;
* 3 helpers x 5 days x 250.00 = 3,750.00;
* 1 helper half a day = 125.00;
* 4 overtime hours of a mason = 4 x 400.00 / 8 = 200.00;
* the sheet: 8,875.00, paid in one ordinary expense from the cashbox (one journal entry, on the expense category's
  account, never 5103), and the project's labour cost is 8,875.00.
"""

import re
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from cashboxes.models import CashboxMovement, CashboxOperation
from cashboxes.services import get_cashbox_balance
from expenses.models import Expense
from expenses.services import cancel_expense
from hesba_testing.factories import make_cashbox
from ledger.models import JournalEntry
from ledger.projector import ensure_fresh
from permissions.models import RoleCode
from sales.services import post_sales_invoice

from . import costs, labour, services
from .models import CostHeading, LabourDay, LabourPaySheet, ProjectExpense, SiteWorker
from .tests_c2_ui import PRE_LABELLED
from .tests_contract import TODAY, ContractSetup, gl, ledger_ok, person

def fund(cashbox, owner, amount="20000.00"):
    """Money into the box through a posted cash operation, so the books carry it (a raw movement would sit in suspense)."""

    from cashboxes.models import CashboxOperationType
    from cashboxes.services import create_cashbox_operation

    return create_cashbox_operation(f"FUND-{cashbox.cashbox_code}", TODAY - timedelta(days=10), CashboxOperationType.DIRECT_IN, D(amount),
                                    "رصيد للتجربة", owner, destination_cashbox=cashbox)


BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")
WEEK = [TODAY - timedelta(days=back) for back in (6, 5, 4, 3, 2, 1)]


class LabourSetup(ContractSetup):
    def setUp(self):
        super().setUp()
        self.engineer = person(RoleCode.SITE_ENGINEER, "c5_engineer")
        fund(self.cashbox, self.owner)
        self.masons = [labour.save_worker(self.owner, {"name": name, "trade": "مبيّض", "day_rate": "400"}) for name in ("حسن علي", "محمود سيد")]
        self.helpers = [labour.save_worker(self.owner, {"name": name, "trade": "عامل مساعد", "day_rate": "250"})
                        for name in ("أحمد فتحي", "إبراهيم عادل", "كريم سعيد", "مصطفى رجب")]

    def record(self, work_date, entries, user=None, project=None):
        return labour.record_day(project or self.project, user or self.engineer, work_date=work_date, entries=entries)

    def record_the_week(self):
        for n, work_date in enumerate(WEEK):
            entries = [(mason, "1", "0") for mason in self.masons]
            if n < 5:
                entries += [(helper, "1", "0") for helper in self.helpers[:3]]
            if work_date == WEEK[-1]:
                entries[0] = (self.masons[0], "1", "4")  # 4 overtime hours of a mason
                entries.append((self.helpers[3], "0.5", "0"))  # one helper, half a day
            self.record(work_date, entries)

    def pay(self, cashbox=None, user=None, **extra):
        return labour.pay_sheet(self.project, user or self.owner, period_from=WEEK[0], period_to=WEEK[-1], cashbox=cashbox or self.cashbox, **extra)

    def url(self, name, *args):
        return reverse(f"projects:{name}", args=[self.project.pk, *args])

    def counts(self):
        return {"expenses": Expense.objects.count(), "sheets": LabourPaySheet.objects.count(), "operations": CashboxOperation.objects.count(),
                "movements": CashboxMovement.objects.count(), "links": ProjectExpense.objects.count()}


class TheWeekTests(LabourSetup):
    def test_the_week_comes_to_8875(self):
        self.record_the_week()
        sheet = labour.preview(self.project, WEEK[0], WEEK[-1])
        by_worker = {row["worker"].name: (row["days"], row["overtime"], row["amount"]) for row in sheet["rows"]}
        self.assertEqual(by_worker["حسن علي"], (D("6.0"), D("4.00"), D("2600.00")))  # 6 x 400.00 + 4 x 400.00 / 8
        self.assertEqual(by_worker["محمود سيد"], (D("6.0"), D("0.00"), D("2400.00")))
        for name in ("أحمد فتحي", "إبراهيم عادل", "كريم سعيد"):
            self.assertEqual(by_worker[name], (D("5.0"), D("0.00"), D("1250.00")))  # 5 x 250.00
        self.assertEqual(by_worker["مصطفى رجب"], (D("0.5"), D("0.00"), D("125.00")))
        masons = sum((by_worker[w.name][2] for w in self.masons), D("0")) - D("200.00")
        helpers = sum((by_worker[w.name][2] for w in self.helpers[:3]), D("0"))
        self.assertEqual((masons, helpers, by_worker["مصطفى رجب"][2], D("200.00")), (D("4800.00"), D("3750.00"), D("125.00"), D("200.00")))
        self.assertEqual(sheet["total"], D("8875.00"))
        self.assertEqual(sheet["day_hours"], D("8"))
        self.assertEqual(LabourDay.objects.count(), 6 * 2 + 5 * 3 + 1)

    def test_paying_the_week_is_one_expense_one_journal_entry_and_the_projects_labour_cost(self):
        self.record_the_week()
        costs.save_budget(self.project, self.owner, {CostHeading.LABOUR: "20000"})
        ensure_fresh()
        journal, general = JournalEntry.objects.count(), gl("general_expense")
        before_used = next(row for row in costs.budget_rows(self.project)["rows"] if row["heading"] == CostHeading.LABOUR)["used"]
        self.assertEqual((get_cashbox_balance(self.cashbox), before_used, labour.labour_cost(self.project)), (D("20000.00"), 0, D("0.00")))

        sheet = self.pay()
        self.assertEqual((sheet.number, sheet.total, sheet.day_hours, sheet.period_from, sheet.period_to), (1, D("8875.00"), D("8.00"), WEEK[0], WEEK[-1]))
        expense = Expense.objects.get()
        self.assertEqual((expense.amount, expense.cashbox, expense.category.code, expense.category.name_ar, expense.status),
                         (D("8875.00"), self.cashbox, "daily_labour", "عمالة يومية", "posted"))
        self.assertEqual(sheet.expense, expense)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("11125.00"))  # 20,000.00 − 8,875.00
        link = ProjectExpense.objects.get(expense=expense)
        self.assertEqual((link.project, link.heading), (self.project, CostHeading.LABOUR))
        self.assertEqual(labour.labour_cost(self.project), D("8875.00"))
        self.assertEqual(services.summary(self.project)["expenses"], D("8875.00"))
        row = next(row for row in costs.budget_rows(self.project)["rows"] if row["heading"] == CostHeading.LABOUR)
        self.assertEqual((row["budget"], row["actual"], row["variance"], row["used"]), (D("20000.00"), D("8875.00"), D("11125.00"), 44))
        self.assertEqual(set(LabourDay.objects.values_list("pay_sheet", flat=True)), {sheet.pk})

        # One journal entry, the cash operation's, on the category's account (general expense); never project cost 5103.
        ensure_fresh()
        self.assertEqual(JournalEntry.objects.count(), journal + 1)
        self.assertEqual(JournalEntry.objects.filter(source_type="cashbox_operation", source_id=expense.cashbox_operation_id).count(), 1)
        self.assertEqual(gl("general_expense") - general, D("8875.00"))
        self.assertEqual(gl("project_cost"), D("0.00"))
        ledger_ok(self)

    def test_a_second_sheet_over_the_same_week_comes_to_0_and_is_refused(self):
        self.record_the_week()
        self.pay()
        self.assertEqual(labour.preview(self.project, WEEK[0], WEEK[-1])["total"], D("0.00"))
        before = self.counts()
        with self.assertRaisesMessage(ValidationError, "مفيش أيام لسه ما اتصرفتش"):
            self.pay()
        with self.assertRaisesMessage(ValidationError, "no unpaid days"):
            self.pay(lang="en")
        self.assertEqual(self.counts(), before)
        self.assertEqual(Expense.objects.get().amount, D("8875.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("11125.00"))
        self.assertEqual(labour.labour_cost(self.project), D("8875.00"))
        # A day recorded later is paid next, on its own.
        self.record(TODAY, [(self.masons[1], "1", "0")])
        second = labour.pay_sheet(self.project, self.owner, period_from=WEEK[0], period_to=TODAY, cashbox=self.cashbox)
        self.assertEqual((second.number, second.total), (2, D("400.00")))
        ledger_ok(self)

    def test_cancelling_the_expense_frees_the_days_and_brings_the_cash_back(self):
        self.record_the_week()
        sheet = self.pay()
        cancel_expense(sheet.expense_id, reversal_date=TODAY, reason="الكشف غلط", user=self.owner)
        # Nothing is written on cancel: the days still point at the sheet, and are free because its expense is cancelled.
        self.assertEqual(set(LabourDay.objects.values_list("pay_sheet", flat=True)), {sheet.pk})
        self.assertEqual(labour.labour_cost(self.project), D("0.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("20000.00"))
        self.assertEqual(labour.preview(self.project, WEEK[0], WEEK[-1])["total"], D("8875.00"))
        self.assertEqual([row["paid"] for row in labour.sheets(self.project)], [False])
        again = self.pay()
        self.assertEqual((again.number, again.total), (2, D("8875.00")))
        self.assertEqual(set(LabourDay.objects.values_list("pay_sheet", flat=True)), {again.pk})
        self.assertEqual(labour.labour_cost(self.project), D("8875.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("11125.00"))
        sheet.refresh_from_db()
        self.assertEqual(sheet.total, D("8875.00"))  # the cancelled sheet keeps its total; its days moved to the new one
        self.assertEqual(labour.sheet_rows(sheet), ([], D("0.00")))
        ledger_ok(self)

    def test_vat_on_and_off_give_the_same_totals(self):
        from settings_core.capabilities import _write

        self.record_the_week()
        off = labour.preview(self.project, WEEK[0], WEEK[-1])["total"]
        _write("vat", True)
        on = labour.preview(self.project, WEEK[0], WEEK[-1])["total"]
        self.assertEqual((off, on), (D("8875.00"), D("8875.00")))
        sheet = self.pay()
        self.assertEqual((sheet.total, sheet.expense.amount), (D("8875.00"), D("8875.00")))  # no VAT on wages
        self.assertEqual(get_cashbox_balance(self.cashbox), D("11125.00"))
        self.assertEqual(gl("vat_in"), D("0.00"))
        ledger_ok(self)

    def test_a_rate_changed_after_recording_leaves_the_recorded_rate_on_old_days(self):
        mason = self.masons[0]
        self.record(WEEK[0], [(mason, "1", "0")])
        labour.save_worker(self.owner, {"name": mason.name, "trade": mason.trade, "day_rate": "500"}, mason)
        self.record(WEEK[1], [(mason, "1", "0")])
        self.record(WEEK[0], [(mason, "1", "2")])  # changing an old unpaid day keeps its rate
        days = {day.work_date: day for day in LabourDay.objects.all()}
        self.assertEqual((days[WEEK[0]].rate, days[WEEK[1]].rate), (D("400.00"), D("500.00")))
        self.assertEqual(labour.preview(self.project, WEEK[0], WEEK[1])["total"], D("1000.00"))  # 400 + 2 x 400 / 8 + 500

    def test_the_overtime_divisor_is_a_setting_and_the_sheet_keeps_the_one_it_was_paid_with(self):
        from settings_core.models import SystemSetting

        self.record(WEEK[0], [(self.masons[0], "1", "4")])
        SystemSetting.objects.create(key=labour.DAY_HOURS_KEY, value="10", data_type=SystemSetting.DataType.DECIMAL)
        self.assertEqual(labour.preview(self.project, WEEK[0], WEEK[0])["total"], D("560.00"))  # 400 + 4 x 400 / 10
        sheet = labour.pay_sheet(self.project, self.owner, period_from=WEEK[0], period_to=WEEK[0], cashbox=self.cashbox)
        self.assertEqual((sheet.total, sheet.day_hours), (D("560.00"), D("10.00")))
        SystemSetting.objects.filter(key=labour.DAY_HOURS_KEY).update(value="8")
        self.assertEqual(labour.sheet_rows(sheet)[1], D("560.00"))  # the paid sheet still adds up to its total
        SystemSetting.objects.filter(key=labour.DAY_HOURS_KEY).update(value="abc")
        self.assertEqual(labour.day_hours(), D("8"))


class GuardTests(LabourSetup):
    def test_the_engineer_can_neither_pay_nor_set_rates(self):
        self.record_the_week()
        before = self.counts()
        with self.assertRaises(PermissionDenied):
            self.pay(user=self.engineer)
        with self.assertRaises(PermissionDenied):
            labour.pay_sheet(self.project, self.engineer, period_from=WEEK[0], period_to=WEEK[-1], cashbox=self.cashbox, lang="en")
        self.assertEqual(self.counts(), before)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("20000.00"))
        self.assertFalse(LabourDay.objects.exclude(pay_sheet=None).exists())

    def test_another_entitys_cashbox_is_refused(self):
        from entities.current import working_in
        from entities.models import Entity

        post_sales_invoice(self.certify(concrete="10").invoice_id, self.owner)  # the project's money lives in the main entity
        branch = Entity.objects.create(code="E-C5", name_ar="فرع العمالة")
        far = make_cashbox(cashbox_code="C5-FAR", entity=branch)
        fund(far, self.owner)
        self.record_the_week()
        before = self.counts()
        with self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            self.pay(cashbox=far)
        with working_in(branch), self.assertRaisesMessage(ValidationError, "كيان تاني"):
            self.pay(cashbox=far)  # the branch sees none of the main entity's workers' days either
        self.assertEqual(self.counts(), before)
        self.assertEqual(get_cashbox_balance(far), D("20000.00"))

    def test_a_cashbox_of_another_entity_than_the_workers_is_refused_on_a_project_with_no_money_yet(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="E-C5B", name_ar="فرع تاني")
        far = make_cashbox(cashbox_code="C5-FAR2", entity=branch)
        fund(far, self.owner)
        self.record_the_week()
        before = self.counts()
        with self.assertRaisesMessage(ValidationError, "كيان تاني غير كيان الخزنة"):
            self.pay(cashbox=far)
        self.assertEqual(self.counts(), before)

    def test_a_labour_expense_cannot_be_unlinked_from_the_project(self):
        self.record_the_week()
        sheet = self.pay()
        link = ProjectExpense.objects.get(expense=sheet.expense)
        with self.assertRaisesMessage(ValidationError, "مصروف كشف عمالة يومية"):
            services.unlink(self.project, link, self.owner)
        with self.assertRaisesMessage(ValidationError, "pay sheet's expense"):
            services.unlink(self.project, link, self.owner, "en")
        self.assertTrue(ProjectExpense.objects.filter(pk=link.pk).exists())
        self.assertEqual(labour.labour_cost(self.project), D("8875.00"))

    def test_the_rules_of_a_day(self):
        mason = self.masons[0]
        for days, overtime, message in (("0.75", "0", "يوم كامل أو نص يوم"), ("2", "0", "يوم كامل أو نص يوم"), ("x", "0", "يوم كامل أو نص يوم"),
                                        ("1", "-1", "الإضافي"), ("1", "17", "بيعدّي 24 ساعة")):
            with self.subTest(days=days, overtime=overtime), self.assertRaisesMessage(ValidationError, message):
                self.record(WEEK[0], [(mason, days, overtime)])
        with self.assertRaisesMessage(ValidationError, "بتاريخ جاي"):
            self.record(TODAY + timedelta(days=1), [(mason, "1", "0")])
        idle = labour.save_worker(self.owner, {"name": "عامل موقوف", "day_rate": "200", "active": False})
        with self.assertRaisesMessage(ValidationError, "موقوف"):
            self.record(WEEK[0], [(idle, "1", "0")])
        other = services.save_project({"name": "فيلا الشروق", "customer": self.customer}, self.owner)
        self.record(WEEK[0], [(mason, "0.5", "0")])
        self.record(WEEK[0], [(mason, "0.5", "0")], project=other)  # half a day on each site is fine
        with self.assertRaisesMessage(ValidationError, "يوم كامل في اليوم ده"):
            self.record(WEEK[0], [(mason, "1", "0")], project=other)
        self.assertEqual(LabourDay.objects.count(), 2)
        with self.assertRaisesMessage(ValidationError, "أكبر من صفر"):
            labour.save_worker(self.owner, {"name": "x", "day_rate": "0"})
        with self.assertRaisesMessage(ValidationError, "اسم العامل"):
            labour.save_worker(self.owner, {"name": " ", "day_rate": "100"})

    def test_a_paid_day_cannot_change_and_an_absent_worker_loses_an_unpaid_day(self):
        self.record(WEEK[0], [(self.masons[0], "1", "0"), (self.masons[1], "1", "0")])
        self.record(WEEK[0], [(self.masons[1], "0", "0")])  # absent after all: the unpaid day goes
        self.assertEqual(list(LabourDay.objects.values_list("worker", flat=True)), [self.masons[0].pk])
        labour.pay_sheet(self.project, self.owner, period_from=WEEK[0], period_to=WEEK[0], cashbox=self.cashbox)
        with self.assertRaisesMessage(ValidationError, "اتصرف في كشف"):
            self.record(WEEK[0], [(self.masons[0], "0.5", "0")])
        with self.assertRaisesMessage(ValidationError, "اتصرف في كشف"):
            self.record(WEEK[0], [(self.masons[0], "0", "0")])
        self.assertEqual(self.record(WEEK[0], [(self.masons[0], "1", "0")]), (0, 0))  # the same again changes nothing
        self.assertEqual(LabourDay.objects.get().days, D("1.0"))

    def test_a_closed_project_takes_no_days_and_pays_no_sheet(self):
        self.record_the_week()
        self.project.status = "done"
        self.project.save(update_fields=["status"])
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            self.record(TODAY, [(self.masons[0], "1", "0")])
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            self.pay()
        self.assertEqual(Expense.objects.count(), 0)

    def test_a_crew_count_by_trade_for_the_daily_report(self):
        self.record_the_week()
        self.assertEqual(labour.crew_by_trade(self.project, WEEK[-1]), [("عامل مساعد", 1), ("مبيّض", 2)])
        self.assertEqual(labour.crew_by_trade(self.project, WEEK[0]), [("عامل مساعد", 3), ("مبيّض", 2)])
