"""Manufacturing F12 (HG-132): what an order really used and really spent.

The reference example (``MANUFACTURING_PLAN.md``, section 3): recipe R-REF, 10 kg flour at 20.00 + 2 kg sugar at
30.00 makes 100 units; planned labour 50.00, no overhead. The start issues 260.00 to work in progress; the standard
unit cost is (260.00 + 50.00) / 100 = 3.1000.

The plant holds F11's stock: flour 200 kg at 20.00, sugar 20 kg at 30.00, boxes 100 at 2.50 (raw materials 4,850.00).

Every money scenario runs on the manufacturing chart (raw materials, work in progress, finished goods, absorbed
production cost) and again on a restaurant bakery's chart with the module on, where work in progress and absorbed
cost both fall back to the cost of sales (``tests_f11.BakeryChart``).
"""

from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_item, make_location, stock_in
from inventory.models import StockOperation, StockOperationStatus
from inventory.services import get_item_authoritative_average_cost, get_item_stock_value
from ledger import reports as gl
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode

from . import actuals, orders, services, variance
from .models import IssueKind, LotUse, OrderCostLine, OrderStandardLine, OrderStatus, RunStatus
from .tests import person
from .tests_f11 import BakeryChart, F11Setup

TODAY = timezone.localdate()


class ActualsSetup(F11Setup):
    def setUp(self):
        super().setUp()
        self.ref = make_item(item_code="FG-REF", item_name="منتج المرجع", is_stock_tracked=True, default_sale_price="5")
        self.ref_recipe = services.save_recipe({"product": self.ref, "output_quantity": "100"}, [(self.flour, "10"), (self.sugar, "2")], self.owner)

    def open(self, labor="50", overhead="0", quantity="100", recipe=None):
        return orders.create_order({"recipe": recipe or self.ref_recipe, "location": self.plant, "quantity": quantity,
                                    "labor_cost": labor, "overhead_cost": overhead}, self.owner)

    def start(self, **extra):
        order = self.open(**extra)
        orders.advance(order, self.owner)
        order.refresh_from_db()
        return order

    def walk(self, order, defects=None):
        """Every stage left (``defects`` at the last one) and the finish."""

        order.refresh_from_db()
        while order.stage_index < len(order.stages):
            last = order.stage_index == len(order.stages) - 1
            orders.advance(order, self.owner, defect=defects if last else None)
            order.refresh_from_db()
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        return order

    def two_workers(self, order):
        for name in ("أحمد", "محمود"):
            actuals.add_cost_line(order, self.owner, kind="labour", description=name, hours="4", rate="7.50")

    def output_journal(self, operation):
        """{control key: debit − credit} of the entry the ledger made for one stock operation."""

        rows = (JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id=operation.pk)
                .values("account__control").annotate(d=Sum("debit"), c=Sum("credit")))
        return {row["account__control"]: row["d"] - row["c"] for row in rows if row["d"] - row["c"]}

    def expected_output_journal(self, finished, wip, absorbed):
        return {"finished_goods": D(finished), "wip": -D(wip), "production_cost": -D(absorbed)}


class RestaurantChart(BakeryChart):
    def expected_output_journal(self, finished, wip, absorbed):
        return {"inventory": D(finished), "cogs": -D(finished)}   # work in progress and absorbed cost both fall back to 5101


# ---------------------------------------------------------------------------------------------------------------
# The reference order
# ---------------------------------------------------------------------------------------------------------------


class ReferenceOrderTests(ActualsSetup):
    def test_the_reference_order_from_start_to_variance(self):
        # 1. Start: flour 10 x 20.00 + sugar 2 x 30.00 = 260.00 to work in progress.
        order = self.start()
        self.assertEqual(orders.issued_value(order), D("260.00"))
        self.assertEqual([(row.component_id, row.quantity, row.unit_cost) for row in order.standard_lines.all()],
                         [(self.flour.pk, D("10.000"), D("20.0000")), (self.sugar.pk, D("2.000"), D("30.0000"))])
        self.books(finished="0.00", raw="4590.00", wip="260.00")

        # 2. Extra issue: 1 kg flour at its average 20.00 -> 280.00.
        extra = actuals.extra_issue(order, self.owner, item=self.flour, quantity="1", reason="العجينة احتاجت دقيق زيادة")
        self.assertEqual((extra.adjustment_direction, extra.quantity, extra.unit_cost), ("out", D("1.000"), D("20.0000")))
        self.assertEqual(order.issues.get(operation=extra).kind, IssueKind.EXTRA)
        self.assertEqual(orders.issued_value(order), D("280.00"))
        self.books(finished="0.00", raw="4570.00", wip="280.00")

        # 3. Sugar's average moves to 32.00 (18 kg more at 34.00); the return still comes back at the 30.00 issued.
        stock_in(self.sugar, self.plant, 18, "34.00", movement_date=TODAY)
        self.assertEqual(get_item_authoritative_average_cost(self.sugar), D("32.0000"))
        self.assertEqual(get_item_stock_value(self.sugar), D("1152.00"))
        back = actuals.return_materials(order, self.owner, item=self.sugar, quantity="0.5", reason="فضل نص كيلو")
        self.assertEqual((back.adjustment_direction, back.quantity, back.unit_cost), ("in", D("0.500"), D("30.0000")))
        self.assertEqual(get_item_stock_value(self.sugar), D("1167.00"))          # +15.00
        self.assertEqual(orders.issued_value(order), D("265.00"))
        self.books(finished="0.00", raw="5197.00", wip="265.00")                 # 4,850 - 260 - 20 + 612 + 15

        # 4. Actual labour: two workers x 4 h x 7.50 = 60.00 (planned 50.00).
        self.two_workers(order)
        self.assertEqual(actuals.conversion(order)["labour"], D("60.00"))
        self.assertEqual(orders.cost_card(order)["labor"], D("60.00"))

        # 5. Finish with 2 defective: 98 good units carry 265.00 + 60.00 = 325.00.
        order = self.walk(order, defects="2")
        run = order.run
        self.assertEqual((run.output_quantity, run.scrap_quantity, run.total_cost, run.conversion_cost), (D("98.000"), D("2.000"), D("265.00"), D("60.00")))
        self.assertEqual(run.output_operation.unit_cost, D("3.3163"))              # 325 / 98
        self.assertEqual(self.position(self.ref), (D("98.000"), D("325.00"), D("3.3163")))   # 98 x 3.3163 = 324.9974, rounded
        self.assertEqual(run.consumptions.count(), 4)                              # flour, sugar, the extra and the return
        Projector().rebuild()
        self.assertEqual(self.output_journal(run.output_operation), self.expected_output_journal("325.00", "265.00", "60.00"))
        self.books(finished="325.00", raw="5197.00", wip="0.00", absorbed="60.00")

        # 6. Variance against the standard written at the start.
        result = variance.order_variance(order)
        lines = {line["item"].pk: line for line in result["materials"]}
        self.assertEqual((lines[self.flour.pk]["diff_qty"], lines[self.flour.pk]["diff_value"]), (D("1.000"), D("20.00")))
        self.assertEqual((lines[self.sugar.pk]["diff_qty"], lines[self.sugar.pk]["diff_value"]), (D("-0.500"), D("-15.00")))
        self.assertEqual((result["std_material"], result["material"], result["diff_material"]), (D("260.00"), D("265.00"), D("5.00")))
        self.assertEqual((result["planned_labour"], result["labour"], result["diff_labour"]), (D("50.00"), D("60.00"), D("10.00")))
        self.assertEqual((result["planned_units"], result["good"], result["diff_units"]), (D("100.000"), D("98.000"), D("-2.000")))
        self.assertEqual((result["unit"], result["standard_unit"]), (D("3.3163"), D("3.1000")))

    def test_with_no_actual_lines_the_planned_figure_is_absorbed(self):
        order = self.walk(self.start())
        self.assertEqual((order.run.total_cost, order.run.conversion_cost), (D("260.00"), D("50.00")))
        self.assertEqual(order.run.output_operation.unit_cost, D("3.1000"))
        Projector().rebuild()
        self.assertEqual(self.output_journal(order.run.output_operation), self.expected_output_journal("310.00", "260.00", "50.00"))
        self.books(finished="310.00", raw="4590.00", absorbed="50.00")
        result = variance.order_variance(order)
        self.assertEqual((result["diff_material"], result["diff_labour"], result["diff_units"], result["unit"], result["standard_unit"]),
                         (D("0.00"), D("0.00"), D("0.000"), D("3.1000"), D("3.1000")))

    def test_editing_the_recipe_after_the_finish_leaves_the_variance_unchanged(self):
        order = self.start()
        actuals.extra_issue(order, self.owner, item=self.flour, quantity="1", reason="زيادة")
        order = self.walk(order)
        before = variance.order_variance(order)
        services.save_recipe({"product": self.ref, "output_quantity": "50"}, [(self.flour, "30"), (self.box, "5")], self.owner, recipe=self.ref_recipe)
        after = variance.order_variance(order)
        key = lambda result: [(line["item"].pk, line["std_qty"], line["std_value"], line["qty"], line["value"]) for line in result["materials"]]
        self.assertEqual(key(after), key(before))
        self.assertEqual((after["standard_unit"], after["std_material"]), (before["standard_unit"], before["std_material"]))
        self.assertEqual(after["standard_unit"], D("3.1000"))


class ReferenceOrderOnARestaurantChartTests(RestaurantChart, ReferenceOrderTests):
    pass


# ---------------------------------------------------------------------------------------------------------------
# Conversion per kind
# ---------------------------------------------------------------------------------------------------------------


class ConversionTests(ActualsSetup):
    def test_each_kind_keeps_its_planned_figure_until_it_has_a_line(self):
        order = self.open(labor="50", overhead="20")
        self.assertEqual(actuals.conversion(order)["total"], D("70.00"))
        actuals.add_cost_line(order, self.owner, kind="overhead", amount="30", description="كهرباء الخط")
        self.assertEqual((actuals.conversion(order)["labour"], actuals.conversion(order)["overhead"]), (D("50.00"), D("30.00")))
        # Outside processing (F4) counts its lines only.
        OrderCostLine.objects.create(order=order, kind="outside", amount=D("15.00"), created_by=self.owner)
        self.assertEqual(actuals.conversion(order)["total"], D("95.00"))

    def test_a_cancelled_line_brings_the_planned_figure_back(self):
        order = self.start()
        line = actuals.add_cost_line(order, self.owner, kind="labour", hours="8", rate="7.50")
        self.assertEqual(line.amount, D("60.00"))
        actuals.cancel_cost_line(line, self.owner)
        self.assertEqual(actuals.conversion(order)["labour"], D("50.00"))
        with self.assertRaisesMessage(ValidationError, "اتلغى قبل كده"):
            actuals.cancel_cost_line(line, self.owner)
        order = self.walk(order)
        self.assertEqual(order.run.conversion_cost, D("50.00"))

    def test_line_rules(self):
        order = self.open()
        for data in ({"kind": "outside", "amount": "5"}, {"kind": "nothing", "amount": "5"}):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, "نوع التكلفة"):
                actuals.add_cost_line(order, self.owner, **data)
        for data in ({"kind": "labour"}, {"kind": "labour", "hours": "4"}, {"kind": "labour", "amount": "-3"}, {"kind": "labour", "amount": "x"},
                     {"kind": "labour", "hours": "0", "rate": "7.50"}):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, "الساعات وسعر الساعة"):
                actuals.add_cost_line(order, self.owner, **data)
        order = self.walk(self.start())
        with self.assertRaisesMessage(ValidationError, "لحد ما الأمر يخلص"):
            actuals.add_cost_line(order, self.owner, kind="labour", amount="5")

    def test_cost_lines_need_the_cost_permission(self):
        order = self.open()
        keeper = person(RoleCode.STOCK_KEEPER, "f12_keeper")
        with self.assertRaises(PermissionDenied):
            actuals.add_cost_line(order, keeper, kind="labour", amount="5")
        line = actuals.add_cost_line(order, self.owner, kind="labour", amount="5")
        with self.assertRaises(PermissionDenied):
            actuals.cancel_cost_line(line, keeper)


# ---------------------------------------------------------------------------------------------------------------
# Extra issues and returns: refusals and the cost a return comes back at
# ---------------------------------------------------------------------------------------------------------------


class ExtraAndReturnTests(ActualsSetup):
    def test_a_return_comes_back_at_the_cost_weighted_over_issue_and_extra(self):
        order = self.start()                                                     # sugar 2 at 30.00
        stock_in(self.sugar, self.plant, 18, "34.00", movement_date=TODAY)       # average 32.00
        actuals.extra_issue(order, self.owner, item=self.sugar, quantity="1", reason="زيادة")   # 1 at 32.00
        self.assertEqual(actuals.issued_cost(order, self.sugar), D("30.6667"))   # (60 + 32) / 3
        back = actuals.return_materials(order, self.owner, item=self.sugar, quantity="1")
        self.assertEqual(back.unit_cost, D("30.6667"))
        self.assertEqual([(row["item"].pk, row["left"]) for row in actuals.returnable(order)], [(self.flour.pk, D("10.000")), (self.sugar.pk, D("2.000"))])

    def test_a_return_after_a_return_and_a_cheaper_extra_takes_only_what_is_left(self):
        """Codex on #439: a return prices from the value still on the order, never the average of every issue, so after a
        return and a cheaper extra the next return cannot take more than the order holds."""

        order = self.start()                                                     # sugar 2 at 30.00 = 60.00
        first = actuals.return_materials(order, self.owner, item=self.sugar, quantity="1.5")
        self.assertEqual(first.unit_cost, D("30.0000"))                          # 45.00 back, 0.5 and 15.00 left
        stock_in(self.sugar, self.plant, 100, "1.00", movement_date=TODAY)       # the average drops well below 30.00
        extra = actuals.extra_issue(order, self.owner, item=self.sugar, quantity="2", reason="زيادة")
        self.assertLess(extra.unit_cost, D("30"))
        held = D("15.00") + extra.quantity * extra.unit_cost
        self.assertEqual(actuals.net_left(order, self.sugar), (D("2.5"), held))
        self.assertEqual([row["unit_cost"] for row in actuals.returnable(order) if row["item"].pk == self.sugar.pk], [actuals.return_cost(order, self.sugar)])
        last = actuals.return_materials(order, self.owner, item=self.sugar, quantity="2.5")
        self.assertEqual(last.unit_cost, (held / D("2.5")).quantize(D("0.0001")))
        self.assertLess(last.quantity * last.unit_cost, D("60"))                 # the old average, (60 + extra) / 4, took far more
        left, value = actuals.net_left(order, self.sugar)
        self.assertEqual(left, 0)
        self.assertLess(abs(value), D("0.01"))                                   # nothing left on the order but a rounding residue

    def test_a_return_above_what_was_issued_is_refused(self):
        order = self.start()
        with self.assertRaisesMessage(ValidationError, "المتاح للرجوع من «سكر (كيلو)» 2"):
            actuals.return_materials(order, self.owner, item=self.sugar, quantity="2.5")
        actuals.return_materials(order, self.owner, item=self.sugar, quantity="1.5")
        with self.assertRaisesMessage(ValidationError, "0.5"):
            actuals.return_materials(order, self.owner, item=self.sugar, quantity="0.6")
        with self.assertRaisesMessage(ValidationError, "ماتصرفش"):
            actuals.return_materials(order, self.owner, item=self.box, quantity="1")
        for quantity in ("0", "-1", "x"):
            with self.subTest(quantity=quantity), self.assertRaisesMessage(ValidationError, "أكبر من صفر"):
                actuals.return_materials(order, self.owner, item=self.sugar, quantity=quantity)
        actuals.extra_issue(order, self.owner, item=self.sugar, quantity="1", reason="زيادة")
        actuals.return_materials(order, self.owner, item=self.sugar, quantity="1.5")   # 2 + 1 - 1.5 = 1.5 left

    def test_a_return_on_a_finished_or_cancelled_order_is_refused(self):
        done = self.walk(self.start())
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            actuals.return_materials(done, self.owner, item=self.sugar, quantity="0.5")
        cancelled = self.start()
        orders.cancel(cancelled, self.owner, "اتلغى")
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            actuals.return_materials(cancelled, self.owner, item=self.sugar, quantity="0.5")
        with self.assertRaisesMessage(ValidationError, "شغال في المصنع"):
            actuals.extra_issue(cancelled, self.owner, item=self.flour, quantity="1", reason="x")

    def test_a_return_needs_the_cost_permission(self):
        order = self.start()
        keeper = person(RoleCode.STOCK_KEEPER, "f12_keeper")
        with self.assertRaises(PermissionDenied):
            actuals.return_materials(order, keeper, item=self.sugar, quantity="0.5")
        actuals.extra_issue(order, keeper, item=self.flour, quantity="1", reason="زيادة")   # an extra issue sets no cost

    def test_extra_issue_rules(self):
        planned = self.open()
        with self.assertRaisesMessage(ValidationError, "شغال في المصنع"):
            actuals.extra_issue(planned, self.owner, item=self.flour, quantity="1", reason="x")
        order = self.start()
        for kwargs, text in (({"item": None, "quantity": "1", "reason": "x"}, "اختار الصنف"),
                             ({"item": self.ref, "quantity": "1", "reason": "x"}, "المنتج نفسه"),
                             ({"item": make_item(item_code="SRV", item_name="خدمة", is_stock_tracked=False), "quantity": "1", "reason": "x"}, "متتبع"),
                             ({"item": self.flour, "quantity": "0", "reason": "x"}, "أكبر من صفر"),
                             ({"item": self.flour, "quantity": "1", "reason": " "}, "اكتب سبب"),
                             ({"item": self.flour, "quantity": "191", "reason": "x"}, "مش كفاية")):
            with self.subTest(text=text), self.assertRaisesMessage(ValidationError, text):
                actuals.extra_issue(order, self.owner, **kwargs)
        substitute = actuals.extra_issue(order, self.owner, item=self.box, quantity="4", reason="بديل")   # a substitute outside the recipe
        self.assertEqual(substitute.unit_cost, D("2.5000"))

    def test_an_order_started_before_hg036_takes_no_extra_and_no_return(self):
        order = self.open()
        order.status, order.stage_index = OrderStatus.IN_PROGRESS, 1   # as the floor left it before HG-036: nothing issued
        order.save(update_fields=["status", "stage_index"])
        with self.assertRaisesMessage(ValidationError, "قبل ما الخامات تتصرف"):
            actuals.extra_issue(order, self.owner, item=self.flour, quantity="1", reason="x")
        with self.assertRaisesMessage(ValidationError, "قبل ما الخامات تتصرف"):
            actuals.return_materials(order, self.owner, item=self.flour, quantity="1")

    def test_the_stock_screen_does_not_reverse_an_extra_or_a_return(self):
        from .guards import owners

        order = self.start()
        extra = actuals.extra_issue(order, self.owner, item=self.flour, quantity="1", reason="زيادة")
        back = actuals.return_materials(order, self.owner, item=self.sugar, quantity="0.5")
        found = owners([extra.pk, back.pk])
        self.assertEqual({found[extra.pk].kind, found[back.pk].kind}, {"order"})


# ---------------------------------------------------------------------------------------------------------------
# Cancelling: issues, extras and returns all reversed, stock back exactly
# ---------------------------------------------------------------------------------------------------------------


class CancelTests(ActualsSetup):
    def scenario(self):
        order = self.start()
        actuals.extra_issue(order, self.owner, item=self.flour, quantity="1", reason="زيادة")
        stock_in(self.sugar, self.plant, 18, "34.00", movement_date=TODAY)
        actuals.return_materials(order, self.owner, item=self.sugar, quantity="0.5")
        return order

    def stock(self):
        return {item.item_code: (self.on_hand(item)[0], get_item_stock_value(item)) for item in (self.flour, self.sugar, self.box, self.ref)}

    def test_cancelling_an_order_reverses_issues_extras_and_returns(self):
        order = self.scenario()
        orders.cancel(order, self.owner, "العميل لغى")
        self.assertEqual(self.stock(), {"RM-FLOUR": (D("200.000"), D("4000.00")), "RM-SUGAR": (D("38.000"), D("1212.00")),
                                        "PK-BOX": (D("100.000"), D("250.00")), "FG-REF": (D("0"), D("0.00"))})
        self.assertFalse(order.issues.filter(operation__status=StockOperationStatus.POSTED).exists())
        self.assertEqual(order.issues.count(), 4)
        self.books(finished="0.00", raw="5462.00")                               # 4,850 + 612

    def test_cancelling_the_run_reverses_every_leg(self):
        order = self.scenario()
        self.two_workers(order)
        order = self.walk(order, defects="2")
        services.cancel_run(order.run, self.owner, reason="غلطة")
        order.run.refresh_from_db()
        self.assertEqual(order.run.status, RunStatus.CANCELLED)
        self.assertEqual(self.stock(), {"RM-FLOUR": (D("200.000"), D("4000.00")), "RM-SUGAR": (D("38.000"), D("1212.00")),
                                        "PK-BOX": (D("100.000"), D("250.00")), "FG-REF": (D("0.000"), D("0.00"))})
        self.books(finished="0.00", raw="5462.00")


class CancelOnARestaurantChartTests(RestaurantChart, CancelTests):
    pass


# ---------------------------------------------------------------------------------------------------------------
# The rounding residue left in work in progress (trap 2, owner question 10)
# ---------------------------------------------------------------------------------------------------------------


class ResidueTests(ActualsSetup):
    # Proved first: on the manufacturing chart both failed until the projector booked the residue (HG-132): the order's
    # work in progress ended at -0.03 with 5102 at -1,000.00, the plain run's at +0.01.
    def setUp(self):
        super().setUp()
        stock_in(self.flour, self.plant, 400, "20.00", movement_date=TODAY)     # flour 600 kg, average 20.00; raw 12,850.00
        self.big = make_item(item_code="FG-BIG", item_name="منتج كبير", is_stock_tracked=True)
        self.big_recipe = services.save_recipe({"product": self.big, "output_quantity": "780"}, [(self.flour, "600")], self.owner)

    def test_an_orders_residue_goes_to_absorbed_cost(self):
        # 12,000.00 of materials + 1,000.00 of labour over 780 units = 16.6667 each, worth 13,000.03 in stock.
        order = self.walk(self.start(recipe=self.big_recipe, quantity="780", labor="1000"))
        run = order.run
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_operation.unit_cost), (D("12000.00"), D("1000.00"), D("16.6667")))
        self.assertEqual(self.position(self.big), (D("780.000"), D("13000.03"), D("16.6667")))
        Projector().rebuild()
        self.assertEqual(self.output_journal(run.output_operation), self.expected_output_journal("13000.03", "12000.00", "1000.03"))
        self.books(finished="13000.03", raw="850.00", wip="0.00", absorbed="1000.03")   # without the rule: wip -0.03, absorbed 1,000.00
        services.cancel_run(run, self.owner, reason="غلطة")
        self.books(finished="0.00", raw="12850.00")

    def test_a_plain_runs_residue_goes_to_absorbed_cost(self):
        # 12,000.00 over 780 units = 15.3846 each, worth 11,999.99: a piastre short of what work in progress received.
        run = services.produce(self.big_recipe, self.owner, batches="1", location=self.plant)
        self.assertEqual((run.total_cost, run.conversion_cost, run.output_operation.unit_cost), (D("12000.00"), D("0.00"), D("15.3846")))
        self.assertEqual(self.position(self.big), (D("780.000"), D("11999.99"), D("15.3846")))
        Projector().rebuild()
        self.assertEqual(self.output_journal(run.output_operation), self.expected_output_journal("11999.99", "12000.00", "-0.01"))
        self.books(finished="11999.99", raw="850.00", wip="0.00", absorbed="-0.01")      # without the rule: wip 0.01


class ResidueOnARestaurantChartTests(RestaurantChart, ResidueTests):
    """Both fallbacks land in 5101 here, so the rule changes no balance on this chart."""


# ---------------------------------------------------------------------------------------------------------------
# Over-yield (owner question 8)
# ---------------------------------------------------------------------------------------------------------------


class OverYieldTests(ActualsSetup):
    def to_last_stage(self):
        order = self.start()
        while order.stage_index < len(order.stages) - 1:
            orders.advance(order, self.owner)
            order.refresh_from_db()
        return order

    def test_up_to_ten_percent_more_with_a_reason(self):
        order = self.to_last_stage()
        with self.assertRaisesMessage(ValidationError, "اكتب السبب"):
            orders.advance(order, self.owner, good="105")
        with self.assertRaisesMessage(ValidationError, "110"):
            orders.advance(order, self.owner, good="110.001", note="العجينة فردت")
        orders.advance(order, self.owner, good="105", note="العجينة فردت")
        order.refresh_from_db()
        self.assertEqual(orders.good_units(order), D("105"))
        orders.finish(order, self.owner)
        order.refresh_from_db()
        run = order.run
        self.assertEqual((run.output_quantity, run.scrap_quantity, run.total_cost), (D("105.000"), D("0.000"), D("260.00")))
        self.assertEqual(run.output_operation.unit_cost, D("2.9524"))              # the same 310.00 over 105 units
        self.books(finished="310.00", raw="4590.00", absorbed="50.00")

    def test_only_the_final_stage_may_record_more_than_ordered(self):
        order = self.open()
        with self.assertRaisesMessage(ValidationError, "بين صفر وكمية الأمر"):
            orders.advance(order, self.owner, good="105", note="x")


# ---------------------------------------------------------------------------------------------------------------
# Lots: an extra issue records the lots it used, as the start does (F3)
# ---------------------------------------------------------------------------------------------------------------


class ExtraIssueLotTests(ActualsSetup):
    def test_an_extra_issue_records_its_lots_and_they_follow_the_run(self):
        from datetime import timedelta

        from batches.services import register_batch
        from settings_core.capabilities import set_capability_enabled

        set_capability_enabled(self.profile, "batches_expiry", True)
        register_batch(item=self.flour, location=self.plant, batch_no="LOT-F", expiry_date=TODAY + timedelta(days=30), quantity=D("200"),
                       received_on=TODAY, user=self.owner)
        order = self.start()
        actuals.extra_issue(order, self.owner, item=self.flour, quantity="1", reason="زيادة")
        uses = LotUse.objects.filter(order=order, item=self.flour)
        self.assertEqual(sorted(use.quantity for use in uses), [D("1.000"), D("10.000")])
        actuals.return_materials(order, self.owner, item=self.flour, quantity="0.5")   # a return writes no lot row
        self.assertEqual(uses.count(), 2)
        order = self.walk(order)
        self.assertEqual(LotUse.objects.filter(run=order.run, item=self.flour).count(), 2)


# ---------------------------------------------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------------------------------------------


class ScreenTests(ActualsSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def url(self, order):
        return reverse("manufacturing:order", args=[order.pk])

    def test_the_order_page_issues_returns_and_records_actual_costs(self):
        order = self.start()
        for lang in ("ar", "en"):
            page = self.client.get(self.url(order), {"lang": lang})
            for marker in ("data-extra-form", "data-return-form", "data-cost-form", "data-order-materials", "data-actual-summary"):
                self.assertContains(page, marker)
        self.assertContains(self.client.get(self.url(order), {"lang": "ar"}), "صرف إضافي")
        self.assertContains(self.client.get(self.url(order), {"lang": "en"}), "Return materials")
        self.client.post(self.url(order), {"action": "extra", "item": self.flour.pk, "quantity": "1", "why": "زيادة", "lang": "ar"})
        self.client.post(self.url(order), {"action": "return", "item": self.sugar.pk, "quantity": "0.5", "why": "فضل", "lang": "ar"})
        self.client.post(self.url(order), {"action": "add_cost", "kind": "labour", "hours": "4", "rate": "7.50", "lang": "ar"})
        self.assertEqual(sorted(order.issues.values_list("kind", flat=True)), ["extra", "issue", "issue", "return"])
        line = order.cost_lines.get()
        self.assertEqual(line.amount, D("30.00"))
        self.client.post(self.url(order), {"action": "cancel_cost", "line": line.pk, "lang": "ar"})
        line.refresh_from_db()
        self.assertIsNotNone(line.cancelled_at)
        refused = self.client.post(self.url(order), {"action": "return", "item": self.sugar.pk, "quantity": "9", "lang": "ar"})
        self.assertContains(refused, "data-order-error")

    def test_a_stock_keeper_sees_no_cost_and_cannot_return(self):
        order = self.start()
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "f12_keeper"))
        page = self.client.get(self.url(order))
        self.assertContains(page, "data-extra-form")
        for marker in ("data-return-form", "data-cost-form", "data-cost-lines"):
            self.assertNotContains(page, marker)
        self.assertEqual(self.client.post(self.url(order), {"action": "return", "item": self.sugar.pk, "quantity": "0.5"}).status_code, 403)
        self.assertEqual(self.client.post(self.url(order), {"action": "add_cost", "kind": "labour", "amount": "5"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("manufacturing:costs")).status_code, 403)

    def test_the_variance_report(self):
        order = self.start()
        actuals.extra_issue(order, self.owner, item=self.flour, quantity="1", reason="زيادة")
        self.two_workers(order)
        order = self.walk(order, defects="2")
        for lang in ("ar", "en"):
            page = self.client.get(reverse("manufacturing:costs"), {"lang": lang})
            self.assertContains(page, f'data-variance-order="{order.number}"')
            self.assertContains(page, "3.4694")                                   # (280.00 + 60.00) / 98
            self.assertContains(page, 'data-variance-item="RM-FLOUR"')
            self.assertContains(page, "data-costs-months")
        empty = self.client.get(reverse("manufacturing:costs"), {"from": "2020-01-01", "to": "2020-01-31"})
        self.assertContains(empty, "data-costs-empty")
        Projector().rebuild()
        month = variance.month_lines(TODAY.replace(day=1), TODAY)[-1]
        self.assertEqual(month["absorbed"], D("60.00"))

    def test_another_entitys_orders_stay_out_of_the_report(self):
        from entities.services import save_entity

        self.walk(self.start())
        branch = save_entity({"code": "BR12", "name_ar": "فرع العبور", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        make_location(location_code="BR-PLANT", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        self.assertContains(self.client.get(reverse("manufacturing:costs")), "data-costs-empty")
        self.client.post(reverse("entities:switch"), {"entity": "", "next": "/dashboard/"})
        self.assertNotContains(self.client.get(reverse("manufacturing:costs")), "data-costs-empty")

    def test_the_new_screens_use_no_banned_word(self):
        import pathlib

        from django.conf import settings

        banned = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")
        for name in ("templates/manufacturing/_actuals.html", "templates/manufacturing/costs.html", "manufacturing/actuals.py",
                     "manufacturing/variance.py", "manufacturing/costs_views.py", "manufacturing/order_views.py"):
            text = (pathlib.Path(settings.BASE_DIR) / name).read_text(encoding="utf-8")
            for word in banned:
                with self.subTest(name=name, word=word):
                    self.assertNotIn(word, text)


class ScreensOnARestaurantChartTests(RestaurantChart, ScreenTests):
    test_another_entitys_orders_stay_out_of_the_report = None

    def test_the_variance_report(self):
        order = self.walk(self.start())
        page = self.client.get(reverse("manufacturing:costs"))
        self.assertContains(page, f'data-variance-order="{order.number}"')
        self.assertContains(page, "data-no-months")                               # this chart has no absorbed cost account
