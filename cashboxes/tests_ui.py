from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .models import CashboxDirection


class CashboxUiTests(TestCase):
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

    def test_direct_movement_and_transfer_actions_are_not_offered(self):
        self.login_as(RoleCode.OWNER, "cashbox_blocked")
        make_cashbox()
        response = self.client.get(reverse("cashboxes:list"), {"lang": "en"})
        self.assertContains(response, "Direct cash movements and transfers remain unavailable")
        self.assertNotContains(response, "name=\"direct_cash\"")
        self.assertNotContains(response, "name=\"cashbox_transfer\"")

