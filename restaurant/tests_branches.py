"""Restaurants R12 (HG-108): tables and orders belong to a branch, and a bill posts to its own branch's store and cashbox.

Two entities: the main one (its hall «HALL», cashbox «TILL») and the Maadi branch («BR-HALL», «BR-TILL»), each with a table
named «T1». The café kitchen of ``tests_kitchen``: a large koshary made in the kitchen from 0.200 kg of rice (30.00) and
0.100 kg of lentils (50.00), 11.00 a plate, sold at 60.00; a can of cola at 20.00 that costs 9.00. Rice, lentils and cola
are in stock in both halls. VAT is off, so a bill of two koshary and a cola is 140.00 exactly.

The clock stands still on a Wednesday at noon in Cairo: nothing here depends on the day or the hour.
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import expectedFailure, mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxMovement
from entities.current import SESSION_KEY, working_in
from entities.models import EntityMembership
from entities.services import main_entity, save_entity
from hesba_testing.factories import make_cashbox, make_item, make_location, open_test_shift, stock_in
from inventory.models import StockMovement
from inventory.services import get_item_location_stock_quantity, recalculate_item_average_cost
from ledger import reports as gl
from ledger.models import JournalLine
from ledger.projector import Projector
from manufacturing.models import ProductionRun
from permissions.models import RoleCode
from sales.models import SalesInvoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from . import kitchen, services
from .insights import period_report, save_ingredients
from .models import DiningTable, KitchenTicket, Order, OrderKind, OrderStatus
from .tests import RestaurantSetup, person

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)  # a Wednesday, 12:00 in Cairo


class BranchSetup(RestaurantSetup):
    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        self.main = main_entity()
        self.branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي", "name_en": "Maadi branch"}, self.owner)
        self.cashbox.name_ar = "خزنة الصالة"
        self.cashbox.save(update_fields=["name_ar"])
        self.b_location = make_location(location_code="BR-HALL", name_ar="صالة المعادي", entity=self.branch, is_selling_location=True, is_default=True)
        self.b_cashbox = make_cashbox(cashbox_code="BR-TILL", name_ar="خزنة المعادي", entity=self.branch, is_default=True)
        self.rice = make_item(item_code="R-RICE", item_name="أرز", unit="كجم", is_stock_tracked=True)
        self.lentil = make_item(item_code="R-LENT", item_name="عدس", unit="كجم", is_stock_tracked=True)
        for location, rice, lentil in ((self.location, 20, 10), (self.b_location, 10, 5)):
            stock_in(self.rice, location, rice, "30.00")
            stock_in(self.lentil, location, lentil, "50.00")
        stock_in(self.cola, self.b_location, 5, "9.00")
        for item in (self.rice, self.lentil, self.cola):
            recalculate_item_average_cost(item)
        save_ingredients(self.koshary, [(self.rice, "0.200"), (self.lentil, "0.100")], self.owner)  # 11.00 a plate
        self.koshary = kitchen.make_in_kitchen(self.koshary, self.owner)
        self.manager_a = person(RoleCode.MANAGER, "branch_a_manager")
        EntityMembership.objects.create(user=self.manager_a, entity=self.main, is_default=True)
        self.manager_b = person(RoleCode.MANAGER, "branch_b_manager")
        EntityMembership.objects.create(user=self.manager_b, entity=self.branch, is_default=True)
        open_test_shift(self.owner, self.cashbox)  # AN4: the bill is taken inside a shift (the owner's on the main hall's till)
        open_test_shift(self.manager_b, self.b_cashbox)

    # ---- helpers ------------------------------------------------------------------------------------------------

    def tables_t1(self):
        """«T1» in the main entity and «T1» in the branch."""

        self.a_t1 = services.save_table({"name": "T1", "seats": 4}, self.owner, entity=self.main)
        self.b_t1 = services.save_table({"name": "T1", "seats": 2}, self.owner, entity=self.branch)

    def bill(self, order):
        services.add_item(order, self.koshary, self.owner, 2)  # 2 × 60.00, made in the kitchen at payment
        services.add_item(order, self.cola, self.owner, 1)  # 20.00, a stock can
        return order

    def work_in(self, entity, user=None):
        self.client.force_login(user or self.owner)
        session = self.client.session
        if entity is None:
            session.pop(SESSION_KEY, None)
        else:
            session[SESSION_KEY] = entity.pk
        session.save()

    def stock(self, item, location):
        return get_item_location_stock_quantity(item, location)

    def moved(self):
        return {"invoices": SalesInvoice.objects.count(), "cash": CashboxMovement.objects.count(), "stock": StockMovement.objects.count(),
                "runs": ProductionRun.objects.count(), "orders": Order.objects.count(),
                "open": list(Order.objects.filter(status=OrderStatus.OPEN).values_list("pk", flat=True).order_by("pk"))}

    def pay_from_screen(self, order, cashbox, **extra):
        return self.client.post(reverse("restaurant:order", args=[order.pk]),
                                {"lang": "ar", "action": "pay", "cashbox": cashbox.pk, "tendered": "1000", **extra}, follow=True)


class GapTests(BranchSetup):
    """Before R12 (proof of the gap; replaced by the tests below once tables and orders carry their branch)."""

    def test_today_a_whole_group_bill_lands_on_the_main_hall_whatever_the_branch(self):
        order = self.bill(services.open_order(self.owner, table=self.t1, guests=2))
        self.work_in(None)
        page = self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": "ar"}).content.decode()
        self.assertIn(f'<option value="{self.b_cashbox.pk}"', page)  # whole group: every entity's cashbox is offered
        self.assertIn(f'<option value="{self.cashbox.pk}"', page)
        response = self.pay_from_screen(order, self.b_cashbox)
        order.refresh_from_db()
        invoice = order.invoice
        self.assertEqual((invoice.selling_location, invoice.cashbox, invoice.total_amount), (self.location, self.b_cashbox, D("140.00")))
        self.assertEqual(ProductionRun.objects.get().location, self.location)  # plates made in the main hall
        self.assertEqual((self.stock(self.rice, self.location), self.stock(self.rice, self.b_location)), (D("19.600"), D("10.000")))
        Projector().rebuild()
        self.assertEqual((gl.control_balance("sales", entity=self.main), gl.control_balance("sales", entity=self.branch)), (D("-140.00"), D("0.00")))
        self.assertEqual(CashboxMovement.objects.get(sales_invoice=invoice).cashbox, self.b_cashbox)  # the cash in the branch's till

    def test_today_a_branch_manager_sees_and_opens_the_main_halls_orders(self):
        order = services.open_order(self.owner, table=self.t1)
        self.work_in(None, self.manager_b)
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"}).content.decode()
        self.assertIn(reverse("restaurant:order", args=[order.pk]), board)
        self.assertEqual(self.client.get(reverse("restaurant:order", args=[order.pk])).status_code, 200)

    def test_today_a_second_branch_cannot_name_a_table_1(self):
        with working_in(self.branch), self.assertRaisesMessage(ValidationError, "فيه طاولة بنفس الاسم"):
            services.save_table({"name": "1"}, self.owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DiningTable.objects.create(name="1")


@expectedFailure
class TableTests(BranchSetup):
    def test_each_branch_has_its_own_T1_and_the_database_refuses_a_second_in_one_branch(self):
        self.tables_t1()
        self.assertEqual((self.a_t1.entity, self.b_t1.entity), (self.main, self.branch))
        with self.assertRaises(IntegrityError), transaction.atomic():
            DiningTable.objects.create(name="T1", entity=self.branch)  # the database itself, not only the form
        with self.assertRaises(IntegrityError), transaction.atomic():
            DiningTable.objects.create(name="T1")  # made without an entity: it belongs to the main one, which has a «T1»
        with self.assertRaisesMessage(ValidationError, "فيه طاولة بنفس الاسم"):
            services.save_table({"name": "t1"}, self.owner, entity=self.branch)  # the form, case-insensitively, within the branch
        DiningTable.objects.create(name="T2", entity=self.branch)
        self.assertEqual(DiningTable.objects.filter(name="T1").count(), 2)

    def test_a_table_made_without_an_entity_belongs_to_the_main_one(self):
        self.assertEqual(DiningTable.objects.create(name="Bar 1").entity, self.main)
        self.assertEqual((self.t1.entity, self.t2.entity), (self.main, self.main))
        with working_in(self.branch):
            self.assertEqual(services.save_table({"name": "Garden"}, self.owner).entity, self.branch)

    def test_a_free_table_can_move_to_another_branch_but_not_while_an_order_is_open(self):
        moved = services.save_table({"name": "1", "seats": 4}, self.owner, self.t1, entity=self.branch)
        self.assertEqual(moved.entity, self.branch)
        services.open_order(self.owner, table=self.t2)
        with self.assertRaisesMessage(ValidationError, "الطاولة دي عليها طلبات مفتوحة"):
            services.save_table({"name": "2", "seats": 2}, self.owner, self.t2, entity=self.branch)
        self.assertEqual(DiningTable.objects.get(pk=self.t2.pk).entity, self.main)

    def test_the_tables_page_lists_and_edits_its_own_branchs_tables_only(self):
        self.tables_t1()
        self.work_in(None, self.manager_a)
        page = self.client.get(reverse("restaurant:tables"), {"lang": "ar"})
        self.assertContains(page, f"edit={self.a_t1.pk}")
        self.assertNotContains(page, f"edit={self.b_t1.pk}")
        self.assertEqual(self.client.get(reverse("restaurant:tables"), {"lang": "ar", "edit": self.b_t1.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse("restaurant:tables"), {"lang": "ar", "pk": self.b_t1.pk, "name": "X", "seats": "2"}).status_code, 404)
        self.assertEqual(DiningTable.objects.get(pk=self.b_t1.pk).name, "T1")
        self.client.post(reverse("restaurant:tables"), {"lang": "ar", "name": "T9", "seats": "2", "active": "on", "entity": self.branch.pk})
        self.assertEqual(DiningTable.objects.get(name="T9").entity, self.main)  # a crafted branch is ignored: a manager works in their own

    def test_in_whole_group_mode_a_new_table_asks_for_its_branch(self):
        self.work_in(None)
        page = self.client.get(reverse("restaurant:tables"), {"lang": "ar"})
        self.assertContains(page, 'name="entity"')
        self.assertContains(page, "فرع المعادي")
        response = self.client.post(reverse("restaurant:tables"), {"lang": "ar", "name": "T5", "seats": "2", "active": "on"})
        self.assertContains(response, "اختار الفرع")
        self.assertFalse(DiningTable.objects.filter(name="T5").exists())
        self.client.post(reverse("restaurant:tables"), {"lang": "ar", "name": "T5", "seats": "2", "active": "on", "entity": self.branch.pk})
        self.assertEqual(DiningTable.objects.get(name="T5").entity, self.branch)


@expectedFailure
class OpeningTests(BranchSetup):
    def test_a_dine_in_order_belongs_to_its_tables_branch_a_takeaway_to_the_working_one(self):
        self.tables_t1()
        self.assertEqual(services.open_order(self.owner, table=self.b_t1).entity, self.branch)  # whole group: the table's branch
        self.assertEqual(services.open_order(self.owner, kind=OrderKind.TAKEAWAY).entity, self.main)
        self.assertEqual(services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.branch).entity, self.branch)
        with working_in(self.branch):
            self.assertEqual(services.open_order(self.owner, kind=OrderKind.TAKEAWAY).entity, self.branch)
            with self.assertRaisesMessage(ValidationError, "الطاولة دي تبع فرع تاني"):
                services.open_order(self.owner, table=self.a_t1)
        self.assertEqual(Order.objects.filter(table=self.a_t1).count(), 0)

    def test_moving_an_order_to_another_branchs_table_is_refused(self):
        self.tables_t1()
        order = services.open_order(self.owner, table=self.t1)
        with self.assertRaisesMessage(ValidationError, "الطاولة دي تبع فرع تاني"):
            services.move_table(order, self.b_t1, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.table, order.entity), (self.t1, self.main))

    def test_in_whole_group_mode_a_takeaway_asks_for_the_branch(self):
        self.work_in(None)
        before = Order.objects.count()
        response = self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "takeaway"}, follow=True)
        self.assertContains(response, "اختار الفرع")
        self.assertEqual(Order.objects.count(), before)
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "takeaway", "entity": self.branch.pk})
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "delivery", "entity": self.branch.pk, "address": "شارع 9"})
        self.assertEqual(list(Order.objects.order_by("pk").values_list("kind", "entity")),
                         [(OrderKind.TAKEAWAY, self.branch.pk), (OrderKind.DELIVERY, self.branch.pk)])

    def test_a_crafted_post_naming_another_branchs_table_opens_nothing(self):
        self.tables_t1()
        self.work_in(None, self.manager_a)
        before = self.moved()
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "dine_in", "table": self.b_t1.pk}, follow=True)
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "takeaway", "entity": self.branch.pk}, follow=True)
        self.assertEqual(Order.objects.filter(entity=self.branch).count(), 0)
        after = self.moved()
        self.assertEqual((after["invoices"], after["cash"], after["stock"]), (before["invoices"], before["cash"], before["stock"]))
        order = services.open_order(self.manager_a, table=self.a_t1)
        response = self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", "action": "move", "table": self.b_t1.pk}, follow=True)
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.table, self.a_t1)


@expectedFailure
class BillTests(BranchSetup):
    def test_the_owner_in_whole_group_mode_bills_branch_bs_T1_into_bs_store_cashbox_and_books(self):
        self.tables_t1()
        Projector().rebuild()
        main_books = gl.trial_balance(entity=self.main)
        order = self.bill(services.open_order(self.owner, table=self.b_t1, guests=2))
        self.work_in(None)
        page = self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": "ar"}).content.decode()
        self.assertIn(f'<option value="{self.b_cashbox.pk}"', page)  # the pay form lists the branch's cashboxes only
        self.assertNotIn(f'<option value="{self.cashbox.pk}"', page)
        self.pay_from_screen(order, self.b_cashbox)
        order.refresh_from_db()
        invoice = order.invoice
        self.assertEqual((order.status, order.entity), (OrderStatus.PAID, self.branch))
        self.assertEqual((invoice.selling_location, invoice.cashbox, invoice.total_amount, invoice.status), (self.b_location, self.b_cashbox, D("140.00"), "posted"))
        self.assertEqual([(m.cashbox_id, m.amount) for m in CashboxMovement.objects.filter(sales_invoice=invoice)], [(self.b_cashbox.pk, D("140.00"))])
        run = ProductionRun.objects.get()
        self.assertEqual((run.location, run.output_quantity), (self.b_location, D("2.000")))  # the plates were made in the branch
        self.assertEqual({key: self.stock(item, self.b_location) for key, item in (("rice", self.rice), ("lentil", self.lentil), ("cola", self.cola), ("plate", self.koshary))},
                         {"rice": D("9.600"), "lentil": D("4.800"), "cola": D("4.000"), "plate": D("0.000")})
        self.assertEqual({key: self.stock(item, self.location) for key, item in (("rice", self.rice), ("lentil", self.lentil), ("cola", self.cola))},
                         {"rice": D("20.000"), "lentil": D("10.000"), "cola": D("10.000")})  # the main hall is untouched
        Projector().rebuild()
        self.assertEqual(set(JournalLine.objects.filter(entry__source_type="sales_invoice", entry__source_id=invoice.pk).values_list("entity", flat=True)),
                         {self.branch.pk})
        self.assertEqual((gl.control_balance("sales", entity=self.branch), gl.control_balance("cogs", entity=self.branch)), (D("-140.00"), D("31.00")))
        self.assertEqual((gl.control_balance("sales", entity=self.main), gl.control_balance("cogs", entity=self.main)), (D("0.00"), D("0.00")))
        self.assertEqual(gl.trial_balance(entity=self.main), main_books)  # the main entity's books did not move
        self.assertTrue(gl.trial_balance(entity=self.branch)["balanced"])

    def test_a_branch_manager_bills_their_own_table_in_their_own_branch(self):
        self.tables_t1()
        order = self.bill(services.open_order(self.manager_b, table=self.b_t1))
        self.work_in(None, self.manager_b)
        self.pay_from_screen(order, self.b_cashbox)
        order.refresh_from_db()
        self.assertEqual((order.invoice.selling_location, order.invoice.cashbox, order.invoice.total_amount), (self.b_location, self.b_cashbox, D("140.00")))

    def test_a_crafted_pay_into_another_branchs_cashbox_moves_nothing(self):
        self.tables_t1()
        order = self.bill(services.open_order(self.owner, table=self.b_t1))
        before = self.moved()
        self.work_in(None)
        response = self.pay_from_screen(order, self.cashbox)  # the main hall's till on the branch's bill, in whole-group mode
        self.assertContains(response, "لازم يكون فيه مخزن بيع نشط")
        self.assertEqual(self.moved(), before)
        with self.assertRaisesMessage(ValidationError, "الخزنة دي تبع فرع تاني"):
            services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("1000"))
        self.assertEqual(self.moved(), before)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.OPEN)

    def test_the_restaurant_report_counts_each_branchs_bills_in_that_branch_only(self):
        self.tables_t1()
        services.pay(self.bill(services.open_order(self.owner, table=self.b_t1)), self.owner, cashbox=self.b_cashbox, tendered=D("1000"))
        day = timezone.localdate()
        with working_in(self.branch):
            self.assertEqual((period_report(day, day)["totals"]["orders"], period_report(day, day)["totals"]["sales"]), (1, D("140.00")))
        with working_in(self.main):
            self.assertEqual((period_report(day, day)["totals"]["orders"], period_report(day, day)["totals"]["sales"]), (0, D("0.00")))


class MainHallTests(BranchSetup):
    """Guard: with a second branch, a main-hall table bills exactly as before."""

    def test_a_main_table_still_bills_into_the_main_hall_and_till(self):
        order = self.bill(services.open_order(self.owner, table=self.t1))
        invoice, change = services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("200"))
        self.assertEqual((invoice.selling_location, invoice.cashbox, invoice.total_amount, change), (self.location, self.cashbox, D("140.00"), D("60.00")))
        self.assertEqual(ProductionRun.objects.get().location, self.location)


@expectedFailure
class ScopeTests(BranchSetup):
    def setUp(self):
        super().setUp()

    def prepare(self):
        self.tables_t1()
        self.a_order = services.open_order(self.owner, table=self.a_t1)
        services.add_item(self.a_order, self.tea, self.owner)
        self.b_order = services.open_order(self.owner, table=self.b_t1)
        services.add_item(self.b_order, self.tea, self.owner, 2)
        self.b_ticket = services.send_to_kitchen(self.b_order, self.owner)

    def test_a_branch_a_manager_gets_404_on_branch_bs_order_ticket_and_actions(self):
        self.prepare()
        self.work_in(None, self.manager_a)
        self.assertEqual(self.client.get(reverse("restaurant:order", args=[self.a_order.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("restaurant:order", args=[self.b_order.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("restaurant:ticket", args=[self.b_ticket.pk])).status_code, 404)
        lines = list(self.b_order.lines.values_list("pk", "quantity", "voided"))
        for data in ({"action": "add", "item": self.tea.pk}, {"action": "cancel", "reason": "x"}, {"action": "pay", "cashbox": self.cashbox.pk}):
            self.assertEqual(self.client.post(reverse("restaurant:order", args=[self.b_order.pk]), {"lang": "ar", **data}).status_code, 404)
        self.b_order.refresh_from_db()
        self.assertEqual((self.b_order.status, list(self.b_order.lines.values_list("pk", "quantity", "voided"))), (OrderStatus.OPEN, lines))

    def test_each_branch_sees_its_own_board(self):
        self.prepare()
        self.work_in(None, self.manager_a)
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"}).content.decode()
        self.assertIn(reverse("restaurant:order", args=[self.a_order.pk]), board)
        self.assertNotIn(reverse("restaurant:order", args=[self.b_order.pk]), board)
        self.work_in(None, self.manager_b)
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"}).content.decode()
        self.assertIn(reverse("restaurant:order", args=[self.b_order.pk]), board)
        self.assertNotIn(reverse("restaurant:order", args=[self.a_order.pk]), board)
        self.assertNotIn(f'value="{self.t2.pk}"', board)  # the main hall's free tables are not offered either
        self.assertNotIn("data-branch-group", board)  # a branch's own board has no branch headings

    def test_the_whole_group_board_is_grouped_by_branch(self):
        self.prepare()
        self.work_in(None)
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"}).content.decode()
        main_at, branch_at = board.index(f'data-branch-group="{self.main.code}"'), board.index('data-branch-group="BR2"')
        self.assertLess(main_at, branch_at)
        self.assertIn(reverse("restaurant:order", args=[self.a_order.pk]), board[main_at:branch_at])
        self.assertIn(reverse("restaurant:order", args=[self.b_order.pk]), board[branch_at:])
        self.assertIn('name="entity"', board)  # the takeaway and delivery forms ask for the branch
        english = self.client.get(reverse("restaurant:board"), {"lang": "en"}).content.decode()
        self.assertIn("Maadi branch", english)

    def test_a_kitchen_screen_shows_its_own_branchs_tickets(self):
        self.prepare()
        a_ticket = services.send_to_kitchen(self.a_order, self.owner)
        self.work_in(self.main)
        self.assertEqual([card["number"] for card in self.client.get(reverse("restaurant:kds_feed")).json()["tickets"]], [self.a_order.number])
        self.work_in(self.branch)
        self.assertEqual([card["number"] for card in self.client.get(reverse("restaurant:kds_feed")).json()["tickets"]], [self.b_order.number])
        self.assertEqual(self.client.post(reverse("restaurant:kds_act"), {"op": "done", "id": a_ticket.pk}).status_code, 404)
        self.assertEqual(KitchenTicket.objects.get(pk=a_ticket.pk).done_at, None)

    def test_a_send_routes_to_the_orders_branch_stations_whatever_entity_the_sender_works_in(self):
        self.prepare()
        from .stations import save_station

        with working_in(self.branch):
            far = save_station({"name_ar": "مطبخ المعادي", "is_default": True}, self.owner)
        save_station({"name_ar": "مطبخ الصالة", "is_default": True}, self.owner)
        services.add_item(self.b_order, self.koshary, self.owner)
        ticket = services.send_to_kitchen(self.b_order, self.owner)  # sent while working across the group
        self.assertEqual(ticket.station, far)


@expectedFailure
class SingleBranchTests(RestaurantSetup):
    def test_a_single_branch_restaurant_looks_as_before(self):
        self.client.force_login(self.owner)
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"}).content.decode()
        tables = self.client.get(reverse("restaurant:tables"), {"lang": "ar"}).content.decode()
        for page in (board, tables):
            self.assertNotIn("data-branch-group", page)
            self.assertNotIn('name="entity"', page)
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "takeaway"})
        self.assertEqual(Order.objects.get().entity, main_entity())


@expectedFailure
class MigrationTests(TransactionTestCase):
    """0005 on a database that already holds tables and orders: every row gets an entity, and no amount changes."""

    serialized_rollback = True
    before, after = ("restaurant", "0004_kitchen_stations"), ("restaurant", "0005_branches")

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def old_rows(self, with_main):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([self.before])
        leaves = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "restaurant"] + [self.before]
        old = executor.loader.project_state(leaves).apps
        Entity = old.get_model("entities", "Entity")
        Entity.objects.filter(is_main=True).exclude(code="MAIN").delete()
        main = Entity.objects.filter(is_main=True).first()
        if with_main and main is None:
            main = Entity.objects.create(code="MAIN", name_ar="الرئيسي", is_main=True)
        if not with_main:
            Entity.objects.filter(is_main=True).delete()
        branch = Entity.objects.create(code="BRM", name_ar="فرع قديم")
        Location = old.get_model("master_data", "Location")
        home = Location.objects.create(location_code="M-HOME", name_ar="قديم", entity=main if with_main else None)
        far = Location.objects.create(location_code="M-FAR", name_ar="فرع", entity=branch)
        user = old.get_model("auth", "User").objects.create(username="mig_r12")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="C-R12", name="عميل قديم")
        item = old.get_model("master_data", "Item").objects.create(item_code="I-R12", item_name="طبق قديم", is_stock_tracked=False)
        Table, Order, Line = (old.get_model("restaurant", name) for name in ("DiningTable", "Order", "OrderLine"))
        Invoice = old.get_model("sales", "SalesInvoice")
        t1, t2 = Table.objects.create(name="1"), Table.objects.create(name="2")
        rows = []
        for number, kind, table, status, location, total in (("R-1", "dine_in", t1, "paid", far, "150.00"), ("R-2", "takeaway", None, "paid", home, "75.50"),
                                                             ("R-3", "dine_in", t2, "open", None, None), ("R-4", "delivery", None, "cancelled", None, None)):
            invoice = None
            if location is not None:
                invoice = Invoice.objects.create(invoice_number=f"POS-{number}", invoice_date=NOW.date(), customer=customer, selling_location=location,
                                                 status="posted", subtotal=D(total), total_amount=D(total), created_by=user)
            order = Order.objects.create(number=number, kind=kind, table=table, status=status, invoice=invoice, opened_by=user, delivery_fee=D("0"))
            Line.objects.create(order=order, item=item, quantity=D("2.500"), unit_price=D("30.20"))
            rows.append(order.pk)
        totals = sorted(Invoice.objects.values_list("invoice_number", "total_amount"))
        return {"main": main.pk if main else None, "branch": branch.pk, "orders": rows, "tables": (t1.pk, t2.pk), "totals": totals}

    def forward(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([self.after])
        return executor.loader.project_state([self.after]).apps

    def check(self, made):
        new = self.forward()
        Entity = new.get_model("entities", "Entity")
        main = Entity.objects.get(is_main=True).pk
        if made["main"] is not None:
            self.assertEqual(main, made["main"])
        Table, Order, Line = (new.get_model("restaurant", name) for name in ("DiningTable", "Order", "OrderLine"))
        self.assertFalse(Table.objects.filter(entity__isnull=True).exists())
        self.assertFalse(Order.objects.filter(entity__isnull=True).exists())
        self.assertEqual(list(Table.objects.filter(pk__in=made["tables"]).order_by("pk").values_list("entity", flat=True)), [main, main])
        self.assertEqual([Order.objects.get(pk=pk).entity_id for pk in made["orders"]], [made["branch"], main, main, main])
        self.assertEqual(sorted(new.get_model("sales", "SalesInvoice").objects.values_list("invoice_number", "total_amount")), made["totals"])
        self.assertEqual(sorted(Line.objects.values_list("quantity", "unit_price")), [(D("2.500"), D("30.20"))] * 4)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Table.objects.create(name="1", entity_id=main)
        Table.objects.create(name="1", entity_id=made["branch"])

    def test_existing_tables_and_orders_get_an_entity_and_no_amount_changes(self):
        self.check(self.old_rows(with_main=True))

    def test_with_no_main_entity_yet_the_migration_makes_it(self):
        self.check(self.old_rows(with_main=False))
