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

import html
import re
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock

from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from config.money import money_round
from hesba_testing.factories import make_location
from inventory.models import StockMovement, StockOperation
from inventory.services import (
    adjust_stock,
    cancel_stock_operation,
    get_item_authoritative_average_cost,
    get_item_location_stock_quantity,
    get_item_stock_value,
    transfer_stock,
)
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode

from . import guards, orders, services
from .models import OrderStatus, RunStatus
from .tests import person
from .tests_f11 import REFUSED_AR, BakeryChart, F11Setup

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)   # a Wednesday, 12:00 in Cairo
DAY = date(2026, 11, 11)

RUN_AR = ("الحركة دي تابعة لتشغيلة الإنتاج {number}، ومينفعش تتعكس من شاشة التحويلات والتسويات. "
          "ألغِ التشغيلة من صفحتها، والمنتج بيخرج من المخزن وكل خاماته بترجع مع بعض.")
RUN_EN = ("This operation belongs to production run {number} and cannot be reversed from the transfers and adjustments screen. "
          "Cancel the run from its own page: its product leaves stock and all of its materials come back, together.")
ORDER_AR = ("الحركة دي صرف خامات لأمر الإنتاج {number}، ومينفعش تتعكس من شاشة التحويلات والتسويات. "
            "ألغِ الأمر من صفحته، وكل خاماته بترجع المخزن مع بعض.")
ORDER_EN = ("This operation is a material issue of production order {number} and cannot be reversed from the transfers and adjustments screen. "
            "Cancel the order from its own page: all of its materials come back together.")

ERROR_BOX = re.compile(r'<div class="op-message op-message-error"[^>]*>(.*?)</div>', re.S)
OWNER_LINK = re.compile(r'<a href="([^"]+)" data-owner-link>([^<]+)</a>')
REVERSAL_FORM = re.compile(r'<form method="post" action="(/inventory/operations/\d+/reverse/)">')


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

    def errors(self, page):
        """The refusals the page shows, as the person reads them."""

        return [html.unescape(text) for text in ERROR_BOX.findall(page.content.decode())]

    def row(self, page, operation):
        return next(row for row in page.context["page"].object_list if row.pk == operation.pk)

    def reversal_forms(self, page):
        """The operations the page offers to reverse (the «عكس» form of each row), in order."""

        return sorted(REVERSAL_FORM.findall(page.content.decode()))

    def form_of(self, operation):
        return reverse("inventory:operation_cancel", args=[operation.pk])

    def of_run(self, run):
        """The run's operations: its product, then its materials in the order they were consumed."""

        return [run.output_operation] + [row.operation for row in run.consumptions.select_related("operation").order_by("pk")]

    def stock_of(self, item):
        """(units at the plant, stock value, average cost) of any item."""

        return (get_item_location_stock_quantity(item, self.plant), money_round(get_item_stock_value(item)), get_item_authoritative_average_cost(item))

    def everything(self):
        """All that a reversal would change: stock rows, cancelled operations, every item's position, the journal, the audit log."""

        Projector().rebuild()
        totals = JournalLine.objects.aggregate(d=Sum("debit"), c=Sum("credit"))
        return {"movements": StockMovement.objects.count(), "cancelled": StockOperation.objects.filter(status="cancelled").count(),
                "stock": [self.stock_of(item) for item in (self.cake, self.flour, self.sugar, self.box)],
                "entries": JournalEntry.objects.count(), "debit": totals["d"], "credit": totals["c"],
                "audit": AuditLog.objects.filter(action="cancel_stock_operation").count()}

    def untouched(self, *operations):
        """No operation was reversed and no stock row was written since ``self.rows`` was counted."""

        self.assertEqual(StockMovement.objects.count(), self.rows)
        self.assertEqual(list(StockOperation.objects.filter(pk__in=[operation.pk for operation in operations]).order_by("pk")
                              .values_list("status", flat=True)), ["posted"] * len(operations))


class TheGapTests(F11bSetup):
    """Written before the fix (and marked as expected failures until it): each of these went through."""

    def test_the_output_of_a_run_whose_cakes_were_sold_is_not_reversed_from_the_stock_screen(self):
        first, _ = self.two_runs()
        self.rows = StockMovement.objects.count()
        self.from_the_stock_screen(first.output_operation)
        # It went through: 20 out at 14.50 left 5 cakes worth 102.50, 20.50 each, and the run stayed «posted».
        self.assertEqual(self.position(), (D("25.000"), D("392.50"), D("15.7000")))
        first.refresh_from_db()
        self.assertEqual(first.status, RunStatus.POSTED)
        self.untouched(first.output_operation)

    def test_one_material_of_a_run_is_not_reversed_from_the_stock_screen(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        flour = run.consumptions.get(operation__item=self.flour).operation
        self.rows = StockMovement.objects.count()
        self.from_the_stock_screen(flour)
        # It went through: the 2 kg came back (200 kg) while the 10 cakes stayed at 9.50, a cost that holds that flour.
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("198.000"), D("19.000"), D("90.000")))
        self.assertEqual(self.position(), (D("10.000"), D("95.00"), D("9.5000")))
        self.untouched(flour)

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


class TheGapOnARestaurantChartTests(BakeryChart, TheGapTests):
    """A restaurant bakery with the manufacturing module on is protected as a factory is."""


# ---------------------------------------------------------------------------------------------------------------
# The refusal: what it says, where it points, and that a crafted request gets the same answer
# ---------------------------------------------------------------------------------------------------------------


class RefusalTests(F11bSetup):
    def test_a_runs_operation_is_refused_with_the_runs_number_in_both_languages(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        self.assertEqual(run.number, "MO-20261111-001")
        before = self.everything()
        for lang, text in (("ar", RUN_AR), ("en", RUN_EN)):
            for operation in self.of_run(run):                 # the cakes coming in, then each material going out
                with self.subTest(lang=lang, operation=operation.reference_number):
                    page = self.from_the_stock_screen(operation, lang)
                    self.assertEqual(self.errors(page), [text.format(number="MO-20261111-001")])
        self.assertEqual(self.everything(), before)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.POSTED)
        self.books(finished="95.00", raw="4755.00")

    def test_the_row_of_a_runs_operation_links_to_the_run_instead_of_offering_to_reverse_it(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        plain = adjust_stock("ADJ-F11B-1", DAY, self.flour, self.plant, "out", D("5"), "هالك", self.owner)
        self.client.force_login(self.owner)
        target = reverse("manufacturing:run", args=[run.pk])
        for lang, note, label in (("ar", "تابعة للتشغيلة", "افتح صفحة التشغيلة"), ("en", "Part of run", "Open the run")):
            with self.subTest(lang=lang):
                page = self.client.get(reverse("inventory:operations"), {"lang": lang})
                for operation in self.of_run(run):
                    self.assertEqual(self.row(page, operation).owner, guards.Owner("run", run.number, target))
                self.assertIsNone(self.row(page, plain).owner)
                # Four links to the run, one for each of its operations, and one «عكس» form: the ordinary adjustment's.
                self.assertEqual(OWNER_LINK.findall(page.content.decode()), [(f"{target}?lang={lang}", label)] * 4)
                self.assertEqual(self.reversal_forms(page), [self.form_of(plain)])
                self.assertContains(page, note, count=4)
                self.assertEqual(self.client.get(f"{target}?lang={lang}").status_code, 200)   # the link opens

    def test_an_orders_issue_is_refused_with_the_orders_number_and_its_row_links_to_the_order(self):
        order = self.started_order()
        self.assertEqual(order.number, "PO-20261111-001")
        issues = [issue.operation for issue in order.issues.select_related("operation").order_by("pk")]
        self.assertEqual(len(issues), 3)
        before = self.everything()
        target = reverse("manufacturing:order", args=[order.pk])
        for lang, text, label in (("ar", ORDER_AR, "افتح صفحة الأمر"), ("en", ORDER_EN, "Open the order")):
            with self.subTest(lang=lang):
                page = self.from_the_stock_screen(issues[0], lang)
                self.assertEqual(self.errors(page), [text.format(number="PO-20261111-001")])
                self.assertEqual([self.row(page, operation).owner for operation in issues], [guards.Owner("order", order.number, target)] * 3)
                self.assertEqual(OWNER_LINK.findall(page.content.decode()), [(f"{target}?lang={lang}", label)] * 3)
                self.assertEqual(self.reversal_forms(page), [])
                self.assertEqual(self.client.get(f"{target}?lang={lang}").status_code, 200)
        self.assertEqual(self.everything(), before)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.IN_PROGRESS)
        self.books(finished="0.00", raw="4660.00", wip="190.00")

    def test_once_the_order_is_finished_its_materials_belong_to_its_run(self):
        run = self.make()                                     # an order for 20 cakes, finished: its issues are the run's materials
        issue = run.order.issues.select_related("operation").order_by("pk").first().operation
        self.assertEqual(guards.owners([issue.pk]), {issue.pk: guards.Owner("run", run.number, reverse("manufacturing:run", args=[run.pk]))})
        self.assertEqual(self.errors(self.from_the_stock_screen(issue)), [RUN_AR.format(number=run.number)])
        self.assertEqual(self.errors(self.from_the_stock_screen(run.output_operation, "en")), [RUN_EN.format(number=run.number)])

    def test_a_crafted_request_gets_the_same_answer_whoever_sends_it_and_whatever_it_says(self):
        first, _ = self.two_runs()
        keeper = person(RoleCode.STOCK_KEEPER, "f11b_keeper")
        before = self.everything()
        url = self.form_of(first.output_operation)
        expected = [RUN_AR.format(number=first.number)]
        for user in (self.owner, keeper):
            self.client.force_login(user)
            for label, data in (("as the form sends it", {"reversal_date": DAY.isoformat(), "reason": "طلب مكتوب باليد"}),
                                ("no reason and no date", {}),
                                ("a page of the list that does not hold it", {"reversal_date": DAY.isoformat(), "reason": "x", "page": "9"})):
                with self.subTest(user=user.username, request=label):
                    self.assertEqual(self.errors(self.client.post(url, data, follow=True)), expected)
        self.assertRedirects(self.client.get(url), reverse("inventory:operations"))   # only a POST acts at all
        self.assertEqual(self.everything(), before)
        self.assertEqual(self.position(), (D("25.000"), D("392.50"), D("15.7000")))
        first.refresh_from_db()
        self.assertEqual(first.status, RunStatus.POSTED)
        self.books(finished="392.50", raw="4470.00", absorbed="230.00", cogs="217.50")

    def test_an_operation_of_a_cancelled_run_is_answered_the_same_way(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        services.cancel_run(run, self.owner, reason="غلطة في التسجيل")
        before = self.everything()
        self.assertEqual(self.errors(self.from_the_stock_screen(run.output_operation)), [RUN_AR.format(number=run.number)])
        self.assertEqual(self.everything(), before)


# ---------------------------------------------------------------------------------------------------------------
# A second person's approval (FB-27): «عكس تسوية أو تحويل مخزون»
# ---------------------------------------------------------------------------------------------------------------


class ApprovalPathTests(F11bSetup):
    ACTION = "inventory.reverse_operation"

    def setUp(self):
        super().setUp()
        from approvals.services import set_held_actions
        from approvals.tests import clerk_with

        self.manager = person(RoleCode.MANAGER, "f11b_manager")
        self.clerk = clerk_with("inventory.adjust_stock", "inventory.view_stock", username="f11b_clerk")
        self.run_ = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        set_held_actions([self.ACTION], self.owner)

    def test_a_runs_operation_is_refused_at_once_and_no_request_is_sent(self):
        from approvals.models import ApprovalRequest

        before = self.everything()
        page = self.from_the_stock_screen(self.run_.output_operation, user=self.clerk)
        self.assertEqual(self.errors(page), [RUN_AR.format(number=self.run_.number)])
        self.assertFalse(ApprovalRequest.objects.exists())
        self.assertEqual(self.everything(), before)

    def test_a_request_that_was_already_waiting_fails_with_the_reason_and_nothing_moves(self):
        from approvals import services as approvals
        from approvals.models import ApprovalRequest

        # As one sent from the stock screen before this change would be.
        operation = self.run_.output_operation
        request = ApprovalRequest.objects.create(action=self.ACTION, object_id=operation.pk, object_label=operation.reference_number,
                                                 data={"reversal_date": DAY.isoformat(), "reason": "قبل التعديل"}, requested_by=self.clerk)
        before = self.everything()
        request = approvals.approve(request.pk, self.manager)
        self.assertEqual((request.status, request.note), ("failed", RUN_EN.format(number=self.run_.number)))
        self.assertEqual(self.everything(), before)
        self.run_.refresh_from_db()
        self.assertEqual((self.run_.status, self.position()), (RunStatus.POSTED, (D("10.000"), D("95.00"), D("9.5000"))))
        self.client.force_login(self.clerk)                   # and the requester reads why, in Arabic
        self.assertContains(self.client.get("/approvals/"), RUN_AR.format(number=self.run_.number))

    def test_an_ordinary_adjustment_is_still_held_and_reversed_on_approval(self):
        from approvals import services as approvals
        from approvals.models import ApprovalRequest

        plain = adjust_stock("ADJ-F11B-2", DAY, self.flour, self.plant, "out", D("5"), "هالك", self.owner)
        self.assertEqual(self.stock_of(self.flour), (D("193.000"), D("3860.00"), D("20.0000")))
        page = self.from_the_stock_screen(plain, user=self.clerk)
        self.assertContains(page, "محتاجة موافقة")
        self.assertEqual(self.stock_of(self.flour), (D("193.000"), D("3860.00"), D("20.0000")))
        request = approvals.approve(ApprovalRequest.objects.get().pk, self.manager)
        self.assertEqual((request.status, request.object_id), ("done", plain.pk))
        self.assertEqual(self.stock_of(self.flour), (D("198.000"), D("3960.00"), D("20.0000")))


# ---------------------------------------------------------------------------------------------------------------
# What does not change: the run's and the order's own cancellation, and every other operation
# ---------------------------------------------------------------------------------------------------------------


class OwnCancellationTests(F11bSetup):
    """A run's and an order's own pages call the engine directly: they cancel exactly as before."""

    def from_its_page(self, name, document, **data):
        self.client.force_login(self.owner)
        return self.client.post(reverse(name, args=[document.pk]), {"lang": "ar", **data}, follow=True)

    def test_the_run_page_cancels_a_run_whole(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        self.assertEqual(self.position(), (D("10.000"), D("95.00"), D("9.5000")))
        self.assertEqual([self.stock_of(item) for item in (self.flour, self.sugar, self.box)],
                         [(D("198.000"), D("3960.00"), D("20.0000")), (D("19.000"), D("570.00"), D("30.0000")), (D("90.000"), D("225.00"), D("2.5000"))])
        page = self.from_its_page("manufacturing:run", run, reason="خبزة باظت")
        self.assertContains(page, "اتلغت التشغيلة ورجعت الخامات للمخزن.")
        run.refresh_from_db()
        self.assertEqual((run.status, run.cancellation_reason), (RunStatus.CANCELLED, "خبزة باظت"))
        self.assertEqual(self.position(), (D("0.000"), D("0.00"), D("0")))
        self.assertEqual([self.stock_of(item) for item in (self.flour, self.sugar, self.box)],
                         [(D("200.000"), D("4000.00"), D("20.0000")), (D("20.000"), D("600.00"), D("30.0000")), (D("100.000"), D("250.00"), D("2.5000"))])
        self.assertEqual([operation.status for operation in StockOperation.objects.filter(pk__in=[o.pk for o in self.of_run(run)]).order_by("pk")], ["cancelled"] * 4)
        self.assertEqual(StockMovement.objects.filter(reversal_of__isnull=False).count(), 4)   # the cakes and three materials
        self.books(finished="0.00", raw="4850.00")

    def test_the_run_page_still_applies_its_own_rule(self):
        first, second = self.two_runs()
        page = self.from_its_page("manufacturing:run", first, reason="غلطة")      # HG-100: its cakes were sold
        self.assertEqual(page.context["error"], REFUSED_AR)
        self.assertEqual(self.position(), (D("25.000"), D("392.50"), D("15.7000")))
        self.from_its_page("manufacturing:run", second, reason="غلطة")           # nothing has left since run 2
        second.refresh_from_db()
        self.assertEqual(second.status, RunStatus.CANCELLED)
        self.assertEqual(self.position(), (D("5.000"), D("72.50"), D("14.5000")))
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("196.000"), D("18.000"), D("80.000")))
        self.books(finished="72.50", raw="4660.00", absorbed="100.00", cogs="217.50")

    def test_the_order_page_cancels_an_order_on_the_floor_and_every_material_comes_back(self):
        order = self.started_order()
        self.assertEqual([self.stock_of(item) for item in (self.flour, self.sugar, self.box)],
                         [(D("196.000"), D("3920.00"), D("20.0000")), (D("18.000"), D("540.00"), D("30.0000")), (D("80.000"), D("200.00"), D("2.5000"))])
        self.books(finished="0.00", raw="4660.00", wip="190.00")
        self.from_its_page("manufacturing:order", order, action="cancel", reason="العميل لغى الطلب")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual([self.stock_of(item) for item in (self.flour, self.sugar, self.box)],
                         [(D("200.000"), D("4000.00"), D("20.0000")), (D("20.000"), D("600.00"), D("30.0000")), (D("100.000"), D("250.00"), D("2.5000"))])
        self.assertEqual(list(order.issues.order_by("pk").values_list("operation__status", flat=True)), ["cancelled"] * 3)
        self.assertEqual(self.position(), (D("0"), D("0.00"), D("0")))
        self.books(finished="0.00", raw="4850.00")

    def test_an_order_refused_at_the_stock_screen_is_finished_as_if_nothing_was_asked(self):
        order = self.started_order()
        self.from_the_stock_screen(order.issues.order_by("pk").first().operation)
        for _ in order.stages[order.stage_index:]:
            orders.advance(order, self.owner)
            order.refresh_from_db()
        orders.finish(order, self.owner)
        self.assertEqual(self.position(), (D("20.000"), D("290.00"), D("14.5000")))   # the HG-036 figures
        self.books(finished="290.00", raw="4660.00", absorbed="100.00")


class OwnCancellationOnARestaurantChartTests(BakeryChart, OwnCancellationTests):
    pass


class OrdinaryOperationTests(F11bSetup):
    """An adjustment or a transfer that no run and no order made is reversed from the screen as before."""

    def test_an_ordinary_adjustment_is_reversed_from_the_screen(self):
        services.produce(self.recipe, self.owner, batches="1", location=self.plant)   # a run beside it changes nothing
        plain = adjust_stock("ADJ-F11B-3", DAY, self.flour, self.plant, "out", D("5"), "هالك", self.owner)
        self.assertEqual(guards.owners([plain.pk]), {})
        self.assertEqual(self.stock_of(self.flour), (D("193.000"), D("3860.00"), D("20.0000")))
        page = self.from_the_stock_screen(plain)
        self.assertEqual(self.errors(page), [])
        self.assertContains(page, "تم عكس حركة المخزون.")
        plain.refresh_from_db()
        self.assertEqual((plain.status, plain.reversal_date, plain.cancelled_by), ("cancelled", DAY, self.owner))
        self.assertEqual(self.stock_of(self.flour), (D("198.000"), D("3960.00"), D("20.0000")))
        self.books(finished="95.00", raw="4755.00")

    def test_an_ordinary_transfer_is_reversed_from_the_screen(self):
        shop = make_location(location_code="SHOP", name_ar="المعرض", name_en="Shop")
        moved = transfer_stock("TR-F11B-1", DAY, self.flour, self.plant, shop, D("30"), self.owner, "للمعرض")
        self.assertEqual(guards.owners([moved.pk]), {})
        self.assertEqual((self.stock_of(self.flour), get_item_location_stock_quantity(self.flour, shop)),
                         ((D("170.000"), D("4000.00"), D("20.0000")), D("30.000")))
        self.client.force_login(self.owner)
        listed = self.client.get(reverse("inventory:operations"))
        self.assertEqual((self.row(listed, moved).owner, self.reversal_forms(listed)), (None, [self.form_of(moved)]))
        self.assertEqual(self.errors(self.from_the_stock_screen(moved)), [])
        moved.refresh_from_db()
        self.assertEqual(moved.status, "cancelled")
        self.assertEqual((self.stock_of(self.flour), get_item_location_stock_quantity(self.flour, shop)),
                         ((D("200.000"), D("4000.00"), D("20.0000")), D("0")))

    def test_the_engines_own_refusals_are_still_said(self):
        plain = adjust_stock("ADJ-F11B-4", DAY, self.flour, self.plant, "out", D("5"), "هالك", self.owner)
        self.from_the_stock_screen(plain)
        self.assertEqual(self.errors(self.from_the_stock_screen(plain)), ["حركة المخزون دي اتعكست قبل كده."])


class EntityScopeTests(F11bSetup):
    """Another entity's operation is still «not found», before anything is said about what it belongs to."""

    def test_a_runs_operation_of_another_entity_is_a_404_not_the_new_message(self):
        from entities.services import save_entity

        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)    # the main entity's plant
        branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        make_location(location_code="BR-PLANT", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        before = self.everything()
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        for operation in self.of_run(run):
            with self.subTest(operation=operation.reference_number):
                response = self.client.post(self.form_of(operation), {"reversal_date": DAY.isoformat(), "reason": "من الفرع"})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(reverse("manufacturing:run", args=[run.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("inventory:operations")).context["page"].paginator.count, 0)
        self.client.post(reverse("entities:switch"), {"entity": "", "next": "/dashboard/"})   # back to the whole group
        self.assertEqual(self.errors(self.from_the_stock_screen(run.output_operation)), [RUN_AR.format(number=run.number)])
        self.assertEqual(self.everything(), before)


class ModuleSwitchedOffTests(BakeryChart, F11bSetup):
    """The rule holds where the owning page can open (HG-102, question 1). A factory cannot switch the module off; a
    restaurant bakery can, and a restaurant's kitchen runs with it off (``restaurant.tests_kitchen``)."""

    def test_a_factory_cannot_switch_manufacturing_off(self):
        from settings_core.models import ClientProfile
        from settings_core.setup_services import ModuleChangeRefused, complete_setup, set_module_enabled

        factory = ClientProfile.get_active()
        complete_setup(factory, "manufacturing", "food", F11Setup.MODULES)
        with self.assertRaises(ModuleChangeRefused):
            set_module_enabled(factory, "manufacturing", False, self.owner)
        self.assertTrue(guards.pages_open())

    def test_with_the_manufacturing_module_off_the_screen_works_as_before(self):
        from settings_core.setup_services import set_module_enabled

        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        mine = sorted(operation.pk for operation in self.of_run(run))
        self.assertTrue(guards.pages_open())
        self.assertEqual(sorted(guards.guarded(mine)), mine)
        set_module_enabled(self.profile, "manufacturing", False, self.owner)
        self.assertFalse(guards.pages_open())
        self.assertEqual(guards.guarded(mine), {})
        self.assertEqual(sorted(guards.owners(mine)), mine)            # still the run's; only the screen's rule is off
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("manufacturing:run", args=[run.pk])).status_code, 403)   # the run's page is closed
        listed = self.client.get(reverse("inventory:operations"))
        self.assertEqual(self.reversal_forms(listed), sorted(self.form_of(operation) for operation in self.of_run(run)))
        self.assertEqual(OWNER_LINK.findall(listed.content.decode()), [])
        self.assertEqual(self.errors(self.from_the_stock_screen(run.output_operation)), [])
        self.assertEqual(self.position(), (D("0.000"), D("0.00"), D("0")))
        set_module_enabled(self.profile, "manufacturing", True, self.owner)   # back on: the rest of the run is the run's again
        flour = run.consumptions.get(operation__item=self.flour).operation
        self.assertEqual(self.errors(self.from_the_stock_screen(flour)), [RUN_AR.format(number=run.number)])
        self.assertEqual(self.on_hand(self.flour, self.sugar, self.box), (D("198.000"), D("19.000"), D("90.000")))
