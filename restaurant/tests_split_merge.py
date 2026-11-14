"""Restaurants R2a (HG-111): «ادفع جزء» (split a table's bill by items) and «ادمج مع طاولة» (merge two tables).

The set-up of ``tests_split_pay`` (R2b): the main hall «HALL» with «TILL» and the card machine «CARD», the Maadi branch
«BR-HALL» with «BR-TILL», a «T1» in each, R1a's burger (120.00 with «ميديم» and the priced add-on «جبنة زيادة» at 15.00),
VAT on at 14% per line, the clock on Wednesday 11 Nov 2026 at noon in Cairo. Added here: a koshary at 45.00 and a tea at
10.00, both service dishes.

Table «1» (two guests) orders 2 × koshary and 3 × tea = **120.00**. Guest A pays 1 koshary and 1 tea: **55.00**, 62.70 with
VAT (7.70); the rest is **65.00**, 74.10 with VAT (9.10). The two invoices total 136.80, the bill unsplit (120.00 + 16.80).
A line is split by whole plates and cups, never by money (trap 1): equal shares are R2b's tenders on one bill.
"""

import threading
import time
from decimal import Decimal as D
from unittest import mock, skipUnless

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.services import get_cashbox_balance
from entities.current import working_in
from hesba_testing.factories import make_cashbox, make_item, make_location
from permissions.models import RoleCode

from . import services, stations
from .insights import period_report
from .models import KitchenTicket, Order, OrderKind, OrderLine, OrderStatus
from .tests import person
from .tests_split_pay import SplitBillSetup


TILL_REFUSAL = "افتح وردية الأول عشان تسجّل بيع."


def snapshot(order):
    """The order as the floor and the kitchen see it, without primary keys: every line (its item, quantity, price, note,
    choices, kitchen ticket, ready and void state, and its dish if it is an add-on), the tickets, the guests and the total."""

    order = Order.objects.get(pk=order.pk)
    lines = sorted(
        (line.item.item_code, line.quantity, line.unit_price, line.note, line.options, (line.ticket.order_id, line.ticket.sequence) if line.ticket_id else None,
         bool(line.ready_at), line.voided, line.parent.item.item_code if line.parent_id else "")
        for line in order.lines.select_related("item", "ticket", "parent__item"))
    tickets = list(order.tickets.order_by("sequence").values_list("sequence", "station_id"))
    return {"status": order.status, "table": order.table_id, "guests": order.guests, "total": order.total, "lines": lines, "tickets": tickets}


class SplitSetup(SplitBillSetup):
    def setUp(self):
        super().setUp()
        self.k45 = make_item(item_code="D-K45", item_name="كشري", default_sale_price="45.00", is_stock_tracked=False)
        self.t10 = make_item(item_code="D-T10", item_name="شاي كوباية", default_sale_price="10.00", is_stock_tracked=False)

    def table_order(self, table=None, guests=2, send=True):
        order = services.open_order(self.owner, table=table or self.t1, guests=guests)
        services.add_item(order, self.k45, self.owner, 2)
        services.add_item(order, self.t10, self.owner, 3)
        if send:
            services.send_to_kitchen(order, self.owner)
        return order

    def line(self, order, item):
        return order.lines.get(item=item, parent__isnull=True)

    def split(self, order, picks, user=None, lang="ar"):
        return services.split_order(order, {self.line(order, item).pk: quantity for item, quantity in picks}, user or self.owner, lang=lang)

    def pay(self, order, **extra):
        return services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, tendered=D("1000"), **extra)[0]

    def state(self):
        return {"orders": list(Order.objects.order_by("pk").values_list("pk", "status", "table_id", "guests", "split_from_id", "merged_into_id")),
                "lines": list(OrderLine.objects.order_by("pk").values_list("pk", "order_id", "quantity", "ticket_id", "voided", "parent_id")),
                "tickets": list(KitchenTicket.objects.order_by("pk").values_list("pk", "order_id", "sequence")),
                **self.moved()}

    def assert_refused(self, call, text):
        before = self.state()
        with self.assertRaisesMessage(ValidationError, text):
            call()
        self.assertEqual(self.state(), before)


class SplitBillTests(SplitSetup):
    def test_120_split_as_55_and_65_with_vat_62_70_and_74_10_the_same_136_80_as_unsplit(self):
        order = self.table_order()
        part = self.split(order, [(self.k45, 1), (self.t10, 1)])
        self.assertEqual((part.split_from, part.table, part.guests, part.kind, part.entity, part.waiter, part.status),
                         (order, None, 0, OrderKind.DINE_IN, self.main, order.waiter, OrderStatus.OPEN))
        self.assertEqual((part.total, Order.objects.get(pk=order.pk).total), (D("55.00"), D("65.00")))
        self.assertEqual(part.home_table, self.t1)
        first = self.pay(part)
        self.assertEqual((first.total_amount, first.tax_amount), (D("62.70"), D("7.70")))
        part.refresh_from_db()
        self.assertEqual((part.status, part.table, part.invoice), (OrderStatus.PAID, self.t1, first))  # its table once paid: the parent's
        self.assertIn("(1)", first.notes)  # the invoice names the parent's table, as the table's own bill does
        second = self.pay(order)
        self.assertEqual((second.total_amount, second.tax_amount), (D("74.10"), D("9.10")))
        unsplit = self.pay(self.table_order(table=self.t2))
        self.assertEqual((first.total_amount + second.total_amount, unsplit.total_amount, unsplit.tax_amount), (D("136.80"), D("136.80"), D("16.80")))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("136.80") * 2)

    def test_vat_off_55_00_and_65_00(self):
        self.vat(False)
        order = self.table_order()
        part = self.split(order, [(self.k45, "1"), (self.t10, "1.000")])
        self.assertEqual((self.pay(part).total_amount, self.pay(order).total_amount), (D("55.00"), D("65.00")))

    def test_a_maadi_tables_part_bills_into_maadis_store_and_cashbox(self):
        order = self.table_order(table=self.b_t1)
        part = self.split(order, [(self.k45, 1)])
        self.assertEqual(part.entity, self.branch)
        invoice = services.pay(part, self.manager_b, cashbox=self.b_cashbox, tendered=D("100"))[0]
        self.assertEqual((invoice.selling_location, invoice.cashbox, invoice.total_amount), (self.b_location, self.b_cashbox, D("51.30")))
        with self.assertRaisesMessage(ValidationError, "الخزنة دي تبع فرع تاني"):
            services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("100"))  # R12 still holds on the rest of the bill

    def test_the_moved_lines_keep_their_kitchen_ticket_and_ready_state_and_the_kitchen_shows_each_line_once(self):
        order = self.table_order()
        koshary = self.line(order, self.k45)
        stations.mark_line(koshary)
        ticket = koshary.ticket
        part = self.split(order, [(self.k45, 1), (self.t10, 3)])
        tea = part.lines.get(item=self.t10)
        self.assertEqual(tea.pk, self.line(Order.objects.get(pk=order.pk), self.k45).pk + 1)  # a whole line moves itself, pk and all
        moved = part.lines.get(item=self.k45)
        self.assertEqual((moved.ticket, bool(moved.ready_at), moved.quantity), (ticket, True, D("1.000")))
        self.assertEqual(KitchenTicket.objects.count(), 1)
        card = stations.ticket_card(KitchenTicket.objects.get(), timezone.now(), "صالة", "طاولة")
        self.assertEqual(card["where"], "طاولة 1")
        self.assertEqual(sorted((row["name"], row["quantity"]) for row in card["lines"]), [("شاي كوباية", "3"), ("كشري", "1"), ("كشري", "1")])
        self.client.force_login(self.owner)
        printed = self.client.get(reverse("restaurant:ticket", args=[ticket.pk]))
        self.assertContains(printed, "كشري", count=2)
        self.assertContains(printed, "شاي كوباية", count=1)

    def test_a_kitchen_made_dish_split_across_two_bills_takes_its_ingredients_out_once(self):
        self.vat(False)
        order = services.open_order(self.owner, table=self.t1, guests=2)
        services.add_item(order, self.koshary, self.owner, 2)  # 60.00 a plate, 0.200 kg rice and 0.100 kg lentils each
        services.send_to_kitchen(order, self.owner)
        part = self.split(order, [(self.koshary, 1)])
        self.pay(part)
        self.assertEqual((self.stock(self.rice, self.location), self.stock(self.lentil, self.location)), (D("19.800"), D("9.900")))
        self.pay(order)
        self.assertEqual((self.stock(self.rice, self.location), self.stock(self.lentil, self.location), self.stock(self.koshary, self.location)),
                         (D("19.600"), D("9.800"), D("0")))  # what one bill of two plates takes; no plate left over or short

    def test_a_dish_with_an_add_on_split_one_of_two_the_add_on_follows_on_both_sides_in_family_order(self):
        order = self.burgers(table=self.t1)
        part = services.split_order(order, {order.lines.get(item=self.burger).pk: "1"}, self.owner)
        for side in (Order.objects.get(pk=order.pk), part):
            burger = side.lines.get(item=self.burger)
            self.assertEqual((burger.quantity, burger.options), (D("1.000"), "ميديم"))
            self.assertEqual(list(burger.children.values_list("item__item_code", "quantity", "options", "order_id")), [("A-CHEESE", D("1.000"), "جبنة زيادة", side.pk)])
        invoice = self.pay(part)
        self.assertEqual([(row.item.item_code, row.quantity, row.line_total_amount) for row in invoice.lines.order_by("pk")],
                         [("D-BURG", D("1.000"), D("120.00")), ("A-CHEESE", D("1.000"), D("15.00"))])
        self.assertEqual(invoice.total_amount, D("153.90"))
        rest = self.pay(order)
        self.assertEqual([row.item.item_code for row in rest.lines.order_by("pk")], ["D-BURG", "A-CHEESE"])
        self.assertEqual(invoice.total_amount + rest.total_amount, D("307.80"))  # R2b's unsplit bill of the same two burgers

    def test_the_whole_family_moves_when_a_dish_goes_with_all_its_plates(self):
        order = self.burgers(table=self.t1)
        services.add_item(order, self.t10, self.owner, 1)
        dish = order.lines.get(item=self.burger)
        part = services.split_order(order, {dish.pk: "2"}, self.owner)
        self.assertEqual(sorted(part.lines.values_list("pk", flat=True)), sorted([dish.pk] + list(dish.children.values_list("pk", flat=True))))

    def test_a_part_paid_with_r2b_tenders(self):
        order = self.table_order()
        part = self.split(order, [(self.k45, 1), (self.t10, 1)])
        invoice = services.pay(part, self.owner, cashbox=None, tenders=[(self.cashbox, D("40.00")), (self.card, D("22.70"))])[0]
        self.assertEqual((invoice.total_amount, get_cashbox_balance(self.cashbox), get_cashbox_balance(self.card)), (D("62.70"), D("40.00"), D("22.70")))
        self.assertEqual(Order.objects.get(pk=part.pk).status, OrderStatus.PAID)


class SplitRuleTests(SplitSetup):
    def test_splitting_every_live_line_is_refused_pay_the_bill_itself(self):
        order = self.table_order()
        self.assert_refused(lambda: self.split(order, [(self.k45, 2), (self.t10, 3)]), "ادفع حساب الطاولة نفسه")
        services.void_line(self.line(order, self.t10), self.owner, reason="اتكب")
        self.assert_refused(lambda: self.split(order, [(self.k45, 2)]), "ادفع حساب الطاولة نفسه")  # a voided line is no money owed

    def test_crafted_parts_are_refused_and_nothing_changes(self):
        order = self.table_order()
        other = self.table_order(table=self.t2)
        koshary, tea = self.line(order, self.k45), self.line(order, self.t10)
        burgers = self.burgers()
        cheese = burgers.lines.get(item=self.cheese)
        services.void_line(tea, self.owner, reason="اتكب")
        cases = [
            ({tea.pk: "1"}, "مش من الطلب ده"),  # a voided line
            ({koshary.pk: "3"}, "عدد صحيح من 1 لحد 2"),  # more than the line holds
            ({koshary.pk: "0.5"}, "عدد صحيح من 1 لحد 2"),  # trap 1: never a fraction of a plate (and so never a fraction of its money)
            ({koshary.pk: "-1"}, "عدد صحيح من 1 لحد 2"),
            ({koshary.pk: "abc"}, "عدد صحيح من 1 لحد 2"),
            ({koshary.pk: "0"}, "اختار صنف واحد على الأقل"),  # a zero part
            ({}, "اختار صنف واحد على الأقل"),
            ({self.line(other, self.k45).pk: "1"}, "مش من الطلب ده"),  # another order's line
            ({999999: "1"}, "مش من الطلب ده"),
        ]
        for picks, text in cases:
            with self.subTest(picks=picks):
                self.assert_refused(lambda: services.split_order(order, picks, self.owner), text)
        self.assert_refused(lambda: services.split_order(burgers, {cheese.pk: "1"}, self.owner), "الإضافة بتتغيّر مع طبقها")  # an add-on alone

    def test_a_fraction_of_a_line_is_refused_with_the_way_to_share_equally(self):
        order = self.table_order()
        self.assert_refused(lambda: self.split(order, [(self.t10, "1.5")]), "بالعدد، مش بالفلوس")

    def test_a_part_cannot_be_split_again_and_a_closed_order_cannot_be_split(self):
        order = self.table_order()
        part = self.split(order, [(self.k45, 2)])
        self.assert_refused(lambda: services.split_order(part, {part.lines.get().pk: "1"}, self.owner), "قسّم من حساب الطاولة نفسه")
        self.pay(part)
        self.pay(order)
        self.assert_refused(lambda: self.split(order, [(self.t10, 1)]), "الطلب ده اتقفل")

    def test_a_delivery_is_one_bill(self):
        order = services.open_order(self.owner, kind=OrderKind.DELIVERY, address="12 ش النصر")
        services.add_item(order, self.k45, self.owner, 2)
        self.assert_refused(lambda: self.split(order, [(self.k45, 1)]), "ادفع بأكتر من طريقة")

    def test_a_takeaway_can_be_split_and_its_part_stays_a_takeaway(self):
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY)
        services.add_item(order, self.k45, self.owner, 2)
        part = self.split(order, [(self.k45, 1)])
        self.assertEqual((part.kind, part.table, part.home_table), (OrderKind.TAKEAWAY, None, None))
        self.pay(part)
        self.assertEqual(Order.objects.get(pk=part.pk).table, None)

    def test_the_parent_is_not_paid_cancelled_or_merged_while_a_part_is_open_and_moving_it_takes_the_part_along(self):
        order = self.table_order()
        part = self.split(order, [(self.k45, 1)])
        self.assert_refused(lambda: self.pay(order), part.number)
        self.assert_refused(lambda: services.cancel_order(order, self.owner, "مشيوا"), "لسه مفتوح")
        self.assert_refused(lambda: services.move_table(part, self.t2, self.owner), "انقل حساب الطاولة نفسه")
        other = self.table_order(table=self.a_t1)
        self.assert_refused(lambda: services.merge_orders(other, part, self.owner), "ادمج حساب الطاولة نفسه")
        self.assert_refused(lambda: services.merge_orders(part, other, self.owner), "ادمج حساب الطاولة نفسه")
        services.move_table(order, self.t2, self.owner)
        self.assertEqual(Order.objects.get(pk=part.pk).home_table, self.t2)
        self.pay(part)
        self.assertEqual(Order.objects.get(pk=part.pk).table, self.t2)
        services.cancel_order(order, self.owner, "مشيوا")  # with the part paid, the rest of the bill is the parent's alone again
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.CANCELLED)

    def test_shift_rule_and_void_rules_still_hold_on_a_part(self):
        order = self.table_order()
        part = self.split(order, [(self.k45, 1), (self.t10, 1)])
        self.client.force_login(self.manager_a)  # no shift
        before = self.state()
        response = self.client.post(reverse("restaurant:order", args=[part.pk]), {"lang": "ar", "action": "pay", "cashbox": self.cashbox.pk, "tendered": "100"}, follow=True)
        self.assertContains(response, TILL_REFUSAL)
        self.assertEqual(self.state(), before)
        waiter = person(RoleCode.WAITER, "split_waiter")
        with self.assertRaises(PermissionDenied):
            services.void_line(part.lines.get(item=self.t10), waiter, reason="اتكب")
        with self.assertRaisesMessage(ValidationError, "اكتب سبب إلغاء السطر"):
            services.void_line(part.lines.get(item=self.t10), self.owner)
        services.void_line(part.lines.get(item=self.t10), self.owner, reason="اتكب")
        self.assertEqual(Order.objects.get(pk=part.pk).total, D("45.00"))
        self.assertTrue(AuditLog.objects.filter(action="void_order_line", object_id=str(part.pk)).exists())


class PutBackTests(SplitSetup):
    def test_a_part_put_back_before_paying_leaves_the_order_exactly_as_it_was(self):
        order = self.burgers(table=self.t1)
        services.add_item(order, self.k45, self.owner, 2)
        services.add_item(order, self.t10, self.owner, 3)
        services.send_to_kitchen(order, self.owner)
        stations.mark_line(self.line(order, self.k45))
        services.add_item(order, self.t10, self.owner, 2)  # a new, unsent line of tea
        before = snapshot(order)
        burger = order.lines.get(item=self.burger)
        sent_tea = order.lines.get(item=self.t10, ticket__isnull=False)
        new_tea = order.lines.get(item=self.t10, ticket__isnull=True)
        part = services.split_order(order, {burger.pk: "1", self.line(order, self.k45).pk: "1", sent_tea.pk: "3", new_tea.pk: "1"}, self.owner)
        self.assertNotEqual(snapshot(order), before)
        parent = services.put_back(part, self.owner)
        self.assertEqual(parent, order)
        self.assertEqual(snapshot(order), before)
        part.refresh_from_db()
        self.assertEqual((part.status, part.merged_into, part.lines.count()), (OrderStatus.MERGED, order, 0))
        self.assertTrue(AuditLog.objects.filter(action="put_back_part", object_id=str(order.pk)).exists())
        self.assertEqual(self.pay(order).total_amount, D("307.80") + D("136.80") + D("22.80"))

    def test_a_parts_own_ticket_goes_back_to_the_table_numbered_after_its_last(self):
        order = self.table_order()
        services.add_item(order, self.t10, self.owner, 1)
        part = services.split_order(order, {order.lines.get(item=self.t10, ticket__isnull=True).pk: "1"}, self.owner)
        ticket = services.send_to_kitchen(part, self.owner)
        self.assertEqual((ticket.order, ticket.sequence), (part, 1))
        card = stations.ticket_card(ticket, timezone.now(), "صالة", "طاولة")
        self.assertEqual(card["where"], "طاولة 1")  # the part's own ticket names its table's
        services.put_back(part, self.owner)
        ticket.refresh_from_db()
        self.assertEqual((ticket.order, ticket.sequence), (order, 2))
        self.assertEqual(list(order.tickets.values_list("sequence", flat=True)), [1, 2])

    def test_a_paid_part_or_an_ordinary_order_cannot_be_put_back(self):
        order = self.table_order()
        part = self.split(order, [(self.k45, 1)])
        self.assert_refused(lambda: services.put_back(order, self.owner), "ده مش جزء من حساب")
        self.pay(part)
        self.assert_refused(lambda: services.put_back(part, self.owner), "الطلب ده اتقفل")


class MergeTests(SplitSetup):
    def two_tables(self):
        one = services.open_order(self.owner, table=self.t1, guests=2)
        services.add_item(one, self.k45, self.owner, 2)  # 90.00
        services.send_to_kitchen(one, self.owner)
        two = services.open_order(self.owner, table=self.t2, guests=3)
        services.add_item(two, self.t10, self.owner, 3)  # 30.00
        services.send_to_kitchen(two, self.owner)
        return one, two

    def test_table_1_and_table_2_become_one_order_of_120_with_5_guests_and_table_2_is_free_at_once(self):
        self.vat(False)
        one, two = self.two_tables()
        moved_ticket = two.tickets.get()
        merged = services.merge_orders(one, two, self.owner)
        self.assertEqual((merged, merged.total, merged.guests, merged.table, merged.waiter), (one, D("120.00"), 5, self.t1, one.waiter))
        two.refresh_from_db()
        self.assertEqual((two.status, two.merged_into, two.lines.count(), two.tickets.count()), (OrderStatus.MERGED, one, 0, 0))
        moved_ticket.refresh_from_db()
        self.assertEqual((moved_ticket.order, moved_ticket.sequence), (one, 2))
        self.assertEqual(list(one.tickets.values_list("sequence", flat=True)), [1, 2])
        self.assertEqual(stations.ticket_card(moved_ticket, timezone.now(), "صالة", "طاولة")["where"], "طاولة 1")
        self.assertEqual(set(AuditLog.objects.filter(action__in=("merge_orders", "merged_into")).values_list("action", "object_id")),
                         {("merge_orders", str(one.pk)), ("merged_into", str(two.pk))})
        tables, _others = services.board()
        self.assertEqual({row["table"].name: row["order"] for row in tables}["2"], None)
        fresh = services.open_order(self.owner, table=self.t2)
        self.assertNotEqual(fresh.pk, two.pk)
        self.assertEqual(self.pay(one).total_amount, D("120.00"))
        report = period_report(timezone.localdate(), timezone.localdate())
        self.assertEqual((report["totals"]["orders"], report["totals"]["sales"], report["totals"]["guests"]), (1, D("120.00"), 5))

    def test_merging_into_a_paid_or_cancelled_order_itself_another_branch_or_a_takeaway_is_refused(self):
        one, two = self.two_tables()
        maadi = self.table_order(table=self.b_t1)
        takeaway = services.open_order(self.owner, kind=OrderKind.TAKEAWAY)
        services.add_item(takeaway, self.k45, self.owner, 1)
        self.assert_refused(lambda: services.merge_orders(one, one, self.owner), "في نفسها")
        self.assert_refused(lambda: services.merge_orders(one, maadi, self.owner), "تبع فرع تاني")
        self.assert_refused(lambda: services.merge_orders(maadi, one, self.owner), "تبع فرع تاني")
        self.assert_refused(lambda: services.merge_orders(one, takeaway, self.owner), "لطلبات الصالة المفتوحة بس")
        self.assert_refused(lambda: services.merge_orders(takeaway, one, self.owner), "لطلبات الصالة المفتوحة بس")
        self.assert_refused(lambda: services.merge_orders(one, None, self.owner), "اختار الطاولة")
        self.pay(two)
        self.assert_refused(lambda: services.merge_orders(one, two, self.owner), "اتقفل")
        self.assert_refused(lambda: services.merge_orders(two, one, self.owner), "اتقفل")
        services.cancel_order(one, self.owner)
        three = self.table_order(table=self.a_t1)
        self.assert_refused(lambda: services.merge_orders(one, three, self.owner), "اتقفل")
        self.assert_refused(lambda: services.merge_orders(three, one, self.owner), "اتقفل")

    def test_the_parts_of_the_merged_table_open_and_paid_follow_it_so_its_guests_count_once(self):
        self.vat(False)
        one, two = self.two_tables()
        paid = self.split(two, [(self.t10, 1)])
        self.pay(paid)
        still_open = self.split(two, [(self.t10, 1)])
        services.merge_orders(one, two, self.owner)
        self.assertEqual(set(Order.objects.filter(pk__in=[paid.pk, still_open.pk]).values_list("split_from_id", flat=True)), {one.pk})
        self.assertEqual(Order.objects.get(pk=one.pk).total, D("100.00"))
        self.pay(still_open)
        self.pay(one)
        report = period_report(timezone.localdate(), timezone.localdate())
        self.assertEqual((report["totals"]["orders"], report["totals"]["sales"], report["totals"]["guests"]), (1, D("120.00"), 5))
        self.assertEqual([(row["table"].name, row["orders"], row["guests"]) for row in report["tables"]], [("1", 1, 5)])


class ReportTests(SplitSetup):
    def test_after_the_split_table_1_shows_one_order_120_and_the_guests_once(self):
        self.vat(False)
        order = self.table_order()
        part = self.split(order, [(self.k45, 1), (self.t10, 1)])
        self.pay(part)
        midway = period_report(timezone.localdate(), timezone.localdate())
        self.assertEqual((midway["totals"]["orders"], midway["totals"]["sales"], midway["totals"]["guests"]), (1, D("55.00"), 2))
        self.pay(order)
        report = period_report(timezone.localdate(), timezone.localdate())
        self.assertEqual({key: report["totals"][key] for key in ("orders", "sales", "average", "guests", "minutes")},
                         {"orders": 1, "sales": D("120.00"), "average": D("120.00"), "guests": 2, "minutes": 0})
        self.assertEqual([(row["table"].name, row["orders"], row["sales"], row["guests"]) for row in report["tables"]], [("1", 1, D("120.00"), 2)])
        self.assertEqual([(row["kind"], row["orders"], row["sales"]) for row in report["kinds"]], [(OrderKind.DINE_IN, 1, D("120.00"))])
        self.assertEqual([(row["orders"], row["sales"]) for row in report["waiters"]], [(1, D("120.00"))])
        self.assertEqual(sum(row["orders"] for row in report["hours"]), 1)
        self.assertEqual({row["item"].item_code: (row["quantity"], row["sales"]) for row in report["dishes"]},
                         {"D-K45": (D("2.000"), D("90.00")), "D-T10": (D("3.000"), D("30.00"))})

    def test_the_dashboards_floor_tiles_count_a_split_table_once(self):
        from reports import activity_panel

        def tiles():
            with working_in(self.main):
                return {tile["key"]: tile["value"] for tile in activity_panel._kitchen(activity_panel.WORDS["en"], timezone.localdate())[0]}

        order = self.table_order()
        part = self.split(order, [(self.k45, 1), (self.t10, 1)])
        self.assertEqual({key: tiles()[key] for key in ("open_orders", "open_value", "tables_busy")}, {"open_orders": 1, "open_value": D("120.00"), "tables_busy": "1 of 3"})
        self.pay(part)
        self.pay(order)
        self.assertEqual({key: tiles()[key] for key in ("open_orders", "paid_today", "avg_order")}, {"open_orders": 0, "paid_today": 1, "avg_order": D("136.80")})


class ScreenTests(SplitSetup):
    def post(self, order, user=None, **data):
        self.client.force_login(user or self.owner)
        return self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", **data}, follow=True)

    def test_the_part_sheet_opens_a_part_and_both_pages_show_it(self):
        order = self.table_order()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:order", args=[order.pk]))
        self.assertContains(page, 'data-part-sheet')
        self.assertContains(page, "ادفع جزء")
        self.assertContains(page, f'name="part_{self.line(order, self.k45).pk}"')
        response = self.post(order, action="split", **{f"part_{self.line(order, self.k45).pk}": "1", f"part_{self.line(order, self.t10).pk}": "1"})
        part = Order.objects.get(split_from=order)
        self.assertEqual(response.redirect_chain[-1][0], f"{reverse('restaurant:order', args=[part.pk])}?lang=ar")
        self.assertContains(response, "جزء من حساب الطاولة 1")
        self.assertContains(response, "رجّع للحساب")
        self.assertNotContains(response, "data-part-sheet")  # a part is not split again
        self.assertNotContains(response, "data-merge")
        parent = self.client.get(reverse("restaurant:order", args=[order.pk]))
        self.assertContains(parent, f'data-part="{part.number}"')
        self.assertContains(parent, "55.00")
        board = self.client.get(reverse("restaurant:board"))
        self.assertContains(board, f'data-table-part="{part.number}"')
        self.assertNotContains(board, f'data-open-order="{part.number}"')  # never a stray takeaway
        paid = self.post(part, action="pay", cashbox=self.cashbox.pk, tendered="100", print="0")
        self.assertEqual(paid.redirect_chain[-1][0], f"{reverse('restaurant:order', args=[order.pk])}?lang=ar")  # back to the table's bill
        self.assertContains(paid, f'data-part-paid="{Order.objects.get(pk=part.pk).invoice.invoice_number}"')

    def test_put_back_and_merge_from_the_screen(self):
        order = self.table_order()
        part = self.split(order, [(self.k45, 1)])
        response = self.post(part, action="put_back")
        self.assertEqual(response.redirect_chain[-1][0], f"{reverse('restaurant:order', args=[order.pk])}?lang=ar")
        self.assertEqual(Order.objects.get(pk=part.pk).status, OrderStatus.MERGED)
        other = self.table_order(table=self.t2, guests=3)
        page = self.client.get(reverse("restaurant:order", args=[order.pk]))
        self.assertContains(page, "data-merge")
        self.assertContains(page, f'<option value="{other.pk}">')
        self.post(order, action="merge", other=other.pk)
        self.assertEqual((Order.objects.get(pk=other.pk).status, Order.objects.get(pk=order.pk).guests), (OrderStatus.MERGED, 5))
        self.assertContains(self.client.get(reverse("restaurant:order", args=[other.pk])), "اتدمج في")

    def test_a_table_that_never_splits_or_merges_looks_as_before(self):
        order = self.table_order()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:order", args=[order.pk]))
        self.assertNotContains(page, "data-parts")
        self.assertNotContains(page, "جزء من حساب")
        board = self.client.get(reverse("restaurant:board"))
        self.assertNotContains(board, "data-table-part")

    def test_a_branch_a_manager_cannot_split_merge_or_put_back_branch_bs_order(self):
        maadi = self.table_order(table=self.b_t1)
        other = self.table_order(table=self.t1)
        before = self.state()
        for data in ({"action": "split", f"part_{self.line(maadi, self.k45).pk}": "1"}, {"action": "merge", "other": other.pk}, {"action": "put_back"}):
            with self.subTest(data=data):
                self.client.force_login(self.manager_a)
                self.assertEqual(self.client.post(reverse("restaurant:order", args=[maadi.pk]), {"lang": "ar", **data}).status_code, 404)
        self.assertEqual(self.state(), before)
        response = self.post(maadi, user=self.manager_b, action="merge", other=other.pk)  # a crafted pick of another branch's order
        self.assertContains(response, "اختار الطاولة")
        self.assertEqual(self.state(), before)

    def test_english_words(self):
        order = self.table_order()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:order", args=[order.pk]) + "?lang=en")
        self.assertContains(page, "Pay a part")
        self.assertContains(page, "Merge with a table")
        part = self.split(order, [(self.k45, 1)], lang="en")
        self.assertContains(self.client.get(reverse("restaurant:order", args=[part.pk]) + "?lang=en"), "Part of the bill of table 1")


@skipUnless(connection.vendor == "postgresql", "row locks are PostgreSQL's: SQLite's writers already take turns")
class MergeLockTests(TransactionTestCase):
    """Two people merging table 1 into table 2 and table 2 into table 1 at the same moment: one merge happens, the other is
    refused, and neither waits for ever. Both lock the two orders in pk order. ``services._lock_one`` is slowed after each
    lock, so if the order were not fixed each would hold one row while asking for the other."""

    serialized_rollback = True

    def setUp(self):
        from reports.tests_dashboard import prepared_client

        prepared_client(activity="restaurants", sub_activity="cafe",
                        modules="items_services,sales_operations,cashboxes,reports,inventory,pdf_printing,tables_orders")
        self.owner = person(RoleCode.OWNER, "merge_lock_owner")
        make_location(location_code="HALL", is_default=True)
        make_cashbox(cashbox_code="TILL", is_default=True)
        tea = make_item(item_code="D-T10", item_name="شاي", default_sale_price="10.00", is_stock_tracked=False)
        self.one = services.open_order(self.owner, table=services.save_table({"name": "1"}, self.owner), guests=2)
        self.two = services.open_order(self.owner, table=services.save_table({"name": "2"}, self.owner), guests=3)
        for order in (self.one, self.two):
            services.add_item(order, tea, self.owner, 1)

    def test_a_to_b_and_b_to_a_at_once_merge_once_and_never_deadlock(self):
        start, outcome = threading.Barrier(2), {}
        real = services._lock_one

        def slow(pk):  # hold the first row a moment before asking for the second
            locked = real(pk)
            time.sleep(0.5)
            return locked

        def merge(name, into, other):
            try:
                start.wait()
                services.merge_orders(Order.objects.get(pk=into.pk), Order.objects.get(pk=other.pk), self.owner)
                outcome[name] = "merged"
            except ValidationError as exc:
                outcome[name] = "refused: " + " ".join(exc.messages)
            except Exception as exc:  # noqa: BLE001 - surfaced in the assertion message
                outcome[name] = f"error: {exc!r}"
            finally:
                connection.close()

        with mock.patch.object(services, "_lock_one", side_effect=slow):
            threads = [threading.Thread(target=merge, args=("1<-2", self.one, self.two)), threading.Thread(target=merge, args=("2<-1", self.two, self.one))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(value.split(":")[0] for value in outcome.values()), ["merged", "refused"], outcome)
        statuses = sorted(Order.objects.filter(pk__in=[self.one.pk, self.two.pk]).values_list("status", flat=True))
        self.assertEqual(statuses, [OrderStatus.MERGED, OrderStatus.OPEN])
        survivor = Order.objects.get(status=OrderStatus.OPEN)
        self.assertEqual((survivor.guests, survivor.lines.count(), survivor.total), (5, 2, D("20.00")))
