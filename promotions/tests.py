"""T5 (MARKET-PARITY): promotions and coupons fill the line discounts; posting stays as it is (HG-057)."""

from datetime import time, timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, open_test_shift, stock_in
from inventory.services import recalculate_item_average_cost
from master_data.models import Category
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.pos import checkout, walk_in_customer

from . import services
from .models import InvoicePromotion, Promotion, PromotionKind

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def line(item, quantity, price, discount="0"):
    return {"item": item, "quantity": D(str(quantity)), "unit_sale_price": D(price), "line_discount_amount": D(discount), "description": ""}


class PromotionSetup(TestCase):
    def setUp(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        prepared_client("commercial", "fashion")
        caps.set_capability_enabled(ClientProfile.get_active(), "promotions", True)
        self.owner = person(RoleCode.OWNER, "pr_owner")
        self.cashier = person(RoleCode.CASHIER, "pr_cashier")
        self.shoes = Category.objects.create(category_code="SHOES", name_ar="أحذية")
        self.sneaker = make_item(item_code="SNK", item_name="كوتشي", default_sale_price="500.00", category=self.shoes)
        self.sock = make_item(item_code="SOCK", item_name="شراب", default_sale_price="30.00")

    def promo(self, **fields):
        return Promotion.objects.create(**{"name": "عرض", "kind": PromotionKind.PERCENT, **fields})


class RuleTests(PromotionSetup):
    def test_percent_off_a_section(self):
        self.promo(kind=PromotionKind.PERCENT, percent=D("20"), category=self.shoes)
        lines, promo, off = services.apply([line(self.sneaker, 1, "500.00"), line(self.sock, 2, "30.00")])
        self.assertEqual([l["line_discount_amount"] for l in lines], [D("100.00"), D("0")])
        self.assertEqual(off, D("100.00"))

    def test_buy_two_get_one_free(self):
        self.promo(kind=PromotionKind.BUY_GET, buy_quantity=2, get_quantity=1, item=self.sock)
        lines, _promo, off = services.apply([line(self.sock, 7, "30.00")])
        self.assertEqual(off, D("60.00"))  # 7 socks: two groups of three, two free

    def test_an_amount_off_over_a_total_is_spread_over_the_lines(self):
        self.promo(kind=PromotionKind.AMOUNT_OVER, amount=D("50.00"), min_total=D("500.00"))
        lines, _promo, off = services.apply([line(self.sneaker, 1, "500.00"), line(self.sock, 1, "30.00")])
        self.assertEqual(off, D("50.00"))
        self.assertEqual(sum(l["line_discount_amount"] for l in lines), D("50.00"))
        _lines, none, nothing = services.apply([line(self.sock, 1, "30.00")])
        self.assertEqual((none, nothing), (None, D("0.00")))  # under the minimum

    def test_the_best_single_promotion_wins_and_a_typed_discount_stays(self):
        self.promo(name="10%", kind=PromotionKind.PERCENT, percent=D("10"))
        best = self.promo(name="25 off", kind=PromotionKind.AMOUNT_OVER, amount=D("80.00"), min_total=D("100.00"))
        lines, promo, off = services.apply([line(self.sneaker, 1, "500.00"), line(self.sock, 1, "30.00", discount="5.00")])
        self.assertEqual((promo, off), (best, D("80.00")))
        self.assertEqual(lines[1]["line_discount_amount"], D("5.00"))  # typed by hand: kept, takes no promotion
        self.assertEqual(lines[0]["line_discount_amount"], D("80.00"))

    def test_dates_hours_coupons_and_switching_off(self):
        self.promo(percent=D("10"), ends_on=TODAY - timedelta(days=1))
        self.promo(percent=D("10"), active=False)
        self.promo(percent=D("10"), from_time=time(3, 0), to_time=time(3, 1))
        coupon = self.promo(percent=D("15"), coupon_code="EID15")
        now = timezone.localtime().replace(hour=12)
        _l, promo, _off = services.apply([line(self.sock, 1, "30.00")], now=now)
        self.assertIsNone(promo)  # expired, off, out of hours, and the coupon was not typed
        _l, promo, off = services.apply([line(self.sock, 1, "30.00")], now=now, coupon="eid15")
        self.assertEqual((promo, off), (coupon, D("4.50")))
        with self.assertRaisesMessage(ValidationError, "مش موجود"):
            services.apply([line(self.sock, 1, "30.00")], coupon="NOPE")
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        caps.set_capability_enabled(ClientProfile.get_active(), "promotions", False)
        _l, promo, _off = services.apply([line(self.sock, 1, "30.00")], coupon="EID15")
        self.assertIsNone(promo)  # the feature is off

    def test_an_evening_past_midnight(self):
        promo = self.promo(percent=D("10"), from_time=time(22, 0), to_time=time(2, 0))
        late = timezone.localtime().replace(hour=23, minute=30)
        self.assertIn(promo, services.running(late))
        self.assertNotIn(promo, services.running(late.replace(hour=12)))

    def test_saving_from_the_form(self):
        promo = services.save({"name": "بلاك فرايداي", "kind": "percent", "percent": "30", "category": str(self.shoes.pk), "active": "1"}, self.owner)
        self.assertEqual((promo.percent, promo.category, promo.active), (D("30"), self.shoes, True))
        for bad, text in (({"name": "x", "kind": "percent", "percent": "120"}, "من 1 لـ 100"), ({"name": "x", "kind": "buy_get", "buy_quantity": "1.5", "get_quantity": "1"}, "أرقام صحيحة"),
                          ({"name": "x", "kind": "amount_over", "amount": "10"}, "الحد الأدنى"), ({"name": "x", "kind": "percent", "percent": "5", "from_time": "10:00"}, "الوقتين")):
            with self.assertRaisesMessage(ValidationError, text):
                services.save(bad, self.owner)
        services.save({"name": "c1", "kind": "percent", "percent": "5", "coupon_code": "SAVE5", "active": "1"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مستخدم"):
            services.save({"name": "c2", "kind": "percent", "percent": "6", "coupon_code": "save5", "active": "1"}, self.owner)
        with self.assertRaises(PermissionDenied):
            services.save({"name": "x", "kind": "percent", "percent": "5"}, self.cashier)


class SellingTests(PromotionSetup):
    def setUp(self):
        super().setUp()
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        for item in (self.sneaker, self.sock):
            stock_in(item, self.location, 20, "10.00", movement_date=TODAY)
            recalculate_item_average_cost(item)
        self.promo(name="الأحذية 20%", percent=D("20"), category=self.shoes)

    def test_the_till_takes_the_promotion_off_and_remembers_it(self):
        open_test_shift(self.cashier, self.cashbox)
        self.client.force_login(self.cashier)
        page = self.client.get(reverse("sales:pos") + "?lang=ar")
        self.assertContains(page, "data-pos-coupon")
        self.assertContains(page, "الأحذية 20%")
        response = self.client.post(reverse("sales:pos"), {"line_count": "1", "customer": str(walk_in_customer().pk), "location": str(self.location.pk),
                                                           "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "500", "print": "0",
                                                           "item_0": str(self.sneaker.pk), "qty_0": "1", "price_0": "500.00"})
        self.assertEqual(response.status_code, 302)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.status, invoice.total_amount), ("posted", D("400.00")))
        self.assertEqual(invoice.lines.get().line_discount_amount, D("100.00"))
        self.assertEqual(InvoicePromotion.objects.get(invoice=invoice).discount, D("100.00"))
        self.assertEqual(services.report()[InvoicePromotion.objects.get().promotion_id], {"invoices": 1, "discount": D("100.00")})

    def test_an_offline_sale_syncs_at_the_price_it_was_rung_up_at(self):
        invoice, _change = checkout(lines=[line(self.sneaker, 1, "500.00")], customer=walk_in_customer(), location=self.location, cashbox=self.cashbox,
                                    discount=D("0"), tendered=D("500.00"), user=self.owner)
        self.assertEqual(invoice.total_amount, D("500.00"))  # the sync path applies nothing
        self.assertFalse(InvoicePromotion.objects.exists())

    def test_the_invoice_form_with_a_coupon(self):
        Promotion.objects.create(name="كوبون", kind=PromotionKind.AMOUNT_OVER, amount=D("150.00"), min_total=D("300.00"), coupon_code="VIP")
        customer = make_customer(customer_code="PR-C", name="عميل")
        self.client.force_login(self.owner)
        open_test_shift(self.owner, self.cashbox)
        self.assertContains(self.client.get(reverse("sales:create") + "?lang=ar"), 'name="coupon"')
        data = {"lang": "ar", "invoice_number": "INV-PR-1", "invoice_date": TODAY.isoformat(), "customer": customer.pk, "selling_location": self.location.pk,
                "cashbox": self.cashbox.pk, "paid_now": "0", "discount_amount": "0", "tax_amount": "0", "coupon": "VIP",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
                "lines-0-item": self.sneaker.pk, "lines-0-quantity": "1", "lines-0-unit_sale_price": "500.00", "lines-0-line_discount_amount": "0"}
        response = self.client.post(reverse("sales:create"), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context.get("form") and response.context["form"].errors)
        invoice = SalesInvoice.objects.get(invoice_number="INV-PR-1")
        self.assertEqual(invoice.lines.get().line_discount_amount, D("150.00"))  # the coupon beats the 20% (100)
        self.assertEqual(invoice.promotion_use.coupon_code, "VIP")
        self.assertContains(self.client.get(reverse("sales:detail", args=[invoice.pk]) + "?lang=ar"), "data-invoice-promotion")
        bad = self.client.post(reverse("sales:create"), dict(data, invoice_number="INV-PR-2", coupon="WRONG"))
        self.assertContains(bad, "مش موجود")

    def test_the_promotions_screen(self):
        self.client.force_login(self.owner)
        url = reverse("promotions:list")
        for lang in ("ar", "en"):
            self.assertContains(self.client.get(url + f"?lang={lang}"), "data-promotion-form")
        response = self.client.post(url, {"lang": "ar", "name": "اشتري 2 وخد 1", "kind": "buy_get", "buy_quantity": "2", "get_quantity": "1",
                                          "item": str(self.sock.pk), "active": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Promotion.objects.filter(kind=PromotionKind.BUY_GET, item=self.sock).exists())
        self.assertContains(self.client.get(reverse("dashboard_snapshot") + "?lang=ar"), 'href="/promotions/?lang=ar"')
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.post(url, {"lang": "ar", "name": "x", "kind": "percent", "percent": "5"}).status_code, 403)
