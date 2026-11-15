"""Restaurants R14 (HG-120): delivery-platform orders by hand («طلبات», «elmenus»), with no platform API (that is X2).

R8's set-up (``tests_reservations.ReservationSetup``): the main entity (hall «HALL», till «TILL») and the Maadi branch, the clock on
Wednesday 11 Nov 2026 at noon in Cairo, VAT on at 14%, the cashier, the waiter, the manager and the stock keeper. On top of it:

* «كشري» (``D-KOSHARY``) is **45.00** on the menu, a service dish; «طلبات» (``C-TALABAT``) is a named customer whose price list
  «أسعار طلبات» has it at **55.00**; the capability «قوائم الأسعار» is on;
* «طلبات» is also a courier (``shipping.save_courier``, its own box of kind ``courier``) and the platform setting links the two,
  with an **18%** commission;
* two plates on a platform order are **110.00**; with VAT **125.40**, posted on credit to «طلبات» (nothing in the drawer). Handed
  over and collected: the courier box +125.40 and «طلبات» owes 0.00. The weekly settlement to the bank with the 18% commission
  **22.57**: the bank +102.83, the commission an expense of 22.57, the courier box 0.00. VAT off: 110.00, 19.80, 90.20.
"""

from decimal import Decimal as D

from django.core.exceptions import ValidationError

from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import make_customer, make_item
from pricing import services as pricing
from sales.models import SalesInvoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from shipping import services as shipping

from . import platforms, services
from .models import Order, OrderKind, OrderStatus
from .tests_reservations import ReservationSetup, balance


class PlatformSetup(ReservationSetup):
    def setUp(self):
        super().setUp()
        set_capability_enabled(ClientProfile.get_active(), "price_lists", True, user=self.owner)
        set_capability_enabled(ClientProfile.get_active(), "shipping", True, user=self.owner)
        self.dish = make_item(item_code="D-KOSHARY", item_name="كشري", default_sale_price="45.00", is_stock_tracked=False)
        self.talabat = make_customer(customer_code="C-TALABAT", name="طلبات")
        self.talabat_list = pricing.save_price_list(user=self.owner, code="TALABAT", name_ar="أسعار طلبات")
        pricing.set_item_prices(self.talabat_list, {self.dish: D("55.00")}, self.owner)
        pricing.assign_customers(self.talabat_list, [self.talabat], self.owner)
        self.courier = shipping.save_courier({"name": "طلبات", "fee_per_parcel": "0"}, self.owner)
        self.platform = platforms.save_platform(self.talabat, self.courier, "18", self.owner)

    def delivery(self, customer=None, **extra):
        extra.setdefault("address", "15 ش التحرير، الدقي")
        return services.open_order(self.owner, kind=OrderKind.DELIVERY, customer=customer, entity=self.main, **extra)


# ---- the platform's price list on the order (bug fixes, shown failing first) ---------------------------------------


class PriceBookTests(PlatformSetup):
    def test_two_plates_on_the_platform_order_are_110_from_its_price_list(self):
        order = self.delivery(self.talabat)
        line = services.add_item(order, self.dish, self.owner, 2)
        self.assertEqual((line.unit_price, line.quantity, line.amount), (D("55.00"), D("2.000"), D("110.00")))

    def test_the_same_dish_added_twice_merges_into_one_line_at_the_list_price(self):
        order = self.delivery(self.talabat)
        first = services.add_item(order, self.dish, self.owner, 1)
        second = services.add_item(order, self.dish, self.owner, 1)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual([(line.unit_price, line.quantity) for line in order.lines.all()], [(D("55.00"), D("2.000"))])

    def test_paying_a_platform_priced_order_in_another_customers_name_is_refused(self):
        order = self.delivery(self.talabat)
        services.add_item(order, self.dish, self.owner, 2)
        with self.assertRaises(ValidationError) as caught:
            services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, customer=self.nour, tendered=D("125.40"))
        self.assertIn("قائمة أسعار طلبات", caught.exception.messages[0])
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.OPEN)
        self.assertEqual((balance(self.nour), get_cashbox_balance(self.cashbox)), (D("0.00"), D("0.00")))


# ---- posted on credit to the platform (trap 16; shown failing first) ----------------------------------------------


class CreditOnlyTests(PlatformSetup):
    def test_money_in_the_drawer_for_a_platform_order_is_refused(self):
        order = platforms.open_order(self.owner, self.platform, "TB-1001")
        services.add_item(order, self.dish, self.owner, 2)
        with self.assertRaises(ValidationError) as caught:
            services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, tendered=D("125.40"))
        self.assertIn("بيتسجّل آجل على حسابها", caught.exception.messages[0])
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(get_cashbox_balance(self.cashbox), D("0.00"))

    def test_a_walk_in_order_is_not_posted_on_the_platforms_account(self):
        order = self.delivery()
        services.add_item(order, self.dish, self.owner, 2)
        with self.assertRaises(ValidationError) as caught:
            services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, customer=self.talabat, tendered=D("0"))
        self.assertIn("حساب طلبات لطلبات طلبات بس", caught.exception.messages[0])
        self.assertFalse(SalesInvoice.objects.exists())
