"""Manufacturing F3 (HG-131): a lot and an expiry on every production run, and tracing from ingredient to customer.

The bakery: a recipe makes 10 cakes from 10 kg of flour (a batch), so an order for 20 cakes needs 20 kg. Flour LOT-A is
30 kg expiring in 16 days and LOT-B is 50 kg expiring in 106 days (dates are the test clock's today plus days, never a
literal date: the runner pins one Cairo day, and nothing here may expire by accident as real time moves).

The batch screens do not book lots per movement: they hand the on-hand quantity to the latest-expiring lots first, so
what leaves is the unbatched stock and then the earliest-expiring lot. ``LotUse`` is the positions before an issue
minus the positions after it.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from barcode import designs as label_designs
from batches.models import Batch
from batches.services import batch_positions, counted_batches, register_batch
from cashboxes.models import CashboxMovement
from hesba_testing.factories import (
    add_purchase_line,
    make_cashbox,
    make_customer,
    make_draft_purchase_invoice,
    make_item,
    make_location,
    make_supplier,
    stock_in,
)
from inventory.models import StockAdjustmentDirection, StockMovement, StockOperation
from inventory.services import adjust_stock, get_item_authoritative_average_cost, get_item_location_stock_quantity
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from purchases.services import post_purchase_invoice
from batches.services import attach_purchase_batches
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled

from . import lots, orders, services
from .models import LotUse, OrderStatus, ProductionRun, RunStatus
from .tests import person

TODAY = timezone.localdate()
A_EXPIRES, B_EXPIRES = TODAY + timedelta(days=16), TODAY + timedelta(days=106)
EXPIRED_AR = "دقيق (كيلو) LOT-A خلص تاريخه؛ اشطبه الأول"
EXPIRED_EN = "دقيق (كيلو) LOT-A has expired; write it off first"


class LotSetup(TestCase):
    MODULES = "customers,items_services,sales_operations,purchases,inventory,cashboxes,reports,manufacturing"
    SHELF_DAYS = "10"

    def setUp(self):
        self.profile = prepared_client(activity="manufacturing", sub_activity="food", modules=self.MODULES)
        set_capability_enabled(self.profile, "batches_expiry", True)
        self.owner = person(RoleCode.OWNER, "lot_owner")
        self.plant = make_location(location_code="PLANT", is_default=True, is_receiving_location=True)
        self.cashbox = make_cashbox()
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق (كيلو)", is_stock_tracked=True)
        self.cake = make_item(item_code="FG-CAKE", item_name="كيكة", is_stock_tracked=True, default_sale_price="120")
        # One batch is 10 kg of flour for 10 cakes; a cake keeps 10 days.
        self.recipe = services.save_recipe({"product": self.cake, "output_quantity": "10", "shelf_life_days": self.SHELF_DAYS},
                                           [(self.flour, "10")], self.owner)

    # ---- the flour ----

    def lot(self, number, expires, quantity):
        """A flour lot registered by hand (a lot that came on a purchase is ``bought``)."""

        return register_batch(item=self.flour, location=self.plant, batch_no=number, expiry_date=expires, quantity=D(quantity),
                              received_on=TODAY, user=self.owner)

    def bought(self, number, expires, quantity, supplier, invoice_number):
        """Flour received on a posted purchase invoice, with its lot and expiry typed on the line."""

        invoice = make_draft_purchase_invoice(supplier=supplier, location=self.plant, invoice_number=invoice_number, invoice_date=TODAY)
        add_purchase_line(invoice, self.flour, quantity, "20.00")
        attach_purchase_batches(invoice, [{"batch_no": number, "expiry_date": expires}], self.owner)
        post_purchase_invoice(invoice.pk, self.owner)
        return invoice

    def on_hand(self):
        return get_item_location_stock_quantity(self.flour, self.plant)

    def positions(self):
        """({lot number: what is left}, the unbatched quantity) for the flour at the plant."""

        rows, uncovered = batch_positions(items=[self.flour], locations=[self.plant])
        return {row["batch"].batch_no: row["remaining"] for row in rows}, uncovered.get(self.flour.pk, D("0"))

    # ---- the floor ----

    def order(self, quantity="20", recipe=None):
        return orders.create_order({"recipe": recipe or self.recipe, "location": self.plant, "quantity": quantity, "labor_cost": "0",
                                    "overhead_cost": "0"}, self.owner)

    def walk(self, order, **finish):
        for _ in order.stages:
            orders.advance(order, self.owner)
        orders.finish(order, self.owner, **finish)
        order.refresh_from_db()
        return order

    def uses(self, **filters):
        """{lot number or None for "no lot": quantity} of the flour a run or an order used."""

        rows = LotUse.objects.filter(item=self.flour, **filters).select_related("batch")
        return {(row.batch.batch_no if row.batch else None): row.quantity for row in rows}

    def sell(self, quantity, customer, item=None):
        from sales.pos import checkout

        invoice, _ = checkout(lines=[{"item": item or self.cake, "quantity": D(quantity), "unit_sale_price": D("120"), "line_discount_amount": D("0"),
                                      "description": ""}], customer=customer, location=self.plant, cashbox=self.cashbox, discount=D("0"),
                              tendered=D(quantity) * D("120"), user=self.owner)
        return invoice

    def footprint(self):
        """Every table stock, cost, cash or the ledger is written to, after the books are rebuilt from the movements."""

        Projector().rebuild()
        return {"movements": StockMovement.objects.count(), "operations": StockOperation.objects.count(),
                "cashbox": CashboxMovement.objects.count(), "journal_entries": JournalEntry.objects.count(),
                "journal_lines": JournalLine.objects.count()}


class IngredientLotTests(LotSetup):
    def test_an_order_takes_the_earliest_expiring_lot_first_and_records_it(self):
        self.lot("LOT-A", A_EXPIRES, "30")
        self.lot("LOT-B", B_EXPIRES, "50")
        stock_in(self.flour, self.plant, 60, "20.00", movement_date=TODAY)
        self.assertEqual(self.positions(), ({"LOT-A": D("10"), "LOT-B": D("50")}, D("0")))
        order = self.order()
        orders.advance(order, self.owner)                       # the start issues the flour
        self.assertEqual(self.uses(order=order), {"LOT-A": D("10.000"), "LOT-B": D("10.000")})
        self.assertEqual(self.on_hand(), D("40"))
        self.assertEqual(self.positions(), ({"LOT-A": D("0"), "LOT-B": D("40")}, D("0")))
        # Written at the issue and not computed later: the rows survive the stock moving on, and the finish hands them to the run.
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)
        for _ in order.stages[1:]:
            orders.advance(order, self.owner)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(self.uses(run=order.run), {"LOT-A": D("10.000"), "LOT-B": D("10.000")})
        self.assertEqual(LotUse.objects.filter(order=order, run__isnull=True).count(), 0)

    def test_unbatched_stock_leaves_first_then_the_earliest_lot(self):
        self.lot("LOT-A", A_EXPIRES, "30")
        self.lot("LOT-B", B_EXPIRES, "50")
        stock_in(self.flour, self.plant, 70, "20.00", movement_date=TODAY)
        self.assertEqual(self.positions(), ({"LOT-A": D("20"), "LOT-B": D("50")}, D("0")))
        stock_in(self.flour, self.plant, 15, "20.00", movement_date=TODAY)       # arrives without a lot
        self.assertEqual(self.positions(), ({"LOT-A": D("30"), "LOT-B": D("50")}, D("5")))
        order = self.order()
        orders.advance(order, self.owner)
        self.assertEqual(self.uses(order=order), {None: D("5.000"), "LOT-A": D("15.000")})

    def test_every_component_adds_up_to_what_was_issued_even_with_no_lots_at_all(self):
        stock_in(self.flour, self.plant, 60, "20.00", movement_date=TODAY)
        order = self.order()
        orders.advance(order, self.owner)
        self.assertEqual(self.uses(order=order), {None: D("20.000")})              # a component with no lot is one full "no lot" row
        issued = sum((issue.operation.quantity for issue in order.issues.all()), D("0"))
        self.assertEqual(sum((row.quantity for row in LotUse.objects.filter(order=order)), D("0")), issued)

    def test_a_plain_run_records_its_own_lots(self):
        self.lot("LOT-A", A_EXPIRES, "30")
        self.lot("LOT-B", B_EXPIRES, "50")
        stock_in(self.flour, self.plant, 60, "20.00", movement_date=TODAY)
        run = lots.produce(self.recipe, self.owner, batches="2", location=self.plant)
        self.assertEqual(self.uses(run=run), {"LOT-A": D("10.000"), "LOT-B": D("10.000")})
        self.assertEqual(run.product_lot.quantity, D("20.000"))

    def test_the_restaurant_kitchen_keeps_calling_the_service_and_records_no_lot(self):
        stock_in(self.flour, self.plant, 60, "20.00", movement_date=TODAY)
        run = services.produce(self.recipe, self.owner, batches="2", location=self.plant)   # what restaurant/kitchen.py does; a restaurant checkout is never refused for a flour lot
        self.assertFalse(Batch.objects.filter(production_run=run).exists())
        self.assertFalse(LotUse.objects.exists())


class ExpiredIngredientTests(LotSetup):
    def setUp(self):
        super().setUp()
        self.lot("LOT-A", TODAY - timedelta(days=1), "30")      # expired yesterday
        self.lot("LOT-B", B_EXPIRES, "50")
        stock_in(self.flour, self.plant, 60, "20.00", movement_date=TODAY)

    def test_the_start_is_refused_and_nothing_moves(self):
        order = self.order()
        before = (StockMovement.objects.count(), StockOperation.objects.count(), self.on_hand())
        with self.assertRaisesMessage(ValidationError, EXPIRED_AR):
            orders.advance(order, self.owner)
        with self.assertRaisesMessage(ValidationError, EXPIRED_EN):
            orders.advance(order, self.owner, lang="en")
        order.refresh_from_db()
        self.assertEqual((order.status, order.stage_index, order.issues.count(), order.stage_logs.count()), (OrderStatus.PLANNED, 0, 0, 0))
        self.assertEqual((StockMovement.objects.count(), StockOperation.objects.count(), self.on_hand()), before)
        self.assertFalse(LotUse.objects.exists())

    def test_a_plain_run_is_refused_too(self):
        with self.assertRaisesMessage(ValidationError, EXPIRED_AR):
            lots.produce(self.recipe, self.owner, batches="2", location=self.plant)
        self.assertEqual((self.on_hand(), StockOperation.objects.count()), (D("60.000"), 0))
        self.assertFalse(Batch.objects.filter(production_run__isnull=False).exists())

    def test_writing_the_lot_off_first_lets_the_start_through(self):
        adjust_stock("WO-1", TODAY, self.flour, self.plant, StockAdjustmentDirection.OUT, D("10"), "اشطب LOT-A", self.owner)
        order = self.order()
        orders.advance(order, self.owner)
        self.assertEqual(self.uses(order=order), {"LOT-B": D("20.000")})

    def test_unbatched_stock_never_refuses_and_an_unreached_expired_lot_does_not_either(self):
        stock_in(self.flour, self.plant, 40, "20.00", movement_date=TODAY)       # 100 on hand: B 50, A 30, no lot 20
        self.assertEqual(self.positions(), ({"LOT-A": D("30"), "LOT-B": D("50")}, D("20")))
        order = self.order()
        orders.advance(order, self.owner)                                          # takes the 20 unbatched kg only
        self.assertEqual(self.uses(order=order), {None: D("20.000")})

    def test_expiring_today_is_still_fine(self):
        Batch.objects.filter(batch_no="LOT-A").update(expiry_date=TODAY)
        order = self.order()
        orders.advance(order, self.owner)
        self.assertEqual(self.uses(order=order), {"LOT-A": D("10.000"), "LOT-B": D("10.000")})


class ProductLotTests(LotSetup):
    def setUp(self):
        super().setUp()
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)

    def test_the_finish_makes_the_cakes_their_lot_and_batches_shows_it_as_from_the_run(self):
        order = self.walk(self.order())
        lot = order.run.product_lot
        self.assertEqual((lot.item, lot.location, lot.batch_no, lot.quantity, lot.received_on), (self.cake, self.plant, order.run.number, D("20.000"), order.run.run_date))
        self.assertEqual(lot.expiry_date, order.run.run_date + timedelta(days=10))
        self.assertIn(lot, counted_batches())
        self.client.force_login(self.owner)
        page = self.client.get(reverse("batches:index"), {"lang": "ar"})
        self.assertContains(page, "من تشغيلة إنتاج")
        self.assertContains(page, "data-batch-run")
        self.assertContains(page, order.run.number)
        self.assertNotContains(page, "مسجلة يدويًا")          # the cake lot is not "registered by hand"
        self.assertNotContains(page, 'name="retire"')         # and a run's lot cannot be removed from that screen
        english = self.client.get(reverse("batches:index"), {"lang": "en"})
        self.assertContains(english, "From a production run")

    def test_a_lot_of_a_run_cannot_be_retired_by_hand(self):
        run = self.walk(self.order()).run
        self.client.force_login(self.owner)
        response = self.client.post(reverse("batches:index"), {"retire": run.product_lot.pk, "lang": "ar"})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Batch.objects.get(pk=run.product_lot.pk).active)

    def test_cancelling_the_run_stops_the_lot_counting_without_deleting_it(self):
        run = self.walk(self.order()).run
        lot_pk = run.product_lot.pk
        self.assertEqual(batch_positions(items=[self.cake])[0][0]["remaining"], D("20"))
        services.cancel_run(run, self.owner, reason="غلطة")
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.CANCELLED)
        self.assertTrue(Batch.objects.filter(pk=lot_pk).exists())                 # never deleted
        self.assertNotIn(lot_pk, counted_batches().values_list("pk", flat=True))
        self.assertEqual(batch_positions(items=[self.cake]), ([], {}))
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("batches:index"), {"all": "1"}), run.number)

    def test_counted_batches_before_and_after(self):
        """The filter's four cases: by hand, a posted purchase, a draft purchase, a run that is posted and one cancelled."""

        by_hand = self.lot("HAND", B_EXPIRES, "5")
        self.bought("BOUGHT", B_EXPIRES, "5", make_supplier(), "PI-X")
        draft = make_draft_purchase_invoice(supplier=make_supplier("SUP-D"), location=self.plant, invoice_number="PI-D", invoice_date=TODAY)
        add_purchase_line(draft, self.flour, "5", "20.00")
        attach_purchase_batches(draft, [{"batch_no": "DRAFT", "expiry_date": B_EXPIRES}], self.owner)
        kept = self.walk(self.order()).run
        cancelled = self.walk(self.order()).run
        services.cancel_run(cancelled, self.owner, reason="x")
        counted = set(counted_batches().values_list("batch_no", flat=True))
        self.assertEqual(counted, {"HAND", "BOUGHT", kept.number})
        self.assertIn(by_hand.batch_no, counted)

    def test_the_lot_number_and_expiry_can_be_typed_at_the_finish_and_on_a_plain_run(self):
        order = self.order()
        for _ in order.stages:
            orders.advance(order, self.owner)
        orders.finish(order, self.owner, lot_no="BATCH-77", lot_expiry=TODAY + timedelta(days=3))
        order.refresh_from_db()
        self.assertEqual((order.run.product_lot.batch_no, order.run.product_lot.expiry_date), ("BATCH-77", TODAY + timedelta(days=3)))
        run = lots.produce(self.recipe, self.owner, batches="1", location=self.plant, lot_no="PLAIN-1")
        self.assertEqual((run.product_lot.batch_no, run.product_lot.expiry_date), ("PLAIN-1", run.run_date + timedelta(days=10)))
        with self.assertRaises(ValidationError):                                   # an expiry before the day it is made
            lots.produce(self.recipe, self.owner, batches="1", location=self.plant, lot_expiry=TODAY - timedelta(days=1))

    def test_no_shelf_life_means_no_expiry(self):
        services.save_recipe({"product": self.cake, "output_quantity": "10", "shelf_life_days": ""}, [(self.flour, "10")], self.owner, self.recipe)
        self.recipe.refresh_from_db()
        self.assertIsNone(self.recipe.shelf_life_days)
        run = self.walk(self.order()).run
        self.assertIsNone(run.product_lot.expiry_date)

    def test_other_callers_of_save_recipe_leave_the_shelf_life_alone(self):
        services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(self.flour, "10")], self.owner, self.recipe)   # the MRP, the demo
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.shelf_life_days, 10)
        for bad in ("0", "-3", "x", "1.5"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                services.save_recipe({"product": self.cake, "output_quantity": "10", "shelf_life_days": bad}, [(self.flour, "10")], self.owner, self.recipe)

    def test_the_product_expiry_is_not_capped_by_an_ingredient_that_expires_sooner_and_the_run_page_warns(self):
        self.lot("LOT-A", A_EXPIRES, "130")                                         # 100 on hand: all of it is LOT-A
        services.save_recipe({"product": self.cake, "output_quantity": "10", "shelf_life_days": "30"}, [(self.flour, "10")], self.owner, self.recipe)
        run = self.walk(self.order()).run
        self.assertEqual(run.product_lot.expiry_date, run.run_date + timedelta(days=30))          # past LOT-A's 16 days, untouched
        self.client.force_login(self.owner)
        page = self.client.get(reverse("manufacturing:run", args=[run.pk]), {"lang": "ar"})
        self.assertContains(page, "data-lot-warning")
        self.assertContains(page, "دقيق (كيلو) (LOT-A)")
        self.assertContains(self.client.get(reverse("manufacturing:run", args=[run.pk]), {"lang": "en"}), "Note: a material expires before the product does")

    def test_no_warning_when_the_product_expires_first(self):
        self.lot("LOT-A", A_EXPIRES, "130")
        run = self.walk(self.order()).run                                           # a cake keeps 10 days, LOT-A 16
        self.client.force_login(self.owner)
        page = self.client.get(reverse("manufacturing:run", args=[run.pk]))
        self.assertNotContains(page, "data-lot-warning")
        self.assertContains(page, 'data-product-lot')
        self.assertContains(page, 'data-lot-use="RM-FLOUR"')


class BatchesOffTests(LotSetup):
    def setUp(self):
        super().setUp()
        set_capability_enabled(self.profile, "batches_expiry", False)
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)

    def test_no_lot_is_created_and_nothing_else_changes(self):
        order = self.walk(self.order())
        run = lots.produce(self.recipe, self.owner, batches="1", location=self.plant)
        self.assertEqual((Batch.objects.count(), LotUse.objects.count()), (0, 0))
        self.assertEqual((order.status, order.run.output_quantity, run.output_quantity), (OrderStatus.DONE, D("20.000"), D("10.000")))
        self.assertEqual(self.on_hand(), D("70.000"))
        self.assertEqual(get_item_location_stock_quantity(self.cake, self.plant), D("30.000"))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("manufacturing:run", args=[run.pk]))
        self.assertNotContains(page, "data-product-lot")
        self.assertNotContains(page, "data-lot-uses")
        self.assertNotContains(self.client.get(reverse("manufacturing:recipe", args=[self.recipe.pk])), "data-shelf-life")

    def test_an_expired_hand_registered_lot_does_not_stop_a_factory_that_switched_batches_off(self):
        self.lot("LOT-A", TODAY - timedelta(days=5), "30")
        order = self.order()
        orders.advance(order, self.owner)                                           # no refusal, no record
        order.refresh_from_db()
        self.assertEqual((order.status, LotUse.objects.count()), (OrderStatus.IN_PROGRESS, 0))


class NoMoneyEffectTests(LotSetup):
    def test_stock_cost_cash_and_journal_are_the_same_with_lots_as_without(self):
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)
        self.lot("LOT-A", A_EXPIRES, "30")
        self.lot("LOT-B", B_EXPIRES, "50")

        def one_run():
            before = self.footprint()
            run = self.walk(self.order()).run
            after = self.footprint()
            return ({key: after[key] - before[key] for key in after}, run.total_cost, run.output_operation.unit_cost,
                    get_item_authoritative_average_cost(self.cake), self.on_hand(), get_item_location_stock_quantity(self.cake, self.plant))

        set_capability_enabled(self.profile, "batches_expiry", False)
        without = one_run()
        set_capability_enabled(self.profile, "batches_expiry", True)
        with_lots = one_run()
        self.assertEqual(without[0], with_lots[0])                       # the same rows written (stock, operations, cash, journal)
        self.assertEqual(without[1:4], with_lots[1:4])                   # the run's cost, its unit cost, the product's average
        self.assertEqual(with_lots[4], without[4] - D("20"))             # and the second run took its own 20 kg
        self.assertEqual(with_lots[5], without[5] + D("20"))
        self.assertEqual(with_lots[0]["cashbox"], 0)
        self.assertEqual(with_lots[1], D("400.00"))                      # 20 kg at 20.00


class TraceTests(LotSetup):
    """LOT-A and LOT-B came on two posted purchases; an order uses 10 kg of each and its 20 cakes are sold to two customers."""

    def setUp(self):
        super().setUp()
        self.mill, self.delta = make_supplier("SUP-M", name="مطاحن الأمل"), make_supplier("SUP-D", name="شركة الدلتا")
        self.invoice_a = self.bought("LOT-A", A_EXPIRES, "30", self.mill, "PI-A")
        self.invoice_b = self.bought("LOT-B", B_EXPIRES, "50", self.delta, "PI-B")
        adjust_stock("PRE-1", TODAY, self.flour, self.plant, StockAdjustmentDirection.OUT, D("20"), "سحب", self.owner)   # draws LOT-A down first
        self.assertEqual(self.positions(), ({"LOT-A": D("10"), "LOT-B": D("50")}, D("0")))
        self.order_done = self.walk(self.order())
        self.run = self.order_done.run
        self.cake_lot = self.run.product_lot
        self.lot_a, self.lot_b = Batch.objects.get(batch_no="LOT-A"), Batch.objects.get(batch_no="LOT-B")
        self.ali, self.mona, self.hala = (make_customer("C-ALI", name="علي"), make_customer("C-MONA", name="منى"), make_customer("C-HALA", name="هالة"))
        self.sale_1, self.sale_2 = self.sell("12", self.ali), self.sell("8", self.mona)       # the lot is used up by the second
        # A second run of cakes comes later and a third customer buys from it: not from LOT-A's cakes.
        self.second = lots.produce(self.recipe, self.owner, batches="2", location=self.plant)
        self.sale_3 = self.sell("5", self.hala)
        self.client.force_login(self.owner)

    def trace(self, **params):
        return self.client.get(reverse("manufacturing:trace"), {"lang": "ar", **params})

    def test_backward_from_the_cake_lot_lists_the_flour_lots_and_their_purchases(self):
        self.assertEqual(self.uses(run=self.run), {"LOT-A": D("10.000"), "LOT-B": D("10.000")})
        page = self.trace(lot=self.cake_lot.pk)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-trace-backward")
        for text in ("LOT-A", "LOT-B", "PI-A", "PI-B", "مطاحن الأمل", "شركة الدلتا"):
            self.assertContains(page, text)
        rows = page.context["backward"]
        self.assertEqual({(row["batch"].batch_no, row["quantity"], row["source"].invoice_number, row["source"].supplier.name) for row in rows},
                         {("LOT-A", D("10.000"), "PI-A", "مطاحن الأمل"), ("LOT-B", D("10.000"), "PI-B", "شركة الدلتا")})
        self.assertContains(page, "data-report-tools")              # the Excel button of every reading screen

    def test_forward_from_lot_a_lists_the_cake_lot_and_the_two_sales_with_their_customers(self):
        page = self.trace(lot=self.lot_a.pk)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "حسب أقرب انتهاء")
        self.assertContains(page, self.run.number)
        self.assertContains(page, "data-trace-forward")
        sold = [(sale["invoice"].invoice_number, sale["customer"].name, sale["quantity"]) for sale in page.context["paths"][0]["sold"]["sales"]]
        self.assertEqual(sold, [(self.sale_1.invoice_number, "علي", D("12.000")), (self.sale_2.invoice_number, "منى", D("8.000"))])
        for text in (self.sale_1.invoice_number, self.sale_2.invoice_number, "علي", "منى"):
            self.assertContains(page, text)
        self.assertNotContains(page, self.sale_3.invoice_number)     # sold from the second run, after the lot was used up
        self.assertNotContains(page, "هالة")
        self.assertEqual(len(page.context["paths"]), 1)
        self.assertFalse(page.context["paths"][0]["sold"]["open"])
        self.assertEqual(self.client.get(reverse("manufacturing:trace"), {"lang": "en", "lot": self.lot_a.pk}).status_code, 200)

    def test_lot_b_went_into_the_same_cakes_and_the_search_finds_a_lot_by_its_number(self):
        page = self.trace(lot=self.lot_b.pk)
        self.assertEqual({path["run"] for path in page.context["paths"]}, {self.run, self.second})      # LOT-B also fed the second run
        found = self.trace(q="LOT-A")
        self.assertContains(found, f'data-found-lot="{self.lot_a.pk}"')
        self.assertNotContains(found, f'data-found-lot="{self.lot_b.pk}"')
        self.assertContains(self.trace(), "data-trace-found")          # the bare screen: the latest lots
        self.assertEqual(self.trace(run=self.run.pk).context["lot"], self.cake_lot)

    def test_a_cancelled_run_and_a_cancelled_order_are_not_paths_and_a_lot_that_does_not_count_says_so(self):
        paths = lambda: len(self.trace(lot=self.lot_b.pk).context["paths"])
        self.assertEqual(paths(), 2)                                   # the two runs so far
        third = lots.produce(self.recipe, self.owner, batches="1", location=self.plant)
        self.assertEqual(paths(), 3)
        services.cancel_run(third, self.owner, reason="x")             # nothing of it was sold
        self.assertEqual(paths(), 2)
        order = self.order("10")
        orders.advance(order, self.owner)                              # an order on the floor shows as such, with no cake lot yet
        page = self.trace(lot=self.lot_b.pk)
        self.assertEqual(len(page.context["paths"]), 3)
        self.assertContains(page, order.number)
        orders.cancel(order, self.owner, "تراجع")
        self.assertEqual(paths(), 2)
        self.assertContains(self.trace(lot=third.product_lot.pk), "data-trace-not-counted")

    def test_names_follow_the_purchase_and_sales_permissions(self):
        from hesba_testing.factories import make_user, make_user_profile
        from permissions.role_services import create_role

        # The stock keeper may read purchase invoices but not sales invoices.
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "lot_keeper"))
        back = self.trace(lot=self.cake_lot.pk)
        self.assertContains(back, "PI-A")
        self.assertContains(back, "مطاحن الأمل")
        forward = self.trace(lot=self.lot_a.pk)
        self.assertContains(forward, self.run.number)
        self.assertNotContains(forward, "علي")
        self.assertNotContains(forward, self.sale_1.invoice_number)
        self.assertNotContains(forward, "data-forward-sale")
        # A role with the stock screens only sees the lots, neither suppliers nor purchase invoices.
        role = create_role(name_ar="مراقب مخزن", codes=["inventory.view_stock"], actor=self.owner)
        viewer = make_user(username="lot_viewer")
        make_user_profile(user=viewer, role=role)
        self.client.force_login(viewer)
        bare = self.trace(lot=self.cake_lot.pk)
        self.assertContains(bare, "LOT-A")
        for text in ("PI-A", "PI-B", "مطاحن الأمل", "شركة الدلتا"):
            self.assertNotContains(bare, text)


class MultiLevelTraceTests(LotSetup):
    """Flour makes dough (2 kg of flour make 3 kg), dough makes layer cakes (3 kg of dough make 10): a recall goes through both."""

    def setUp(self):
        super().setUp()
        self.dough = make_item(item_code="SF-DOUGH", item_name="عجينة (كيلو)", is_stock_tracked=True)
        self.layer = make_item(item_code="FG-LAYER", item_name="كيكة طبقات", is_stock_tracked=True, default_sale_price="150")
        self.dough_recipe = services.save_recipe({"product": self.dough, "output_quantity": "3"}, [(self.flour, "2")], self.owner)
        self.layer_recipe = services.save_recipe({"product": self.layer, "output_quantity": "10"}, [(self.dough, "3")], self.owner)
        self.flour_lot = self.lot("LOT-A", A_EXPIRES, "100")
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)
        self.dough_run = lots.produce(self.dough_recipe, self.owner, batches="3", location=self.plant)           # 6 kg flour, 9 kg dough
        self.layer_order = self.walk(self.order("10", recipe=self.layer_recipe))                               # 3 kg of the dough
        self.layer_run = self.layer_order.run
        self.client.force_login(self.owner)

    def test_the_dough_lot_leads_to_the_cake_run_its_lot_and_the_customers(self):
        dough_lot = self.dough_run.product_lot
        self.assertEqual(self.uses(run=self.dough_run), {"LOT-A": D("6.000")})
        self.assertEqual(LotUse.objects.get(run=self.layer_run, item=self.dough).batch, dough_lot)
        nadia = make_customer("C-NADIA", name="نادية")
        sale = self.sell("4", nadia, item=self.layer)
        page = self.client.get(reverse("manufacturing:trace"), {"lang": "ar", "lot": dough_lot.pk})
        paths = page.context["paths"]
        self.assertEqual([path["run"] for path in paths], [self.dough_run, self.layer_run])      # its own, then the run that took it
        cake_path = paths[1]
        self.assertEqual(cake_path["product_lot"], self.layer_run.product_lot)
        self.assertEqual([(row["invoice"], row["customer"].name, row["quantity"]) for row in cake_path["sold"]["sales"]], [(sale, "نادية", D("4.000"))])
        self.assertContains(page, "نادية")
        self.assertContains(page, sale.invoice_number)
        # And from the flour lot, the dough run is the first step of the chain, whose lot links on.
        flour_page = self.client.get(reverse("manufacturing:trace"), {"lang": "ar", "lot": self.flour_lot.pk})
        self.assertIn(self.dough_run, [path["run"] for path in flour_page.context["paths"]])
        self.assertContains(flour_page, f"?lot={dough_lot.pk}")

    def test_the_trace_is_one_click_from_the_manufacturing_home(self):
        self.assertContains(self.client.get(reverse("manufacturing:home")), "data-trace-nav")


class DemoStepTests(LotSetup):
    """prepare_demo starts orders with ``orders.advance``; with batches on that records lots and can never crash the build."""

    def test_the_demo_orders_step_records_lots_and_swallows_a_refusal(self):
        from settings_core.management.commands.prepare_demo import Command

        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)
        customers = [make_customer(f"C-D{n}", name=f"عميل {n}") for n in range(4)]
        master = {"items": [self.cake, self.flour], "location": self.plant, "customers": customers}
        self.lot("LOT-A", A_EXPIRES, "100")
        Command()._orders(master, self.owner)
        started = ProductionOrderOf(self.recipe)
        self.assertEqual((started.status, self.uses(order=started)), (OrderStatus.IN_PROGRESS, {"LOT-A": D("10.000")}))
        self.assertFalse(Batch.objects.filter(production_run__isnull=False).exists())            # it never finishes an order
        # An expired lot: the step's own «except ValidationError: break» keeps the order planned and the build going.
        Batch.objects.filter(batch_no="LOT-A").update(expiry_date=TODAY - timedelta(days=1))
        ProductionOrderOf(self.recipe).delete()
        Command()._orders(master, self.owner)
        self.assertEqual(ProductionOrderOf(self.recipe).status, OrderStatus.PLANNED)


def ProductionOrderOf(recipe):
    from .models import ProductionOrder

    return ProductionOrder.objects.filter(recipe=recipe).latest("pk")


class RunPageTests(LotSetup):
    def setUp(self):
        super().setUp()
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)
        set_capability_enabled(self.profile, "barcode", True)
        self.run = self.walk(self.order()).run
        self.client.force_login(self.owner)

    def test_the_run_page_shows_the_lot_the_materials_and_the_label_and_trace_buttons(self):
        page = self.client.get(reverse("manufacturing:run", args=[self.run.pk]), {"lang": "ar"})
        self.assertContains(page, 'data-lot-no')
        self.assertContains(page, self.run.number)
        self.assertContains(page, "data-lot-trace")
        self.assertContains(page, "اطبع ملصقات التشغيلة")
        self.assertContains(page, "20 ملصق (وحدة)")
        self.assertContains(self.client.get(reverse("manufacturing:run", args=[self.run.pk]), {"lang": "en"}), "Print lot labels")

    def test_labels_are_per_carton_when_the_product_has_a_carton_unit(self):
        from units.models import ItemUnit

        self.assertEqual(lots.label_count(self.run), (20, False))
        ItemUnit.objects.create(item=self.cake, name_ar="كرتونة", name_en="Carton", factor=D("10"))
        self.assertEqual(lots.label_count(self.run), (2, True))
        self.assertContains(self.client.get(reverse("manufacturing:run", args=[self.run.pk])), "2 ملصق (كرتونة)")

    def test_the_lot_label_prints_the_lot_expiry_and_day_made_from_a_design_with_the_flags(self):
        design, errors = label_designs.clean({"name": "تشغيلة", "kind": "roll", "show_name": "1", "show_batch": "1", "show_expiry": "1", "show_made_on": "1"})
        self.assertEqual(errors, [])
        self.assertEqual((design["show_batch"], design["show_expiry"], design["show_made_on"]), (True, True, True))
        saved = label_designs.save(design, self.owner)
        page = self.client.get(reverse("manufacturing:run", args=[self.run.pk]))
        link = next(part for part in page.content.decode().split('"') if "/barcode/labels/print/" in part).replace("&amp;", "&")
        self.assertIn(f"template=d{saved['id']}", link)
        printed = self.client.get(link)
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, "data-label-lot")
        self.assertContains(printed, self.run.number)
        self.assertContains(printed, "data-label-expiry")
        self.assertContains(printed, (self.run.run_date + timedelta(days=10)).isoformat())
        self.assertContains(printed, "data-label-made")
        self.assertContains(printed, self.run.run_date.isoformat())
        self.assertEqual(printed.content.decode().count("data-label-lot"), 20)
        # The built-in sizes have the three lines off unless asked; an older design (saved before the flags) reads as off.
        self.assertFalse(label_designs.blank()["show_batch"])
        from barcode.label_templates import spec

        old = dict(saved)
        for flag in ("show_batch", "show_expiry", "show_made_on"):
            old.pop(flag)
        label_designs.save(old, self.owner, design_id=saved["id"])
        self.assertEqual({k: v for k, v in spec(f"d{saved['id']}")["defaults"].items() if k.startswith("show_") and k not in ("show_name", "show_price", "show_code", "show_shop")},
                         {"show_batch": False, "show_expiry": False, "show_made_on": False})

    def test_the_recipe_form_sets_the_shelf_life_and_the_plain_run_form_takes_a_lot(self):
        url = reverse("manufacturing:recipe", args=[self.recipe.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-shelf-life")
        self.assertContains(page, 'name="lot_no"')
        edited = self.client.post(url, {"action": "edit", "product": str(self.cake.pk), "output_quantity": "10", "active": "on",
                                        "shelf_life_days": "21", "component_0": str(self.flour.pk), "qty_0": "10", "lang": "ar"})
        self.assertEqual(edited.status_code, 302)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.shelf_life_days, 21)
        refused = self.client.post(url, {"action": "edit", "product": str(self.cake.pk), "output_quantity": "10", "active": "on",
                                         "shelf_life_days": "0", "component_0": str(self.flour.pk), "qty_0": "10", "lang": "ar"})
        self.assertContains(refused, "مدة الصلاحية لازم تكون عدد أيام صحيح")
        ran = self.client.post(url, {"action": "produce", "batches": "1", "location": str(self.plant.pk), "lot_no": "SCREEN-1", "lot_expiry": (TODAY + timedelta(days=4)).isoformat()})
        self.assertEqual(ran.status_code, 302)
        run = ProductionRun.objects.exclude(pk=self.run.pk).get()
        self.assertEqual((run.product_lot.batch_no, run.product_lot.expiry_date), ("SCREEN-1", TODAY + timedelta(days=4)))

    def test_the_order_finish_form_takes_a_lot_from_the_screen(self):
        order = self.order("10")
        for _ in order.stages:
            orders.advance(order, self.owner)
        url = reverse("manufacturing:order", args=[order.pk])
        self.assertContains(self.client.get(url), 'name="lot_no"')
        self.client.post(url, {"action": "finish", "lot_no": "FIN-1", "lot_expiry": (TODAY + timedelta(days=2)).isoformat(), "lang": "ar"})
        order.refresh_from_db()
        self.assertEqual(order.run.product_lot.batch_no, "FIN-1")


class PermissionAndEntityTests(LotSetup):
    def setUp(self):
        super().setUp()
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)
        set_capability_enabled(self.profile, "barcode", True)
        self.run = self.walk(self.order()).run
        self.lot = self.run.product_lot

    def test_a_user_without_manufacturing_access_gets_403(self):
        cashier = person(RoleCode.CASHIER, "lot_cashier")
        self.client.force_login(cashier)
        for url in (reverse("manufacturing:trace"), f"{reverse('manufacturing:trace')}?lot={self.lot.pk}", reverse("manufacturing:run", args=[self.run.pk]),
                    f"{reverse('barcode:labels_print')}?copies_{self.cake.pk}=2&run={self.run.pk}"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_another_entitys_lot_and_run_are_404(self):
        from entities.services import save_entity

        branch = save_entity({"code": "BR3", "name_ar": "فرع المعادي", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        make_location(location_code="BR-PLANT", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        trace = reverse("manufacturing:trace")
        self.assertEqual(self.client.get(trace, {"lot": self.lot.pk}).status_code, 404)
        self.assertEqual(self.client.get(trace, {"run": self.run.pk}).status_code, 404)
        self.assertEqual(self.client.get(reverse("manufacturing:run", args=[self.run.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("barcode:labels_print"), {f"copies_{self.cake.pk}": "2", "run": self.run.pk}).status_code, 404)
        self.assertNotContains(self.client.get(trace, {"q": "MO-"}), f'data-found-lot="{self.lot.pk}"')   # nor does it turn up in a search
        self.client.post(reverse("entities:switch"), {"entity": "", "next": "/dashboard/"})
        self.assertEqual(self.client.get(trace, {"lot": self.lot.pk}).status_code, 200)

    def test_the_new_screens_use_no_banned_word(self):
        import pathlib

        from django.conf import settings

        banned = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")
        texts = [(pathlib.Path(settings.BASE_DIR) / name).read_text(encoding="utf-8") for name in
                 ("templates/manufacturing/trace.html", "templates/manufacturing/_lots.html", "manufacturing/trace_views.py", "manufacturing/lots.py")]
        for text in texts:
            for word in banned:
                self.assertNotIn(word, text)
