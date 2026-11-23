from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from cashboxes.models import CashboxDirection, CashboxMovement
from hesba_testing.factories import make_cashbox, make_seeded_role, make_supplier, make_user, make_user_profile
from permissions.models import RoleCode

from .models import SupplierLedgerEntry, SupplierPayment


class SupplierPaymentUiTests(TestCase):
    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def payload(self):
        return {"lang": "en", "payment_number": "SP-UI-1", "payment_date": "2026-01-15", "supplier": make_supplier().pk, "cashbox": make_cashbox().pk, "amount": "25.00", "notes": "settlement"}

    def test_accountant_records_atomic_supplier_payment(self):
        user = self.login_as(RoleCode.ACCOUNTANT, "supplier_payment")
        response = self.client.post(reverse("purchases:payment_create"), self.payload())
        self.assertEqual(response.status_code, 302)
        payment = SupplierPayment.objects.get()
        self.assertEqual(payment.created_by, user)
        self.assertEqual(SupplierLedgerEntry.objects.get().due_decrease, Decimal("25.00"))
        movement = CashboxMovement.objects.get()
        self.assertEqual(movement.direction, CashboxDirection.OUT)
        self.assertEqual(movement.amount, Decimal("25.00"))

    def test_manager_without_pay_permission_is_denied(self):
        self.login_as(RoleCode.MANAGER, "supplier_payment_denied")
        self.assertEqual(self.client.get(reverse("purchases:payment_create")).status_code, 403)

    def test_invalid_amount_creates_no_effects(self):
        self.login_as(RoleCode.ACCOUNTANT, "supplier_payment_invalid")
        payload = self.payload()
        payload["amount"] = "0.00"
        response = self.client.post(reverse("purchases:payment_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SupplierPayment.objects.exists())
        self.assertFalse(SupplierLedgerEntry.objects.exists())
        self.assertFalse(CashboxMovement.objects.exists())

    def test_cancel_reverses_ledger_and_cash_once(self):
        self.login_as(RoleCode.ACCOUNTANT, "supplier_payment_cancel")
        self.client.post(reverse("purchases:payment_create"), self.payload())
        payment = SupplierPayment.objects.get()
        self.client.post(reverse("purchases:payment_cancel", args=[payment.pk]), {"reason": "Wrong"})
        self.client.post(reverse("purchases:payment_cancel", args=[payment.pk]), {"reason": "Again"})
        payment.refresh_from_db()
        self.assertEqual(payment.status, "cancelled")
        self.assertEqual(SupplierLedgerEntry.objects.count(), 2)
        self.assertEqual(CashboxMovement.objects.count(), 2)

    def test_payment_history_requires_visible_operator_cancellation_reason(self):
        self.login_as(RoleCode.ACCOUNTANT, "supplier_payment_reason")
        self.client.post(reverse("purchases:payment_create"), self.payload())
        response = self.client.get(reverse("purchases:payments"), {"lang": "en"})
        self.assertContains(response, 'name="reason" required')
        self.assertNotContains(response, 'type="hidden" name="reason"')
