"""Restaurants R6 (HG-114): what ``promotions.apply`` returns today for the till's own cases, pinned before R6 lets a line
carry its own time («at»).

Written and run green against ``promotions/services.py`` before R6 touched it, and kept unedited after: a caller that
passes no line time (the till, the invoice form, the offline sync, the quote) must get exactly what it got before,
the same values and the very same line objects back.

The clock stands still on Wednesday 11 Nov 2026; ``now`` is passed explicitly (local time, Cairo) unless a case says
otherwise.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_location, open_test_shift, stock_in
from inventory.services import recalculate_item_average_cost
from sales.models import SalesInvoice
from sales.pos import walk_in_customer

from . import services
from .models import InvoicePromotion, PromotionKind
from .tests import PromotionSetup, line

TZ = timezone.get_default_timezone()
DAY = datetime(2026, 11, 11, 12, 0)  # a Wednesday


def at(hour, minute=0, day=DAY):
    return timezone.make_aware(day.replace(hour=hour, minute=minute), TZ)


def discounts(lines):
    return [row["line_discount_amount"] for row in lines]


class ApplyCharacterizationTests(PromotionSetup):
    def setUp(self):
        super().setUp()
        clock = mock.patch("django.utils.timezone.now", return_value=at(12))
        clock.start()
        self.addCleanup(clock.stop)
        from master_data.models import Category
        from hesba_testing.factories import make_item

        self.drinks = Category.objects.create(category_code="DRINKS", name_ar="مشروبات")
        self.tea = make_item(item_code="TEA", item_name="شاي", default_sale_price="20.00", category=self.drinks)

    def test_a_timed_offer_inside_its_hours(self):
        happy = self.promo(name="هابي أور", percent=D("50"), category=self.drinks, from_time=time(17, 0), to_time=time(19, 0))
        rows = [line(self.tea, 1, "20.00"), line(self.sock, 1, "30.00")]
        filled, promo, off = services.apply(rows, now=at(18, 30))
        self.assertEqual((promo, off), (happy, D("10.00")))
        self.assertEqual(discounts(filled), [D("10.00"), D("0")])
        self.assertIsNot(filled, rows)
        self.assertIsNot(filled[0], rows[0])
        self.assertIs(filled[1], rows[1])  # a line the offer does not touch comes back as the same object
        self.assertEqual(rows[0]["line_discount_amount"], D("0"))  # the caller's line is not changed in place
        self.assertEqual(set(filled[0]), {"item", "quantity", "unit_sale_price", "line_discount_amount", "description"})
        # the edges of the window count; the evening that runs past midnight
        self.assertEqual(services.apply(rows, now=at(17, 0))[2], D("10.00"))
        self.assertEqual(services.apply(rows, now=at(19, 0))[2], D("10.00"))

    def test_a_timed_offer_outside_its_hours(self):
        self.promo(name="هابي أور", percent=D("50"), category=self.drinks, from_time=time(17, 0), to_time=time(19, 0))
        rows = [line(self.tea, 1, "20.00")]
        filled, promo, off = services.apply(rows, now=at(19, 1))
        self.assertEqual((promo, off), (None, D("0.00")))
        self.assertIs(filled, rows)  # nothing applies: the very list given comes back
        self.assertEqual(services.apply(rows, now=at(16, 59))[1:], (None, D("0.00")))
        self.assertEqual(services.apply(rows)[1:], (None, D("0.00")))  # no ``now``: the clock (12:00)

    def test_without_now_the_clock_decides(self):
        happy = self.promo(name="هابي أور", percent=D("50"), category=self.drinks, from_time=time(17, 0), to_time=time(19, 0))
        with mock.patch("django.utils.timezone.now", return_value=at(18, 0)):
            self.assertEqual(services.apply([line(self.tea, 2, "20.00")])[1:], (happy, D("20.00")))
            self.assertEqual(services.running(), [happy])

    def test_an_evening_past_midnight(self):
        late = self.promo(name="سهرة", percent=D("10"), from_time=time(22, 0), to_time=time(2, 0))
        rows = [line(self.sock, 1, "30.00")]
        self.assertEqual(services.apply(rows, now=at(23, 30))[1:], (late, D("3.00")))
        self.assertEqual(services.apply(rows, now=at(1, 59))[1:], (late, D("3.00")))
        self.assertEqual(services.apply(rows, now=at(2, 1))[1:], (None, D("0.00")))

    def test_dates(self):
        self.promo(name="انتهى", percent=D("10"), ends_on=DAY.date() - timedelta(days=1))
        later = self.promo(name="لسه", percent=D("20"), starts_on=DAY.date() + timedelta(days=1))
        rows = [line(self.sock, 1, "30.00")]
        self.assertEqual(services.apply(rows, now=at(12))[1:], (None, D("0.00")))
        self.assertEqual(services.apply(rows, now=at(12, day=DAY + timedelta(days=1)))[1:], (later, D("6.00")))

    def test_a_coupon(self):
        coupon = self.promo(name="كوبون", percent=D("15"), coupon_code="EID15")
        rows = [line(self.sneaker, 1, "500.00"), line(self.sock, 2, "30.00")]
        self.assertEqual(services.apply(rows, now=at(12))[1:], (None, D("0.00")))  # not typed: does not count
        filled, promo, off = services.apply(rows, now=at(12), coupon="  eid15 ")
        self.assertEqual((promo, off), (coupon, D("84.00")))
        self.assertEqual(discounts(filled), [D("75.00"), D("9.00")])
        with self.assertRaisesMessage(ValidationError, "الكوبون «NOPE» مش موجود أو مش شغال دلوقتي."):
            services.apply(rows, now=at(12), coupon="NOPE")
        with self.assertRaisesMessage(ValidationError, "The coupon “NOPE” does not exist or is not running now."):
            services.apply(rows, now=at(12), coupon="NOPE", lang="en")

    def test_a_coupon_out_of_its_hours_or_dates_is_refused(self):
        self.promo(name="كوبون العصر", percent=D("15"), coupon_code="TEA15", from_time=time(15, 0), to_time=time(16, 0))
        self.promo(name="كوبون قديم", percent=D("15"), coupon_code="OLD15", ends_on=DAY.date() - timedelta(days=1))
        rows = [line(self.sock, 1, "30.00")]
        with self.assertRaisesMessage(ValidationError, "TEA15"):
            services.apply(rows, now=at(12), coupon="TEA15")
        self.assertEqual(services.apply(rows, now=at(15, 30), coupon="TEA15")[2], D("4.50"))
        with self.assertRaisesMessage(ValidationError, "OLD15"):
            services.apply(rows, now=at(12), coupon="OLD15")

    def test_a_coupon_that_covers_nothing_on_the_bill_gives_nothing_and_no_error(self):
        self.promo(name="كوبون الأحذية", percent=D("15"), coupon_code="SHOE15", category=self.shoes)
        rows = [line(self.sock, 1, "30.00")]
        filled, promo, off = services.apply(rows, now=at(12), coupon="SHOE15")
        self.assertEqual((promo, off), (None, D("0.00")))
        self.assertIs(filled, rows)

    def test_a_line_with_a_typed_discount_takes_no_promotion(self):
        best = self.promo(name="10%", percent=D("10"))
        rows = [line(self.sneaker, 1, "500.00"), line(self.sock, 1, "30.00", discount="5.00"), line(self.tea, 1, "20.00", discount="0.00")]
        filled, promo, off = services.apply(rows, now=at(12))
        self.assertEqual((promo, off), (best, D("52.00")))
        self.assertEqual(discounts(filled), [D("50.00"), D("5.00"), D("2.00")])  # 0.00 typed is no discount
        self.assertIs(filled[1], rows[1])
        # the invoice form's ``typed_discount`` key (a unit rounding's cents are not a typed discount)
        rounded = dict(line(self.sock, 3, "10.00", discount="0.01"), typed_discount=D("0"))
        typed = dict(line(self.sock, 1, "30.00", discount="4.00"), typed_discount=D("4.00"))
        filled, _promo, off = services.apply([rounded, typed], now=at(12))
        self.assertEqual(off, D("3.00"))  # 10% of 30.00 - 0.01 = 29.99 → 3.00
        self.assertEqual(discounts(filled), [D("3.01"), D("4.00")])

    def test_the_best_single_offer_wins_and_offers_never_stack(self):
        self.promo(name="10%", percent=D("10"))
        drinks = self.promo(name="مشروبات 50%", percent=D("50"), category=self.drinks)
        over = self.promo(name="80 فوق 100", kind=PromotionKind.AMOUNT_OVER, amount=D("80.00"), min_total=D("100.00"))
        teas = [line(self.tea, 4, "20.00")]
        self.assertEqual(services.apply(teas, now=at(12))[1:], (drinks, D("40.00")))  # 40.00 beats 8.00; 80.00 is under the 100.00 minimum
        big = [line(self.sneaker, 1, "500.00"), line(self.tea, 1, "20.00")]
        filled, promo, off = services.apply(big, now=at(12))
        self.assertEqual((promo, off), (over, D("80.00")))
        self.assertEqual(discounts(filled), [D("76.92"), D("3.08")])

    def test_a_tie_keeps_the_first_running_offer(self):
        first = self.promo(name="أ", percent=D("10"))
        self.promo(name="ب", percent=D("10"))
        self.assertEqual(services.apply([line(self.sock, 1, "30.00")], now=at(12))[1], first)

    def test_buy_two_get_one(self):
        free = self.promo(name="2+1", kind=PromotionKind.BUY_GET, buy_quantity=2, get_quantity=1, category=self.drinks)
        filled, promo, off = services.apply([line(self.tea, 2, "20.00"), line(self.tea, 1, "18.00")], now=at(12))
        self.assertEqual((promo, off), (free, D("18.00")))
        self.assertEqual(discounts(filled), [D("0"), D("18.00")])

    def test_a_members_only_offer(self):
        from agreements.models import ContractStatus, ServiceContract
        from hesba_testing.factories import make_item

        gym = make_item(item_code="GYM", item_name="اشتراك", default_sale_price="300.00", is_stock_tracked=False)
        member = self.promo(name="أعضاء", percent=D("25"), membership_service=gym)
        buyer, other = make_customer(customer_code="C-M1", name="عضو"), make_customer(customer_code="C-M2", name="مش عضو")
        ServiceContract.objects.create(number="SC-1", customer=buyer, service=gym, status=ContractStatus.ACTIVE, starts_on=DAY.date() - timedelta(days=5),
                                       amount=D("300.00"), created_by=self.owner)
        rows = [line(self.sock, 1, "30.00")]
        self.assertEqual(services.apply(rows, now=at(12), customer=buyer)[1:], (member, D("7.50")))
        self.assertEqual(services.apply(rows, now=at(12), customer=other)[1:], (None, D("0.00")))
        self.assertEqual(services.apply(rows, now=at(12))[1:], (None, D("0.00")))

    def test_switched_off_nothing_applies(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        self.promo(name="10%", percent=D("10"), coupon_code="X")
        caps.set_capability_enabled(ClientProfile.get_active(), "promotions", False)
        rows = [line(self.sock, 1, "30.00")]
        filled, promo, off = services.apply(rows, now=at(12), coupon="NOPE")  # not even checked
        self.assertIs(filled, rows)
        self.assertEqual((promo, off), (None, D("0.00")))

    def test_discounts_and_running_directly(self):
        happy = self.promo(name="هابي أور", percent=D("50"), category=self.drinks, from_time=time(17, 0), to_time=time(19, 0))
        rows = [line(self.tea, 1, "20.00"), line(self.sock, 1, "30.00"), line(self.tea, 1, "20.00", discount="1.00")]
        self.assertEqual(services.discounts(happy, rows), [D("10.00"), D("0.00"), D("0.00")])  # ``discounts`` does not look at the hours
        self.assertEqual(services.running(at(18, 0)), [happy])
        self.assertEqual(services.running(at(20, 0)), [])
        self.assertEqual(services.running(at(12)), [])


class TillCharacterizationTests(PromotionSetup):
    """The till's own flow with a timed offer: what the cashier screen charges inside and outside the hours."""

    def setUp(self):
        super().setUp()
        from master_data.models import Category
        from hesba_testing.factories import make_item

        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        drinks = Category.objects.create(category_code="DRINKS", name_ar="مشروبات")
        self.tea = make_item(item_code="TEA", item_name="شاي", default_sale_price="20.00", category=drinks)
        stock_in(self.tea, self.location, 20, "5.00", movement_date=DAY.date())
        recalculate_item_average_cost(self.tea)
        self.promo(name="هابي أور", percent=D("50"), category=drinks, from_time=time(17, 0), to_time=time(19, 0))
        open_test_shift(self.cashier, self.cashbox)
        self.client.force_login(self.cashier)

    def sell(self, when, **extra):
        with mock.patch("django.utils.timezone.now", return_value=when):
            return self.client.post(reverse("sales:pos"), {"line_count": "1", "customer": str(walk_in_customer().pk), "location": str(self.location.pk),
                                                           "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "100", "print": "0",
                                                           "item_0": str(self.tea.pk), "qty_0": "2", "price_0": "20.00", **extra})

    def test_inside_the_hours_the_till_takes_half_off(self):
        self.assertEqual(self.sell(at(18, 30)).status_code, 302)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.subtotal, invoice.total_amount, invoice.lines.get().line_discount_amount), (D("20.00"), D("20.00"), D("20.00")))
        self.assertEqual(InvoicePromotion.objects.get(invoice=invoice).discount, D("20.00"))

    def test_outside_the_hours_the_till_charges_the_full_price(self):
        self.assertEqual(self.sell(at(19, 30)).status_code, 302)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.total_amount, invoice.lines.get().line_discount_amount), (D("40.00"), D("0.00")))
        self.assertFalse(InvoicePromotion.objects.exists())

    def test_a_quote_taken_inside_the_hours_is_refused_after_them(self):
        refused = self.sell(at(19, 30), promotion_seen="20.00")
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, "العروض اتغيرت")
        self.assertFalse(SalesInvoice.objects.exists())
