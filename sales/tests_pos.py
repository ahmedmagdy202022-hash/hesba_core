"""POS-001: the cashier screen posts real invoices through the sales services."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from .models import CustomerLedgerEntry, SalesInvoice
from .pos import WALK_IN_CODE, walk_in_customer


POS = reverse("sales:pos")


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class PosTests(TestCase):
    def setUp(self):
        prepared_client()
        self.cashier = person(RoleCode.CASHIER, "pos_cashier")
        self.client.force_login(self.cashier)
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.shirt = make_item(item_code="SHIRT", barcode="5901234123457", default_sale_price="240.00")
        self.belt = make_item(item_code="BELT", default_sale_price="120.00")
        for item in (self.shirt, self.belt):
            stock_in(item, self.location, 10, "100.00", movement_date=timezone.localdate())
            recalculate_item_average_cost(item)

    def sell(self, lines, tendered, customer=None, discount="0", print_flag="0", follow=False):
        data = {
            "line_count": str(len(lines)),
            "customer": str((customer or walk_in_customer()).pk),
            "location": str(self.location.pk),
            "cashbox": str(self.cashbox.pk),
            "discount": discount,
            "tendered": tendered,
            "print": print_flag,
        }
        for index, (item, qty, price) in enumerate(lines):
            data.update({f"item_{index}": str(item.pk), f"qty_{index}": qty, f"price_{index}": price})
        return self.client.post(POS, data, follow=follow)

    def test_cash_sale_posts_moves_stock_and_cash_and_gives_change(self):
        response = self.sell([(self.shirt, "2", "240.00"), (self.belt, "1", "120.00")], tendered="700")
        invoice = SalesInvoice.objects.get()
        self.assertRedirects(response, f"/sales/pos/?lang=ar&last={invoice.pk}", fetch_redirect_response=False)
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(invoice.invoice_number, f"POS-{timezone.localdate():%Y%m%d}-0001")
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("600.00"), D("600.00"), D("0.00")))
        self.assertEqual(invoice.customer.customer_code, WALK_IN_CODE)
        self.assertEqual(invoice.created_by, self.cashier)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("600.00"))
        self.assertEqual(StockMovement.objects.filter(sales_invoice=invoice).count(), 2)
        page = self.client.get(response.url)
        self.assertContains(page, "الباقي للعميل 100.00")
        self.assertContains(page, invoice.invoice_number)

    def test_numbers_follow_each_other_within_the_day(self):
        self.sell([(self.belt, "1", "120.00")], tendered="120")
        self.sell([(self.belt, "1", "120.00")], tendered="120")
        numbers = sorted(SalesInvoice.objects.values_list("invoice_number", flat=True))
        self.assertTrue(numbers[1].endswith("-0002"))

    def test_discount_and_too_large_discount(self):
        self.sell([(self.shirt, "1", "240.00")], tendered="200", discount="40")
        self.assertEqual(SalesInvoice.objects.get().total_amount, D("200.00"))
        self.sell([(self.shirt, "1", "240.00")], tendered="0", discount="500")
        self.assertEqual(SalesInvoice.objects.count(), 1)

    def test_walk_in_customer_cannot_buy_on_credit(self):
        response = self.sell([(self.shirt, "1", "240.00")], tendered="100", follow=True)
        self.assertContains(response, "البيع الآجل محتاج عميل باسمه")
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(get_cashbox_balance(self.cashbox), D("0.00"))

    def test_named_customer_credit_sale_goes_to_their_account(self):
        customer = make_customer(customer_code="C-POS", name="Ali")
        self.sell([(self.shirt, "1", "240.00")], tendered="100", customer=customer)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.paid_now, invoice.remaining_due, invoice.payment_status), (D("100.00"), D("140.00"), "partial"))
        self.assertEqual(CustomerLedgerEntry.objects.get(sales_invoice=invoice).due_increase, D("140.00"))

    def test_short_stock_keeps_nothing(self):
        before = StockMovement.objects.count()
        response = self.sell([(self.shirt, "50", "240.00")], tendered="12000", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("0.00"))
        self.assertContains(response, "op-message-error")

    def test_bad_lines_are_refused(self):
        for qty, price in (("0", "10"), ("-1", "10"), ("abc", "10"), ("1", "-5"), ("1", "NaN")):
            with self.subTest(qty=qty, price=price):
                self.sell([(self.belt, qty, price)], tendered="10")
        self.shirt.active = False
        self.shirt.save()
        self.sell([(self.shirt, "1", "240")], tendered="240")
        self.assertFalse(SalesInvoice.objects.exists())

    def test_empty_cart_is_refused(self):
        response = self.client.post(POS, {"line_count": "0", "location": self.location.pk, "cashbox": self.cashbox.pk, "tendered": "0"}, follow=True)
        self.assertContains(response, "السلة فاضية")
        self.assertFalse(SalesInvoice.objects.exists())

    def test_pay_and_print_opens_the_receipt_that_returns_to_the_till(self):
        from settings_core.models import ClientProfile
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(ClientProfile.get_active(), "pdf_printing", True)
        response = self.sell([(self.belt, "1", "120.00")], tendered="120", print_flag="1")
        invoice = SalesInvoice.objects.get()
        self.assertEqual(response.url, f"/print/sales/{invoice.pk}/?lang=ar&format=receipt&autoprint=1&next=pos")
        receipt = self.client.get(response.url)
        self.assertContains(receipt, 'href="/sales/pos/?lang=ar"')
        self.assertContains(receipt, "afterprint")

    def test_with_printing_off_the_sale_returns_to_the_till(self):
        response = self.sell([(self.belt, "1", "120.00")], tendered="120", print_flag="1")
        self.assertTrue(response.url.startswith("/sales/pos/?lang=ar&last="))
        self.assertNotContains(self.client.get(POS), "pay_print")

    def test_refused_sale_restores_a_clean_cart_only(self):
        payload = '[{"id": %d, "qty": "1", "price": "240"}, {"id": "</script><script>alert(1)</script>", "qty": "1", "price": "1"}]' % self.shirt.pk
        response = self.client.post(POS, {"line_count": "1", "item_0": str(self.shirt.pk), "qty_0": "1", "price_0": "240", "customer": walk_in_customer().pk, "location": self.location.pk, "cashbox": self.cashbox.pk, "tendered": "10", "cart_json": payload})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertEqual(response.context["cart_back"], [{"id": self.shirt.pk, "qty": "1.000", "price": "240.00"}])

    def test_screen_ships_catalog_without_cost_and_today_summary(self):
        self.sell([(self.belt, "2", "120.00")], tendered="240")
        page = self.client.get(POS)
        self.assertContains(page, "5901234123457")
        self.assertContains(page, "hesba/js/pos.js")
        self.assertNotContains(page, "100.0000")
        self.assertEqual(page.context["summary"]["count"], 1)
        self.assertEqual(page.context["summary"]["total"], D("240.00"))
        other = person(RoleCode.CASHIER, "pos_other")
        self.client.force_login(other)
        self.assertEqual(self.client.get(POS).context["summary"]["count"], 0)

    def test_permissions_and_module_gate(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "pos_keeper"))
        self.assertEqual(self.client.get(POS).status_code, 403)
        owner = person(RoleCode.OWNER, "pos_owner")
        self.client.force_login(owner)
        self.assertContains(self.client.get(reverse("sales:list")), reverse("sales:pos"))
        # sales_operations is required for a commercial activity, so it cannot be switched off;
        # the gate still covers /sales/pos/ through the /sales/ prefix.
        from settings_core.module_gate import module_for_path

        self.assertEqual(module_for_path(POS), "sales_operations")
