from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .models import CashboxDirection


class CashboxUiTests(TestCase):
    def setUp(self):
        Period.objects.create(
            period_code="2026-CASH-UI",
            name="2026 cash UI",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )

    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def test_cashier_sees_identity_but_not_finance(self):
        self.login_as(RoleCode.CASHIER, "cashbox_cashier")
        cashbox = make_cashbox(opening_balance=Decimal("9000.00"))
        make_cashbox_movement(cashbox, CashboxDirection.IN, "250.00")
        response = self.client.get(reverse("cashboxes:list"), {"lang": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_finance"])
        self.assertContains(response, cashbox.cashbox_code)
        self.assertNotContains(response, "9000.00")
        self.assertNotContains(response, "250.00")

    def test_accountant_sees_report_derived_balance(self):
        self.login_as(RoleCode.ACCOUNTANT, "cashbox_accountant")
        cashbox = make_cashbox(opening_balance=Decimal("100.00"))
        make_cashbox_movement(cashbox, CashboxDirection.IN, "50.00")
        make_cashbox_movement(cashbox, CashboxDirection.OUT, "20.00")
        response = self.client.get(reverse("cashboxes:detail", args=[cashbox.pk]), {"lang": "en"})
        self.assertTrue(response.context["can_view_finance"])
        self.assertEqual(response.context["finance"]["balance"], Decimal("130.00"))
        self.assertContains(response, "Cashbox movement history")

    def test_cashier_cannot_open_financial_movement_history(self):
        self.login_as(RoleCode.CASHIER, "cashbox_history_denied")
        self.assertEqual(self.client.get(reverse("cashboxes:movements")).status_code, 403)

    def test_stock_keeper_without_cashbox_permission_is_denied(self):
        self.login_as(RoleCode.STOCK_KEEPER, "cashbox_stock_keeper")
        self.assertEqual(self.client.get(reverse("cashboxes:list")).status_code, 403)

    def test_owner_is_offered_protected_cash_operation_actions(self):
        self.login_as(RoleCode.OWNER, "cashbox_operations")
        make_cashbox(opening_balance=Decimal("100.00"))
        response = self.client.get(reverse("cashboxes:list"), {"lang": "en"})
        self.assertContains(response, "Record cash operation")
        self.assertContains(response, reverse("cashboxes:operation_create"))

    def test_accountant_posts_and_reverses_direct_cash_operation(self):
        self.login_as(RoleCode.ACCOUNTANT, "cashbox_operation_ui")
        cashbox = make_cashbox(opening_balance=Decimal("100.00"))
        response = self.client.post(
            reverse("cashboxes:operation_create"),
            {
                "lang": "en",
                "reference_number": "UI-CASH-1",
                "operation_date": "2026-04-10",
                "operation_type": "direct_out",
                "source_cashbox": cashbox.pk,
                "destination_cashbox": "",
                "amount": "10.00",
                "reason": "Office supplies",
            },
        )
        self.assertEqual(response.status_code, 302)
        operation = cashbox.cash_operations_out.get()
        response = self.client.post(
            reverse("cashboxes:operation_cancel", args=[operation.pk]),
            {"reversal_date": "2026-04-11", "reason": "Entry duplicated", "lang": "en"},
        )
        self.assertEqual(response.status_code, 302)
        operation.refresh_from_db()
        self.assertEqual(operation.status, "cancelled")
        self.assertEqual(operation.movements.count(), 2)

    def test_manager_cannot_open_cash_operation_route(self):
        self.login_as(RoleCode.MANAGER, "cash_operation_manager_ui")
        self.assertEqual(
            self.client.get(reverse("cashboxes:operation_create")).status_code, 403
        )
