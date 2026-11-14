"""Restaurants R6 (HG-114): coupons, points, gift cards and happy hour on the table bill.

The bill takes them as the cashier screen does, through the unchanged ``checkout(promotion=…, points=…, gift=…)``:

* **happy hour by order time:** 50% on drinks 17:00–19:00. A tea (20.00) ordered at 18:30 and paid at 19:30 keeps its
  10.00 off; a tea ordered at 19:10 is full price. VAT on: 10.00 + 1.40 and 20.00 + 2.80 = **34.20**; VAT off **30.00**;
* **points:** 200 points worth 20.00 (0.10 a point) on two burgers with cheese, 270.00 (VAT on 307.80): **287.80**, the
  VAT unchanged (37.80); VAT off 270.00 → **250.00**. The bill then earns floor(287.80 / 10) = **28** points, the till's
  rule. A walk-in cannot spend points;
* **a gift card** of 100.00 on 307.80: the card pays 100.00, the cash 207.80; the card's balance goes down by 100.00 and
  the till up by 207.80;
* **a coupon** «WELCOME10» (10%) on the same bill: 24.00 + 3.00 off; VAT on 216.00 + 27.00 + 30.24 + 3.78 = **277.02**;
* a combo (R1b) takes no offer, not even a component whose share of the saving rounded to 0.00; the service (R3) is worked
  out on the food after the offer, and the charge lines are never discounted by it;
* a cancelled bill gives the points and the card's money back by the listeners that already follow the sales engine.

The clock stands still on Wednesday 11 Nov 2026 (Cairo); a test moves it to place an order line and the payment at
different times, as ``tests_charges`` does.
"""

from contextlib import contextmanager
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from cashboxes.services import get_cashbox_balance
from giftcards import services as giftcards
from hesba_testing.factories import make_customer, make_item, stock_in
from loyalty import services as loyalty
from loyalty.models import EntryKind, InvoiceRedemption, LoyaltySettings, PointsEntry
from master_data.models import Category
from promotions import services as promotions
from promotions.models import InvoicePromotion, Promotion, PromotionKind
from sales.models import SalesInvoice
from sales.pos import walk_in_customer
from sales.services import cancel_posted_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from taxes.models import SalesLineTax

from . import charges, combos, offers, services
from .models import Order, OrderLine, OrderStatus
from .tests_charges import _counts as _all_counts
from .tests_combos import meal
from .tests_split_pay import SplitBillSetup, walk_in_balance

TZ = timezone.get_default_timezone()
DAY = datetime(2026, 11, 11, 12, 0)


def at(hour, minute=0):
    return timezone.make_aware(DAY.replace(hour=hour, minute=minute), TZ)


def _counts():
    """Every row in the database but the login's own session (the screen tests sign in between the two counts)."""

    return {label: count for label, count in _all_counts().items() if label != "sessions.Session"}


@contextmanager
def clock(hour, minute=0):
    with mock.patch("django.utils.timezone.now", return_value=at(hour, minute)):
        yield


class OfferSetup(SplitBillSetup):
    def setUp(self):
        super().setUp()
        profile = ClientProfile.get_active()
        for slug in ("promotions", "loyalty", "gift_cards"):
            set_capability_enabled(profile, slug, True, user=self.owner)
        self.drinks = Category.objects.create(category_code="DRINKS", name_ar="مشروبات")
        self.tea20 = make_item(item_code="D-TEA20", item_name="شاي بالنعناع", default_sale_price="20.00", is_stock_tracked=False, category=self.drinks)
        self.mona = make_customer(customer_code="C-MONA", name="منى سامي", phone="0100 123 4567")
        LoyaltySettings.objects.update_or_create(pk=1, defaults={"amount_per_point": D("10.00"), "point_value": D("0.10"), "min_points": 20, "expiry_months": 0})

    # ---- helpers ------------------------------------------------------------------------------------------------

    def happy_hour(self, **extra):
        return Promotion.objects.create(**{"name": "هابي أور", "kind": PromotionKind.PERCENT, "percent": D("50"), "category": self.drinks,
                                           "from_time": time(17, 0), "to_time": time(19, 0), **extra})

    def coupon(self, code="WELCOME10", percent="10", **extra):
        return Promotion.objects.create(**{"name": "كوبون الترحيب", "kind": PromotionKind.PERCENT, "percent": D(percent), "coupon_code": code, **extra})

    def give_points(self, customer, points=200):
        PointsEntry.objects.create(customer=customer, kind=EntryKind.EARN, points=points, entry_date=DAY.date() - timedelta(days=3))

    def teas(self, table=None):
        """A tea ordered at 18:30 (sent to the kitchen) and another at 19:10: two lines."""

        with clock(18, 30):
            order = services.open_order(self.owner, table=table or self.a_t1, guests=2)
            services.add_item(order, self.tea20, self.owner, 1)
            services.send_to_kitchen(order, self.owner)
        with clock(19, 10):
            services.add_item(order, self.tea20, self.owner, 1)
        return order

    def pay(self, order, user=None, cashbox=None, **extra):
        extra.setdefault("tendered", D("10000"))
        return services.pay(Order.objects.get(pk=order.pk), user or self.owner, cashbox=cashbox or self.cashbox, **extra)[0]

    def invoice_rows(self, invoice):
        taxes = {row.line_id: row.tax_amount for row in SalesLineTax.objects.filter(line__invoice=invoice)}
        return [(line.item.item_code, line.quantity, line.unit_sale_price, line.line_discount_amount, line.line_total_amount, taxes.get(line.pk, D("0.00")))
                for line in invoice.lines.select_related("item").order_by("line_number")]

    def preview_rows(self, order, offer=None):
        bill = charges.preview(Order.objects.get(pk=order.pk), offer=offer)
        return [(row["code"], row["quantity"], row["unit_sale_price"], row["line_discount_amount"], row["net"], row["tax"]) for row in bill["rows"]]

    def screen_pay(self, order, user=None, **data):
        body = {"lang": "ar", "action": "pay", "cashbox": str(self.cashbox.pk), "tendered": "10000", "discount": "0", "print": "0", **data}
        self.client.force_login(user or self.owner)
        return self.client.post(reverse("restaurant:order", args=[order.pk]), body, follow=True)

    def page(self, order, user=None, **query):
        self.client.force_login(user or self.owner)
        return self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", **query})


# ---- promotions: a line's own time («at»), HG-114 ----------------------------------------------------------------------


class LineTimeTests(OfferSetup):
    def row(self, ordered=None, item=None):
        row = {"item": item or self.tea20, "quantity": D("1"), "unit_sale_price": D("20.00"), "line_discount_amount": D("0"), "description": ""}
        if ordered is not None:
            row["at"] = ordered
        return row

    def test_a_line_ordered_inside_the_hours_keeps_the_offer_after_them(self):
        happy = self.happy_hour()
        filled, promo, off = promotions.apply([self.row(at(18, 30)), self.row(at(19, 10))], now=at(19, 30))
        self.assertEqual((promo, off), (happy, D("10.00")))
        self.assertEqual([row["line_discount_amount"] for row in filled], [D("10.00"), D("0")])
        self.assertEqual(filled[0]["at"], at(18, 30))  # read, never changed

    def test_a_line_ordered_before_the_hours_is_full_price_even_paid_inside_them(self):
        self.happy_hour()
        self.assertEqual(promotions.apply([self.row(at(16, 30))], now=at(18, 0))[1:], (None, D("0.00")))

    def test_without_now_the_clock_still_judges_a_line_without_its_own_time(self):
        happy = self.happy_hour()
        with clock(19, 30):
            filled, promo, off = promotions.apply([self.row(at(18, 30)), self.row()])
        self.assertEqual((promo, off), (happy, D("10.00")))  # the line with no time is judged at 19:30: outside
        self.assertEqual([row["line_discount_amount"] for row in filled], [D("10.00"), D("0")])

    def test_a_coupon_whose_hours_cover_the_order_time_is_accepted_after_them(self):
        lunch = self.coupon(code="LUNCH", percent="20", from_time=time(12, 0), to_time=time(15, 0))
        self.assertEqual(promotions.apply([self.row(at(14, 50))], now=at(15, 20), coupon="lunch")[1:], (lunch, D("4.00")))
        with self.assertRaisesMessage(ValidationError, "LUNCH"):
            promotions.apply([self.row()], now=at(15, 20), coupon="LUNCH")  # the till at 15:20: refused, as before

    def test_the_dates_are_still_judged_at_now(self):
        self.happy_hour(ends_on=DAY.date() - timedelta(days=1))
        self.assertEqual(promotions.apply([self.row(at(18, 30))], now=at(19, 30))[1:], (None, D("0.00")))


# ---- happy hour on the table bill -------------------------------------------------------------------------------------


class HappyHourTests(OfferSetup):
    def test_a_tea_ordered_at_18_30_keeps_half_off_when_paid_at_19_30_vat_on_34_20(self):
        self.happy_hour()
        order = self.teas()
        with clock(19, 30):
            self.assertEqual(services.bill_total(order, list(order.lines.all())), D("34.20"))
            invoice = self.pay(order)
        self.assertEqual(self.invoice_rows(invoice), [("D-TEA20", D("1.000"), D("20.00"), D("10.00"), D("10.00"), D("1.40")),
                                                      ("D-TEA20", D("1.000"), D("20.00"), D("0.00"), D("20.00"), D("2.80"))])
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.discount_amount, invoice.total_amount), (D("30.00"), D("4.20"), D("0.00"), D("34.20")))
        use = InvoicePromotion.objects.get(invoice=invoice)
        self.assertEqual((use.promotion.name, use.discount, use.coupon_code), ("هابي أور", D("10.00"), ""))

    def test_vat_off_30_00(self):
        self.vat(False)
        self.happy_hour()
        order = self.teas()
        with clock(19, 30):
            self.assertEqual(self.pay(order).total_amount, D("30.00"))

    def test_a_tea_ordered_before_the_hour_is_full_price_though_paid_inside_it(self):
        self.vat(False)
        self.happy_hour()
        with clock(16, 30):
            order = services.open_order(self.owner, table=self.a_t1, guests=1)
            services.add_item(order, self.tea20, self.owner, 2)
        with clock(18, 0):
            invoice = self.pay(order)
        self.assertEqual(invoice.total_amount, D("40.00"))
        self.assertFalse(InvoicePromotion.objects.exists())

    def test_an_unsent_tea_added_later_joins_its_line_and_keeps_its_time(self):
        """Default (unchanged add_item): an identical unsent plain line takes the new quantity; that line's time is the
        first order's. Once the first tea went to the kitchen, a later one is its own line (the test above)."""

        self.vat(False)
        self.happy_hour()
        with clock(18, 30):
            order = services.open_order(self.owner, table=self.a_t1, guests=1)
            services.add_item(order, self.tea20, self.owner, 1)
        with clock(19, 10):
            services.add_item(order, self.tea20, self.owner, 1)
        self.assertEqual(order.lines.count(), 1)
        with clock(19, 30):
            self.assertEqual(self.pay(order).total_amount, D("20.00"))

    def test_a_part_split_after_the_hour_keeps_its_teas_order_time(self):
        self.vat(False)
        self.happy_hour()
        with clock(18, 30):
            order = services.open_order(self.owner, table=self.a_t1, guests=2)
            tea = services.add_item(order, self.tea20, self.owner, 2)
            services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk])
            services.send_to_kitchen(order, self.owner)
        with clock(19, 20):
            part = services.split_order(order, {str(tea.pk): "1"}, self.owner)
        self.assertEqual(part.lines.get().created_at, at(18, 30))  # R2a's copy keeps when it was ordered
        with clock(19, 30):
            self.assertEqual(self.pay(part).total_amount, D("10.00"))
            self.assertEqual(self.pay(order).total_amount, D("130.00"))  # its own tea: 10.00, the burger 120.00

    def test_the_board_and_the_page_show_the_bill_after_the_offer(self):
        self.vat(False)
        self.happy_hour()
        order = self.teas()
        with clock(19, 30):
            tables, _others = services.board()
            self.assertEqual(next(row["bill"] for row in tables if row["order"] and row["order"].pk == order.pk), D("30.00"))
            page = self.page(order)
        self.assertContains(page, 'data-offer-row data-offer-amount="10.00"')
        self.assertContains(page, "عرض: هابي أور")
        self.assertContains(page, "data-order-total>30.00")


# ---- coupons ------------------------------------------------------------------------------------------------------------


class CouponTests(OfferSetup):
    def test_a_coupon_on_the_table_bill_vat_on_277_02(self):
        self.coupon()
        order = self.burgers()
        invoice = self.pay(order, coupon="welcome10")
        self.assertEqual(self.invoice_rows(invoice), [("D-BURG", D("2.000"), D("120.00"), D("24.00"), D("216.00"), D("30.24")),
                                                      ("A-CHEESE", D("2.000"), D("15.00"), D("3.00"), D("27.00"), D("3.78"))])
        self.assertEqual(invoice.total_amount, D("277.02"))
        use = InvoicePromotion.objects.get(invoice=invoice)
        self.assertEqual((use.discount, use.coupon_code), (D("27.00"), "welcome10"))

    def test_vat_off_243_00_and_without_the_code_the_coupon_does_not_count(self):
        self.vat(False)
        self.coupon()
        self.assertEqual(self.pay(self.burgers()).total_amount, D("270.00"))
        other = services.open_order(self.owner, table=self.b_t1, guests=1)
        services.add_item(other, self.burger, self.owner, 2, options=[self.medium.pk, self.cheese_opt.pk])
        self.assertEqual(self.pay(other, cashbox=self.b_cashbox, coupon="WELCOME10").total_amount, D("243.00"))

    def test_an_expired_or_unknown_coupon_is_refused_and_nothing_is_kept(self):
        self.coupon(code="OLD", ends_on=DAY.date() - timedelta(days=1))
        order = self.burgers()
        before = _counts()
        for code in ("OLD", "NOPE"):
            response = self.screen_pay(order, coupon=code)
            self.assertContains(response, f"الكوبون «{code}» مش موجود أو مش شغال دلوقتي.")
        self.assertEqual(_counts(), before)
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.OPEN)

    def test_a_part_takes_its_own_coupon(self):
        self.vat(False)
        self.coupon()
        order = self.burgers()
        services.add_item(order, self.tea20, self.owner, 1)
        tea = order.lines.get(item=self.tea20)
        part = services.split_order(order, {str(tea.pk): "1"}, self.owner)
        self.assertEqual(self.pay(part, coupon="WELCOME10").total_amount, D("18.00"))
        invoice = self.pay(order)  # the rest of the table's bill, no coupon typed
        self.assertEqual(invoice.total_amount, D("270.00"))
        self.assertEqual(InvoicePromotion.objects.count(), 1)

    def test_a_stale_offer_is_refused_as_at_the_till(self):
        self.coupon()
        order = self.burgers()
        before = _counts()
        response = self.screen_pay(order, coupon="WELCOME10", promotion_seen="0.00")
        self.assertContains(response, "العروض اتغيرت")
        self.assertEqual(_counts(), before)
        response = self.screen_pay(order, coupon="WELCOME10", promotion_seen="27.00")
        self.assertEqual(SalesInvoice.objects.get().total_amount, D("277.02"))


# ---- combos and the charges (R1b, R3) --------------------------------------------------------------------------------------


class ComboAndChargeTests(OfferSetup):
    def combo(self, price="150.00"):
        burger = make_item(item_code="C-BURG", item_name="برجر الوجبة", default_sale_price="120.00", is_stock_tracked=False)
        fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=True)
        stock_in(pepsi, self.location, 20, "8.00")
        combo = combos.save_combo(meal(burger, fries, large, pepsi, price=price), self.owner)
        sandwich, side, drink = combo.slots.order_by("sort", "pk")
        picks = {sandwich.pk: sandwich.choices.get().pk, side.pk: side.choices.order_by("sort", "pk").first().pk, drink.pk: drink.choices.get().pk}
        order = services.open_order(self.owner, table=self.a_t1, guests=2)
        services.add_combo(order, combo, self.owner, picks)
        return order

    def test_a_combo_takes_no_offer_and_a_dish_beside_it_does(self):
        self.vat(False)
        Promotion.objects.create(name="عشرة في المية", kind=PromotionKind.PERCENT, percent=D("10"))
        order = self.combo()
        services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk])
        invoice = self.pay(order)
        self.assertEqual([(row[0], row[3]) for row in self.invoice_rows(invoice)],
                         [("C-BURG", D("22.70")), ("C-FRIES", D("7.57")), ("C-PEPSI", D("4.73")), ("D-BURG", D("12.00"))])
        self.assertEqual(InvoicePromotion.objects.get(invoice=invoice).discount, D("12.00"))

    def test_a_component_whose_share_rounded_to_0_00_takes_no_offer_either(self):
        self.vat(False)
        Promotion.objects.create(name="عشرة في المية", kind=PromotionKind.PERCENT, percent=D("10"))
        order = self.combo(price="184.99")  # a saving of 0.01: the burger takes it, fries and pepsi 0.00
        self.assertEqual([line.discount for line in order.lines.order_by("pk")], [D("0.01"), D("0.00"), D("0.00")])
        invoice = self.pay(order)
        self.assertEqual([row[3] for row in self.invoice_rows(invoice)], [D("0.01"), D("0.00"), D("0.00")])
        self.assertEqual(invoice.total_amount, D("184.99"))
        self.assertFalse(InvoicePromotion.objects.exists())

    def test_the_service_is_on_the_food_after_the_offer_and_never_discounted(self):
        self.vat(False)
        charges.save_settings({"percent": "12", "kinds": ["dine_in"], "minimum": ""}, self.owner)
        self.coupon()
        invoice = self.pay(self.burgers(), coupon="WELCOME10")
        rows = self.invoice_rows(invoice)
        self.assertEqual(rows[-1], ("SERVICE-CHARGE", D("1.000"), D("29.16"), D("0.00"), D("29.16"), D("0.00")))  # 12% of 243.00
        self.assertEqual(invoice.total_amount, D("272.16"))

    def test_vat_on_with_the_service(self):
        charges.save_settings({"percent": "12", "kinds": ["dine_in"], "minimum": ""}, self.owner)
        self.coupon()
        order = self.burgers()
        bill = charges.preview(Order.objects.get(pk=order.pk), offer=offers.Offer(coupon="WELCOME10"))
        invoice = self.pay(order, coupon="WELCOME10")
        self.assertEqual(self.invoice_rows(invoice)[-1], ("SERVICE-CHARGE", D("1.000"), D("29.16"), D("0.00"), D("29.16"), D("4.08")))
        self.assertEqual((bill["total"], invoice.total_amount), (D("310.26"), D("310.26")))  # 272.16 + 30.24 + 3.78 + 4.08

    def test_the_minimums_top_up_is_measured_on_the_food_before_the_offer(self):
        self.vat(False)
        charges.save_settings({"percent": "12", "kinds": ["dine_in"], "minimum": "200"}, self.owner)
        self.coupon()
        invoice = self.pay(self.burgers(), coupon="WELCOME10")
        codes = {row[0]: row[4] for row in self.invoice_rows(invoice)}
        self.assertEqual((codes["MIN-CHARGE"], codes["SERVICE-CHARGE"]), (D("130.00"), D("44.76")))  # 400 − 270; 12% of (243 + 130)
        self.assertEqual(invoice.total_amount, D("417.76"))

    def test_points_never_reduce_the_service_line(self):
        self.vat(False)
        charges.save_settings({"percent": "12", "kinds": ["dine_in"], "minimum": ""}, self.owner)
        self.give_points(self.mona)
        invoice = self.pay(self.burgers(customer=self.mona), points="200")
        self.assertEqual(self.invoice_rows(invoice)[-1][4], D("32.40"))  # 12% of 270.00, as without points
        self.assertEqual((invoice.discount_amount, invoice.total_amount), (D("20.00"), D("282.40")))


# ---- points (T6) --------------------------------------------------------------------------------------------------------


class PointsTests(OfferSetup):
    def test_200_points_take_20_00_off_307_80_and_vat_stays(self):
        self.give_points(self.mona)
        order = self.burgers(customer=self.mona)
        invoice = self.pay(order, points="200")
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.discount_amount, invoice.total_amount),
                         (D("270.00"), D("37.80"), D("20.00"), D("287.80")))
        self.assertEqual(InvoiceRedemption.objects.get(invoice=invoice).points, 200)
        self.assertEqual(PointsEntry.objects.get(invoice=invoice, kind=EntryKind.SPEND).points, -200)
        earned = PointsEntry.objects.get(invoice=invoice, kind=EntryKind.EARN).points
        self.assertEqual((earned, loyalty.earned_for(invoice.total_amount)), (28, 28))  # the till's rule: floor(287.80 / 10)
        self.assertEqual(loyalty.balance(self.mona)["points"], 28)

    def test_vat_off_270_00_becomes_250_00(self):
        self.vat(False)
        self.give_points(self.mona)
        self.assertEqual(self.pay(self.burgers(customer=self.mona), points="200").total_amount, D("250.00"))

    def test_a_table_bill_earns_what_the_till_earns_for_the_same_total(self):
        self.vat(False)
        invoice = self.pay(self.burgers(customer=self.mona))
        self.assertEqual(PointsEntry.objects.get(invoice=invoice, kind=EntryKind.EARN).points, 27)  # floor(270.00 / 10)

    def test_a_walk_in_cannot_spend_points_and_nothing_is_kept(self):
        order = self.burgers()
        before = _counts()
        with self.assertRaisesMessage(ValidationError, "النقاط بتتحسب لعميل باسمه"):
            self.pay(order, points="200")
        response = self.screen_pay(order, redeem_points="200")
        self.assertContains(response, "النقاط بتتحسب لعميل باسمه")
        self.assertEqual(_counts(), before)

    def test_more_points_than_the_customer_has_are_refused(self):
        self.give_points(self.mona, 150)
        order = self.burgers(customer=self.mona)
        before = _counts()
        response = self.screen_pay(order, redeem_points="200")
        self.assertContains(response, "رصيد العميل 150 نقطة بس.")
        self.assertEqual(_counts(), before)


# ---- gift cards (HG-071) and split tenders (R2b) ---------------------------------------------------------------------------


class GiftCardTests(OfferSetup):
    def gift_card(self, value="100.00", cashbox=None):
        return giftcards.sell(D(value), cashbox or self.cashbox, self.owner)

    def test_a_card_of_100_00_on_307_80_leaves_207_80_cash(self):
        card = self.gift_card()
        till = get_cashbox_balance(self.cashbox)
        order = self.burgers()
        invoice = self.pay(order, tendered=D("207.80"), gift=(card, D("100.00")))
        self.assertEqual((invoice.total_amount, invoice.paid_now), (D("307.80"), D("207.80")))
        self.assertEqual(giftcards.balance(card), D("0.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox) - till, D("207.80"))
        self.assertEqual(walk_in_balance(), D("0.00"))  # the walk-in owes nothing

    def test_the_card_from_the_screen(self):
        card = self.gift_card()
        order = self.burgers()
        response = self.screen_pay(order, tendered="207.80", gift_code=card.code, gift_amount="100.00")
        self.assertEqual(response.status_code, 200)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.status, invoice.paid_now, giftcards.balance(card)), ("posted", D("207.80"), D("0.00")))

    def test_a_card_of_another_branch_or_with_too_little_balance_is_refused_and_nothing_is_kept(self):
        other = self.gift_card(cashbox=self.b_cashbox)
        small = self.gift_card("50.00")
        order = self.burgers()
        before = _counts()
        self.assertContains(self.screen_pay(order, tendered="207.80", gift_code=other.code, gift_amount="100.00"), "الكارت ده اتباع في فرع تاني")
        self.assertContains(self.screen_pay(order, tendered="207.80", gift_code=small.code, gift_amount="100.00"), "رصيد الكارت مش كفاية")
        self.assertEqual(_counts(), before)
        self.assertEqual((giftcards.balance(other), giftcards.balance(small)), (D("100.00"), D("50.00")))

    def test_a_card_with_split_tenders_pays_the_rest_and_the_tenders_cannot_cover_the_card_again(self):
        card = self.gift_card()
        order = self.burgers()
        before = _counts()
        with self.assertRaisesMessage(ValidationError, "err_tenders_over"):
            self.pay(order, tenders=[(self.cashbox, D("207.80")), (self.card, D("100.00"))], gift=(card, D("100.00")))
        self.assertEqual(_counts(), before)
        invoice = self.pay(order, tenders=[(self.cashbox, D("107.80")), (self.card, D("100.00"))], gift=(card, D("100.00")))
        self.assertEqual((invoice.total_amount, invoice.paid_now), (D("307.80"), D("107.80")))
        self.assertEqual(get_cashbox_balance(self.card), D("100.00"))
        self.assertEqual((giftcards.balance(card), walk_in_balance()), (D("0.00"), D("0.00")))

    def test_short_tenders_beside_the_card_are_refused(self):
        card = self.gift_card()
        order = self.burgers()
        with self.assertRaisesMessage(ValidationError, "207.80"):
            self.pay(order, customer=self.mona, tenders=[(self.cashbox, D("100.00")), (self.card, D("50.00"))], gift=(card, D("100.00")))


# ---- cancelling ---------------------------------------------------------------------------------------------------------


class CancelTests(OfferSetup):
    def test_a_cancelled_bill_gives_the_points_and_the_cards_money_back(self):
        self.give_points(self.mona)
        card = giftcards.sell(D("100.00"), self.cashbox, self.owner)
        order = self.burgers(customer=self.mona)
        invoice = self.pay(order, points="200", tendered=D("187.80"), gift=(card, D("100.00")))
        self.assertEqual((invoice.total_amount, loyalty.balance(self.mona)["points"], giftcards.balance(card)), (D("287.80"), 28, D("0.00")))
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط")
        self.assertEqual(loyalty.balance(self.mona)["points"], 200)  # the spend given back, the 28 earned taken back
        self.assertEqual(giftcards.balance(card), D("100.00"))
        self.assertEqual(PointsEntry.objects.filter(invoice=invoice, kind=EntryKind.BACK).count(), 2)


# ---- the printed check (R3) and the bill box -------------------------------------------------------------------------------


class CheckAndScreenTests(OfferSetup):
    def test_the_check_equals_the_posted_invoice_row_by_row(self):
        charges.save_settings({"percent": "12", "kinds": ["dine_in"], "minimum": ""}, self.owner)
        self.happy_hour()
        order = self.teas()
        with clock(19, 30):
            services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk])
            shown = self.preview_rows(order)
            self.client.force_login(self.owner)
            check = self.client.get(reverse("restaurant:check", args=[order.pk]), {"lang": "ar"})
            invoice = self.pay(order)
        self.assertEqual(shown, self.invoice_rows(invoice))
        self.assertContains(check, 'data-check-offer="10.00"')
        self.assertContains(check, 'data-check-offer-total="10.00"')
        self.assertContains(check, f'data-check-total="{invoice.total_amount:.2f}"')

    def test_the_check_carries_the_coupon_the_points_and_the_card(self):
        self.vat(False)
        self.coupon()
        self.give_points(self.mona)
        card = giftcards.sell(D("100.00"), self.cashbox, self.owner)
        order = self.burgers(customer=self.mona)
        self.client.force_login(self.owner)
        check = self.client.get(reverse("restaurant:check", args=[order.pk]),
                                {"lang": "ar", "coupon": "WELCOME10", "redeem_points": "200", "gift_code": card.code, "gift_amount": "100.00"})
        for marker in ('data-check-total="243.00"', 'data-check-points="20.00"', 'data-check-due="223.00"', 'data-check-gift="100.00"',
                       'data-check-cash-due="123.00"'):
            self.assertContains(check, marker)
        invoice = self.pay(order, coupon="WELCOME10", points="200", tendered=D("123.00"), gift=(card, D("100.00")))
        self.assertEqual((invoice.total_amount, invoice.paid_now), (D("223.00"), D("123.00")))

    def test_with_everything_off_the_page_has_no_offer_box(self):
        profile = ClientProfile.get_active()
        for slug in ("promotions", "loyalty", "gift_cards"):
            set_capability_enabled(profile, slug, False, user=self.owner)
        page = self.page(self.burgers())
        self.assertNotContains(page, "data-offer-box")
        self.assertNotContains(page, "data-offer-row")
        self.assertNotContains(page, 'name="promotion_seen"')

    def test_with_nothing_typed_the_box_is_closed_and_the_figures_are_as_before(self):
        order = self.burgers()
        page = self.page(order)
        self.assertContains(page, "<details class=\"rs-more\" data-offer-box><summary>خصم أو نقاط أو كارت هدية</summary>")
        self.assertNotContains(page, "data-offer-row")
        self.assertNotContains(page, "data-offer-summary")
        self.assertContains(page, "data-order-total>270.00")
        self.assertContains(page, 'data-bill="307.80"')

    def test_the_box_finds_the_customer_by_mobile_and_works_the_bill_out(self):
        self.vat(False)
        self.coupon()
        self.give_points(self.mona, 260)
        order = self.burgers()
        page = self.page(order, phone="01001234567", coupon="WELCOME10", redeem_points="200")
        self.assertContains(page, f'data-offer-customer="{self.mona.pk}"')
        self.assertContains(page, 'data-offer-balance="260"')
        self.assertContains(page, 'data-offer-row data-offer-amount="27.00"')
        self.assertContains(page, 'data-points-amount="20.00"')
        self.assertContains(page, 'data-offer-due="223.00"')
        for hidden in ('name="coupon" value="WELCOME10"', 'name="redeem_points" value="200"', 'name="points_seen" value="20.00"',
                       'name="promotion_seen" value="27.00"', f'name="customer" value="{self.mona.pk}"'):
            self.assertContains(page, hidden)
        self.assertContains(page, f"coupon=WELCOME10")  # the check's link carries the box
        response = self.screen_pay(order, customer=str(self.mona.pk), coupon="WELCOME10", redeem_points="200", points_seen="20.00",
                                   promotion_seen="27.00", tendered="223.00")
        self.assertEqual(response.status_code, 200)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.customer, invoice.discount_amount, invoice.total_amount), (self.mona, D("20.00"), D("223.00")))

    def test_the_box_says_what_it_refused_and_keeps_the_rest(self):
        self.vat(False)
        self.coupon()
        page = self.page(self.burgers(), phone="0122 999 0000", coupon="WELCOME10", redeem_points="100")
        self.assertContains(page, "مفيش عميل بالرقم ده")
        self.assertContains(page, "النقاط بتتصرف لعميل باسمه")
        self.assertContains(page, 'data-offer-row data-offer-amount="27.00"')  # the coupon still counts
        page = self.page(self.burgers(table=self.b_t1), coupon="NOPE")
        self.assertContains(page, "الكوبون «NOPE» مش موجود")

    def test_english(self):
        self.vat(False)
        self.coupon()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:order", args=[self.burgers().pk]), {"lang": "en", "coupon": "WELCOME10"})
        self.assertContains(page, "Discount, points or a gift card")
        self.assertContains(page, "Offer: كوبون الترحيب")


# ---- R12: a branch's bill ------------------------------------------------------------------------------------------------


class BranchTests(OfferSetup):
    def test_a_branchs_bill_with_an_offer_posts_to_its_branch(self):
        self.vat(False)
        self.happy_hour()
        order = self.teas(table=self.b_t1)
        main_till = get_cashbox_balance(self.cashbox)
        with clock(19, 30):
            invoice = self.pay(order, user=self.manager_b, cashbox=self.b_cashbox)
        self.assertEqual((invoice.selling_location.location_code, invoice.cashbox.cashbox_code, invoice.total_amount), ("BR-HALL", "BR-TILL", D("30.00")))
        self.assertEqual(InvoicePromotion.objects.get(invoice=invoice).discount, D("10.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), main_till)
