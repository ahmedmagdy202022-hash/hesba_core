"""A characterization of how the general ledger books a production run today, shape by shape.

Written before Manufacturing F9 (HG-139) touched ``ledger/projector.py`` and left unedited by it: it passed on the
commit before the projector change and passes after it, so every run without an extra output books exactly as it did.

Each shape pins the journal lines of every stock operation the run owns (its materials and its product), per account
code, to the piastre, and the control balances the scenario ends with:

* a plain run, and the same run cancelled on a later day (the reversal entries);
* an order with labour and overhead (F12): an extra issue, a return, an actual overhead line, a defect;
* an order whose four-decimal unit cost leaves three piastres (F12's residue rule);
* an order finished into the quality hold place (F5);
* an order with an outside processing fee (F4);
* a restaurant kitchen preparation (``restaurant.kitchen``).

The factory shapes run twice: on the manufacturing chart (110501 raw materials, 110502 work in progress, 110503
finished goods, 5102 absorbed production cost) and on a restaurant bakery's chart with the manufacturing module on
(one inventory account, 1105; work in progress and absorbed cost both fall back to the cost of sales, 5101). The
kitchen preparation runs on the restaurant's own chart, the only one a kitchen has.

The stock the fixtures start with comes in through the test factory's bare movements, which the ledger books as a stock
gain; it is pinned with the rest so nothing else can move unseen.

The bakery: flour 20.00 a kilo, sugar 30.00, a box 2.50, wheat 12.00. Cakes: one batch is 2 kg flour + 1 kg sugar +
10 boxes = 95.00 for 10 cakes. Fine flour: one batch is 1,000 kg of wheat = 12,000.00 for 780 kg.
"""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_item, make_location, make_supplier, stock_in
from ledger import reports as gl
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from purchases.services import post_purchase_invoice
from reports.tests_dashboard import prepared_client
from restaurant import kitchen
from restaurant.tests_kitchen import KitchenSetup
from settings_core.capabilities import set_capability_enabled

from . import actuals, orders, outside, quality, services
from .models import OrderStatus, ProductionRun
from .tests import person
from .tests_f11 import BakeryChart

TODAY = timezone.localdate()
BEFORE = TODAY - timedelta(days=3)
CONTROLS = ("inventory", "raw_materials", "wip", "finished_goods", "production_cost", "cogs", "stock_loss", "stock_gain", "general_expense", "suspense")


def journal(run):
    """{operation: [(days after the run date, account code, debit, credit)]} for every stock operation ``run`` owns:
    its materials (the order's issues, extras and returns) and its product."""

    Projector().rebuild()
    found = {}
    operations = [consumption.operation for consumption in run.consumptions.select_related("operation")] + [run.output_operation]
    for operation in operations:
        lines = (JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id=operation.pk)
                 .select_related("account", "entry").order_by("entry__entry_date", "account__code", "debit", "credit"))
        found[re.sub(r"-\d{8}-", "-", operation.reference_number)] = [
            ((line.entry.entry_date - run.run_date).days, line.account.code, str(line.debit), str(line.credit)) for line in lines]
    return found


def controls():
    """Every control account of the list that holds anything, after a rebuild."""

    Projector().rebuild()
    return {key: str(value) for key in CONTROLS if (value := gl.control_balance(key))}


class FactoryShapes(TestCase):
    ACTIVITY, SUB = "manufacturing", "food"
    MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,manufacturing"

    PLAIN = {
        "MO-001-C01": [(0, "110501", "0.00", "120.00"), (0, "110502", "120.00", "0.00")],
        "MO-001-C02": [(0, "110501", "0.00", "90.00"), (0, "110502", "90.00", "0.00")],
        "MO-001-C03": [(0, "110501", "0.00", "75.00"), (0, "110502", "75.00", "0.00")],
        "MO-001-OUT": [(0, "110502", "0.00", "285.00"), (0, "110503", "285.00", "0.00")],
    }
    PLAIN_CONTROLS = {"raw_materials": "16565.00", "finished_goods": "285.00", "stock_gain": "-16850.00"}
    PLAIN_CANCELLED = {
        "MO-001-C01": [(0, "110501", "0.00", "120.00"), (0, "110502", "120.00", "0.00"), (3, "110501", "120.00", "0.00"), (3, "110502", "0.00", "120.00")],
        "MO-001-C02": [(0, "110501", "0.00", "90.00"), (0, "110502", "90.00", "0.00"), (3, "110501", "90.00", "0.00"), (3, "110502", "0.00", "90.00")],
        "MO-001-C03": [(0, "110501", "0.00", "75.00"), (0, "110502", "75.00", "0.00"), (3, "110501", "75.00", "0.00"), (3, "110502", "0.00", "75.00")],
        "MO-001-OUT": [(0, "110502", "0.00", "285.00"), (0, "110503", "285.00", "0.00"), (3, "110502", "285.00", "0.00"), (3, "110503", "0.00", "285.00")],
    }
    PLAIN_CANCELLED_CONTROLS = {"raw_materials": "16850.00", "stock_gain": "-16850.00"}
    # Debit finished goods 300.00; credit work in progress its 195.00 (190.00 + 20.00 - 15.00); credit 5102 the 105.00.
    ORDER = {
        "PO-001-I01": [(0, "110501", "0.00", "80.00"), (0, "110502", "80.00", "0.00")],
        "PO-001-I02": [(0, "110501", "0.00", "60.00"), (0, "110502", "60.00", "0.00")],
        "PO-001-I03": [(0, "110501", "0.00", "50.00"), (0, "110502", "50.00", "0.00")],
        "PO-001-X01": [(0, "110501", "0.00", "20.00"), (0, "110502", "20.00", "0.00")],
        "PO-001-R01": [(0, "110501", "15.00", "0.00"), (0, "110502", "0.00", "15.00")],
        "MO-001-OUT": [(0, "110502", "0.00", "195.00"), (0, "110503", "300.00", "0.00"), (0, "5102", "0.00", "105.00")],
    }
    ORDER_CONTROLS = {"raw_materials": "16655.00", "finished_goods": "300.00", "production_cost": "-105.00", "stock_gain": "-16850.00"}
    RESIDUE = {
        "PO-001-I01": [(0, "110501", "0.00", "12000.00"), (0, "110502", "12000.00", "0.00")],
        "MO-001-OUT": [(0, "110502", "0.00", "12000.00"), (0, "110503", "13000.03", "0.00"), (0, "5102", "0.00", "1000.03")],
    }
    RESIDUE_CONTROLS = {"raw_materials": "4850.00", "finished_goods": "13000.03", "production_cost": "-1000.03", "stock_gain": "-16850.00"}
    HOLD = {
        "PO-001-I01": [(0, "110501", "0.00", "80.00"), (0, "110502", "80.00", "0.00")],
        "PO-001-I02": [(0, "110501", "0.00", "60.00"), (0, "110502", "60.00", "0.00")],
        "PO-001-I03": [(0, "110501", "0.00", "50.00"), (0, "110502", "50.00", "0.00")],
        "MO-001-OUT": [(0, "110502", "0.00", "190.00"), (0, "110503", "290.00", "0.00"), (0, "5102", "0.00", "100.00")],
    }
    HOLD_CONTROLS = {"raw_materials": "16660.00", "finished_goods": "290.00", "production_cost": "-100.00", "stock_gain": "-16850.00"}
    OUTSIDE = {
        "PO-001-I01": [(0, "110501", "0.00", "80.00"), (0, "110502", "80.00", "0.00")],
        "PO-001-I02": [(0, "110501", "0.00", "60.00"), (0, "110502", "60.00", "0.00")],
        "PO-001-I03": [(0, "110501", "0.00", "50.00"), (0, "110502", "50.00", "0.00")],
        "MO-001-OUT": [(0, "110502", "0.00", "190.00"), (0, "110503", "330.00", "0.00"), (0, "5102", "0.00", "140.00")],
    }
    OUTSIDE_CONTROLS = {"raw_materials": "16660.00", "finished_goods": "330.00", "production_cost": "-140.00", "general_expense": "40.00",
                        "stock_gain": "-16850.00"}

    def setUp(self):
        self.profile = prepared_client(activity=self.ACTIVITY, sub_activity=self.SUB, modules=self.MODULES)
        set_capability_enabled(self.profile, "vat", False)
        self.owner = person(RoleCode.OWNER, "shape_owner")
        self.plant = make_location(location_code="PLANT", is_default=True)
        self.cashbox = make_cashbox()
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق (كيلو)", is_stock_tracked=True)
        self.sugar = make_item(item_code="RM-SUGAR", item_name="سكر (كيلو)", is_stock_tracked=True)
        self.box = make_item(item_code="PK-BOX", item_name="علبة", is_stock_tracked=True)
        self.wheat = make_item(item_code="RM-WHEAT", item_name="قمح (كيلو)", is_stock_tracked=True)
        self.cake = make_item(item_code="FG-CAKE", item_name="كيكة", is_stock_tracked=True, default_sale_price="120")
        self.fine = make_item(item_code="FG-FINE", item_name="دقيق فاخر (كيلو)", is_stock_tracked=True, default_sale_price="22")
        stock_in(self.flour, self.plant, 200, "20.00", movement_date=BEFORE)
        stock_in(self.sugar, self.plant, 20, "30.00", movement_date=BEFORE)
        stock_in(self.box, self.plant, 100, "2.50", movement_date=BEFORE)
        stock_in(self.wheat, self.plant, 1000, "12.00", movement_date=BEFORE)          # raw materials: 4,850.00 + 12,000.00 = 16,850.00
        self.recipe = services.save_recipe({"product": self.cake, "output_quantity": "10"},
                                           [(self.flour, "2"), (self.sugar, "1"), (self.box, "10")], self.owner)
        self.milling = services.save_recipe({"product": self.fine, "output_quantity": "780"}, [(self.wheat, "1000")], self.owner)

    def open(self, recipe=None, quantity="20", labor="60", overhead="40"):
        return orders.create_order({"recipe": recipe or self.recipe, "location": self.plant, "quantity": quantity,
                                    "labor_cost": labor, "overhead_cost": overhead}, self.owner)

    def walk(self, order, defect_at=None, defect=None):
        order.refresh_from_db()
        while order.stage_index < len(order.stages):
            orders.advance(order, self.owner, defect=defect if order.stage_index == defect_at else None)
            order.refresh_from_db()
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        return order.run

    def pinned(self, run, lines, balances):
        self.assertEqual(journal(run), lines)
        self.assertEqual(controls(), balances)
        self.assertTrue(gl.trial_balance()["balanced"])

    # ---- a plain run, and its cancellation on a later day ----

    def test_a_plain_run_and_its_cancellation_on_a_later_day(self):
        run = services.produce(self.recipe, self.owner, batches="3", location=self.plant, run_date=BEFORE)
        self.assertEqual((run.total_cost, run.output_operation.unit_cost), (D("285.00"), D("9.5000")))
        self.pinned(run, self.PLAIN, self.PLAIN_CONTROLS)
        services.cancel_run(run, self.owner, reason="غلطة في التسجيل", reversal_date=TODAY)
        self.pinned(ProductionRun.objects.get(pk=run.pk), self.PLAIN_CANCELLED, self.PLAIN_CANCELLED_CONTROLS)

    # ---- an order with labour and overhead (F12) ----

    def test_an_order_with_labour_overhead_an_extra_issue_and_a_return(self):
        # 20 cakes: 4 kg flour (80.00) + 2 kg sugar (60.00) + 20 boxes (50.00) = 190.00 issued; 1 kg more flour (20.00);
        # 0.5 kg sugar back at its issued 30.00 (15.00): materials 195.00. Labour 60.00 planned; overhead 45.00 actual
        # in place of the 40.00 planned: conversion 105.00. One defective: 19 good at 300.00 / 19 = 15.7895 (300.00).
        order = self.open()
        orders.advance(order, self.owner)
        actuals.extra_issue(order, self.owner, item=self.flour, quantity="1", reason="العجينة ناشفة")
        actuals.return_materials(order, self.owner, item=self.sugar, quantity="0.5", reason="فضل من الوزنة")
        actuals.add_cost_line(order, self.owner, kind="overhead", amount="45", description="غاز الفرن")
        run = self.walk(order, defect_at=1, defect="1")
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_quantity, run.output_operation.unit_cost), (D("195.00"), D("105.00"), D("19.000"), D("15.7895")))
        self.pinned(run, self.ORDER, self.ORDER_CONTROLS)

    def test_an_order_whose_unit_cost_leaves_three_piastres(self):
        # 1,000 kg of wheat (12,000.00) + labour 1,000.00 over 780 kg = 16.6667 each, worth 13,000.03: work in progress
        # gives back its 12,000.00 and absorbed production cost takes 1,000.03 (F12's residue rule).
        run = self.walk(self.open(self.milling, quantity="780", labor="1000", overhead="0"))
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_operation.unit_cost), (D("12000.00"), D("1000.00"), D("16.6667")))
        self.pinned(run, self.RESIDUE, self.RESIDUE_CONTROLS)

    # ---- an order finished into the quality hold place (F5) ----

    def test_an_order_finished_into_the_quality_hold_place(self):
        quality.save_recipe_quality(self.recipe, self.owner, needs_release=True, rows=[])
        run = self.walk(self.open())
        self.assertTrue(quality.is_hold_place(run.output_operation.destination_location))
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_operation.unit_cost), (D("190.00"), D("100.00"), D("14.5000")))
        self.pinned(run, self.HOLD, self.HOLD_CONTROLS)
        stock_line = JournalLine.objects.get(entry__source_type="stock_operation", entry__source_id=run.output_operation_id, debit=D("290.00"))
        self.assertEqual(stock_line.location, quality.hold_place(self.plant.entity_id, create=False))   # the product's value sits in the hold place

    # ---- an order with an outside processing fee (F4) ----

    def test_an_order_with_an_outside_processing_fee(self):
        # The second stage goes out at 2.00 a cake: 20 back good, a bill of 40.00 (no VAT here) posted to general
        # expenses; the finish absorbs labour 60.00 + overhead 40.00 + the fee 40.00 = 140.00: 330.00 / 20 = 16.5000.
        supplier = make_supplier("SUP-BAKE", name="فرن الأمانة")
        order = self.open()
        orders.advance(order, self.owner)
        job = outside.send(order, self.owner, supplier=supplier, unit_price="2.00")
        outside.receive(job, self.owner, good="20", defect="0")
        job.refresh_from_db()
        post_purchase_invoice(outside.make_bill(job, self.owner).pk, self.owner)
        run = self.walk(order)
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_operation.unit_cost), (D("190.00"), D("140.00"), D("16.5000")))
        self.pinned(run, self.OUTSIDE, self.OUTSIDE_CONTROLS)


class BakeryShapes(BakeryChart, FactoryShapes):
    """The same shapes on a restaurant bakery's chart with the manufacturing module on: stock is one account (1105), and
    work in progress and absorbed production cost are both the cost of sales (5101), in two lines on the product's entry."""

    PLAIN = {
        "MO-001-C01": [(0, "1105", "0.00", "120.00"), (0, "5101", "120.00", "0.00")],
        "MO-001-C02": [(0, "1105", "0.00", "90.00"), (0, "5101", "90.00", "0.00")],
        "MO-001-C03": [(0, "1105", "0.00", "75.00"), (0, "5101", "75.00", "0.00")],
        "MO-001-OUT": [(0, "1105", "285.00", "0.00"), (0, "5101", "0.00", "285.00")],
    }
    PLAIN_CONTROLS = {"inventory": "16850.00", "stock_gain": "-16850.00"}
    PLAIN_CANCELLED = {
        "MO-001-C01": [(0, "1105", "0.00", "120.00"), (0, "5101", "120.00", "0.00"), (3, "1105", "120.00", "0.00"), (3, "5101", "0.00", "120.00")],
        "MO-001-C02": [(0, "1105", "0.00", "90.00"), (0, "5101", "90.00", "0.00"), (3, "1105", "90.00", "0.00"), (3, "5101", "0.00", "90.00")],
        "MO-001-C03": [(0, "1105", "0.00", "75.00"), (0, "5101", "75.00", "0.00"), (3, "1105", "75.00", "0.00"), (3, "5101", "0.00", "75.00")],
        "MO-001-OUT": [(0, "1105", "285.00", "0.00"), (0, "5101", "0.00", "285.00"), (3, "1105", "0.00", "285.00"), (3, "5101", "285.00", "0.00")],
    }
    PLAIN_CANCELLED_CONTROLS = {"inventory": "16850.00", "stock_gain": "-16850.00"}
    ORDER = {
        "PO-001-I01": [(0, "1105", "0.00", "80.00"), (0, "5101", "80.00", "0.00")],
        "PO-001-I02": [(0, "1105", "0.00", "60.00"), (0, "5101", "60.00", "0.00")],
        "PO-001-I03": [(0, "1105", "0.00", "50.00"), (0, "5101", "50.00", "0.00")],
        "PO-001-X01": [(0, "1105", "0.00", "20.00"), (0, "5101", "20.00", "0.00")],
        "PO-001-R01": [(0, "1105", "15.00", "0.00"), (0, "5101", "0.00", "15.00")],
        "MO-001-OUT": [(0, "1105", "300.00", "0.00"), (0, "5101", "0.00", "105.00"), (0, "5101", "0.00", "195.00")],
    }
    ORDER_CONTROLS = {"inventory": "16955.00", "cogs": "-105.00", "stock_gain": "-16850.00"}
    RESIDUE = {
        "PO-001-I01": [(0, "1105", "0.00", "12000.00"), (0, "5101", "12000.00", "0.00")],
        "MO-001-OUT": [(0, "1105", "13000.03", "0.00"), (0, "5101", "0.00", "1000.03"), (0, "5101", "0.00", "12000.00")],
    }
    RESIDUE_CONTROLS = {"inventory": "17850.03", "cogs": "-1000.03", "stock_gain": "-16850.00"}
    HOLD = {
        "PO-001-I01": [(0, "1105", "0.00", "80.00"), (0, "5101", "80.00", "0.00")],
        "PO-001-I02": [(0, "1105", "0.00", "60.00"), (0, "5101", "60.00", "0.00")],
        "PO-001-I03": [(0, "1105", "0.00", "50.00"), (0, "5101", "50.00", "0.00")],
        "MO-001-OUT": [(0, "1105", "290.00", "0.00"), (0, "5101", "0.00", "100.00"), (0, "5101", "0.00", "190.00")],
    }
    HOLD_CONTROLS = {"inventory": "16950.00", "cogs": "-100.00", "stock_gain": "-16850.00"}
    OUTSIDE = {
        "PO-001-I01": [(0, "1105", "0.00", "80.00"), (0, "5101", "80.00", "0.00")],
        "PO-001-I02": [(0, "1105", "0.00", "60.00"), (0, "5101", "60.00", "0.00")],
        "PO-001-I03": [(0, "1105", "0.00", "50.00"), (0, "5101", "50.00", "0.00")],
        "MO-001-OUT": [(0, "1105", "330.00", "0.00"), (0, "5101", "0.00", "140.00"), (0, "5101", "0.00", "190.00")],
    }
    OUTSIDE_CONTROLS = {"inventory": "16990.00", "cogs": "-140.00", "general_expense": "40.00", "stock_gain": "-16850.00"}


class KitchenShape(KitchenSetup):
    """A restaurant kitchen's preparation: the plain-run path of ``services.produce`` on the restaurant's own chart."""

    KITCHEN = {
        "MO-001-C01": [(0, "1105", "0.00", "30.00"), (0, "5101", "30.00", "0.00")],
        "MO-001-C02": [(0, "1105", "0.00", "25.00"), (0, "5101", "25.00", "0.00")],
        "MO-001-OUT": [(0, "1105", "55.00", "0.00"), (0, "5101", "0.00", "55.00")],
    }
    KITCHEN_CONTROLS = {"inventory": "1190.00", "stock_gain": "-1190.00"}                 # rice 600.00 + lentils 500.00 + cola 90.00

    def test_a_kitchen_preparation(self):
        # Five plates of koshary: 1 kg rice at 30.00 + 0.5 kg lentils at 50.00 = 55.00, 11.0000 a plate.
        dish = kitchen.make_in_kitchen(self.koshary, self.owner)
        run = kitchen.prepare(dish, "5", self.owner)
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_operation.unit_cost), (D("55.00"), D("0.00"), D("11.0000")))
        self.assertEqual(journal(run), self.KITCHEN)
        self.assertEqual(controls(), self.KITCHEN_CONTROLS)
        self.assertTrue(gl.trial_balance()["balanced"])
