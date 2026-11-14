"""Restaurants R1b (HG-112): combos (meal deals).

«وجبة برجر» at 150.00: a burger (120.00), fries (40.00, or large fries at 50.00 for 10.00 more) and a Pepsi (25.00, a
stocked can). The combo's own item never goes on the bill: its components do, each at its menu price, with the saving
(185.00 − 150.00 = 35.00) spread over them as line discounts in proportion to their prices, the piastres left over by
rounding going to the largest remainders: 22.70, 7.57 and 4.73. VAT (14%) is worked out per line on what each line
charges. Each combo taken is its own bundle of quantity-1 lines, so two combos are exactly twice one.

The clock stands still on a Wednesday at noon in Cairo, as in ``tests_modifiers``.
"""

import re
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_item, stock_in
from inventory.models import StockMovement
from permissions.models import RoleCode
from sales.models import SalesInvoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from . import combos, modifiers, services, stations
from .insights import period_report
from .models import Combo, ComboChoice, ComboSlot, KitchenTicket, Order, OrderLine, OrderStatus
from .tests import RestaurantSetup, person
from .tests_branches import BranchSetup

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)  # a Wednesday, 12:00 in Cairo


def meal(burger, fries, large, pepsi, price="150.00"):
    return {"name_ar": "وجبة برجر", "name_en": "Burger meal", "price": price, "active": True,
            "slots": [{"name_ar": "الساندويتش", "name_en": "Sandwich", "choices": [{"item": burger}]},
                      {"name_ar": "البطاطس", "name_en": "Fries", "choices": [{"item": fries}, {"item": large, "upcharge": "10.00"}]},
                      {"name_ar": "المشروب", "name_en": "Drink", "choices": [{"item": pepsi}]}]}


class ComboSetup(RestaurantSetup):
    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.burger = make_item(item_code="C-BURG", item_name="برجر", default_sale_price="120.00", is_stock_tracked=False)
        self.fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        self.large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        self.pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=True)
        stock_in(self.pepsi, self.location, 20, "8.00")
        self.combo = combos.save_combo(meal(self.burger, self.fries, self.large, self.pepsi), self.owner)
        self.sandwich, self.side, self.drink = self.combo.slots.order_by("sort", "pk")
        self.c_burger = self.sandwich.choices.get()
        self.c_fries, self.c_large = self.side.choices.order_by("sort", "pk")
        self.c_pepsi = self.drink.choices.get()
        self.waiter = person(RoleCode.WAITER, "combo_waiter")

    def vat(self, on):
        set_capability_enabled(ClientProfile.get_active(), "vat", on, user=self.owner)

    def picks(self, large=False):
        return {self.sandwich.pk: self.c_burger.pk, self.side.pk: (self.c_large if large else self.c_fries).pk, self.drink.pk: self.c_pepsi.pk}

    def take(self, order=None, large=False, quantity=1, options=None, table=None):
        order = order or services.open_order(self.owner, table=table or self.t1, guests=2)
        services.add_combo(order, self.combo, self.owner, self.picks(large), quantity=quantity, options=options)
        return order

    def rows(self, order):
        return [(line.item.item_code, line.quantity, line.unit_price, line.discount, line.line_discount, line.amount, line.combo_id, line.bundle)
                for line in Order.objects.get(pk=order.pk).lines.select_related("item").order_by("pk")]

    def pay(self, order, **extra):
        return services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, tendered=D("10000"), **extra)[0]

    def invoice_rows(self, invoice):
        return [(line.item.item_code, line.quantity, line.unit_sale_price, line.line_discount_amount, line.line_total_amount)
                for line in invoice.lines.select_related("item").order_by("line_number")]

    def line_taxes(self, invoice):
        from taxes.models import SalesLineTax

        return [tax.tax_amount for tax in SalesLineTax.objects.filter(line__invoice=invoice).order_by("line__line_number")]

    def state(self):
        return {"lines": list(OrderLine.objects.order_by("pk").values_list("pk", "order_id", "item_id", "quantity", "unit_price", "discount", "combo_id", "bundle",
                                                                           "parent_id", "ticket_id", "voided", "note")),
                "orders": list(Order.objects.order_by("pk").values_list("pk", "status", "table_id", "split_from_id", "merged_into_id")),
                "tickets": KitchenTicket.objects.count(), "invoices": SalesInvoice.objects.count(), "cash": CashboxMovement.objects.count(),
                "stock": StockMovement.objects.count(), "combos": list(Combo.objects.values_list("pk", "active", "item__default_sale_price")),
                "slots": ComboSlot.objects.count(), "choices": list(ComboChoice.objects.values_list("pk", "item_id", "upcharge", "active"))}

    def assert_refused(self, call, text, error=ValidationError):
        before = self.state()
        with self.assertRaisesMessage(error, text):
            call()
        self.assertEqual(self.state(), before)


class SpreadTests(ComboSetup):
    def test_the_saving_is_spread_by_menu_price_and_the_largest_remainders_take_the_piastres(self):
        self.assertEqual(combos.spread([D("120.00"), D("40.00"), D("25.00")], D("35.00")), [D("22.70"), D("7.57"), D("4.73")])
        self.assertEqual(combos.spread([D("120.00"), D("50.00"), D("25.00")], D("35.00")), [D("21.54"), D("8.97"), D("4.49")])

    def test_the_shares_always_add_up_to_the_saving_and_never_pass_a_price(self):
        for prices, saving in (([D("10.00"), D("10.00"), D("10.00")], D("10.00")), ([D("0.01"), D("99.99")], D("50.00")),
                               ([D("33.33"), D("33.33"), D("33.34")], D("0.02")), ([D("5.00")], D("5.00")), ([D("7.00"), D("3.00")], D("0.00"))):
            shares = combos.spread(prices, saving)
            self.assertEqual(sum(shares, D("0")), saving)
            self.assertTrue(all(D("0") <= share <= price for share, price in zip(shares, prices)))


class TakingTests(ComboSetup):
    def test_a_combo_is_its_components_at_menu_price_with_the_saving_as_discounts(self):
        order = self.take()
        self.assertEqual(self.rows(order), [
            ("C-BURG", D("1.000"), D("120.00"), D("22.70"), D("22.70"), D("97.30"), self.combo.pk, 1),
            ("C-FRIES", D("1.000"), D("40.00"), D("7.57"), D("7.57"), D("32.43"), self.combo.pk, 1),
            ("C-PEPSI", D("1.000"), D("25.00"), D("4.73"), D("4.73"), D("20.27"), self.combo.pk, 1),
        ])
        self.assertEqual(Order.objects.get(pk=order.pk).total, D("150.00"))
        self.assertFalse(OrderLine.objects.filter(item=self.combo.item).exists())  # the combo's own item is never a line

    def test_vat_on_the_bill_is_21_00_per_line_and_171_00_and_the_invoice_is_exact(self):
        self.vat(True)
        order = self.take()
        self.assertEqual(services.bill_total(order, list(order.lines.select_related("item"))), D("171.00"))  # R2b's «الباقي» sees the discounts
        invoice = self.pay(order)
        self.assertEqual(self.invoice_rows(invoice), [
            ("C-BURG", D("1.000"), D("120.00"), D("22.70"), D("97.30")),
            ("C-FRIES", D("1.000"), D("40.00"), D("7.57"), D("32.43")),
            ("C-PEPSI", D("1.000"), D("25.00"), D("4.73"), D("20.27")),
        ])
        self.assertEqual(self.line_taxes(invoice), [D("13.62"), D("4.54"), D("2.84")])
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.discount_amount), (D("150.00"), D("21.00"), D("171.00"), D("0.00")))
        self.assertEqual(invoice.lines.get(item=self.pepsi).quantity, D("1.000"))
        self.assertEqual(StockMovement.objects.filter(item=self.pepsi, sales_invoice=invoice).count(), 1)  # the can leaves stock like any can

    def test_vat_off_the_bill_is_150_00(self):
        self.vat(False)
        invoice = self.pay(self.take())
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("0.00"), D("150.00")))

    def test_large_fries_charge_160_00_and_vat_is_22_39_because_it_is_rounded_per_line(self):
        self.vat(True)
        order = self.take(large=True)
        self.assertEqual([(code, discount, amount) for code, _q, _p, discount, _l, amount, _c, _b in self.rows(order)],
                         [("C-BURG", D("21.54"), D("98.46")), ("C-FRIES-L", D("8.97"), D("41.03")), ("C-PEPSI", D("4.49"), D("20.51"))])
        self.assertEqual(Order.objects.get(pk=order.pk).total, D("160.00"))
        invoice = self.pay(order)
        self.assertEqual(self.line_taxes(invoice), [D("13.78"), D("5.74"), D("2.87")])
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("22.39"), D("182.39")))

    def test_large_fries_vat_off_is_160_00(self):
        self.vat(False)
        self.assertEqual(self.pay(self.take(large=True)).total_amount, D("160.00"))

    def test_two_combos_are_two_bundles_and_exactly_twice_one(self):
        self.vat(True)
        order = self.take(quantity=2)
        rows = self.rows(order)
        self.assertEqual([(code, quantity, discount, bundle) for code, quantity, _p, discount, _l, _a, _c, bundle in rows], [
            ("C-BURG", D("1.000"), D("22.70"), 1), ("C-FRIES", D("1.000"), D("7.57"), 1), ("C-PEPSI", D("1.000"), D("4.73"), 1),
            ("C-BURG", D("1.000"), D("22.70"), 2), ("C-FRIES", D("1.000"), D("7.57"), 2), ("C-PEPSI", D("1.000"), D("4.73"), 2),
        ])
        invoice = self.pay(order)
        self.assertEqual(sum((line.line_discount_amount for line in invoice.lines.all()), D("0")), D("70.00"))
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount), (D("300.00"), D("42.00"), D("342.00")))

    def test_two_combos_with_large_fries_are_364_78_twice_182_39(self):
        # Kept as two bundles of quantity 1 on purpose: one line of quantity 2 would round VAT on 196.92, 82.06 and 41.02
        # (27.57 + 11.49 + 5.74 = 44.80) and charge a piastre more than two combos rung up one by one (2 × 22.39 = 44.78).
        self.vat(True)
        invoice = self.pay(self.take(large=True, quantity=2))
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount), (D("320.00"), D("44.78"), D("364.78")))
        self.vat(False)
        self.assertEqual(self.pay(self.take(large=True, quantity=2, table=self.t2)).total_amount, D("320.00"))

    def test_a_combo_is_taken_by_whole_count(self):
        order = services.open_order(self.owner, table=self.t1)
        for bad in ("1.5", "0", "-1", "abc", "1000"):
            self.assert_refused(lambda: services.add_combo(order, self.combo, self.owner, self.picks(), quantity=bad), "")

    def test_the_combos_own_item_is_never_added_as_a_dish(self):
        order = services.open_order(self.owner, table=self.t1)
        self.assert_refused(lambda: services.add_item(order, self.combo.item, self.owner), "اختار من كل خانة")

    def test_a_plain_burger_never_merges_into_a_combo_component(self):
        order = self.take()
        services.add_item(order, self.burger, self.owner)
        self.assertEqual(order.lines.filter(item=self.burger).count(), 2)
        alone = order.lines.get(item=self.burger, bundle__isnull=True)
        self.assertEqual((alone.discount, alone.combo_id, alone.amount), (D("0.00"), None, D("120.00")))
        self.assertEqual(Order.objects.get(pk=order.pk).total, D("270.00"))


class ReturnTests(ComboSetup):
    def returned_fries(self, vat):
        from sales.services import create_sales_return

        self.vat(vat)
        order = self.take(quantity=2)
        invoice = self.pay(order)
        fries = invoice.lines.filter(item=self.fries).order_by("line_number").first()
        return create_sales_return("SR-COMBO-1", invoice.invoice_date, invoice.pk, [{"source_line": fries, "quantity": D("1")}], "البطاطس باردة", self.owner)

    def test_returning_the_fries_of_the_first_combo_refunds_36_97_with_vat(self):
        sales_return = self.returned_fries(True)
        self.assertEqual((sales_return.total_amount, sales_return.cash_amount), (D("36.97"), D("36.97")))  # 32.43 + 4.54

    def test_returning_the_fries_vat_off_refunds_32_43(self):
        self.assertEqual(self.returned_fries(False).total_amount, D("32.43"))


class PromotionTests(ComboSetup):
    def test_a_running_10_percent_offer_takes_12_00_off_a_burger_alone_and_nothing_off_combo_lines(self):
        from promotions import services as promotions
        from promotions.models import Promotion, PromotionKind

        set_capability_enabled(ClientProfile.get_active(), "promotions", True, user=self.owner)
        Promotion.objects.create(name="عشرة في المية", kind=PromotionKind.PERCENT, percent=D("10"))
        order = self.take()
        services.add_item(order, self.burger, self.owner)
        lines = list(order.lines.select_related("item"))
        rows = services.bill_rows(order, lines)
        filled, promotion, off = promotions.apply(rows)
        self.assertIsNotNone(promotion)
        self.assertEqual(off, D("12.00"))
        self.assertEqual([(row["item"].item_code, row["line_discount_amount"]) for row in filled],
                         [("C-BURG", D("22.70")), ("C-FRIES", D("7.57")), ("C-PEPSI", D("4.73")), ("C-BURG", D("12.00"))])


class SetUpTests(ComboSetup):
    def test_the_combo_sells_through_a_non_stock_service_item_at_its_price(self):
        item = self.combo.item
        self.assertEqual((item.item_name, item.default_sale_price, item.is_stock_tracked, item.active), ("وجبة برجر", D("150.00"), False, True))
        self.assertEqual([(slot.name_ar, [(choice.item.item_code, choice.upcharge) for choice in slot.choices.order_by("sort", "pk")])
                          for slot in self.combo.slots.order_by("sort", "pk")],
                         [("الساندويتش", [("C-BURG", D("0.00"))]), ("البطاطس", [("C-FRIES", D("0.00")), ("C-FRIES-L", D("10.00"))]),
                          ("المشروب", [("C-PEPSI", D("0.00"))])])
        self.assertEqual(combos.cheapest_saving(self.combo), D("35.00"))

    def test_a_combo_priced_above_its_cheapest_parts_is_refused(self):
        self.assert_refused(lambda: combos.save_combo(meal(self.burger, self.fries, self.large, self.pepsi, price="185.01"), self.owner), "185.00")
        at_parts = combos.save_combo(dict(meal(self.burger, self.fries, self.large, self.pepsi, price="185.00"), name_ar="وجبة كاملة"), self.owner)
        self.assertEqual(combos.cheapest_saving(at_parts), D("0.00"))
        # changing the price of a saved combo is checked the same way
        self.assert_refused(lambda: combos.save_combo(dict(meal(self.burger, self.fries, self.large, self.pepsi, price="190.00")), self.owner, self.combo), "185.00")

    def test_a_combo_inside_a_combo_and_the_combos_own_item_as_a_choice_are_refused(self):
        data = meal(self.burger, self.fries, self.large, self.pepsi)
        data["name_ar"] = "وجبة عائلية"
        data["slots"][0]["choices"] = [{"item": self.combo.item}]
        self.assert_refused(lambda: combos.save_combo(data, self.owner), "كومبو")

    def test_bad_set_ups_are_refused(self):
        base = lambda **change: dict(meal(self.burger, self.fries, self.large, self.pepsi), name_ar="وجبة تانية", **change)
        cases = [
            (base(name_ar=""), "اسم"), (base(price="0"), "السعر"), (base(price="abc"), "السعر"), (base(name_ar="وجبة برجر"), "نفس الاسم"),
            (base(slots=[]), "خانة"),
            (base(slots=[{"name_ar": "الساندويتش", "choices": []}]), "اختيار"),
            (base(slots=[{"name_ar": "الساندويتش", "choices": [{"item": self.burger, "upcharge": "-1"}]}]), "الزيادة"),
            (base(slots=[{"name_ar": "الساندويتش", "choices": [{"item": self.burger}, {"item": self.burger}]}]), "متكرر"),
            (base(slots=[{"name_ar": "", "choices": [{"item": self.burger}]}]), "اسم الخانة"),
        ]
        for data, text in cases:
            self.assert_refused(lambda: combos.save_combo(data, self.owner), text)

    def test_an_add_on_group_never_offers_a_combos_item(self):
        self.assert_refused(lambda: modifiers.save_group({"name_ar": "زيادات", "min_choices": "0", "max_choices": "1",
                                                          "options": [{"name_ar": "وجبة", "item": self.combo.item}]}, self.owner), "كومبو")

    def test_the_set_up_is_audited(self):
        from audit.models import AuditLog

        log = AuditLog.objects.filter(action="create_combo").get()
        self.assertEqual(log.after_data["price"], "150.00")
        self.assertEqual([slot["choices"] for slot in log.after_data["slots"]][1], [["C-FRIES", "0.00"], ["C-FRIES-L", "10.00"]])


class AddOnTests(ComboSetup):
    def setUp(self):
        super().setUp()
        self.cheese = make_item(item_code="C-CHEESE", item_name="جبنة زيادة", default_sale_price="15.00", is_stock_tracked=False)
        self.extras = modifiers.save_group({"name_ar": "إضافات", "name_en": "Extras", "min_choices": "0", "max_choices": "2",
                                            "options": [{"name_ar": "جبنة زيادة", "item": self.cheese}, {"name_ar": "من غير بصل"}]}, self.owner)
        self.cheese_opt, self.no_onion = self.extras.options.order_by("sort", "pk")
        modifiers.set_dish_groups(self.burger, [self.extras], self.owner)

    def test_a_components_add_on_is_billed_at_its_own_price_and_never_discounted(self):
        self.vat(True)
        order = self.take(options={self.sandwich.pk: [self.cheese_opt.pk, self.no_onion.pk]})
        burger = order.lines.get(item=self.burger)
        cheese = order.lines.get(item=self.cheese)
        self.assertEqual((burger.options, burger.discount), ("من غير بصل", D("22.70")))
        self.assertEqual((cheese.parent_id, cheese.unit_price, cheese.discount, cheese.combo_id, cheese.bundle, cheese.amount), (burger.pk, D("15.00"), D("0.00"), None, None, D("15.00")))
        self.assertEqual(Order.objects.get(pk=order.pk).total, D("165.00"))
        invoice = self.pay(order)
        self.assertEqual(self.invoice_rows(invoice)[:2], [("C-BURG", D("1.000"), D("120.00"), D("22.70"), D("97.30")),
                                                          ("C-CHEESE", D("1.000"), D("15.00"), D("0.00"), D("15.00"))])
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("23.10"), D("188.10")))  # 21.00 + 2.10 on the cheese

    def test_a_required_choice_on_a_component_is_asked_and_an_add_on_of_another_slot_is_refused(self):
        doneness = modifiers.save_group({"name_ar": "درجة الاستواء", "min_choices": "1", "max_choices": "1", "options": [{"name_ar": "ميديم"}, {"name_ar": "ويل دن"}]}, self.owner)
        modifiers.set_dish_groups(self.burger, [doneness, self.extras], self.owner)
        order = services.open_order(self.owner, table=self.t1)
        self.assert_refused(lambda: services.add_combo(order, self.combo, self.owner, self.picks()), "درجة الاستواء")
        self.assert_refused(lambda: services.add_combo(order, self.combo, self.owner, self.picks(), options={self.side.pk: [self.cheese_opt.pk]}), "")
        medium = doneness.options.order_by("sort", "pk").first()
        services.add_combo(order, self.combo, self.owner, self.picks(), options={self.sandwich.pk: [medium.pk]})
        self.assertEqual(order.lines.get(item=self.burger).options, "ميديم")


class FamilyTests(ComboSetup):
    def bundle(self, order, number=1):
        return list(Order.objects.get(pk=order.pk).lines.filter(bundle=number).order_by("pk"))

    def test_removing_the_combo_removes_every_component_and_its_add_ons(self):
        order = self.take()
        head = self.bundle(order)[0]
        services.change_line(head, self.owner, quantity="0")
        self.assertFalse(order.lines.exists())
        order = self.take(order)
        services.void_line(self.bundle(order)[0], self.owner)  # unsent: just goes
        self.assertFalse(order.lines.exists())

    def test_a_component_is_never_changed_removed_or_voided_alone(self):
        order = self.take()
        burger, fries, pepsi = self.bundle(order)
        self.assert_refused(lambda: services.change_line(fries, self.owner, quantity="2"), "الكومبو")
        self.assert_refused(lambda: services.change_line(fries, self.owner, quantity="0"), "الكومبو")
        self.assert_refused(lambda: services.void_line(pepsi, self.owner), "الكومبو")
        self.assert_refused(lambda: services.change_line(burger, self.owner, quantity="2"), "كومبو تاني")  # each combo taken is one
        services.change_line(fries, self.owner, note="من غير ملح")  # a note for the kitchen is the component's own
        self.assertEqual(OrderLine.objects.get(pk=fries.pk).note, "من غير ملح")
        self.assertEqual(Order.objects.get(pk=order.pk).total, D("150.00"))

    def test_the_components_go_to_the_kitchen_together_and_a_void_after_needs_the_right_and_a_reason(self):
        order = self.take()
        ticket = services.send_to_kitchen(order, self.owner)
        self.assertEqual(set(OrderLine.objects.filter(order=order).values_list("ticket_id", flat=True)), {ticket.pk})
        head = self.bundle(order)[0]
        self.assert_refused(lambda: services.void_line(head, self.waiter, reason="غلط"), "", error=PermissionDenied)
        self.assert_refused(lambda: services.void_line(head, self.owner, reason=""), "سبب")
        services.void_line(head, self.owner, reason="العميل غيّر رأيه")
        self.assertEqual(list(order.lines.values_list("voided", "void_reason").distinct()), [(True, "العميل غيّر رأيه")])
        self.assertEqual(Order.objects.get(pk=order.pk).total, D("0.00"))
        from audit.models import AuditLog

        log = AuditLog.objects.filter(action="void_order_line").get()
        self.assertEqual(log.after_data["combo"], "وجبة برجر")
        self.assertEqual([row["item"] for row in log.after_data["components"]], ["C-FRIES", "C-PEPSI"])


class CraftedTests(ComboSetup):
    def test_crafted_choices_are_refused_and_nothing_changes(self):
        order = services.open_order(self.owner, table=self.t1)
        add = lambda picks, combo=None: services.add_combo(order, combo or self.combo, self.owner, picks)
        wrong_slot = dict(self.picks(), **{self.side.pk: self.c_pepsi.pk})
        self.assert_refused(lambda: add(wrong_slot), "مش من اختيارات")
        missing = {self.sandwich.pk: self.c_burger.pk, self.side.pk: self.c_fries.pk}
        self.assert_refused(lambda: add(missing), "المشروب")
        self.assert_refused(lambda: add(dict(self.picks(), **{self.side.pk: "x"})), "مش من اختيارات")
        self.assert_refused(lambda: add(dict(self.picks(), **{999999: self.c_fries.pk})), "مش من اختيارات")
        ComboChoice.objects.filter(pk=self.c_large.pk).update(active=False)
        self.assert_refused(lambda: add(self.picks(large=True)), "مش متاح")
        ComboChoice.objects.filter(pk=self.c_large.pk).update(active=True)
        self.large.active = False
        self.large.save(update_fields=["active"])
        self.assert_refused(lambda: add(self.picks(large=True)), "مش متاح")
        Combo.objects.filter(pk=self.combo.pk).update(active=False)
        self.assert_refused(lambda: add(self.picks()), "مش متاح")

    def test_a_combo_whose_parts_got_cheaper_than_its_price_is_refused_when_taken(self):
        self.fries.default_sale_price = D("10.00")
        self.fries.save(update_fields=["default_sale_price"])
        order = services.open_order(self.owner, table=self.t1)
        self.assert_refused(lambda: services.add_combo(order, self.combo, self.owner, self.picks()), "أغلى")

    def test_crafted_requests_through_the_order_page_are_refused(self):
        self.client.force_login(self.owner)
        order = services.open_order(self.owner, table=self.t1)
        url = reverse("restaurant:order", args=[order.pk])
        before = self.state()
        self.client.post(url, {"action": "combo", "combo": self.combo.pk, f"slot-{self.sandwich.pk}": self.c_burger.pk, f"slot-{self.side.pk}": self.c_pepsi.pk,
                               f"slot-{self.drink.pk}": self.c_pepsi.pk})
        self.client.post(url, {"action": "add", "item": self.combo.item.pk})
        self.assertEqual(self.state(), before)


class SplitMergeTests(ComboSetup):
    def setUp(self):
        super().setUp()
        self.vat(True)

    def combo_and_koshary(self, table=None):
        order = self.take(table=table)
        services.add_item(order, self.koshary, self.owner)
        services.send_to_kitchen(order, self.owner)
        return Order.objects.get(pk=order.pk)

    def snapshot(self, order):
        order = Order.objects.get(pk=order.pk)
        return {"total": order.total, "status": order.status,
                "lines": sorted(order.lines.values_list("pk", "item_id", "quantity", "unit_price", "discount", "combo_id", "bundle", "parent_id", "ticket_id", "voided"))}

    def test_splitting_one_component_alone_is_refused(self):
        order = self.combo_and_koshary()
        fries = order.lines.get(item=self.fries)
        self.assert_refused(lambda: services.split_order(order, {fries.pk: 1}, self.owner), "الكومبو")

    def test_splitting_the_whole_bundle_moves_every_component_and_the_two_bills_total_the_unsplit_figure(self):
        unsplit = self.pay(self.combo_and_koshary(table=self.t2))
        self.assertEqual(unsplit.total_amount, D("239.40"))  # 171.00 + 68.40
        order = self.combo_and_koshary()
        head = order.lines.filter(bundle=1).order_by("pk").first()
        part = services.split_order(order, {head.pk: 1}, self.owner)
        self.assertEqual(sorted(part.lines.values_list("item__item_code", "discount", "bundle")),
                         [("C-BURG", D("22.70"), 1), ("C-FRIES", D("7.57"), 1), ("C-PEPSI", D("4.73"), 1)])
        self.assertEqual((Order.objects.get(pk=part.pk).total, Order.objects.get(pk=order.pk).total), (D("150.00"), D("60.00")))
        first, second = self.pay(part), self.pay(order)
        self.assertEqual((first.total_amount, second.total_amount, first.total_amount + second.total_amount), (D("171.00"), D("68.40"), D("239.40")))

    def test_the_order_page_offers_a_combo_as_one_row_of_the_part_sheet(self):
        order = self.combo_and_koshary()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:order", args=[order.pk])).content.decode()
        head = order.lines.filter(bundle=1).order_by("pk").first()
        self.assertEqual(len(re.findall(r'name="part_(\d+)"', page)), 2)  # the combo and the koshary
        self.assertIn(f'name="part_{head.pk}"', page)
        self.assertIn('data-each="150.00"', page)

    def test_a_combo_alone_is_the_whole_bill_and_is_not_split(self):
        order = self.take()
        head = order.lines.filter(bundle=1).order_by("pk").first()
        self.assert_refused(lambda: services.split_order(order, {head.pk: 1}, self.owner), "الحساب كله")

    def test_put_back_restores_the_bill_exactly(self):
        order = self.combo_and_koshary()
        before = self.snapshot(order)
        head = order.lines.filter(bundle=1).order_by("pk").first()
        part = services.split_order(order, {head.pk: 1}, self.owner)
        services.add_combo(Order.objects.get(pk=order.pk), self.combo, self.owner, self.picks(large=True))  # takes bundle 1 again meanwhile
        services.put_back(part, self.owner)
        after = Order.objects.get(pk=order.pk)
        self.assertEqual(sorted(after.lines.values_list("bundle", flat=True).distinct()), [None, 1, 2])
        returned = sorted(after.lines.filter(pk__in=[line[0] for line in before["lines"]]).values_list("pk", "item_id", "quantity", "unit_price", "discount",
                                                                                                       "combo_id", "parent_id", "ticket_id", "voided"))
        self.assertEqual(returned, sorted((pk, item, qty, price, discount, combo, parent, ticket, voided)
                                          for pk, item, qty, price, discount, combo, _bundle, parent, ticket, voided in before["lines"]))
        self.assertEqual(after.total, D("370.00"))  # 150.00 + 60.00 + the large-fries combo taken meanwhile (160.00)
        self.assertEqual(len({line.bundle for line in after.lines.filter(combo__isnull=False)}), 2)

    def test_put_back_with_no_collision_is_the_same_bill(self):
        order = self.combo_and_koshary()
        before = self.snapshot(order)
        head = order.lines.filter(bundle=1).order_by("pk").first()
        services.put_back(services.split_order(order, {head.pk: 1}, self.owner), self.owner)
        self.assertEqual(self.snapshot(order), before)

    def test_merging_two_tables_each_with_bundle_1_keeps_two_bundles(self):
        first, second = self.take(), self.take(table=self.t2, large=True)
        merged = services.merge_orders(first, second, self.owner)
        bundles = {}
        for line in merged.lines.order_by("pk"):
            bundles.setdefault(line.bundle, []).append(line.item.item_code)
        self.assertEqual(bundles, {1: ["C-BURG", "C-FRIES", "C-PEPSI"], 2: ["C-BURG", "C-FRIES-L", "C-PEPSI"]})
        self.assertEqual(Order.objects.get(pk=merged.pk).total, D("310.00"))
        self.assertEqual(self.pay(merged).total_amount, D("353.39"))  # 171.00 + 182.39


class ReportAndKitchenTests(ComboSetup):
    def test_dishes_stay_at_menu_price_and_the_combo_discount_reconciles_with_the_invoices(self):
        self.vat(False)
        order = self.take()
        services.add_item(order, self.burger, self.owner)
        invoice = self.pay(order)
        report = period_report(NOW.date(), NOW.date())
        self.assertEqual({row["item"].item_code: (row["quantity"], row["sales"]) for row in report["dishes"]},
                         {"C-BURG": (D("2.000"), D("240.00")), "C-FRIES": (D("1.000"), D("40.00")), "C-PEPSI": (D("1.000"), D("25.00"))})
        self.assertEqual(report["totals"]["combo_discount"], D("35.00"))
        self.assertEqual(report["totals"]["sales"], invoice.total_amount)  # 270.00
        self.assertEqual(sum((row["sales"] for row in report["dishes"]), D("0")) - report["totals"]["combo_discount"], report["totals"]["sales"])
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:report")).content.decode()
        self.assertIn("data-combo-discount", page)
        self.assertIn("خصم الكومبو", page)

    def test_a_report_without_combos_shows_no_combo_discount(self):
        order = services.open_order(self.owner, table=self.t1)
        services.add_item(order, self.burger, self.owner)
        self.pay(order)
        self.assertEqual(period_report(NOW.date(), NOW.date())["totals"]["combo_discount"], D("0.00"))
        self.client.force_login(self.owner)
        self.assertNotIn("data-combo-discount", self.client.get(reverse("restaurant:report")).content.decode())

    def test_the_kitchen_ticket_shows_the_combo_name_over_its_components(self):
        order = self.take()
        services.add_item(order, self.koshary, self.owner)
        ticket = services.send_to_kitchen(order, self.owner)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:ticket", args=[ticket.pk])).content.decode()
        combo_at = page.index('data-ticket-combo="1"')
        self.assertIn("وجبة برجر", page[combo_at:combo_at + 200])
        self.assertTrue(combo_at < page.index('data-ticket-line="C-BURG"') < page.index('data-ticket-line="C-PEPSI"'))
        self.assertEqual(page.count("data-ticket-combo="), 1)

    def test_the_kitchen_screen_carries_the_combo_name_on_its_components_only(self):
        order = self.take()
        services.add_item(order, self.koshary, self.owner)
        ticket = services.send_to_kitchen(order, self.owner)
        card = stations.ticket_card(KitchenTicket.objects.get(pk=ticket.pk), NOW, "صالة", "طاولة")
        self.assertEqual({line["name"]: line.get("combo") for line in card["lines"]}, {"برجر": "وجبة برجر", "بطاطس": "وجبة برجر", "بيبسي": "وجبة برجر", "كشري كبير": None})
        self.assertNotIn("combo", next(line for line in card["lines"] if line["name"] == "كشري كبير"))


class ScreenTests(ComboSetup):
    def test_the_set_up_page_lists_combos_with_their_saving_and_creates_one(self):
        self.client.force_login(self.owner)
        url = reverse("restaurant:combos")
        page = self.client.get(url).content.decode()
        self.assertIn('data-combo="وجبة برجر"', page)
        self.assertIn('data-combo-saving="35.00"', page)
        self.assertIn("بطاطس كبيرة", page)
        response = self.client.post(url, {"name_ar": "وجبة كشري", "name_en": "Koshary meal", "price": "70.00", "active": "on", "sort": "2",
                                          "slot-0-name_ar": "الطبق", "slot-0-choice-0-item": self.koshary.pk,
                                          "slot-1-name_ar": "المشروب", "slot-1-choice-0-item": self.tea.pk, "slot-1-choice-1-item": self.pepsi.pk,
                                          "slot-1-choice-1-upcharge": "10"})
        self.assertEqual(response.status_code, 302)
        made = Combo.objects.get(item__item_name="وجبة كشري")
        self.assertEqual((made.price, made.name_en, made.slots.count(), made.slots.get(name_ar="المشروب").choices.get(item=self.pepsi).upcharge),
                         (D("70.00"), "Koshary meal", 2, D("10.00")))
        english = self.client.get(url + "?lang=en").content.decode()
        self.assertIn("Combos", english)

    def test_only_who_sets_up_the_menu_may_open_the_set_up(self):
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(reverse("restaurant:combos")).status_code, 403)
        self.assertEqual(self.client.post(reverse("restaurant:combos"), {"name_ar": "x", "price": "1"}).status_code, 403)

    def test_the_order_page_opens_the_combo_sheet_and_shows_the_combo_as_one_block(self):
        self.client.force_login(self.owner)
        order = services.open_order(self.owner, table=self.t1)
        url = reverse("restaurant:order", args=[order.pk])
        page = self.client.get(url).content.decode()
        self.assertIn(f'data-opens-sheet="combo-{self.combo.pk}"', page)
        self.assertIn(f'data-combo-sheet="{self.combo.pk}"', page)
        self.assertIn(f'name="slot-{self.side.pk}" value="{self.c_large.pk}"', page)
        self.assertIn("+10.00", page)
        response = self.client.post(url, {"action": "combo", "combo": self.combo.pk, "quantity": "1", **{f"slot-{slot}": choice for slot, choice in self.picks().items()}})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.get(pk=order.pk).total, D("150.00"))
        page = self.client.get(url).content.decode()
        self.assertIn('data-combo-block="1"', page)
        self.assertIn('data-combo-saving="35.00"', page)
        self.assertEqual(page.count("data-combo-component="), 3)
        self.assertIn("?v=resto-r1b", page)
        english = self.client.get(url + "?lang=en").content.decode()
        self.assertIn("Burger meal", english)

    def test_the_waiter_takes_a_combo(self):
        self.client.force_login(self.waiter)
        order = services.open_order(self.owner, table=self.t1)
        self.client.post(reverse("restaurant:order", args=[order.pk]), {"action": "combo", "combo": self.combo.pk, **{f"slot-{s}": c for s, c in self.picks().items()}})
        self.assertEqual(order.lines.count(), 3)


class BranchComboTests(BranchSetup):
    def test_a_branchs_combo_posts_to_its_branch(self):
        burger = make_item(item_code="C-BURG", item_name="برجر", default_sale_price="120.00", is_stock_tracked=False)
        fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=False)
        combo = combos.save_combo(meal(burger, fries, large, pepsi), self.owner)
        table = services.save_table({"name": "B1", "seats": 2}, self.owner, entity=self.branch)
        order = services.open_order(self.owner, table=table)
        picks = {slot.pk: slot.choices.order_by("sort", "pk").first().pk for slot in combo.slots.all()}
        services.add_combo(order, combo, self.owner, picks)
        invoice, _change = services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.b_cashbox, tendered=D("150"))
        self.assertEqual((invoice.selling_location, invoice.cashbox, invoice.total_amount), (self.b_location, self.b_cashbox, D("150.00")))
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.PAID)
