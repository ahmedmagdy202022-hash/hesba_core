"""Manufacturing F11b (HG-102): the stock operations screen does not reverse an operation that belongs to a
production run or to a production order.

HG-100 made ``cancel_run`` refuse a run whose goods have left its place. It left one gap, pinned there as known: the
screen «التحويلات والتسويات» (``inventory.views.operation_cancel``) reverses any adjustment through the engine alone,
so the run's own output could still be reversed from there, one operation at a time:

* the run's output, after a sale and a refill: 20 cakes out at 14.50 leave 5 cakes worth 102.50, 20.50 each, for
  cakes that cost 14.50 and 16.00, and the run stays «posted» with nothing of it in stock;
* one of the run's materials: 2 kg of flour come back (40.00) while the 10 cakes stay in stock at 9.50, a cost that
  still holds that flour, and the run stays «posted»;
* a material issued to an order on the floor: the flour comes back, the order stays «in progress» and can no longer
  be finished.

The bakery and its figures are those of ``tests_f11``. The clock stands still on a Wednesday at noon: nothing here
depends on the hour or on the day of the week.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import expectedFailure, mock

from django.urls import reverse
from django.utils import timezone

from inventory.models import StockMovement, StockOperation
from inventory.services import cancel_stock_operation

from . import orders, services
from .models import OrderStatus, RunStatus
from .tests_f11 import F11Setup

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)   # a Wednesday, 12:00 in Cairo
DAY = date(2026, 11, 11)


class F11bSetup(F11Setup):
    def setUp(self):
        for clock in (mock.patch("django.utils.timezone.now", return_value=NOW), mock.patch("manufacturing.tests_f11.TODAY", DAY)):
            clock.start()
            self.addCleanup(clock.stop)
        self.assertEqual(timezone.localdate(), DAY)
        super().setUp()

    def two_runs(self):
        """Run 1 makes 20 cakes at 14.50 (290.00). 15 are sold. Run 2 makes 20 at 16.00 (320.00): 25 cakes worth
        392.50, 15.70 each."""

        first = self.make()
        self.sell("15")
        second = self.make(labor="90", overhead="40")
        self.assertEqual(self.position(), (D("25.000"), D("392.50"), D("15.7000")))
        return first, second

    def started_order(self):
        """An order for 20 cakes whose first stage is done: its materials are on the floor (190.00)."""

        order = orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "20", "labor_cost": "60", "overhead_cost": "40"}, self.owner)
        orders.advance(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, self.on_hand(self.flour, self.sugar, self.box)), (OrderStatus.IN_PROGRESS, (D("196.000"), D("18.000"), D("80.000"))))
        return order

    def from_the_stock_screen(self, operation, lang="ar", user=None, reason="من شاشة عمليات المخزون"):
        """Reverse ``operation`` as the stock operations screen does: its own POST, followed to the list."""

        self.client.force_login(user or self.owner)
        return self.client.post(reverse("inventory:operation_cancel", args=[operation.pk]),
                                {"lang": lang, "reversal_date": DAY.isoformat(), "reason": reason}, follow=True)

    def untouched(self, *operations):
        """No operation was reversed and no stock row was written since ``self.rows`` was counted."""

        self.assertEqual(StockMovement.objects.count(), self.rows)
        self.assertEqual(list(StockOperation.objects.filter(pk__in=[operation.pk for operation in operations]).order_by("pk")
                              .values_list("status", flat=True)), ["posted"] * len(operations))


class TheGapTests(F11bSetup):
    """Written before the fix and marked as expected failures until it: each of these went through."""

    @expectedFailure
    def test_the_output_of_a_run_whose_cakes_were_sold_is_not_reversed_from_the_stock_screen(self):
        first, _ = self.two_runs()
        self.rows = StockMovement.objects.count()
        self.from_the_stock_screen(first.output_operation)
        # It went through: 20 out at 14.50 left 5 cakes worth 102.50, 20.50 each, and the run stayed «posted».
        self.assertEqual(self.position(), (D("25.000"), D("392.50"), D("15.7000")))
        first.refresh_from_db()
        self.assertEqual(first.status, RunStatus.POSTED)
        self.untouched(first.output_operation)

    @expectedFailure
    def test_one_material_of_a_run_is_not_reversed_from_the_stock_screen(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        flour = run.consumptions.get(operation__item=self.flour).operation
        self.rows = StockMovement.objects.count()
        self.from_the_stock_screen(flour)
        # It went through: the 2 kg came back (200 kg) while the 10 cakes stayed at 9.50, a cost that holds that flour.
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("198.000"), D("19.000"), D("90.000")))
        self.assertEqual(self.position(), (D("10.000"), D("95.00"), D("9.5000")))
        self.untouched(flour)

    @expectedFailure
    def test_a_material_issued_to_an_order_on_the_floor_is_not_reversed_from_the_stock_screen(self):
        order = self.started_order()
        flour = order.issues.get(operation__item=self.flour).operation
        self.rows = StockMovement.objects.count()
        self.from_the_stock_screen(flour)
        # It went through: the 4 kg came back (200 kg) and the order stayed «in progress», never to be finished.
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("196.000"), D("18.000"), D("80.000")))
        self.untouched(flour)
        orders.finish(self.walk(order), self.owner)

    def walk(self, order):
        for _ in order.stages[order.stage_index:]:
            orders.advance(order, self.owner)
            order.refresh_from_db()
        return order

    def test_the_engine_alone_still_reverses_a_runs_output(self):
        # The engine is not changed (HG-102): called directly, as only a run's and an order's own cancellation do, it
        # reverses one operation and knows nothing of the run. These are the figures the screen used to produce.
        first, _ = self.two_runs()
        cancel_stock_operation(first.output_operation_id, DAY, "المحرك لوحده", self.owner)
        first.refresh_from_db()
        self.assertEqual((first.status, self.position()), (RunStatus.POSTED, (D("5.000"), D("102.50"), D("20.5000"))))
