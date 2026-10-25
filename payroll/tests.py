"""T12 (MARKET-PARITY): payroll — the accrual, its payments and its reversal, checked in the ledger (HG-060)."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox, CashboxOperation
from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from ledger import reports
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from staff.models import Employee

from . import services
from .models import PayrollRun, PayTo, RunStatus, SalaryProfile

TODAY = timezone.localdate()
LAST_MONTH = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def bal(code):
    agg = JournalLine.objects.filter(account__code=code).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


class PayrollSetup(TestCase):
    def setUp(self):
        prepared_client("commercial", "fashion", modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,employees_technicians,reports")
        self.owner = person(RoleCode.OWNER, "py_owner")
        self.cashier = person(RoleCode.CASHIER, "py_cashier")
        self.cash = Cashbox.objects.create(cashbox_code="CASH-PY", name_ar="الخزنة", opening_balance=D("50000.00"))
        self.mona = Employee.objects.create(code="E1", name="منى")
        self.ali = Employee.objects.create(code="E2", name="علي")
        SalaryProfile.objects.create(employee=self.mona, basic=D("10000.00"), allowances=D("2000.00"))
        SalaryProfile.objects.create(employee=self.ali, basic=D("3000.00"), taxed=False)

    def make_run(self):
        return services.prepare(LAST_MONTH.strftime("%Y-%m"), self.owner)


class CalculationTests(PayrollSetup):
    def test_each_line_from_the_salary_insurance_and_brackets(self):
        run = self.make_run()
        mona = run.lines.get(employee=self.mona)
        # gross 12,000; insurance on the 10,000 basic: 11% and 18.75%;
        # tax: (12,000 − 1,100) × 12 − 20,000 = 110,800 → 1,500 + 2,250 + 8,160 = 11,910 a year → 992.50 a month.
        self.assertEqual((mona.gross, mona.employee_insurance, mona.company_insurance, mona.income_tax, mona.net),
                         (D("12000.00"), D("1100.00"), D("1875.00"), D("992.50"), D("9907.50")))
        ali = run.lines.get(employee=self.ali)
        self.assertEqual((ali.employee_insurance, ali.company_insurance, ali.income_tax, ali.net), (D("330.00"), D("562.50"), D("0.00"), D("2670.00")))
        self.assertEqual(services.run_totals(run), {"gross": D("15000.00"), "employee_insurance": D("1430.00"), "company_insurance": D("2437.50"),
                                                    "income_tax": D("992.50"), "net": D("12577.50")})

    def test_the_insured_wage_stays_within_the_floor_and_the_ceiling(self):
        SalaryProfile.objects.filter(employee=self.mona).update(basic=D("40000.00"))
        SalaryProfile.objects.filter(employee=self.ali).update(basic=D("1500.00"))
        run = self.make_run()
        self.assertEqual(run.lines.get(employee=self.mona).employee_insurance, D("1595.00"))  # 11% of the 14,500 ceiling
        self.assertEqual(run.lines.get(employee=self.ali).employee_insurance, D("253.00"))    # 11% of the 2,300 floor

    def test_a_draft_line_changes_by_hand_and_a_posted_one_does_not(self):
        run = self.make_run()
        line = run.lines.get(employee=self.ali)
        line = services.change_line(line, {"absence_deduction": "300", "other_deductions": "200"}, self.owner)
        self.assertEqual((line.gross, line.net), (D("2500.00"), D("2170.00")))
        services.post(run, self.owner)
        with self.assertRaisesMessage(ValidationError, "اترحّل"):
            services.change_line(line, {"basic": "1"}, self.owner)

    def test_the_rules_are_data(self):
        with self.assertRaisesMessage(ValidationError, "شرائح"):
            services.save_rules({"tax_brackets": "40000:0\n30000:10\n:20"}, self.owner)
        services.save_rules({"employee_insurance_percent": "10", "tax_brackets": "100000:0\n:10", "yearly_exemption": "0"}, self.owner)
        line = self.make_run().lines.get(employee=self.mona)
        self.assertEqual(line.employee_insurance, D("1000.00"))
        self.assertEqual(line.income_tax, D("266.67"))  # (12,000 − 1,000) × 12 = 132,000 → 3,200 a year
        with self.assertRaises(PermissionDenied):
            services.save_rules({"yearly_exemption": "1"}, self.cashier)

    def test_one_run_a_month_and_only_with_salaries(self):
        self.make_run()
        with self.assertRaisesMessage(ValidationError, "اتعملت قبل كده"):
            self.make_run()
        SalaryProfile.objects.update(basic=0)
        with self.assertRaisesMessage(ValidationError, "مفيش موظفين"):
            services.prepare(TODAY.strftime("%Y-%m"), self.owner)


class LedgerTests(PayrollSetup):
    def test_posting_books_the_cost_and_the_debts_without_moving_money(self):
        run = services.post(self.make_run(), self.owner)
        self.assertEqual(run.posted_on, (LAST_MONTH.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
        Projector().rebuild()
        self.assertEqual(bal("5201"), D("17437.50"))   # gross + the company's insurance
        self.assertEqual(bal("2107"), D("-12577.50"))  # net pay owed
        self.assertEqual(bal("2108"), D("-3867.50"))   # both insurance shares
        self.assertEqual(bal("2109"), D("-992.50"))    # income tax
        self.assertEqual(bal("1199"), D("0"))
        self.assertEqual(get_cashbox_balance(self.cash), D("50000.00"))
        self.assertTrue(reports.trial_balance()["balanced"])

    def test_paying_moves_only_what_is_paid_and_clears_its_debt(self):
        run = services.post(self.make_run(), self.owner)
        services.pay(run, PayTo.NET, self.cash, "", self.owner)  # empty: all that is left
        services.pay(run, PayTo.INSURANCE, self.cash, "2000", self.owner)
        with self.assertRaisesMessage(ValidationError, "أكتر من المستحق"):
            services.pay(run, PayTo.INSURANCE, self.cash, "5000", self.owner)
        self.assertEqual(get_cashbox_balance(self.cash), D("50000.00") - D("12577.50") - D("2000.00"))
        owed = services.owed(run)
        self.assertEqual((owed[PayTo.NET]["left"], owed[PayTo.INSURANCE]["left"], owed[PayTo.TAX]["left"]), (D("0.00"), D("1867.50"), D("992.50")))
        Projector().rebuild()
        self.assertEqual(bal("2107"), D("0"))
        self.assertEqual(bal("2108"), D("-1867.50"))
        self.assertEqual(bal("5201"), D("17437.50"))  # paying is not a second cost
        self.assertEqual(bal("1199"), D("0"))
        self.assertTrue(reports.trial_balance()["balanced"])
        self.assertTrue(reports.reconciliation()["ok"])

    def test_a_payment_is_cancelled_and_the_run_reversed_append_only(self):
        run = services.post(self.make_run(), self.owner)
        payment = services.pay(run, PayTo.TAX, self.cash, "", self.owner)
        with self.assertRaisesMessage(ValidationError, "ألغي الدفعات"):
            services.reverse(run, self.owner, "غلط")
        services.cancel_payment(payment, self.owner, "اتدفعت مرتين")
        self.assertEqual(get_cashbox_balance(self.cash), D("50000.00"))
        run = services.reverse(run, self.owner, "غلط في المرتبات")
        self.assertEqual(run.status, RunStatus.REVERSED)
        Projector().rebuild()
        for code in ("5201", "2107", "2108", "2109", "1199"):
            self.assertEqual(bal(code), D("0"), code)
        self.assertEqual(JournalLine.objects.filter(entry__source_type="payroll_run").values("entry").distinct().count(), 2)  # the accrual and its reversal
        self.assertTrue(reports.trial_balance()["balanced"])

    def test_the_cashbox_screen_leaves_payroll_payments_alone(self):
        run = services.post(self.make_run(), self.owner)
        payment = services.pay(run, PayTo.NET, self.cash, "100", self.owner)
        self.client.force_login(self.owner)
        response = self.client.post(reverse("cashboxes:operation_cancel", args=[payment.operation_id]), {"reversal_date": TODAY.isoformat(), "reason": "x"}, follow=True)
        self.assertContains(response, "تابعة للمرتبات")
        self.assertEqual(CashboxOperation.objects.get(pk=payment.operation_id).status, "posted")
        form = self.client.get(reverse("cashboxes:operation_create") + "?lang=ar")
        self.assertNotContains(form, 'value="payroll"')

    def test_only_who_may_record_expenses_runs_payroll(self):
        with self.assertRaises(PermissionDenied):
            services.prepare(LAST_MONTH.strftime("%Y-%m"), self.cashier)


class ScreenTests(PayrollSetup):
    def test_the_screens_in_both_languages(self):
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            self.assertContains(self.client.get(reverse("payroll:list") + f"?lang={lang}"), "data-payroll-prepare")
            self.assertContains(self.client.get(reverse("payroll:salaries") + f"?lang={lang}"), f'data-salary-row="{self.mona.pk}"')
            self.assertContains(self.client.get(reverse("payroll:rules") + f"?lang={lang}"), "data-payroll-rules-form")
        response = self.client.post(reverse("payroll:list"), {"lang": "ar", "month": LAST_MONTH.strftime("%Y-%m")})
        run = PayrollRun.objects.get()
        self.assertRedirects(response, f"{reverse('payroll:detail', args=[run.pk])}?lang=ar", fetch_redirect_response=False)
        url = reverse("payroll:detail", args=[run.pk])
        self.assertContains(self.client.get(url + "?lang=ar"), 'data-run-line="%d"' % self.mona.pk)
        self.client.post(url, {"lang": "ar", "action": "post"})
        page = self.client.get(url + "?lang=ar")
        self.assertContains(page, 'data-run-status="posted"')
        self.assertContains(page, "data-run-pay")
        self.client.post(url, {"lang": "ar", "action": "pay", "pay_to": "net", "cashbox": self.cash.pk, "amount": "", "paid_on": TODAY.isoformat()})
        self.assertEqual(services.owed(run)[PayTo.NET]["left"], D("0.00"))
        self.client.post(reverse("payroll:salaries"), {"lang": "ar", f"basic_{self.ali.pk}": "4000", f"allowances_{self.ali.pk}": "0", f"insured_{self.ali.pk}": "1"})
        self.assertEqual(SalaryProfile.objects.get(employee=self.ali).basic, D("4000.00"))
        self.assertFalse(SalaryProfile.objects.get(employee=self.ali).taxed)
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("payroll:list")).status_code, 403)


class CodexFixesTests(PayrollSetup):
    """Codex on #269: the reports count the payroll cost, and absences are counted up to the real day."""

    def test_the_reports_count_the_posted_cost_and_drop_a_reversed_run(self):
        from expenses.services import expense_total

        before = expense_total()
        run = services.post(self.make_run(), self.owner)
        self.assertEqual(services.cost_total(), D("17437.50"))
        self.assertEqual(expense_total() - before, D("17437.50"))  # the dashboard's net profit sees it
        self.assertEqual(services.cost_total(run.posted_on, run.posted_on), D("17437.50"))
        self.assertEqual(services.cost_total(run.posted_on + timedelta(days=1)), D("0.00"))
        services.reverse(run, self.owner, "غلط", reversed_on=TODAY.isoformat())
        self.assertEqual(services.cost_total(), D("0.00"))
        self.assertEqual(services.cost_total(TODAY, TODAY), D("-17437.50"))  # the reversal's own date

    def test_absences_are_counted_up_to_today(self):
        from unittest import mock

        with mock.patch("attendance.services.month_sheet", return_value=[]) as sheet:
            services.prepare(TODAY.strftime("%Y-%m"), self.owner)
        self.assertEqual(sheet.call_args.kwargs["today"], TODAY)  # not the month's end: days to come are not absences


class SecondReviewTests(PayrollSetup):
    """Codex on #269 (second look): a cancellation never goes back before its payment, and only the main entity pays."""

    def test_a_cancellation_is_not_dated_before_its_payment(self):
        run = services.post(self.make_run(), self.owner)
        payment = services.pay(run, PayTo.TAX, self.cash, "", self.owner, paid_on=TODAY.isoformat())
        with self.assertRaisesMessage(ValidationError, "قبل تاريخ الدفعة"):
            services.cancel_payment(payment, self.owner, "غلط", cancelled_on=(TODAY - timedelta(days=1)).isoformat())

    def test_only_the_main_entitys_money_pays_payroll(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="BR2", name_ar="الفرع")
        other = Cashbox.objects.create(cashbox_code="CASH-BR2", name_ar="خزنة الفرع", opening_balance=D("50000.00"), entity=branch)
        run = services.post(self.make_run(), self.owner)
        with self.assertRaisesMessage(ValidationError, "الشركة الرئيسية"):
            services.pay(run, PayTo.NET, other, "", self.owner)


class ThirdReviewTests(PayrollSetup):
    """Codex on #269 (third look): payroll is the main entity's, and a reversal waits for every payment cancellation."""

    def test_someone_kept_to_another_entity_never_sees_or_changes_payroll(self):
        from entities.models import Entity, EntityMembership
        from entities.services import main_entity

        main_entity()
        branch = Entity.objects.create(code="BR3", name_ar="الفرع")
        manager = person(RoleCode.MANAGER, "py_branch_manager")
        EntityMembership.objects.create(user=manager, entity=branch, is_default=True)
        run = services.post(self.make_run(), self.owner)
        self.assertFalse(services.can_view(manager))
        with self.assertRaises(PermissionDenied):
            services.reverse(run, manager, "غلط")
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("payroll:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("payroll:detail", args=[run.pk])).status_code, 403)
        EntityMembership.objects.create(user=manager, entity=main_entity())
        self.assertTrue(services.can_view(manager))  # a member of the main entity too: allowed again

    def test_a_reversal_is_not_dated_before_a_payment_cancellation(self):
        run = services.post(self.make_run(), self.owner, posted_on=LAST_MONTH.isoformat())
        payment = services.pay(run, PayTo.TAX, self.cash, "", self.owner, paid_on=LAST_MONTH.isoformat())
        services.cancel_payment(payment, self.owner, "غلط", cancelled_on=TODAY.isoformat())
        with self.assertRaisesMessage(ValidationError, "قبل إلغاء دفعة"):
            services.reverse(run, self.owner, "غلط", reversed_on=LAST_MONTH.isoformat())
        self.assertEqual(services.reverse(run, self.owner, "غلط", reversed_on=TODAY.isoformat()).status, RunStatus.REVERSED)

    def test_a_replacement_payment_is_not_dated_before_the_cancellation_it_replaces(self):
        run = services.post(self.make_run(), self.owner, posted_on=LAST_MONTH.isoformat())
        payment = services.pay(run, PayTo.TAX, self.cash, "", self.owner, paid_on=LAST_MONTH.isoformat())
        services.cancel_payment(payment, self.owner, "غلط", cancelled_on=TODAY.isoformat())
        with self.assertRaisesMessage(ValidationError, "قبل إلغاء دفعة"):
            services.pay(run, PayTo.TAX, self.cash, "", self.owner, paid_on=LAST_MONTH.isoformat())
        self.assertEqual(services.pay(run, PayTo.TAX, self.cash, "", self.owner, paid_on=TODAY.isoformat()).paid_on, TODAY)


class FourthReviewTests(PayrollSetup):
    """Codex on #269 (fourth look): no negative net pay is posted, and a reversed month is made again, corrected."""

    def test_a_negative_net_pay_is_not_posted(self):
        run = self.make_run()
        line = run.lines.get(employee=self.mona)
        services.change_line(line, {"other_deductions": "12000"}, self.owner)
        self.assertLess(run.lines.get(employee=self.mona).net, 0)  # insurance still withheld on a nothing salary
        with self.assertRaisesMessage(ValidationError, "منى"):
            services.post(run, self.owner)
        services.change_line(line, {"other_deductions": "0"}, self.owner)
        self.assertEqual(services.post(run, self.owner).status, RunStatus.POSTED)

    def test_a_reversed_month_is_made_again(self):
        first = services.post(self.make_run(), self.owner)
        with self.assertRaisesMessage(ValidationError, "اتعملت قبل كده"):
            self.make_run()
        services.reverse(first, self.owner, "غلط في المرتبات")
        again = self.make_run()
        self.assertNotEqual(again.pk, first.pk)
        self.assertEqual(services.post(again, self.owner).status, RunStatus.POSTED)
        with self.assertRaisesMessage(ValidationError, "اتعملت قبل كده"):
            self.make_run()  # one standing run a month
        Projector().rebuild()
        self.assertTrue(reports.trial_balance()["balanced"])
        self.assertEqual(PayrollRun.objects.filter(month=LAST_MONTH).count(), 2)

    def test_a_tax_bracket_rate_is_never_negative(self):
        for text in (":-10", "40000:0\n:-5", "40000:150\n:10"):
            with self.assertRaisesMessage(ValidationError, "شرائح", msg=text):
                services.save_rules({"tax_brackets": text}, self.owner)
