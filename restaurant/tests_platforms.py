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

import json
import re
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from audit.models import AuditLog
from cashboxes.models import CashboxKind
from cashboxes.services import get_cashbox_balance
from entities.current import working_in
from hesba_testing.factories import make_cashbox, make_customer, make_item
from ledger import reports as gl
from ledger.projector import Projector
from pricing import services as pricing
from promotions.models import InvoicePromotion, Promotion, PromotionKind
from sales.models import SalesInvoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile, SystemSetting
from shipping import services as shipping
from shipping.models import Shipment, ShipmentStatus

from . import platforms, services
from .insights import period_report
from .models import Order, OrderKind, OrderLine, OrderStatus
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

    def platform_order(self, number="TB-1001", plates=2):
        order = platforms.open_order(self.owner, self.platform, number)
        services.add_item(order, self.dish, self.owner, plates)
        return order

    def pay(self, order, **extra):
        return services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, **extra)

    def bank(self):
        return make_cashbox(cashbox_code="BANK", name_ar="البنك الأهلي", kind=CashboxKind.BANK, entity=self.main)

    def order_page(self, order, user=None, entity="main"):
        self.enter(user, entity)
        return self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": "ar"})

    def order_post(self, order, action, user=None, entity="main", **data):
        self.enter(user, entity)
        return self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", "action": action, **data}, follow=True)

    def assert_balanced(self):
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertEqual(gl.control_balance("receivable", entity=self.main), D("0.00"))


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


class MoreCreditRulesTests(PlatformSetup):
    def test_paying_several_ways_or_with_a_gift_card_is_refused_and_a_platform_order_has_no_fee_or_driver_of_ours(self):
        order = self.platform_order()
        with self.assertRaises(ValidationError) as caught:
            self.pay(order, tenders=[(self.cashbox, D("100.00")), (self.card, D("25.40"))])
        self.assertIn("بيتسجّل آجل", caught.exception.messages[0])
        typed = self.delivery(self.talabat, delivery_fee="25")  # the plain delivery form with the platform picked
        self.assertEqual(typed.delivery_fee, D("0.00"))
        with self.assertRaises(ValidationError) as caught:
            services.dispatch(order, None, self.owner, delivery_fee="20")
        self.assertIn("بيستلمه مندوب طلبات", caught.exception.messages[0])
        self.assertFalse(SalesInvoice.objects.exists())

    def test_a_platform_order_without_a_price_list_is_still_paid_in_its_name_only(self):
        pricing.assign_customers(self.talabat_list, [], self.owner)
        order = self.platform_order()
        self.assertEqual(order.lines.get().unit_price, D("45.00"))  # no list: the menu price
        with self.assertRaises(ValidationError) as caught:
            self.pay(order, customer=self.nour)
        self.assertIn("بيتسجّل على حساب طلبات بس", caught.exception.messages[0])

    def test_a_running_offer_of_ours_does_not_touch_a_platform_order_but_still_applies_to_a_walk_in(self):
        set_capability_enabled(ClientProfile.get_active(), "promotions", True, user=self.owner)
        Promotion.objects.create(name="عشرة في المية", kind=PromotionKind.PERCENT, percent=D("10"))
        order = self.platform_order()
        self.assertContains(self.order_page(order), 'data-platform-due="125.40"')
        invoice, _change = self.pay(order)
        self.assertEqual(invoice.total_amount, D("125.40"))
        self.assertFalse(InvoicePromotion.objects.filter(invoice=invoice).exists())
        walk_in = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main)
        services.add_item(walk_in, self.dish, self.owner, 2)
        invoice, _change = self.pay(walk_in, tendered=D("92.34"))
        self.assertEqual(invoice.total_amount, D("92.34"))  # 90.00 − 10% = 81.00, + 14% VAT
        self.assertTrue(InvoicePromotion.objects.filter(invoice=invoice).exists())


# ---- the money path: posted on credit, handed over, collected, settled -------------------------------------------


class MoneyPathTests(PlatformSetup):
    def test_vat_on_125_40_on_the_platforms_account_then_its_box_then_102_83_to_the_bank_and_22_57_commission(self):
        order = self.platform_order()
        self.assertEqual((order.kind, order.customer, order.notes, order.delivery_fee, order.address, order.entity),
                         (OrderKind.DELIVERY, self.talabat, "طلبات #TB-1001", D("0.00"), "يستلمه مندوب طلبات", self.main))
        invoice, change = self.pay(order)
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.paid_now, invoice.remaining_due, invoice.customer),
                         (D("110.00"), D("15.40"), D("125.40"), D("0.00"), D("125.40"), self.talabat))
        self.assertEqual(change, D("0.00"))
        self.assertEqual((balance(self.talabat), get_cashbox_balance(self.cashbox)), (D("125.40"), D("0.00")))  # nothing in the drawer
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice), (OrderStatus.PAID, invoice))
        shipment = platforms.hand_over(order, self.owner)
        self.assertEqual((shipment.courier, shipment.cod_amount, shipment.fee, shipment.tracking_number, shipment.status),
                         (self.courier, D("125.40"), D("22.57"), "TB-1001", ShipmentStatus.WITH_COURIER))
        platforms.collected(order, self.owner)
        self.assertEqual((shipping.held(self.courier), balance(self.talabat)), (D("125.40"), D("0.00")))
        bank = self.bank()
        settlement = shipping.settle([shipment], bank, self.owner)
        self.assertEqual((settlement.collected, settlement.fees, settlement.net), (D("125.40"), D("22.57"), D("102.83")))
        self.assertEqual((get_cashbox_balance(bank), settlement.fee_expense.amount, shipping.held(self.courier)), (D("102.83"), D("22.57"), D("0.00")))
        self.assertEqual(Shipment.objects.get().status, ShipmentStatus.SETTLED)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("0.00"))
        self.assert_balanced()

    def test_vat_off_110_then_19_80_commission_and_90_20_to_the_bank(self):
        self.vat(False)
        order = self.platform_order()
        invoice, _change = self.pay(order)
        self.assertEqual((invoice.total_amount, invoice.tax_amount, invoice.paid_now, balance(self.talabat)), (D("110.00"), D("0.00"), D("0.00"), D("110.00")))
        shipment = platforms.hand_over(Order.objects.get(pk=order.pk), self.owner)
        self.assertEqual((shipment.cod_amount, shipment.fee), (D("110.00"), D("19.80")))
        platforms.collected(Order.objects.get(pk=order.pk), self.owner)
        self.assertEqual((shipping.held(self.courier), balance(self.talabat)), (D("110.00"), D("0.00")))
        bank = self.bank()
        settlement = shipping.settle([shipment], bank, self.owner)
        self.assertEqual((settlement.fees, settlement.net, get_cashbox_balance(bank), shipping.held(self.courier)), (D("19.80"), D("90.20"), D("90.20"), D("0.00")))
        self.assert_balanced()

    def test_the_whole_path_from_the_screens(self):
        self.enter()
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"})
        self.assertContains(board, 'data-new-order="platform"')
        self.assertContains(board, f'<option value="{self.talabat.pk}">طلبات</option>')
        response = self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "platform", "platform": self.talabat.pk, "number": "#TB-2002",
                                                                   "address": "", "phone": ""}, follow=True)
        order = Order.objects.get()
        self.assertRedirects(response, reverse("restaurant:order", args=[order.pk]) + "?lang=ar")
        self.assertEqual(order.notes, "طلبات #TB-2002")
        self.assertContains(response, 'data-platform-tag="%d"' % self.talabat.pk)
        self.assertContains(response, '<small>55.00')  # the menu tile shows the platform's price
        self.assertNotContains(response, "data-dispatch")
        self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", "action": "add", "item": self.dish.pk, "quantity": "2"})
        page = self.order_page(order)
        self.assertContains(page, 'data-platform-pay')
        self.assertContains(page, 'data-platform-due="125.40"')
        self.assertNotContains(page, "data-tendered")
        self.assertNotContains(page, "data-offer-box")
        response = self.order_post(order, "pay", tendered="0", discount="0", customer=self.talabat.pk, cashbox=self.cashbox.pk)
        self.assertRedirects(response, reverse("restaurant:order", args=[order.pk]) + "?lang=ar")
        self.assertContains(response, 'data-platform-panel="posted"')
        self.assertContains(response, 'data-platform-fee="22.57"')
        response = self.order_post(order, "platform_hand_over")
        self.assertContains(response, "اتسلّم الطلب لمندوب طلبات.")
        self.assertContains(response, 'data-platform-panel="handed"')
        response = self.order_post(order, "platform_collected")
        self.assertContains(response, 'data-platform-panel="collected"')
        self.assertContains(response, reverse("shipping:board"))
        self.assertEqual((balance(self.talabat), shipping.held(self.courier), get_cashbox_balance(self.cashbox)), (D("0.00"), D("125.40"), D("0.00")))
        shipping.settle(list(Shipment.objects.all()), self.bank(), self.owner)
        self.assertContains(self.order_page(order), 'data-platform-panel="settled"')

    def test_the_same_platform_number_twice_is_refused_until_the_first_is_cancelled(self):
        first = self.platform_order("TB-77")
        with self.assertRaises(ValidationError) as caught:
            platforms.open_order(self.owner, self.platform, "TB-77")
        self.assertIn(f"متسجّل قبل كده على طلبات ({first.number})", caught.exception.messages[0])
        services.cancel_order(first, self.owner, "اتلغى من المنصة")
        self.assertEqual(platforms.open_order(self.owner, self.platform, "TB-77").notes, "طلبات #TB-77")
        with self.assertRaises(ValidationError):
            platforms.open_order(self.owner, self.platform, "  ")

    def test_handing_over_before_paying_or_collecting_before_handing_over_is_refused(self):
        order = self.platform_order()
        with self.assertRaises(ValidationError):
            platforms.hand_over(order, self.owner)
        self.pay(order)
        with self.assertRaises(ValidationError) as caught:
            platforms.collected(Order.objects.get(pk=order.pk), self.owner)
        self.assertIn("لسه ما اتسلّمش", caught.exception.messages[0])
        walk_in = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main)
        services.add_item(walk_in, self.dish, self.owner, 1)
        self.pay(walk_in, tendered=D("51.30"))
        with self.assertRaises(ValidationError) as caught:
            platforms.hand_over(Order.objects.get(pk=walk_in.pk), self.owner)
        self.assertIn("ده مش طلب منصة", caught.exception.messages[0])


# ---- the walk-in and the plain orders stay as they were -------------------------------------------------------------


class WalkInUnchangedTests(PlatformSetup):
    def test_a_walk_in_order_is_at_the_menu_price_merges_as_before_and_cannot_post_on_credit(self):
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main)
        first = services.add_item(order, self.dish, self.owner, 1)
        second = services.add_item(order, self.dish, self.owner, 1)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual([(line.unit_price, line.quantity) for line in order.lines.all()], [(D("45.00"), D("2.000"))])
        with self.assertRaises(ValidationError) as caught:
            self.pay(order, tendered=D("0"))
        self.assertIn("err_walk_in_credit", caught.exception.messages[0])
        invoice, change = self.pay(order, tendered=D("110.00"))
        self.assertEqual((invoice.total_amount, invoice.paid_now, change), (D("102.60"), D("102.60"), D("7.40")))
        self.assertContains(self.order_page(services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main)), '<small>45.00')

    def test_an_order_opened_with_no_customer_is_still_paid_in_any_name_and_a_named_customer_on_no_list_as_before(self):
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main)
        services.add_item(order, self.dish, self.owner, 2)
        invoice, _change = self.pay(order, customer=self.nour, tendered=D("0"))  # on credit to Nour, at the menu price, as before
        self.assertEqual((invoice.customer, invoice.total_amount, balance(self.nour)), (self.nour, D("102.60"), D("102.60")))
        named = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, customer=self.nour)
        self.assertEqual(services.add_item(named, self.dish, self.owner, 1).unit_price, D("45.00"))

    def test_with_price_lists_switched_off_every_order_is_at_the_menu_price(self):
        set_capability_enabled(ClientProfile.get_active(), "price_lists", False, user=self.owner)
        order = self.platform_order()
        self.assertEqual(order.lines.get().unit_price, D("45.00"))
        self.enter()
        self.assertContains(self.client.get(reverse("restaurant:platforms"), {"lang": "ar"}), 'data-platform-warning="price_lists"')


# ---- the price list on other orders, add-ons, agreed lines, and changing the customer -------------------------------


class PriceListRulesTests(PlatformSetup):
    def test_a_percentage_list_prices_the_dish_and_its_add_on_and_a_dish_with_add_ons_never_merges(self):
        plus = pricing.save_price_list(user=self.owner, code="PLUS10", name_ar="زيادة 10%", adjust_percent=D("10"))
        pricing.assign_customers(plus, [self.nour], self.owner)
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, customer=self.nour)
        line = services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk, self.cheese_opt.pk])
        services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk, self.cheese_opt.pk])
        self.assertEqual([(row.item.item_code, row.unit_price, row.parent_id is not None) for row in order.lines.order_by("pk")],
                         [("D-BURG", D("132.00"), False), ("A-CHEESE", D("16.50"), True), ("D-BURG", D("132.00"), False), ("A-CHEESE", D("16.50"), True)])
        self.assertEqual(line.unit_price, D("132.00"))

    def test_an_agreed_line_is_kept_and_a_list_priced_dish_does_not_join_it(self):
        order = self.delivery(self.talabat)
        agreed = services.add_agreed_line(order, self.dish, self.owner, 1, D("50.00"))
        added = services.add_item(order, self.dish, self.owner, 1)
        again = services.add_item(order, self.dish, self.owner, 1)
        self.assertNotEqual(agreed.pk, added.pk)
        self.assertEqual(added.pk, again.pk)
        self.assertEqual(sorted((row.unit_price, row.quantity) for row in order.lines.all()), [(D("50.00"), D("1.000")), (D("55.00"), D("2.000"))])

    def test_changing_the_customer_at_the_bill_same_list_allowed_another_list_refused_with_nothing_kept(self):
        twin = make_customer(customer_code="C-TALABAT2", name="طلبات فرع 2")
        pricing.assign_customers(self.talabat_list, [self.talabat, twin], self.owner)
        listed = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, customer=twin)
        services.add_item(listed, self.dish, self.owner, 1)
        invoice, _change = self.pay(listed, customer=twin, tendered=D("0"))
        self.assertEqual((invoice.customer, invoice.total_amount), (twin, D("62.70")))  # 55.00 + 14%
        other = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, customer=twin)
        services.add_item(other, self.dish, self.owner, 1)
        before = (SalesInvoice.objects.count(), balance(self.nour), get_cashbox_balance(self.cashbox), AuditLog.objects.filter(action="pay_order").count())
        with self.assertRaises(ValidationError) as caught:
            self.pay(other, customer=self.nour, tendered=D("62.70"))
        self.assertIn("أسعار الطلب ده من قائمة أسعار طلبات فرع 2؛ ادفعه باسم طلبات فرع 2", caught.exception.messages[0])
        self.assertEqual((SalesInvoice.objects.count(), balance(self.nour), get_cashbox_balance(self.cashbox), AuditLog.objects.filter(action="pay_order").count()), before)
        self.assertEqual(Order.objects.get(pk=other.pk).status, OrderStatus.OPEN)

    def test_the_change_of_customer_refused_on_the_screen_too(self):
        order = self.platform_order()
        response = self.order_post(order, "pay", tendered="0", discount="0", customer=self.nour.pk, cashbox=self.cashbox.pk)
        self.assertContains(response, "أسعار الطلب ده من قائمة أسعار طلبات")
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.OPEN)


# ---- the report -----------------------------------------------------------------------------------------------------


class ReportTests(PlatformSetup):
    def test_orders_sales_and_commission_per_platform(self):
        elmenus = make_customer(customer_code="C-ELMENUS", name="elmenus")
        elmenus_courier = shipping.save_courier({"name": "elmenus", "fee_per_parcel": "0"}, self.owner)
        elmenus_platform = platforms.save_platform(elmenus, elmenus_courier, "15", self.owner)
        for number in ("TB-1", "TB-2"):
            order = self.platform_order(number)
            self.pay(order)
            platforms.hand_over(Order.objects.get(pk=order.pk), self.owner)
        other = platforms.open_order(self.owner, elmenus_platform, "EM-9")
        services.add_item(other, self.dish, self.owner, 1)  # elmenus has no list: 45.00
        self.pay(other)
        walk_in = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main)
        services.add_item(walk_in, self.dish, self.owner, 1)
        self.pay(walk_in, tendered=D("51.30"))
        today = Order.objects.filter(status=OrderStatus.PAID).first().closed_at.date()
        data = period_report(today, today)
        self.assertEqual([(row["platform"].name, row["orders"], row["sales"], row["average"], row["commission"]) for row in data["platforms"]],
                         [("طلبات", 2, D("220.00"), D("110.00"), D("45.14")), ("elmenus", 1, D("45.00"), D("45.00"), D("0.00"))])
        self.assertEqual(data["totals"]["orders"], 4)
        self.assertEqual({row["kind"]: row["orders"] for row in data["kinds"]}, {"delivery": 3, "takeaway": 1})
        self.enter()
        page = self.client.get(reverse("restaurant:report"), {"lang": "ar", "from": today.isoformat(), "to": today.isoformat()})
        self.assertContains(page, "حسب المنصة")
        self.assertContains(page, 'data-r-platform-sales="220.00"')
        self.assertContains(page, 'data-r-platform-commission="45.14"')

    def test_no_platform_no_section(self):
        platforms.remove_platform(self.talabat, self.owner)
        self.enter()
        page = self.client.get(reverse("restaurant:report"), {"lang": "ar"})
        self.assertNotContains(page, "data-r-platforms")
        self.assertNotContains(self.client.get(reverse("restaurant:board"), {"lang": "ar"}), 'data-new-order="platform"')


# ---- the set-up page ------------------------------------------------------------------------------------------------


class SetupPageTests(PlatformSetup):
    def setup_post(self, user=None, **data):
        self.enter(user)
        return self.client.post(reverse("restaurant:platforms"), {"lang": "ar", **data}, follow=True)

    def test_the_page_lists_the_platform_with_its_list_courier_commission_and_box(self):
        order = self.platform_order()
        self.pay(order)
        platforms.hand_over(Order.objects.get(pk=order.pk), self.owner)
        platforms.collected(Order.objects.get(pk=order.pk), self.owner)
        self.enter()
        page = self.client.get(reverse("restaurant:platforms"), {"lang": "ar"})
        self.assertContains(page, f'data-platform-row="{self.talabat.pk}"')
        self.assertContains(page, "أسعار طلبات")
        self.assertContains(page, "18%")
        self.assertContains(page, "125.40")
        self.assertContains(page, "data-platform-form")
        self.assertNotContains(page, "data-platform-warning")
        english = self.client.get(reverse("restaurant:platforms"), {"lang": "en"})
        self.assertContains(english, "Delivery platforms")
        self.assertContains(english, "How it works")

    def test_save_change_and_unlink_audited_and_the_refusals(self):
        elmenus = make_customer(customer_code="C-ELMENUS", name="elmenus")
        courier = shipping.save_courier({"name": "elmenus", "fee_per_parcel": "0"}, self.owner)
        self.setup_post(customer=elmenus.pk, courier=courier.pk, commission="15")
        self.assertEqual(platforms.for_customer(elmenus).commission, D("15.00"))
        self.setup_post(customer=elmenus.pk, courier=courier.pk, commission="16.5")
        self.assertEqual(platforms.for_customer(elmenus).commission, D("16.50"))
        stored = json.loads(SystemSetting.objects.get(key=platforms.KEY).value)
        self.assertEqual(stored[str(elmenus.pk)], {"courier": courier.pk, "commission": "16.5"})
        for data, text in (({"customer": elmenus.pk, "courier": self.courier.pk, "commission": "15"}, "متربطة بمنصة تانية (طلبات)"),
                           ({"customer": elmenus.pk, "courier": courier.pk, "commission": "120"}, "من 0 لحد 100"),
                           ({"customer": walk_in_pk(), "courier": courier.pk, "commission": "15"}, "مش العميل النقدي"),
                           ({"customer": elmenus.pk, "courier": "", "commission": "15"}, "اختار شركة الشحن")):
            with self.subTest(text=text):
                self.assertContains(self.setup_post(**data), text)
        self.setup_post(action="remove", customer=elmenus.pk)
        self.assertIsNone(platforms.for_customer(elmenus))
        self.assertEqual(list(AuditLog.objects.filter(action__in=("save_platform", "remove_platform")).values_list("action", flat=True).order_by("pk")),
                         ["save_platform", "save_platform", "save_platform", "remove_platform"])

    def test_the_warnings_when_shipping_is_off(self):
        set_capability_enabled(ClientProfile.get_active(), "shipping", False, user=self.owner)
        self.enter()
        self.assertContains(self.client.get(reverse("restaurant:platforms"), {"lang": "ar"}), 'data-platform-warning="shipping"')

    def test_the_stylesheet_uses_the_tokens_only(self):
        from django.contrib.staticfiles import finders

        with open(finders.find("hesba/css/restaurant_platforms.css"), encoding="utf-8") as sheet:
            css = sheet.read()
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
        self.assertNotRegex(css, r"rgba?\(")
        with open(finders.find("hesba/css/tokens.css"), encoding="utf-8") as sheet:
            defined = set(re.findall(r"(--hs-[\w-]+)\s*:", sheet.read()))
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])

    def test_the_wording(self):
        from .views_platforms import PLATFORM_ORDER_WORDS, PLATFORM_WORDS

        for lang in ("ar", "en"):
            texts = list(PLATFORM_WORDS[lang].values()) + list(PLATFORM_ORDER_WORDS[lang].values()) + list(platforms.MESSAGES[lang].values())
            texts.append(services.MESSAGES[lang]["other_prices"])
            for text in texts:
                for word in ("ورشة", "الأسطى", "بتاع", "دوس", "لينك"):
                    self.assertNotIn(word, text)
        self.assertEqual(set(PLATFORM_WORDS["ar"]), set(PLATFORM_WORDS["en"]))
        self.assertEqual(set(PLATFORM_ORDER_WORDS["ar"]), set(PLATFORM_ORDER_WORDS["en"]))
        self.assertEqual(set(platforms.MESSAGES["ar"]), set(platforms.MESSAGES["en"]))


def walk_in_pk():
    from sales.pos import walk_in_customer

    return walk_in_customer().pk


# ---- permissions and branches -----------------------------------------------------------------------------------------


class PermissionTests(PlatformSetup):
    def test_the_stock_keeper_is_refused_everywhere(self):
        order = self.platform_order()
        self.enter(self.keeper)
        self.assertEqual(self.client.get(reverse("restaurant:platforms"), {"lang": "ar"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("restaurant:platforms"), {"lang": "ar", "customer": self.talabat.pk, "action": "remove"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "platform", "platform": self.talabat.pk, "number": "X1"}).status_code, 403)
        for action in ("pay", "platform_hand_over", "platform_collected"):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", "action": action, "tendered": "0"}).status_code, 403)
        self.assertEqual(Order.objects.count(), 1)
        self.assertIsNotNone(platforms.for_customer(self.talabat))
        self.assertFalse(SalesInvoice.objects.exists())

    def test_the_waiter_opens_platform_orders_but_never_posts_hands_over_or_sets_up(self):
        self.enter(self.waiter_user)
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"})
        self.assertContains(board, 'data-new-order="platform"')
        self.assertNotContains(board, "data-platforms-link")
        response = self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "platform", "platform": self.talabat.pk, "number": "W-1"}, follow=True)
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get()
        self.assertEqual(order.customer, self.talabat)
        services.add_item(order, self.dish, self.owner, 1)
        self.assertEqual(self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", "action": "pay", "tendered": "0"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("restaurant:platforms"), {"lang": "ar"}).status_code, 403)
        self.pay(order)
        self.assertNotContains(self.order_page(order, self.waiter_user), "data-platform-hand-over")
        self.assertEqual(self.order_post(order, "platform_hand_over", self.waiter_user).status_code, 403)
        self.assertFalse(Shipment.objects.exists())

    def test_the_cashier_sees_the_set_up_without_changing_it_and_hands_over(self):
        order = self.platform_order()
        self.pay(order)
        self.enter(self.cashier)
        page = self.client.get(reverse("restaurant:platforms"), {"lang": "ar"})
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-platform-form")
        self.assertEqual(self.client.post(reverse("restaurant:platforms"), {"lang": "ar", "customer": self.talabat.pk, "action": "remove"}).status_code, 403)
        self.assertIsNotNone(platforms.for_customer(self.talabat))
        self.assertContains(self.order_post(order, "platform_hand_over", self.cashier), 'data-platform-panel="handed"')

    def test_collecting_needs_the_receive_payment_right(self):
        """No seeded role sells without receiving payments, so a role holding the floor and selling only is made here."""

        from hesba_testing.factories import grant, make_role, make_user, make_user_profile
        from permissions.models import Permission

        order = self.platform_order()
        self.pay(order)
        platforms.hand_over(Order.objects.get(pk=order.pk), self.owner)
        till_only = make_role(code="TILL-ONLY")
        for code in ("sales.view_sales_invoices", "sales.create_sales_invoice", "restaurant.take_orders"):
            grant(till_only, Permission.objects.get(code=code))
        seller = make_user(username="till_only")
        make_user_profile(user=seller, role=till_only)
        page = self.order_page(order, seller)
        self.assertContains(page, 'data-platform-panel="handed"')
        self.assertNotContains(page, "data-platform-collected")
        self.assertEqual(self.order_post(order, "platform_collected", seller).status_code, 403)
        self.assertEqual((Shipment.objects.get().status, balance(self.talabat)), (ShipmentStatus.WITH_COURIER, D("125.40")))
        self.assertContains(self.order_page(order, self.cashier), "data-platform-collected")  # the cashier holds it
        with self.assertRaises(PermissionDenied):
            platforms.collected(Order.objects.get(pk=order.pk), self.keeper)

    def test_another_branchs_platform_is_not_listed_and_a_404_on_the_set_up_page(self):
        order = self.platform_order()
        self.pay(order)
        platforms.hand_over(Order.objects.get(pk=order.pk), self.owner)
        platforms.collected(Order.objects.get(pk=order.pk), self.owner)
        self.enter(self.manager_b, entity=self.branch)
        page = self.client.get(reverse("restaurant:platforms"), {"lang": "ar"})
        self.assertNotContains(page, f'data-platform-row="{self.talabat.pk}"')
        self.assertNotContains(page, "125.40")
        self.assertEqual(self.client.get(reverse("restaurant:platforms"), {"lang": "ar", "edit": self.talabat.pk}).status_code, 404)
        response = self.client.post(reverse("restaurant:platforms"), {"lang": "ar", "action": "remove", "customer": self.talabat.pk})
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse("restaurant:platforms"), {"lang": "ar", "customer": self.talabat.pk, "courier": self.courier.pk, "commission": "5"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(platforms.for_customer(self.talabat).commission, D("18.00"))

    def test_another_branchs_order_is_a_404_and_a_platform_opens_only_in_its_couriers_branch(self):
        order = self.platform_order()
        self.pay(order)
        response = self.order_post(order, "platform_hand_over", self.manager_b, entity=self.branch)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.order_page(order, self.manager_b, entity=self.branch).status_code, 404)
        self.assertFalse(Shipment.objects.exists())
        with working_in(self.branch), self.assertRaises(ValidationError) as caught:
            platforms.open_order(self.manager_b, self.platform, "BR-1")
        self.assertIn("طلبات متسجّلة على", caught.exception.messages[0])
        self.enter(self.manager_b, entity=self.branch)
        self.assertNotContains(self.client.get(reverse("restaurant:board"), {"lang": "ar"}), 'data-new-order="platform"')
