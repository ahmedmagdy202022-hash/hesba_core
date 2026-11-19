"""Manufacturing F9 (HG-139): by-products and co-products from one run.

The mill (the plan's example, to the piastre): 1,000 kg of wheat at 12.00 (12,000.00) and 1,000.00 of labour make a
joint cost of 13,000.00. Out come 780 kg of flour (the recipe's product), 200 kg of bran (a by-product at 3.00 a
kilo, 600.00) and 20 kg lost. The bran's 600.00 comes off the joint cost, so the flour enters stock at
(13,000.00 − 600.00) / 780 = 15.8974 a kilo, worth 12,399.97. The outputs are worth 12,999.97, three piastres less
than the joint cost: work in progress ends at 0.00 and absorbed production cost (5102) at −999.97, on the flour's
entry (F12's residue rule).

The dairy: 1,000 litres of milk at 4.00 (4,000.00) and 1,000.00 of labour and overhead make 5,000.00. Cheese (the
product, 100 kg) and butter (a co-product, 50 kg at 40%) share it 3,000.00 and 2,000.00.

Every money scenario runs on the manufacturing chart and on a restaurant bakery's chart with the manufacturing module
on (one inventory account; work in progress and absorbed cost both fall back to the cost of sales). How a run with no
extra output books is pinned, shape by shape, in ``tests_run_journal`` (written before the projector was touched).
"""

import pathlib
from datetime import timedelta
from decimal import Decimal as D

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from batches.models import Batch
from hesba_testing.factories import make_item, make_location, stock_in
from inventory.models import StockMovement, StockOperation
from inventory.services import get_item_authoritative_average_cost, get_item_location_stock_quantity, get_item_stock_value
from ledger import reports as gl
from ledger import statements
from ledger.models import JournalLine
from ledger.projector import Projector
from config.money import money_round
from settings_core.capabilities import set_capability_enabled

from . import byproducts, byproducts_views, guards, orders, quality, services, variance
from .models import OrderStatus, OutputKind, ProductionRun, RecipeOutput, RunOutput, RunStatus
from .tests_f11 import BakeryChart, F11Setup
from .tests_quality import BANNED, LATIN_WORD, edges, limited, sentences

TODAY = timezone.localdate()
BEFORE = TODAY - timedelta(days=3)
BANNED_WORDS = BANNED + ("شغلانة",)


class Mill(F11Setup):
    """``F11Setup``'s bakery (raw materials 4,850.00), plus a mill and a dairy: wheat 1,500 kg at 12.00 (18,000.00) and
    milk 2,000 litres at 4.00 (8,000.00). Raw materials start at 30,850.00."""

    RAW = D("30850.00")
    #: Which account each side of a run's entry lands in, on this chart.
    CODES = {"raw": "110501", "wip": "110502", "fg": "110503", "absorbed": "5102"}

    def setUp(self):
        super().setUp()
        set_capability_enabled(self.profile, "vat", False)
        self.wheat = make_item(item_code="RM-WHEAT", item_name="قمح (كيلو)", is_stock_tracked=True)
        self.milk = make_item(item_code="RM-MILK", item_name="لبن (لتر)", is_stock_tracked=True)
        self.fine = make_item(item_code="FG-FLOUR", item_name="دقيق فاخر (كيلو)", is_stock_tracked=True, default_sale_price="22")
        self.bran = make_item(item_code="BP-BRAN", item_name="ردة (كيلو)", is_stock_tracked=True, default_sale_price="4")
        self.cheese = make_item(item_code="FG-CHEESE", item_name="جبنة (كيلو)", is_stock_tracked=True, default_sale_price="80")
        self.butter = make_item(item_code="FG-BUTTER", item_name="زبدة (كيلو)", is_stock_tracked=True, default_sale_price="160")
        self.whey = make_item(item_code="BP-WHEY", item_name="شرش (لتر)", is_stock_tracked=True)
        stock_in(self.wheat, self.plant, 1500, "12.00", movement_date=BEFORE)
        stock_in(self.milk, self.plant, 2000, "4.00", movement_date=BEFORE)
        # One batch of the mill: 1,000 kg of wheat -> 800 kg of flour and 200 kg of bran (a by-product at 3.00 a kilo).
        self.milling = services.save_recipe({"product": self.fine, "output_quantity": "800"}, [(self.wheat, "1000")], self.owner)
        byproducts.save_outputs(self.milling, self.owner, [self.by(self.bran, "200", "3")])
        # One batch of the dairy: 1,000 litres of milk -> 100 kg of cheese and 50 kg of butter (a co-product at 40%).
        self.dairy = services.save_recipe({"product": self.cheese, "output_quantity": "100"}, [(self.milk, "1000")], self.owner)
        byproducts.save_outputs(self.dairy, self.owner, [self.co(self.butter, "50", "40")])

    # ---- the recipes' rows ----

    @staticmethod
    def by(item, quantity, value):
        return {"item": item, "kind": "by_product", "quantity": quantity, "value_per_unit": value}

    @staticmethod
    def co(item, quantity, percent=""):
        return {"item": item, "kind": "co_product", "quantity": quantity, "share_percent": percent}

    # ---- the floor ----

    def open(self, recipe, quantity, labor="0", overhead="0"):
        return orders.create_order({"recipe": recipe, "location": self.plant, "quantity": quantity, "labor_cost": labor, "overhead_cost": overhead}, self.owner)

    def ready(self, order, defect=None):
        """Every stage done (``defect`` at the second one): the order waits for its finish."""

        for index, _stage in enumerate(order.stages):
            orders.advance(order, self.owner, defect=defect if index == 1 else None)
        order.refresh_from_db()
        return order

    def milled(self, **finish):
        """The plan's example: an order of 800 kg with 1,000.00 of labour; 20 kg are lost at a stage, 780 kg are good."""

        order = self.ready(self.open(self.milling, "800", labor="1000"), defect="20")
        orders.finish(order, self.owner, **finish)
        order.refresh_from_db()
        return order.run

    def churned(self, **finish):
        """The dairy's order: 100 kg of cheese with 600.00 of labour and 400.00 of overhead."""

        order = self.ready(self.open(self.dairy, "100", labor="600", overhead="400"))
        orders.finish(order, self.owner, **finish)
        order.refresh_from_db()
        return order.run

    def sell_item(self, item, quantity, price):
        from sales.pos import checkout, walk_in_customer

        invoice, _ = checkout(lines=[{"item": item, "quantity": D(quantity), "unit_sale_price": D(price), "line_discount_amount": D("0"), "description": ""}],
                              customer=walk_in_customer(), location=self.plant, cashbox=self.cashbox, discount=D("0"),
                              tendered=D(quantity) * D(price), user=self.owner)
        return invoice

    # ---- what is read back ----

    def extra(self, run, item):
        return RunOutput.objects.select_related("operation").get(run=run, item=item)

    def stock(self, item):
        """(units at the plant, stock value, average cost)."""

        return (get_item_location_stock_quantity(item, self.plant), money_round(get_item_stock_value(item)), get_item_authoritative_average_cost(item))

    def lines(self, **amounts):
        """{account code: debit minus credit} from sides named ``fg``, ``raw``, ``wip`` and ``absorbed`` on this chart."""

        found = {}
        for side, amount in amounts.items():
            found[self.CODES[side]] = found.get(self.CODES[side], D("0")) + D(amount)
        return {code: amount for code, amount in found.items() if amount}

    def entry(self, operation, day=None):
        """{account code: debit minus credit} of the entries the ledger made for one stock operation (on ``day`` only, when given)."""

        Projector().rebuild()
        rows = JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id=operation.pk).select_related("account")
        if day is not None:
            rows = rows.filter(entry__entry_date=day)
        found = {}
        for line in rows:
            found[line.account.code] = found.get(line.account.code, D("0")) + line.debit - line.credit
        return {code: amount for code, amount in found.items() if amount}


class BakeryMill(BakeryChart):
    """The same mill on a restaurant bakery's chart: stock is 1105, work in progress and absorbed cost are both 5101."""

    CODES = {"raw": "1105", "wip": "5101", "fg": "1105", "absorbed": "5101"}


# ---------------------------------------------------------------------------------------------------------------
# The milling example
# ---------------------------------------------------------------------------------------------------------------


class MillingTests(Mill):
    def test_the_milling_example_to_the_piastre(self):
        run = self.milled()
        bran = self.extra(run, self.bran)
        # The run: 12,000.00 of wheat and 1,000.00 of labour; 780 kg good, 20 kg lost.
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_quantity, run.scrap_quantity), (D("12000.00"), D("1000.00"), D("780.000"), D("20.000")))
        # Bran: 200 kg at its fixed 3.00 = 600.00, no share of the labour.
        self.assertEqual((bran.kind, bran.quantity, bran.cost, bran.conversion_share, bran.operation.unit_cost),
                         (OutputKind.BY_PRODUCT, D("200.000"), D("600.00"), D("0.00"), D("3.0000")))
        self.assertEqual(self.stock(self.bran), (D("200.000"), D("600.00"), D("3.0000")))
        # Flour: (13,000.00 - 600.00) / 780 = 15.8974 a kilo, worth 12,399.97.
        self.assertEqual(run.output_operation.unit_cost, D("15.8974"))
        self.assertEqual(self.stock(self.fine), (D("780.000"), D("12399.97"), D("15.8974")))
        self.assertEqual(self.stock(self.wheat), (D("500.000"), D("6000.00"), D("12.0000")))
        # The journal: the bran's entry clears 600.00 of work in progress; the flour's entry gives back the other
        # 11,400.00 and books what its value holds beyond that to absorbed production cost: 999.97, not 1,000.00.
        self.assertEqual(self.entry(bran.operation), self.lines(fg="600.00", wip="-600.00"))
        self.assertEqual(self.entry(run.output_operation), self.lines(fg="12399.97", wip="-11400.00", absorbed="-999.97"))
        # Where the 0.03 ends: the outputs are worth 12,999.97 against a joint cost of 13,000.00; work in progress ends at
        # 0.00 and 5102 at -999.97.
        self.books(finished="12999.97", raw=self.RAW - D("12000.00"), wip="0.00", absorbed="999.97")

    def test_a_plain_run_with_a_by_product_and_no_labour(self):
        # One batch: 12,000.00 of wheat; 800 kg of flour at (12,000.00 - 600.00) / 800 = 14.2500 and 200 kg of bran at 3.00.
        run = services.produce(self.milling, self.owner, batches="1", location=self.plant)
        bran = self.extra(run, self.bran)
        self.assertEqual((run.output_quantity, run.scrap_quantity, run.output_operation.unit_cost), (D("800.000"), D("0.000"), D("14.2500")))
        self.assertEqual((self.stock(self.fine), self.stock(self.bran)), ((D("800.000"), D("11400.00"), D("14.2500")), (D("200.000"), D("600.00"), D("3.0000"))))
        self.assertEqual(bran.operation.reference_number, f"{run.number}-OUT02")
        self.assertEqual(bran.operation.destination_location, self.plant)
        self.assertEqual(self.entry(bran.operation), self.lines(fg="600.00", wip="-600.00"))
        self.assertEqual(self.entry(run.output_operation), self.lines(fg="11400.00", wip="-11400.00"))
        self.books(finished="12000.00", raw=self.RAW - D("12000.00"))

    def test_scrap_carries_no_value_and_the_by_product_comes_off_the_cost_once(self):
        run = self.milled()
        # The 20 kg lost are the product's scrap: counted on the run, never a stock movement, never a value.
        self.assertEqual(run.scrap_quantity, D("20.000"))
        self.assertEqual(StockMovement.objects.filter(item=self.fine).count(), 1)
        self.assertEqual(StockMovement.objects.filter(item=self.bran).count(), 1)
        # Flour + bran = the joint cost less the three piastres of the four-decimal unit cost: the bran's 600.00 came off once.
        worth = money_round(get_item_stock_value(self.fine)) + money_round(get_item_stock_value(self.bran))
        self.assertEqual(worth, D("13000.00") - D("0.03"))
        # The same units can never be both: an extra output is neither the recipe's product nor one of its components.
        for row, text in ((self.by(self.fine, "20", "3"), "المنتج الأساسي للوصفة مينفعش يتكتب كمان منتج ثانوي أو مشترك."),
                          (self.by(self.wheat, "20", "3"), "الصنف «قمح (كيلو)» مكوّن في الوصفة نفسها")):
            with self.subTest(item=row["item"].item_code), self.assertRaisesMessage(ValidationError, text):
                byproducts.save_outputs(self.milling, self.owner, [row])

    def test_the_quantity_that_really_came_out_can_be_typed_at_the_finish(self):
        output = self.milling.outputs.get()
        # 180 kg of bran instead of 200: 540.00 comes off; flour (13,000.00 - 540.00) / 780 = 15.9744, worth 12,460.03.
        run = self.milled(extra_outputs={output.pk: "180"})
        bran = self.extra(run, self.bran)
        self.assertEqual((bran.quantity, bran.cost, run.output_operation.unit_cost), (D("180.000"), D("540.00"), D("15.9744")))
        self.assertEqual(self.entry(bran.operation), self.lines(fg="540.00", wip="-540.00"))
        self.assertEqual(self.entry(run.output_operation), self.lines(fg="12460.03", wip="-11460.00", absorbed="-1000.03"))
        self.books(finished="13000.03", raw=self.RAW - D("12000.00"), absorbed="1000.03")

    def test_a_by_product_that_did_not_come_out_leaves_an_ordinary_run(self):
        stock_in(self.wheat, self.plant, 1000, "12.00", movement_date=BEFORE)
        run = self.milled(extra_outputs={self.milling.outputs.get().pk: "0"})
        # No bran: the flour carries the whole 13,000.00, as a recipe with no extra output would (16.6667, worth 13,000.03).
        self.assertFalse(run.outputs.exists())
        self.assertEqual(run.output_operation.unit_cost, D("16.6667"))
        self.assertEqual(self.stock(self.bran)[0], D("0"))
        self.assertEqual(self.entry(run.output_operation), self.lines(fg="13000.03", wip="-12000.00", absorbed="-1000.03"))
        self.books(finished="13000.03", raw=self.RAW, absorbed="1000.03")

    def test_what_cannot_be_split_is_refused_and_leaves_nothing_behind(self):
        order = self.ready(self.open(self.milling, "800", labor="1000"), defect="20")
        output = self.milling.outputs.get()
        before = (StockMovement.objects.count(), StockOperation.objects.count(), ProductionRun.objects.count())
        with self.assertRaisesMessage(ValidationError, "كمية «ردة (كيلو)» اللي طلعت لازم تكون رقم مش سالب."):
            orders.finish(order, self.owner, extra_outputs={output.pk: "-5"})
        with self.assertRaisesMessage(ValidationError, "The quantity of “ردة (كيلو)” that came out must be a number, not negative."):
            orders.finish(order, self.owner, lang="en", extra_outputs={output.pk: "كتير"})
        # A value that swallows the whole run: 200 kg at 70.00 = 14,000.00 against a joint cost of 13,000.00.
        RecipeOutput.objects.filter(pk=output.pk).update(value_per_unit=D("70"))
        with self.assertRaisesMessage(ValidationError, "قيمة المنتجات الثانوية 14,000.00 وتكلفة التشغيلة كلها 13,000.00، فمفيش تكلفة تفضل للمنتج الأساسي"):
            orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, order.run_id), (OrderStatus.IN_PROGRESS, None))
        self.assertEqual((StockMovement.objects.count(), StockOperation.objects.count(), ProductionRun.objects.count()), before)
        self.assertFalse(RunOutput.objects.exists())


class BakeryMillingTests(BakeryMill, MillingTests):
    pass


# ---------------------------------------------------------------------------------------------------------------
# Co-products
# ---------------------------------------------------------------------------------------------------------------


class DairyTests(Mill):
    def test_cheese_and_butter_share_the_joint_cost_60_and_40(self):
        run = self.churned()
        butter = self.extra(run, self.butter)
        self.assertEqual((run.total_cost, run.conversion_cost), (D("4000.00"), D("1000.00")))        # joint cost 5,000.00
        # Butter: 40% of 5,000.00 = 2,000.00 over 50 kg; its share of the labour and overhead is 40% of 1,000.00.
        self.assertEqual((butter.kind, butter.quantity, butter.cost, butter.conversion_share, butter.operation.unit_cost),
                         (OutputKind.CO_PRODUCT, D("50.000"), D("2000.00"), D("400.00"), D("40.0000")))
        # Cheese: the other 60%, 3,000.00 over 100 kg.
        self.assertEqual(run.output_operation.unit_cost, D("30.0000"))
        self.assertEqual((self.stock(self.cheese), self.stock(self.butter)), ((D("100.000"), D("3000.00"), D("30.0000")), (D("50.000"), D("2000.00"), D("40.0000"))))
        # The 5102 credit follows each output's share: 400.00 on the butter's entry, 600.00 on the cheese's.
        self.assertEqual(self.entry(butter.operation), self.lines(fg="2000.00", wip="-1600.00", absorbed="-400.00"))
        self.assertEqual(self.entry(run.output_operation), self.lines(fg="3000.00", wip="-2400.00", absorbed="-600.00"))
        self.books(finished="5000.00", raw=self.RAW - D("4000.00"), absorbed="1000.00")

    def test_with_no_percent_the_split_is_by_sale_value(self):
        byproducts.save_outputs(self.dairy, self.owner, [self.co(self.butter, "50")])
        # Cheese 100 kg x 80.00 = 8,000.00 and butter 50 kg x 160.00 = 8,000.00: half each, 2,500.00 and 2,500.00.
        run = self.churned()
        butter = self.extra(run, self.butter)
        self.assertEqual((butter.cost, butter.conversion_share, butter.operation.unit_cost, run.output_operation.unit_cost),
                         (D("2500.00"), D("500.00"), D("50.0000"), D("25.0000")))
        self.assertEqual(self.entry(butter.operation), self.lines(fg="2500.00", wip="-2000.00", absorbed="-500.00"))
        self.assertEqual(self.entry(run.output_operation), self.lines(fg="2500.00", wip="-2000.00", absorbed="-500.00"))
        self.books(finished="5000.00", raw=self.RAW - D("4000.00"), absorbed="1000.00")

    def test_a_split_by_sale_value_needs_every_sale_price(self):
        byproducts.save_outputs(self.dairy, self.owner, [self.co(self.butter, "50")])
        type(self.butter).objects.filter(pk=self.butter.pk).update(default_sale_price=D("0"))
        order = self.ready(self.open(self.dairy, "100", labor="600", overhead="400"))
        with self.assertRaisesMessage(ValidationError, "التقسيم بقيمة البيع محتاج سعر بيع للصنف «زبدة (كيلو)»؛ اكتب سعره أو حدد نسبة في الوصفة."):
            orders.finish(order, self.owner)
        self.assertFalse(ProductionRun.objects.exists())

    def test_the_by_product_comes_off_first_and_the_co_products_share_the_rest(self):
        byproducts.save_outputs(self.dairy, self.owner, [self.by(self.whey, "400", "0.5"), self.co(self.butter, "50", "40")])
        # Whey 400 litres at 0.50 = 200.00 comes off: 4,800.00 left; butter 40% = 1,920.00, cheese 2,880.00.
        run = self.churned()
        whey, butter = self.extra(run, self.whey), self.extra(run, self.butter)
        self.assertEqual((whey.cost, whey.conversion_share, whey.operation.unit_cost), (D("200.00"), D("0.00"), D("0.5000")))
        self.assertEqual((butter.cost, butter.conversion_share, butter.operation.unit_cost), (D("1920.00"), D("400.00"), D("38.4000")))
        self.assertEqual(run.output_operation.unit_cost, D("28.8000"))
        self.assertEqual([row.operation.reference_number for row in run.outputs.select_related("operation")], [f"{run.number}-OUT02", f"{run.number}-OUT03"])
        self.assertEqual(self.entry(whey.operation), self.lines(fg="200.00", wip="-200.00"))
        self.assertEqual(self.entry(butter.operation), self.lines(fg="1920.00", wip="-1520.00", absorbed="-400.00"))
        self.assertEqual(self.entry(run.output_operation), self.lines(fg="2880.00", wip="-2280.00", absorbed="-600.00"))
        self.books(finished="5000.00", raw=self.RAW - D("4000.00"), absorbed="1000.00")

    def test_a_co_products_piastres_end_in_5102_on_the_products_entry(self):
        byproducts.save_outputs(self.dairy, self.owner, [self.co(self.butter, "900", "33.33")])
        # Butter: 33.33% of 5,000.00 = 1,666.50 over 900 = 1.8517 each, worth 1,666.53 in stock (three piastres more);
        # its share of the labour and overhead is 333.30. Cheese: 3,333.50 over 100 = 33.3350, worth 3,333.50.
        run = self.churned()
        butter = self.extra(run, self.butter)
        self.assertEqual((butter.cost, butter.conversion_share, butter.operation.unit_cost, run.output_operation.unit_cost),
                         (D("1666.50"), D("333.30"), D("1.8517"), D("33.3350")))
        self.assertEqual(self.entry(butter.operation), self.lines(fg="1666.53", wip="-1333.23", absorbed="-333.30"))
        # Work in progress gives the cheese what the butter left of the milk's 4,000.00; the rest of the cheese's value,
        # with the three piastres, is absorbed cost: 666.73.
        self.assertEqual(self.entry(run.output_operation), self.lines(fg="3333.50", wip="-2666.77", absorbed="-666.73"))
        self.books(finished="5000.03", raw=self.RAW - D("4000.00"), absorbed="1000.03")


    def three_co_products(self):
        cream = make_item(item_code="FG-CREAM", item_name="قشطة (كيلو)", is_stock_tracked=True, default_sale_price="120")
        ghee = make_item(item_code="FG-GHEE", item_name="سمنة (كيلو)", is_stock_tracked=True, default_sale_price="300")
        byproducts.save_outputs(self.dairy, self.owner, [self.co(self.butter, "50", "33.33"), self.co(cream, "50", "33.33"), self.co(ghee, "50", "33.33")])
        return cream, ghee

    def test_co_products_take_the_running_totals_piastres_and_never_more_than_is_left(self):
        self.three_co_products()
        # 33.33% of 101.00 is 33.6633: rounded each on its own that is 33.66 three times; by the running total it is
        # 33.66, then 67.33 less 33.66, then 100.99 less 67.33. Together 100.99, which is 99.99% of 101.00 to the piastre.
        shared = byproducts.split(self.dairy, D("1"), D("80.80"), D("20.20"), D("100"))
        self.assertEqual([row.cost for row in shared.rows], [D("33.66"), D("33.67"), D("33.66")])
        self.assertEqual([row.conversion for row in shared.rows], [D("6.73"), D("6.74"), D("6.73")])    # 20.20 the same way: 20.20 less 0.00
        self.assertEqual((shared.main_cost, shared.main_conversion, shared.main_unit_cost), (D("0.01"), D("0.00"), D("0.0001")))
        # Codex's case: 1.01 left and three shares of 33.33% took 0.34 each, 1.02 in all, and the product's cost went
        # below zero. Now they take 1.01 between them, and a product left with nothing is refused in words.
        with self.assertRaisesMessage(ValidationError, "نصيب المنتج الأساسي من تكلفة التشغيلة طلع صفر"):
            byproducts.split(self.dairy, D("1"), D("1.01"), D("0"), D("100"))
        with self.assertRaisesMessage(ValidationError, "The main product's share of the run's cost comes to nothing"):
            byproducts.split(self.dairy, D("1"), D("1.01"), D("0"), D("100"), lang="en")
        # A share that never reaches a piastre is refused too: a co-product that came out is worth something in stock.
        byproducts.save_outputs(self.dairy, self.owner, [self.co(self.butter, "50", "0.01")])
        with self.assertRaisesMessage(ValidationError, "نصيب «زبدة (كيلو)» من تكلفة التشغيلة طلع صفر"):
            byproducts.split(self.dairy, D("1"), D("40"), D("0"), D("100"))

    def test_a_run_whose_product_would_be_worth_nothing_is_refused_and_leaves_no_cost_in_work_in_progress_behind_a_run(self):
        cream, ghee = self.three_co_products()
        order = self.ready(self.open(self.dairy, "1"))                         # 10 litres of milk: 40.00 in work in progress
        before = (StockMovement.objects.count(), StockOperation.objects.count(), ProductionRun.objects.count())
        # The three take 13.33, 13.33 and 13.34: all 40.00. A cheese worth 0.00 would have no entry of its own, and
        # what its entry gives back to work in progress would stay there; so the finish is refused and nothing posts.
        with self.assertRaisesMessage(ValidationError, "نصيب المنتج الأساسي من تكلفة التشغيلة طلع صفر"):
            orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, order.run_id), (OrderStatus.IN_PROGRESS, None))
        self.assertEqual((StockMovement.objects.count(), StockOperation.objects.count(), ProductionRun.objects.count()), before)
        self.assertFalse(RunOutput.objects.exists())
        # The recipe's outputs do not change while its order is on the floor; the person says what really came out.
        # Only the butter did: it takes 33.33% of 40.00, the cheese keeps the rest, and work in progress ends at nothing.
        left_out = {row.pk: "0" for row in self.dairy.outputs.filter(item__in=[cream, ghee])}
        orders.finish(order, self.owner, extra_outputs=left_out)
        order.refresh_from_db()
        self.assertEqual([row.item for row in order.run.outputs.all()], [self.butter])
        self.assertEqual((self.extra(order.run, self.butter).cost, order.run.output_operation.unit_cost), (D("13.33"), D("26.6700")))
        self.books(finished="40.00", raw=self.RAW - D("40.00"), absorbed="0.00")


class BakeryDairyTests(BakeryMill, DairyTests):
    pass


# ---------------------------------------------------------------------------------------------------------------
# The balance sheet: a by-product is finished goods
# ---------------------------------------------------------------------------------------------------------------


class BalanceSheetTests(Mill):
    def account_of(self, item):
        """The account codes the journal keeps this item's stock lines in."""

        Projector().rebuild()
        return set(JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id__in=StockOperation.objects.filter(item=item),
                                              location__isnull=False).values_list("account__code", flat=True))

    def test_bran_sits_under_finished_goods_not_raw_materials(self):
        run = self.milled()
        self.assertEqual(self.account_of(self.bran), {"110503"})
        self.assertEqual((gl.control_balance("finished_goods"), gl.control_balance("raw_materials")), (D("12999.97"), self.RAW - D("12000.00")))
        sheet = statements.balance_sheet()
        rows = {line["account"].code: line["amount"] for line in sheet["sections"]["current_assets"]["lines"]}
        self.assertEqual((rows["110503"], rows["110501"]), (D("12999.97"), D("18850.00")))             # flour 12,399.97 + bran 600.00
        self.assertTrue(sheet["balanced"])
        # Taking the bran off the recipe later does not send what the runs made back to raw materials.
        byproducts.save_outputs(self.milling, self.owner, [])
        self.assertFalse(run.recipe.outputs.exists())
        self.assertEqual(self.account_of(self.bran), {"110503"})
        self.assertEqual(gl.control_balance("finished_goods"), D("12999.97"))

    def test_an_item_a_recipe_lists_as_an_extra_output_is_finished_goods_from_the_next_rebuild(self):
        from inventory.models import StockAdjustmentDirection
        from inventory.services import adjust_stock

        sawdust = make_item(item_code="BP-DUST", item_name="نشارة (كيلو)", is_stock_tracked=True)
        found = adjust_stock("ADJ-DUST-1", TODAY, sawdust, self.plant, StockAdjustmentDirection.IN, D("10"), "رصيد موجود", self.owner, unit_cost=D("2"))
        self.assertIn("110501", self.entry(found))                                                    # bought or found: raw materials
        byproducts.save_outputs(self.milling, self.owner, [self.by(self.bran, "200", "3"), self.by(sawdust, "5", "2")])
        self.assertIn("110503", self.entry(found))                                                    # a recipe makes it now: finished goods
        self.assertNotIn("110501", self.entry(found))


# ---------------------------------------------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------------------------------------------


class CancelTests(Mill):
    def test_cancelling_reverses_the_flour_and_the_bran_then_the_wheat(self):
        run = services.produce(self.milling, self.owner, batches="1", location=self.plant, run_date=BEFORE)
        bran = self.extra(run, self.bran)
        services.cancel_run(run, self.owner, reason="غلطة في التسجيل", reversal_date=TODAY)
        run.refresh_from_db()
        bran.operation.refresh_from_db()
        run.output_operation.refresh_from_db()
        self.assertEqual((run.status, run.output_operation.status, bran.operation.status), (RunStatus.CANCELLED, "cancelled", "cancelled"))
        self.assertEqual([c.operation.status for c in run.consumptions.select_related("operation")], ["cancelled"])
        self.assertEqual((self.stock(self.fine)[:2], self.stock(self.bran)[:2], self.stock(self.wheat)[:2]),
                         ((D("0.000"), D("0.00")), (D("0.000"), D("0.00")), (D("1500.000"), D("18000.00"))))
        # The outputs left before the wheat came back: the reversal rows were written in that order.
        reversals = list(StockMovement.objects.filter(reversal_of__isnull=False).order_by("pk").values_list("item__item_code", flat=True))
        self.assertEqual(reversals, ["FG-FLOUR", "BP-BRAN", "RM-WHEAT"])
        # Each entry of the run's day is mirrored on the day of the cancellation.
        self.assertEqual(self.entry(bran.operation, BEFORE), self.lines(fg="600.00", wip="-600.00"))
        self.assertEqual(self.entry(bran.operation, TODAY), self.lines(fg="-600.00", wip="600.00"))
        self.assertEqual(self.entry(run.output_operation, BEFORE), self.lines(fg="11400.00", wip="-11400.00"))
        self.assertEqual(self.entry(run.output_operation, TODAY), self.lines(fg="-11400.00", wip="11400.00"))
        self.books(finished="0.00", raw=self.RAW)

    def test_an_order_with_labour_cancelled_takes_back_what_each_entry_booked(self):
        run = self.churned()
        butter = self.extra(run, self.butter)
        services.cancel_run(run, self.owner, reason="غلطة في التسجيل")
        # The same day: each operation's entry and its reversal net to nothing, and the milk is back.
        for operation in (butter.operation, run.output_operation):
            self.assertEqual(self.entry(operation), {})
        self.assertEqual((self.stock(self.cheese)[0], self.stock(self.butter)[0], self.stock(self.milk)[:2]), (D("0.000"), D("0.000"), (D("2000.000"), D("8000.00"))))
        self.books(finished="0.00", raw=self.RAW)

    def test_the_cancel_is_refused_once_some_bran_was_sold(self):
        run = self.milled()
        self.sell_item(self.bran, "10", "4")
        with self.assertRaisesMessage(ValidationError, "مينفعش تلغي التشغيلة دي، عشان في كمية من «ردة (كيلو)» خرجت من المخزن بعد ما التشغيلة اتسجلت"):
            services.cancel_run(run, self.owner, reason="غلطة في التسجيل")
        with self.assertRaisesMessage(ValidationError, "This run cannot be cancelled: some “ردة (كيلو)” has left the location since the run was recorded"):
            services.check_cancellable(run, "a mistake", "en")
        run.refresh_from_db()
        self.assertEqual((run.status, run.output_operation.status, self.extra(run, self.bran).operation.status), (RunStatus.POSTED, "posted", "posted"))
        # Nothing moved: 780 kg of flour, 190 kg of bran (10 sold at their 3.00), and the books as the sale left them.
        self.assertEqual((self.stock(self.fine), self.stock(self.bran)), ((D("780.000"), D("12399.97"), D("15.8974")), (D("190.000"), D("570.00"), D("3.0000"))))
        self.books(finished="12969.97", raw=self.RAW - D("12000.00"), absorbed="999.97", cogs="30.00")

    def test_the_cancel_is_refused_once_some_flour_was_sold_and_the_bran_stays(self):
        run = self.milled()
        self.sell_item(self.fine, "5", "22")
        with self.assertRaisesMessage(ValidationError, "«دقيق فاخر (كيلو)» خرجت من المخزن"):
            services.cancel_run(run, self.owner, reason="غلطة في التسجيل")
        self.assertEqual(self.extra(run, self.bran).operation.status, "posted")
        self.assertEqual(self.stock(self.bran)[0], D("200.000"))

    def test_the_stock_screen_leaves_an_extra_output_to_its_run(self):
        run = self.milled()
        bran = self.extra(run, self.bran)
        owner = guards.owners([bran.operation_id])[bran.operation_id]
        self.assertEqual((owner.kind, owner.number, owner.url), ("run", run.number, reverse("manufacturing:run", args=[run.pk])))
        with self.assertRaisesMessage(ValidationError, f"This operation belongs to production run {run.number}"):
            guards.before_stock_operation_reversal(bran.operation_id)
        self.assertEqual(bran.operation.status, "posted")


class BakeryCancelTests(BakeryMill, CancelTests):
    pass


# ---------------------------------------------------------------------------------------------------------------
# Runs without extra outputs, and the rest of the module
# ---------------------------------------------------------------------------------------------------------------


class UnchangedTests(Mill):
    def test_a_recipe_without_extra_outputs_runs_and_books_as_before(self):
        # The bakery's cakes, beside a mill whose recipe has a by-product: one batch is 95.00 for 10 cakes.
        self.assertIsNone(byproducts.split(self.recipe, D("3"), D("285"), D("0"), D("30")))
        plain = services.produce(self.recipe, self.owner, batches="3", location=self.plant)
        made = self.make(quantity="20", labor="60", overhead="40", defects="1")                       # 190.00 + 100.00 over 19 = 15.2632
        self.assertFalse(RunOutput.objects.exists())
        self.assertEqual((plain.output_operation.unit_cost, made.output_operation.unit_cost), (D("9.5000"), D("15.2632")))
        self.assertEqual(self.entry(plain.output_operation), self.lines(fg="285.00", wip="-285.00"))
        self.assertEqual(self.entry(made.output_operation), self.lines(fg="290.00", wip="-190.00", absorbed="-100.00"))
        self.assertEqual(orders.cost_card(made.order).get("extras"), None)
        self.books(finished="575.00", raw=self.RAW - D("475.00"), absorbed="100.00")
        services.cancel_run(plain, self.owner, reason="غلطة في التسجيل")
        self.books(finished="290.00", raw=self.RAW - D("190.00"), absorbed="100.00")

    def test_the_quality_hold_takes_the_product_and_the_by_product_goes_to_the_orders_place(self):
        quality.save_recipe_quality(self.milling, self.owner, needs_release=True, rows=[])
        run = self.milled()
        hold = quality.hold_place(self.plant.entity_id, create=False)
        bran = self.extra(run, self.bran)
        self.assertEqual((run.output_operation.destination_location, bran.operation.destination_location), (hold, self.plant))
        self.assertEqual((get_item_location_stock_quantity(self.fine, hold), get_item_location_stock_quantity(self.fine, self.plant)), (D("780.000"), D("0")))
        self.assertEqual(self.stock(self.bran)[0], D("200.000"))
        self.assertEqual(quality.position(quality.order_subject(run.order))["waiting"], D("780.000"))  # the check still decides on the flour alone
        self.books(finished="12999.97", raw=self.RAW - D("12000.00"), absorbed="999.97")

    def test_a_recipe_that_needs_release_takes_no_co_product_either_way(self):
        with self.assertRaisesMessage(ValidationError, "الوصفة دي فيها منتج مشترك، والفحص بيخص المنتج الأساسي بس"):
            quality.save_recipe_quality(self.dairy, self.owner, needs_release=True, rows=[])
        self.dairy.refresh_from_db()
        self.assertFalse(self.dairy.needs_release)
        quality.save_recipe_quality(self.milling, self.owner, needs_release=True, rows=[])
        with self.assertRaisesMessage(ValidationError, "الوصفة دي محتاجة إفراج جودة، والفحص بيخص المنتج الأساسي بس"):
            byproducts.save_outputs(self.milling, self.owner, [self.by(self.bran, "200", "3"), self.co(self.butter, "5", "10")])
        self.assertEqual(list(self.milling.outputs.values_list("item__item_code", flat=True)), ["BP-BRAN"])

    def test_with_lots_on_the_product_gets_its_lot_and_the_run_is_whole(self):
        set_capability_enabled(self.profile, "batches_expiry", True)
        run = self.milled()
        self.assertEqual(list(Batch.objects.filter(production_run=run).values_list("item__item_code", "quantity")), [("FG-FLOUR", D("780.000"))])
        self.assertEqual(self.extra(run, self.bran).quantity, D("200.000"))                           # no lot is kept for an extra output (not built)

    def test_the_cost_card_and_the_variance_read_the_products_own_cost(self):
        order = self.open(self.milling, "800", labor="1000")
        card = orders.cost_card(order)                                                                # before the start: today's costs
        self.assertEqual((card["stage"], card["total"], card["extras"], card["per_unit"]), ("estimate", D("13000.00"), D("600.00"), D("15.5000")))
        order = self.ready(order, defect="20")
        card = orders.cost_card(order)                                                                # on the floor: 780 good units
        self.assertEqual((card["stage"], card["extras"], card["per_unit"]), ("issued", D("600.00"), D("15.8974")))
        orders.finish(order, self.owner)
        order.refresh_from_db()
        card = orders.cost_card(order)
        self.assertEqual((card["stage"], card["total"], card["extras"], card["per_unit"], card["scrap"]), ("posted", D("13000.00"), D("600.00"), D("15.8974"), D("20.000")))
        # The standard leaves the bran its 600.00 too: (12,000.00 + 1,000.00 - 600.00) / 800 = 15.5000 against 15.8974.
        row = variance.order_variance(order)
        self.assertEqual((row["standard_unit"], row["unit"], row["diff_unit"]), (D("15.5000"), D("15.8974"), D("0.3974")))

    def test_the_board_shows_the_products_own_materials_per_unit(self):
        self.client.force_login(self.owner)
        rows = {row["recipe"].code: row["unit_cost"] for row in self.client.get(reverse("manufacturing:home")).context["products"]}
        # Flour: (12,000.00 - 600.00) / 800; cheese: 60% of 4,000.00 over 100; cakes: 95.00 / 10, as ever.
        self.assertEqual((rows[self.milling.code], rows[self.dairy.code], rows[self.recipe.code]), (D("14.2500"), D("24.0000"), D("9.5000")))


class BakeryUnchangedTests(BakeryMill, UnchangedTests):
    pass


# ---------------------------------------------------------------------------------------------------------------
# The recipe's rules
# ---------------------------------------------------------------------------------------------------------------


class RuleTests(Mill):
    def test_what_a_recipe_refuses_as_an_extra_output(self):
        service = make_item(item_code="SRV-GRIND", item_name="طحن", is_stock_tracked=False)
        cases = (
            ([self.by(service, "1", "3")], "الصنف «طحن» لازم يكون متتبع في المخزون."),
            ([self.by(self.bran, "1", "3"), self.co(self.bran, "1", "10")], "الصنف «ردة (كيلو)» متكرر؛ اكتبه في سطر واحد."),
            ([self.by(self.bran, "0", "3")], "كمية «ردة (كيلو)» لكل دفعة لازم تكون رقم أكبر من صفر."),
            ([{"item": self.bran, "kind": "", "quantity": "5"}], "اختار نوع «ردة (كيلو)»: منتج ثانوي أو منتج مشترك."),
            ([self.by(self.bran, "5", "")], "اكتب قيمة الوحدة من «ردة (كيلو)» برقم أكبر من صفر"),
            ([self.by(self.bran, "5", "0")], "اكتب قيمة الوحدة من «ردة (كيلو)» برقم أكبر من صفر"),
            ([self.co(self.butter, "5", "100")], "نسبة «زبدة (كيلو)» لازم تكون رقم أكبر من صفر وأقل من 100"),
            ([self.co(self.butter, "5", "نص")], "نسبة «زبدة (كيلو)» لازم تكون رقم أكبر من صفر وأقل من 100"),
            ([self.co(self.butter, "5", "40"), self.co(self.whey, "5")], "اكتب نسبة لكل منتج مشترك، أو سيبهم كلهم فاضيين"),
            ([self.co(self.butter, "5", "60"), self.co(self.whey, "5", "40")], "نسب المنتجات المشتركة مجموعها 100%"),
        )
        for rows, text in cases:
            with self.subTest(text=text), self.assertRaisesMessage(ValidationError, text):
                byproducts.save_outputs(self.milling, self.owner, rows)
        self.assertEqual(list(self.milling.outputs.values_list("item__item_code", "kind", "quantity", "value_per_unit", "share_percent")),
                         [("BP-BRAN", "by_product", D("200.000"), D("3.0000"), None)])                # every refusal left the list as it was

    def test_the_recipe_cannot_make_an_extra_output_its_product_or_a_component(self):
        with self.assertRaisesMessage(ValidationError, "الصنف «ردة (كيلو)» مكوّن في الوصفة نفسها"):
            services.save_recipe({"product": self.fine, "output_quantity": "800"}, [(self.wheat, "1000"), (self.bran, "5")], self.owner, self.milling)
        with self.assertRaisesMessage(ValidationError, "المنتج الأساسي للوصفة مينفعش يتكتب كمان منتج ثانوي أو مشترك."):
            services.save_recipe({"product": self.bran, "output_quantity": "800"}, [(self.wheat, "1000")], self.owner, self.milling)
        services.save_recipe({"product": self.fine, "output_quantity": "800", "name": "طحن القمح"}, [(self.wheat, "1000")], self.owner, self.milling)
        self.assertEqual(self.milling.outputs.count(), 1)                                             # an ordinary edit keeps the extra outputs

    def test_the_extra_outputs_do_not_change_while_an_order_is_on_the_floor(self):
        order = self.open(self.milling, "800", labor="1000")
        orders.advance(order, self.owner)
        with self.assertRaisesMessage(ValidationError, "في أمر إنتاج شغال في المصنع بالوصفة دي وخاماته اتصرفت"):
            byproducts.save_outputs(self.milling, self.owner, [self.by(self.bran, "200", "5")])
        self.assertEqual(self.milling.outputs.get().value_per_unit, D("3.0000"))


# ---------------------------------------------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------------------------------------------


class ScreenTests(Mill):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def recipe_url(self, recipe=None):
        return reverse("manufacturing:recipe", args=[(recipe or self.milling).pk])

    def test_the_recipe_page_shows_the_section_the_split_and_the_form(self):
        page = self.client.get(self.recipe_url(), {"batches": "1"})
        self.assertContains(page, "<h2 data-outputs-section>منتجات ثانوية ومشتركة</h2>")
        self.assertContains(page, 'data-recipe-output="BP-BRAN" data-output-kind="by_product"')
        self.assertContains(page, '<div class="mfg-title">ردة (كيلو)<small class="op-muted">منتج ثانوي</small></div>')
        self.assertContains(page, "<strong data-main-unit-cost>14.25</strong>")                       # (12,000.00 - 600.00) / 800
        self.assertContains(page, "<strong data-plan-unit-cost>14.25</strong>")                       # the plan's own tile says the same
        self.assertContains(page, "<strong data-outputs-deducted>600.00</strong>")
        self.assertContains(page, "data-outputs-form")
        self.assertContains(page, 'name="out_value_0" value="3"')
        self.assertContains(page, f'name="output_qty_{self.milling.outputs.get().pk}" value="200"')    # the plain run asks what really came out
        self.assertContains(page, "الهالك اللي بيتباع يتسجّل هنا منتج ثانوي بقيمته")
        self.assertContains(page, "بيظهر في الميزانية ضمن مخزون المنتج التام")
        english = self.client.get(self.recipe_url(self.dairy), {"lang": "en", "batches": "2"})
        self.assertContains(english, "<h2 data-outputs-section>By-products and co-products</h2>")
        self.assertContains(english, '<div class="mfg-title">زبدة (كيلو)<small class="op-muted">Co-product</small></div>')
        self.assertContains(english, '<bdi dir="ltr">40%</bdi>')
        self.assertContains(english, '<bdi dir="ltr">60%</bdi>')                                      # the main product's share
        self.assertContains(english, "<strong data-main-unit-cost>24.00</strong>")                    # 60% of 8,000.00 over 200 kg
        plain = self.client.get(self.recipe_url(self.recipe))
        self.assertContains(plain, "data-outputs-none")
        self.assertContains(plain, "<strong data-plan-unit-cost>9.50</strong>")                       # a recipe with no extra output: as before
        self.assertNotContains(plain, "data-output-inputs")

    def test_the_form_saves_and_a_refusal_keeps_what_was_typed(self):
        data = {"action": "outputs", "out_item_0": self.bran.pk, "out_kind_0": "by_product", "out_qty_0": "190", "out_value_0": "3.5",
                "out_item_1": self.whey.pk, "out_kind_1": "co_product", "out_qty_1": "10", "out_percent_1": "5"}
        saved = self.client.post(self.recipe_url(), data, follow=True)
        self.assertContains(saved, "اتحفظت المنتجات الثانوية والمشتركة.")
        self.assertEqual(list(self.milling.outputs.values_list("item__item_code", "kind", "quantity", "value_per_unit", "share_percent")),
                         [("BP-BRAN", "by_product", D("190.000"), D("3.5000"), None), ("BP-WHEY", "co_product", D("10.000"), None, D("5.00"))])
        refused = self.client.post(self.recipe_url(), {**data, "out_value_0": ""})
        self.assertContains(refused, 'data-outputs-error>اكتب قيمة الوحدة من «ردة (كيلو)» برقم أكبر من صفر')
        self.assertContains(refused, 'name="out_qty_0" value="190"')
        self.assertEqual(self.milling.outputs.get(item=self.bran).value_per_unit, D("3.5000"))
        english = self.client.post(self.recipe_url() + "?lang=en", {**data, "lang": "en", "out_kind_1": ""})
        self.assertContains(english, "Choose what “شرش (لتر)” is: a by-product or a co-product.")

    def test_a_plain_run_from_the_recipe_page_takes_the_typed_quantity(self):
        output = self.milling.outputs.get()
        done = self.client.post(self.recipe_url(), {"action": "produce", "batches": "1", "location": self.plant.pk, f"output_qty_{output.pk}": "150"}, follow=True)
        run = ProductionRun.objects.get()
        self.assertEqual((self.extra(run, self.bran).quantity, run.output_operation.unit_cost), (D("150.000"), D("14.4375")))   # (12,000.00 - 450.00) / 800
        self.assertContains(done, "<h2 data-outputs-table>مخرجات التشغيلة</h2>")
        self.assertContains(done, 'data-run-output="FG-FLOUR" data-main-output')
        self.assertContains(done, '<div class="mfg-title">دقيق فاخر (كيلو)<small class="op-muted">المنتج الأساسي</small></div>')
        self.assertContains(done, 'data-run-output="BP-BRAN"')
        self.assertContains(done, "<td data-output-unit-cost>3.00</td><td data-output-value>450.00</td>")
        self.assertContains(done, "<td data-output-unit-cost>14.4375</td><td data-output-value>11,550.00</td>")

    def test_the_order_page_before_and_after_the_finish(self):
        order = self.ready(self.open(self.milling, "800", labor="1000"), defect="20")
        output = self.milling.outputs.get()
        url = reverse("manufacturing:order", args=[order.pk])
        page = self.client.get(url)
        self.assertContains(page, "<h2 data-outputs-table>المخرجات المتوقعة من الأمر</h2>")
        self.assertContains(page, "<h3 data-output-inputs>الكميات اللي طلعت فعلًا</h3>")
        self.assertContains(page, f'name="output_qty_{output.pk}" value="200"')
        self.assertContains(page, "<td data-output-unit-cost>15.8974</td><td data-output-value>12,399.97</td>")
        self.assertContains(page, "data-cost-extras><small>منتجات ثانوية ومشتركة (مخصومة من تكلفة المنتج)</small><strong>600.00</strong>")
        self.assertContains(page, "<strong data-cost-per-unit>15.8974</strong>")
        self.client.post(url, {"action": "finish", f"output_qty_{output.pk}": "200"})
        order.refresh_from_db()
        self.assertEqual((order.status, self.extra(order.run, self.bran).quantity, order.run.output_operation.unit_cost), (OrderStatus.DONE, D("200.000"), D("15.8974")))
        page = self.client.get(url, {"lang": "en"})
        self.assertContains(page, "<h2 data-outputs-table>The order&#x27;s outputs</h2>")
        self.assertContains(page, '<span class="op-status op-status-posted">In stock</span>')
        self.assertContains(page, "<td data-output-unit-cost>15.8974</td><td data-output-value>12,399.97</td>")
        self.assertContains(page, "<td data-output-unit-cost>3.00</td><td data-output-value>600.00</td>")
        self.assertNotContains(page, "data-output-inputs")
        plain = self.client.get(reverse("manufacturing:order", args=[self.open(self.recipe, "20").pk]))
        for marker in ("data-outputs-table", "data-output-inputs", "data-cost-extras"):
            self.assertNotContains(plain, marker)

    def test_a_cancelled_run_says_each_output_was_reversed(self):
        run = self.milled()
        services.cancel_run(run, self.owner, reason="غلطة في التسجيل")
        page = self.client.get(reverse("manufacturing:run", args=[run.pk]))
        self.assertContains(page, '<span class="op-status op-status-cancelled">اترجع</span>', count=2)
        self.assertNotContains(page, '<span class="op-status op-status-posted">في المخزون</span>')

    def test_the_pages_use_no_banned_word_and_no_arabic_line_starts_or_ends_with_a_latin_word(self):
        root = pathlib.Path(settings.BASE_DIR)
        files = ["manufacturing/byproducts.py", "manufacturing/byproducts_views.py"] + [f"templates/manufacturing/{name}" for name in (
            "_outputs.html", "_outputs_inputs.html", "_outputs_recipe.html")]
        for name in files:
            text = (root / name).read_text(encoding="utf-8")
            for word in BANNED_WORDS:
                self.assertNotIn(word, text, name)
        for label, words in (("screens", byproducts_views.WORDS["ar"]), ("rules", byproducts.MESSAGES["ar"])):
            for key, text in words.items():
                first, last = edges(text)
                with self.subTest(words=label, key=key):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)
                    self.assertFalse(first.startswith("{") or last.endswith("}"), text)               # a code or a number never opens or closes the line
        stock_in(self.wheat, self.plant, 3000, "12.00", movement_date=BEFORE)
        waiting = self.ready(self.open(self.milling, "800", labor="1000"), defect="20")
        done = self.milled()
        cancelled = self.milled()
        services.cancel_run(cancelled, self.owner, reason="غلطة في التسجيل")
        pages = [self.recipe_url(), self.recipe_url(self.dairy), reverse("manufacturing:order", args=[waiting.pk]), reverse("manufacturing:order", args=[done.order.pk]),
                 reverse("manufacturing:run", args=[done.pk]), reverse("manufacturing:run", args=[cancelled.pk])]
        for url in pages:
            html = self.client.get(url).content.decode()
            for word in BANNED_WORDS:
                self.assertNotIn(word, html, url)
            for text in sentences(html):
                first, last = edges(text)
                with self.subTest(url=url, text=text[:50]):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)


# ---------------------------------------------------------------------------------------------------------------
# Permissions and entities
# ---------------------------------------------------------------------------------------------------------------


class PermissionTests(Mill):
    MONEY = ("15.8974", "12,399.97", "600.00", "14.25", "12,000.00", "13,000.00")
    ROW = {"action": "outputs", "out_kind_0": "by_product", "out_qty_0": "200", "out_value_0": "9"}

    def test_the_rule_asks_for_the_items_and_the_cost_permissions_together(self):
        recipes_only = limited("by_recipes", "inventory.view_stock", "master_data.manage_items")
        cost_only = limited("by_cost", "inventory.view_stock", "inventory.view_cost")
        both = limited("by_both", "master_data.manage_items", "inventory.view_cost")
        for user in (recipes_only, cost_only):
            with self.assertRaises(PermissionDenied):
                byproducts.save_outputs(self.milling, user, [self.by(self.bran, "200", "9")])
        self.assertEqual(self.milling.outputs.get().value_per_unit, D("3.0000"))
        byproducts.save_outputs(self.milling, both, [self.by(self.bran, "200", "9")])
        self.assertEqual(self.milling.outputs.get().value_per_unit, D("9.0000"))

    def test_someone_who_may_not_see_cost_reads_quantities_and_no_money(self):
        stock_in(self.wheat, self.plant, 1000, "12.00", movement_date=BEFORE)
        order = self.ready(self.open(self.milling, "800", labor="1000"), defect="20")
        done = self.milled()
        keeper = limited("by_keeper", "inventory.view_stock", "inventory.adjust_stock")                # the floor: moves stock, sees no cost
        self.client.force_login(keeper)
        urls = (reverse("manufacturing:recipe", args=[self.milling.pk]), reverse("manufacturing:order", args=[order.pk]),
                reverse("manufacturing:order", args=[done.order.pk]), reverse("manufacturing:run", args=[done.pk]))
        for url in urls:
            for lang in ("ar", "en"):
                text = self.client.get(url, {"lang": lang, "batches": "1"}).content.decode()
                with self.subTest(url=url, lang=lang):
                    self.assertIn("ردة (كيلو)", text)                                                 # the output and its quantity are said
                    for figure in self.MONEY:
                        self.assertNotIn(figure, text)
                    for marker in ("data-output-unit-cost", "data-output-value", "data-main-unit-cost", "data-outputs-deducted", "data-cost-extras",
                                   "data-outputs-form", 'name="out_value_0"'):
                        self.assertNotIn(marker, text)
        self.assertContains(self.client.get(urls[3]), 'data-run-output="BP-BRAN"')
        self.assertContains(self.client.get(urls[1]), f'name="output_qty_{self.milling.outputs.get().pk}"')   # the quantity that came out is not money
        # No by-product value is saved, and no order is finished, without the cost permission.
        self.assertEqual(self.client.post(urls[0], {**self.ROW, "out_item_0": self.bran.pk}).status_code, 403)
        self.assertEqual(self.client.post(urls[1], {"action": "finish"}).status_code, 403)
        order.refresh_from_db()
        self.assertEqual((self.milling.outputs.get().value_per_unit, order.status), (D("3.0000"), OrderStatus.IN_PROGRESS))

    def test_the_recipe_writer_without_cost_sees_no_value_and_no_form(self):
        self.client.force_login(limited("by_writer", "inventory.view_stock", "master_data.manage_items"))
        page = self.client.get(reverse("manufacturing:recipe", args=[self.milling.pk]))
        self.assertContains(page, "data-outputs-view-only")
        self.assertContains(page, 'data-recipe-output="BP-BRAN"')
        self.assertNotContains(page, "data-outputs-form")
        self.assertNotContains(page, 'name="out_value_0"')
        self.assertEqual(self.client.post(reverse("manufacturing:recipe", args=[self.milling.pk]), {**self.ROW, "out_item_0": self.bran.pk}).status_code, 403)
        self.assertEqual(self.milling.outputs.get().value_per_unit, D("3.0000"))

    def test_a_reader_of_stock_cannot_cancel_a_run_and_a_cashier_sees_nothing(self):
        run = self.milled()
        self.client.force_login(limited("by_reader", "inventory.view_stock"))
        self.assertEqual(self.client.post(reverse("manufacturing:run", args=[run.pk]), {"reason": "x"}).status_code, 403)
        from permissions.models import RoleCode

        from .tests import person

        self.client.force_login(person(RoleCode.CASHIER, "by_cashier"))
        for url in (reverse("manufacturing:recipe", args=[self.milling.pk]), reverse("manufacturing:run", args=[run.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)
        run.refresh_from_db()
        self.assertEqual((run.status, self.extra(run, self.bran).operation.status), (RunStatus.POSTED, "posted"))


class EntityTests(Mill):
    def test_a_branchs_run_keeps_its_outputs_in_the_branch_and_is_not_opened_from_another_entity(self):
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BRM", "name_ar": "مطحن الفرع", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        branch_plant = make_location(location_code="BR-MILL", name_ar="مطحن الفرع", name_en="Branch mill", entity=branch)
        stock_in(self.wheat, branch_plant, 1000, "12.00", movement_date=BEFORE)
        near = self.milled()
        far = services.produce(self.milling, self.owner, batches="1", location=branch_plant)
        bran = self.extra(far, self.bran)
        # The by-product entered the branch's own place, and its entry is the branch's.
        self.assertEqual((bran.operation.destination_location, get_item_location_stock_quantity(self.bran, branch_plant)), (branch_plant, D("200.000")))
        Projector().rebuild()
        self.assertEqual(set(JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id=bran.operation_id).values_list("entity_id", flat=True)),
                         {branch.pk})
        self.assertTrue(gl.trial_balance(entity=branch)["balanced"])
        main = Entity.objects.get(is_main=True)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": main.pk, "next": "/dashboard/"})
        self.assertEqual(self.client.get(reverse("manufacturing:run", args=[near.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("manufacturing:run", args=[far.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("manufacturing:run", args=[far.pk]), {"reason": "من كيان تاني"}).status_code, 404)
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        page = self.client.get(reverse("manufacturing:run", args=[far.pk]))
        self.assertContains(page, 'data-run-output="BP-BRAN"')
        self.assertEqual(self.client.get(reverse("manufacturing:run", args=[near.pk])).status_code, 404)
        far.refresh_from_db()
        self.assertEqual(far.status, RunStatus.POSTED)
