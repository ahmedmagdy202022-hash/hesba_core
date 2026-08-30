from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from cashboxes.models import CashboxMovement
from hesba_testing.factories import (
    make_cashbox,
    make_item,
    make_location,
    make_seeded_role,
    make_supplier,
    make_user,
    make_user_profile,
    purchase_ready,
)
from inventory.models import StockMovement
from permissions.models import RoleCode

from .models import PurchaseInvoice, SupplierLedgerEntry
from .services import post_purchase_invoice


class PurchaseUiTests(TestCase):
    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def draft_payload(self, paid_now="20.00"):
        supplier = make_supplier()
        location = make_location()
        cashbox = make_cashbox()
        item = make_item()
        payload = {
            "lang": "en",
            "invoice_number": "PI-UI-001",
            "invoice_date": "2026-01-15",
            "supplier": supplier.pk,
            "receiving_location": location.pk,
            "cashbox": cashbox.pk,
            "discount_amount": "5.00",
            "tax_amount": "5.00",
            "paid_now": paid_now,
            "notes": "UI draft",
            "lines-TOTAL_FORMS": "5",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "20",
            "lines-0-item": item.pk,
            "lines-0-description": "First line",
            "lines-0-quantity": "2.000",
            "lines-0-unit_purchase_price": "25.00",
            "lines-0-line_discount_amount": "0.00",
        }
        return payload

    def test_anonymous_route_redirects_to_login(self):
        response = self.client.get(reverse("purchases:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_cashier_cannot_view_purchase_invoices(self):
        self.login_as(RoleCode.CASHIER, "purchase_cashier")
        response = self.client.get(reverse("purchases:list"))
        self.assertEqual(response.status_code, 403)

    def test_owner_creates_consistent_draft_with_server_totals(self):
        self.login_as(RoleCode.OWNER, "purchase_owner")
        response = self.client.post(reverse("purchases:create"), self.draft_payload())
        self.assertEqual(response.status_code, 302)
        invoice = PurchaseInvoice.objects.get(invoice_number="PI-UI-001")
        self.assertEqual(invoice.subtotal, Decimal("50.00"))
        self.assertEqual(invoice.total_amount, Decimal("50.00"))
        self.assertEqual(invoice.paid_now, Decimal("20.00"))
        self.assertEqual(invoice.remaining_due, Decimal("30.00"))
        self.assertEqual(invoice.lines.get().line_total_amount, Decimal("50.00"))
        self.assertFalse(StockMovement.objects.exists())

    def test_paid_draft_requires_cashbox(self):
        self.login_as(RoleCode.OWNER, "purchase_no_cashbox")
        payload = self.draft_payload()
        payload["cashbox"] = ""
        response = self.client.post(reverse("purchases:create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PurchaseInvoice.objects.exists())
        self.assertContains(response, "Cashbox is required")

    def test_fractional_cent_purchase_line_rounds_half_up_per_line(self):
        self.login_as(RoleCode.OWNER, "purchase_fraction")
        payload = self.draft_payload(paid_now="0.00")
        payload["lines-0-quantity"] = "1.001"
        payload["lines-0-unit_purchase_price"] = "1.01"
        response = self.client.post(reverse("purchases:create"), payload)
        self.assertEqual(response.status_code, 302)
        invoice = PurchaseInvoice.objects.get()
        self.assertEqual(invoice.lines.get().line_total_amount, Decimal("1.01"))
        self.assertEqual(invoice.subtotal, Decimal("1.01"))

    def test_post_route_calls_existing_service_and_creates_all_side_effects(self):
        user = self.login_as(RoleCode.OWNER, "purchase_post")
        invoice, item, location, cashbox = purchase_ready(paid_now="25.00")
        response = self.client.post(
            reverse("purchases:post", args=[invoice.pk]), {"lang": "en"}
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(SupplierLedgerEntry.objects.get().due_increase, Decimal("75.00"))
        self.assertEqual(CashboxMovement.objects.get().amount, Decimal("25.00"))
        self.assertEqual(StockMovement.objects.get().quantity, Decimal("4.000"))
        self.assertEqual(StockMovement.objects.get().created_by, user)

    def test_repeated_post_is_rejected_without_duplicate_effects(self):
        self.login_as(RoleCode.OWNER, "purchase_repeat")
        invoice, *_ = purchase_ready(paid_now="25.00")
        post_purchase_invoice(invoice.pk)
        response = self.client.post(reverse("purchases:post", args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SupplierLedgerEntry.objects.count(), 1)
        self.assertEqual(CashboxMovement.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_cancel_route_reverses_supplier_cashbox_and_stock(self):
        self.login_as(RoleCode.OWNER, "purchase_cancel")
        invoice, *_ = purchase_ready(paid_now="25.00")
        post_purchase_invoice(invoice.pk)
        response = self.client.post(
            reverse("purchases:cancel", args=[invoice.pk]),
            {"reason": "Wrong document", "lang": "en"},
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "cancelled")
        self.assertEqual(SupplierLedgerEntry.objects.count(), 2)
        self.assertEqual(CashboxMovement.objects.count(), 2)
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_english_purchase_routes_render(self):
        self.login_as(RoleCode.OWNER, "purchase_english")
        response = self.client.get(reverse("purchases:list"), {"lang": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Purchase invoices")
