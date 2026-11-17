"""Restaurants close-out: the restaurant demo company shows every merged restaurant feature with realistic data.

The demo is built the way a visitor's copy is (``build``: the demo setup with its capabilities and modules, the demo users, the sample
month), once for the whole class. Every figure below is stated in full; each is what the screen reads from the service that made it.
The clock is the real one: dates are counted from ``timezone.localdate()``. Nothing is paid or sent through a gateway: the online payment
link stays off in the demo.

Not shown, and why (R5b, R12): paying from the table needs a connected payment gateway (``payment_links`` stays off, ``payments.E001``),
and a second branch would switch the whole demo into group mode.
"""

from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config import demo as showcase
from config import demo_first_run as first_run
from master_data.models import Customer, Item

from . import branches, charges, demo, kitchen, platforms, preorders
from .models import Brand, DeliveryZone, DiningTable, Order, OrderLine, PreOrder, TableRequest, TableReservation

BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")


def build(sub="restaurant"):
    """The demo a visitor gets: the demo setup (its capabilities and modules), the demo users, then the sample month, as the template builder does."""

    from config.demo_templates import complete_demo_setup, seed_sample_business

    call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
    complete_demo_setup("restaurants", sub)
    call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
    assert seed_sample_business()


def books_balance(test):
    from ledger.reports import reconciliation
    from reports import selectors

    result = reconciliation()
    test.assertTrue(result["balanced"] and result["ok"], result["rows"])
    alerts = selectors.stock_alert_counts()
    test.assertEqual((alerts["out_of_stock"], alerts["low_stock"]), (1, 1))  # the demo's planted alerts are as they were


def table(name):
    return DiningTable.objects.get(name=name)


class RestaurantDemo(TestCase):
    @classmethod
    def setUpTestData(cls):
        build()
        cls.today = timezone.localdate()
        cls.owner = get_user_model().objects.get(username="owner")
        cls.roast = Item.objects.get(item_code="DEMO-ITEM-01")  # «نصف فرخة مشوية» 180.00
        cls.pasta = Item.objects.get(item_code="DEMO-ITEM-03")  # «مكرونة بشاميل» 120.00

    # ---- the setup the demo switches on --------------------------------------------------------------------------------------

    def test_the_demo_setup_switches_on_what_the_features_need_and_leaves_the_payment_link_off(self):
        from settings_core.capabilities import capability_enabled

        for slug in ("promotions", "loyalty", "gift_cards", "deposits", "price_lists", "shipping"):
            self.assertTrue(capability_enabled(slug), slug)
        self.assertFalse(capability_enabled("payment_links"))  # R5b: no gateway can be connected on a public demo
        from payments.models import GatewayTransaction, PaymentLink

        self.assertEqual((PaymentLink.objects.count(), GatewayTransaction.objects.count()), (0, 0))

    # ---- R1a, R1b: the table with add-ons and a combo ---------------------------------------------------------------------------

    def test_table_one_has_add_ons_a_required_choice_a_combo_and_drinks(self):
        order = Order.objects.get(table=table("1"), status="open")
        self.assertEqual((order.guests, order.total), (4, D("830.00")))
        lines = [line for line in order.lines.order_by("pk") if line.bundle is None]
        self.assertEqual([(line.item.item_name, line.quantity, line.unit_price, line.options) for line in lines if not line.parent_id],
                         [("نصف فرخة مشوية", D("2.000"), D("180.00"), ""), ("كفتة مشوية (ربع كيلو)", D("1.000"), D("220.00"), "ميديم"),
                          ("مياه معدنية صغيرة", D("2.000"), D("15.00"), "")])  # the required «درجة التسوية» on the second dish
        self.assertEqual([(line.item.item_name, line.quantity, line.unit_price) for line in lines if line.parent_id],
                         [("جبنة زيادة", D("2.000"), D("15.00")), ("جبنة زيادة", D("1.000"), D("15.00"))])  # an add-on is its own priced line
        bundle = list(order.lines.filter(bundle=1).order_by("pk"))
        self.assertEqual(len(bundle), 2)
        self.assertEqual(bundle[0].combo.item.item_name, "وجبة التوفير")
        self.assertEqual(sum(line.quantity * line.unit_price - line.discount for line in bundle), D("175.00"))  # menu 195.00, the combo's saving 20.00
        self.assertEqual(order.tickets.count(), 2)  # sent: one ticket for the kitchen and one for the bar

    # ---- R2a: merge two tables and pay a part by items --------------------------------------------------------------------------

    def test_table_five_is_a_merged_bill_and_a_part_of_it_is_paid_on_its_own(self):
        order = Order.objects.get(table=table("5"), status="open")
        merged = Order.objects.get(table=table("6"), merged_into=order)
        self.assertEqual((merged.status, merged.lines.count()), ("merged", 0))
        self.assertEqual((order.guests, order.total), (5, D("660.00")))  # 2 + 3 guests; 180.00 + 120.00 + 2 × 180.00
        part = Order.objects.get(split_from=order)
        self.assertEqual((part.status, part.total, part.invoice.total_amount, part.invoice.status), ("paid", D("180.00"), D("162.00"), "posted"))  # the shop's 10% on the menu

    # ---- R2b + R6: a coupon and two ways to pay ---------------------------------------------------------------------------------

    def test_a_bill_with_the_coupon_is_paid_in_the_drawer_and_the_bank(self):
        from cashboxes.models import CashboxMovement
        from promotions.models import InvoicePromotion

        invoice = Order.objects.filter(status="paid", customer__customer_code="WALK-IN", invoice__total_amount=D("408.00")).get().invoice
        self.assertEqual(invoice.total_amount, D("408.00"))  # 480.00 less the coupon's 15%
        moves = {move.cashbox.cashbox_code: move.amount for move in CashboxMovement.objects.filter(movement_date=self.today, direction="in", amount__in=(D("245.00"), D("163.00")))}
        self.assertEqual(moves, {"DEMO-CASH-01": D("245.00"), "DEMO-BANK-01": D("163.00")})
        self.assertEqual(InvoicePromotion.objects.get(invoice=invoice).coupon_code, demo.COUPON[0])

    def test_the_coupon_and_the_happy_hour_are_running_offers(self):
        from promotions.models import Promotion

        coupon = Promotion.objects.get(coupon_code=demo.COUPON[0])
        self.assertEqual((coupon.name, coupon.percent, coupon.active), ("كوبون الترحيب", D("15.00"), True))
        happy = Promotion.objects.get(name=demo.HAPPY_HOUR[0])
        self.assertEqual((happy.percent, happy.from_time.strftime("%H:%M"), happy.to_time.strftime("%H:%M"), happy.category.name_ar), (D("20.00"), "17:00", "19:00", "المشروبات"))

    def test_points_and_a_gift_card_pay_part_of_a_bill_and_half_of_the_card_stays(self):
        from giftcards import services as giftcards
        from giftcards.models import GiftCard
        from loyalty.models import EntryKind, PointsEntry

        first = Customer.objects.get(customer_code="DEMO-CUST-01")
        order = Order.objects.get(status="paid", customer=first, table=table("6"), split_from__isnull=True)
        self.assertEqual((order.total, order.invoice.total_amount), (D("300.00"), D("260.00")))  # 100 points are worth 40.00
        spent = PointsEntry.objects.filter(customer=first, kind=EntryKind.SPEND).order_by("-pk").first()  # the shop's own sample spent some earlier
        self.assertEqual(abs(spent.points), 100)
        card = GiftCard.objects.get()
        self.assertEqual((card.face_value, giftcards.balance(card), card.holder_name), (D("260.00"), D("130.00"), first.name))

    # ---- R5a: the table's QR menu with a request waiting -------------------------------------------------------------------------

    def test_a_guests_order_and_a_call_for_the_bill_wait_for_the_cashier(self):
        order, bill = TableRequest.objects.order_by("pk")
        self.assertEqual((order.kind, order.table.name, order.status, order.note), ("order", "6", "new", "بدون بصل لو سمحت"))
        self.assertEqual([(row["item"], row["quantity"]) for row in order.items], [(self.roast.pk, 2), (self.pasta.pk, 1), (Item.objects.get(item_code="DEMO-ITEM-04").pk, 2)])  # two plates, one more and two waters
        self.assertEqual((bill.kind, bill.table.name, bill.items), ("bill", "4", []))
        self.assertEqual(self.client.get(f"/t/{table('6').qr_key}/?lang=ar").status_code, 200)  # the guest's page, no sign-in

    # ---- R7: the call centre ----------------------------------------------------------------------------------------------------

    def test_the_call_centre_knows_three_zones_two_homes_and_repeats_the_last_delivery(self):
        zones = {zone.name_ar: (zone.fee, zone.minutes) for zone in DeliveryZone.objects.all()}
        self.assertEqual(zones, {"مدينة نصر": (D("20.00"), 35), "مصر الجديدة": (D("25.00"), 40), "التجمع الخامس": (D("35.00"), 55)})
        first = Customer.objects.get(customer_code="DEMO-CUST-01")
        self.assertEqual([(a.label, a.zone.name_ar, a.is_default) for a in first.delivery_addresses.order_by("pk")], [("البيت", "مدينة نصر", True), ("الشغل", "التجمع الخامس", False)])
        repeat = Order.objects.get(kind="delivery", status="open", customer=first)
        self.assertEqual((repeat.delivery_fee, repeat.phone, repeat.address.startswith("12 شارع النصر")), (D("20.00"), "01000000101", True))
        self.assertEqual([(line.item.item_name, line.quantity) for line in repeat.lines.all()], [("نصف فرخة مشوية", D("2.000"))])

    # ---- R8: reservations --------------------------------------------------------------------------------------------------------

    def test_tonight_has_three_bookings_one_with_a_deposit_and_tomorrow_one_more(self):
        from deposits import services as deposits

        rows = list(TableReservation.objects.order_by("starts_at"))
        local = [timezone.localtime(row.starts_at) for row in rows]
        self.assertEqual([(row.status, row.table.name, row.party_size, when.date() - self.today, when.strftime("%H:%M")) for row, when in zip(rows, local)],
                         [("confirmed", "3", 4, timedelta(0), "19:30"), ("booked", "2", 4, timedelta(0), "21:00"),
                          ("confirmed", "6", 2, timedelta(0), "21:30"), ("booked", "1", 4, timedelta(days=1), "20:00")])
        held = deposits.held_for(rows[0])
        self.assertEqual((held.payment.amount, held.payment.customer.name), (D("200.00"), "سارة محمود"))
        self.assertIsNone(deposits.held_for(rows[1]))

    # ---- R10: pre-orders ---------------------------------------------------------------------------------------------------------

    def test_two_preorders_for_tomorrow_the_first_with_a_deposit_and_the_production_list_counts_the_kept_plates(self):
        from deposits import services as deposits

        tomorrow = self.today + timedelta(days=1)
        first, second = PreOrder.objects.order_by("starts_at")
        self.assertEqual((first.status, timezone.localtime(first.starts_at).strftime("%H:%M"), [(l.item.item_name, l.quantity, l.unit_price) for l in first.lines.all()]),
                         ("confirmed", "14:00", [("نصف فرخة مشوية", D("6.000"), D("180.00"))]))
        self.assertEqual(deposits.held_for(first).payment.amount, D("300.00"))
        self.assertEqual((second.status, second.lines.count(), second.lines.get(item=self.pasta).quantity), ("booked", 2, D("2.000")))
        row = next(row for row in preorders.production(tomorrow) if row["item"] == self.roast)
        self.assertEqual((row["quantity"], row["in_stock"], row["missing"]), (D("10.000"), D("4.000"), D("6.000")))  # 6 + 4 wanted, 4 kept from the cancelled-and-redone preparation

    # ---- R14: the platforms ------------------------------------------------------------------------------------------------------

    def test_two_platforms_one_collected_and_one_still_with_its_rider(self):
        from cashboxes.services import get_cashbox_balance
        from reports.services import get_customer_balance
        from shipping.models import ShipmentStatus

        found = {one.name: one for one in platforms.platforms()}
        self.assertEqual({name: one.commission for name, one in found.items()}, {"طلبات": D("18.00"), "elmenus": D("15.00")})
        talabat, elmenus = found["طلبات"], found["elmenus"]
        order = Order.objects.get(customer=talabat.customer)
        self.assertEqual((platforms.number_of(order), order.invoice.total_amount, order.invoice.paid_now), ("TB-48210", D("660.00"), D("0.00")))  # on its list: +10%
        self.assertEqual([(line.quantity, line.unit_price) for line in order.lines.order_by("pk")], [(D("2.000"), D("198.00")), (D("2.000"), D("132.00"))])
        shipment = platforms.shipment(order)
        self.assertEqual((shipment.status, shipment.fee, shipment.tracking_number), (ShipmentStatus.DELIVERED, D("118.80"), "TB-48210"))
        self.assertEqual((get_cashbox_balance(talabat.courier.cashbox), get_customer_balance(talabat.customer)), (D("660.00"), D("0.00")))
        other = Order.objects.get(customer=elmenus.customer)
        self.assertEqual((platforms.number_of(other), other.invoice.total_amount), ("EM-77305", D("600.00")))
        self.assertEqual(platforms.shipment(other).status, ShipmentStatus.WITH_COURIER)
        self.assertEqual((get_cashbox_balance(elmenus.courier.cashbox), get_customer_balance(elmenus.customer)), (D("0.00"), D("600.00")))

    # ---- R11: brands -------------------------------------------------------------------------------------------------------------

    def test_two_brands_from_one_kitchen_and_the_orders_after_them_carry_a_brand(self):
        food, drinks = Brand.objects.order_by("sort")
        self.assertEqual((food.name_ar, food.is_default, [c.name_ar for c in food.categories.all()]), ("بيت الواحة", True, []))  # the whole menu
        self.assertEqual((drinks.name_ar, drinks.is_default, [c.name_ar for c in drinks.categories.all()]), ("واحة جوس", False, ["المشروبات"]))
        takeaway = Order.objects.get(kind="takeaway", status="open")
        self.assertEqual((takeaway.brand, takeaway.total), (drinks, D("30.00")))
        self.assertEqual(Order.objects.get(notes=demo.BIRTHDAY_NOTE).brand, food)
        # a table opened after the build keeps the whole menu on its screen: its drinks and the combo are there to order
        from . import services

        self.client.force_login(self.owner)
        fresh = services.open_order(self.owner, table=table("6"), guests=2)
        self.assertEqual(fresh.brand, food)
        page = self.client.get(reverse("restaurant:order", args=[fresh.pk]), {"lang": "ar"}).content.decode()
        self.assertIn("مياه معدنية صغيرة", page)
        self.assertIn("وجبة التوفير", page)
        self.assertEqual(Order.objects.filter(brand__isnull=True, status="paid").count(), 14)  # every bill made before the brands carries none: the sample's nine and this close-out's five

    # ---- R3: the service charge, the minimum and the check -----------------------------------------------------------------------

    def test_the_check_of_table_three_carries_the_minimum_the_service_and_the_coupon(self):
        order = Order.objects.get(notes=demo.BIRTHDAY_NOTE)
        config = charges.settings()
        self.assertEqual((config.percent, config.kinds, config.minimum), (D("12.00"), ("dine_in",), D("40.00")))
        bill = demo._bill(order, demo.COUPON[0])
        self.assertEqual([(row["kind"], row["net"]) for row in bill["rows"]],
                         [("food", D("102.00")), ("food", D("12.75")), ("min", D("25.00")), ("service", D("16.77"))])
        self.assertEqual((bill["offer"], bill["topup"], bill["service"], bill["total"]), (D("20.25"), D("25.00"), D("16.77"), D("156.52")))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:check", args=[order.pk]), {"lang": "ar", "coupon": demo.COUPON[0]})
        self.assertContains(page, "فرق الحد الأدنى")
        self.assertContains(page, "بيت الواحة")

    def test_only_the_check_table_is_billed_with_the_service_charge(self):
        invoices = Order.objects.filter(status="paid").select_related("invoice")
        self.assertFalse(any(order.invoice.lines.filter(item__item_code__in=charges.CHARGE_CODES).exists() for order in invoices))

    # ---- «إلغاء التحضير» ---------------------------------------------------------------------------------------------------------

    def test_a_preparation_was_cancelled_whole_and_four_plates_were_kept(self):
        from manufacturing.models import RunStatus

        runs = kitchen.preparations(self.roast)[:2][::-1]  # the newest two, the oldest first
        self.assertEqual([(run.status, run.batches) for run in runs], [(RunStatus.CANCELLED, D("6.000")), (RunStatus.POSTED, D("4.000"))])
        self.assertTrue(kitchen.why_not(runs[1]) == "")  # the four are kept, and nothing has left them yet: they can still be cancelled

    # ---- the books ---------------------------------------------------------------------------------------------------------------

    def test_the_books_balance_and_the_planted_alerts_are_unchanged(self):
        books_balance(self)

    def test_the_demo_is_safe_to_run_twice(self):
        from reports.management.commands.seed_demo_business import Command as Seed

        from config.demo_templates import seed_sample_business

        before = (Order.objects.count(), TableReservation.objects.count(), PreOrder.objects.count(), Brand.objects.count(), TableRequest.objects.count())
        master = Seed()._seed_master_data()
        demo.seed_features(master, master["catalog"], [item for item, product in zip(master["items"], master["catalog"].items) if not product.stock][:3], self.owner)
        self.assertEqual(before, (Order.objects.count(), TableReservation.objects.count(), PreOrder.objects.count(), Brand.objects.count(), TableRequest.objects.count()))
        self.assertFalse(seed_sample_business())  # a shop with posted sales is left alone

    # ---- the tour ----------------------------------------------------------------------------------------------------------------

    def card(self, lang):
        from django.test import RequestFactory

        from settings_core.models import ClientProfile

        request = RequestFactory().get("/dashboard/")
        request.user = self.owner
        return first_run.tour_card(request, lang, ClientProfile.get_active())

    @override_settings(DEMO_MODE=True)
    def test_the_card_has_twelve_steps_each_opening_the_demos_own_record(self):
        card = self.card("ar")
        urls = [step["url"] for step in card["steps"]]
        order = Order.objects.get(table=table("1"), status="open")
        split = Order.objects.get(table=table("5"), status="open")
        check = Order.objects.get(notes=demo.BIRTHDAY_NOTE)
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        self.assertEqual(len(urls), 12)
        self.assertEqual(urls[0], "/restaurant/?lang=ar")
        self.assertEqual(urls[1], f"/restaurant/orders/{order.pk}/?lang=ar")
        self.assertEqual(urls[2], f"/restaurant/orders/{split.pk}/?lang=ar")
        self.assertEqual(urls[3], f"/restaurant/orders/{check.pk}/check/?coupon={demo.COUPON[0]}&lang=ar")
        self.assertEqual(urls[4], f"/t/{table('6').qr_key}/?lang=ar")
        self.assertEqual(urls[5], f"/restaurant/reservations/?day={self.today.isoformat()}&lang=ar")
        self.assertEqual(urls[6], f"/restaurant/preorders/?day={tomorrow}&make={tomorrow}&lang=ar")
        self.assertEqual(urls[7], "/restaurant/call/?phone=01000000101&lang=ar")
        self.assertEqual(urls[8], "/restaurant/platforms/?lang=ar")
        self.assertEqual(urls[9], "/restaurant/brands/?lang=ar")
        self.assertEqual(urls[10], f"/restaurant/costs/{self.roast.pk}/?lang=ar")
        self.assertEqual(urls[11], "/restaurant/report/?lang=ar")
        self.assertEqual(len(set(urls)), 12)

    def test_every_step_opens_for_the_owner_in_arabic_and_in_english(self):
        self.client.force_login(self.owner)
        steps = showcase.demo_tour("restaurants", "restaurant")
        self.assertEqual(len(steps), 12)
        for _ar, _en, _hint_ar, _hint_en, url_name in steps:
            path = first_run.step_path(url_name)
            self.assertIsNotNone(path, url_name)
            for lang in ("ar", "en"):
                with self.subTest(step=url_name, lang=lang):
                    self.assertEqual(self.client.get(first_run.step_url(path, lang)).status_code, 200)

    @override_settings(DEMO_MODE=True)
    def test_the_dashboard_shows_the_twelve_steps(self):
        self.client.force_login(self.owner)
        page = self.client.get("/dashboard/?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.content.decode().count("data-demo-tour-step="), 12)
        self.assertEqual(self.client.get("/dashboard/?lang=en").content.decode().count("data-demo-tour-step="), 12)

    def test_no_hint_states_a_day_count_or_a_date_and_every_step_has_both_languages(self):
        for ar, en, hint_ar, hint_en, url_name in showcase.DEMO_TOURS["restaurants"]:
            for text in (hint_ar, hint_en):
                self.assertFalse(any(ch.isdigit() for ch in text), text)  # a built template ages
            self.assertTrue(ar and en and hint_ar and hint_en)
        self.assertEqual(showcase.DEMO_TOURS["restaurants"][0][4], "restaurant:board")

    def test_the_tour_and_the_demo_say_it_plainly(self):
        from reports.tests_demo_names import SLANG, SLANG_FRAGMENTS, UNWANTED_WORDS

        from promotions.models import Promotion
        from staff.models import Employee

        texts = [step[index] for step in showcase.DEMO_TOURS["restaurants"] for index in (0, 2)]
        texts += [value for brand in Brand.objects.all() for value in (brand.name_ar, brand.header)]
        texts += [value for zone in DeliveryZone.objects.all() for value in (zone.name_ar, zone.name_en)]
        texts += [value for order in Order.objects.all() for value in (order.notes, order.address)]
        texts += [value for row in TableReservation.objects.all() for value in (row.name, row.notes)]
        texts += [value for row in PreOrder.objects.all() for value in (row.name, row.notes)]
        texts += [row.name for row in Promotion.objects.all()] + [name for name, *_rest in demo.PLATFORMS]
        texts += [demo.CANCELLED_PREPARATION]
        self.assertGreater(len([text for text in texts if text]), 40)
        for text in texts:
            for word in BANNED + UNWANTED_WORDS:
                self.assertNotIn(word, text)
            for pattern, word in zip(SLANG_FRAGMENTS, SLANG):
                self.assertIsNone(pattern.search(text), f"{word}: {text}")


class AnEmptiedDemo(TestCase):
    """A demo with no sample (the visitor pressed «ابدأ فاضي»): the steps that belong to a record are dropped, the others still open."""

    def test_no_record_step_has_a_path(self):
        for name in ("order_combo", "order_split", "check", "table_menu", "reservations", "preorders", "call", "platforms", "brands"):
            self.assertIsNone(demo.tour_path(f"restaurant:{name}"), name)
        self.assertEqual(demo.tour_path("restaurant:dish"), reverse("restaurant:costing"))  # the costing list still shows what a dish costs

    def test_a_pickup_of_the_tour_says_nothing_about_other_businesses(self):
        self.assertEqual(len(showcase.demo_tour("commercial", "retail")), 3)
        self.assertIsNone(demo.tour_path("restaurant:unknown"))


class OtherRestaurantBusinesses(TestCase):
    """The same build for the other kinds of restaurant: a visitor who restarts and picks one of them presses «املاها ببيانات تجريبية»."""

    def check(self, sub, tables):
        build(sub)
        self.assertEqual(DiningTable.objects.count(), tables)
        books_balance(self)
        owner = get_user_model().objects.get(username="owner")
        self.client.force_login(owner)
        shown = 0
        for _ar, _en, _hint_ar, _hint_en, url_name in showcase.demo_tour("restaurants", sub):
            path = first_run.step_path(url_name)
            if path is None:
                continue
            shown += 1
            for lang in ("ar", "en"):
                self.assertEqual(self.client.get(first_run.step_url(path, lang)).status_code, 200, (url_name, lang))
        return shown

    def test_a_cafe_gets_the_same_demo(self):
        self.assertEqual(self.check("cafe", 6), 12)
        from . import services

        fresh = services.open_order(get_user_model().objects.get(username="owner"), table=table("6"), guests=2)  # a table opened after the build
        page = self.client.get(reverse("restaurant:order", args=[fresh.pk]), {"lang": "ar"}).content.decode()
        self.assertIn("قهوة تركي", page)  # the café's coffee is in the bar's category: the default brand must not hide it
        self.assertIn("وجبة التوفير", page)
        self.assertEqual(TableReservation.objects.count(), 4)
        self.assertEqual(Brand.objects.count(), 2)

    def test_a_fast_food_restaurant_gets_the_same_demo(self):
        self.assertEqual(self.check("fast_food", 6), 12)
        self.assertEqual(PreOrder.objects.count(), 2)

    def test_a_bakery_has_no_tables_so_only_the_steps_that_need_none_are_shown(self):
        shown = self.check("bakery", 0)
        self.assertEqual((shown, Order.objects.count(), TableReservation.objects.count()), (3, 0, 0))  # the board, what a dish costs, and the report

    def test_a_cloud_kitchen_has_no_tables_either(self):
        self.assertEqual(self.check("cloud_kitchen", 0), 3)
