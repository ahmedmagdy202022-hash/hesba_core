"""T5 (MARKET-PARITY): promotions and coupons fill the line discounts; posting stays as it is (HG-057)."""

from datetime import time, timedelta
from decimal import Decimal as D
from unittest import mock

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

    def test_checkout_itself_applies_nothing(self):
        invoice, _change = checkout(lines=[line(self.sneaker, 1, "500.00")], customer=walk_in_customer(), location=self.location, cashbox=self.cashbox,
                                    discount=D("0"), tendered=D("500.00"), user=self.owner)
        self.assertEqual(invoice.total_amount, D("500.00"))  # the views apply promotions before checkout, not checkout
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


class CodexFixesTests(SellingTests):
    """Codex on #268: the till shows the promotion before payment, unit rounding is not a typed discount, buy X get Y
    counts across lines, and a form with an error keeps the item or section it was for."""

    def test_the_till_quotes_the_promotion_and_refuses_a_payment_for_another_total(self):
        open_test_shift(self.cashier, self.cashbox)
        self.client.force_login(self.cashier)
        self.assertContains(self.client.get(reverse("sales:pos") + "?lang=ar"), "data-pos-promo-row")
        cart = {"line_count": "1", "item_0": str(self.sneaker.pk), "qty_0": "1", "price_0": "500.00"}
        quote = self.client.post(reverse("sales:pos_promotion"), cart).json()
        self.assertEqual((quote["discount"], quote["name"], quote["lines"]), ("100.00", "الأحذية 20%", ["100.00"]))
        sale = dict(cart, customer=str(walk_in_customer().pk), location=str(self.location.pk), cashbox=str(self.cashbox.pk), discount="0", tendered="500", print="0")
        refused = self.client.post(reverse("sales:pos"), dict(sale, promotion_seen="0.00"), follow=True)
        self.assertContains(refused, "العروض اتغيرت")
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(self.client.post(reverse("sales:pos"), dict(sale, promotion_seen="100.00")).status_code, 302)
        self.assertEqual(SalesInvoice.objects.get().total_amount, D("400.00"))

    def test_a_unit_price_rounding_is_not_a_typed_discount(self):
        converted = dict(line(self.sneaker, "3", "166.67", discount="0.01"), typed_discount=D("0.00"))  # a box of 3 at 500.00
        lines, promo, off = services.apply([converted])
        self.assertEqual(off, D("100.00"))  # 20% of the 500.00 the box costs
        self.assertEqual(lines[0]["line_discount_amount"], D("100.01"))  # the rounding cent stays
        typed = dict(line(self.sneaker, "3", "166.67", discount="5.01"), typed_discount=D("5.00"))
        self.assertIsNone(services.apply([typed])[1])  # a discount typed by hand still keeps the line out

    def test_buy_two_get_one_counts_across_lines(self):
        Promotion.objects.update(active=False)
        self.promo(kind=PromotionKind.BUY_GET, buy_quantity=2, get_quantity=1, item=self.sock)
        lines, _promo, off = services.apply([line(self.sock, 1, "30.00"), line(self.sock, 1, "30.00"), line(self.sock, 1, "25.00")])
        self.assertEqual(off, D("25.00"))  # three single serial lines: one free, the cheapest
        self.assertEqual([l["line_discount_amount"] for l in lines], [D("0"), D("0"), D("25.00")])

    def test_a_form_with_an_error_keeps_its_item(self):
        self.client.force_login(self.owner)
        page = self.client.post(reverse("promotions:list"), {"lang": "ar", "name": "x", "kind": "percent", "percent": "500", "item": str(self.sock.pk)})
        self.assertContains(page, f'<option value="{self.sock.pk}" selected>')


class SecondReviewTests(SellingTests):
    """Codex on #268 (second look): an offline sale syncs with the promotion of its moment, and the report follows the entity."""

    def sync(self, when, coupon="", seen="100.00", tendered="400"):
        import json
        import uuid

        from shifts.services import open_shift_for

        if open_shift_for(self.cashier) is None:
            open_test_shift(self.cashier, self.cashbox)
        self.client.force_login(self.cashier)
        body = {"key": str(uuid.uuid4()), "recorded_at": when.isoformat(), "customer": "", "location": str(self.location.pk), "cashbox": str(self.cashbox.pk),
                "discount": "0", "tendered": tendered, "coupon": coupon, "promotion_seen": seen, "lines": [{"id": self.sneaker.pk, "qty": "1", "price": "500.00", "unit": None, "serial": None}]}
        return self.client.post(reverse("sales:pos_sync"), json.dumps(body), content_type="application/json").json()

    def test_an_offline_sale_syncs_with_the_promotion_running_when_it_was_rung_up(self):
        answer = self.sync(timezone.now() - timedelta(minutes=30))
        self.assertEqual(answer["status"], "posted")
        invoice = SalesInvoice.objects.get()
        self.assertEqual(invoice.total_amount, D("400.00"))  # what the till showed and collected
        self.assertEqual(InvoicePromotion.objects.get(invoice=invoice).discount, D("100.00"))
        Promotion.objects.update(ends_on=TODAY - timedelta(days=3))
        answer = self.sync(timezone.now() - timedelta(days=5))  # rung up while it still ran
        self.assertEqual(answer["status"], "posted")
        self.assertEqual(SalesInvoice.objects.latest("pk").total_amount, D("400.00"))

    def test_the_report_follows_the_entity(self):
        from unittest import mock

        self.sync(timezone.now())
        promo = Promotion.objects.get()
        self.assertEqual(services.report()[promo.pk]["invoices"], 1)
        with mock.patch("entities.scope.scope", side_effect=lambda queryset, path: queryset.none()):
            self.assertEqual(services.report(), {})


class ThirdReviewTests(SecondReviewTests):
    """Codex on #268 (third look): no payment without a confirmed quote, and an offline sale is never re-priced by an edited promotion."""

    def test_the_till_takes_no_payment_before_the_quote_is_confirmed(self):
        open_test_shift(self.cashier, self.cashbox)
        self.client.force_login(self.cashier)
        sale = {"line_count": "1", "item_0": str(self.sneaker.pk), "qty_0": "2", "price_0": "500.00", "customer": str(walk_in_customer().pk),
                "location": str(self.location.pk), "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "900", "print": "0"}
        self.assertContains(self.client.post(reverse("sales:pos"), dict(sale, promotion_seen=""), follow=True), "العروض اتغيرت")
        self.assertFalse(SalesInvoice.objects.exists())
        from django.contrib.staticfiles import finders

        js = open(finders.find("hesba/js/pos.js"), encoding="utf-8").read()
        for needed in ("promoPending", "stopImmediatePropagation", "promo_wait", "seq !== promoSeq", "controller.abort()", "4000"):
            self.assertIn(needed, js)
        self.assertIn("promotion_seen:", open(finders.find("hesba/js/pos_offline.js"), encoding="utf-8").read())

    def test_an_offline_sale_is_refused_when_its_promotion_was_edited_or_switched_off(self):
        Promotion.objects.update(percent=D("10"))
        answer = self.sync(timezone.now() - timedelta(minutes=30))
        self.assertEqual(answer["status"], "refused")
        self.assertIn("100.00", answer["error"])
        Promotion.objects.update(percent=D("20"), active=False)
        self.assertEqual(self.sync(timezone.now() - timedelta(minutes=20))["status"], "refused")
        self.assertFalse(SalesInvoice.objects.exists())

    def test_an_offline_sale_without_a_quote_posts_at_the_full_price_shown(self):
        answer = self.sync(timezone.now() - timedelta(minutes=30), seen="", tendered="500")
        self.assertEqual(answer["status"], "posted")
        invoice = SalesInvoice.objects.get()
        self.assertEqual(invoice.total_amount, D("500.00"))
        self.assertFalse(InvoicePromotion.objects.filter(invoice=invoice).exists())


class FourthReviewTests(PromotionSetup):
    """Codex on #268 (fourth look): a fixed amount is taken off in full, and editing keeps the promotion's own target."""

    def test_a_fixed_amount_is_taken_off_in_full_when_the_last_line_is_small(self):
        self.promo(kind=PromotionKind.AMOUNT_OVER, amount=D("99.99"))
        lines = [line(self.sneaker, 1, "100.00") for _ in range(6)] + [line(self.sock, 1, "0.01")]
        lines, _promo, off = services.apply(lines)
        self.assertEqual(off, D("99.99"))
        self.assertEqual(sum(l["line_discount_amount"] for l in lines), D("99.99"))
        self.assertTrue(all(l["line_discount_amount"] <= l["quantity"] * l["unit_sale_price"] for l in lines))

    def test_editing_keeps_an_inactive_target(self):
        promo = self.promo(percent=D("10"), item=self.sock)
        type(self.sock).objects.filter(pk=self.sock.pk).update(active=False)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("promotions:list"), {"lang": "ar", "edit": promo.pk})
        self.assertContains(page, f'<option value="{self.sock.pk}" selected>')


class FifthReviewTests(SellingTests):
    """Codex on #268 (fifth look): the invoice form quotes the promotion live, so its total and "paid now" match it."""

    def test_the_invoice_form_quotes_the_promotion_before_saving(self):
        self.client.force_login(self.owner)
        form = self.client.get(reverse("sales:create"), {"lang": "ar"})
        self.assertContains(form, f'data-url="{reverse("sales:promotion_quote")}"')
        self.assertContains(form, "data-promo-line")
        cart = {"line_count": "2", "item_0": str(self.sneaker.pk), "qty_0": "1", "price_0": "500.00", "item_1": str(self.sneaker.pk), "qty_1": "1",
                "price_1": "500.00", "discount_1": "10"}
        quote = self.client.post(reverse("sales:promotion_quote"), cart).json()
        self.assertEqual((quote["discount"], quote["lines"]), ("100.00", ["100.00", "0.00"]))  # a line with a typed discount takes none
        from django.contrib.staticfiles import finders

        js = open(finders.find("hesba/js/invoice_form.js"), encoding="utf-8").read()
        for needed in ("quotePromotion", "promo.byLine[i]", "seq !== promoSeq", "discount_"):
            self.assertIn(needed, js)


class SixthReviewTests(SecondReviewTests):
    """Codex on #268 (sixth look): the promotion usage figures follow the sales-report permissions."""

    def test_usage_follows_the_sales_report_permissions(self):
        self.sync(timezone.now())  # rung up by self.cashier
        promo = Promotion.objects.get()
        self.assertEqual(services.report(user=self.owner)[promo.pk]["invoices"], 1)
        self.assertEqual(services.report(user=self.cashier)[promo.pk]["invoices"], 1)  # their own sale
        other = person(RoleCode.CASHIER, "pr_other_cashier")
        self.assertEqual(services.report(user=other), {})
        keeper = person(RoleCode.STOCK_KEEPER, "pr_keeper")
        self.assertEqual(services.report(user=keeper), {})


class PayItAllTests(PromotionSetup):
    """The invoice form's "pay it all": paid now follows the total the server charges after the promotion, the coupon
    and the invoice discount, however old the figure on the page is. A typed amount (no flag) is never changed."""

    def setUp(self):
        super().setUp()
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        for item in (self.sneaker, self.sock):
            stock_in(item, self.location, 20, "10.00", movement_date=TODAY)
            recalculate_item_average_cost(item)
        self.promo(name="الأحذية 20%", percent=D("20"), category=self.shoes)
        self.customer = make_customer(customer_code="PR-PAY", name="عميل")
        self.client.force_login(self.owner)
        open_test_shift(self.owner, self.cashbox)

    def data(self, **fields):
        return {"lang": "ar", "invoice_number": "", "invoice_date": TODAY.isoformat(), "customer": self.customer.pk, "selling_location": self.location.pk,
                "cashbox": self.cashbox.pk, "paid_now": "500.00", "discount_amount": "0", "tax_amount": "0",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
                "lines-0-item": self.sneaker.pk, "lines-0-quantity": "1", "lines-0-unit_sale_price": "500.00", "lines-0-line_discount_amount": "0", **fields}

    def pay_all_invoice(self, **fields):
        response = self.client.post(reverse("sales:create"), self.data(**{"pay_full": "1", "paid_now": "9999.00", **fields}))
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context.get("form") and response.context["form"].errors)
        return SalesInvoice.objects.get()

    def test_the_page_carries_the_flag_and_the_script_follows_the_quote(self):
        from django.contrib.staticfiles import finders

        page = self.client.get(reverse("sales:create") + "?lang=ar")
        self.assertContains(page, 'name="pay_full"')
        self.assertContains(page, "hesba/js/invoice_form.js?v=ux2-1")  # UX Wave 2 bumped it (quick item dialog)
        js = open(finders.find("hesba/js/invoice_form.js"), encoding="utf-8").read()
        for needed in ("if (payAllOn) {", "event.isTrusted", "payAll(false)", "quotePromotion();  // ask for the promotion now"):
            self.assertIn(needed, js)
        self.assertNotContains(self.client.get(reverse("purchases:create") + "?lang=ar"), 'name="pay_full"')

    def test_pay_all_with_a_stale_total_saves_paid_equal_to_the_discounted_total(self):
        response = self.client.post(reverse("sales:create"), self.data(pay_full="1"))  # 500 is the total before the 20% promotion
        self.assertEqual(response.status_code, 302)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("400.00"), D("400.00"), D("0.00")))
        self.assertEqual(invoice.promotion_use.discount, D("100.00"))

    def test_pay_all_with_a_coupon(self):
        Promotion.objects.create(name="كوبون", kind=PromotionKind.AMOUNT_OVER, amount=D("150.00"), min_total=D("300.00"), coupon_code="VIP")
        invoice = self.pay_all_invoice(coupon="VIP")  # the coupon beats the 20%: 500 - 150
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("350.00"), D("350.00"), D("0.00")))

    def test_pay_all_with_an_invoice_discount(self):
        invoice = self.pay_all_invoice(discount_amount="50.00")
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("350.00"), D("350.00"), D("0.00")))

    def test_pay_all_with_vat(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        caps.set_capability_enabled(ClientProfile.get_active(), "vat", True)
        invoice = self.pay_all_invoice()
        self.assertGreater(invoice.tax_amount, 0)
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("400.00") + invoice.tax_amount, D("400.00") + invoice.tax_amount, D("0.00")))

    def test_without_the_flag_nothing_changes(self):
        too_much = self.client.post(reverse("sales:create"), self.data())  # still the overpaid message
        self.assertEqual(too_much.status_code, 200)
        self.assertContains(too_much, "المدفوع أكبر من إجمالي الفاتورة")
        self.assertFalse(SalesInvoice.objects.exists())
        partly = self.client.post(reverse("sales:create"), self.data(paid_now="150.00", pay_full=""))  # a typed part payment stays
        self.assertEqual(partly.status_code, 302)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("400.00"), D("150.00"), D("250.00")))

    def test_a_sale_without_a_promotion_pays_in_full_as_before(self):
        Promotion.objects.all().delete()
        invoice = self.pay_all_invoice()
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("500.00"), D("500.00"), D("0.00")))

    def test_a_failed_save_keeps_pay_all_on(self):
        from django.contrib.staticfiles import finders

        bad = self.client.post(reverse("sales:create"), self.data(pay_full="1", coupon="WRONG"))
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, 'name="pay_full" value="1"')
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertContains(self.client.get(reverse("sales:create") + "?lang=ar"), 'name="pay_full" value=""')  # a fresh form starts off
        js = open(finders.find("hesba/js/invoice_form.js"), encoding="utf-8").read()
        self.assertIn("var payAllOn = !!payFull && payFull.value === '1';", js)  # and the script picks it up on load

    def test_pay_full_matches_the_service_total_in_every_case(self):
        """Guards drift between the view's ``_pay_full_amount`` and the total ``sales/services.py`` computes: for each case
        the amount the view works out (recorded as it returns) must be what the draft really totals, so paid now equals
        the total and nothing is left due. If the service's arithmetic ever changes, this fails."""
        from sales import views
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile
        from units.models import ItemUnit
        from loyalty.models import EntryKind, LoyaltySettings, PointsEntry

        caps.set_capability_enabled(ClientProfile.get_active(), "loyalty", True)
        LoyaltySettings.objects.update_or_create(pk=1, defaults={"amount_per_point": D("10.00"), "point_value": D("0.50"), "min_points": 20, "expiry_months": 0})
        PointsEntry.objects.create(customer=self.customer, kind=EntryKind.EARN, points=1000, entry_date=TODAY)
        Promotion.objects.create(name="كوبون", kind=PromotionKind.AMOUNT_OVER, amount=D("150.00"), min_total=D("300.00"), coupon_code="VIP")
        carton = ItemUnit.objects.create(item=self.sneaker, name_ar="كرتونة", name_en="Carton", factor=D("12"), sale_price=D("5000.00"))
        many = {"lines-TOTAL_FORMS": "3",
                "lines-0-item": self.sock.pk, "lines-0-quantity": "3", "lines-0-unit_sale_price": "33.33", "lines-0-line_discount_amount": "0",
                "lines-1-item": self.sock.pk, "lines-1-quantity": "0.333", "lines-1-unit_sale_price": "33.33", "lines-1-line_discount_amount": "0",
                "lines-2-item": self.sneaker.pk, "lines-2-quantity": "1.5", "lines-2-unit_sale_price": "99.99", "lines-2-line_discount_amount": "0.01"}
        unit = {"lines-0-unit": carton.pk, "lines-0-quantity": "3", "lines-0-unit_sale_price": "5000.00"}
        cases = {
            "plain": {},
            "invoice discount": {"discount_amount": "33.33"},
            "coupon": {"coupon": "VIP"},
            "points": {"redeem_points": "40"},
            "points and coupon": {"redeem_points": "40", "coupon": "VIP", "discount_amount": "10.00"},
            "unit conversion": unit,
            "unit conversion and discount": dict(unit, discount_amount="12.34"),
            "rounded lines": many,
            "rounded lines and discount": dict(many, discount_amount="7.77"),
        }
        recorded = []
        real = views._pay_full_amount

        def spy(header, lines):
            recorded.append(real(header, lines))
            return recorded[-1]

        for promotion in (False, True):
            Promotion.objects.filter(coupon_code="").update(active=promotion)
            for vat in (False, True):
                caps.set_capability_enabled(ClientProfile.get_active(), "vat", vat)
                for name, fields in cases.items():
                    with self.subTest(case=name, promotion=promotion, vat=vat):
                        recorded.clear()
                        before = set(SalesInvoice.objects.values_list("pk", flat=True))
                        with mock.patch("sales.views._pay_full_amount", spy):
                            response = self.client.post(reverse("sales:create"), self.data(**{"pay_full": "1", "paid_now": "99999.00", **fields}))
                        errors = getattr(response, "context", None) and response.context.get("form") and response.context["form"].errors
                        self.assertEqual(response.status_code, 302, errors)
                        invoice = SalesInvoice.objects.exclude(pk__in=before).get()
                        self.assertEqual(len(recorded), 1)
                        self.assertEqual(recorded[0], invoice.total_amount)
                        self.assertEqual(invoice.paid_now, invoice.total_amount)
                        self.assertEqual(invoice.remaining_due, D("0.00"))
                        if vat:
                            self.assertGreater(invoice.tax_amount, 0)
