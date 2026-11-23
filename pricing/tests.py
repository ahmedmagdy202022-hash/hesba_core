"""PRICE-001: price lists suggest prices; posting keeps whatever was charged."""

import json
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from .models import CustomerPriceList, PriceList, PriceListItem
from .services import adjusted, assign_customers, list_prices, price_book, price_for, save_price_list, set_item_prices


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class PriceResolutionTests(TestCase):
    def setUp(self):
        prepared_client(sub_activity="wholesale")  # price lists suggested -> on
        self.owner = person(RoleCode.OWNER, "pl_owner")
        self.rice = make_item(item_code="RICE", item_name="Rice", default_sale_price=D("30.00"))
        self.oil = make_item(item_code="OIL", item_name="Oil", default_sale_price=D("85.50"))
        self.wholesale = save_price_list(user=self.owner, code="wholesale", name_ar="جملة", adjust_percent=D("-10"))
        self.karim = make_customer(customer_code="KARIM", name="Karim")
        self.walk = make_customer(customer_code="MONA", name="Mona")

    def test_explicit_price_then_rule_then_retail(self):
        set_item_prices(self.wholesale, {self.rice: D("26.5")}, self.owner)
        assign_customers(self.wholesale, [self.karim], self.owner)
        self.assertEqual(price_for(self.rice, self.karim), D("26.50"))  # explicit
        self.assertEqual(price_for(self.oil, self.karim), D("76.95"))   # 85.50 - 10%
        self.assertEqual(price_for(self.oil, self.walk), D("85.50"))    # no list: retail
        self.assertEqual(list_prices(self.wholesale)[self.oil.pk], D("76.95"))
        self.assertEqual(adjusted(D("10.00"), D("5")), D("10.50"))

    def test_inactive_list_or_capability_off_means_retail(self):
        assign_customers(self.wholesale, [self.karim], self.owner)
        save_price_list(user=self.owner, instance=self.wholesale, code="WHOLESALE", name_ar="جملة", adjust_percent=D("-10"), active=False)
        self.assertEqual(price_for(self.oil, self.karim), D("85.50"))
        save_price_list(user=self.owner, instance=self.wholesale, code="WHOLESALE", name_ar="جملة", adjust_percent=D("-10"), active=True)
        set_capability_enabled(ClientProfile.get_active(), "price_lists", False, user=self.owner)
        self.assertEqual(price_for(self.oil, self.karim), D("85.50"))
        self.assertIsNone(price_book())

    def test_price_book_for_the_screens(self):
        self.assertIsNone(price_book())  # nobody on a list yet
        assign_customers(self.wholesale, [self.karim], self.owner)
        book = price_book()
        self.assertEqual(book["customers"], {str(self.karim.pk): str(self.wholesale.pk)})
        self.assertEqual(book["lists"][str(self.wholesale.pk)]["prices"][str(self.oil.pk)], "76.95")
        self.assertEqual(book["lists"][str(self.wholesale.pk)]["name_ar"], "جملة")

    def test_a_customer_is_on_one_list_and_moves(self):
        vip = save_price_list(user=self.owner, code="VIP", name_ar="كبار العملاء")
        assign_customers(self.wholesale, [self.karim, self.walk], self.owner)
        assign_customers(vip, [self.karim], self.owner)
        self.assertEqual(CustomerPriceList.objects.get(customer=self.karim).price_list, vip)
        assign_customers(self.wholesale, [], self.owner)
        self.assertFalse(CustomerPriceList.objects.filter(customer=self.walk).exists())
        log = AuditLog.objects.filter(action="assign_customers").latest("pk")
        self.assertEqual((log.before_data, log.after_data), ({"customers": ["MONA"]}, {"customers": []}))

    def test_clearing_a_price_returns_to_the_rule_and_is_audited(self):
        set_item_prices(self.wholesale, {self.rice: D("26.50")}, self.owner)
        self.assertEqual(set_item_prices(self.wholesale, {self.rice: D("26.50")}, self.owner), 0)  # no change
        set_item_prices(self.wholesale, {self.rice: None}, self.owner)
        self.assertFalse(PriceListItem.objects.exists())
        log = AuditLog.objects.filter(action="set_item_prices").latest("pk")
        self.assertEqual((log.before_data, log.after_data), ({"prices": {"RICE": "26.50"}}, {"prices": {"RICE": None}}))

    def test_validation(self):
        with self.assertRaises(ValidationError):
            save_price_list(user=self.owner, code="WHOLESALE", name_ar="مكرر")
        with self.assertRaises(ValidationError):
            save_price_list(user=self.owner, code="X", name_ar="x", adjust_percent=D("-150"))
        with self.assertRaises(ValidationError):
            set_item_prices(self.wholesale, {self.rice: D("-1")}, self.owner)
        self.assertEqual(self.wholesale.code, "WHOLESALE")  # codes are upper-cased


class PostingIsUnchangedTests(TestCase):
    def test_the_invoice_keeps_the_price_actually_charged(self):
        from datetime import timedelta

        from django.utils import timezone

        from closing.models import Period
        from hesba_testing.factories import make_cashbox, make_location, stock_in
        from inventory.services import recalculate_item_average_cost
        from sales.services import create_sales_draft, post_sales_invoice

        prepared_client(sub_activity="wholesale")
        today = timezone.localdate()
        Period.objects.create(period_code="PL", name="pl", start_date=today - timedelta(days=5), end_date=today + timedelta(days=5))
        owner = person(RoleCode.OWNER, "pl_post_owner")
        item = make_item(item_code="TEA", item_name="Tea", default_sale_price=D("20.00"))
        location = make_location()
        stock_in(item, location, 10, "8.00", movement_date=today)
        recalculate_item_average_cost(item)
        customer = make_customer(customer_code="WH", name="Wholesaler")
        price_list = save_price_list(user=owner, code="WH", name_ar="جملة", adjust_percent=D("-25"))
        assign_customers(price_list, [customer], owner)
        suggested = price_for(item, customer)
        self.assertEqual(suggested, D("15.00"))
        invoice = create_sales_draft(
            {"invoice_number": "PL-1", "invoice_date": today, "customer": customer, "selling_location": location, "cashbox": make_cashbox(), "paid_now": D("0")},
            [{"item": item, "quantity": D("2"), "unit_sale_price": D("16.00")}],  # cashier typed 16
            owner,
        )
        post_sales_invoice(invoice.pk, owner)
        line = invoice.lines.get()
        self.assertEqual((line.unit_sale_price, line.line_total_amount, line.line_profit_amount), (D("16.00"), D("32.00"), D("16.00")))


class PriceListScreenTests(TestCase):
    def setUp(self):
        prepared_client(sub_activity="wholesale")
        self.rice = make_item(item_code="RICE", item_name="Rice", default_sale_price=D("30.00"))
        self.karim = make_customer(customer_code="KARIM", name="Karim")

    def test_owner_creates_prices_and_assigns(self):
        self.client.force_login(person(RoleCode.OWNER, "pls_owner"))
        self.assertContains(self.client.get(reverse("master_data:items")), reverse("pricing:list"))
        created = self.client.post(reverse("pricing:list"), {"code": "wh", "name_ar": "جملة", "adjust_percent": "-10", "active": "1"})
        price_list = PriceList.objects.get(code="WH")
        self.assertRedirects(created, f"{reverse('pricing:detail', args=[price_list.pk])}?lang=ar", fetch_redirect_response=False)
        page = self.client.get(reverse("pricing:detail", args=[price_list.pk]))
        self.assertContains(page, 'placeholder="27.00"')  # 30 - 10%
        url = reverse("pricing:detail", args=[price_list.pk])
        self.client.post(url, {"action": "prices", f"shown_{self.rice.pk}": "1", f"price_{self.rice.pk}": "25,5"})
        self.assertEqual(PriceListItem.objects.get(price_list=price_list, item=self.rice).price, D("25.50"))
        self.client.post(url, {"action": "customers", "customer": [str(self.karim.pk)]})
        self.assertTrue(CustomerPriceList.objects.filter(customer=self.karim, price_list=price_list).exists())
        bad = self.client.post(url, {"action": "prices", f"shown_{self.rice.pk}": "1", f"price_{self.rice.pk}": "abc"}, follow=True)
        self.assertContains(bad, "أسعار غير صحيحة")
        self.assertEqual(PriceListItem.objects.get(price_list=price_list, item=self.rice).price, D("25.50"))

    def test_sales_form_and_pos_get_the_price_book(self):
        owner = person(RoleCode.OWNER, "pls_owner2")
        price_list = save_price_list(user=owner, code="WH", name_ar="جملة", adjust_percent=D("-10"))
        assign_customers(price_list, [self.karim], owner)
        set_capability_enabled(ClientProfile.get_active(), "pos", True, user=owner)
        self.client.force_login(owner)
        form = self.client.get(reverse("sales:create"))
        self.assertContains(form, 'id="hs-price-book"')
        book = json.loads(form.content.decode().split('id="hs-price-book" type="application/json">')[1].split("</script>")[0])
        self.assertEqual(book["lists"][str(price_list.pk)]["prices"][str(self.rice.pk)], "27.00")
        self.assertContains(self.client.get(reverse("sales:pos")), 'id="hs-price-book"')
        self.assertNotContains(self.client.get(reverse("purchases:create")), 'id="hs-price-book"')

    def test_permissions_and_capability_gate(self):
        # A cashier may look (prices are not secret) but not change anything.
        self.client.force_login(person(RoleCode.CASHIER, "pls_cashier"))
        page = self.client.get(reverse("pricing:list"))
        self.assertIn(page.status_code, (200, 403))
        self.assertEqual(self.client.post(reverse("pricing:list"), {"code": "X", "name_ar": "x"}).status_code, 403)
        self.assertFalse(PriceList.objects.exists())
        owner = person(RoleCode.OWNER, "pls_owner3")
        set_capability_enabled(ClientProfile.get_active(), "price_lists", False, user=owner)
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("pricing:list")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("master_data:items")), reverse("pricing:list"))
