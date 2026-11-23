"""POS-003: sales rung up offline are posted once each when the till syncs."""

import json
import uuid
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import get_item_location_stock_quantity, recalculate_item_average_cost
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, SalesInvoice

from .models import OfflineSale


SYNC = reverse("sales:pos_sync")


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class OfflineSyncTests(TestCase):
    def setUp(self):
        prepared_client()
        self.cashier = person(RoleCode.CASHIER, "off_cashier")
        self.client.force_login(self.cashier)
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.shirt = make_item(item_code="SHIRT", default_sale_price="240.00")
        stock_in(self.shirt, self.location, 3, "100.00", movement_date=timezone.localdate() - timedelta(days=30))
        recalculate_item_average_cost(self.shirt)

    def sale(self, qty="1", tendered="240", when=None, key=None, customer=None, **extra):
        body = {
            "key": str(key or uuid.uuid4()), "recorded_at": (when or timezone.now()).isoformat(),
            "customer": str(customer.pk) if customer else "", "location": str(self.location.pk), "cashbox": str(self.cashbox.pk),
            "discount": "0", "tendered": tendered, "lines": [{"id": self.shirt.pk, "qty": qty, "price": "240.00", "unit": None, "serial": None}],
        }
        body.update(extra)
        return self.client.post(SYNC, json.dumps(body), content_type="application/json")

    def test_an_offline_sale_posts_like_a_live_one_dated_when_it_was_rung_up(self):
        yesterday = timezone.now() - timedelta(days=1)
        answer = self.sale(qty="2", tendered="500", when=yesterday).json()
        self.assertEqual(answer["status"], "posted")
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.status, invoice.invoice_date, invoice.total_amount, invoice.paid_now, invoice.notes), ("posted", timezone.localtime(yesterday).date(), D("480.00"), D("480.00"), "POS offline"))
        self.assertTrue(invoice.invoice_number.startswith(f"POS-{timezone.localtime(yesterday):%Y%m%d}-"))
        self.assertEqual(get_item_location_stock_quantity(self.shirt, self.location), D("1"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("480.00"))
        self.assertEqual((invoice.created_by, OfflineSale.objects.get().cashier), (self.cashier, self.cashier))

    def test_the_same_sale_sent_twice_posts_once(self):
        key = uuid.uuid4()
        first = self.sale(key=key).json()
        again = self.sale(key=key, qty="3").json()  # a retry, even with a changed body, cannot post again
        self.assertEqual((first["status"], again["status"], again["number"]), ("posted", "already", first["number"]))
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertEqual(get_item_location_stock_quantity(self.shirt, self.location), D("2"))

    def test_a_sale_the_server_cannot_post_is_refused_and_leaves_nothing_behind(self):
        refused = self.sale(qty="5", tendered="1200").json()  # paid in full, but only 3 in stock
        self.assertEqual(refused["status"], "refused")
        self.assertIn("الكمية مش كفاية", refused["error"])  # the till's language, not the service's English
        self.assertEqual((SalesInvoice.objects.count(), OfflineSale.objects.count()), (0, 0))
        credit = self.sale(tendered="0").json()  # walk-in on credit is refused as at the live till
        self.assertEqual(credit["status"], "refused")
        named = make_customer(customer_code="C-1", name="Named")
        on_account = self.sale(tendered="0", customer=named).json()
        self.assertEqual(on_account["status"], "posted")
        self.assertEqual(CustomerLedgerEntry.objects.get().due_increase, D("240.00"))

    def test_dates_keys_and_permissions_are_checked(self):
        self.assertEqual(self.sale(when=timezone.now() + timedelta(days=1)).json()["status"], "refused")
        self.assertIn("7", self.sale(when=timezone.now() - timedelta(days=8)).json()["error"])
        self.assertEqual(self.client.post(SYNC, "{bad json", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(SYNC, json.dumps({"key": "not-a-uuid"}), content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get(SYNC).status_code, 405)
        keeper = person(RoleCode.STOCK_KEEPER, "off_keeper")
        self.client.force_login(keeper)
        self.assertIn(self.sale().status_code, (302, 403))
        self.assertEqual(SalesInvoice.objects.count(), 0)

    def test_the_till_page_carries_the_offline_hooks(self):
        page = self.client.get(reverse("sales:pos"))
        self.assertContains(page, "data-pos-offline")
        self.assertContains(page, f'data-sync-url="{SYNC}"')
        self.assertContains(page, "pos_offline.js")
