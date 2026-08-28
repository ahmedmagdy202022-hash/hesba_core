from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from cashboxes.models import CashboxDirection, CashboxMovement
from hesba_testing.factories import make_cashbox, make_customer, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .models import CustomerLedgerEntry, CustomerPayment


class CustomerPaymentUiTests(TestCase):
    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def payload(self):
        return {"lang": "en", "payment_number": "CP-UI-1", "payment_date": "2026-01-15", "customer": make_customer().pk, "cashbox": make_cashbox().pk, "amount": "30.00", "notes": "collection"}

    def test_cashier_records_atomic_customer_collection(self):
        user = self.login_as(RoleCode.CASHIER, "customer_collection")
        response = self.client.post(reverse("sales:payment_create"), self.payload())
        self.assertEqual(response.status_code, 302)
        payment = CustomerPayment.objects.get()
        self.assertEqual(payment.created_by, user)
        self.assertEqual(CustomerLedgerEntry.objects.get().due_decrease, Decimal("30.00"))
        movement = CashboxMovement.objects.get()
        self.assertEqual(movement.direction, CashboxDirection.IN)
        self.assertEqual(movement.amount, Decimal("30.00"))

    def test_stock_keeper_without_collection_permission_is_denied(self):
        self.login_as(RoleCode.STOCK_KEEPER, "collection_denied")
        self.assertEqual(self.client.get(reverse("sales:payment_create")).status_code, 403)

    def test_cancel_reverses_customer_and_cash_once(self):
        self.login_as(RoleCode.CASHIER, "collection_cancel")
        self.client.post(reverse("sales:payment_create"), self.payload())
        payment = CustomerPayment.objects.get()
        self.client.post(reverse("sales:payment_cancel", args=[payment.pk]), {"reason": "Wrong"})
        self.client.post(reverse("sales:payment_cancel", args=[payment.pk]), {"reason": "Again"})
        payment.refresh_from_db()
        self.assertEqual(payment.status, "cancelled")
        self.assertEqual(CustomerLedgerEntry.objects.count(), 2)
        self.assertEqual(CashboxMovement.objects.count(), 2)

    def test_payment_history_renders_english_context(self):
        self.login_as(RoleCode.CASHIER, "collection_history")
        self.client.post(reverse("sales:payment_create"), self.payload())
        response = self.client.get(reverse("sales:payments"), {"lang": "en"})
        self.assertContains(response, "Customer collections")
        self.assertContains(response, "CP-UI-1")

