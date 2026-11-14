"""Restaurants R1a (HG-109): add-ons and required choices on a dish.

A burger at 120.00 asks «درجة الاستواء» (one free choice, required: «ميديم» or «ويل دن») and offers «إضافات» (up to two,
optional): «جبنة زيادة», a priced add-on that is the item «جبنة زيادة» at 15.00, and «من غير بصل», a free choice. A
priced add-on is its own order line under its dish (the dish's quantity, the item's price at that moment); a free choice
costs nothing and adds no line. The clock stands still on a Wednesday at noon in Cairo, as in ``tests_branches``.
"""

import re
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock

from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from cashboxes.models import CashboxMovement
from entities.services import save_entity
from hesba_testing.factories import make_cashbox, make_item, make_location, stock_in
from inventory.models import StockMovement
from inventory.services import get_item_location_stock_quantity, recalculate_item_average_cost
from ledger.models import JournalEntry
from ledger.projector import Projector
from manufacturing.models import ProductionRun
from master_data.models import Category
from permissions.models import RoleCode
from sales.models import CustomerLedgerEntry, SalesInvoice, SalesLine
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from taxes.models import SalesLineTax

from . import kitchen, modifiers, services, stations
from .insights import period_report, save_ingredients
from .models import MenuItemModifierGroup, ModifierGroup, ModifierOption, OrderKind, OrderLine
from .tests import RestaurantSetup, person

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)  # a Wednesday, 12:00 in Cairo


class ModifierSetup(RestaurantSetup):
    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.burger = make_item(item_code="D-BURG", item_name="برجر", default_sale_price="120.00", is_stock_tracked=False)
        self.cheese = make_item(item_code="A-CHEESE", item_name="جبنة زيادة", default_sale_price="15.00", is_stock_tracked=False)
        self.doneness = modifiers.save_group({"name_ar": "درجة الاستواء", "name_en": "Doneness", "min_choices": "1", "max_choices": "1", "sort": "1",
                                              "options": [{"name_ar": "ميديم", "name_en": "Medium"}, {"name_ar": "ويل دن", "name_en": "Well done"}]}, self.owner)
        self.extras = modifiers.save_group({"name_ar": "إضافات", "name_en": "Extras", "min_choices": "0", "max_choices": "2", "sort": "2",
                                            "options": [{"name_ar": "جبنة زيادة", "name_en": "Extra cheese", "item": self.cheese},
                                                        {"name_ar": "من غير بصل", "name_en": "No onions"}]}, self.owner)
        modifiers.set_dish_groups(self.burger, [self.doneness, self.extras], self.owner)
        self.medium, self.well = self.doneness.options.order_by("sort", "pk")
        self.cheese_opt, self.no_onion = self.extras.options.order_by("sort", "pk")
        self.waiter = person(RoleCode.WAITER, "mod_waiter")

    def vat(self, on):
        set_capability_enabled(ClientProfile.get_active(), "vat", on, user=self.owner)

    def two_burgers_with_cheese(self, order=None, quantity=2):
        order = order or services.open_order(self.owner, table=self.t1, guests=2)
        dish = services.add_item(order, self.burger, self.owner, quantity, options=[self.medium.pk, self.cheese_opt.pk])
        return order, dish

    def nothing_moved(self):
        Projector().rebuild()
        return {"lines": OrderLine.objects.count(), "invoices": SalesInvoice.objects.count(), "cash": CashboxMovement.objects.count(),
                "stock": StockMovement.objects.count(), "journal": JournalEntry.objects.count(), "ledger": CustomerLedgerEntry.objects.count()}


class AddingTests(ModifierSetup):
    def test_two_burgers_with_cheese_are_a_dish_line_and_its_add_on_line(self):
        order, dish = self.two_burgers_with_cheese()
        lines = list(order.lines.order_by("pk"))
        self.assertEqual([(line.item.item_code, line.quantity, line.unit_price, line.amount, line.parent_id, line.options) for line in lines], [
            ("D-BURG", D("2.000"), D("120.00"), D("240.00"), None, "ميديم"),
            ("A-CHEESE", D("2.000"), D("15.00"), D("30.00"), dish.pk, "جبنة زيادة"),
        ])
        self.assertEqual(order.total, D("270.00"))

    def test_the_add_on_takes_its_items_price_at_that_moment(self):
        order, _dish = self.two_burgers_with_cheese(quantity=1)
        self.cheese.default_sale_price = D("18.00")
        self.cheese.save(update_fields=["default_sale_price"])
        services.add_item(order, self.burger, self.owner, 1, options=[self.well.pk, self.cheese_opt.pk])
        self.assertEqual(list(order.lines.filter(item=self.cheese).order_by("pk").values_list("unit_price", flat=True)), [D("15.00"), D("18.00")])

    def test_a_missing_required_choice_is_refused_and_nothing_is_added(self):
        order = services.open_order(self.owner, table=self.t1)
        with self.assertRaisesMessage(ValidationError, "درجة الاستواء"):
            services.add_item(order, self.burger, self.owner, 1, options=[self.cheese_opt.pk])
        with self.assertRaisesMessage(ValidationError, "درجة الاستواء"):
            services.add_item(order, self.burger, self.owner)  # the one-tap path of a dish that asks a question
        with self.assertRaisesMessage(ValidationError, "Doneness"):
            services.add_item(order, self.burger, self.owner, lang="en")
        self.assertEqual(order.lines.count(), 0)

    def test_the_maximum_is_refused(self):
        order = services.open_order(self.owner, table=self.t1)
        with self.assertRaisesMessage(ValidationError, "درجة الاستواء"):
            services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk, self.well.pk])
        third = ModifierOption.objects.create(group=self.extras, name_ar="هالبينو", sort=9)
        with self.assertRaisesMessage(ValidationError, "إضافات"):
            services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk, self.cheese_opt.pk, self.no_onion.pk, third.pk])
        self.assertEqual(order.lines.count(), 0)

    def test_burgers_with_add_ons_never_merge_and_plain_dishes_still_do(self):
        order, first = self.two_burgers_with_cheese(quantity=1)
        second = services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk, self.no_onion.pk])
        same = services.add_item(order, self.burger, self.owner, 1, options=[self.medium.pk, self.cheese_opt.pk])  # the same choices: still its own plate
        self.assertEqual(len({first.pk, second.pk, same.pk}), 3)
        self.assertEqual(second.options, "ميديم · من غير بصل")
        self.assertEqual(order.lines.filter(parent__isnull=True).count(), 3)
        self.assertEqual(order.lines.filter(item=self.cheese).count(), 2)
        services.add_item(order, self.koshary, self.owner)
        plain = services.add_item(order, self.koshary, self.owner, 2)
        self.assertEqual((order.lines.filter(item=self.koshary).count(), plain.quantity), (1, D("3.000")))

    def test_a_plain_add_of_an_item_that_is_someones_add_on_never_merges_into_the_add_on(self):
        order, _dish = self.two_burgers_with_cheese()
        loose = services.add_item(order, self.cheese, self.owner, 1)
        self.assertIsNone(loose.parent_id)
        self.assertEqual(sorted(order.lines.filter(item=self.cheese).values_list("quantity", flat=True)), [D("1.000"), D("2.000")])

    def test_only_optional_groups_and_no_choice_is_the_one_tap_path(self):
        fries = make_item(item_code="D-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        modifiers.set_dish_groups(fries, [self.extras], self.owner)
        order = services.open_order(self.owner, table=self.t1)
        services.add_item(order, fries, self.owner)
        line = services.add_item(order, fries, self.owner)
        self.assertEqual((order.lines.count(), line.quantity, line.options), (1, D("2.000"), ""))


class FamilyTests(ModifierSetup):
    def test_changing_the_dishs_quantity_changes_its_add_ons(self):
        order, dish = self.two_burgers_with_cheese()
        services.change_line(dish, self.owner, quantity="3")
        self.assertEqual(sorted(order.lines.values_list("quantity", flat=True)), [D("3.000"), D("3.000")])
        self.assertEqual(order.total, D("405.00"))
        services.change_line(dish, self.owner, quantity="0")
        self.assertEqual(order.lines.count(), 0)

    def test_an_add_on_is_never_changed_removed_or_voided_on_its_own(self):
        order, dish = self.two_burgers_with_cheese()
        child = dish.children.get()
        before = self.nothing_moved()
        for call in (lambda: services.change_line(child, self.owner, quantity="5"), lambda: services.change_line(child, self.owner, quantity="0"),
                     lambda: services.void_line(child, self.owner)):
            with self.assertRaisesMessage(ValidationError, "الإضافة"):
                call()
        services.send_to_kitchen(order, self.owner)
        with self.assertRaisesMessage(ValidationError, "الإضافة"):
            services.void_line(OrderLine.objects.get(pk=child.pk), self.owner, reason="غلط")
        self.assertEqual(self.nothing_moved(), before)
        self.assertEqual(list(order.lines.values_list("voided", "quantity")), [(False, D("2.000")), (False, D("2.000"))])

    def test_removing_an_unsent_dish_removes_its_add_on(self):
        order, dish = self.two_burgers_with_cheese()
        services.void_line(dish, self.owner)
        self.assertEqual(order.lines.count(), 0)

    def test_voiding_a_fired_dish_voids_its_add_on_under_r13s_rule(self):
        order, dish = self.two_burgers_with_cheese()
        services.send_to_kitchen(order, self.owner)
        dish.refresh_from_db()
        with self.assertRaises(PermissionDenied):  # R13: a waiter cannot void what the kitchen already has
            services.void_line(dish, self.waiter, reason="العميل غيّر رأيه")
        with self.assertRaisesMessage(ValidationError, "سبب"):
            services.void_line(dish, self.owner)
        self.assertFalse(order.lines.filter(voided=True).exists())
        services.void_line(dish, self.owner, reason="العميل غيّر رأيه")
        self.assertEqual(list(order.lines.order_by("pk").values_list("voided", "void_reason")), [(True, "العميل غيّر رأيه"), (True, "العميل غيّر رأيه")])
        self.assertEqual(order.total, D("0.00"))


class BillTests(ModifierSetup):
    def pay(self, order, tendered):
        invoice, change = services.pay(order, self.owner, cashbox=self.cashbox, tendered=D(tendered))
        invoice.refresh_from_db()
        return invoice, change

    def test_vat_on_the_bill_is_307_80_with_the_add_on_right_after_its_dish(self):
        self.vat(True)
        order, _dish = self.two_burgers_with_cheese()
        services.add_item(order, self.tea, self.owner)  # a dish without groups, after the burger
        services.change_line(order.lines.get(item=self.tea), self.owner, quantity="0")
        invoice, change = self.pay(order, "310.00")
        self.assertEqual(change, D("2.20"))
        # 2 × 120.00 = 240.00 and 2 × 15.00 = 30.00; VAT 14%: 33.60 + 4.20 = 37.80; total 307.80.
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.paid_now, invoice.remaining_due),
                         (D("270.00"), D("37.80"), D("307.80"), D("307.80"), D("0.00")))
        self.assertEqual([(line.item.item_code, line.description, line.quantity, line.unit_sale_price, line.line_total_amount)
                          for line in invoice.lines.select_related("item").order_by("pk")], [
            ("D-BURG", "", D("2.000"), D("120.00"), D("240.00")),
            ("A-CHEESE", "+ جبنة زيادة (برجر)", D("2.000"), D("15.00"), D("30.00")),
        ])
        self.assertEqual([(row.line.item.item_code, row.taxable_amount, row.tax_amount) for row in SalesLineTax.objects.filter(line__invoice=invoice).order_by("line__pk")],
                         [("D-BURG", D("240.00"), D("33.60")), ("A-CHEESE", D("30.00"), D("4.20"))])
        self.assertEqual(list(CashboxMovement.objects.filter(sales_invoice=invoice).values_list("cashbox__cashbox_code", "direction", "amount")),
                         [("TILL", "in", D("307.80"))])
        self.assertFalse(CustomerLedgerEntry.objects.filter(sales_invoice=invoice).exists())  # a walk-in paying in full
        Projector().rebuild()
        entry = JournalEntry.objects.get(source_type="sales_invoice")
        self.assertEqual(sorted((line.account.code, line.debit, line.credit) for line in entry.lines.select_related("account")),
                         [("1101", D("307.80"), D("0.00")), ("2103", D("0.00"), D("37.80")), ("4101", D("0.00"), D("270.00"))])

    def test_vat_off_the_bill_is_270_00(self):
        self.vat(False)
        order, _dish = self.two_burgers_with_cheese()
        invoice, change = self.pay(order, "270.00")
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, change), (D("270.00"), D("0.00"), D("270.00"), D("0.00")))
        self.assertEqual(list(invoice.lines.order_by("pk").values_list("item__item_code", "line_total_amount")), [("D-BURG", D("240.00")), ("A-CHEESE", D("30.00"))])
        self.assertEqual(list(CashboxMovement.objects.filter(sales_invoice=invoice).values_list("amount", flat=True)), [D("270.00")])
        Projector().rebuild()
        entry = JournalEntry.objects.get(source_type="sales_invoice")
        self.assertEqual(sorted((line.account.code, line.debit, line.credit) for line in entry.lines.select_related("account")),
                         [("1101", D("270.00"), D("0.00")), ("4101", D("0.00"), D("270.00"))])

    def test_families_reach_the_bill_in_order_even_when_lines_interleave(self):
        self.vat(False)
        order, first = self.two_burgers_with_cheese(quantity=1)
        services.add_item(order, self.tea, self.owner)
        services.add_item(order, self.burger, self.owner, 1, options=[self.well.pk, self.cheese_opt.pk])
        invoice, _change = self.pay(order, "1000")
        self.assertEqual(list(invoice.lines.order_by("pk").values_list("item__item_code", "description")), [
            ("D-BURG", ""), ("A-CHEESE", "+ جبنة زيادة (برجر)"), ("D-TEA", ""), ("D-BURG", ""), ("A-CHEESE", "+ جبنة زيادة (برجر)")])
        self.assertEqual(invoice.total_amount, D("285.00"))

    def test_a_free_choice_costs_nothing_and_adds_no_invoice_row(self):
        self.vat(True)
        order = services.open_order(self.owner, table=self.t1)
        dish = services.add_item(order, self.burger, self.owner, 1, options=[self.well.pk, self.no_onion.pk])
        self.assertEqual((order.lines.count(), dish.options, order.total), (1, "ويل دن · من غير بصل", D("120.00")))
        invoice, _change = self.pay(order, "136.80")
        self.assertEqual(list(invoice.lines.values_list("item__item_code", "description", "line_total_amount")), [("D-BURG", "", D("120.00"))])
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("16.80"), D("136.80")))

    def test_a_voided_dish_and_its_add_on_never_reach_the_bill(self):
        self.vat(False)
        order, dish = self.two_burgers_with_cheese()
        services.add_item(order, self.tea, self.owner)
        services.send_to_kitchen(order, self.owner)
        services.void_line(OrderLine.objects.get(pk=dish.pk), self.owner, reason="اتأخر")
        invoice, _change = self.pay(order, "15.00")
        self.assertEqual(list(invoice.lines.values_list("item__item_code", flat=True)), ["D-TEA"])

    def test_a_kitchen_made_cheese_add_on_takes_0_060_kg_for_two_plates_at_payment(self):
        self.vat(False)
        cheddar = make_item(item_code="R-CHED", item_name="جبنة شيدر", unit="كجم", is_stock_tracked=True)
        stock_in(cheddar, self.location, 5, "200.00")
        recalculate_item_average_cost(cheddar)
        save_ingredients(self.cheese, [(cheddar, "0.030")], self.owner)  # 6.00 a portion
        made = kitchen.make_in_kitchen(self.cheese, self.owner)
        self.assertEqual(made.pk, self.cheese.pk)  # never sold: switched in place, so the option still points at it
        order, _dish = self.two_burgers_with_cheese()
        invoice, _change = self.pay(order, "270.00")
        self.assertEqual(get_item_location_stock_quantity(cheddar, self.location), D("4.940"))
        self.assertEqual(list(ProductionRun.objects.values_list("recipe__product__item_code", "output_quantity")), [("A-CHEESE", D("2.000"))])
        self.assertEqual(list(StockMovement.objects.filter(item__in=[cheddar, self.cheese]).order_by("pk").values_list("item__item_code", "movement_type", "quantity")),
                         [("R-CHED", "adjustment_out", D("0.060")), ("A-CHEESE", "adjustment_in", D("2.000")), ("A-CHEESE", "sale_out", D("2.000"))])
        cheese_row = invoice.lines.get(item=self.cheese)
        self.assertEqual((cheese_row.unit_cost, cheese_row.line_cost_amount), (D("6.0000"), D("12.00")))

    def test_a_dish_switched_to_the_kitchen_after_it_sold_keeps_its_groups_and_add_on_options(self):
        self.vat(False)
        order, _dish = self.two_burgers_with_cheese(quantity=1)
        self.pay(order, "135.00")  # both items now have history: the kitchen switch copies them
        cheddar = make_item(item_code="R-CHED", item_name="جبنة شيدر", unit="كجم", is_stock_tracked=True)
        beef = make_item(item_code="R-BEEF", item_name="لحمة مفرومة", unit="كجم", is_stock_tracked=True)
        save_ingredients(self.cheese, [(cheddar, "0.030")], self.owner)
        save_ingredients(self.burger, [(beef, "0.150")], self.owner)
        new_cheese = kitchen.make_in_kitchen(self.cheese, self.owner)
        new_burger = kitchen.make_in_kitchen(self.burger, self.owner)
        self.assertNotEqual((new_cheese.pk, new_burger.pk), (self.cheese.pk, self.burger.pk))
        self.assertEqual(ModifierOption.objects.get(pk=self.cheese_opt.pk).item_id, new_cheese.pk)
        self.assertEqual(list(MenuItemModifierGroup.objects.filter(item=new_burger).order_by("sort").values_list("group_id", flat=True)),
                         [self.doneness.pk, self.extras.pk])
        self.assertFalse(MenuItemModifierGroup.objects.filter(item=self.burger).exists())

    def test_the_e_receipt_has_one_item_per_invoice_row(self):
        from einvoice.models import ItemCode
        from einvoice.services import save_issuer_settings
        from ereceipt.models import Device, Receipt
        from printing.company import save_company_details

        self.vat(True)
        set_capability_enabled(ClientProfile.get_active(), "e_receipt", True)
        save_company_details({"company.tax_number": "100-200-300"}, self.owner)
        save_issuer_settings({"einvoice.activity_code": "5610", "einvoice.branch_id": "0", "einvoice.governate": "Cairo", "einvoice.region_city": "Nasr City",
                              "einvoice.street": "Abbas El Akkad", "einvoice.building_number": "12", "einvoice.person_id_threshold": "50000"}, self.owner)
        ItemCode.objects.create(item=self.burger, code_type="EGS", code="EG-100200300-BURG", unit_type="EA")
        ItemCode.objects.create(item=self.cheese, code_type="EGS", code="EG-100200300-CHS", unit_type="EA")
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        order, _dish = self.two_burgers_with_cheese()
        invoice, _change = self.pay(order, "307.80")
        items = Receipt.objects.get(invoice=invoice).document["itemData"]
        self.assertEqual([(row["internalCode"], row["description"], row["quantity"], row["totalSale"]) for row in items],
                         [("D-BURG", "برجر", 2.0, 240.0), ("A-CHEESE", "+ جبنة زيادة (برجر)", 2.0, 30.0)])

    def test_a_branchs_order_with_add_ons_posts_to_its_branch(self):
        self.vat(False)
        branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي", "name_en": "Maadi branch"}, self.owner)
        b_hall = make_location(location_code="BR-HALL", name_ar="صالة المعادي", entity=branch, is_selling_location=True, is_default=True)
        b_till = make_cashbox(cashbox_code="BR-TILL", name_ar="خزنة المعادي", entity=branch, is_default=True)
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=branch)
        self.two_burgers_with_cheese(order)
        invoice, _change = services.pay(order, self.owner, cashbox=b_till, tendered=D("270.00"))
        self.assertEqual((invoice.selling_location_id, invoice.cashbox_id, invoice.total_amount), (b_hall.pk, b_till.pk, D("270.00")))
        self.assertEqual(list(CashboxMovement.objects.filter(sales_invoice=invoice).values_list("cashbox__cashbox_code", "amount")), [("BR-TILL", D("270.00"))])
        with self.assertRaisesMessage(ValidationError, "فرع"):  # R12: a branch's bill never goes into the main till
            other = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=branch)
            self.two_burgers_with_cheese(other)
            services.pay(other, self.owner, cashbox=self.cashbox, tendered=D("270.00"))

    def test_the_report_counts_dishes_by_their_dish_lines_and_keeps_the_money(self):
        self.vat(False)
        order, _dish = self.two_burgers_with_cheese()
        self.pay(order, "270.00")
        today = NOW.date()
        report = period_report(today, today)
        self.assertEqual([(row["item"].item_code, row["quantity"], row["sales"]) for row in report["dishes"]], [("D-BURG", D("2.000"), D("270.00"))])
        self.assertEqual((report["totals"]["orders"], report["totals"]["sales"]), (1, D("270.00")))


class CraftedTests(ModifierSetup):
    def refused(self, options, item=None, message=""):
        order = services.open_order(self.owner, table=self.t1)
        before = self.nothing_moved()
        with self.assertRaisesMessage(ValidationError, message):
            services.add_item(order, item or self.burger, self.owner, 1, options=options)
        self.assertEqual(self.nothing_moved(), before)

    def test_an_option_of_a_group_the_dish_does_not_have(self):
        sauces = modifiers.save_group({"name_ar": "صوصات", "min_choices": "0", "max_choices": "1", "options": [{"name_ar": "باربكيو"}]}, self.owner)
        self.refused([self.medium.pk, sauces.options.get().pk], message="مش من اختيارات")

    def test_options_for_a_dish_without_groups(self):
        self.refused([self.cheese_opt.pk], item=self.koshary, message="مش من اختيارات")

    def test_an_inactive_option_group_or_add_on_item(self):
        ModifierOption.objects.filter(pk=self.well.pk).update(active=False)
        self.refused([self.well.pk], message="مش متاح")
        ModifierGroup.objects.filter(pk=self.extras.pk).update(active=False)
        self.refused([self.medium.pk, self.cheese_opt.pk], message="مش من اختيارات")
        ModifierGroup.objects.filter(pk=self.extras.pk).update(active=True)
        self.cheese.active = False
        self.cheese.save(update_fields=["active"])
        self.refused([self.medium.pk, self.cheese_opt.pk], message="مش متاح")

    def test_the_same_option_twice_and_ids_that_are_not_numbers(self):
        self.refused([self.medium.pk, self.medium.pk], message="متكرر")
        self.refused([self.medium.pk, "x1"], message="مش من اختيارات")
        self.refused([self.medium.pk, "999999"], message="مش من اختيارات")

    def test_a_child_line_id_used_as_a_dish_from_the_screen(self):
        order, dish = self.two_burgers_with_cheese()
        child = dish.children.get()
        self.client.force_login(self.owner)
        url = reverse("restaurant:order", args=[order.pk])
        before = self.nothing_moved()
        page = self.client.post(url, {"action": "qty", "line": str(child.pk), "quantity": "7"}, follow=True)
        self.assertContains(page, "الإضافة بتتغيّر مع طبقها")
        self.client.post(url, {"action": "void", "line": str(child.pk)}, follow=True)
        services.send_to_kitchen(order, self.owner)
        self.client.post(url, {"action": "void", "line": str(child.pk), "reason": "x"}, follow=True)
        self.assertEqual(self.nothing_moved(), before)
        self.assertEqual(list(order.lines.order_by("pk").values_list("quantity", "voided")), [(D("2.000"), False), (D("2.000"), False)])

    def test_the_kitchen_screen_cannot_tap_an_add_on_on_its_own(self):
        order, dish = self.two_burgers_with_cheese()
        services.send_to_kitchen(order, self.owner)
        child = dish.children.get()
        self.client.force_login(self.owner)
        self.client.post(reverse("restaurant:kds_act"), {"op": "ready", "id": str(child.pk)})
        self.assertIsNone(OrderLine.objects.get(pk=child.pk).ready_at)


class KitchenTests(ModifierSetup):
    def setUp(self):
        super().setUp()
        self.grill = Category.objects.create(category_code="C-GRILL", name_ar="مشويات", name_en="Grill")
        self.drinks = Category.objects.create(category_code="C-DRINK", name_ar="مشروبات", name_en="Drinks")
        for item, category in ((self.burger, self.grill), (self.cheese, self.drinks)):  # the cheese sits in another station's category
            item.category = category
            item.save(update_fields=["category"])

    def test_the_add_on_follows_its_dish_to_one_station_and_prints_under_it(self):
        self.kitchen = stations.save_station({"name_ar": "المطبخ", "sort": "1", "is_default": True, "categories": [self.grill.pk]}, self.owner)
        self.bar = stations.save_station({"name_ar": "المشروبات", "sort": "2", "categories": [self.drinks.pk]}, self.owner)
        order, dish = self.two_burgers_with_cheese()
        ticket = services.send_to_kitchen(order, self.owner)
        self.assertEqual([one.station_id for one in ticket.batch], [self.kitchen.pk])
        self.assertEqual(set(order.lines.values_list("ticket_id", flat=True)), {ticket.pk})
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:ticket", args=[ticket.pk])).content.decode()
        self.assertIn('data-ticket-line="D-BURG"', page)
        self.assertNotIn('data-ticket-line="A-CHEESE"', page)
        self.assertIn('data-ticket-options>ميديم<', page)
        self.assertIn('data-ticket-addon="A-CHEESE">+ جبنة زيادة<', page)
        self.assertLess(page.index('data-ticket-line="D-BURG"'), page.index('data-ticket-addon="A-CHEESE"'))

    def test_the_kitchen_screen_shows_the_add_on_under_its_dish_not_as_a_dish(self):
        order, dish = self.two_burgers_with_cheese()
        services.add_item(order, self.burger, self.owner, 1, options=[self.well.pk, self.no_onion.pk])
        services.send_to_kitchen(order, self.owner)
        self.client.force_login(self.owner)
        feed = self.client.get(reverse("restaurant:kds_feed")).json()
        lines = feed["tickets"][0]["lines"]
        self.assertEqual([(line["name"], line["quantity"], line["extras"]) for line in lines],
                         [("برجر", "2", ["ميديم", "+ جبنة زيادة"]), ("برجر", "1", ["ويل دن · من غير بصل"])])
        self.client.post(reverse("restaurant:kds_act"), {"op": "ready", "id": str(dish.pk)})
        self.assertEqual(OrderLine.objects.get(pk=dish.pk).ready_at, NOW)
        self.assertEqual(dish.children.get().ready_at, NOW)  # the add-on is ready with its dish

    def test_a_voided_dish_leaves_the_screen_once_the_kitchen_sees_it(self):
        order, dish = self.two_burgers_with_cheese()
        ticket = services.send_to_kitchen(order, self.owner)
        stations.finish_ticket(ticket)
        services.void_line(OrderLine.objects.get(pk=dish.pk), self.owner, reason="اتأخر")
        self.assertEqual(list(stations.shown_tickets()), [ticket])
        stations.mark_line(OrderLine.objects.get(pk=dish.pk))  # the kitchen taps the struck-through dish
        self.assertEqual(list(stations.shown_tickets()), [])


class ScreenTests(ModifierSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_order_page_opens_a_choice_sheet_for_a_dish_with_groups_and_one_tap_for_the_rest(self):
        order = services.open_order(self.owner, table=self.t1)
        page = self.client.get(reverse("restaurant:order", args=[order.pk])).content.decode()
        self.assertIn(f'data-choice-sheet="{self.burger.pk}"', page)
        self.assertIn(f'data-opens-sheet="sheet-{self.burger.pk}"', page)
        self.assertNotIn(f'data-opens-sheet="sheet-{self.koshary.pk}"', page)
        self.assertIn('name="option" value="%d" required' % self.medium.pk, page)  # one required choice: radios
        self.assertIn('type="checkbox" name="option" value="%d"' % self.cheese_opt.pk, page)
        self.assertIn("+15.00", page)
        self.assertIn("اختيار واحد", page)
        english = self.client.get(reverse("restaurant:order", args=[order.pk]) + "?lang=en").content.decode()
        self.assertIn("Doneness", english)
        self.assertIn("Extra cheese", english)

    def test_adding_from_the_sheet_and_in_one_tap(self):
        order = services.open_order(self.owner, table=self.t1)
        url = reverse("restaurant:order", args=[order.pk])
        self.client.post(url, {"action": "add", "item": str(self.burger.pk), "quantity": "2", "option": [str(self.medium.pk), str(self.cheese_opt.pk)]})
        self.client.post(url, {"action": "add", "item": str(self.koshary.pk)})
        refused = self.client.post(url, {"action": "add", "item": str(self.burger.pk)}, follow=True)
        self.assertContains(refused, "درجة الاستواء")
        self.assertEqual(list(order.lines.order_by("pk").values_list("item__item_code", "quantity", "parent_id")),
                         [("D-BURG", D("2.000"), None), ("A-CHEESE", D("2.000"), order.lines.get(item=self.burger).pk), ("D-KOSH", D("1.000"), None)])
        page = self.client.get(url).content.decode()
        self.assertIn('data-addon-line="A-CHEESE"', page)
        self.assertIn('data-line-options>ميديم<', page)
        self.assertEqual(page.count('data-addon-line="A-CHEESE"'), 1)

    def test_the_set_up_page_needs_manage_items_and_saves_a_group(self):
        url = reverse("restaurant:modifiers")
        page = self.client.get(url)
        self.assertContains(page, "الإضافات والاختيارات")
        self.assertContains(page, "درجة الاستواء")
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"name_ar": "صوصات"}).status_code, 403)
        self.client.force_login(self.owner)
        saved = self.client.post(url, {"name_ar": "صوصات", "name_en": "Sauces", "min_choices": "0", "max_choices": "2", "sort": "3", "active": "on",
                                       "opt-0-name_ar": "باربكيو", "opt-0-name_en": "BBQ", "opt-0-item": "", "opt-0-active": "on",
                                       "opt-1-name_ar": "", "opt-1-name_en": "", "opt-1-item": str(self.cheese.pk), "opt-1-active": "on",
                                       "opt-2-name_ar": "", "opt-2-name_en": "", "opt-2-item": ""})
        self.assertEqual(saved.status_code, 302)
        group = ModifierGroup.objects.get(name_ar="صوصات")
        self.assertEqual(list(group.options.order_by("sort", "pk").values_list("name_ar", "item_id", "active")),
                         [("باربكيو", None, True), ("جبنة زيادة", self.cheese.pk, True)])
        bad = self.client.post(url, {"name_ar": "غلط", "min_choices": "3", "max_choices": "1", "opt-0-name_ar": "أ"})
        self.assertContains(bad, "أقل عدد")
        self.assertFalse(ModifierGroup.objects.filter(name_ar="غلط").exists())
        english = self.client.get(url + "?lang=en")
        self.assertContains(english, "Add-ons and choices")

    def test_editing_a_group_keeps_its_options_and_turns_one_off(self):
        url = reverse("restaurant:modifiers")
        self.client.post(url, {"pk": str(self.doneness.pk), "name_ar": "درجة الاستواء", "name_en": "Doneness", "min_choices": "1", "max_choices": "1",
                               "sort": "1", "active": "on", "opt-0-pk": str(self.medium.pk), "opt-0-name_ar": "ميديم", "opt-0-active": "on",
                               "opt-1-pk": str(self.well.pk), "opt-1-name_ar": "ويل دن", "opt-2-name_ar": "رير", "opt-2-active": "on"})
        self.assertEqual(list(self.doneness.options.order_by("sort", "pk").values_list("pk", "name_ar", "active")),
                         [(self.medium.pk, "ميديم", True), (self.well.pk, "ويل دن", False), (self.doneness.options.get(name_ar="رير").pk, "رير", True)])

    def test_the_dish_page_attaches_groups_without_touching_the_ingredients(self):
        rice = make_item(item_code="R-RICE", item_name="أرز", unit="كجم", is_stock_tracked=True)
        save_ingredients(self.koshary, [(rice, "0.200")], self.owner)
        url = reverse("restaurant:dish", args=[self.koshary.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-dish-groups")
        self.client.post(url, {"action": "groups", "group": [str(self.extras.pk)]})
        self.assertEqual(list(MenuItemModifierGroup.objects.filter(item=self.koshary).values_list("group_id", flat=True)), [self.extras.pk])
        self.assertEqual(list(self.koshary.dish_ingredients.values_list("ingredient__item_code", "quantity")), [("R-RICE", D("0.200"))])
        self.client.post(url, {"action": "groups"})
        self.assertFalse(MenuItemModifierGroup.objects.filter(item=self.koshary).exists())
        cashier = person(RoleCode.CASHIER, "mod_cashier")
        self.client.force_login(cashier)
        response = self.client.post(url, {"action": "groups", "group": [str(self.extras.pk)]})
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(MenuItemModifierGroup.objects.filter(item=self.koshary).exists())

    def test_styles_and_script_use_tokens_and_never_write_markup(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", open(finders.find("hesba/css/tokens.css"), encoding="utf-8").read()))
        css = open(finders.find("hesba/css/restaurant.css"), encoding="utf-8").read()
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])
        self.assertEqual([c for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css) if c.lower() not in {"#fff", "#ffffff"}], [])
        script = open(finders.find("hesba/js/restaurant.js"), encoding="utf-8").read()
        self.assertNotIn("innerHTML", script)
        for word in ("دوس", "لينك", "بتاع", "الأسطى", "ورشة"):
            self.assertNotIn(word, str(modifiers.MESSAGES))
