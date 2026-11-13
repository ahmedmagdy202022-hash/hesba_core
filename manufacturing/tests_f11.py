"""Manufacturing F11 (HG-100): two live defects, proved first and then fixed.

1. **Units lost to batch rounding.** An order's batches are its quantity over the recipe's output, kept to three
   decimals; the finish used to put ``output x batches`` into stock, so 100 units with a batch of 3 (33.333 batches)
   came in as 99.999 and with a tray of 12 (8.333) as 99.996. The finish now puts in the order's good units.
2. **Cancelling a run whose goods have left.** The engine refuses a reversal only when the place holds fewer units
   than the run made. Sold and then refilled by a later run, the cancel went through and took the value out at the
   old run's cost, leaving the units that remained at a cost nobody paid. A run is now cancelled only while nothing
   of its product has left its place since.

The bakery: flour 20.00 a kilo (200 kg), sugar 30.00 (20 kg), a box 2.50 (100): raw materials worth 4,850.00.
Cakes: one batch is 2 kg flour + 1 kg sugar + 10 boxes = 95.00 for 10 cakes, as in ``MfgSetup``.

Every scenario runs twice: on the manufacturing chart (raw materials, work in progress, finished goods, absorbed
production cost) and on a restaurant bakery's chart with the manufacturing module on, where there is one inventory
account and both work in progress and absorbed cost fall back to the cost of sales.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from config.money import money_round
from hesba_testing.factories import make_cashbox, make_item, make_location, stock_in
from inventory.models import StockAdjustmentDirection, StockMovement, StockOperation
from inventory.services import (
    adjust_stock,
    cancel_stock_operation,
    get_item_authoritative_average_cost,
    get_item_location_stock_quantity,
    get_item_stock_value,
    transfer_stock,
)
from ledger import reports as gl
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import make_to_order, orders, services
from .models import OrderStatus, RunStatus
from .tests import person
from .tests_make_to_order import MakeToOrderSetup

TODAY = timezone.localdate()
REFUSED_AR = ("مينفعش تلغي التشغيلة دي، عشان في كمية من «كيكة» خرجت من المخزن بعد ما التشغيلة اتسجلت "
              "(بيع أو تحويل أو تسوية أو استخدام في تشغيلة تانية). لو الرصيد محتاج تصحيح، اعمل تسوية مخزون عادية.")
REFUSED_EN = ("This run cannot be cancelled: some “كيكة” has left the location since the run was recorded "
              "(a sale, a transfer, an adjustment or use in another run). To correct the stock, record an ordinary stock adjustment.")


class F11Setup(TestCase):
    ACTIVITY, SUB = "manufacturing", "food"
    MODULES = "customers,items_services,sales_operations,purchases,inventory,cashboxes,reports,manufacturing"

    def setUp(self):
        self.profile = prepared_client(activity=self.ACTIVITY, sub_activity=self.SUB, modules=self.MODULES)
        self.owner = person(RoleCode.OWNER, "f11_owner")
        self.plant = make_location(location_code="PLANT", is_default=True)
        self.cashbox = make_cashbox()
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق (كيلو)", is_stock_tracked=True)
        self.sugar = make_item(item_code="RM-SUGAR", item_name="سكر (كيلو)", is_stock_tracked=True)
        self.box = make_item(item_code="PK-BOX", item_name="علبة", is_stock_tracked=True)
        self.cake = make_item(item_code="FG-CAKE", item_name="كيكة", is_stock_tracked=True, default_sale_price="120")
        stock_in(self.flour, self.plant, 200, "20.00", movement_date=TODAY)
        stock_in(self.sugar, self.plant, 20, "30.00", movement_date=TODAY)
        stock_in(self.box, self.plant, 100, "2.50", movement_date=TODAY)
        self.recipe = services.save_recipe({"product": self.cake, "output_quantity": "10"},
                                           [(self.flour, "2"), (self.sugar, "1"), (self.box, "10")], self.owner)

    # ---- the floor ----

    def make(self, recipe=None, quantity="20", labor="60", overhead="40", defects=None):
        """Open an order, walk every stage (``defects`` at the second one) and finish it. Returns the run."""

        order = orders.create_order({"recipe": recipe or self.recipe, "location": self.plant, "quantity": quantity,
                                     "labor_cost": labor, "overhead_cost": overhead}, self.owner)
        for index, _ in enumerate(order.stages):
            orders.advance(order, self.owner, defect=defects if index == 1 else None)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        return order.run

    def sell(self, quantity, sale_date=None):
        from sales.pos import checkout, walk_in_customer

        invoice, _ = checkout(lines=[{"item": self.cake, "quantity": D(quantity), "unit_sale_price": D("120"), "line_discount_amount": D("0"),
                                      "description": ""}], customer=walk_in_customer(), location=self.plant, cashbox=self.cashbox,
                              discount=D("0"), tendered=D(quantity) * D("120"), user=self.owner, sale_date=sale_date)
        return invoice

    def cancel(self, run, lang="ar"):
        """Try to cancel; the refusal's messages, or None when the run was cancelled."""

        try:
            services.cancel_run(run, self.owner, reason="غلطة في التسجيل", lang=lang)
        except ValidationError as exc:
            return exc.messages
        return None

    # ---- what is read back ----

    def position(self, item=None):
        """(units at the plant, stock value, average cost) of the product."""

        item = item or self.cake
        return (get_item_location_stock_quantity(item, self.plant), money_round(get_item_stock_value(item)), get_item_authoritative_average_cost(item))

    def on_hand(self, *items):
        return tuple(get_item_location_stock_quantity(item, self.plant) for item in items)

    def journal(self, operation):
        """(debit, credit) of the entry the ledger made for one stock operation."""

        totals = JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id=operation.pk).aggregate(d=Sum("debit"), c=Sum("credit"))
        return totals["d"], totals["c"]

    def books(self, *, finished, raw, wip="0.00", absorbed="0.00", cogs="0.00"):
        """The ledger after the scenario: balanced, the stock report agrees with it, and each account holds what is said."""

        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        check = gl.reconciliation()
        self.assertTrue(check["balanced"])
        self.assertEqual({row["key"]: row["difference"] for row in check["rows"] if row["difference"]}, self.unreconciled(D(wip), D(absorbed)))
        self.controls(D(finished), D(raw), D(wip), D(absorbed), D(cogs))

    def unreconciled(self, wip, absorbed):
        return {}  # every control account agrees with the report that owns its figure

    def controls(self, finished, raw, wip, absorbed, cogs):
        self.assertEqual({key: gl.control_balance(key) for key in ("finished_goods", "raw_materials", "wip", "production_cost", "cogs")},
                         {"finished_goods": finished, "raw_materials": raw, "wip": wip, "production_cost": -absorbed, "cogs": cogs})


class BakeryChart:
    """A restaurant bakery with the manufacturing module on: one inventory account, and both work in progress and
    absorbed production cost fall back to the cost of sales."""

    ACTIVITY, SUB = "restaurants", "bakery"
    MODULES = "items_services,sales_operations,cashboxes,reports,inventory,pdf_printing,tables_orders,manufacturing"

    def unreconciled(self, wip, absorbed):
        # As before F11 (HG-036): on this chart the cost of sales also carries what is on the floor and the labour and
        # overhead absorbed, so it differs from the profit report by exactly that. Stock, cash and the parties agree.
        return {"cogs": wip - absorbed} if wip - absorbed else {}

    def controls(self, finished, raw, wip, absorbed, cogs):
        self.assertEqual({key: gl.control_balance(key) for key in ("inventory", "cogs")}, {"inventory": finished + raw, "cogs": cogs + wip - absorbed})
        for key in ("wip", "production_cost", "finished_goods", "raw_materials"):
            self.assertEqual(gl.control_balance(key), D("0.00"), key)  # this chart has no such account


# ---------------------------------------------------------------------------------------------------------------
# Defect 1: units lost to batch rounding
# ---------------------------------------------------------------------------------------------------------------


class RoundingTests(F11Setup):
    def setUp(self):
        super().setUp()
        self.rusk = make_item(item_code="FG-RUSK", item_name="بقسماط", is_stock_tracked=True)
        self.bun = make_item(item_code="FG-BUN", item_name="عيش فينو", is_stock_tracked=True)
        # A batch of 3 takes 3 kg of flour (60.00); a tray of 12 takes 6 kg (120.00).
        self.by_three = services.save_recipe({"product": self.rusk, "output_quantity": "3"}, [(self.flour, "3")], self.owner)
        self.by_tray = services.save_recipe({"product": self.bun, "output_quantity": "12"}, [(self.flour, "6")], self.owner)

    def test_an_order_for_100_with_a_batch_of_3_puts_100_units_in_stock(self):
        # 100 / 3 = 33.333 batches; flour issued 3 x 33.333 = 99.999 kg at 20.00 = 1,999.98.
        run = self.make(self.by_three, quantity="100", labor="0", overhead="0")
        self.assertEqual((run.batches, run.total_cost, run.conversion_cost), (D("33.333"), D("1999.98"), D("0.00")))
        self.assertEqual(self.on_hand(self.flour), (D("100.001"),))                     # materials issued stay recipe x batches
        self.assertEqual((run.output_quantity, run.scrap_quantity), (D("100.000"), D("0.000")))   # was 99.999
        self.assertEqual(run.output_operation.unit_cost, D("19.9998"))                  # 1,999.98 / 100; was 20.0000 over 99.999
        self.assertEqual(self.position(self.rusk), (D("100.000"), D("1999.98"), D("19.9998")))
        Projector().rebuild()
        self.assertEqual(self.journal(run.output_operation), (D("1999.98"), D("1999.98")))
        self.books(finished="1999.98", raw="2850.02")                                   # 4,850.00 - 1,999.98

    def test_an_order_for_100_with_a_tray_of_12_puts_100_units_in_stock(self):
        # 100 / 12 = 8.333 trays; flour issued 6 x 8.333 = 49.998 kg = 999.96; labour 60.04 + overhead 40.00.
        run = self.make(self.by_tray, quantity="100", labor="60.04", overhead="40")
        self.assertEqual((run.batches, run.total_cost, run.conversion_cost), (D("8.333"), D("999.96"), D("100.04")))
        self.assertEqual((run.output_quantity, run.scrap_quantity), (D("100.000"), D("0.000")))   # was 99.996
        self.assertEqual(run.output_operation.unit_cost, D("11.0000"))                  # 1,100.00 / 100; was 11.0004 over 99.996
        self.assertEqual(self.position(self.bun), (D("100.000"), D("1100.00"), D("11.0000")))
        Projector().rebuild()
        self.assertEqual(self.journal(run.output_operation), (D("1100.00"), D("1100.00")))
        self.books(finished="1100.00", raw="3850.04", absorbed="100.04")

    def test_an_order_with_defects_puts_exactly_its_good_units_in_stock(self):
        # 4 of the 100 fail a stage: 96 good units carry the 1,999.98 (20.833125 each, kept as 20.8331).
        run = self.make(self.by_three, quantity="100", labor="0", overhead="0", defects="4")
        self.assertEqual((run.output_quantity, run.scrap_quantity), (D("96.000"), D("4.000")))    # scrap was 3.999
        self.assertEqual(run.output_operation.unit_cost, D("20.8331"))
        self.assertEqual(self.position(self.rusk), (D("96.000"), D("1999.98"), D("20.8331")))
        self.books(finished="1999.98", raw="2850.02")

    def test_an_order_smaller_than_a_batch_records_no_scrap_that_never_was(self):
        # 2 / 3 = 0.667 of a batch; flour issued 3 x 0.667 = 2.001 kg = 40.02. Nothing failed, so nothing is scrap.
        run = self.make(self.by_three, quantity="2", labor="0", overhead="0")
        self.assertEqual((run.batches, run.total_cost), (D("0.667"), D("40.02")))
        self.assertEqual((run.output_quantity, run.scrap_quantity), (D("2.000"), D("0.000")))     # scrap was 0.001
        self.assertEqual(self.position(self.rusk), (D("2.000"), D("40.02"), D("20.0100")))
        self.books(finished="40.02", raw="4809.98")

    def test_an_order_that_divides_exactly_is_identical_to_before(self):
        # The HG-036 figures: 20 cakes = 2 batches = 190.00 of materials + 60.00 labour + 40.00 overhead = 290.00.
        run = self.make()
        self.assertEqual((run.batches, run.output_quantity, run.scrap_quantity), (D("2.000"), D("20.000"), D("0.000")))
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_operation.unit_cost), (D("190.00"), D("100.00"), D("14.5000")))
        self.assertEqual(self.position(), (D("20.000"), D("290.00"), D("14.5000")))
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("196.000"), D("18.000"), D("80.000")))
        Projector().rebuild()
        self.assertEqual(self.journal(run.output_operation), (D("290.00"), D("290.00")))
        self.books(finished="290.00", raw="4660.00", absorbed="100.00")

    def test_one_defect_in_an_exact_order_is_identical_to_before(self):
        run = self.make(defects="1")
        self.assertEqual((run.output_quantity, run.scrap_quantity, run.output_operation.unit_cost), (D("19.000"), D("1.000"), D("15.2632")))
        self.assertEqual(self.position(), (D("19.000"), D("290.00"), D("15.2632")))
        self.books(finished="290.00", raw="4660.00", absorbed="100.00")

    def test_a_plain_run_still_makes_the_recipe_times_its_batches(self):
        # No ``output`` is passed by the recipe page, the restaurant or the demo seed: nothing changes for them.
        run = services.produce(self.by_three, self.owner, batches="33.333", location=self.plant)
        self.assertEqual((run.output_quantity, run.scrap_quantity, run.total_cost), (D("99.999"), D("0.000"), D("1999.98")))
        self.assertEqual(run.output_operation.unit_cost, D("20.0000"))
        self.assertEqual(self.position(self.rusk), (D("99.999"), D("1999.98"), D("20.0000")))
        self.books(finished="1999.98", raw="2850.02")


class RoundingOnARestaurantChartTests(BakeryChart, RoundingTests):
    pass


class ExplicitOutputTests(F11Setup):
    """``produce(output=...)``: the units that enter stock, said by the caller."""

    def test_the_units_said_enter_stock_and_carry_the_whole_cost(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant, output="9.5", scrap="0.5")
        self.assertEqual((run.batches, run.output_quantity, run.scrap_quantity, run.total_cost), (D("1.000"), D("9.500"), D("0.500"), D("95.00")))
        self.assertEqual(run.output_operation.unit_cost, D("10.0000"))
        self.assertEqual(self.position(), (D("9.500"), D("95.00"), D("10.0000")))
        self.assertEqual(AuditLog.objects.get(action="produce").after_data["output"], "9.500")

    def test_no_units_is_refused_and_nothing_moves(self):
        before = StockMovement.objects.count()
        for output in ("0", "-1"):
            with self.subTest(output=output), self.assertRaisesMessage(ValidationError, "مفيش ولا وحدة سليمة"):
                services.produce(self.recipe, self.owner, batches="1", location=self.plant, output=output)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(self.on_hand(self.flour, self.cake), (D("200.000"), D("0")))


# ---------------------------------------------------------------------------------------------------------------
# Defect 2: cancelling a run whose goods have left
# ---------------------------------------------------------------------------------------------------------------


class CancelAfterSaleTests(F11Setup):
    """Run 1 makes 20 cakes at 14.50 (290.00). 15 are sold (cost 217.50). Run 2 makes 20 at 16.00 (320.00).
    Stock: 25 cakes worth 392.50, 15.70 each."""

    def two_runs(self):
        first = self.make()                                   # 190.00 + 60.00 + 40.00 = 290.00
        self.sell("15")
        second = self.make(labor="90", overhead="40")         # 190.00 + 130.00 = 320.00
        self.assertEqual((first.output_operation.unit_cost, second.output_operation.unit_cost), (D("14.5000"), D("16.0000")))
        self.assertEqual(self.position(), (D("25.000"), D("392.50"), D("15.7000")))
        return first, second

    def test_cancelling_the_run_whose_cakes_were_sold_is_refused_and_nothing_moves(self):
        first, _ = self.two_runs()
        movements = StockMovement.objects.count()
        refused = self.cancel(first)
        # It used to go through: 20 out at 14.50 left 5 cakes worth 102.50, 20.50 each, for cakes that cost 14.50 and 16.00.
        self.assertEqual(self.position(), (D("25.000"), D("392.50"), D("15.7000")))
        self.assertEqual(refused, [REFUSED_AR])
        first.refresh_from_db()
        self.assertEqual(first.status, RunStatus.POSTED)
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(StockOperation.objects.filter(status="cancelled").count(), 0)
        self.assertFalse(AuditLog.objects.filter(action="cancel_production").exists())
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("192.000"), D("16.000"), D("60.000")))
        self.assertEqual(self.cancel(first, lang="en"), [REFUSED_EN])
        self.books(finished="392.50", raw="4470.00", absorbed="230.00", cogs="217.50")   # raw: 4,850.00 - 2 x 190.00

    def test_cancelling_the_later_run_passes(self):
        _, second = self.two_runs()
        self.assertIsNone(self.cancel(second))                # nothing left the plant since run 2
        second.refresh_from_db()
        self.assertEqual(second.status, RunStatus.CANCELLED)
        self.assertEqual(self.position(), (D("5.000"), D("72.50"), D("14.5000")))        # run 1's five cakes, at run 1's cost
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("196.000"), D("18.000"), D("80.000")))
        self.books(finished="72.50", raw="4660.00", absorbed="100.00", cogs="217.50")

    def test_a_plain_run_with_nothing_sold_cancels_as_before(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        self.assertEqual(self.position(), (D("10.000"), D("95.00"), D("9.5000")))
        self.assertIsNone(self.cancel(run))
        run.refresh_from_db()
        self.assertEqual((run.status, run.cancellation_reason), (RunStatus.CANCELLED, "غلطة في التسجيل"))
        self.assertEqual(self.position(), (D("0.000"), D("0.00"), D("0")))
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("200.000"), D("20.000"), D("100.000")))
        self.assertEqual(StockMovement.objects.filter(reversal_of__isnull=False).count(), 4)   # the cakes and three materials
        self.assertEqual(self.cancel(run), ["التشغيلة دي اتلغت بالفعل."])
        self.books(finished="0.00", raw="4850.00")

    def refilled(self, leave):
        """A plain run of 10 cakes, ``leave`` takes some out, a second run refills the plant: the engine alone would
        let the first run be reversed."""

        first = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        leave()
        services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        return first

    def assert_refused(self, run, position):
        movements = StockMovement.objects.count()
        self.assertEqual(self.cancel(run), [REFUSED_AR])
        run.refresh_from_db()
        self.assertEqual((run.status, StockMovement.objects.count(), self.position()), (RunStatus.POSTED, movements, position))

    def test_a_transfer_out_blocks(self):
        shop = make_location(location_code="SHOP", name_ar="المحل", name_en="Shop")
        first = self.refilled(lambda: transfer_stock("TR-F11", TODAY, self.cake, self.plant, shop, D("4"), self.owner, reason="للمحل"))
        self.assert_refused(first, (D("16.000"), D("190.00"), D("9.5000")))               # 20 cakes in the group, 16 at the plant
        self.books(finished="190.00", raw="4660.00")

    def test_an_adjustment_out_blocks(self):
        first = self.refilled(lambda: adjust_stock("ADJ-F11", TODAY, self.cake, self.plant, StockAdjustmentDirection.OUT, D("4"), "تالف", self.owner))
        self.assert_refused(first, (D("16.000"), D("152.00"), D("9.5000")))
        self.books(finished="152.00", raw="4660.00")

    def test_use_in_another_run_blocks(self):
        gift = make_item(item_code="FG-GIFT", item_name="علبة هدايا", is_stock_tracked=True)
        boxed = services.save_recipe({"product": gift, "output_quantity": "1"}, [(self.cake, "4")], self.owner)
        first = self.refilled(lambda: services.produce(boxed, self.owner, batches="1", location=self.plant))
        self.assert_refused(first, (D("16.000"), D("152.00"), D("9.5000")))
        self.assertEqual(self.position(gift), (D("1.000"), D("38.00"), D("38.0000")))
        self.books(finished="190.00", raw="4660.00")                                      # cakes 152.00 + the gift box 38.00

    def test_what_left_before_the_run_does_not_block(self):
        stock_in(self.cake, self.plant, 10, "9.50", movement_date=TODAY)                  # bought-in cakes
        self.sell("5")                                                                    # sold this morning
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant, run_date=TODAY - timedelta(days=1))
        self.assertEqual(self.position(), (D("15.000"), D("142.50"), D("9.5000")))
        self.assertIsNone(self.cancel(run))                   # creation order, not dates: the run is dated before the sale
        self.assertEqual(self.position(), (D("5.000"), D("47.50"), D("9.5000")))

    def test_a_back_dated_sale_typed_in_after_the_run_blocks(self):
        first = self.refilled(lambda: self.sell("4", sale_date=TODAY - timedelta(days=1)))
        self.assert_refused(first, (D("16.000"), D("152.00"), D("9.5000")))
        self.books(finished="152.00", raw="4660.00", cogs="38.00")

    def test_two_runs_with_nothing_sold_can_both_be_cancelled_in_either_order(self):
        # Reversing a later run takes its own units out at their own cost; that is not the product leaving.
        for order in ((0, 1), (1, 0)):
            with self.subTest(order=order):
                runs = [services.produce(self.recipe, self.owner, batches="1", location=self.plant) for _ in range(2)]
                for index in order:
                    self.assertIsNone(self.cancel(runs[index]))
                self.assertEqual(self.position(), (D("0.000"), D("0.00"), D("0")))
        self.books(finished="0.00", raw="4850.00")

    def test_an_outgoing_operation_reversed_since_does_not_block(self):
        # The four cakes went out by mistake and came back whole, at the cost they left at.
        first = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        out = adjust_stock("ADJ-F11", TODAY, self.cake, self.plant, StockAdjustmentDirection.OUT, D("4"), "غلطة", self.owner)
        self.assertEqual(self.cancel(first), [REFUSED_AR])
        cancel_stock_operation(out.pk, TODAY, "رجعت", self.owner)
        self.assertIsNone(self.cancel(first))
        self.assertEqual(self.position(), (D("0.000"), D("0.00"), D("0")))
        self.books(finished="0.00", raw="4850.00")

    def test_a_sale_cancelled_since_still_blocks(self):
        # The default (HG-100, question 3): a cancelled invoice brings the cakes back as a return, and that is
        # not read as the sale never having happened.
        from sales.services import cancel_posted_sales_invoice

        first = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        invoice = self.sell("4")
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلطة")
        self.assert_refused(first, (D("10.000"), D("95.00"), D("9.5000")))

    def test_the_stock_screens_own_reversal_is_not_covered_yet(self):
        # A known gap, pinned (HG-100, open question 1): the rule lives in ``cancel_run``. Reversing the run's output
        # adjustment itself, as the stock operations screen can, still goes through the engine alone.
        first, _ = self.two_runs()
        cancel_stock_operation(first.output_operation_id, TODAY, "من شاشة عمليات المخزون", self.owner)
        first.refresh_from_db()
        self.assertEqual((first.status, self.position()), (RunStatus.POSTED, (D("5.000"), D("102.50"), D("20.5000"))))

    def test_the_run_page_says_why_in_both_languages(self):
        first, _ = self.two_runs()
        self.client.force_login(self.owner)
        url = reverse("manufacturing:run", args=[first.pk])
        for lang, text in (("ar", REFUSED_AR), ("en", REFUSED_EN)):
            with self.subTest(lang=lang):
                page = self.client.post(url, {"reason": "غلطة", "lang": lang})
                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.context["error"], text)
                self.assertContains(page, "data-run-error")
        first.refresh_from_db()
        self.assertEqual((first.status, self.position()), (RunStatus.POSTED, (D("25.000"), D("392.50"), D("15.7000"))))


class CancelAfterSaleOnARestaurantChartTests(BakeryChart, CancelAfterSaleTests):
    pass


# ---------------------------------------------------------------------------------------------------------------
# A second person's approval (FB-27)
# ---------------------------------------------------------------------------------------------------------------


class ApprovalTests(F11Setup):
    ACTION = "manufacturing.cancel_run"

    def setUp(self):
        super().setUp()
        from approvals.tests import clerk_with

        self.manager = person(RoleCode.MANAGER, "f11_manager")
        self.clerk = clerk_with("inventory.adjust_stock", "inventory.view_stock", username="f11_clerk")
        self.run_ = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        self.url = reverse("manufacturing:run", args=[self.run_.pk])

    def hold(self):
        from approvals.services import set_held_actions

        set_held_actions([self.ACTION], self.owner)

    def ask(self, user=None, reason="خبزة باظت"):
        self.client.force_login(user or self.clerk)
        return self.client.post(self.url, {"reason": reason, "lang": "ar"}, follow=True)

    def snapshot(self):
        """Everything a cancellation would change: stock rows, operations, the product's cost, the journal, the run."""

        Projector().rebuild()
        totals = JournalLine.objects.aggregate(d=Sum("debit"), c=Sum("credit"))
        self.run_.refresh_from_db()
        return {"movements": StockMovement.objects.count(), "cancelled_operations": StockOperation.objects.filter(status="cancelled").count(),
                "position": self.position(), "materials": self.on_hand(self.flour, self.sugar, self.box),
                "entries": JournalEntry.objects.count(), "debit": totals["d"], "credit": totals["c"], "run": self.run_.status,
                "audit": AuditLog.objects.filter(action="cancel_production").count()}

    def test_the_action_is_one_the_owner_can_tick(self):
        from approvals.actions import ACTIONS, label

        action = ACTIONS[self.ACTION]
        self.assertEqual((label(self.ACTION, "ar"), label(self.ACTION, "en")), ("إلغاء تشغيلة إنتاج", "Cancel a production run"))
        self.assertEqual((action.url(self.run_.pk), action.permissions), (self.url, ("inventory.adjust_stock",)))
        self.assertTrue(action.visible(self.run_.pk))
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/approvals/"), f'value="{self.ACTION}"')

    def test_nothing_is_held_until_the_owner_says_so(self):
        from approvals.models import ApprovalRequest

        self.ask()
        self.run_.refresh_from_db()
        self.assertEqual(self.run_.status, RunStatus.CANCELLED)
        self.assertFalse(ApprovalRequest.objects.exists())

    def test_a_held_cancel_waits_and_nothing_moves_then_applies_once(self):
        from approvals import services as approvals
        from approvals.models import ApprovalRequest

        self.hold()
        before = self.snapshot()
        self.assertEqual((before["position"], before["run"], before["cancelled_operations"]), ((D("10.000"), D("95.00"), D("9.5000")), "posted", 0))
        page = self.ask()
        self.assertContains(page, "محتاجة موافقة")
        self.assertEqual(self.snapshot(), before)
        request = ApprovalRequest.objects.get()
        self.assertEqual((request.action, request.object_id, request.object_label, request.data, request.requested_by, request.status),
                         (self.ACTION, self.run_.pk, self.run_.number, {"reason": "خبزة باظت", "lang": "ar"}, self.clerk, "pending"))
        self.ask()                                            # asking again does not ask twice
        self.assertEqual(ApprovalRequest.objects.count(), 1)
        self.assertEqual(self.snapshot(), before)

        self.client.force_login(self.manager)
        self.assertContains(self.client.get("/approvals/"), f'data-approval="{request.pk}"')
        self.client.post("/approvals/", {"do": "approve", "request": request.pk})
        request.refresh_from_db()
        self.assertEqual((request.status, request.decided_by), ("done", self.manager))
        after = self.snapshot()
        self.assertEqual((after["run"], after["position"], after["materials"]),
                         ("cancelled", (D("0.000"), D("0.00"), D("0")), (D("200.000"), D("20.000"), D("100.000"))))
        self.assertEqual((after["movements"] - before["movements"], after["cancelled_operations"], after["audit"]), (4, 4, 1))
        self.assertEqual(AuditLog.objects.get(action="cancel_production").actor, self.clerk)   # in the requester's name
        self.run_.refresh_from_db()
        self.assertEqual(self.run_.cancellation_reason, "خبزة باظت")
        with self.assertRaisesMessage(ValidationError, "approval_not_waiting"):               # and only once
            approvals.approve(request.pk, self.manager)
        self.assertEqual(self.snapshot(), after)
        self.books(finished="0.00", raw="4850.00")

    def test_those_who_may_approve_cancel_directly(self):
        from approvals.models import ApprovalRequest

        self.hold()
        self.ask(self.manager)
        self.run_.refresh_from_db()
        self.assertEqual(self.run_.status, RunStatus.CANCELLED)
        self.assertFalse(ApprovalRequest.objects.exists())

    def test_a_cancel_that_would_be_refused_is_not_sent_for_approval(self):
        from approvals.models import ApprovalRequest

        self.hold()
        self.assertContains(self.ask(reason="  "), "اكتب سبب الإلغاء")
        self.sell("4")
        services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        page = self.ask()
        self.assertEqual(page.context["error"], REFUSED_AR)
        self.assertFalse(ApprovalRequest.objects.exists())

    def test_goods_sold_while_the_request_waited_stop_it_at_the_approval(self):
        from approvals import services as approvals
        from approvals.models import ApprovalRequest

        self.hold()
        self.ask()
        self.sell("4")
        services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        before = self.snapshot()
        request = approvals.approve(ApprovalRequest.objects.get().pk, self.manager)
        self.assertEqual((request.status, request.note), ("failed", REFUSED_AR))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(before["position"], (D("16.000"), D("152.00"), D("9.5000")))

    def test_rejecting_leaves_the_run_as_it_was(self):
        from approvals import services as approvals
        from approvals.models import ApprovalRequest

        self.hold()
        before = self.snapshot()
        self.ask()
        approvals.reject(ApprovalRequest.objects.get().pk, self.manager, "خليها")
        self.assertEqual(self.snapshot(), before)


# ---------------------------------------------------------------------------------------------------------------
# Make to order (F1, HG-098) reads the run's output: what a finished order covers of its sales order line
# ---------------------------------------------------------------------------------------------------------------


class MakeToOrderTests(MakeToOrderSetup):
    """A sales order for 100 rusks, made in batches of 3 (3 kg of flour a batch): 33.333 batches, 1,999.98 of flour."""

    def setUp(self):
        super().setUp()
        stock_in(self.flour, self.plant, 150, "20.00", movement_date=TODAY)
        self.rusk = make_item(item_code="FG-RUSK", item_name="بقسماط", is_stock_tracked=True)
        services.save_recipe({"product": self.rusk, "output_quantity": "3"}, [(self.flour, "3")], self.owner)
        self.sold = self.line(self.sale("100", item=self.rusk))
        self.production = make_to_order.make(self.sold, self.owner, {})
        self.assertEqual((self.production.quantity, make_to_order.uncovered(self.sold)), (D("100.000"), D("0.000")))

    def finish(self, defects=None):
        for index, _ in enumerate(self.production.stages):
            orders.advance(self.production, self.owner, defect=defects if index == 1 else None)
        orders.finish(self.production, self.owner)
        self.production.refresh_from_db()
        return self.production.run

    def rusks(self):
        return get_item_location_stock_quantity(self.rusk, self.plant)

    def test_a_finished_order_covers_the_whole_line(self):
        run = self.finish()
        self.assertEqual((run.output_quantity, run.scrap_quantity, self.rusks()), (D("100.000"), D("0.000"), D("100.000")))
        self.assertEqual(make_to_order.uncovered(self.sold), D("0.000"))                  # 99.999 in stock left 0.001 to make

    def test_an_order_with_defects_leaves_exactly_the_units_lost_to_make(self):
        run = self.finish(defects="4")
        self.assertEqual((run.output_quantity, run.scrap_quantity, self.rusks()), (D("96.000"), D("4.000"), D("96.000")))
        self.assertEqual(run.output_operation.unit_cost, D("20.8331"))                    # 1,999.98 / 96
        self.assertEqual(make_to_order.uncovered(self.sold), D("4.000"))

    def test_cancelling_its_run_frees_the_line_again(self):
        run = self.finish(defects="4")
        services.cancel_run(run, self.owner, reason="غلطة في التسجيل")
        self.production.refresh_from_db()
        self.assertEqual((self.production.status, self.production.run.status), (OrderStatus.DONE, RunStatus.CANCELLED))
        self.assertEqual((self.rusks(), get_item_location_stock_quantity(self.flour, self.plant)), (D("0.000"), D("200.000")))
        self.assertEqual(make_to_order.uncovered(self.sold), D("100.000"))

    def test_once_the_customer_was_invoiced_the_run_stays_and_so_does_what_it_covers(self):
        run = self.finish(defects="4")
        self.invoice(self.sold.order, "96")
        with self.assertRaisesMessage(ValidationError, "في كمية من «بقسماط» خرجت من المخزن بعد ما التشغيلة اتسجلت"):
            services.cancel_run(run, self.owner, reason="غلطة في التسجيل")
        run.refresh_from_db()
        self.assertEqual((run.status, self.rusks()), (RunStatus.POSTED, D("0.000")))
        self.assertEqual(make_to_order.uncovered(self.sold), D("4.000"))
