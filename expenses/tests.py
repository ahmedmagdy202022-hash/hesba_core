"""EXP-001: operating expenses pay out of a cashbox and come off profit."""

from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from closing.models import Period
from cashboxes.models import CashboxDirection, CashboxMovementType, CashboxOperation, CashboxOperationType
from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import (
    make_cashbox,
    make_cashbox_movement,
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    stock_in,
)
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client, sell
from settings_core.setup_services import set_module_enabled

from .models import Expense, ExpenseCategory
from .services import cancel_expense, expense_total, record_expense


COMMERCIAL_MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports"


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def open_period():
    today = timezone.localdate()
    Period.objects.get_or_create(
        period_code="EXP-TEST",
        defaults={"name": "Expense tests", "start_date": today - timedelta(days=400), "end_date": today + timedelta(days=30)},
    )


def funded_cashbox(amount="1000.00", code="CASH-EXP"):
    open_period()
    cashbox = make_cashbox(cashbox_code=code, name_ar="الخزنة الرئيسية", is_default=True)
    make_cashbox_movement(cashbox, CashboxDirection.IN, amount)
    return cashbox


class SeedTests(TestCase):
    def test_default_categories_and_permissions_are_seeded(self):
        self.assertTrue(ExpenseCategory.objects.filter(code="rent", name_ar="إيجار").exists())
        self.assertEqual(ExpenseCategory.objects.count(), 10)
        from permissions.services import user_has_permission

        expected = {
            RoleCode.OWNER: (True, True),
            RoleCode.ACCOUNTANT: (True, True),
            RoleCode.MANAGER: (True, False),
            RoleCode.CASHIER: (False, False),
            RoleCode.STOCK_KEEPER: (False, False),
            RoleCode.SUPPORT: (False, False),
        }
        for role_code, (view, record) in expected.items():
            with self.subTest(role=role_code):
                user = person(role_code, f"seed_{role_code}")
                self.assertEqual(user_has_permission(user, "cashboxes.view_expenses"), view)
                self.assertEqual(user_has_permission(user, "cashboxes.record_expenses"), record)


class ExpenseServiceTests(TestCase):
    def setUp(self):
        self.owner = person(RoleCode.OWNER, "exp_owner")
        self.cashbox = funded_cashbox()
        self.rent = ExpenseCategory.objects.get(code="rent")

    def record(self, amount="250.00", **kwargs):
        values = dict(
            category=self.rent,
            cashbox=self.cashbox,
            amount=D(amount),
            expense_date=timezone.localdate(),
            description="إيجار شهر سبتمبر",
            user=self.owner,
        )
        values.update(kwargs)
        return record_expense(**values)

    def test_recording_pays_out_of_the_cashbox_through_a_direct_out(self):
        expense = self.record("250.00", payee="صاحب العقار")

        self.assertEqual(get_cashbox_balance(self.cashbox), D("750.00"))
        self.assertEqual(expense.expense_number, "EXP-000001")
        operation = expense.cashbox_operation
        self.assertEqual(operation.operation_type, CashboxOperationType.DIRECT_OUT)
        self.assertEqual(operation.reference_number, expense.expense_number)
        movement = operation.movements.get()
        self.assertEqual((movement.direction, movement.movement_type, movement.amount), (CashboxDirection.OUT, CashboxMovementType.DIRECT_OUT, D("250.00")))
        self.assertTrue(expense.is_posted)
        log = AuditLog.objects.get(module="expenses", action="record_expense")
        self.assertEqual((log.actor, log.after_data["amount"], log.after_data["category"]), (self.owner, "250.00", "rent"))

    def test_amount_is_rounded_to_money(self):
        expense = self.record("99.995")
        self.assertEqual(expense.amount, D("100.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("900.00"))

    def test_the_cashbox_can_never_go_negative(self):
        with self.assertRaises(ValidationError):
            self.record("1000.01")
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(CashboxOperation.objects.count(), 0)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("1000.00"))

    def test_missing_description_zero_amount_and_inactive_category_are_refused(self):
        with self.assertRaises(ValidationError):
            self.record(description="  ")
        with self.assertRaises(ValidationError):
            self.record("0")
        self.rent.active = False
        self.rent.save()
        with self.assertRaises(ValidationError):
            self.record()
        self.assertEqual(get_cashbox_balance(self.cashbox), D("1000.00"))

    def test_cancelling_returns_the_money_with_an_inverse_row(self):
        expense = self.record("300.00")
        cancel_expense(expense.pk, reversal_date=timezone.localdate(), reason="اتسجل بالغلط", user=self.owner)

        expense.refresh_from_db()
        self.assertFalse(expense.is_posted)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("1000.00"))
        movements = expense.cashbox_operation.movements.order_by("id")
        self.assertEqual(movements.count(), 2)
        self.assertEqual(movements[1].reversal_of, movements[0])
        self.assertEqual(movements[1].direction, CashboxDirection.IN)
        self.assertTrue(AuditLog.objects.filter(module="expenses", action="cancel_expense").exists())
        with self.assertRaises(ValidationError):
            cancel_expense(expense.pk, reversal_date=timezone.localdate(), reason="تاني", user=self.owner)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("1000.00"))

    def test_totals_count_only_posted_expenses_in_the_window(self):
        today = timezone.localdate()
        self.record("100.00")
        self.record("40.00", expense_date=today - timedelta(days=40))
        cancelled = self.record("60.00")
        cancel_expense(cancelled.pk, reversal_date=today, reason="غلط", user=self.owner)

        self.assertEqual(expense_total(), D("140.00"))
        self.assertEqual(expense_total(date_from=today - timedelta(days=7)), D("100.00"))
        self.assertEqual(expense_total(date_to=today - timedelta(days=30)), D("40.00"))

    def test_roles_without_record_permission_are_refused(self):
        for role_code in (RoleCode.MANAGER, RoleCode.CASHIER, RoleCode.STOCK_KEEPER):
            with self.subTest(role=role_code):
                with self.assertRaises(PermissionDenied):
                    self.record(user=person(role_code, f"no_{role_code}"))
        accountant = person(RoleCode.ACCOUNTANT, "exp_accountant")
        self.record("10.00", user=accountant)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("990.00"))

    def test_numbers_are_sequential(self):
        numbers = [self.record("1.00").expense_number for _ in range(3)]
        self.assertEqual(numbers, ["EXP-000001", "EXP-000002", "EXP-000003"])


class ExpenseScreenTests(TestCase):
    def setUp(self):
        prepared_client(modules=COMMERCIAL_MODULES)
        self.owner = person(RoleCode.OWNER, "screen_owner")
        self.cashbox = funded_cashbox("500.00")
        self.client.force_login(self.owner)

    def test_owner_records_an_expense_from_the_form(self):
        rent = ExpenseCategory.objects.get(code="rent")
        response = self.client.post(
            reverse("expenses:create"),
            {
                "expense_date": timezone.localdate().isoformat(),
                "category": rent.pk,
                "cashbox": self.cashbox.pk,
                "amount": "120.50",
                "payee": "",
                "description": "إيجار المحل",
            },
        )
        self.assertRedirects(response, "/expenses/?lang=ar", fetch_redirect_response=False)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("379.50"))
        page = self.client.get(reverse("expenses:list"))
        self.assertContains(page, "إيجار المحل")
        self.assertContains(page, "120.50")
        self.assertContains(page, "إجمالي المصروفات")

    def test_form_shows_the_refusal_when_cash_is_short(self):
        rent = ExpenseCategory.objects.get(code="rent")
        response = self.client.post(
            reverse("expenses:create"),
            {"expense_date": timezone.localdate().isoformat(), "category": rent.pk, "cashbox": self.cashbox.pk, "amount": "900", "description": "كبير"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertEqual(Expense.objects.count(), 0)

    def test_cancel_from_the_list(self):
        expense = record_expense(category=ExpenseCategory.objects.get(code="utilities"), cashbox=self.cashbox, amount=D("50"), expense_date=timezone.localdate(), description="كهرباء", user=self.owner)
        self.client.post(reverse("expenses:cancel", args=[expense.pk]), {"reason": "غلط"})
        self.assertEqual(get_cashbox_balance(self.cashbox), D("500.00"))
        self.assertContains(self.client.get(reverse("expenses:list")), "ملغي")

    def test_english_page(self):
        response = self.client.get(reverse("expenses:list"), {"lang": "en"})
        self.assertContains(response, "Total expenses")
        self.assertContains(response, "Record expense")

    def test_manager_reads_but_cannot_post(self):
        self.client.force_login(person(RoleCode.MANAGER, "screen_manager"))
        page = self.client.get(reverse("expenses:list"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, reverse("expenses:create"))
        self.assertEqual(self.client.get(reverse("expenses:create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("expenses:categories"), {"name_ar": "بند"}).status_code, 403)

    def test_cashier_cannot_open_expenses_and_sees_no_link(self):
        self.client.force_login(person(RoleCode.CASHIER, "screen_cashier"))
        self.assertEqual(self.client.get(reverse("expenses:list")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("sales:list")), 'href="/expenses/')

    def test_owner_navigation_has_expenses(self):
        self.assertContains(self.client.get(reverse("sales:list")), 'href="/expenses/?lang=ar"')

    def test_switched_off_module_closes_the_screens(self):
        from settings_core.models import ClientProfile

        set_module_enabled(ClientProfile.get_active(), "expenses", False, user=self.owner)
        response = self.client.get(reverse("expenses:list"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "غير مفعّل", status_code=403)

    def test_categories_can_be_added_and_retired(self):
        self.client.post(reverse("expenses:categories"), {"name_ar": "عمولات", "name_en": "Commissions"})
        category = ExpenseCategory.objects.get(name_ar="عمولات")
        self.client.post(reverse("expenses:category_toggle", args=[category.pk]))
        category.refresh_from_db()
        self.assertFalse(category.active)
        form_page = self.client.get(reverse("expenses:create"))
        self.assertNotContains(form_page, "عمولات")

    def test_cashbox_screen_cannot_reverse_an_expense_behind_its_back(self):
        expense = record_expense(category=ExpenseCategory.objects.get(code="rent"), cashbox=self.cashbox, amount=D("70"), expense_date=timezone.localdate(), description="إيجار", user=self.owner)
        operations = self.client.get(reverse("cashboxes:operations"))
        self.assertNotContains(operations, reverse("cashboxes:operation_cancel", args=[expense.cashbox_operation_id]))
        self.client.post(reverse("cashboxes:operation_cancel", args=[expense.cashbox_operation_id]), {"reversal_date": timezone.localdate().isoformat(), "reason": "x"})
        expense.refresh_from_db()
        self.assertTrue(expense.is_posted)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("430.00"))


class NetProfitTests(TestCase):
    def setUp(self):
        prepared_client(modules=COMMERCIAL_MODULES)
        self.owner = person(RoleCode.OWNER, "profit_owner")
        self.client.force_login(self.owner)

    def test_profit_report_shows_gross_expenses_and_net(self):
        location = make_location()
        cashbox = funded_cashbox("0.00", code="CASH-NET")
        item = make_item(item_code="NET-1")
        stock_in(item, location, 10, unit_cost="6.00")
        sell(item, location, cashbox, 5, "10.00", "50.00", self.owner, number="SI-NET")
        # Gross profit: 5 x (10 - 6) = 20.00. The sale put 50.00 into the cashbox.
        record_expense(category=ExpenseCategory.objects.get(code="utilities"), cashbox=cashbox, amount=D("15.00"), expense_date=timezone.localdate(), description="كهرباء", user=self.owner)

        response = self.client.get(reverse("reports:profit"))
        totals = response.context["totals"]
        self.assertEqual((totals["sales"], totals["cost"], totals["profit"]), (D("50.00"), D("30.00"), D("20.00")))
        self.assertEqual(totals["expenses"], D("15.00"))
        self.assertEqual(totals["net_profit"], D("5.00"))
        self.assertContains(response, "صافي الربح")
        self.assertContains(response, "مجمل الربح")

    def test_viewer_without_expense_permission_sees_the_old_report(self):
        user = make_user(username="profit_only")
        from hesba_testing.factories import grant, make_permission, make_role
        from permissions.models import Permission

        role = make_role(code="profit_viewer")
        grant(role, Permission.objects.get(code="reports.view_profit_report"))
        make_user_profile(user=user, role=role)
        self.client.force_login(user)
        response = self.client.get(reverse("reports:profit"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("expenses", response.context["totals"])
        self.assertNotContains(response, "صافي الربح")
