"""HG-036: work in progress, labour and overhead in the product's cost, scrap.

Bakery from ``MfgSetup``: one batch = 2 kg flour (20) + 1 kg sugar (30) +
10 boxes (2.50) = 95 for 10 cakes. An order for 20 cakes is 2 batches = 190 of
materials, with 60 labour and 40 overhead = 290 in all.
"""

from decimal import Decimal as D

from django.core.exceptions import ValidationError

from inventory.models import StockOperation
from inventory.services import get_item_authoritative_average_cost, get_item_location_stock_quantity
from ledger import reports as gl
from ledger.projector import Projector

from . import orders, services
from .models import OrderStatus, RunStatus
from .tests import MfgSetup


class WorkInProgressTests(MfgSetup):
    def open(self, **extra):
        data = {"recipe": self.recipe, "location": self.plant, "quantity": "20", "labor_cost": "60", "overhead_cost": "40", **extra}
        return orders.create_order(data, self.owner)

    def walk(self, order, defect_at=None):
        for index, _ in enumerate(order.stages):
            if index == defect_at:
                orders.advance(order, self.owner, good="19", defect="1")
            else:
                orders.advance(order, self.owner)

    def stock(self):
        return tuple(get_item_location_stock_quantity(item, self.plant) for item in (self.flour, self.sugar, self.box, self.cake))

    def test_materials_leave_stock_at_the_start_and_wait_in_wip(self):
        order = self.open()
        orders.advance(order, self.owner)
        self.assertEqual(self.stock(), (D("46"), D("18"), D("80"), D("0")))
        self.assertEqual(order.issues.count(), 3)
        self.assertEqual(orders.issued_value(order), D("190.00"))
        Projector().rebuild()
        self.assertEqual(gl.control_balance("wip"), D("190.00"))
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def test_labour_and_overhead_go_into_the_products_stock_cost(self):
        order = self.open()
        self.walk(order)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        run = order.run
        self.assertEqual((run.total_cost, run.conversion_cost, run.scrap_quantity), (D("190.00"), D("100.00"), D("0.000")))
        self.assertEqual(run.output_operation.unit_cost, D("14.5000"))          # 290 / 20
        self.assertEqual(get_item_authoritative_average_cost(self.cake), D("14.5000"))
        self.assertEqual(self.stock(), (D("46"), D("18"), D("80"), D("20")))
        Projector().rebuild()
        self.assertEqual(gl.control_balance("wip"), D("0.00"))                  # cleared by the finish
        self.assertEqual(gl.control_balance("finished_goods"), D("290.00"))
        self.assertEqual(gl.control_balance("production_cost"), D("-100.00"))   # labour + overhead absorbed
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def test_scrap_never_enters_stock_and_good_units_carry_the_cost(self):
        order = self.open()
        self.walk(order, defect_at=1)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.run.output_quantity, order.run.scrap_quantity), (D("19.000"), D("1.000")))
        self.assertEqual(get_item_location_stock_quantity(self.cake, self.plant), D("19"))
        self.assertEqual(get_item_authoritative_average_cost(self.cake), D("15.2632"))   # 290 / 19
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def test_a_lot_with_no_good_units_cannot_finish(self):
        order = self.open(quantity="10")
        orders.advance(order, self.owner, good="0", defect="10")
        for _ in order.stages[1:]:
            orders.advance(order, self.owner, good="0")
        with self.assertRaises(ValidationError):
            orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.IN_PROGRESS)

    def test_cancelling_an_order_on_the_floor_returns_every_material(self):
        order = self.open()
        orders.advance(order, self.owner)
        orders.cancel(order, self.owner, "العميل لغى")
        self.assertEqual(self.stock(), (D("50"), D("20"), D("100"), D("0")))
        self.assertTrue(all(issue.operation.status != "posted" for issue in order.issues.select_related("operation")))
        Projector().rebuild()
        self.assertEqual(gl.control_balance("wip"), D("0.00"))
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def test_cancelling_the_run_reverses_every_leg(self):
        order = self.open()
        self.walk(order)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        services.cancel_run(order.run, self.owner, reason="غلطة")
        order.run.refresh_from_db()
        self.assertEqual(order.run.status, RunStatus.CANCELLED)
        self.assertEqual(self.stock(), (D("50"), D("20"), D("100"), D("0")))
        Projector().rebuild()
        for control in ("wip", "finished_goods", "production_cost"):
            with self.subTest(control=control):
                self.assertEqual(gl.control_balance(control), D("0.00"))
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def test_an_order_started_before_hg036_consumes_at_the_finish(self):
        order = self.open()
        # As the floor left it before this change: started, every stage done, nothing issued.
        order.status, order.stage_index = OrderStatus.IN_PROGRESS, len(order.stages)
        order.save(update_fields=["status", "stage_index"])
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(self.stock(), (D("46"), D("18"), D("80"), D("20")))
        self.assertEqual(order.run.consumptions.count(), 3)
        self.assertEqual(StockOperation.objects.filter(reference_number__startswith=order.run.number).count(), 4)

    def test_a_plain_run_is_unchanged(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        self.assertEqual((run.output_quantity, run.total_cost, run.conversion_cost), (D("10.000"), D("95.00"), D("0.00")))
        self.assertEqual(run.output_operation.unit_cost, D("9.5000"))
