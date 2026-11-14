"""Restaurants R7 (HG-116): the call centre. The caller by phone number, their saved addresses and the delivery zone.

The set-up of ``tests_split_pay``: the main entity (hall «HALL», till «TILL») and the Maadi branch, the clock on Wednesday 11 Nov
2026 at noon in Cairo, and R1a's burger at 120.00 with a required «ميديم» and the priced add-on «جبنة زيادة» at 15.00. Zone «المعادي»
(main) charges 20.00 and takes about 40 minutes; zone «المعادي الجديدة» belongs to the Maadi branch and charges 35.00. Sara's card
has the number 01001234567.

* the typed number finds one customer however it is spelt: «01001234567», «+20 100 123 4567», «1001234567» and Arabic-Indic
  digits; a new caller is added once (a second call with another spelling adds nothing);
* **zone A at 20.00:** a burger (120.00) and the fee at payment: VAT on 120.00 + 16.80 + 20.00 + 2.80 = **159.60**; VAT off
  **140.00**. A fee changed on the order is the one billed. The fee line is the existing ``DELIVERY-FEE`` and nothing is posted
  before payment;
* «كرر» puts a past order's dishes on a new delivery at **today's** prices (the burger at 130.00 and the cheese at 18.00 after
  they were raised), unsent, skipping a dish that is off the menu and saying so; a combo goes back through its own path;
* one default address a customer; another branch's zone is neither listed nor taken from a crafted post (R12); a zone an
  address uses is switched off, never deleted; the balance only with the customer report right.
"""

import re
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, stock_in
from ledger.models import JournalLine
from master_data.models import Customer
from permissions.models import RoleCode
from sales.models import CustomerLedgerEntry, SalesInvoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from . import callcentre, combos, services
from .models import CustomerAddress, DeliveryZone, Order, OrderKind, OrderStatus
from .tests import person
from .tests_combos import meal
from .tests_split_pay import SplitBillSetup

SPELLINGS = ("01001234567", "+20 100 123 4567", "1001234567", "٠١٠٠١٢٣٤٥٦٧", "+٢٠ ١٠٠ ١٢٣ ٤٥٦٧", " 0100 123 4567 ")


class CallSetup(SplitBillSetup):
    def setUp(self):
        super().setUp()
        self.zone_a = callcentre.save_zone({"name_ar": "المعادي", "name_en": "Maadi", "fee": "20.00", "minutes": "40"}, self.owner, entity=self.main)
        self.zone_b = callcentre.save_zone({"name_ar": "المعادي الجديدة", "fee": "35.00"}, self.owner, entity=self.branch)
        self.sara = make_customer(customer_code="C-SARA", name="سارة أحمد", phone="01001234567")
        self.home = callcentre.save_address(self.sara, {"label": "البيت", "address": "12 شارع النصر", "area": "المعادي", "landmark": "جنب الصيدلية",
                                                        "zone": self.zone_a.pk}, self.owner)
        self.cashier = person(RoleCode.CASHIER, "call_cashier")
        self.waiter_user = person(RoleCode.WAITER, "call_waiter")
        self.manager = person(RoleCode.MANAGER, "call_manager")
        self.keeper = person(RoleCode.STOCK_KEEPER, "call_keeper")

    # ---- helpers ------------------------------------------------------------------------------------------------

    def enter(self, user=None, entity=None):
        """Sign in and work in one entity (the main one by default), so a screen is a single branch's, as for a branch's staff."""

        self.work_in(self.main if entity is None else entity, user or self.owner)

    def go(self, phone="", user=None, lang="ar", entity=None, **extra):
        self.enter(user)
        return self.client.get(reverse("restaurant:call"), {"lang": lang, "phone": phone, **extra})

    def act(self, action, user=None, lang="ar", **data):
        self.enter(user)
        return self.client.post(reverse("restaurant:call"), {"lang": lang, "action": action, **data}, follow=True)

    def deliver(self, user=None, address=None, **extra):
        response = self.act("open", user, customer=self.sara.pk, address=(address or self.home).pk, **extra)
        return Order.objects.order_by("-pk").first(), response

    def money(self):
        """Every money row the call screen must never write before a bill is paid."""

        return {"invoices": SalesInvoice.objects.count(), "journal": JournalLine.objects.count(), "customer_ledger": CustomerLedgerEntry.objects.count(),
                **{key: value for key, value in self.moved().items() if key in ("cash", "stock", "invoices")}}

    def pay(self, order, **extra):
        extra.setdefault("tendered", D("10000"))
        return services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, **extra)[0]

    def dishes(self, order):
        return [(line.item.item_code, line.quantity, line.unit_price, line.parent_id is not None) for line in order.lines.order_by("pk")]


class PhoneLookupTests(CallSetup):
    def test_every_spelling_of_the_number_finds_the_same_customer(self):
        for typed in SPELLINGS:
            self.assertEqual(callcentre.find_caller(typed), self.sara, typed)
            self.assertContains(self.go(typed), "سارة أحمد", msg_prefix=typed)

    def test_a_card_stored_with_the_country_code_is_found_by_the_local_spelling(self):
        omar = make_customer(customer_code="C-OMAR", name="عمر خالد", phone="+20 111 222 3333")
        for typed in ("01112223333", "1112223333", "+20 111 222 3333", "٠١١١٢٢٢٣٣٣٣"):
            self.assertEqual(callcentre.find_caller(typed), omar, typed)

    def test_an_unknown_number_finds_nobody_and_the_screen_offers_a_new_customer(self):
        self.assertIsNone(callcentre.find_caller("01555555555"))
        page = self.go("01555555555", self.manager)
        self.assertContains(page, "data-new-caller-form")
        self.assertContains(page, 'value="01555555555"')
        self.assertNotContains(page, "data-caller=")

    def test_a_number_that_is_too_short_or_not_a_number_is_refused_with_a_message(self):
        self.assertContains(self.go("12345"), "٧ أرقام على الأقل")
        self.assertContains(self.go("0100123456a"), "الرقم ده مش مظبوط")
        self.assertContains(self.go("12345", lang="en"), "7 digits at least")
        for bad in ("12345", "", "abcdefgh", "0100123456a"):
            with self.assertRaises(ValidationError):
                callcentre.clean_phone(bad)

    def test_the_walk_in_customer_is_never_a_caller(self):
        from sales.pos import walk_in_customer

        walk_in = walk_in_customer()
        walk_in.phone = "01099998888"
        walk_in.save(update_fields=["phone"])
        self.assertIsNone(callcentre.find_caller("01099998888"))


class NewCallerTests(CallSetup):
    def test_a_new_caller_is_added_once_whatever_the_spelling_of_the_second_call(self):
        before = Customer.objects.count()
        first = self.act("new_customer", self.manager, name="هدى سامي", phone="٠١٢٢٣٣٤٤٥٥٦")
        self.assertEqual(Customer.objects.count(), before + 1)
        huda = Customer.objects.get(name="هدى سامي")
        self.assertEqual(huda.phone, "01223344556")  # stored with Latin digits
        self.assertContains(first, "data-caller-name")
        self.assertContains(first, "اتضاف العميل هدى سامي")
        second = self.act("new_customer", self.manager, name="اسم تاني", phone="+20 122 334 4556")
        third = self.act("new_customer", self.manager, name="اسم تالت", phone="1223344556")
        self.assertEqual(Customer.objects.count(), before + 1)
        self.assertContains(second, "هدى سامي")
        self.assertContains(third, "هدى سامي")
        self.assertNotContains(third, "اسم تالت")
        self.assertEqual(AuditLog.objects.filter(action="call_centre_new_caller").count(), 1)

    def test_the_new_customer_needs_a_name_and_a_real_number(self):
        before = Customer.objects.count()
        self.assertContains(self.act("new_customer", self.manager, name="  ", phone="01233445566"), "اكتب اسم العميل")
        self.assertContains(self.act("new_customer", self.manager, name="هدى", phone="123"), "٧ أرقام على الأقل")
        self.assertEqual(Customer.objects.count(), before)

    def test_adding_a_customer_needs_the_customers_right_as_the_till_does(self):
        before = Customer.objects.count()
        for user in (self.cashier, self.waiter_user):
            self.enter(user)
            response = self.client.post(reverse("restaurant:call"), {"lang": "ar", "action": "new_customer", "name": "هدى", "phone": "01233445566"})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(Customer.objects.count(), before)
        with self.assertRaises(PermissionDenied):
            callcentre.new_caller("هدى", "01233445566", self.waiter_user)

    def test_someone_without_the_right_opens_the_delivery_from_a_typed_number_and_address_with_no_card(self):
        page = self.go("01233445566", self.waiter_user)
        self.assertNotContains(page, "data-new-caller-form")
        self.assertContains(page, "data-typed-delivery-form")
        before = Customer.objects.count()
        response = self.act("open_typed", self.waiter_user, phone="٠١٢٣٣٤٤٥٥٦٦", address="3 شارع التحرير", delivery_fee="12")
        order = Order.objects.get()
        self.assertEqual((order.kind, order.customer, order.phone, order.address, order.delivery_fee, order.opened_by),
                         (OrderKind.DELIVERY, None, "01233445566", "3 شارع التحرير", D("12.00"), self.waiter_user))
        self.assertEqual(Customer.objects.count(), before)

    def test_a_typed_delivery_needs_an_address(self):
        response = self.act("open_typed", self.waiter_user, phone="01233445566", address="  ")
        self.assertContains(response, "الدليفري محتاج عنوان")
        self.assertFalse(Order.objects.exists())


class ZoneFeeTests(CallSetup):
    def test_the_zones_fee_opens_the_delivery_with_the_address_phone_and_fee(self):
        order, response = self.deliver()
        self.assertEqual((order.kind, order.customer, order.entity, order.delivery_fee, order.phone),
                         (OrderKind.DELIVERY, self.sara, self.main, D("20.00"), "01001234567"))
        self.assertEqual(order.address, "12 شارع النصر, المعادي, جنب الصيدلية")
        self.assertContains(response, "12 شارع النصر")
        self.assertContains(response, "data-delivery-fee-line")
        self.assertContains(response, "20.00")

    def test_zone_a_at_20_00_with_vat_on_the_fee_line_is_20_00_plus_2_80_and_the_invoice_159_60(self):
        order, _ = self.deliver()
        services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk])
        invoice = self.pay(order)
        rows = {line.item.item_code: line for line in invoice.lines.all()}
        self.assertEqual((rows["DELIVERY-FEE"].unit_sale_price, rows["DELIVERY-FEE"].quantity, rows["D-BURG"].line_total_amount), (D("20.00"), D("1.000"), D("120.00")))
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("19.60"), D("159.60")))  # 16.80 + 2.80
        from taxes.models import SalesLineTax

        self.assertEqual(SalesLineTax.objects.get(line=rows["DELIVERY-FEE"]).tax_amount, D("2.80"))

    def test_zone_a_at_20_00_with_vat_off_the_invoice_is_140_00(self):
        self.vat(False)
        order, _ = self.deliver()
        services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk])
        invoice = self.pay(order)
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("0.00"), D("140.00")))
        self.assertEqual(invoice.lines.get(item__item_code="DELIVERY-FEE").line_total_amount, D("20.00"))

    def test_a_fee_changed_in_the_box_is_the_one_kept_and_billed(self):
        order, _ = self.deliver(delivery_fee="25")
        self.assertEqual(order.delivery_fee, D("25.00"))
        services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk])
        dispatched = services.dispatch(order, self.owner_employee(), self.owner, delivery_fee="30")  # still editable as today
        self.assertEqual(dispatched.delivery_fee, D("30.00"))
        invoice = self.pay(order)
        self.assertEqual(invoice.lines.get(item__item_code="DELIVERY-FEE").line_total_amount, D("30.00"))
        self.assertEqual(invoice.total_amount, D("120.00") + D("16.80") + D("30.00") + D("4.20"))  # 171.00

    def test_an_emptied_fee_box_means_no_fee_as_it_always_did(self):
        order, _ = self.deliver(delivery_fee="")
        self.assertEqual(order.delivery_fee, D("0.00"))

    def test_a_bad_fee_is_refused_and_nothing_is_opened(self):
        _order, response = self.deliver(delivery_fee="-5")
        self.assertContains(response, "رسوم التوصيل لازم تكون رقم صفر أو أكبر")
        self.assertFalse(Order.objects.exists())

    def test_an_address_without_a_zone_opens_with_no_fee(self):
        office = callcentre.save_address(self.sara, {"label": "الشغل", "address": "5 شارع التسعين"}, self.owner)
        order, _ = self.deliver(address=office, delivery_fee="0")
        self.assertEqual(order.delivery_fee, D("0.00"))
        self.assertEqual(order.address, "5 شارع التسعين")
        self.assertEqual(callcentre.open_delivery(self.owner, customer=self.sara, address=office).delivery_fee, D("0.00"))

    def test_the_service_fills_the_zones_fee_when_no_fee_is_posted(self):
        self.assertEqual(callcentre.open_delivery(self.owner, customer=self.sara, address=self.home).delivery_fee, D("20.00"))

    def test_a_zone_switched_off_gives_no_fee(self):
        callcentre.save_zone({"name_ar": "المعادي", "fee": "20.00", "active": False}, self.owner, self.zone_a)
        self.assertEqual(callcentre.open_delivery(self.owner, customer=self.sara, address=self.home).delivery_fee, D("0.00"))
        with self.assertRaisesMessage(ValidationError, "المنطقة دي مش شغالة"):
            callcentre.save_address(self.sara, {"address": "x", "zone": self.zone_a.pk}, self.owner)

    def test_the_screen_shows_the_zone_its_fee_and_its_usual_time(self):
        page = self.go("01001234567")
        self.assertContains(page, 'data-address-zone')
        self.assertContains(page, "المنطقة: المعادي")
        self.assertContains(page, 'value="20.00"')
        self.assertContains(page, "40")
        english = self.go("01001234567", lang="en")
        self.assertContains(english, "Usually delivered in")

    def owner_employee(self):
        from staff.services import save_employee

        return save_employee({"name": "محمود سعيد"}, self.owner)


class NothingPostsTests(CallSetup):
    def test_the_whole_call_flow_writes_no_money_until_the_bill_is_paid(self):
        before = self.money()
        self.go("01001234567")
        self.act("new_customer", self.manager, name="هدى سامي", phone="01223344556")
        self.act("add_address", self.manager, customer=self.sara.pk, label="الشغل", address="5 شارع التسعين", area="التجمع")
        order, _ = self.deliver()
        services.add_item(order, self.burger, self.owner, 2, options=[self.medium.pk, self.cheese_opt.pk])
        services.send_to_kitchen(order, self.owner)
        self.assertEqual(self.money(), before)
        invoice = self.pay(order)
        self.assertEqual(self.money()["invoices"], before["invoices"] + 1)
        self.assertEqual(invoice.restaurant_order, order)
        self.assertTrue(self.money()["cash"] > before["cash"])


class RepeatTests(CallSetup):
    def first_order(self):
        """Sara's paid delivery: two burgers with «ميديم» and cheese, and two teas."""

        order = callcentre.open_delivery(self.owner, customer=self.sara, address=self.home)
        services.add_item(order, self.burger, self.owner, 2, options=[self.medium.pk, self.cheese_opt.pk])
        services.add_item(order, self.tea, self.owner, 2, note="من غير سكر")
        services.send_to_kitchen(order, self.owner)
        self.pay(order)
        return Order.objects.get(pk=order.pk)

    def repeat(self, source, user=None, lang="ar", **extra):
        self.enter(user)
        response = self.client.post(reverse("restaurant:call"), {"lang": lang, "action": "repeat", "customer": self.sara.pk, "order": source.pk,
                                                                 "address": self.home.pk, **extra}, follow=True)
        return Order.objects.order_by("-pk").first(), response

    def test_repeat_copies_the_dishes_at_todays_prices_unsent_with_their_add_ons_and_choices(self):
        source = self.first_order()
        self.assertEqual(source.invoice.total_amount, D("307.80") + D("30.00") + D("20.00") + D("2.80") + D("4.20"))  # 2 × (120 + 15) + 2 × 15, + the fee
        self.burger.default_sale_price = D("130.00")
        self.burger.save()
        self.cheese.default_sale_price = D("18.00")
        self.cheese.save()
        order, response = self.repeat(source)
        self.assertNotEqual(order, source)
        self.assertEqual((order.status, order.kind, order.customer, order.delivery_fee), (OrderStatus.OPEN, OrderKind.DELIVERY, self.sara, D("20.00")))
        self.assertEqual(self.dishes(order), [("D-BURG", D("2.000"), D("130.00"), False), ("A-CHEESE", D("2.000"), D("18.00"), True),
                                              ("D-TEA", D("2.000"), D("15.00"), False)])
        burger_line = order.lines.get(item=self.burger)
        self.assertEqual((burger_line.options, burger_line.note, order.lines.get(item=self.tea).note), ("ميديم", "", "من غير سكر"))
        self.assertEqual(order.lines.get(item=self.cheese).parent, burger_line)
        self.assertFalse(order.lines.filter(ticket__isnull=False).exists())  # unsent
        self.assertEqual(order.tickets.count(), 0)
        self.assertEqual(order.total, D("2") * D("130.00") + D("2") * D("18.00") + D("2") * D("15.00") + D("20.00"))  # 346.00
        self.assertContains(response, "اتكرر 2 من الطلب")
        self.assertContains(response, "ولسه ما اتبعتش للمطبخ")
        self.assertEqual(source.lines.get(item=self.burger).unit_price, D("120.00"))  # the past order is untouched

    def test_repeat_pays_the_new_order_at_the_new_prices(self):
        source = self.first_order()
        self.burger.default_sale_price = D("130.00")
        self.burger.save()
        order, _ = self.repeat(source)
        invoice = self.pay(order)
        self.assertEqual(invoice.lines.get(item=self.burger).unit_sale_price, D("130.00"))
        self.assertEqual(invoice.lines.get(item__item_code="DELIVERY-FEE").line_total_amount, D("20.00"))

    def test_a_dish_that_is_off_the_menu_is_skipped_and_listed(self):
        source = self.first_order()
        self.tea.active = False
        self.tea.save()
        order, response = self.repeat(source)
        self.assertEqual([code for code, *_ in self.dishes(order)], ["D-BURG", "A-CHEESE"])
        self.assertContains(response, "ما اتكررش:")
        self.assertContains(response, "«شاي» مش على المنيو دلوقتي")
        self.assertContains(response, "اتكرر 1 من الطلب")

    def test_a_dish_whose_choice_is_no_longer_offered_is_skipped_and_listed(self):
        source = self.first_order()
        self.cheese_opt.active = False
        self.cheese_opt.save()
        order, response = self.repeat(source, lang="en")
        self.assertEqual([code for code, *_ in self.dishes(order)], ["D-TEA"])
        self.assertContains(response, "Not repeated:")
        self.assertContains(response, "has other choices now")

    def test_nothing_repeated_still_opens_the_order_and_says_so(self):
        source = self.first_order()
        for item in (self.burger, self.tea):
            item.active = False
            item.save()
        order, response = self.repeat(source)
        self.assertEqual((order.status, order.lines.count()), (OrderStatus.OPEN, 0))
        self.assertContains(response, "مفيش أصناف اتكررت")

    def test_repeating_an_order_that_is_not_this_customers_is_refused(self):
        other = make_customer(customer_code="C-OTHER", name="كريم", phone="01077776666")
        theirs = callcentre.save_address(other, {"address": "9 شارع الهرم"}, self.owner)
        stranger = callcentre.open_delivery(self.owner, customer=other, address=theirs)
        services.add_item(stranger, self.tea, self.owner, 1)
        self.pay(stranger)
        before = Order.objects.count()
        _order, response = self.repeat(stranger)
        self.assertContains(response, "الطلب ده مش من طلبات العميل ده")
        self.assertEqual(Order.objects.count(), before)

    def test_a_combo_goes_back_through_its_own_path_at_todays_prices(self):
        fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=True)
        stock_in(pepsi, self.location, 20, "8.00")
        combo = combos.save_combo(meal(self.burger, fries, large, pepsi), self.owner)
        sandwich, side, drink = combo.slots.order_by("sort", "pk")
        order = callcentre.open_delivery(self.owner, customer=self.sara, address=self.home)
        services.add_combo(order, combo, self.owner, {sandwich.pk: sandwich.choices.get().pk, side.pk: side.choices.get(item=large).pk, drink.pk: drink.choices.get().pk},
                           options={sandwich.pk: [self.medium.pk]})
        self.pay(order)
        source = Order.objects.get(pk=order.pk)
        # the combo's price and its parts go up; the combo is repeated at what its parts cost now, with the saving of the combo
        fries.default_sale_price = D("45.00")
        fries.save()
        large.default_sale_price = D("55.00")
        large.save()
        again, response = self.repeat(source)
        lines = list(again.lines.order_by("pk"))
        self.assertEqual([(line.item.item_code, line.unit_price, line.combo_id, line.bundle) for line in lines],
                         [("D-BURG", D("120.00"), combo.pk, 1), ("C-FRIES-L", D("55.00"), combo.pk, 1), ("C-PEPSI", D("25.00"), combo.pk, 1)])
        self.assertEqual(lines[0].options, "ميديم")
        self.assertEqual(sum(line.line_discount for line in lines), D("120.00") + D("55.00") + D("25.00") - (combo.price + D("10.00")))  # the saving, as add_combo spreads it
        self.assertContains(response, "اتكرر 1 من الطلب")
        self.assertFalse(again.lines.filter(ticket__isnull=False).exists())

    def test_a_combo_that_was_switched_off_is_skipped_and_listed(self):
        fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=True)
        stock_in(pepsi, self.location, 20, "8.00")
        combo = combos.save_combo(meal(self.burger, fries, large, pepsi), self.owner)
        sandwich, side, drink = combo.slots.order_by("sort", "pk")
        order = callcentre.open_delivery(self.owner, customer=self.sara, address=self.home)
        services.add_combo(order, combo, self.owner, {sandwich.pk: sandwich.choices.get().pk, side.pk: side.choices.get(item=fries).pk, drink.pk: drink.choices.get().pk},
                           options={sandwich.pk: [self.medium.pk]})
        services.add_item(order, self.tea, self.owner, 1)
        self.pay(order)
        combo.active = False
        combo.save()
        again, response = self.repeat(Order.objects.get(pk=order.pk))
        self.assertEqual([code for code, *_ in self.dishes(again)], ["D-TEA"])
        self.assertContains(response, "ما اتكررش:")

    def test_the_last_five_orders_are_this_customers_paid_ones_newest_first(self):
        made = []
        for number in range(7):
            order = callcentre.open_delivery(self.owner, customer=self.sara, address=self.home)
            services.add_item(order, self.tea, self.owner, number + 1)
            made.append(self.pay(order).restaurant_order)
        cancelled = callcentre.open_delivery(self.owner, customer=self.sara, address=self.home)
        services.cancel_order(cancelled, self.owner, "x")
        callcentre.open_delivery(self.owner, customer=self.sara, address=self.home)  # still open
        rows = callcentre.last_orders(self.sara)
        self.assertEqual([row["order"].pk for row in rows], [order.pk for order in reversed(made)][:5])
        self.assertEqual(rows[0]["total"], made[-1].invoice.total_amount)
        self.assertIn("شاي × 7", rows[0]["summary"])
        page = self.go("01001234567")
        self.assertEqual(len(re.findall(r"data-past-order=", page.content.decode())), 5)

    def test_another_branchs_orders_are_not_in_this_branchs_list(self):
        order = callcentre.open_delivery(self.owner, customer=self.sara, address=self.home, entity=self.branch)
        services.add_item(order, self.tea, self.owner, 1)
        services.pay(Order.objects.get(pk=order.pk), self.manager_b, cashbox=self.b_cashbox, tendered=D("1000"))
        from entities.current import working_in

        self.assertEqual(len(callcentre.last_orders(self.sara)), 1)  # worked in no entity: the whole group sees it

        with working_in(self.main):
            self.assertEqual(callcentre.last_orders(self.sara), [])
        with working_in(self.branch):
            self.assertEqual(len(callcentre.last_orders(self.sara)), 1)


class AddressTests(CallSetup):
    def test_the_first_address_is_the_default_and_a_new_default_takes_it_over(self):
        self.assertTrue(self.home.is_default)
        office = callcentre.save_address(self.sara, {"label": "الشغل", "address": "5 شارع التسعين"}, self.owner)
        self.assertFalse(office.is_default)
        self.assertEqual([row.label for row in callcentre.addresses_of(self.sara)], ["البيت", "الشغل"])
        villa = callcentre.save_address(self.sara, {"label": "الفيلا", "address": "الساحل", "is_default": "on"}, self.owner)
        self.assertEqual(list(CustomerAddress.objects.filter(customer=self.sara, is_default=True)), [villa])
        self.assertEqual([row.label for row in callcentre.addresses_of(self.sara)], ["الفيلا", "البيت", "الشغل"])  # default first
        callcentre.make_default(self.sara, office, self.owner)
        self.assertEqual(list(CustomerAddress.objects.filter(customer=self.sara, is_default=True)), [office])

    def test_the_database_itself_refuses_two_defaults_for_one_customer(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerAddress.objects.create(customer=self.sara, address="تاني", is_default=True)
        CustomerAddress.objects.create(customer=self.sara, address="تاني")  # not a default: fine
        other = make_customer(customer_code="C-OTHER", name="كريم", phone="01077776666")
        CustomerAddress.objects.create(customer=other, address="غيره", is_default=True)  # another customer's own default

    def test_removing_the_default_passes_it_on_and_a_removed_address_is_not_offered(self):
        office = callcentre.save_address(self.sara, {"label": "الشغل", "address": "5 شارع التسعين"}, self.owner)
        callcentre.remove_address(self.sara, self.home, self.owner)
        self.assertEqual([(row.label, row.is_default) for row in callcentre.addresses_of(self.sara)], [("الشغل", True)])
        self.assertNotContains(self.go("01001234567"), "12 شارع النصر")
        with self.assertRaisesMessage(ValidationError, "العنوان ده مش للعميل ده أو اتشال"):
            callcentre.make_default(self.sara, self.home, self.owner)
        self.assertTrue(CustomerAddress.objects.filter(pk=self.home.pk, active=False, is_default=False).exists())  # kept, never deleted
        self.assertEqual(callcentre.open_delivery(self.owner, customer=self.sara, address=office).address, "5 شارع التسعين")

    def test_an_address_of_another_customer_cannot_open_a_delivery(self):
        other = make_customer(customer_code="C-OTHER", name="كريم", phone="01077776666")
        theirs = callcentre.save_address(other, {"address": "9 شارع الهرم"}, self.owner)
        response = self.act("open", customer=self.sara.pk, address=theirs.pk)
        self.assertContains(response, "العنوان ده مش للعميل ده أو اتشال")
        self.assertFalse(Order.objects.exists())

    def test_the_address_needs_text_and_the_card_address_is_offered_when_there_are_none(self):
        with self.assertRaisesMessage(ValidationError, "اكتب العنوان"):
            callcentre.save_address(self.sara, {"address": "  "}, self.owner)
        noor = make_customer(customer_code="C-NOOR", name="نور", phone="01011112222", address="7 شارع الجامعة\nالدور 3")
        page = self.go("01011112222")
        self.assertContains(page, "data-card-address")
        self.assertContains(page, "7 شارع الجامعة, الدور 3")
        self.assertContains(page, "data-no-addresses")
        self.assertEqual(Customer.objects.get(pk=noor.pk).address, "7 شارع الجامعة\nالدور 3")  # the card's own line is left as it is

    def test_adding_an_address_from_the_screen(self):
        response = self.act("add_address", customer=self.sara.pk, label="الشغل", address="5 شارع التسعين", area="التجمع", landmark="جنب المول",
                            zone=self.zone_a.pk, phone="01001234567")
        self.assertContains(response, "اتحفظ العنوان")
        office = CustomerAddress.objects.get(label="الشغل")
        self.assertEqual((office.zone, office.area, office.landmark, office.is_default), (self.zone_a, "التجمع", "جنب المول", False))
        self.assertEqual(len(re.findall(r"data-address-default", response.content.decode())), 1)


class BranchTests(CallSetup):
    def test_another_branchs_zone_is_not_listed_or_taken_from_a_crafted_post(self):
        page = self.go("01001234567")
        self.assertContains(page, "المعادي · 20.00")
        self.assertNotContains(page, "المعادي الجديدة")
        response = self.act("add_address", customer=self.sara.pk, address="5 شارع التسعين", zone=self.zone_b.pk, phone="01001234567")
        self.assertContains(response, "المنطقة دي مش من مناطق الفرع")
        self.assertEqual(CustomerAddress.objects.filter(customer=self.sara).count(), 1)
        zones = self.client.get(reverse("restaurant:zones"), {"lang": "ar"})
        self.assertContains(zones, "المعادي")
        self.assertNotContains(zones, "المعادي الجديدة")
        self.assertEqual(self.client.get(reverse("restaurant:zones"), {"edit": self.zone_b.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse("restaurant:zones"), {"pk": self.zone_b.pk, "name_ar": "x", "fee": "1"}).status_code, 404)
        self.assertEqual(DeliveryZone.objects.get(pk=self.zone_b.pk).fee, D("35.00"))

    def test_the_branchs_own_staff_see_their_zone_and_it_prices_their_delivery(self):
        branch_zone_address = callcentre.save_address(self.sara, {"label": "المعادي الجديدة", "address": "حدائق المعادي", "zone": self.zone_b.pk}, self.owner)
        self.enter(self.manager_b, self.branch)
        page = self.client.get(reverse("restaurant:call"), {"lang": "ar", "phone": "01001234567"})
        self.assertContains(page, "المعادي الجديدة · 35.00")
        self.assertNotContains(page, "المعادي · 20.00")
        self.client.post(reverse("restaurant:call"), {"lang": "ar", "action": "open", "customer": self.sara.pk, "address": branch_zone_address.pk, "delivery_fee": "35.00"})
        order = Order.objects.get()
        self.assertEqual((order.entity, order.delivery_fee), (self.branch, D("35.00")))

    def test_an_address_whose_zone_is_another_branchs_gives_no_fee_in_this_branch(self):
        far = callcentre.save_address(self.sara, {"label": "بعيد", "address": "x", "zone": self.zone_b.pk}, self.owner)
        self.assertEqual(callcentre.open_delivery(self.owner, customer=self.sara, address=far).delivery_fee, D("0.00"))  # the main branch
        self.assertEqual(callcentre.open_delivery(self.owner, customer=self.sara, address=far, entity=self.branch).delivery_fee, D("35.00"))

    def test_across_the_group_the_branch_is_picked_and_prices_the_delivery(self):
        far = callcentre.save_address(self.sara, {"label": "بعيد", "address": "حدائق المعادي", "zone": self.zone_b.pk}, self.owner)
        self.work_in(None)
        page = self.client.get(reverse("restaurant:call"), {"lang": "ar", "phone": "01001234567"})
        self.assertContains(page, "data-order-branch-pick")
        self.assertContains(page, "اختار الفرع الأول")
        self.assertNotContains(page, "data-open-delivery")
        refused = self.client.post(reverse("restaurant:call"), {"lang": "ar", "action": "open", "customer": self.sara.pk, "address": far.pk}, follow=True)
        self.assertContains(refused, "اختار الفرع الأول")
        self.assertFalse(Order.objects.exists())
        picked = self.client.get(reverse("restaurant:call"), {"lang": "ar", "phone": "01001234567", "entity": self.branch.pk})
        self.assertContains(picked, "data-open-delivery")
        self.assertContains(picked, "المعادي الجديدة · 35.00")
        self.assertNotContains(picked, "المعادي · 20.00")  # the branch picked: only its zones
        self.client.post(reverse("restaurant:call"), {"lang": "ar", "action": "open", "customer": self.sara.pk, "address": far.pk, "entity": self.branch.pk,
                                                      "delivery_fee": "35.00"})
        order = Order.objects.get()
        self.assertEqual((order.entity, order.delivery_fee), (self.branch, D("35.00")))

    def test_the_same_zone_name_can_exist_in_each_branch_but_not_twice_in_one(self):
        callcentre.save_zone({"name_ar": "المعادي", "fee": "50"}, self.owner, entity=self.branch)
        with self.assertRaisesMessage(ValidationError, "فيه منطقة بنفس الاسم"):
            callcentre.save_zone({"name_ar": " المعادي ", "fee": "1"}, self.owner, entity=self.main)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DeliveryZone.objects.create(name_ar="المعادي", entity=self.main)
        self.assertEqual(DeliveryZone.objects.create(name_ar="المقطم").entity, self.main)  # made with no entity: the main one


class ZoneSetupTests(CallSetup):
    def test_a_zone_in_use_is_switched_off_never_deleted(self):
        with self.assertRaisesMessage(ValidationError, "اقفلها بدل ما تمسحها"):
            callcentre.delete_zone(self.zone_a, self.owner)
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.zone_a.delete()  # the database too
        self.enter(self.manager)
        crafted = self.client.post(reverse("restaurant:zones"), {"lang": "ar", "action": "delete", "pk": self.zone_a.pk})
        self.assertContains(crafted, "data-zone-error")
        self.assertTrue(DeliveryZone.objects.filter(pk=self.zone_a.pk).exists())
        page = self.client.get(reverse("restaurant:zones"), {"lang": "ar"})
        self.assertNotContains(page, f'name="pk" value="{self.zone_a.pk}"><input type="hidden" name="action" value="delete"')
        self.client.post(reverse("restaurant:zones"), {"lang": "ar", "pk": self.zone_a.pk, "name_ar": "المعادي", "fee": "20", "minutes": "40"})  # active box left off
        self.assertFalse(DeliveryZone.objects.get(pk=self.zone_a.pk).active)
        self.assertEqual(callcentre.offered_zones().filter(pk=self.zone_a.pk).count(), 0)

    def test_an_unused_zone_can_be_deleted(self):
        spare = callcentre.save_zone({"name_ar": "غلط"}, self.owner, entity=self.main)
        self.enter(self.manager)
        page = self.client.get(reverse("restaurant:zones"), {"lang": "ar"})
        self.assertContains(page, "data-zone-delete")
        self.client.post(reverse("restaurant:zones"), {"lang": "ar", "action": "delete", "pk": spare.pk})
        self.assertFalse(DeliveryZone.objects.filter(pk=spare.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete_zone").exists())

    def test_the_zones_form_saves_a_zone_with_its_fee_and_time(self):
        self.enter(self.manager)
        response = self.client.post(reverse("restaurant:zones"), {"lang": "ar", "name_ar": "التجمع", "name_en": "New Cairo", "fee": "45.5", "minutes": "55", "active": "on"}, follow=True)
        zone = DeliveryZone.objects.get(name_ar="التجمع")
        self.assertEqual((zone.fee, zone.minutes, zone.active, zone.entity, zone.name_en), (D("45.50"), 55, True, self.main, "New Cairo"))
        self.assertContains(response, "اتحفظت المنطقة")
        self.assertTrue(AuditLog.objects.filter(action="create_zone", object_id=str(zone.pk)).exists())

    def test_the_fee_and_the_time_are_checked(self):
        for data, message in (({"name_ar": "x", "fee": "-1"}, "رسوم التوصيل لازم"), ({"name_ar": "x", "fee": "abc"}, "رسوم التوصيل لازم"),
                              ({"name_ar": "x", "fee": "5", "minutes": "0"}, "الوقت بالدقايق"), ({"name_ar": "x", "fee": "5", "minutes": "9999"}, "الوقت بالدقايق"),
                              ({"name_ar": " ", "fee": "5"}, "اكتب اسم المنطقة")):
            with self.assertRaisesMessage(ValidationError, message):
                callcentre.save_zone(data, self.owner, entity=self.main)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DeliveryZone.objects.create(name_ar="سالب", fee=D("-1.00"), entity=self.main)
        self.assertEqual(callcentre.save_zone({"name_ar": "مجاني", "fee": ""}, self.owner, entity=self.main).fee, D("0.00"))

    def test_across_the_group_a_new_zone_names_its_branch_and_never_changes_it(self):
        self.work_in(None)
        refused = self.client.post(reverse("restaurant:zones"), {"lang": "ar", "name_ar": "الشروق", "fee": "30"})
        self.assertContains(refused, "اختار الفرع")
        self.client.post(reverse("restaurant:zones"), {"lang": "ar", "name_ar": "الشروق", "fee": "30", "entity": self.branch.pk})
        self.assertEqual(DeliveryZone.objects.get(name_ar="الشروق").entity, self.branch)
        self.client.post(reverse("restaurant:zones"), {"lang": "ar", "pk": self.zone_b.pk, "name_ar": "المعادي الجديدة", "fee": "36", "entity": self.main.pk, "active": "on"})
        moved = DeliveryZone.objects.get(pk=self.zone_b.pk)
        self.assertEqual((moved.entity, moved.fee), (self.branch, D("36.00")))

    def test_a_branch_cannot_save_a_zone_in_another_branch(self):
        with self.assertRaisesMessage(ValidationError, "الفرع ده مش الفرع اللي شغال فيه"):
            from entities.current import working_in

            with working_in(self.main):
                callcentre.save_zone({"name_ar": "الشروق", "fee": "30"}, self.owner, entity=self.branch)


class PermissionTests(CallSetup):
    def test_a_stock_keeper_cannot_use_the_call_screen_or_the_zones(self):
        self.enter(self.keeper)
        self.assertEqual(self.client.get(reverse("restaurant:call"), {"phone": "01001234567"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("restaurant:call"), {"action": "open", "customer": self.sara.pk, "address": self.home.pk}).status_code, 403)
        self.assertEqual(self.client.get(reverse("restaurant:zones")).status_code, 403)  # holds manage_items but cannot see the floor
        self.assertEqual(self.client.post(reverse("restaurant:zones"), {"name_ar": "x", "fee": "1"}).status_code, 403)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(DeliveryZone.objects.count(), 2)

    def test_whoever_takes_orders_can_use_the_call_screen(self):
        for user in (self.waiter_user, self.cashier, self.manager, self.owner):
            self.assertEqual(self.go("01001234567", user).status_code, 200, user.username)
        order, _ = self.deliver(self.waiter_user)
        self.assertEqual((order.opened_by, order.delivery_fee), (self.waiter_user, D("20.00")))
        order = callcentre.open_delivery(self.cashier, customer=self.sara, address=self.home)
        self.assertEqual(order.opened_by, self.cashier)

    def test_who_manages_the_floor_manages_the_zones(self):
        self.enter(self.waiter_user)
        self.assertEqual(self.client.get(reverse("restaurant:zones")).status_code, 403)
        self.enter(self.cashier)  # may see (the invoices' right), may not change
        seen = self.client.get(reverse("restaurant:zones"), {"lang": "ar"})
        self.assertEqual(seen.status_code, 200)
        self.assertNotContains(seen, "data-zone-form")
        self.assertEqual(self.client.post(reverse("restaurant:zones"), {"lang": "ar", "name_ar": "x", "fee": "1"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("restaurant:zones"), {"lang": "ar", "action": "delete", "pk": self.zone_a.pk}).status_code, 403)
        self.enter(self.manager)
        self.assertContains(self.client.get(reverse("restaurant:zones"), {"lang": "ar"}), "data-zone-form")
        self.assertEqual(self.client.post(reverse("restaurant:zones"), {"lang": "ar", "name_ar": "الشروق", "fee": "30"}).status_code, 302)

    def test_the_balance_is_shown_only_with_the_customer_report_right(self):
        self.sara.opening_balance = D("250.00")
        self.sara.save()
        manager_page = self.go("01001234567", self.manager)
        self.assertContains(manager_page, 'data-caller-balance="250.00"')
        self.assertContains(manager_page, "عليه")
        for user in (self.cashier, self.waiter_user):
            page = self.go("01001234567", user)
            self.assertNotContains(page, "data-caller-balance")
            self.assertNotContains(page, "250.00")
        self.assertIsNone(callcentre.customer_balance(self.sara, self.cashier))
        self.assertEqual(callcentre.customer_balance(self.sara, self.manager), D("250.00"))

    def test_the_board_links_show_only_to_who_can_open_the_page(self):
        self.enter(self.waiter_user)
        board = self.client.get(reverse("restaurant:board"))
        self.assertContains(board, "data-call-link")
        self.assertNotContains(board, "data-zones-link")
        self.enter(self.manager)
        board = self.client.get(reverse("restaurant:board"))
        self.assertContains(board, "data-call-link")
        self.assertContains(board, "data-zones-link")
        self.enter(self.keeper)
        self.assertEqual(self.client.get(reverse("restaurant:board")).status_code, 403)


class PageTests(CallSetup):
    def test_the_call_page_in_arabic_and_english(self):
        arabic = self.go("01001234567", lang="ar")
        self.assertContains(arabic, "دليفري جديد")
        self.assertContains(arabic, "رقم الموبايل")
        self.assertContains(arabic, "آخر الطلبات")
        self.assertContains(arabic, 'dir="rtl"')
        english = self.go("01001234567", lang="en")
        self.assertContains(english, "New delivery")
        self.assertContains(english, "Mobile number")
        self.assertContains(english, "Last orders")
        self.assertContains(english, 'dir="ltr"')
        self.assertContains(english, "Open the order")

    def test_the_phone_is_shown_left_to_right_inside_arabic_text(self):
        page = self.go("01001234567")
        self.assertContains(page, 'dir="ltr" class="rc-phone">01001234567')

    def test_the_zones_page_in_arabic_and_english(self):
        self.enter(self.manager)
        arabic = self.client.get(reverse("restaurant:zones"), {"lang": "ar"})
        self.assertContains(arabic, "مناطق التوصيل")
        self.assertContains(arabic, "الوقت المعتاد")
        english = self.client.get(reverse("restaurant:zones"), {"lang": "en"})
        self.assertContains(english, "Delivery zones")
        self.assertContains(english, "Usual time")
        self.assertContains(english, "Maadi")  # the zone's English name

    def test_an_empty_search_shows_just_the_box(self):
        page = self.go("")
        self.assertContains(page, "data-call-phone")
        self.assertNotContains(page, "data-no-caller")
        self.assertNotContains(page, "data-caller=")

    def test_a_customer_with_no_orders_or_addresses_still_shows(self):
        make_customer(customer_code="C-NEW", name="ياسمين", phone="01288887777")
        page = self.go("01288887777")
        self.assertContains(page, "ياسمين")
        self.assertContains(page, "data-no-addresses")
        self.assertContains(page, "مفيش طلبات مدفوعة قبل كده")

    def test_the_found_customers_card_is_there_with_addresses_default_first(self):
        callcentre.save_address(self.sara, {"label": "الشغل", "address": "5 شارع التسعين"}, self.owner)
        page = self.go("01001234567").content.decode()
        self.assertLess(page.index("البيت"), page.index("الشغل"))
        self.assertIn("data-address-default", page)
        self.assertIn(f'data-caller="{self.sara.pk}"', page)
