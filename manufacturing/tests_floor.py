"""Manufacturing F8 (HG-140): the production worker's floor screen («شغلي»).

The garments plant of F4 (``tests_outside.GarmentsPlant``): an order of 100 shirts takes 120 m of fabric at 65.00
(7,800.00) and 20 spools of thread at 15.00 (300.00), so its first stage puts 8,100.00 into work in progress. Own
labour is 1,000.00, so the finished shirts cost 9,100.00 / 100 = 91.00 each (81.00 of it materials). The stage list is the garments one
(cutting, sewing, finishing, quality check, packing); no criteria are on the recipe, so its quality stage is an ordinary one.

The worker holds ``manufacturing.record_stage`` and ``inventory.view_stock`` and nothing else.
"""

import pathlib
import re
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from attachments.models import Attachment
from attachments.tests import JPEG, PDF, upload
from audit.models import AuditLog
from entities.current import working_in
from entities.models import EntityMembership
from entities.services import save_entity
from hesba_testing.factories import make_item, make_location, stock_in
from inventory.models import StockAdjustmentDirection, StockMovement, StockOperation
from inventory.services import adjust_stock, get_item_location_stock_quantity
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from permissions.services import user_has_permission
from reports.tests_dashboard import prepared_client
from staff.services import save_employee

from . import floor, floor_views, orders, outside, quality, services, workcentres
from .models import OrderStageLog, OrderStandardLine, OrderStatus, OrderStep, ProductionOrder, QualityCheck
from .tests import person
from .tests_outside import GarmentsPlant
from .tests_quality import BANNED, LATIN_WORD, QualityPlant, edges, sentences

TODAY = timezone.localdate()
RECORD = "manufacturing.record_stage"
#: «شغلانة» joins the words the earlier features already refuse
REFUSED_WORDS = BANNED + ("شغلانة",)
#: what a cost looks like in this plant: unit costs, the materials of an order, labour, the finished unit cost
MONEY = ("65.00", "15.00", "300.00", "7,800.00", "8,100.00", "1,000.00", "9,100.00", "91.00", "81.00")
COST_MARKS = ("data-cost-card", "data-cost-per-unit", "data-wc-rate", "data-cost-outside", "data-cost-extras")


class FloorPlant(GarmentsPlant):
    def setUp(self):
        super().setUp()
        self.worker = person(RoleCode.PRODUCTION_WORKER, "floor_worker")
        self.url = reverse("manufacturing:floor")

    def post(self, action, order, user=None, **data):
        self.client.force_login(user or self.worker)
        order.refresh_from_db()
        return self.client.post(self.url, {"action": action, "order": order.pk, "stage": order.stage_index, "lang": "ar", **data})

    def start(self, order, user=None):
        response = self.post("start", order, user)
        self.assertRedirects(response, f"{self.url}?lang=ar", fetch_redirect_response=False)
        return response

    def done(self, order, user=None, **data):
        response = self.post("done", order, user, **data)
        self.assertRedirects(response, f"{self.url}?lang=ar", fetch_redirect_response=False)
        order.refresh_from_db()
        return order

    def page(self, user=None, lang="ar"):
        self.client.force_login(user or self.worker)
        return self.client.get(self.url, {"lang": lang})

    def card_ids(self, user=None):
        return [int(pk) for pk in re.findall(r'data-floor-card="(\d+)"', self.page(user).content.decode())]

    def state(self, order):
        """What a stage of work leaves behind, to show a refusal changed none of it."""

        order.refresh_from_db()
        return (order.status, order.stage_index, order.stage_logs.count(), order.issues.count(), StockOperation.objects.count(),
                StockMovement.objects.count(), list(order.steps.values_list("started_at", "finished_at")))

    def issue_rows(self, order):
        """The order's material issue, as the stock engine wrote it."""

        rows = []
        for issue in order.issues.select_related("operation__item", "operation__source_location").order_by("operation__item__item_code"):
            op = issue.operation
            moves = sorted((m.movement_type, m.quantity, m.unit_cost, m.location.location_code, m.item.item_code) for m in StockMovement.objects.filter(stock_operation=op))
            rows.append((issue.kind, op.item.item_code, op.operation_type, op.adjustment_direction, op.quantity, op.unit_cost, op.status, op.operation_date,
                         op.source_location.location_code, op.destination_location_id, moves))
        return rows

    def issue_journal(self, order):
        """{item code: {control or account code: debit minus credit}} of the order's issue operations (after a rebuild)."""

        Projector().rebuild()
        found = {}
        for issue in order.issues.select_related("operation__item"):
            totals = {}
            for line in JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id=issue.operation_id).select_related("account"):
                key = line.account.control or line.account.code
                totals[key] = totals.get(key, D("0")) + line.debit - line.credit
            found[issue.operation.item.item_code] = {key: value for key, value in totals.items() if value}
        return found


# ---------------------------------------------------------------------------------------------------------------
# The first stage: the materials leave exactly as they do for the owner
# ---------------------------------------------------------------------------------------------------------------


class FirstStageTests(FloorPlant):
    def test_a_worker_starts_and_finishes_the_first_stage_and_the_materials_leave_exactly_as_for_the_owner(self):
        by_owner, by_worker = self.open(), self.open()
        orders.advance(by_owner, self.owner)                                         # the order page's way
        self.start(by_worker)
        by_worker.refresh_from_db()
        self.assertEqual((by_worker.status, by_worker.issues.count(), StockOperation.objects.filter(reference_number__startswith=by_worker.number).count()),
                         (OrderStatus.PLANNED, 0, 0))                                # a start moves nothing
        by_worker = self.done(by_worker)                                             # the floor's way
        by_owner.refresh_from_db()
        self.assertEqual((by_worker.status, by_worker.stage_index), (OrderStatus.IN_PROGRESS, 1))
        self.assertEqual((by_owner.status, by_owner.stage_index), (OrderStatus.IN_PROGRESS, 1))
        # The same operations, quantities and costs, to the last field.
        mine = self.issue_rows(by_worker)
        self.assertEqual(mine, self.issue_rows(by_owner))
        self.assertEqual([(row[1], row[3], row[4], row[5], row[8]) for row in mine],
                         [("RM-FABRIC", "out", D("120.000"), D("65.0000"), "PLANT"), ("RM-THREAD", "out", D("20.000"), D("15.0000"), "PLANT")])
        self.assertEqual(orders.issued_value(by_worker), D("8100.00"))
        self.assertEqual(orders.issued_value(by_owner), D("8100.00"))
        # The same journal: each material leaves raw materials for work in progress at its issue value.
        journal = self.issue_journal(by_worker)
        self.assertEqual(journal, self.issue_journal(by_owner))
        self.assertEqual(journal, {"RM-FABRIC": {"wip": D("7800.00"), "raw_materials": D("-7800.00")}, "RM-THREAD": {"wip": D("300.00"), "raw_materials": D("-300.00")}})
        self.assertEqual(self.books("wip", "raw_materials"), {"wip": D("16200.00"), "raw_materials": D("4200.00")})   # 20,400.00 less the two issues
        self.balanced()
        # The same standard written for the variance report, and the same stock left.
        standard = lambda order: sorted(OrderStandardLine.objects.filter(order=order).values_list("component__item_code", "quantity", "unit_cost"))  # noqa: E731
        self.assertEqual(standard(by_worker), standard(by_owner))
        self.assertEqual((get_item_location_stock_quantity(self.fabric, self.plant), get_item_location_stock_quantity(self.thread, self.plant)), (D("60.000"), D("20.000")))
        # The audit rows name the worker: the stage, and each stock operation the engine posted for it.
        log = by_worker.stage_logs.get()
        self.assertEqual((log.done_by, log.stage_key, log.good_quantity, log.defect_quantity), (self.worker, "cutting", D("100.000"), D("0.000")))
        advanced = AuditLog.objects.get(action="advance_production_order", object_id=str(by_worker.pk))
        self.assertEqual((advanced.actor, advanced.after_data["stage"]), (self.worker, "cutting"))
        started = AuditLog.objects.get(action="start_production_stage", object_id=str(by_worker.pk))
        self.assertEqual((started.actor, started.after_data["stage"], started.after_data["index"]), (self.worker, "cutting", 0))
        ids = [str(issue.operation_id) for issue in by_worker.issues.all()]
        engine = AuditLog.objects.filter(module="inventory", action="adjust_stock", object_id__in=ids)
        self.assertEqual((engine.count(), {row.actor for row in engine}), (2, {self.worker}))
        self.assertEqual({op.created_by for op in StockOperation.objects.filter(pk__in=ids)}, {self.worker})

    def test_the_worker_types_no_stock_and_no_cost_only_good_defective_and_a_note(self):
        order = self.open()
        self.start(order)
        page = self.page().content.decode()
        form = page[page.index("data-floor-done"):page.index("data-floor-done-button")]
        self.assertEqual(sorted(set(re.findall(r'name="(\w+)"', form)) - {"csrfmiddlewaretoken", "lang"}), ["action", "defect", "file", "good", "note", "order", "stage"])
        # A cost or a quantity of material smuggled into the post changes nothing: the issue is the recipe's, at the average.
        order = self.done(order, good="97", defect="3", note="تلات قطع اتقصوا غلط", unit_cost="1.00", quantity="5", minutes="1", location="999")
        self.assertEqual([(row[1], row[4], row[5]) for row in self.issue_rows(order)], [("RM-FABRIC", D("120.000"), D("65.0000")), ("RM-THREAD", D("20.000"), D("15.0000"))])
        log = order.stage_logs.get()
        self.assertEqual((log.good_quantity, log.defect_quantity, log.note), (D("97.000"), D("3.000"), "تلات قطع اتقصوا غلط"))

    def test_short_materials_stop_the_start_and_the_worker_is_told_which(self):
        order = self.open(quantity="300")                                            # 360 m of fabric; 300 on the shelf
        before = self.state(order)
        response = self.post("start", order)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-floor-card-error")
        self.assertContains(response, "خامة «قماش (متر)» مش كفاية في المخزن: الأمر محتاج 360 والموجود 300. بلّغ المشرف.")
        self.assertEqual(self.state(order), before)
        self.assertFalse(AuditLog.objects.filter(action="start_production_stage").exists())
        with self.assertRaisesMessage(ValidationError, "Not enough “قماش (متر)” in stock"):
            floor.start(order, self.worker, lang="en")

    def test_a_finish_that_the_stock_engine_refuses_records_nothing(self):
        order = self.open()
        self.start(order)
        adjust_stock("TAKE-1", TODAY, self.fabric, self.plant, StockAdjustmentDirection.OUT, D("250"), "جرد", self.owner)   # the fabric left meanwhile
        before = self.state(order)
        response = self.post("done", order)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-floor-card-error")
        self.assertContains(response, "«قماش (متر)» مش كفاية")
        self.assertEqual(self.state(order), before)
        self.assertEqual(order.status, OrderStatus.PLANNED)


# ---------------------------------------------------------------------------------------------------------------
# Start, done and the minutes
# ---------------------------------------------------------------------------------------------------------------


class StartDoneTests(FloorPlant):
    def test_minutes_recorded_are_the_finish_minus_the_start(self):
        order = self.open()
        began = timezone.now().replace(microsecond=0) - timedelta(hours=3)
        ended = began + timedelta(minutes=95, seconds=30)
        with mock.patch("django.utils.timezone.now", return_value=began):
            floor.start(order, self.worker)
        with mock.patch("django.utils.timezone.now", return_value=ended):
            floor.done(order, self.worker, good="100")
        step = OrderStep.objects.get(order=order, index=0)
        log = OrderStageLog.objects.get(order=order)
        self.assertEqual((step.started_at, step.finished_at, log.done_at), (began, ended, ended))
        self.assertEqual(log.minutes, D("95.50"))
        self.assertEqual(log.minutes, D(str((step.finished_at - step.started_at).total_seconds())) / 60)
        self.assertEqual(AuditLog.objects.get(action="advance_production_order", object_id=str(order.pk)).after_data["minutes"], "95.50")

    def test_the_minutes_are_the_clocks_and_never_typed(self):
        order = self.open()
        began = timezone.now() - timedelta(minutes=42)
        self.start(order)
        OrderStep.objects.filter(order=order, index=0).update(started_at=began)
        self.done(order, minutes="5")                                                # a typed figure is ignored
        minutes = order.stage_logs.get().minutes
        self.assertTrue(D("42.00") <= minutes <= D("42.50"), minutes)

    def test_elapsed_minutes_are_kept_to_two_decimals_and_inside_what_a_stage_can_hold(self):
        now = timezone.now()
        for seconds, minutes in ((0, "0.01"), (0.2, "0.01"), (30, "0.50"), (61, "1.02"), (3600, "60.00"), (10 ** 8, "99999.99")):
            with self.subTest(seconds=seconds):
                self.assertEqual(floor.elapsed_minutes(now, now + timedelta(seconds=seconds)), D(minutes))

    def test_a_start_writes_the_start_time_and_nothing_else(self):
        order = self.open()
        operations, movements = StockOperation.objects.count(), StockMovement.objects.count()
        self.start(order)
        order.refresh_from_db()
        step = OrderStep.objects.get(order=order, index=0)
        self.assertIsNotNone(step.started_at)
        self.assertIsNone(step.finished_at)
        self.assertEqual((order.status, order.stage_index, order.started_at, order.stage_logs.count()), (OrderStatus.PLANNED, 0, None, 0))
        self.assertEqual((StockOperation.objects.count(), StockMovement.objects.count()), (operations, movements))
        self.assertEqual(list(OrderStep.objects.filter(order=order).exclude(index=0).values_list("started_at", flat=True)), [None] * 4)
        page = self.page()
        self.assertContains(page, f'data-floor-card="{order.pk}" data-state="running"')
        self.assertContains(page, "data-floor-started")
        self.assertContains(page, "data-floor-issue-hint")                           # the first stage says it issues the materials

    def test_a_stage_is_started_once_and_is_not_finished_before_it_is_started(self):
        order = self.open()
        before = self.state(order)
        refused = self.post("done", order)
        self.assertContains(refused, "اضغط «ابدأ» الأول، وبعدها سجّل إن المرحلة خلصت.")
        self.assertEqual(self.state(order), before)
        self.start(order)
        began = OrderStep.objects.get(order=order, index=0).started_at
        again = self.post("start", order)
        self.assertContains(again, "المرحلة دي اتبدأت قبل كده.")
        self.assertEqual(OrderStep.objects.get(order=order, index=0).started_at, began)
        with self.assertRaisesMessage(ValidationError, "This stage was already started."):
            floor.start(order, self.worker, lang="en")

    def test_a_page_that_is_behind_the_order_closes_nothing(self):
        order = self.open()
        self.start(order)
        orders.advance(order, self.owner)                                            # the supervisor closed the stage from the order page meanwhile
        OrderStep.objects.filter(order=order, index=1).update(started_at=timezone.now())   # and the next one is already in hand
        before = self.state(order)
        self.client.force_login(self.worker)
        for action in ("done", "start"):
            with self.subTest(action=action):
                stale = self.client.post(self.url, {"action": action, "order": order.pk, "stage": "0", "good": "100"})
                self.assertContains(stale, "الأمر ده مبقاش في المرحلة دي؛ حدّث الصفحة.")
                self.assertEqual(self.state(order), before)
        self.assertContains(self.client.post(self.url, {"action": "finish", "order": order.pk, "stage": "1"}), "الأمر ده مبقاش في المرحلة دي")
        self.assertEqual(self.state(order), before)

    def test_one_worker_starts_and_another_finishes(self):
        other = person(RoleCode.PRODUCTION_WORKER, "floor_second")
        order = self.open()
        self.start(order)
        order = self.done(order, user=other)
        self.assertEqual(order.stage_logs.get().done_by, other)
        self.assertEqual(AuditLog.objects.get(action="start_production_stage", object_id=str(order.pk)).actor, self.worker)

    def test_a_worker_walks_every_stage_and_the_supervisor_finishes(self):
        order = self.open()
        for good, defect in (("", "2"), ("", ""), ("", "1"), ("", ""), ("97", "")):   # 2 cut wrong, 1 lost in finishing: 97 good
            self.start(order)
            order = self.done(order, good=good, defect=defect)
        self.assertEqual((order.status, order.stage_index, orders.good_units(order)), (OrderStatus.IN_PROGRESS, 5, D("97.000")))
        self.assertEqual([(log.stage_key, log.good_quantity, log.defect_quantity) for log in order.stage_logs.all()],
                         [("cutting", D("98"), D("2")), ("sewing", D("98"), D("0")), ("finishing", D("97"), D("1")), ("quality", D("97"), D("0")), ("packing", D("97"), D("0"))])
        self.assertTrue(all(log.done_by == self.worker and log.minutes is not None and log.minutes > 0 for log in order.stage_logs.all()))
        self.assertEqual(self.card_ids(), [])                                        # nothing is left on the floor: the finish is the supervisor's
        self.assertContains(self.page(), "data-floor-empty")
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, order.run.output_quantity, order.run.scrap_quantity), (OrderStatus.DONE, D("97.000"), D("3.000")))
        self.assertEqual(get_item_location_stock_quantity(self.shirt, self.plant), D("97.000"))
        self.balanced()

    def test_the_card_shows_the_units_entering_the_stage_and_the_last_stage_takes_an_over_yield_with_a_reason(self):
        order = self.open()
        self.start(order)
        order = self.done(order, defect="4")
        self.assertContains(self.page(), '<bdi dir="ltr" data-floor-units>96</bdi>')
        refused = self.post("done", order)                                           # sewing was not started
        self.assertContains(refused, "اضغط «ابدأ» الأول")
        self.start(order)
        too_many = self.post("done", order, good="97")
        self.assertContains(too_many, "السليم والمعيب مع بعض مينفعش يزيدوا عن 96 وحدة داخلة المرحلة دي.")
        self.assertContains(too_many, 'name="good" inputmode="decimal" dir="ltr" autocomplete="off" placeholder="96" value="97"')   # what was typed stays


# ---------------------------------------------------------------------------------------------------------------
# What the worker cannot do, and never sees
# ---------------------------------------------------------------------------------------------------------------


class PermissionTests(FloorPlant):
    def test_the_role_holds_the_floor_and_the_stock_view_and_nothing_else(self):
        for code in (RECORD, "inventory.view_stock"):
            self.assertTrue(user_has_permission(self.worker, code), code)
        for code in ("inventory.adjust_stock", "inventory.transfer_stock", "inventory.manage_movements", "inventory.view_cost", "inventory.view_group_stock",
                     "master_data.manage_items", "master_data.view_master_data", "sales.view_sales_invoices", "sales.create_sales_invoice",
                     "purchases.view_purchase_invoices", "cashboxes.view_cashboxes", "cashboxes.move_cash", "reports.view_profit_report",
                     "reports.view_inventory_report", "settings.view_settings", "accounting.view_ledger"):
            self.assertFalse(user_has_permission(self.worker, code), code)

    def test_the_worker_cannot_reach_the_stock_adjustment_screen_or_any_other_stock_operation(self):
        self.client.force_login(self.worker)
        before = (StockOperation.objects.count(), StockMovement.objects.count())
        for name in ("inventory:adjustment", "inventory:transfer", "inventory:stocktake"):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        refused = self.client.post(reverse("inventory:adjustment"), {"item": self.fabric.pk, "location": self.plant.pk, "direction": "out", "quantity": "5",
                                                                     "reason": "x", "operation_date": TODAY.isoformat()})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual((StockOperation.objects.count(), StockMovement.objects.count()), before)

    def test_the_general_adjustment_service_refuses_the_worker(self):
        with self.assertRaisesMessage(PermissionDenied, "This stock operation requires inventory.adjust_stock."):
            adjust_stock("W-1", TODAY, self.fabric, self.plant, StockAdjustmentDirection.OUT, D("5"), "x", self.worker)
        with self.assertRaisesMessage(PermissionDenied, "This stock operation requires inventory.adjust_stock."):
            adjust_stock("W-2", TODAY, self.fabric, self.plant, StockAdjustmentDirection.IN, D("5"), "x", self.worker, unit_cost=D("1"))
        # A name that is not in the engine's list stands in for nothing, whoever passes it.
        for user in (self.worker, self.owner):
            with self.assertRaisesMessage(PermissionDenied, "manufacturing.finish_order cannot stand in for inventory.adjust_stock."):
                adjust_stock("W-3", TODAY, self.fabric, self.plant, StockAdjustmentDirection.OUT, D("5"), "x", user, stand_in="manufacturing.finish_order")
        self.assertFalse(StockOperation.objects.filter(reference_number__startswith="W-").exists())

    def test_the_stand_in_is_honoured_only_for_someone_who_holds_the_floor_permission(self):
        order = self.open()
        before = self.state(order)
        for code in (RoleCode.CASHIER, RoleCode.ACCOUNTANT, RoleCode.WAITER, RoleCode.SITE_ENGINEER):
            user = person(code, f"floor_no_{code}")
            self.assertFalse(user_has_permission(user, RECORD) or user_has_permission(user, "inventory.adjust_stock"))
            with self.subTest(role=code):
                # The stock engine itself: the stand-in gives nothing to a user who does not hold it.
                with self.assertRaisesMessage(PermissionDenied, "This stock operation requires inventory.adjust_stock."):
                    orders.advance(order, user, stand_in=RECORD)
                with self.assertRaisesMessage(PermissionDenied, "This stock operation requires inventory.adjust_stock."):
                    services.issue(self.recipe, user, batches=D("10"), location=self.plant, reference="X-1", stand_in=RECORD)
                # And the floor's own rules and screen.
                for act in (floor.start, floor.done):
                    with self.assertRaises(PermissionDenied):
                        act(order, user)
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.client.post(self.url, {"action": "start", "order": order.pk, "stage": "0"}).status_code, 403)
                self.assertEqual(self.client.post(reverse("manufacturing:floor_photo", args=[order.pk]), {"file": upload(JPEG, "d.jpg", "image/jpeg")}).status_code, 403)
        self.assertEqual(self.state(order), before)
        # Without the stand-in the worker's own issue is refused too: the floor flow is the only one that passes it.
        with self.assertRaisesMessage(PermissionDenied, "This stock operation requires inventory.adjust_stock."):
            orders.advance(order, self.worker)
        with self.assertRaisesMessage(PermissionDenied, "This stock operation requires inventory.adjust_stock."):
            services.issue(self.recipe, self.worker, batches=D("10"), location=self.plant, reference="X-2")
        self.assertEqual(self.state(order), before)

    def test_only_the_floor_rules_pass_the_stand_in(self):
        """Every place in the code (tests aside) that names the floor permission as a stand-in, or hands one to an order's stage."""

        root = pathlib.Path(settings.BASE_DIR)
        passing, advancing = [], []
        for path in sorted(root.rglob("*.py")):
            name = path.relative_to(root).as_posix()
            if "/tests" in f"/{name}" or "/migrations/" in name or name.startswith(".") or "/." in name:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"stand_in\s*=\s*(RECORD|floor\.RECORD|[\"']manufacturing\.record_stage[\"'])", text):
                passing.append(name)
            for line in text.splitlines():
                if re.search(r"\b(orders\.)?advance\(.*stand_in\s*=\s*(?!stand_in\b)", line) and "def advance" not in line:
                    advancing.append(name)
        self.assertEqual(passing, ["manufacturing/floor.py"])
        self.assertEqual(advancing, ["manufacturing/floor.py"])
        from inventory.services import ADJUSTMENT_STAND_INS

        self.assertEqual(ADJUSTMENT_STAND_INS, frozenset({"restaurant.prepare_at_payment", "projects.issue_to_site", RECORD}))

    def test_the_worker_cannot_finish_or_cancel_an_order_or_do_anything_else_from_the_order_page(self):
        ready = self.open()
        for _ in ready.stages:
            self.start(ready)
            ready = self.done(ready)
        started = self.open()
        self.start(started)
        started = self.done(started)
        planned = self.open()
        self.client.force_login(self.worker)
        snapshot = lambda: [(o.status, o.stage_index, o.run_id, o.notes, o.cost_lines.count(), o.issues.count()) for o in ProductionOrder.objects.order_by("pk")]  # noqa: E731
        before, operations = snapshot(), StockOperation.objects.count()
        for order, data in ((ready, {"action": "finish"}), (ready, {"action": "cancel", "reason": "x"}), (started, {"action": "cancel", "reason": "x"}),
                            (planned, {"action": "cancel", "reason": "x"}), (planned, {"action": "advance"}), (started, {"action": "advance", "good": "100"}),
                            (started, {"action": "extra", "item": self.thread.pk, "quantity": "1", "why": "x"}),
                            (started, {"action": "return", "item": self.thread.pk, "quantity": "1"}),
                            (started, {"action": "add_cost", "kind": "labour", "amount": "50"})):
            with self.subTest(order=order.number, action=data["action"]):
                self.assertEqual(self.client.post(reverse("manufacturing:order", args=[order.pk]), data).status_code, 403)
        self.assertEqual(self.client.post(reverse("manufacturing:orders"), {"recipe": self.recipe.pk, "location": self.plant.pk, "quantity": "10"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("manufacturing:outside_action", args=[started.pk]), {"action": "send", "supplier": self.supplier.pk}).status_code, 403)
        self.assertEqual((snapshot(), StockOperation.objects.count()), (before, operations))
        # The floor screen itself offers neither: no such action exists on it.
        for action in ("finish", "cancel"):
            self.assertContains(self.client.post(self.url, {"action": action, "order": ready.pk, "reason": "x"}, follow=True), "data-floor")
        self.assertEqual(snapshot(), before)
        page = self.client.get(reverse("manufacturing:order", args=[ready.pk])).content.decode()
        for mark in ("data-finish", "data-advance", "mfg-cancel", 'value="finish"', 'value="cancel"'):
            self.assertNotIn(mark, page, mark)

    def pages(self):
        """Every manufacturing page that opens with a plain address, on orders at every point of their life."""

        self.more_materials()
        planned = self.open()
        started = self.open()
        orders.advance(started, self.owner, minutes="30")
        done = self.walk(self.open())
        centre = workcentres.save_work_centre({"code": "CUT", "name_ar": "ترابيزة القص", "kind": "manual", "hours_per_day": "8", "rate_per_hour": "65.00"}, self.owner)
        names = {"home": [], "recipe_new": [], "recipe": [self.recipe.pk], "run": [done.run_id], "orders": [], "mrp": [], "trace": [], "costs": [], "schedule": [],
                 "work_centres": [], "work_centre_new": [], "work_centre": [centre.pk], "quality": [], "outside": [], "floor": []}
        urls = {name: reverse(f"manufacturing:{name}", args=args) for name, args in names.items()}
        for label, order in (("order planned", planned), ("order started", started), ("order done", done)):
            urls[label] = reverse("manufacturing:order", args=[order.pk])
        return urls, started, done

    def test_the_worker_never_sees_a_cost_on_any_manufacturing_page(self):
        urls, started, done = self.pages()
        self.start(self.open())                                                      # a running card too
        self.client.force_login(self.owner)                                          # the owner does see them, so the strings below are real costs
        for label, texts in (("order started", ("7,800.00", "300.00", "8,100.00", "65.00", "15.00")), ("order done", ("9,100.00", "91.00", "81.00", "1,000.00"))):
            body = self.client.get(urls[label]).content.decode()
            for text in texts:
                self.assertIn(text, body, f"{label}: {text}")
        self.client.force_login(self.worker)
        opened = []
        for label, url in urls.items():
            for lang in ("ar", "en"):
                response = self.client.get(url, {"lang": lang})
                with self.subTest(page=label, lang=lang):
                    self.assertIn(response.status_code, (200, 403), label)
                    if response.status_code != 200:
                        continue
                    opened.append(label)
                    body = response.content.decode()
                    for text in MONEY:
                        self.assertNotIn(text, body, f"{label}: {text}")
                    for mark in COST_MARKS:
                        self.assertNotIn(mark, body, f"{label}: {mark}")
        # The pages the worker does open (the rest answer 403): the floor and the read-only production pages of whoever sees stock.
        self.assertEqual(sorted(set(opened)), ["floor", "home", "mrp", "order done", "order planned", "order started", "orders", "outside", "quality",
                                               "recipe", "run", "schedule", "trace", "work_centres"])
        for label in ("costs", "recipe_new", "work_centre_new", "work_centre"):
            self.assertEqual(self.client.get(urls[label]).status_code, 403, label)

    def test_the_floor_screen_says_no_cost_even_to_someone_who_may_see_costs(self):
        order = self.open()
        self.start(order, user=self.owner)
        body = self.page(self.owner).content.decode()
        self.assertIn(f'data-floor-card="{order.pk}"', body)
        for text in MONEY + COST_MARKS:
            self.assertNotIn(text, body, text)

    def test_the_supervisor_uses_the_floor_through_stock_adjustment_without_holding_the_floor_permission(self):
        keeper = person(RoleCode.STOCK_KEEPER, "floor_supervisor")           # holds inventory.adjust_stock, not the floor permission
        self.assertFalse(user_has_permission(keeper, RECORD))
        self.assertTrue(user_has_permission(keeper, "inventory.adjust_stock"))
        order = self.open()
        self.start(order, user=keeper)
        order = self.done(order, user=keeper)
        self.assertEqual((order.status, order.stage_index, order.stage_logs.get().done_by, orders.issued_value(order)),
                         (OrderStatus.IN_PROGRESS, 1, keeper, D("8100.00")))
        self.assertContains(self.client.get(reverse("manufacturing:orders")), "data-floor-link")


class NavigationTests(FloorPlant):
    def test_the_worker_lands_on_the_floor_and_has_it_first_in_the_menu(self):
        self.client.force_login(self.worker)
        self.assertRedirects(self.client.get(reverse("after_login")), self.url, fetch_redirect_response=False)
        body = self.client.get(self.url).content.decode()
        self.assertIn(f'href="{self.url}?lang=ar" title="شغلي" aria-current="page"', body)
        self.assertIn('data-tab="floor"', body)
        self.assertIn('href="#hs-i-floor"', body)
        self.assertLess(body.index('data-tab="floor"'), body.index('data-tab="manufacturing"') if 'data-tab="manufacturing"' in body else len(body))
        self.assertIn("My work", self.client.get(self.url, {"lang": "en"}).content.decode())

    def test_nobody_else_has_the_floor_in_the_menu_and_the_owner_still_lands_on_the_dashboard(self):
        for code in (RoleCode.OWNER, RoleCode.MANAGER, RoleCode.STOCK_KEEPER):
            user = person(code, f"floor_nav_{code}")
            self.client.force_login(user)
            body = self.client.get(reverse("manufacturing:orders")).content.decode()
            with self.subTest(role=code):
                self.assertNotIn('data-tab="floor"', body)
                self.assertNotIn(f'href="{self.url}?lang=ar" title=', body)
                self.assertIn("data-floor-link", body)                               # the way in is on the board, for whoever records stages
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.get(reverse("after_login")), reverse("dashboard_snapshot"), fetch_redirect_response=False)

    def test_the_role_is_offered_where_manufacturing_is_used(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("settings_core:roles"))
        self.assertContains(page, "عامل إنتاج")
        self.assertContains(page, "تسجيل مراحل أوامر الإنتاج من شاشة العامل")
        self.assertContains(page, "شاشة العامل: يبدأ مرحلة أمر الإنتاج")

    def test_the_owner_gives_the_ready_role_and_can_put_its_permission_in_a_role_of_their_own(self):
        """HG-140: nobody hands out a permission they do not hold (FB-ROLES), so the owner and the manager hold the floor
        permission too. A stock keeper, who does not, still cannot hand it out."""

        from accounts.user_services import create_user_account
        from permissions.models import Role
        from permissions.role_services import create_role

        ready = Role.objects.get(code="production_worker")
        made = create_user_account(username="floor_new", password="Temp-pass-91", role=ready, actor=self.owner, display_name="عامل جديد", phone="", email="")
        self.assertTrue(user_has_permission(made, RECORD))
        role = create_role(name_ar="عامل ومخزن", codes=["inventory.view_stock", "inventory.view_group_stock", RECORD], actor=self.owner)
        self.assertEqual(sorted(role.rolepermission_set.values_list("permission__code", flat=True)),
                         ["inventory.view_group_stock", "inventory.view_stock", RECORD])
        self.assertTrue(user_has_permission(self.owner, RECORD) and user_has_permission(person(RoleCode.MANAGER, "floor_manager"), RECORD))
        self.assertFalse(user_has_permission(person(RoleCode.STOCK_KEEPER, "floor_keeper_role"), RECORD))

    def test_a_business_without_manufacturing_is_not_offered_the_role_and_has_no_floor(self):
        self.profile.delete()
        prepared_client()                                                            # a shop: no manufacturing module
        self.client.force_login(self.worker)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.assertRedirects(self.client.get(reverse("after_login")), reverse("dashboard_snapshot"), fetch_redirect_response=False)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("settings_core:roles")), "عامل إنتاج")   # still listed while someone wears it
        self.worker.hesba_profile.delete()
        self.assertNotContains(self.client.get(reverse("settings_core:roles")), "عامل إنتاج")


# ---------------------------------------------------------------------------------------------------------------
# My work: the work centres, the order of the cards, the entity
# ---------------------------------------------------------------------------------------------------------------


class MyWorkTests(FloorPlant):
    def setUp(self):
        super().setUp()
        self.more_materials()
        self.cut = workcentres.save_work_centre({"code": "CUT", "name_ar": "ترابيزة القص", "name_en": "Cutting table", "kind": "manual", "hours_per_day": "8"}, self.owner)
        self.sew = workcentres.save_work_centre({"code": "SEW", "name_ar": "ماكينة الخياطة", "name_en": "Sewing machine", "kind": "machine", "hours_per_day": "8"}, self.owner)
        self.trousers = make_item(item_code="FG-TROUSERS", item_name="بنطلون", is_stock_tracked=True)
        self.plain = services.save_recipe({"product": self.trousers, "output_quantity": "10"}, [(self.fabric, "10")], self.owner)   # no routing: the factory's stages
        workcentres.save_routing(self.recipe, [{"name_ar": "قص", "name_en": "Cutting", "work_centre": self.cut.pk, "minutes_per_batch": "30", "setup_minutes": "0"},
                                               {"name_ar": "خياطة", "name_en": "Sewing", "work_centre": self.sew.pk, "minutes_per_batch": "45", "setup_minutes": "0"}], self.owner)
        self.at_cut = self.open()
        self.at_sew = self.open()
        orders.advance(self.at_sew, self.owner)
        self.unrouted = orders.create_order({"recipe": self.plain, "location": self.plant, "quantity": "20"}, self.owner)

    def worker_at(self, username, *centres):
        user = person(RoleCode.PRODUCTION_WORKER, username)
        employee = save_employee({"name": f"عامل {username}", "user": user.pk}, self.owner)
        for centre in centres:
            centre.workers.add(employee)
        return user

    def test_a_worker_sees_the_stages_of_their_own_work_centres_and_the_stages_that_have_none(self):
        cutter, sewer, both = self.worker_at("cutter", self.cut), self.worker_at("sewer", self.sew), self.worker_at("both", self.cut, self.sew)
        self.assertEqual(self.card_ids(cutter), [self.at_cut.pk, self.unrouted.pk])
        self.assertEqual(self.card_ids(sewer), [self.at_sew.pk, self.unrouted.pk])
        self.assertEqual(self.card_ids(both), [self.at_cut.pk, self.at_sew.pk, self.unrouted.pk])
        self.assertEqual(self.card_ids(self.worker), [self.at_cut.pk, self.at_sew.pk, self.unrouted.pk])   # on no centre: every centre's stages
        page = self.page(cutter).content.decode()
        self.assertIn("data-floor-centres", page)
        self.assertIn("<bdi>ترابيزة القص</bdi>", page)
        self.assertIn('<span class="tc-type tc-type--wo" data-floor-centre>ترابيزة القص</span>', page)
        self.assertNotIn("data-floor-centres", self.page(self.worker).content.decode())
        self.assertIn("Cutting table", self.page(cutter, "en").content.decode())
        # The stage moves on: the order leaves the cutter's screen and reaches the sewer's.
        self.start(self.at_cut, user=cutter)
        self.done(self.at_cut, user=cutter)
        self.assertEqual(self.card_ids(cutter), [self.unrouted.pk])
        self.assertEqual(self.card_ids(sewer), [self.at_cut.pk, self.at_sew.pk, self.unrouted.pk])

    def test_the_schedules_work_centre_is_the_one_that_counts_and_a_centre_switched_off_is_nobodys(self):
        cutter, sewer = self.worker_at("cutter2", self.cut), self.worker_at("sewer2", self.sew)
        OrderStep.objects.filter(order=self.at_cut, index=0).update(work_centre=self.sew)   # the schedule moved the stage to the other centre
        self.assertEqual(self.card_ids(cutter), [self.unrouted.pk])
        self.assertEqual(self.card_ids(sewer), [self.at_cut.pk, self.at_sew.pk, self.unrouted.pk])
        self.cut.active = False
        self.cut.save()
        self.assertEqual(floor.my_centres(cutter), [])                               # no active centre: every centre's stages again
        self.assertEqual(self.card_ids(cutter), [self.at_cut.pk, self.at_sew.pk, self.unrouted.pk])

    def test_the_work_centre_form_keeps_its_workers(self):
        user = person(RoleCode.PRODUCTION_WORKER, "ticked")
        employee = save_employee({"name": "حسن القصّاص", "user": user.pk}, self.owner)
        idle = save_employee({"name": "موظف موقوف", "active": False}, self.owner)
        self.client.force_login(self.owner)
        url = reverse("manufacturing:work_centre", args=[self.cut.pk])
        blank = self.client.get(url)
        self.assertContains(blank, "data-wc-workers")
        self.assertContains(blank, f'name="workers" value="{employee.pk}"><span>حسن القصّاص')
        self.assertNotContains(blank, "موظف موقوف")
        data = {"code": "CUT", "name_ar": "ترابيزة القص", "kind": "manual", "hours_per_day": "8", "rate_per_hour": "0", "active": "on"}
        self.client.post(url, {**data, "workers": [str(employee.pk), str(idle.pk), "999999", "x"]})
        self.assertEqual(list(self.cut.workers.all()), [employee])                   # an inactive or unknown employee is left out
        self.assertContains(self.client.get(url), f'name="workers" value="{employee.pk}" checked')
        self.assertEqual(floor.my_centres(user), [self.cut])
        audit = AuditLog.objects.filter(action="save_work_centre", object_id=str(self.cut.pk)).order_by("-pk").first()
        self.assertEqual((audit.before_data["workers"], audit.after_data["workers"]), ([], [employee.code]))
        self.client.post(url, data)                                                  # none ticked: nobody works at the centre
        self.assertEqual(self.cut.workers.count(), 0)
        workcentres.save_work_centre({"code": "CUT", "name_ar": "ترابيزة القص", "kind": "manual", "hours_per_day": "6"}, self.owner, self.cut)
        self.cut.workers.add(employee)
        workcentres.save_work_centre({"code": "CUT", "name_ar": "ترابيزة القص", "kind": "manual", "hours_per_day": "7"}, self.owner, self.cut)
        self.assertEqual(list(self.cut.workers.all()), [employee])                   # a save that does not send the workers leaves them

    def test_the_cards_are_in_the_order_of_the_promised_date_and_a_late_one_says_so(self):
        ProductionOrder.objects.filter(pk=self.at_cut.pk).update(due_date=TODAY + timedelta(days=5))
        ProductionOrder.objects.filter(pk=self.unrouted.pk).update(due_date=TODAY - timedelta(days=1))
        self.assertEqual(self.card_ids(), [self.unrouted.pk, self.at_cut.pk, self.at_sew.pk])   # late, then due, then the one with no date
        cards = {card["pk"]: card for card in floor.cards(self.worker)}
        self.assertEqual([cards[pk]["late"] for pk in (self.unrouted.pk, self.at_cut.pk, self.at_sew.pk)], [True, False, False])
        page = self.page().content.decode()
        self.assertEqual(page.count("data-floor-late"), 1)
        self.assertIn(f"<bdi>{(TODAY - timedelta(days=1)).isoformat()}</bdi>", page)

    def test_finished_and_cancelled_orders_and_orders_ready_to_finish_are_not_on_the_floor(self):
        done = self.walk(self.open())
        cancelled = self.open()
        orders.cancel(cancelled, self.owner, "اتلغى")
        ready = self.unrouted
        for _ in ready.stages:
            orders.advance(ready, self.owner)
            ready.refresh_from_db()
        self.assertEqual(self.card_ids(), [self.at_cut.pk, self.at_sew.pk])
        for order in (done, cancelled, ready):
            with self.subTest(order=order.status):
                for action in ("start", "done"):
                    self.assertContains(self.post(action, order), "الأمر ده مبقاش في المرحلة دي")


class EntityTests(FloorPlant):
    def test_a_worker_of_another_entity_neither_sees_nor_touches_this_entitys_orders(self):
        branch = save_entity({"code": "BRF", "name_ar": "فرع العبور", "activity_slug": "manufacturing", "sub_activity_slug": "garments"}, self.owner)
        branch_plant = make_location(location_code="BR-PLANT-F", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        stock_in(self.fabric, branch_plant, 300, "65.00", movement_date=TODAY)
        stock_in(self.thread, branch_plant, 60, "15.00", movement_date=TODAY)
        near = self.open()                                                           # the main entity's
        far = orders.create_order({"recipe": self.recipe, "location": branch_plant, "quantity": "100", "labor_cost": "1000"}, self.owner)
        outsider = person(RoleCode.PRODUCTION_WORKER, "branch_worker")
        EntityMembership.objects.create(user=outsider, entity=branch, is_default=True)
        self.client.force_login(outsider)
        page = self.client.get(self.url).content.decode()
        self.assertIn(f'data-floor-card="{far.pk}"', page)
        self.assertNotIn(f'data-floor-card="{near.pk}"', page)
        self.assertNotIn(near.number, page)
        before = self.state(near)
        for data in ({"action": "start"}, {"action": "done", "good": "100"}):
            with self.subTest(action=data["action"]):
                self.assertEqual(self.client.post(self.url, {**data, "order": near.pk, "stage": "0"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("manufacturing:floor_photo", args=[near.pk]), {"file": upload(JPEG, "d.jpg", "image/jpeg")}).status_code, 404)
        self.assertEqual(self.client.get(reverse("manufacturing:order", args=[near.pk])).status_code, 404)
        self.assertEqual((self.state(near), Attachment.objects.count()), (before, 0))
        with working_in(branch):
            self.assertEqual([card["pk"] for card in floor.cards(outsider)], [far.pk])
        # In their own entity the same worker does the work, and the materials leave the branch's place.
        self.client.post(self.url, {"action": "start", "order": far.pk, "stage": "0"})
        self.client.post(self.url, {"action": "done", "order": far.pk, "stage": "0"})
        far.refresh_from_db()
        self.assertEqual((far.status, far.stage_index, {issue.operation.source_location for issue in far.issues.all()}), (OrderStatus.IN_PROGRESS, 1, {branch_plant}))
        self.assertEqual(get_item_location_stock_quantity(self.fabric, self.plant), D("300.000"))   # the main entity's shelf is untouched
        # A worker of the main entity sees it the other way round.
        local = person(RoleCode.PRODUCTION_WORKER, "main_worker")
        from entities.models import Entity

        EntityMembership.objects.create(user=local, entity=Entity.objects.get(is_main=True), is_default=True)
        self.assertEqual(self.card_ids(local), [near.pk])
        self.assertEqual(self.client.post(self.url, {"action": "start", "order": far.pk, "stage": "1"}).status_code, 404)


# ---------------------------------------------------------------------------------------------------------------
# F5 and F4: not closed from the floor
# ---------------------------------------------------------------------------------------------------------------


class QualityStageTests(QualityPlant):
    """The detergent plant of F5: mixing, processing, quality check (two criteria on the recipe), filling. Lots are on."""

    def setUp(self):
        super().setUp()
        self.worker = person(RoleCode.PRODUCTION_WORKER, "qc_floor_worker")
        self.url = reverse("manufacturing:floor")
        self.client.force_login(self.worker)

    def act(self, action, order, **data):
        order.refresh_from_db()
        return self.client.post(self.url, {"action": action, "order": order.pk, "stage": order.stage_index, **data})

    def at_quality(self):
        order = self.open()
        for _ in range(2):                                                           # mixing (which issues the base, with its lots) and processing
            self.assertEqual(self.act("start", order).status_code, 302)
            self.assertEqual(self.act("done", order).status_code, 302)
        order.refresh_from_db()
        self.assertEqual((order.stages[order.stage_index][0], order.status), ("quality", OrderStatus.IN_PROGRESS))
        return order

    def test_a_quality_stage_with_criteria_is_not_closed_from_the_floor(self):
        order = self.at_quality()
        self.assertEqual(orders.issued_value(order), D("2000.00"))                   # the worker's first stage issued 100 kg at 20.00, lots recorded
        self.assertTrue(order.lot_uses.exists())
        page = self.client.get(self.url)
        self.assertContains(page, f'data-floor-card="{order.pk}" data-state="quality"')
        self.assertContains(page, '<p class="fl-wait" data-floor-wait="quality">دي مرحلة فحص الجودة، والمشرف هو اللي بيسجّل نتيجتها من صفحة الأمر.</p>')
        card = page.content.decode().split(f'data-floor-card="{order.pk}"')[1].split("</article>")[0]
        for mark in ("data-floor-start", "data-floor-done", "<button"):
            self.assertNotIn(mark, card, mark)
        before = (order.stage_index, order.stage_logs.count(), QualityCheck.objects.count(), list(order.steps.values_list("started_at", flat=True)))
        for action in ("start", "done"):
            with self.subTest(action=action):
                refused = self.act(action, order, good="50")
                self.assertContains(refused, "دي مرحلة فحص الجودة، والمشرف هو اللي بيسجّل نتيجتها من صفحة الأمر.")
        for act in (floor.start, floor.done):
            with self.assertRaisesMessage(ValidationError, "This is the quality check stage; the supervisor records its result on the order page."):
                act(order, self.worker, lang="en")
        order.refresh_from_db()
        self.assertEqual((order.stage_index, order.stage_logs.count(), QualityCheck.objects.count(), list(order.steps.values_list("started_at", flat=True))), before)
        self.assertContains(self.client.get(self.url, {"lang": "en"}), "This is the quality check stage; the supervisor records its result on the order page.")
        # The supervisor records it with its criteria, and the next stage comes back to the floor.
        quality.advance(order, self.owner, results=self.good())
        self.assertEqual(QualityCheck.objects.filter(order=order, kind="stage").count(), 1)
        self.assertContains(self.client.get(self.url), f'data-floor-card="{order.pk}" data-state="ready"')
        self.assertEqual(self.act("start", order).status_code, 302)
        self.assertEqual(self.act("done", order).status_code, 302)
        order.refresh_from_db()
        self.assertEqual((order.stage_index, order.stage_logs.last().done_by), (4, self.worker))

    def test_a_quality_stage_without_criteria_is_an_ordinary_stage(self):
        plain = services.save_recipe({"product": make_item(item_code="FG-BLEACH", item_name="كرتونة مبيّض", is_stock_tracked=True), "output_quantity": "10"},
                                     [(self.base, "20")], self.owner)
        order = self.open(recipe=plain)
        for _ in range(3):
            self.assertEqual(self.act("start", order).status_code, 302)
            self.assertEqual(self.act("done", order).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(([log.stage_key for log in order.stage_logs.all()], order.stage_index, QualityCheck.objects.count()), (["mixing", "processing", "quality"], 3, 0))


class OutsideStageTests(FloorPlant):
    def test_a_stage_at_an_outside_supplier_is_not_closed_from_the_floor(self):
        order = self.at_finishing()
        self.start(order)                                                            # in hand on the floor, then sent out
        job = self.sent(order)
        page = self.page()
        self.assertContains(page, f'data-floor-card="{order.pk}" data-state="outside"')
        self.assertContains(page, '<p class="fl-wait" data-floor-wait="outside">المرحلة دي عند المورّد «مصنع النور للتطريز»، والمشرف هو اللي بيسجّل رجوعها.</p>')
        card = page.content.decode().split(f'data-floor-card="{order.pk}"')[1].split("</article>")[0]
        for mark in ("data-floor-start", "data-floor-done", "<button", "3.00"):
            self.assertNotIn(mark, card, mark)
        before = self.state(order)
        for action in ("start", "done"):
            with self.subTest(action=action):
                self.assertContains(self.post(action, order, good="100"), "المرحلة دي عند المورّد «مصنع النور للتطريز»، والمشرف هو اللي بيسجّل رجوعها.")
        for act in (floor.start, floor.done):
            with self.assertRaisesMessage(ValidationError, "This stage is at the supplier “مصنع النور للتطريز”; the supervisor records its return."):
                act(order, self.worker, lang="en")
        self.assertEqual(self.state(order), before)
        self.assertContains(self.page(lang="en"), "This stage is at the supplier “مصنع النور للتطريز”; the supervisor records its return.")
        # Its return closes it (F4), and the next stage is the floor's again.
        outside.receive(job, self.owner, good="98", defect="2")
        order.refresh_from_db()
        self.assertEqual(order.stages[order.stage_index][0], "quality")
        self.assertContains(self.page(), f'data-floor-card="{order.pk}" data-state="ready"')
        self.start(order)
        order = self.done(order)
        self.assertEqual((order.stage_index, order.stage_logs.last().good_quantity), (4, D("98.000")))


# ---------------------------------------------------------------------------------------------------------------
# The photo of a defect
# ---------------------------------------------------------------------------------------------------------------


class PhotoTests(FloorPlant):
    def setUp(self):
        super().setUp()
        self.order = self.open()
        self.start(self.order)
        self.photo_url = reverse("manufacturing:floor_photo", args=[self.order.pk])

    def send(self, raw=JPEG, name="defect.jpg", mime="image/jpeg", user=None, **data):
        self.client.force_login(user or self.worker)
        return self.client.post(self.photo_url, {"file": upload(raw, name, mime), "stage": "photo", **data})

    def test_a_defect_photo_is_kept_on_the_order_and_shows_on_the_floor_and_on_the_order_page(self):
        operations = StockOperation.objects.count()
        response = self.send()
        self.assertEqual(response.status_code, 201)
        photo = Attachment.objects.get()
        self.assertEqual((photo.owner, photo.kind, photo.stage, photo.uploaded_by, photo.content_type_name, photo.entity_id),
                         (self.order, "photo", "photo", self.worker, "image/jpeg", self.plant.entity_id))
        self.assertEqual(StockOperation.objects.count(), operations)                 # a photo books nothing
        self.assertTrue(AuditLog.objects.filter(action="add_attachment", actor=self.worker, object_id=str(photo.pk)).exists())
        file_url = reverse("attachments:file", args=[photo.pk])
        card = self.page()
        self.assertContains(card, f'id="fl-photos-{self.order.pk}" data-attachments data-refresh="inline"')
        self.assertContains(card, f'data-floor-photo="{photo.pk}"')
        self.assertContains(card, f'action="{self.photo_url}?lang=ar"')
        self.assertContains(card, 'accept="image/*" capture="environment"')
        self.assertEqual(self.client.get(file_url).status_code, 200)
        self.assertNotContains(card, "data-att-delete")                              # the floor adds; it does not remove
        self.client.force_login(self.owner)
        page = self.client.get(reverse("manufacturing:order", args=[self.order.pk]))
        self.assertContains(page, "data-floor-photos-card")
        self.assertContains(page, "صور العيوب")
        self.assertContains(page, f'data-attachment="{photo.pk}"')
        self.assertContains(page, f'data-att-delete data-url="{reverse("attachments:delete", args=[photo.pk])}?lang=ar"')
        self.assertNotContains(self.client.get(reverse("manufacturing:order", args=[self.open().pk])), "data-floor-photos-card")   # an order with no photo has no such section

    def test_only_a_real_image_and_only_as_a_defect_photo(self):
        self.assertEqual(self.send(PDF, "report.pdf", "application/pdf", kind="document").status_code, 400)
        self.assertEqual(self.send(PDF, "report.jpg", "image/jpeg").status_code, 400)                       # a PDF dressed as a photo
        self.assertEqual(self.send(b"<html><script>alert(1)</script></html>", "x.jpg", "image/jpeg").status_code, 400)
        self.assertEqual(self.send(stage="receive").status_code, 400)                                        # an order takes a defect photo, nothing else
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(self.client.get(self.photo_url).status_code, 405)

    def test_the_worker_adds_but_only_the_supervisor_removes_and_a_closed_order_keeps_its_photos(self):
        self.send()
        photo = Attachment.objects.get()
        delete = reverse("attachments:delete", args=[photo.pk])
        self.client.force_login(self.worker)
        refused = self.client.post(delete)
        self.assertEqual((refused.status_code, refused.json()["error"]), (403, "مسح صورة العيب لصاحب صلاحية تسوية المخزون بس."))
        self.assertNotContains(self.client.get(reverse("manufacturing:order", args=[self.order.pk])), "data-att-delete")
        viewer = person(RoleCode.SITE_ENGINEER, "floor_viewer")                      # sees stock, records no stage
        self.assertEqual(self.send(user=viewer).status_code, 403)
        self.assertEqual(self.client.get(reverse("attachments:file", args=[photo.pk])).status_code, 200)   # whoever sees the order sees its photos
        cashier = person(RoleCode.CASHIER, "floor_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("attachments:file", args=[photo.pk])).status_code, 403)
        # Once the order is finished, nothing is added and nothing is removed.
        self.done(self.order)
        self.walk(self.order)
        late = self.send()
        self.assertEqual((late.status_code, late.json()["error"]), (400, "الأمر ده خلص أو اتلغى؛ مينفعش تتضاف عليه صور."))
        self.client.force_login(self.owner)
        kept = self.client.post(delete)
        self.assertEqual((kept.status_code, kept.json()["error"]), (400, "الأمر ده خلص أو اتلغى؛ صوره بتفضل زي ما هي ومينفعش تتمسح."))
        page = self.client.get(reverse("manufacturing:order", args=[self.order.pk]))
        self.assertContains(page, f'data-attachment="{photo.pk}"')
        self.assertNotContains(page, "data-att-delete")
        self.assertEqual(Attachment.objects.count(), 1)

    def test_the_supervisor_removes_a_photo_while_the_order_is_open(self):
        self.send()
        photo = Attachment.objects.get()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse("attachments:delete", args=[photo.pk])).status_code, 200)
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertTrue(AuditLog.objects.filter(action="delete_attachment", actor=self.owner).exists())


# ---------------------------------------------------------------------------------------------------------------
# The screen and its words
# ---------------------------------------------------------------------------------------------------------------


class ScreenTests(FloorPlant):
    def test_the_screen_is_one_card_per_order_with_big_buttons_and_no_table(self):
        self.more_materials()
        ready, running = self.open(), self.open()
        self.start(running)
        for lang, title, start, done in (("ar", "شغلي", "ابدأ", "خلصت"), ("en", "My work", "Start", "Done")):
            body = self.page(lang=lang).content.decode()
            with self.subTest(lang=lang):
                self.assertIn(f"<h1>{title}</h1>", body)
                self.assertEqual(body.count("data-floor-card="), 2)
                self.assertIn(f'<button class="op-primary fl-btn" type="submit">{start}</button>', body)
                self.assertIn(f'<button class="op-primary fl-btn" type="submit" form="fl-done-{running.pk}" data-floor-done-button>{done}</button>', body)
                self.assertIn("hesba/css/floor.css", body)
                self.assertNotIn("<table", body[body.index("data-floor"):])
                self.assertIn(f'dir="{"ltr" if lang == "en" else "rtl"}"', body)
        css = (pathlib.Path(settings.BASE_DIR) / "static/hesba/css/floor.css").read_text(encoding="utf-8")
        self.assertIn(".fl .fl-btn{width:100%;min-height:56px", css)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css))    # colours come from the tokens only
        card = self.page().content.decode().split(f'data-floor-card="{ready.pk}"')[1].split("</article>")[0]
        for text in ("قميص", "قص", "المرحلة 1 من 5 مراحل", ready.number, "لسه مبدأتش"):
            self.assertIn(text, card)

    def test_the_words_use_no_refused_word_and_no_arabic_line_starts_or_ends_with_a_latin_word_or_a_placeholder(self):
        from attachments import services as attachment_services
        from permissions.migrations import __path__ as migrations_path

        from . import workcentre_views

        root = pathlib.Path(settings.BASE_DIR)
        files = ["manufacturing/floor.py", "manufacturing/floor_views.py", "templates/manufacturing/floor.html", "templates/manufacturing/_floor_photos.html",
                 "static/hesba/css/floor.css", "permissions/migrations/0013_hg140_production_worker.py"]
        self.assertTrue((pathlib.Path(migrations_path[0]) / "0013_hg140_production_worker.py").exists())
        for name in files:
            text = (root / name).read_text(encoding="utf-8")
            for word in REFUSED_WORDS:
                self.assertNotIn(word, text, name)
        from importlib import import_module

        from settings_core import operational_views

        seed = import_module("permissions.migrations.0013_hg140_production_worker")
        groups = (("screen", floor_views.WORDS["ar"]), ("upload", floor_views.ATT_MESSAGES["ar"]), ("rules", floor.MESSAGES["ar"]),
                  ("work centre", {key: workcentre_views.WORDS["ar"][key] for key in ("workers", "workers_hint", "workers_none")}),
                  ("attachments", {key: text for key, text in attachment_services.MESSAGES["ar"].items() if key.startswith("floor_")}),
                  ("seed", {"role": seed.ROLE[1], "about": seed.ROLE[3], "permission": seed.PERMISSION[1], "roles screen": operational_views.ROLE_ABOUT["production_worker"][0]}))
        self.assertEqual(len(groups[4][1]), 5)
        for label, words in groups:
            for key, text in words.items():
                first, last = edges(text)
                with self.subTest(words=label, key=key):
                    for word in REFUSED_WORDS:
                        self.assertNotIn(word, text)
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)
                    self.assertFalse(first.startswith("{") or last.endswith("}"), text)   # a code or a number never opens or closes the line

    def test_the_rendered_pages_use_no_refused_word_and_no_arabic_line_starts_or_ends_with_a_latin_word(self):
        self.more_materials()
        self.open()                                                                  # ready
        running = self.open()
        self.start(running)
        out = self.at_finishing()
        self.sent(out)
        short = self.open(quantity="2000")
        self.client.force_login(self.worker)
        self.client.post(reverse("manufacturing:floor_photo", args=[running.pk]), {"file": upload(JPEG, "d.jpg", "image/jpeg")})
        pages = {"floor": self.client.get(self.url), "refused start": self.client.post(self.url, {"action": "start", "order": short.pk, "stage": "0"}),
                 "refused done": self.client.post(self.url, {"action": "done", "order": running.pk, "stage": "0", "good": "999"}),
                 "order with photos": self.client.get(reverse("manufacturing:order", args=[running.pk])),
                 "work centre form": None}
        self.client.force_login(self.owner)
        centre = workcentres.save_work_centre({"code": "CUT", "name_ar": "ترابيزة القص", "kind": "manual", "hours_per_day": "8"}, self.owner)
        save_employee({"name": "حسن القصّاص"}, self.owner)
        pages["work centre form"] = self.client.get(reverse("manufacturing:work_centre", args=[centre.pk]))
        pages["order with photos, supervisor"] = self.client.get(reverse("manufacturing:order", args=[running.pk]))
        for label, response in pages.items():
            html = response.content.decode()
            self.assertEqual(response.status_code, 200, label)
            for word in REFUSED_WORDS:
                self.assertNotIn(word, html, label)
            lines = sentences(html)
            self.assertTrue(lines, label)
            for text in lines:
                first, last = edges(text)
                with self.subTest(page=label, text=text[:50]):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)
        floor_lines = sentences(pages["floor"].content.decode())
        for text in ("شغلي", "ابدأ", "خلصت", "قميص", "السليم", "المعيب", "المرحلة دي عند المورّد «مصنع النور للتطريز»، والمشرف هو اللي بيسجّل رجوعها."):
            self.assertIn(text, floor_lines, text)
        # An empty floor has its own plain line.
        ProductionOrder.objects.all().update(status=OrderStatus.CANCELLED)
        self.client.force_login(self.worker)
        empty = self.client.get(self.url).content.decode()
        self.assertIn('<p class="tc-empty" data-floor-empty>مفيش مراحل عليك دلوقتي.</p>', empty)
        for word in REFUSED_WORDS:
            self.assertNotIn(word, empty)
