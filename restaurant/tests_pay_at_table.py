"""Restaurants R5b (HG-121): pay from the table.

The set-up of ``tests_qr`` (the main hall's «T1» and the Maadi branch's own «T1», VAT 14% on, the clock frozen on Wednesday
11 Nov 2026 at noon in Cairo, R1a's burger 120.00 with «ميديم» and the priced add-on «جبنة زيادة» at 15.00), plus a «بوابة الدفع»
bank account in each branch, the online-payment capability on, the mock Paymob gateway (no socket, signed callbacks only) and
an online-payments user whose role holds **only** ``sales.create_sales_invoice`` and ``restaurant.prepare_at_payment``.

Two burgers with cheese: 2 × 120.00 + 2 × 15.00 = 270.00; with VAT 14% (33.60 + 4.20) **307.80**.

* «ادفع» on the QR page locks the order and sends the guest to the payment company's page with a link for the bill now;
* the verified callback for exactly that amount on the unchanged order posts a ``POS-`` invoice paid in full into «بوابة الدفع»
  as the online-payments user, and the e-receipt is issued;
* any other amount, a changed order or bill, a link no longer live: nothing posts, the link goes to review, the order is open;
* while the link is live, add, change, void, split, merge, move, cancel and pay (with a discount too) are refused.
"""

import json
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from hesba_testing.factories import grant, make_cashbox, make_role, make_user, make_user_profile
from payments import providers
from payments import services as gateway
from payments.models import GatewayTransaction, LinkStatus, PaymentLink
from permissions.models import Permission, RoleCode
from sales.models import SalesInvoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from . import charges, pay_at_table, services, views, views_qr
from .models import Order, OrderLine, OrderStatus
from .tests import person
from .tests_branches import NOW
from .tests_qr import QrSetup

GATEWAY = {"PAYMENT_GATEWAY_MOCK": True, "PAYMOB_LIVE": False, "PAYMOB_API_KEY": "", "PAYMOB_HMAC_SECRET": "", "PAYMOB_INTEGRATION_ID": "",
           "PAYMOB_IFRAME_ID": ""}
LOCKED = "مقفول للدفع"


def online_role_user(username="online_pay"):
    """The online-payments user, with the two permissions HG-121 documents and nothing else."""

    role = make_role(code="ONLINE-PAY")
    for code in (pay_at_table.SELL, pay_at_table.MAKE):
        grant(role, Permission.objects.get(code=code))
    user = make_user(username=username)
    make_user_profile(user=user, role=role)
    return user


@override_settings(**GATEWAY)
class PaySetup(QrSetup):
    def setUp(self):
        super().setUp()
        self.gateway = make_cashbox(cashbox_code="GW", name_ar="بوابة الدفع", name_en="Payment gateway", kind="bank", entity=self.main)
        self.b_gateway = make_cashbox(cashbox_code="BR-GW", name_ar="بوابة الدفع – المعادي", kind="bank", entity=self.branch)
        set_capability_enabled(ClientProfile.get_active(), "payment_links", True, user=self.owner)
        self.online = online_role_user()
        pay_at_table.save_online_user(self.online.pk, self.owner)

    # ---- helpers ------------------------------------------------------------------------------------------------

    def press(self, table=None, client=None, lang="ar"):
        return (client or self.guest).post(self.url(table, lang=lang), {"action": "pay"})

    def start(self, order=None, table=None):
        """The guest's «ادفع» on ``table``'s page; returns the link it made."""

        response = self.press(table)
        self.assertEqual(response.status_code, 303, response.content.decode()[:800] if response.status_code != 303 else "")
        link = PaymentLink.objects.order_by("-pk").first()
        self.assertEqual(response["Location"], link.checkout_url)
        return link

    def provider(self):
        provider = providers.active()
        self.assertIsNotNone(provider)
        return provider

    def signed(self, link, cents=None, transaction_id=None, **flags):
        return self.provider().callback(link.gateway_order_id, gateway.to_cents(link.amount) if cents is None else cents, transaction_id=transaction_id,
                                        merchant_reference=link.reference, **flags)

    def post_callback(self, payload, signature):
        client = Client(enforce_csrf_checks=True)  # the webhook is CSRF-exempt; it is authenticated by the gateway's HMAC
        return client.post(f"{reverse('pay_webhook_paymob')}?hmac={signature}", data=json.dumps(payload), content_type="application/json")

    def callback(self, link, cents=None, transaction_id=None, **flags):
        payload, signature = self.signed(link, cents, transaction_id, **flags)
        response = self.post_callback(payload, signature)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["result"]

    def moved(self):
        return {"invoices": SalesInvoice.objects.count(), "cash": CashboxMovement.objects.count(), "lines": OrderLine.objects.count()}

    def gateway_in(self, box=None):
        return list(CashboxMovement.objects.filter(cashbox=box or self.gateway).values_list("direction", "amount"))

    def e_receipt_on(self):
        from einvoice.models import ItemCode
        from einvoice.services import save_issuer_settings
        from ereceipt.models import Device
        from printing.company import save_company_details

        set_capability_enabled(ClientProfile.get_active(), "e_receipt", True)
        save_company_details({"company.tax_number": "100-200-300"}, self.owner)
        save_issuer_settings({"einvoice.activity_code": "5610", "einvoice.branch_id": "0", "einvoice.governate": "Cairo", "einvoice.region_city": "Nasr City",
                              "einvoice.street": "Abbas El Akkad", "einvoice.building_number": "12", "einvoice.person_id_threshold": "50000"}, self.owner)
        ItemCode.objects.create(item=self.burger, code_type="EGS", code="EG-100200300-BURG", unit_type="EA")
        ItemCode.objects.create(item=self.cheese, code_type="EGS", code="EG-100200300-CHS", unit_type="EA")
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")

    def order_page(self, order, user=None):
        self.staff(user=user or self.owner)
        return self.client.get(reverse("restaurant:order", args=[order.pk]))

    def order_post(self, order, data, user=None):
        self.staff(user=user or self.owner)
        return self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", **data})


class MoneyTests(PaySetup):
    def test_307_80_with_vat_paid_from_the_table_posts_a_pos_invoice_into_the_gateway_and_issues_the_e_receipt(self):
        self.e_receipt_on()
        order = self.burgers()
        page = self.guest.get(self.url())
        self.assertContains(page, 'data-pay-state="ready"')
        self.assertContains(page, '<strong data-pay-total><bdi dir="ltr">307.80</bdi>', html=False)
        self.assertContains(page, "data-pay-button")
        link = self.start(order)
        self.assertEqual((link.order, link.invoice, link.amount, link.cashbox, link.created_by, link.status, link.provider),
                         (order, None, D("307.80"), self.gateway, self.online, LinkStatus.NEW, "paymob_mock"))
        self.assertEqual(link.expires_at, NOW + timedelta(minutes=30))
        self.assertEqual(self.callback(link), "posted_order")
        order.refresh_from_db()
        link.refresh_from_db()
        invoice = order.invoice
        self.assertEqual((order.status, link.status, link.paid_amount), (OrderStatus.PAID, LinkStatus.PAID, D("307.80")))
        self.assertTrue(invoice.invoice_number.startswith("POS-"))
        self.assertEqual((invoice.status, invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.paid_now, invoice.remaining_due, invoice.cashbox, invoice.created_by),
                         ("posted", D("270.00"), D("37.80"), D("307.80"), D("307.80"), D("0.00"), self.gateway, self.online))
        self.assertEqual(self.gateway_in(), [("in", D("307.80"))])
        self.assertFalse(CashboxMovement.objects.filter(sales_invoice=invoice).exclude(cashbox=self.gateway).exists())  # not the drawer
        from ereceipt.models import Receipt, ReceiptStatus

        receipt = Receipt.objects.get(invoice=invoice)
        self.assertEqual((receipt.status, receipt.document["totalAmount"]), (ReceiptStatus.QUEUED, 307.8))
        self.assertTrue(AuditLog.objects.filter(module="payments", action="post_order_link", object_id=str(link.pk)).exists())
        self.assertTrue(AuditLog.objects.filter(module="restaurant", action="pay_order", actor=self.online).exists())

    def test_270_00_with_vat_off_the_same(self):
        self.vat(False)
        order = self.burgers()
        link = self.start(order)
        self.assertEqual(link.amount, D("270.00"))
        self.assertEqual(self.callback(link), "posted_order")
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice.total_amount, order.invoice.tax_amount, order.invoice.paid_now), (OrderStatus.PAID, D("270.00"), D("0.00"), D("270.00")))
        self.assertEqual(self.gateway_in(), [("in", D("270.00"))])

    def test_the_whole_way_through_the_mock_checkout_page_and_the_done_page(self):
        order = self.burgers()
        link = self.start(order)
        checkout = self.guest.get(link.checkout_url)
        self.assertContains(checkout, "307.80")
        self.assertContains(checkout, order.number)
        done = self.guest.post(link.checkout_url, {"outcome": "success"}, follow=True)
        self.assertContains(done, 'data-banner="paid"')
        self.assertContains(done, "طلب رقم")
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice.total_amount), (OrderStatus.PAID, D("307.80")))
        self.assertEqual(self.guest.get(link.checkout_url).status_code, 404)  # paid: nothing to pay there any more

    def test_a_kitchen_dish_at_the_branch_is_made_by_the_online_user_and_paid_into_the_branchs_gateway(self):
        order = services.open_order(self.owner, table=self.b_t1, guests=2)
        services.add_item(order, self.koshary, self.owner, 2)  # made from rice and lentils at payment
        link = self.start(order, table=self.b_t1)
        self.assertEqual((link.cashbox, link.order.entity), (self.b_gateway, self.branch))
        self.assertEqual(self.callback(link), "posted_order")
        order.refresh_from_db()
        invoice = order.invoice
        self.assertEqual((order.status, invoice.selling_location, invoice.cashbox, invoice.total_amount), (OrderStatus.PAID, self.b_location, self.b_gateway, link.amount))
        self.assertEqual(self.gateway_in(self.b_gateway), [("in", link.amount)])
        self.assertEqual(self.gateway_in(), [])
        from manufacturing.models import ProductionRun

        self.assertTrue(ProductionRun.objects.filter(location=self.b_location).exists())

    def test_the_payout_is_booked_with_the_existing_book_payout(self):
        order = self.burgers()
        self.callback(self.start(order))
        bank = make_cashbox(cashbox_code="NBE", name_ar="البنك الأهلي", kind="bank", entity=self.main)
        gateway.book_payout(self.gateway, bank, "300.00", "7.80", self.owner, reference="PO-1")
        from cashboxes.services import get_cashbox_balance

        self.assertEqual((get_cashbox_balance(self.gateway), get_cashbox_balance(bank)), (D("0.00"), D("300.00")))


class DriftTests(PaySetup):
    def test_a_callback_of_300_00_needs_review_posts_nothing_and_frees_the_order(self):
        order = self.burgers()
        link = self.start(order)
        before = self.moved()
        self.assertEqual(self.callback(link, cents=30000), "amount_mismatch")
        self.assertEqual(self.moved(), before)
        order.refresh_from_db()
        link.refresh_from_db()
        txn = GatewayTransaction.objects.get(link=link)
        self.assertEqual((order.status, order.invoice, link.status, link.paid_amount), (OrderStatus.OPEN, None, LinkStatus.REVIEW, D("300.00")))
        self.assertTrue(txn.needs_review)
        self.assertIn("300.00", txn.note)
        services.add_item(order, self.tea, self.owner)  # the order is the staff's again
        invoice, _change = services.pay(order, self.owner, cashbox=self.gateway, tendered=services.bill_total(order, list(order.lines.filter(voided=False))))
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.PAID)
        page = self.guest.get(self.url())
        self.assertNotContains(page, "data-pay-box")  # paid by staff: the table has no open bill

    def test_an_order_changed_behind_the_lock_is_never_posted(self):
        order = self.burgers()
        link = self.start(order)
        OrderLine.objects.filter(order=order, item=self.burger).update(quantity=D("1"))  # not through a service: as if the lock were missed
        before = self.moved()
        self.assertEqual(self.callback(link), "order_not_posted")
        self.assertEqual(self.moved(), before)
        link.refresh_from_db()
        self.assertEqual((Order.objects.get(pk=order.pk).status, link.status), (OrderStatus.OPEN, LinkStatus.REVIEW))
        self.assertIn("changed", GatewayTransaction.objects.get(link=link).note)

    def test_a_bill_changed_by_a_setting_after_the_link_is_never_posted(self):
        order = self.burgers()
        link = self.start(order)
        charges.save_settings({"percent": "12", "kinds": ["dine_in"], "minimum": ""}, self.owner)  # a service charge switched on afterwards
        self.assertEqual(self.callback(link), "order_not_posted")
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertIn("the bill is now", GatewayTransaction.objects.get(link=link).note)
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.OPEN)

    def test_the_same_callback_twice_posts_once(self):
        order = self.burgers()
        link = self.start(order)
        payload, signature = self.signed(link, transaction_id=770001)
        self.assertEqual(self.post_callback(payload, signature).json()["result"], "posted_order")
        self.assertEqual(self.post_callback(payload, signature).json()["result"], "repeat_confirmed")
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertEqual(self.gateway_in(), [("in", D("307.80"))])
        self.assertEqual(GatewayTransaction.objects.count(), 1)

    def test_a_second_payment_on_a_paid_table_link_is_flagged_and_posts_nothing(self):
        order = self.burgers()
        link = self.start(order)
        self.callback(link, transaction_id=770001)
        self.assertEqual(self.callback(link, transaction_id=770002), "order_link_not_live")
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertTrue(GatewayTransaction.objects.get(transaction_id="770002").needs_review)

    def test_a_declined_card_keeps_the_link_live_and_the_next_card_pays(self):
        order = self.burgers()
        link = self.start(order)
        self.assertEqual(self.callback(link, transaction_id=770001, success=False), "failed")
        link.refresh_from_db()
        self.assertEqual(link.status, LinkStatus.NEW)
        with self.assertRaisesMessage(ValidationError, LOCKED):
            services.add_item(order, self.tea, self.owner)
        self.assertEqual(self.callback(link, transaction_id=770002), "posted_order")
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.PAID)

    def test_a_payment_after_staff_cancelled_the_link_is_flagged_and_not_posted(self):
        order = self.burgers()
        link = self.start(order)
        pay_at_table.cancel(order, self.owner)
        self.assertEqual(self.callback(link), "order_link_not_live")
        link.refresh_from_db()
        self.assertEqual((link.status, SalesInvoice.objects.count()), (LinkStatus.EXPIRED, 0))
        self.assertTrue(GatewayTransaction.objects.get(link=link).needs_review)
        page = self.order_page(order)
        self.assertContains(page, 'data-table-pay="review"')
        self.assertContains(self.guest.get(self.url()), 'data-pay-state="review"')  # the guest is not offered «ادفع» a second time
        self.assertEqual(self.press().status_code, 400)

    def test_an_online_user_who_can_no_longer_sell_posts_nothing(self):
        order = self.burgers()
        link = self.start(order)
        self.online.is_active = False
        self.online.save(update_fields=["is_active"])
        self.assertEqual(self.callback(link), "order_not_posted")
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(PaymentLink.objects.get(pk=link.pk).status, LinkStatus.REVIEW)


class LockTests(PaySetup):
    def locked(self):
        order = self.burgers()
        self.start(order)
        return Order.objects.get(pk=order.pk)

    def test_adding_changing_and_voiding_are_refused_while_the_link_is_live(self):
        order = self.locked()
        line = order.lines.get(item=self.burger)
        before = self.moved()
        for call in (lambda: services.add_item(order, self.tea, self.waiter),
                     lambda: services.add_agreed_line(order, self.tea, self.owner, 1, "5.00"),
                     lambda: services.change_line(line, self.owner, quantity="3"),
                     lambda: services.void_line(line, self.owner)):
            with self.assertRaisesMessage(ValidationError, LOCKED):
                call()
        services.send_to_kitchen(order, self.owner)  # the kitchen keeps working: sending is not money
        line.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, LOCKED):
            services.void_line(line, self.owner, reason="العميل غيّر رأيه")
        self.assertEqual({k: v for k, v in self.moved().items()}, before)
        self.assertEqual(self.callback(PaymentLink.objects.get(order=order)), "posted_order")  # sending left the bill as it was

    def test_split_merge_move_cancel_and_pay_with_a_discount_are_refused_while_the_link_is_live(self):
        order = self.locked()
        line = order.lines.get(item=self.burger)
        t2 = services.save_table({"name": "T2", "seats": 4}, self.owner, entity=self.main)
        t3 = services.save_table({"name": "T3", "seats": 4}, self.owner, entity=self.main)
        other = services.open_order(self.owner, table=t2)
        services.add_item(other, self.tea, self.owner)
        for call in (lambda: services.split_order(order, {line.pk: 1}, self.owner),
                     lambda: services.merge_orders(order, other, self.owner),
                     lambda: services.merge_orders(other, order, self.owner),
                     lambda: services.move_table(order, t3, self.owner),
                     lambda: services.cancel_order(order, self.owner, "غلط"),
                     lambda: services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("307.80")),
                     lambda: services.pay(order, self.owner, cashbox=self.cashbox, discount=D("7.80"), tendered=D("300.00"))):
            with self.assertRaisesMessage(ValidationError, LOCKED):
                call()
        order.refresh_from_db()
        self.assertEqual((order.status, order.table, SalesInvoice.objects.count(), Order.objects.filter(split_from=order).count()), (OrderStatus.OPEN, self.a_t1, 0, 0))

    def test_the_order_page_says_the_order_is_locked(self):
        order = self.locked()
        response = self.order_post(order, {"action": "add", "item": self.tea.pk, "quantity": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(response["Location"]), LOCKED)
        page = self.order_page(order)
        self.assertContains(page, 'data-table-pay="waiting"')
        self.assertContains(page, "data-cancel-pay-link")
        self.assertContains(page, '<bdi dir="ltr" data-table-pay-amount>307.80</bdi>', html=False)

    def test_an_expired_link_frees_the_order(self):
        order = self.locked()
        with mock.patch("django.utils.timezone.now", return_value=NOW + timedelta(minutes=31)):
            services.add_item(order, self.tea, self.owner)
            self.assertEqual(PaymentLink.objects.get(order=order).status, LinkStatus.EXPIRED)
            self.assertContains(self.order_page(order), 'data-table-pay="ended"')

    def test_a_cancelled_link_frees_the_order(self):
        order = self.locked()
        response = self.order_post(order, {"action": "cancel_pay_link"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PaymentLink.objects.get(order=order).status, LinkStatus.EXPIRED)
        services.add_item(order, self.tea, self.owner)
        self.assertTrue(order.lines.filter(item=self.tea).exists())

    def test_pressing_twice_is_one_link_and_one_gateway_order(self):
        order = self.burgers()
        first = self.start(order)
        page = self.guest.get(self.url())
        self.assertContains(page, 'data-pay-state="waiting"')
        second = self.start(order)
        self.assertEqual((first.pk, PaymentLink.objects.count()), (second.pk, 1))


class GateTests(PaySetup):
    def assert_not_offered(self):
        self.burgers()
        page = self.guest.get(self.url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-pay-box")
        self.assertContains(page, views_qr.WORDS["ar"]["pay_note"])  # R5a's page, as it was
        response = self.press()
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "الدفع مع الجرسون", status_code=400)
        self.assertFalse(PaymentLink.objects.exists())

    def test_no_online_payments_user_no_button_and_the_endpoint_refuses(self):
        pay_at_table.save_online_user("", self.owner)
        self.assert_not_offered()

    def test_the_capability_off_no_button_and_the_endpoint_refuses(self):
        set_capability_enabled(ClientProfile.get_active(), "payment_links", False, user=self.owner)
        self.assert_not_offered()

    @override_settings(PAYMENT_GATEWAY_MOCK=False)
    def test_no_gateway_connected_no_button(self):
        self.assert_not_offered()

    def test_an_online_user_who_cannot_sell_is_refused_and_a_deactivated_one_switches_it_off(self):
        with self.assertRaisesMessage(ValidationError, "صلاحية البيع"):
            pay_at_table.save_online_user(self.waiter.pk, self.owner)
        self.online.is_active = False
        self.online.save(update_fields=["is_active"])
        self.assertIsNone(pay_at_table.online_user())
        self.assert_not_offered()

    def test_no_gateway_account_in_the_branch_shows_why_and_refuses(self):
        services.add_item(services.open_order(self.owner, table=self.b_t1), self.tea, self.owner)
        self.b_gateway.active = False
        self.b_gateway.save(update_fields=["active"])
        page = self.guest.get(self.url(self.b_t1))
        self.assertContains(page, 'data-pay-state="blocked"')
        self.assertEqual(self.press(self.b_t1).status_code, 400)
        self.assertFalse(PaymentLink.objects.exists())

    def test_a_split_bill_is_paid_with_the_waiter(self):
        order = self.burgers()
        services.split_order(order, {order.lines.get(item=self.burger).pk: 1}, self.owner)
        self.assertContains(self.guest.get(self.url()), 'data-pay-state="blocked"')
        self.assertContains(self.press(), "متقسم", status_code=400)

    def test_a_table_with_no_open_bill_shows_nothing_and_refuses(self):
        page = self.guest.get(self.url())
        self.assertNotContains(page, "data-pay-box")
        self.assertContains(self.press(), "مفيش حساب مفتوح", status_code=400)


class PermissionTests(PaySetup):
    def test_only_the_tables_own_key_reaches_its_bill(self):
        order = self.burgers()
        forged = self.guest.post(reverse("table_menu", args=["not-a-key"]), {"action": "pay"})
        self.assertEqual(forged.status_code, 404)
        t2 = services.save_table({"name": "T2", "seats": 4}, self.owner, entity=self.main)
        self.assertEqual(self.press(t2).status_code, 400)  # T2's key never pays T1's order
        self.assertNotContains(self.guest.get(self.url(self.b_t1)), "data-pay-box")  # nor the branch's T1
        self.assertEqual(self.press(self.b_t1).status_code, 400)
        self.assertFalse(PaymentLink.objects.exists())
        services.add_item(services.open_order(self.owner, table=self.b_t1), self.tea, self.owner)
        link = self.start(table=self.b_t1)
        self.assertEqual(link.order.table, self.b_t1)
        self.assertFalse(PaymentLink.objects.filter(order=order).exists())

    def test_cancelling_the_link_needs_the_collection_right(self):
        order = self.burgers()
        link = self.start(order)
        page = self.order_page(order, self.waiter)
        self.assertContains(page, 'data-table-pay="waiting"')
        self.assertNotContains(page, "data-cancel-pay-link")
        self.assertEqual(self.order_post(order, {"action": "cancel_pay_link"}, self.waiter).status_code, 403)
        self.assertEqual(PaymentLink.objects.get(pk=link.pk).status, LinkStatus.NEW)
        with self.assertRaises(PermissionDenied):
            gateway.cancel_link(link, self.waiter)
        cashier = person(RoleCode.CASHIER, "pt_cashier")
        from entities.models import EntityMembership

        EntityMembership.objects.create(user=cashier, entity=self.main, is_default=True)
        self.assertEqual(self.order_post(order, {"action": "cancel_pay_link"}, cashier).status_code, 302)
        self.assertEqual(PaymentLink.objects.get(pk=link.pk).status, LinkStatus.EXPIRED)

    def test_choosing_the_online_user_needs_the_tables_and_collection_rights(self):
        cashier = person(RoleCode.CASHIER, "pt_cashier2")
        from entities.models import EntityMembership

        EntityMembership.objects.create(user=cashier, entity=self.main, is_default=True)
        self.staff(user=cashier)
        page = self.client.get(reverse("restaurant:tables"))
        self.assertContains(page, "data-online-pay-view")
        self.assertNotContains(page, "data-online-pay-form")
        self.assertEqual(self.client.post(reverse("restaurant:tables"), {"action": "online_pay", "online_user": ""}).status_code, 403)
        self.assertEqual(pay_at_table.online_user(), self.online)
        self.staff(user=self.owner)
        self.assertContains(self.client.get(reverse("restaurant:tables")), "data-online-pay-form")
        response = self.client.post(reverse("restaurant:tables"), {"action": "online_pay", "online_user": "", "lang": "ar"})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(pay_at_table.online_user())
        self.assertTrue(AuditLog.objects.filter(action="save_online_payments_user", actor=self.owner).exists())


class GatewayScreenTests(PaySetup):
    """S4's screens with an order link among the invoice links (each shown failing first on R14's head + the core commit)."""

    def test_the_gateway_screen_lists_a_table_link_and_its_review_row(self):
        order = self.burgers()
        link = self.start(order)
        self.callback(link, cents=30000)
        self.staff(user=self.owner)
        page = self.client.get(reverse("payments:gateway"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'href="{reverse("restaurant:order", args=[order.pk])}?lang=ar" data-order-link', html=False)
        self.assertContains(page, f"<bdi data-review-order>{order.number}</bdi>", html=False)

    def test_the_invoice_links_cancel_address_never_takes_a_table_link(self):
        order = self.burgers()
        link = self.start(order)
        self.staff(user=self.owner)
        response = self.client.post(reverse("payments:cancel", args=[link.pk]), {"lang": "ar"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PaymentLink.objects.get(pk=link.pk).status, LinkStatus.NEW)

    def test_the_done_page_of_a_table_link_names_the_order(self):
        order = self.burgers()
        link = self.start(order)
        page = self.guest.get(reverse("pay_done", args=[gateway.sign_token(link)]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "طلب رقم")
        self.assertContains(page, order.number)


class ConstraintTests(PaySetup):
    def test_a_link_has_exactly_one_of_an_invoice_and_an_order(self):
        order = self.burgers()
        common = {"customer": services_walk_in(), "cashbox": self.gateway, "amount": D("10.00"), "provider": "x", "expires_at": NOW, "created_by": self.owner}
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentLink.objects.create(reference="R-NONE", token="t-none", **common)
        invoice, _change = services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("307.80"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentLink.objects.create(reference="R-BOTH", token="t-both", invoice=invoice, order=order, **common)


def services_walk_in():
    from sales.pos import walk_in_customer

    return walk_in_customer()


class WordingTests(PaySetup):
    def test_the_words_are_plain_and_never_the_ones_we_avoid(self):
        texts = [pay_at_table.MESSAGES, views_qr.PAY_WORDS, views.TABLE_PAY_WORDS]
        flat = json.dumps(texts, ensure_ascii=False)
        for word in ("ورشة", "الأسطى", "بتاع", "دوس", "لينك"):
            self.assertNotIn(word, flat)
        for table in texts:
            self.assertEqual(set(table["ar"]), set(table["en"]))

    def test_the_new_rules_use_tokens_only(self):
        import re
        from pathlib import Path

        from django.conf import settings

        sheet = Path(settings.BASE_DIR, "static/hesba/css/table_menu.css").read_text()
        block = sheet.split("/* R5b (HG-121)", 1)[1]
        self.assertFalse(re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", block))

    def test_english(self):
        self.burgers()
        page = self.guest.get(self.url(lang="en"))
        self.assertContains(page, "Your table&#x27;s bill")
        self.assertContains(page, ">Pay</button>", html=False)


class MigrationTests(TransactionTestCase):
    """0002 on a database that already holds an invoice link: it stays an invoice link, and it can go back and forward."""

    serialized_rollback = True
    before, after = ("payments", "0001_initial"), ("payments", "0002_order_links")

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_an_existing_invoice_link_is_kept(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([self.before])
        leaves = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "payments"] + [self.before]
        old = executor.loader.project_state(leaves).apps
        user = old.get_model("auth", "User").objects.create(username="mig_r5b")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="C-R5B", name="عميل")
        location = old.get_model("master_data", "Location").objects.create(location_code="L-R5B", name_ar="محل")
        box = old.get_model("cashboxes", "Cashbox").objects.create(cashbox_code="GW-R5B", name_ar="بوابة الدفع", kind="bank")
        invoice = old.get_model("sales", "SalesInvoice").objects.create(invoice_number="SI-R5B", invoice_date=NOW.date(), customer=customer, selling_location=location,
                                                                         status="posted", subtotal=D("100.00"), total_amount=D("100.00"), created_by=user)
        link = old.get_model("payments", "PaymentLink").objects.create(invoice=invoice, customer=customer, cashbox=box, amount=D("100.00"), provider="x",
                                                                      reference="HSB-R5B", token="tok-r5b", expires_at=NOW, created_by=user)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([self.after])
        new = executor.loader.project_state([self.after]).apps
        kept = new.get_model("payments", "PaymentLink").objects.get(pk=link.pk)
        self.assertEqual((kept.invoice_id, kept.order_id, kept.order_digest, kept.status, kept.amount), (invoice.pk, None, "", "new", D("100.00")))
