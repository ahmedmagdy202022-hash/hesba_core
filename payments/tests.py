"""S4 (HG-070): the online payment link (Paymob first), with Paymob's real code and a mock transport (no network).

Every money test asserts the customer's ledger, the «بوابة الدفع» bank account, the settlement row, what the invoice
still asks for, and the ledger's reconciliation after ``Projector().rebuild()``.
"""

import hashlib
import hmac as hmac_lib
import json
import socket
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from ledger import reports as ledger_reports
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.digest import daily_digest
from reports.functional_views import _posted_totals
from sales.models import CustomerPayment, SalesInvoice
from sales.pos import WALK_IN_CODE, today_summary
from sales.services import cancel_posted_sales_invoice, create_sales_draft, post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settlements.models import InvoiceSettlement

from . import providers, services
from .models import GatewayTransaction, LinkStatus, PaymentLink, TransactionOutcome
from .providers.mock import MockPaymob
from .providers.paymob import HMAC_FIELDS, PaymobProvider, hmac_message, sign

TODAY = timezone.localdate()
ZERO = D("0.00")
BASE = "https://pay.example.test"
GATEWAY_SETTINGS = {"PAYMENT_GATEWAY_MOCK": True, "PAYMOB_LIVE": False, "PUBLIC_BASE_URL": BASE,
                    "PAYMOB_API_KEY": "", "PAYMOB_HMAC_SECRET": "", "PAYMOB_INTEGRATION_ID": "", "PAYMOB_IFRAME_ID": ""}


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def customer_balance(customer):
    agg = customer.ledger_entries.aggregate(i=Sum("due_increase"), d=Sum("due_decrease"))
    return customer.opening_balance + (agg["i"] or D("0")) - (agg["d"] or D("0"))


def box_balance(cashbox):
    moves = CashboxMovement.objects.filter(cashbox=cashbox)
    return (moves.filter(direction="in").aggregate(t=Sum("amount"))["t"] or D("0")) - (moves.filter(direction="out").aggregate(t=Sum("amount"))["t"] or D("0"))


def bal(code):
    Projector().rebuild()
    agg = JournalLine.objects.filter(account__code=code).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


@override_settings(**GATEWAY_SETTINGS)
class PayLinkSetup(TestCase):
    activity, sub_activity, modules = "commercial", "retail", "customers,items_services,sales_operations,cashboxes,reports"

    def setUp(self):
        from reports.tests_dashboard import prepared_client

        self.profile = prepared_client(self.activity, self.sub_activity, self.modules)
        self.owner = person(RoleCode.OWNER, "pl_owner")
        self.cashier = person(RoleCode.CASHIER, "pl_cashier")
        self.keeper = person(RoleCode.STOCK_KEEPER, "pl_keeper")
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cash = make_cashbox(cashbox_code="CASH", is_default=True)
        self.gateway = make_cashbox(cashbox_code="GW", name_ar="بوابة الدفع – Paymob", name_en="Payment gateway", kind="bank")
        self.bank = make_cashbox(cashbox_code="BANK", name_ar="البنك الأهلي", name_en="NBE", kind="bank")
        self.customer = make_customer(customer_code="C-PL", name="مدام سعاد", phone="01001234567")
        self.service = make_item(item_code="SRV-PL", item_name="خدمة", is_stock_tracked=False)
        set_capability_enabled(self.profile, "payment_links", True, user=self.owner)  # off by default
        self.number = 0

    def invoice(self, total="1000.00", customer=None, location=None):
        self.number += 1
        draft = create_sales_draft(
            {"invoice_number": f"SI-PL-{self.number}", "invoice_date": TODAY, "customer": customer or self.customer, "selling_location": location or self.location,
             "cashbox": None, "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO},
            [{"item": self.service, "quantity": D("1"), "unit_sale_price": D(total), "line_discount_amount": ZERO, "description": "خدمة"}],
            self.owner,
        )
        post_sales_invoice(draft.pk, self.owner)
        draft.refresh_from_db()
        return draft

    def provider(self):
        provider = providers.active()
        self.assertIsInstance(provider, MockPaymob)
        return provider

    def callback(self, link, amount_cents=None, client=None, **flags):
        """POST a Paymob-shaped, signed transaction callback to the webhook, as Paymob would."""

        payload, signature = self.provider().callback(link.gateway_order_id, services.to_cents(link.amount) if amount_cents is None else amount_cents,
                                                      merchant_reference=link.reference, **flags)
        return self.post_callback(payload, signature, client), payload

    def post_callback(self, payload, signature, client=None):
        client = client or Client(enforce_csrf_checks=True)  # proves the webhook is CSRF-exempt
        return client.post(f"{reverse('pay_webhook_paymob')}?hmac={signature}", data=json.dumps(payload), content_type="application/json")

    def money(self, invoice):
        invoice.refresh_from_db()
        return {"customer": customer_balance(self.customer), "gateway": box_balance(self.gateway), "payments": CustomerPayment.objects.count(),
                "settled": InvoiceSettlement.objects.filter(invoice=invoice).aggregate(t=Sum("amount"))["t"] or ZERO, "outstanding": services.outstanding(invoice)}


class LinkTests(PayLinkSetup):
    def test_a_link_is_for_the_outstanding_amount_built_from_public_base_url_and_audited(self):
        invoice = self.invoice("1000.00")
        link = services.create_link(invoice, self.owner)
        self.assertEqual((link.amount, link.status, link.cashbox, link.customer, link.provider), (D("1000.00"), LinkStatus.NEW, self.gateway, self.customer, "paymob_mock"))
        self.assertTrue(link.gateway_order_id and link.checkout_url)
        self.assertGreater(link.expires_at, timezone.now() + timedelta(hours=71))
        url = services.link_url(link)
        self.assertTrue(url.startswith(f"{BASE}/pay/"))
        self.assertEqual(services.read_token(url.split("/pay/")[1].split("/")[0]), link)
        self.assertIsNone(services.read_token("forged" + url.split("/pay/")[1].split("/")[0]))
        self.assertTrue(AuditLog.objects.filter(module="payments", action="create_link", object_id=str(link.pk)).exists())
        self.assertEqual(self.money(invoice)["payments"], 0)  # a link moves no money

    @override_settings(ALLOWED_HOSTS=["testserver", "evil.example.com"])
    def test_the_staff_page_shows_the_link_from_public_base_url_never_the_request_host(self):
        invoice = self.invoice("1000.00")
        self.client.force_login(self.owner)
        response = self.client.post(reverse("payments:create", args=[invoice.pk]), {"lang": "ar", "amount": "1000"}, HTTP_HOST="evil.example.com")
        self.assertEqual(response.status_code, 302)
        page = self.client.get(f"{reverse('sales:detail', args=[invoice.pk])}?lang=ar")
        self.assertContains(page, "data-pay-link-live")
        self.assertContains(page, f'data-copy-link="{BASE}/pay/')
        self.assertNotContains(page, "evil.example.com")
        self.assertNotContains(page, "testserver/pay/")

    def test_one_live_link_and_the_cap(self):
        invoice = self.invoice("1000.00")
        with self.assertRaisesMessage(ValidationError, "أكبر من الباقي"):
            services.create_link(invoice, self.owner, "1000.01")
        link = services.create_link(invoice, self.owner, "400")
        with self.assertRaisesMessage(ValidationError, "لينك دفع لسه شغال"):
            services.create_link(invoice, self.owner, "100")
        self.callback(link)  # paid 400
        self.assertEqual(services.outstanding(invoice), D("600.00"))
        with self.assertRaisesMessage(ValidationError, "more than what is left"):
            services.create_link(invoice, self.owner, "600.01", lang="en")
        second = services.create_link(invoice, self.owner)
        self.assertEqual(second.amount, D("600.00"))

    def test_refusals(self):
        invoice = self.invoice()
        walk_in = make_customer(customer_code=WALK_IN_CODE, name="عميل نقدي")
        with self.assertRaisesMessage(ValidationError, "walk-in"):
            services.create_link(self.invoice(customer=walk_in), self.owner, lang="en")
        for bad in ("0", "-5", "abc"):
            with self.subTest(bad=bad), self.assertRaisesMessage(ValidationError, "مبلغ اللينك مش صحيح"):
                services.create_link(invoice, self.owner, bad)
        with override_settings(PUBLIC_BASE_URL=""), self.assertRaisesMessage(ValidationError, "PUBLIC_BASE_URL"):
            services.create_link(invoice, self.owner)
        with override_settings(PAYMENT_GATEWAY_MOCK=False), self.assertRaisesMessage(ValidationError, "غير موصّلة"):
            services.create_link(invoice, self.owner)
        draft = create_sales_draft({"invoice_number": "SI-DRAFT", "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location,
                                    "cashbox": None, "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO},
                                   [{"item": self.service, "quantity": D("1"), "unit_sale_price": D("10"), "line_discount_amount": ZERO}], self.owner)
        with self.assertRaisesMessage(ValidationError, "posted invoice only"):
            services.create_link(draft, self.owner, lang="en")
        self.gateway.active = False
        self.gateway.save()
        with self.assertRaisesMessage(ValidationError, "بوابة الدفع"):
            services.create_link(invoice, self.owner)
        self.assertFalse(PaymentLink.objects.exists())

    def test_a_gateway_that_refuses_makes_no_link(self):
        invoice = self.invoice()
        refusing = MockPaymob(hmac_secret="x", fail_with="Paymob answered 401.")
        with patch.object(providers, "active", return_value=refusing), self.assertRaisesMessage(ValidationError, "try again"):
            services.create_link(invoice, self.owner, lang="en")
        self.assertFalse(PaymentLink.objects.exists())

    def test_cancel_and_expiry(self):
        invoice = self.invoice()
        link = services.create_link(invoice, self.owner)
        services.cancel_link(link, self.owner)
        link.refresh_from_db()
        self.assertEqual(link.status, LinkStatus.EXPIRED)
        stale = services.create_link(invoice, self.owner)
        PaymentLink.objects.filter(pk=stale.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        token = services.sign_token(stale)
        public = Client()
        page = public.get(reverse("pay_page", args=[token]))
        self.assertContains(page, 'data-banner="expired"')
        self.assertNotContains(page, "data-pay-go")
        go = public.get(reverse("pay_go", args=[token]))
        self.assertEqual(go.status_code, 303)
        self.assertTrue(go["Location"].startswith(reverse("pay_page", args=[token])))  # back to the page, not to the gateway
        stale.refresh_from_db()
        self.assertEqual(stale.status, LinkStatus.EXPIRED)
        fresh = services.create_link(invoice, self.owner)  # an expired link no longer blocks a new one
        self.assertEqual(fresh.status, LinkStatus.NEW)
        self.assertEqual(public.get(reverse("pay_page", args=["not-a-token"])).status_code, 404)


class CallbackTests(PayLinkSetup):
    def test_pending_then_confirmed_posts_once(self):
        invoice = self.invoice("1000.00")
        link = services.create_link(invoice, self.owner)
        provider = self.provider()
        pending, signature = provider.callback(link.gateway_order_id, 100000, transaction_id=555001, success=False, pending=True)
        response = self.post_callback(pending, signature)
        self.assertEqual((response.status_code, response.json()["result"]), (200, "pending"))
        self.assertEqual(self.money(invoice), {"customer": D("1000.00"), "gateway": ZERO, "payments": 0, "settled": ZERO, "outstanding": D("1000.00")})
        self.assertEqual(GatewayTransaction.objects.get().outcome, TransactionOutcome.PENDING)
        confirmed, signature = provider.callback(link.gateway_order_id, 100000, transaction_id=555001)
        response = self.post_callback(confirmed, signature)
        self.assertEqual((response.status_code, response.json()["result"]), (200, "posted"))
        self.assertEqual(self.money(invoice), {"customer": ZERO, "gateway": D("1000.00"), "payments": 1, "settled": D("1000.00"), "outstanding": ZERO})
        payment = CustomerPayment.objects.get()
        self.assertEqual((payment.payment_number, payment.amount, payment.cashbox, payment.payment_date), ("MOCKPAY-555001", D("1000.00"), self.gateway, TODAY))
        txn = GatewayTransaction.objects.get()
        self.assertEqual((txn.outcome, txn.customer_payment, txn.hmac_ok, txn.needs_review), (TransactionOutcome.CONFIRMED, payment, True, False))
        link.refresh_from_db()
        self.assertEqual((link.status, link.paid_amount), (LinkStatus.PAID, D("1000.00")))
        self.assertEqual(InvoiceSettlement.objects.get().source, "payment_link")
        self.assertEqual(bal("1102"), D("1000.00"))  # the gateway account is a bank account of ours
        self.assertEqual(bal("1103"), ZERO)
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assertEqual(AuditLog.objects.filter(module="payments", action="callback").count(), 2)  # every callback is logged

    def test_a_double_confirmed_posts_once(self):
        invoice = self.invoice("250.00")
        link = services.create_link(invoice, self.owner)
        payload, signature = self.provider().callback(link.gateway_order_id, 25000, transaction_id=777)
        first, second = self.post_callback(payload, signature), self.post_callback(payload, signature)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(second.json()["result"], "repeat_confirmed")
        self.assertEqual(self.money(invoice), {"customer": ZERO, "gateway": D("250.00"), "payments": 1, "settled": D("250.00"), "outstanding": ZERO})
        self.assertEqual(GatewayTransaction.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(module="payments", action="callback").count(), 2)

    def test_confirmed_then_pending_changes_nothing(self):
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        provider = self.provider()
        self.post_callback(*provider.callback(link.gateway_order_id, 30000, transaction_id=901))
        before = self.money(invoice)
        late = self.post_callback(*provider.callback(link.gateway_order_id, 30000, transaction_id=901, success=False, pending=True))
        self.assertEqual((late.status_code, late.json()["result"]), (200, "late_pending_ignored"))
        self.assertEqual(self.money(invoice), before)
        self.assertEqual(GatewayTransaction.objects.get().outcome, TransactionOutcome.CONFIRMED)
        link.refresh_from_db()
        self.assertEqual(link.status, LinkStatus.PAID)

    def test_pending_twice_records_nothing(self):
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        payload, signature = self.provider().callback(link.gateway_order_id, 30000, transaction_id=902, success=False, pending=True)
        self.post_callback(payload, signature)
        again = self.post_callback(payload, signature)
        self.assertEqual(again.json()["result"], "repeat_pending")
        self.assertEqual(self.money(invoice)["payments"], 0)

    def test_failed_records_no_money_and_failed_after_confirmed_reverses_nothing(self):
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        provider = self.provider()
        failed = self.post_callback(*provider.callback(link.gateway_order_id, 30000, transaction_id=903, success=False, pending=False))
        self.assertEqual((failed.status_code, failed.json()["result"]), (200, "failed"))
        link.refresh_from_db()
        self.assertEqual(link.status, LinkStatus.FAILED)
        self.assertEqual(self.money(invoice), {"customer": D("300.00"), "gateway": ZERO, "payments": 0, "settled": ZERO, "outstanding": D("300.00")})
        page = Client().get(reverse("pay_page", args=[services.sign_token(link)]))
        self.assertContains(page, 'data-banner="failed"')
        self.assertNotContains(page, "data-pay-go")
        # A retry with a new transaction on the same order is confirmed: the money is recorded (the gateway took it).
        self.post_callback(*provider.callback(link.gateway_order_id, 30000, transaction_id=904))
        before = self.money(invoice)
        self.assertEqual(before["payments"], 1)
        refund = self.post_callback(*provider.callback(link.gateway_order_id, 30000, transaction_id=904, extra={"is_refunded": True}))
        self.assertEqual(refund.json()["result"], "flagged_after_confirmed")
        self.assertEqual(self.money(invoice), before)
        txn = GatewayTransaction.objects.get(transaction_id="904")
        self.assertEqual((txn.outcome, txn.needs_review), (TransactionOutcome.CONFIRMED, True))

    def test_failed_then_confirmed_on_the_same_transaction_is_flagged_not_posted(self):
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        provider = self.provider()
        self.post_callback(*provider.callback(link.gateway_order_id, 30000, transaction_id=906, success=False, pending=False))
        pending = self.post_callback(*provider.callback(link.gateway_order_id, 30000, transaction_id=906, success=False, pending=True))
        self.assertEqual(pending.json()["result"], "late_pending_ignored")
        confirmed = self.post_callback(*provider.callback(link.gateway_order_id, 30000, transaction_id=906))
        self.assertEqual((confirmed.status_code, confirmed.json()["result"]), (200, "flagged_after_failed"))
        txn = GatewayTransaction.objects.get()
        self.assertEqual((txn.outcome, txn.customer_payment, txn.needs_review), (TransactionOutcome.FAILED, None, True))
        self.assertEqual(self.money(invoice), {"customer": D("300.00"), "gateway": ZERO, "payments": 0, "settled": ZERO, "outstanding": D("300.00")})

    def test_a_forged_or_unsigned_callback_is_refused_and_writes_no_row(self):
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        payload, signature = self.provider().callback(link.gateway_order_id, 30000, transaction_id=905)
        forged = dict(payload, obj=dict(payload["obj"], amount_cents=3000000))
        for body, sig in ((forged, signature), (payload, ""), (payload, "0" * 128), (payload, sign(payload["obj"], "someone-elses-secret"))):
            with self.subTest(sig=sig[:8]):
                response = self.post_callback(body, sig)
                self.assertEqual(response.status_code, 400)
        bad_json = Client().post(f"{reverse('pay_webhook_paymob')}?hmac={signature}", data="not json", content_type="application/json")
        self.assertEqual(bad_json.status_code, 400)
        self.assertEqual(Client().get(reverse("pay_webhook_paymob")).status_code, 405)
        self.assertFalse(GatewayTransaction.objects.exists())
        self.assertEqual(self.money(invoice)["payments"], 0)
        self.assertEqual(AuditLog.objects.filter(module="payments", action="callback_rejected").count(), 5)
        # A server with no gateway connected refuses even a well-signed callback.
        with override_settings(PAYMENT_GATEWAY_MOCK=False):
            self.assertEqual(self.post_callback(payload, signature).status_code, 400)
        # A provider with an empty secret verifies nothing (hmac with b"" would let anyone sign).
        self.assertFalse(PaymobProvider("k", "", "1", "2").verify_callback(payload, sign(payload["obj"], "")))
        self.assertFalse(GatewayTransaction.objects.exists())

    def test_a_partial_link_settles_its_amount_and_counts_once(self):
        invoice = self.invoice("1000.00")
        link = services.create_link(invoice, self.owner, "400")
        self.callback(link)
        self.assertEqual(self.money(invoice), {"customer": D("600.00"), "gateway": D("400.00"), "payments": 1, "settled": D("400.00"), "outstanding": D("600.00")})
        invoice.refresh_from_db()
        self.assertEqual(invoice.remaining_due, D("1000.00"))  # the posted document is never rewritten
        totals = _posted_totals(SalesInvoice.objects.filter(pk=invoice.pk))
        self.assertEqual((totals["total"], totals["paid"], totals["applied"], totals["due"]), (D("1000.00"), ZERO, D("400.00"), D("600.00")))
        digest = daily_digest(self.owner, TODAY)
        self.assertEqual((digest["sales"]["credit"], digest["sales"]["cash"], digest["customers"]["collected"]), (D("600.00"), ZERO, D("400.00")))
        summary = today_summary(self.owner)
        self.assertEqual((summary["credit"], summary["applied"]), (D("600.00"), D("400.00")))
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_an_amount_different_from_the_link_records_what_was_taken(self):
        invoice = self.invoice("1000.00")
        link = services.create_link(invoice, self.owner, "400")
        response, _ = self.callback(link, amount_cents=45000)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.money(invoice), {"customer": D("550.00"), "gateway": D("450.00"), "payments": 1, "settled": D("450.00"), "outstanding": D("550.00")})
        link.refresh_from_db()
        self.assertEqual((link.status, link.paid_amount), (LinkStatus.MISMATCH, D("450.00")))
        self.assertTrue(GatewayTransaction.objects.get().needs_review)
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_more_than_the_outstanding_settles_the_invoice_and_leaves_the_rest_on_account(self):
        invoice = self.invoice("1000.00")
        link = services.create_link(invoice, self.owner)
        self.callback(link, amount_cents=120000)
        self.assertEqual(self.money(invoice), {"customer": D("-200.00"), "gateway": D("1200.00"), "payments": 1, "settled": D("1000.00"), "outstanding": ZERO})
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_a_link_paid_after_the_invoice_was_settled_otherwise_stays_on_account(self):
        invoice = self.invoice("500.00")
        old = services.create_link(invoice, self.owner)
        services.cancel_link(old, self.owner)
        new = services.create_link(invoice, self.owner)
        self.callback(new)
        response, _ = self.callback(old, transaction_id=990)  # the customer also paid the cancelled link
        self.assertEqual(response.json()["result"], "posted_settled_0.00")
        self.assertEqual(self.money(invoice), {"customer": D("-500.00"), "gateway": D("1000.00"), "payments": 2, "settled": D("500.00"), "outstanding": ZERO})
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_the_unique_payment_number_is_a_second_guard(self):
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        CustomerPayment.objects.create(payment_number="MOCKPAY-4242", payment_date=TODAY, customer=self.customer, cashbox=self.cash, amount=D("1.00"))
        response, _ = self.callback(link, transaction_id=4242)
        self.assertEqual((response.status_code, response.json()["result"]), (200, "posting_failed"))
        self.assertEqual(CustomerPayment.objects.count(), 1)
        self.assertEqual(box_balance(self.gateway), ZERO)
        txn = GatewayTransaction.objects.get()
        self.assertEqual((txn.customer_payment, txn.needs_review), (None, True))
        self.assertTrue(AuditLog.objects.filter(module="payments", action="callback", object_id=str(txn.pk)).exists())  # the log survives

    def test_an_unknown_order_is_recorded_and_ignored(self):
        payload, signature = self.provider().callback(99999, 5000, transaction_id=31337)
        response = self.post_callback(payload, signature)
        self.assertEqual((response.status_code, response.json()["result"]), (200, "unknown_order"))
        txn = GatewayTransaction.objects.get()
        self.assertEqual((txn.link, txn.customer_payment, txn.needs_review), (None, None, True))
        self.assertFalse(CustomerPayment.objects.exists())

    def test_cancelling_a_link_paid_invoice_leaves_the_payment_on_account(self):
        invoice = self.invoice("300.00")
        self.callback(services.create_link(invoice, self.owner))
        cancel_posted_sales_invoice(invoice.pk, self.owner, "خطأ")
        self.assertFalse(InvoiceSettlement.objects.filter(invoice=invoice).exists())
        self.assertEqual(customer_balance(self.customer), D("-300.00"))
        self.assertEqual(box_balance(self.gateway), D("300.00"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_the_return_page_reads_status_only(self):
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        public = Client()
        # Query parameters claiming success never record money.
        back = public.get(reverse("pay_return_paymob"), {"merchant_order_id": link.reference, "success": "true", "amount_cents": "30000", "hmac": "x"})
        self.assertEqual(back.status_code, 303)
        done = public.get(back["Location"])
        self.assertContains(done, 'data-banner="waiting"')
        self.assertFalse(CustomerPayment.objects.exists())
        self.assertContains(public.get(reverse("pay_return_paymob"), {"merchant_order_id": "HSB-unknown"}), 'data-banner="waiting"')
        self.assertFalse(CustomerPayment.objects.exists())
        self.callback(link)
        self.assertContains(public.get(reverse("pay_done", args=[services.sign_token(link)]), {"lang": "en"}), "Your payment reached the shop")

    def test_the_pay_page_sends_the_customer_to_the_gateway_and_blocks_a_changed_amount(self):
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        public = Client()
        token = services.sign_token(link)
        page = public.get(reverse("pay_page", args=[token]), {"lang": "en"})
        self.assertContains(page, "data-pay-go")
        self.assertContains(page, "300.00")
        self.assertIn("no-store", page["Cache-Control"])
        go = public.get(reverse("pay_go", args=[token]))
        self.assertEqual((go.status_code, go["Location"]), (303, link.checkout_url))
        # Collected at the counter meanwhile: the outstanding fell below the link, so the page no longer offers to pay.
        from sales.services import record_customer_payment
        from settlements.services import settle

        cash = record_customer_payment("CP-1", TODAY, self.customer, self.cash, D("100.00"), self.owner)
        settle(invoice, cash, D("100.00"), "deposit")
        page = public.get(reverse("pay_page", args=[token]))
        self.assertContains(page, 'data-banner="changed"')
        self.assertNotContains(page, "data-pay-go")


class ProviderTests(PayLinkSetup):
    def test_hmac_message_follows_paymobs_field_order_with_a_known_vector(self):
        self.assertEqual(HMAC_FIELDS[0], "amount_cents")
        self.assertEqual(HMAC_FIELDS[-1], "success")
        obj = {"amount_cents": 100, "created_at": "2026-11-08T12:00:00.000000", "currency": "EGP", "error_occured": False, "has_parent_transaction": False,
               "id": 123456, "integration_id": 4567, "is_3d_secure": True, "is_auth": False, "is_capture": False, "is_refunded": False,
               "is_standalone_payment": True, "is_voided": False, "order": {"id": 7890}, "owner": 11, "pending": False,
               "source_data": {"pan": "2346", "sub_type": "MasterCard", "type": "card"}, "success": True}
        message = "1002026-11-08T12:00:00.000000EGPfalsefalse1234564567truefalsefalsefalsetruefalse789011false2346MasterCardcardtrue"
        self.assertEqual(hmac_message(obj), message)
        expected = hmac_lib.new(b"secret", message.encode(), hashlib.sha512).hexdigest()
        self.assertEqual(sign(obj, "secret"), expected)
        provider = PaymobProvider("k", "secret", "1", "2")
        self.assertTrue(provider.verify_callback({"type": "TRANSACTION", "obj": obj}, expected.upper()))
        self.assertFalse(provider.verify_callback({"type": "TRANSACTION", "obj": dict(obj, amount_cents=101)}, expected))

    def test_live_is_selected_only_with_the_switch_and_every_key(self):
        keys = {"PAYMOB_API_KEY": "k", "PAYMOB_HMAC_SECRET": "s", "PAYMOB_INTEGRATION_ID": "1", "PAYMOB_IFRAME_ID": "2"}
        with override_settings(PAYMOB_LIVE=False, PAYMENT_GATEWAY_MOCK=False, **keys):
            self.assertIsNone(providers.active())  # keys alone never make a live call
        with override_settings(PAYMOB_LIVE=False, PAYMENT_GATEWAY_MOCK=True, **keys):
            self.assertIsInstance(providers.active(), MockPaymob)
        for missing in keys:
            with self.subTest(missing=missing), override_settings(PAYMOB_LIVE=True, PAYMENT_GATEWAY_MOCK=True, **dict(keys, **{missing: ""})):
                self.assertIsNone(providers.active())  # half-configured live is nothing, never the mock
        with override_settings(PAYMOB_LIVE=True, **keys):
            live = providers.active()
            self.assertIsInstance(live, PaymobProvider)
            self.assertNotIsInstance(live, MockPaymob)
            self.assertTrue(live.live)

    def test_the_live_adapter_sends_paymobs_three_calls_through_its_one_http_helper(self):
        """The real adapter's code path against a fake shaped like Paymob's API (``_http`` patched: no socket)."""

        seen = []

        def fake_http(self, method, path, body):
            seen.append((method, path, body))
            return {"/api/auth/tokens": {"token": "AUTH"}, "/api/ecommerce/orders": {"id": 424242, "merchant_order_id": body.get("merchant_order_id")},
                    "/api/acceptance/payment_keys": {"token": "PAYKEY"}}[path]

        provider = PaymobProvider("api-k", "hmac-s", "4567", "890")
        with patch.object(PaymobProvider, "_http", fake_http):
            checkout = provider.create_checkout(amount_cents=12345, currency="EGP", merchant_reference="HSB-abc", customer=self.customer, expires_seconds=3600)
        self.assertEqual(checkout.order_id, "424242")
        self.assertEqual(checkout.checkout_url, "https://accept.paymob.com/api/acceptance/iframes/890?payment_token=PAYKEY")
        self.assertEqual([path for _, path, _ in seen], ["/api/auth/tokens", "/api/ecommerce/orders", "/api/acceptance/payment_keys"])
        self.assertEqual(seen[0][2], {"api_key": "api-k"})
        self.assertEqual((seen[1][2]["amount_cents"], seen[1][2]["merchant_order_id"], seen[1][2]["auth_token"]), ("12345", "HSB-abc", "AUTH"))
        self.assertEqual((seen[2][2]["order_id"], seen[2][2]["integration_id"], seen[2][2]["expiration"]), (424242, 4567, 3600))

    def test_no_network_is_touched_making_a_link_and_taking_its_callback(self):
        invoice = self.invoice("300.00")

        def no_network(*args, **kwargs):
            raise AssertionError("a socket was opened")

        with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
            link = services.create_link(invoice, self.owner)
            response, _ = self.callback(link)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.money(invoice)["payments"], 1)
        self.assertEqual(link.provider, "paymob_mock")

    def test_cents_convert_exactly(self):
        self.assertEqual(services.to_cents(D("1234.56")), 123456)
        self.assertEqual(services.to_cents(D("0.1")), 10)
        self.assertEqual(services.from_cents(123456), D("1234.56"))
        self.assertEqual(services.from_cents("99"), D("0.99"))

    def test_no_secret_is_stored_in_a_model_migration_or_fixture(self):
        names = {field.name.lower() for model in (PaymentLink, GatewayTransaction) for field in model._meta.get_fields()}
        self.assertFalse({name for name in names if any(word in name for word in ("secret", "api_key", "hmac_key", "password"))})
        root = Path(__file__).resolve().parent
        for path in list((root / "migrations").glob("*.py")) + list(root.rglob("*.json")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("PAYMOB_API_KEY", text)
            self.assertNotIn("PAYMOB_HMAC_SECRET", text)
        env = (root.parent / ".env.example").read_text(encoding="utf-8")
        for name in ("PAYMOB_API_KEY", "PAYMOB_HMAC_SECRET", "PAYMOB_INTEGRATION_ID", "PAYMOB_IFRAME_ID"):
            self.assertIn(f"{name}=\n", env)  # listed by name, empty


class CapabilityAndPermissionTests(PayLinkSetup):
    def test_capability_off_blocks_new_links_and_the_pay_button_but_a_confirmation_still_posts(self):
        from settings_core import capabilities as caps

        self.assertTrue(caps.is_available("payment_links"))
        self.assertNotIn("payment_links", caps.default_selection("services", "maintenance"))
        self.assertNotIn("payment_links", caps.default_selection("commercial", "retail"))
        invoice = self.invoice("300.00")
        link = services.create_link(invoice, self.owner)
        set_capability_enabled(self.profile, "payment_links", False, user=self.owner)
        with self.assertRaisesMessage(ValidationError, "Payment links are switched off"):
            services.create_link(self.invoice(), self.owner, lang="en")
        page = Client().get(reverse("pay_page", args=[services.sign_token(link)]))
        self.assertContains(page, 'data-banner="off"')
        self.assertNotContains(page, "data-pay-go")
        self.client.force_login(self.owner)
        detail = self.client.get(f"{reverse('sales:detail', args=[invoice.pk])}?lang=ar")
        self.assertNotContains(detail, "data-pay-link-box")
        response, _ = self.callback(link)  # money already taken by the gateway is recorded whatever the switch says now
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.money(invoice)["outstanding"], ZERO)

    def test_permissions(self):
        invoice = self.invoice("300.00")
        with self.assertRaises(PermissionDenied):
            services.create_link(invoice, self.keeper)
        self.client.force_login(self.keeper)
        self.assertEqual(self.client.post(reverse("payments:create", args=[invoice.pk]), {"lang": "ar"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("payments:gateway")).status_code, 403)
        self.assertFalse(PaymentLink.objects.exists())
        self.client.force_login(self.cashier)  # takes payments, so may send a link, but moves no cash between accounts
        self.client.post(reverse("payments:create", args=[invoice.pk]), {"lang": "ar"})
        link = PaymentLink.objects.get()
        self.callback(link)
        self.assertEqual(self.client.get(reverse("payments:gateway")).status_code, 200)
        refused = self.client.post(reverse("payments:gateway"), {"lang": "ar", "gateway": self.gateway.pk, "bank": self.bank.pk, "net": "290", "fee": "10"})
        self.assertEqual(refused.status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.book_payout(self.gateway, self.bank, "290", "10", self.cashier)
        self.assertEqual(box_balance(self.gateway), D("300.00"))

    def test_the_payout_books_the_net_to_the_bank_and_the_fee_in_5212(self):
        invoice = self.invoice("1000.00")
        self.callback(services.create_link(invoice, self.owner))
        self.client.force_login(self.owner)
        response = self.client.post(reverse("payments:gateway"), {"lang": "ar", "gateway": self.gateway.pk, "bank": self.bank.pk, "net": "972.50", "fee": "27.50", "reference": "PO-1"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual((box_balance(self.gateway), box_balance(self.bank)), (ZERO, D("972.50")))
        self.assertEqual(bal("5212"), D("27.50"))
        self.assertEqual(bal("1102"), D("972.50"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assertTrue(AuditLog.objects.filter(module="payments", action="book_payout").exists())
        with self.assertRaisesMessage(ValidationError, "cannot become negative"):
            services.book_payout(self.gateway, self.bank, "1", "0", self.owner)
        with self.assertRaisesMessage(ValidationError, "payment-gateway account"):
            services.book_payout(self.bank, self.gateway, "1", "0", self.owner, lang="en")


class EntityTests(PayLinkSetup):
    def setUp(self):
        super().setUp()
        from entities.services import save_entity

        self.branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        self.branch_store = make_location(location_code="BR-SHOP", name_ar="محل الفرع", entity=self.branch, is_selling_location=True)

    def test_a_branch_invoice_pays_into_the_branchs_gateway_account(self):
        branch_invoice = self.invoice("500.00", location=self.branch_store)
        with self.assertRaisesMessage(ValidationError, "بوابة الدفع"):  # the main entity's gateway account is not the branch's
            services.create_link(branch_invoice, self.owner)
        branch_gateway = make_cashbox(cashbox_code="BR-GW", name_ar="بوابة الدفع", kind="bank", entity=self.branch)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": "", "next": "/dashboard/"})  # the owner, group-wide
        self.client.post(reverse("payments:create", args=[branch_invoice.pk]), {"lang": "ar"})
        link = PaymentLink.objects.get()
        self.assertEqual(link.cashbox, branch_gateway)
        self.callback(link)
        self.assertEqual((box_balance(branch_gateway), box_balance(self.gateway)), (D("500.00"), ZERO))
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        with self.assertRaisesMessage(ValidationError, "payment-gateway account"):  # a payout stays inside one entity
            services.book_payout(branch_gateway, self.bank, "100", "0", self.owner, lang="en")

    def test_a_user_working_in_the_main_entity_cannot_reach_a_branch_invoice(self):
        from entities.models import Entity

        make_cashbox(cashbox_code="BR-GW", name_ar="بوابة الدفع", kind="bank", entity=self.branch)
        branch_invoice = self.invoice("500.00", location=self.branch_store)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": Entity.objects.get(is_main=True).pk, "next": "/dashboard/"})
        self.assertEqual(self.client.post(reverse("payments:create", args=[branch_invoice.pk]), {"lang": "ar"}).status_code, 404)
        self.assertFalse(PaymentLink.objects.exists())


class WorkOrderTests(PayLinkSetup):
    activity, sub_activity = "services", "maintenance"

    @property
    def modules(self):
        from settings_core import setup_catalog as catalog

        return ",".join(catalog.default_modules("services"))

    def test_the_work_order_pages_offer_the_link_once_an_invoice_exists(self):
        from workorders.models import WorkOrder

        invoice = self.invoice("300.00")
        order = WorkOrder.objects.create(number="WO-PL-1", customer=self.customer, device="لابتوب", created_by=self.owner, invoice=invoice)
        self.client.force_login(self.owner)
        staff = self.client.get(f"{reverse('workorders:detail', args=[order.pk])}?lang=ar")
        self.assertContains(staff, "data-pay-link-form")
        self.assertContains(staff, f'name="next" value="{reverse("workorders:detail", args=[order.pk])}?lang=ar"')
        from printing.share import make_token

        public = Client()
        page = public.get(reverse("share_document", args=[make_token("work_order", order.pk)]), HTTP_ACCEPT="text/html")
        self.assertNotContains(page, "data-pay-online")  # no link sent yet: the customer's page never makes one
        services.create_link(invoice, self.owner)
        page = public.get(reverse("share_document", args=[make_token("work_order", order.pk)]), HTTP_ACCEPT="text/html")
        self.assertContains(page, "data-pay-online")
        self.assertFalse(CustomerPayment.objects.exists())



class RepTests(PayLinkSetup):
    modules = PayLinkSetup.modules + ",employees_technicians"  # reps are used only with the employees module

    def test_rep_report_and_digest_count_the_link_payment_once(self):
        from reports.reps import rep_performance
        from staff.services import save_employee

        rep = save_employee({"name": "سمير المندوب", "commission_percent": "5"}, self.owner)
        self.customer.sales_rep = rep
        self.customer.save()
        day_one = TODAY - timedelta(days=2)
        draft = create_sales_draft({"invoice_number": "SI-REP", "invoice_date": day_one, "customer": self.customer, "selling_location": self.location,
                                    "cashbox": None, "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO, "salesperson": rep},
                                   [{"item": self.service, "quantity": D("1"), "unit_sale_price": D("1000"), "line_discount_amount": ZERO}], self.owner)
        post_sales_invoice(draft.pk, self.owner)
        draft.refresh_from_db()
        self.callback(services.create_link(draft, self.owner, "400"))  # paid today, the day the webhook arrives
        whole = {row["rep"]: row for row in rep_performance(day_one, TODAY)[0]}[rep]
        self.assertEqual((whole["collected"], whole["credit_sold"]), (D("400.00"), D("600.00")))  # the link payment once
        first_day = {row["rep"]: row for row in rep_performance(day_one, day_one)[0]}[rep]
        self.assertEqual((first_day["collected"], first_day["credit_sold"]), (ZERO, D("600.00")))  # collected today, not on day one
        self.assertEqual(daily_digest(self.owner, TODAY)["customers"]["collected"], D("400.00"))
        self.assertEqual(daily_digest(self.owner, day_one)["sales"]["credit"], D("600.00"))
