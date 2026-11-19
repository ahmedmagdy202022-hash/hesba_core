"""Manufacturing F14: the factory demos show every merged manufacturing feature with realistic data, and the «جرّب» card walks it.

The demo is built the way a visitor's copy is (``build``: the demo setup with its capabilities and modules, the demo users, the sample
month), once for each class. Every figure below is stated in full; each is what the screen reads from the service that made it (the
demo only calls services). The clock is the real one: every date is counted from ``timezone.localdate()``.

Not shown, and why: the garments' size grid (F13) is not merged; the chemicals and printing factories keep their catalogues and the
production orders they always had (no template of their own).
"""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config import demo as showcase
from config import demo_first_run as first_run
from master_data.models import Item

from . import demo, floor, maintenance, make_to_order, mrp, orders, quality, schedule, variance
from .models import (
    CostSheet, MaintenanceJob, MaintenancePart, OrderCostLine, OrderMaterialIssue, OrderStatus, OrderStep, OutsideJob, ProductionOrder, ProductionRun, QualityCheck,
    Recipe, RecipeOutput, RunOutput, WorkCentre,
)

BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك", "شغلانة")
LATIN_EDGE = re.compile(r"^[A-Za-z]|[A-Za-z][.!؟?]?$|[{}%]")
ARABIC = re.compile(r"[ء-ي]")


def build(sub="food"):
    """The demo a visitor gets: the demo setup (its capabilities and modules), the demo users, then the sample month, as the template builder does."""

    from config.demo_templates import complete_demo_setup, seed_sample_business

    call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
    complete_demo_setup("manufacturing", sub)
    call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
    assert seed_sample_business()


def books_balance(test):
    from ledger.reports import reconciliation
    from reports import selectors

    result = reconciliation()
    test.assertTrue(result["balanced"] and result["ok"], result["rows"])
    alerts = selectors.stock_alert_counts()
    test.assertEqual((alerts["out_of_stock"], alerts["low_stock"]), (1, 1))  # the demo's planted alerts are as they were


def order_noted(note):
    return ProductionOrder.objects.get(notes=note)


class FactoryDemo:
    """What every factory demo's tests share; the classes below build the demo once each (``sub``)."""

    sub = "food"

    @classmethod
    def setUpTestData(cls):
        build(cls.sub)
        cls.today = timezone.localdate()
        cls.owner = get_user_model().objects.get(username="owner")
        cls.worker = get_user_model().objects.get(username="worker")

    def card(self, lang):
        from settings_core.models import ClientProfile

        request = RequestFactory().get("/dashboard/")
        request.user = self.owner
        return first_run.tour_card(request, lang, ClientProfile.get_active())

    # ---- the tour, for both factories ----------------------------------------------------------------------------------------

    def every_step_opens(self, expected):
        self.client.force_login(self.owner)
        steps = showcase.demo_tour("manufacturing", self.sub)
        self.assertEqual(len(steps), expected)
        for _ar, _en, _hint_ar, _hint_en, url_name in steps:
            path = first_run.step_path(url_name)
            self.assertIsNotNone(path, url_name)
            for lang in ("ar", "en"):
                with self.subTest(step=url_name, lang=lang):
                    self.assertEqual(self.client.get(first_run.step_url(path, lang)).status_code, 200)

    def test_the_demo_has_a_production_worker_login_that_lands_on_the_floor_screen(self):
        self.assertEqual(self.worker.hesba_profile.role.code, "production_worker")
        self.assertEqual(self.worker.hesba_profile.display_name, "أ. خالد عبد الغني")
        self.client.force_login(self.worker)
        self.assertRedirects(self.client.get(reverse("after_login")), reverse("manufacturing:floor"), fetch_redirect_response=False)
        for lang in ("ar", "en"):
            self.assertEqual(self.client.get(reverse("manufacturing:floor"), {"lang": lang}).status_code, 200)

    def test_the_books_balance_and_the_planted_alerts_are_unchanged(self):
        books_balance(self)

    def test_the_demo_is_safe_to_run_twice(self):
        from reports.management.commands.seed_demo_business import Command as Seed

        from config.demo_templates import seed_sample_business

        before = (ProductionOrder.objects.count(), WorkCentre.objects.count(), MaintenanceJob.objects.count(), CostSheet.objects.count(), Recipe.objects.count())
        master = Seed()._seed_master_data()
        staff = None
        demo.seed_routing(master, master["catalog"], self.owner)
        demo.seed_features(master, master["catalog"], self.owner, staff)
        self.assertEqual(before, (ProductionOrder.objects.count(), WorkCentre.objects.count(), MaintenanceJob.objects.count(), CostSheet.objects.count(), Recipe.objects.count()))
        self.assertFalse(seed_sample_business())  # a shop with posted sales is left alone

    @override_settings(DEMO_MODE=True)
    def test_the_dashboard_shows_the_steps(self):
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            page = self.client.get(f"/dashboard/?lang={lang}")
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.content.decode().count("data-demo-tour-step="), self.steps)


class FoodDemo(FactoryDemo, TestCase):
    sub = "food"
    steps = 10

    # ---- the setup the demo switches on ------------------------------------------------------------------------------------------

    def test_quotations_and_sales_orders_are_on_for_the_factory_demo(self):
        from settings_core.capabilities import capability_enabled

        self.assertTrue(capability_enabled("quotations"))

    # ---- F3: lots from runs ---------------------------------------------------------------------------------------------------

    def test_every_lot_of_a_product_comes_from_a_production_run(self):
        from batches.models import Batch

        products = Item.objects.filter(item_code__startswith="DEMO-ITEM-")
        lots = Batch.objects.filter(item__in=products)
        self.assertGreater(lots.count(), 5)
        self.assertFalse(lots.filter(production_run__isnull=True).exists())  # none registered by hand
        self.assertFalse(Batch.objects.filter(batch_no__startswith="LOT-").exists())

    def test_a_finished_orders_lot_belongs_to_its_run_with_the_recipes_shelf_life(self):
        from batches.models import Batch

        order = order_noted("معمول للعيد")
        lot = Batch.objects.get(production_run=order.run)
        self.assertEqual((lot.item.item_name, lot.quantity, lot.batch_no), ("معمول بالتمر (كرتونة)", D("12.000"), order.run.number))
        self.assertEqual(lot.expiry_date, order.run.run_date + timedelta(days=90))  # ninety days, the recipe's shelf life
        self.assertEqual(lot.received_on, order.run.run_date)

    # ---- F1: a production order from a confirmed sales order ----------------------------------------------------------------------

    def test_a_confirmed_sales_order_made_a_production_order_and_its_second_line_is_still_to_make(self):
        made = ProductionOrder.objects.get(sales_order_line__isnull=False)
        sales_order = made.sales_order_line.order
        self.assertEqual((sales_order.status, sales_order.deliver_by, made.due_date), ("confirmed", self.today, self.today))
        first, second = list(sales_order.lines.order_by("pk"))
        self.assertEqual((first.quantity, made.quantity, make_to_order.uncovered(first)), (D("30.000"), D("30.000"), D("0")))
        self.assertEqual((second.quantity, make_to_order.uncovered(second)), (D("12.000"), D("12.000")))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("orders:detail", args=[sales_order.pk]), {"lang": "ar"})
        self.assertContains(page, make_to_order.WORDS["ar"]["make"])  # the button of the line still to make
        self.assertContains(page, made.number)

    # ---- F2: the requirements and the draft purchase order -------------------------------------------------------------------------

    def test_three_materials_are_short_and_a_draft_purchase_order_asks_for_them(self):
        from orders.models import PurchaseOrder

        short = {row["item"].item_name: row["to_buy"] for row in mrp.calculate()["materials"] if row["to_buy"] > 0}
        self.assertEqual(set(short), {"دقيق", "سكر", "زبدة"})
        order = PurchaseOrder.objects.get()
        self.assertEqual(order.status, "draft")
        self.assertEqual({line.item.item_name: line.quantity for line in order.lines.all()}, {"دقيق": D("48.000"), "سكر": D("24.000"), "زبدة": D("7.500")})
        for row in mrp.calculate()["materials"]:
            if row["to_buy"] > 0:
                self.assertEqual([draft["number"] for draft in row["drafts"]], [order.number])  # noted beside the row, so nobody orders it twice

    # ---- F6a, F6b: work centres, routing and the schedule ------------------------------------------------------------------------------

    def test_three_work_centres_with_routed_stages_and_a_schedule_over_several_days(self):
        self.assertEqual(list(WorkCentre.objects.values_list("code", "kind", "rate_per_hour")),
                         [("DEMO-MIX", "machine", D("80.00")), ("DEMO-OVEN", "machine", D("150.00")), ("DEMO-PACK", "line", D("70.00"))])
        for order in ProductionOrder.objects.filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)):
            self.assertTrue(all(stage[3] for stage in order.stages), order.number)  # every stage is on a work centre, the sample's own three included
            pending = OrderStep.objects.filter(order=order, finished_at__isnull=True)
            self.assertEqual(pending.exists(), order.stage_index < len(order.stages), order.number)  # an order with every stage closed waits to be finished
            self.assertFalse(pending.filter(planned_start__isnull=True).exists(), order.number)
        days = {timezone.localtime(step.planned_start).date() for step in OrderStep.objects.filter(finished_at__isnull=True, planned_start__isnull=False)}
        self.assertGreaterEqual(len(days), 3)
        self.assertEqual(min(days), self.today)

    def test_one_order_is_late_one_is_due_today_and_the_rest_are_later(self):
        open_orders = ProductionOrder.objects.filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS))
        late = open_orders.get(due_date__lt=self.today)
        self.assertEqual((late.notes, late.due_date, late.status), (demo.LATE_NOTE, self.today - timedelta(days=3), OrderStatus.PLANNED))
        self.assertEqual(schedule.late_orders([late], self.today), {late.pk: "overdue"})
        self.assertEqual(open_orders.filter(due_date=self.today).count(), 1)
        self.assertGreaterEqual(open_orders.filter(due_date__gt=self.today).count(), 5)

    # ---- F12: a finished order with extra issue, a return and the real labour ---------------------------------------------------------

    def test_the_cost_card_of_the_eid_order_shows_actual_against_standard(self):
        order = order_noted("معمول للعيد")
        issues = [(row.kind, row.operation.item.item_name, row.operation.quantity) for row in order.issues.select_related("operation__item").order_by("pk")]
        self.assertEqual(issues, [("issue", "دقيق", D("36.000")), ("issue", "سكر", D("12.000")), ("issue", "زبدة", D("4.800")), ("extra", "دقيق", D("3.000")),
                                  ("return", "زبدة", D("0.500"))])
        self.assertEqual(list(order.cost_lines.order_by("pk").values_list("kind", "hours", "rate", "amount")),
                         [("labour", D("6.00"), D("45.00"), D("270.00")), ("overhead", None, None, D("120.00"))])
        card = orders.cost_card(order)
        self.assertEqual((card["material"], card["labor"], card["overhead"], card["per_unit"]), (D("1992.00"), D("270.00"), D("120.00"), D("198.5000")))
        result = variance.order_variance(order)
        self.assertEqual((result["planned_labour"], result["labour"], result["diff_labour"]), (D("200.00"), D("270.00"), D("70.00")))
        self.assertEqual((result["planned_overhead"], result["overhead"], result["diff_overhead"]), (D("150.00"), D("120.00"), D("-30.00")))
        self.assertEqual([(row["item"].item_name, row["diff_qty"], row["diff_value"]) for row in result["materials"]],
                         [("دقيق", D("3.000"), D("66.00")), ("سكر", D("0.000"), D("0.00")), ("زبدة", D("-0.500"), D("-90.00"))])

    # ---- F5: a lot on quality hold, one released with a small reject ------------------------------------------------------------------

    def test_one_lot_waits_on_hold_and_another_was_released_with_two_rejected(self):
        check = QualityCheck.objects.get()
        self.assertEqual((check.result, check.quantity, check.passed_quantity, check.rejected_quantity), ("partly", D("20.000"), D("18.000"), D("2.000")))
        self.assertEqual(check.order.notes, "دفعة الصباح")
        self.assertEqual(check.results.count(), 2)  # both criteria were answered
        self.assertTrue(check.release_operation_id and check.reject_operation_id)
        waiting = quality.waiting_rows()
        self.assertEqual(len(waiting), 1)
        self.assertEqual((waiting[0]["subject"].order.notes, waiting[0]["position"]["waiting"], waiting[0]["subject"].place.location_code), ("دفعة بانتظار الفحص", D("15.000"), "QC-1"))
        self.assertTrue(quality.is_hold_place(waiting[0]["subject"].place))

    def test_no_plain_run_of_the_checked_product_was_made_after_its_release_was_switched_on(self):
        from audit.models import AuditLog

        recipe = Recipe.objects.get(needs_release=True)
        switched = AuditLog.objects.get(action="save_recipe_quality", object_id=str(recipe.pk))
        plain = ProductionRun.objects.filter(recipe=recipe, order__isnull=True)
        self.assertTrue(plain.exists())  # the sample month's own runs
        self.assertFalse(plain.filter(created_at__gt=switched.created_at).exists())

    def test_the_next_days_top_up_makes_the_checked_product_by_an_order_and_releases_it_with_a_check(self):
        """The daily top-up used to make every product with a plain run, which put the checked product on the shelf without its
        check (the recipe page refuses exactly that). It is now an order, finished on hold and released in full."""

        from unittest import mock

        from config.demo_templates import top_up_current_db
        from inventory.services import get_item_location_stock_quantity

        recipe = Recipe.objects.get(needs_release=True)
        shelf = ProductionOrder.objects.filter(recipe=recipe).first().location
        hold = quality.hold_place(shelf.entity_id)
        plain = ProductionRun.objects.filter(recipe=recipe, order__isnull=True).count()
        before = (get_item_location_stock_quantity(recipe.product, shelf), get_item_location_stock_quantity(recipe.product, hold))
        self.assertEqual(before[1], D("15.000"))
        tomorrow = self.today + timedelta(days=1)  # resolved before patching, or the replacement calls itself
        with mock.patch("django.utils.timezone.localdate", lambda *a, **k: tomorrow):
            top_up_current_db()
            count = ProductionOrder.objects.count()
            top_up_current_db()  # the same day again adds nothing
            self.assertEqual(ProductionOrder.objects.count(), count)
        self.assertEqual(ProductionRun.objects.filter(recipe=recipe, order__isnull=True).count(), plain)  # no plain run of it
        order = ProductionOrder.objects.get(notes=demo.DAILY_NOTE)
        self.assertEqual((order.recipe, order.status, order.due_date, order.labor_cost, order.overhead_cost), (recipe, OrderStatus.DONE, tomorrow, D("0"), D("0")))
        self.assertGreater(order.quantity, 0)
        self.assertEqual(order.stage_logs.count(), len(order.stages))
        self.assertEqual(order.run.output_operation.destination_location, hold)  # finished on hold, not on the shelf
        self.assertEqual((order.run.output_quantity, order.run.conversion_cost, order.run.scrap_quantity), (order.quantity, D("0.00"), D("0.000")))
        check = QualityCheck.objects.get(order=order)
        self.assertEqual((check.result, check.quantity, check.passed_quantity, check.rejected_quantity), ("passed", order.quantity, order.quantity, D("0.000")))
        self.assertEqual(check.results.count(), 2)
        self.assertTrue(check.release_operation_id)
        self.assertIsNone(check.reject_operation_id)
        # the hold place holds only the planted lot again, and the day bought back and made what it sold
        self.assertEqual([row["subject"].order.notes for row in quality.waiting_rows()], ["دفعة بانتظار الفحص"])
        self.assertEqual((get_item_location_stock_quantity(recipe.product, shelf), get_item_location_stock_quantity(recipe.product, hold)), before)
        books_balance(self)

    # ---- F4: a stage out at an outside centre, one back with its bill --------------------------------------------------------------

    def test_one_stage_is_out_and_one_is_back_with_a_posted_bill(self):
        out = OutsideJob.objects.get(status="sent")
        self.assertEqual((out.supplier.name, out.quantity_sent, out.unit_price, out.order.notes), ("مركز تعبئة النيل", D("18.000"), D("12.00"), "تعبئة هدايا بالخارج"))
        back = OutsideJob.objects.get(status="billed")
        self.assertEqual((back.returned_good, back.returned_defective, back.order.stage_index, len(back.order.stages)), (D("7.000"), D("1.000"), 3, 3))
        self.assertEqual((back.purchase_invoice.status, back.purchase_invoice.total_amount, back.purchase_invoice.supplier), ("posted", D("126.00"), out.supplier))
        self.assertEqual(floor.waiting_reason(out.order)[0], "outside")

    # ---- F9: a by-product -------------------------------------------------------------------------------------------------------------

    def test_the_cake_recipe_has_a_by_product_and_a_finished_run_made_it(self):
        recipe = Recipe.objects.get(product__item_name="كيك فانيليا (كرتونة)")
        output = RecipeOutput.objects.get(recipe=recipe)
        self.assertEqual((output.item.item_name, output.kind, output.quantity, output.value_per_unit), ("فتات كيك للتحلية", "by_product", D("0.400"), D("30.0000")))
        run = order_noted("كيك الصباح").run
        made = RunOutput.objects.get(run=run)
        self.assertEqual((made.item, made.quantity, made.cost), (output.item, D("4.000"), D("120.00")))  # ten cartons: four kilos at thirty
        from inventory.services import get_item_location_stock_quantity

        self.assertEqual(get_item_location_stock_quantity(made.item, run.location), D("4.000"))

    # ---- F10: maintenance ---------------------------------------------------------------------------------------------------------------

    def test_the_oven_plan_is_due_and_a_closed_breakdown_gave_the_machine_a_cost(self):
        oven = WorkCentre.objects.get(code="DEMO-OVEN")
        states = {state["plan"].title: state["due"] for state in maintenance.plan_states()}
        self.assertEqual(states, {"تنظيف الفرن وفحص السخانات": True, "تزييت سير التعبئة": False})
        job = MaintenanceJob.objects.get(work_centre=oven)
        self.assertEqual((job.kind, job.downtime_minutes, job.closed_at is not None), ("breakdown", 330, True))
        self.assertEqual(MaintenancePart.objects.get(job=job).operation.quantity, D("1.000"))
        bill = job.purchases.get()
        self.assertEqual((bill.purchase_invoice.status, bill.purchase_invoice.total_amount, bill.purchase_invoice.supplier.name), ("posted", D("500.00"), "مركز الصيانة الصناعية"))
        month = maintenance.history(oven)[0]
        self.assertEqual((month["parts"], month["bills"], month["total"], month["minutes"]), (D("350.00"), D("500.00"), D("850.00"), 330))

    def test_the_mixer_is_down_now_and_the_schedule_has_no_capacity_for_it_today(self):
        mixer = WorkCentre.objects.get(code="DEMO-MIX")
        job = maintenance.open_job_of(mixer)
        self.assertEqual((job.kind, job.cause, job.closed_at), ("breakdown", "سير الخلاط اتقطع", None))
        today = schedule.grid(self.today, 7)["day_list"][0]
        lane = next(line for line in today["lines"] if line["centre"] == mixer)
        self.assertEqual((lane["row"]["capacity"], lane["row"]["over"], lane["row"]["down"]), (D("0"), True, True))

    # ---- F8: the production worker's «شغلي» ----------------------------------------------------------------------------------------------

    def test_the_worker_is_ticked_on_the_oven_and_sees_two_cards_one_ready_and_one_running(self):
        oven = WorkCentre.objects.get(code="DEMO-OVEN")
        self.assertEqual([employee.user for employee in oven.workers.all()], [self.worker])
        cards = floor.cards(self.worker)
        self.assertEqual(sorted(card["state"] for card in cards), ["ready", "running"])
        self.assertEqual({card["centre"] for card in cards}, {"فرن الخَبز"})
        running = next(card for card in cards if card["state"] == "running")
        self.assertEqual(running["order"].sales_order_line_id is not None, True)  # the sales order's own order, started by the worker
        self.client.force_login(self.worker)
        page = self.client.get(reverse("manufacturing:floor"), {"lang": "ar"})
        for card in cards:
            self.assertContains(page, card["number"])
        for other in ProductionOrder.objects.filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)).exclude(pk__in=[card["pk"] for card in cards]):
            self.assertNotContains(page, other.number)  # the stages of the other centres are not this worker's

    def test_the_owner_sees_every_open_stage_on_the_same_screen(self):
        cards = floor.cards(self.owner)
        self.assertEqual(sorted({card["state"] for card in cards}), ["outside", "ready", "running"])
        self.assertGreater(len(cards), 2)

    # ---- the tour --------------------------------------------------------------------------------------------------------------------------

    @override_settings(DEMO_MODE=True)
    def test_the_card_has_ten_steps_and_the_ones_of_a_record_open_the_demos_own(self):
        urls = [step["url"] for step in self.card("ar")["steps"]]
        sales_order = ProductionOrder.objects.get(sales_order_line__isnull=False).sales_order_line.order
        eid = order_noted("معمول للعيد")
        cake = order_noted("كيك الصباح")
        released = order_noted("دفعة الصباح")
        from batches.models import Batch

        lot = Batch.objects.get(production_run=released.run)
        oven = WorkCentre.objects.get(code="DEMO-OVEN")
        self.assertEqual(len(urls), 10)
        self.assertEqual(urls[0], f"/orders/{sales_order.pk}/?lang=ar")
        self.assertEqual(urls[1], "/manufacturing/mrp/?lang=ar")
        self.assertEqual(urls[2], "/manufacturing/floor/?lang=ar")
        self.assertEqual(urls[3], f"/manufacturing/orders/{eid.pk}/?lang=ar")
        self.assertEqual(urls[4], f"/manufacturing/runs/{cake.run_id}/?lang=ar")
        self.assertEqual(urls[5], "/manufacturing/quality/?lang=ar")
        self.assertEqual(urls[6], f"/manufacturing/trace/?lot={lot.pk}&lang=ar")
        self.assertEqual(urls[7], "/manufacturing/schedule/?lang=ar")
        self.assertEqual(urls[8], f"/manufacturing/work-centres/{oven.pk}/maintenance/?lang=ar")
        self.assertEqual(urls[9], "/manufacturing/costs/?lang=ar")
        self.assertEqual(len(set(urls)), 10)

    def test_every_step_opens_for_the_owner_in_arabic_and_in_english(self):
        self.every_step_opens(10)

    def test_no_hint_states_a_day_count_or_a_date_and_every_step_has_both_languages(self):
        for ar, en, hint_ar, hint_en, _url_name in showcase.DEMO_TOURS["manufacturing"] + showcase.DEMO_TOURS[("manufacturing", "furniture")]:
            for text in (hint_ar, hint_en):
                self.assertFalse(any(ch.isdigit() for ch in text), text)  # a built template ages
            self.assertTrue(ar and en and hint_ar and hint_en)

    def test_the_tour_and_the_demo_say_it_plainly(self):
        from reports.tests_demo_names import SLANG, SLANG_FRAGMENTS, UNWANTED_WORDS

        texts = demo_words(self)
        self.assertGreater(len([text for text in texts if text]), 60)
        for text in texts:
            for word in BANNED + UNWANTED_WORDS:
                self.assertNotIn(word, text)
            for pattern, word in zip(SLANG_FRAGMENTS, SLANG):
                self.assertIsNone(pattern.search(text), f"{word}: {text}")
            if ARABIC.search(text):
                self.assertIsNone(LATIN_EDGE.search(text.strip()), text)  # no Arabic line starts or ends with a Latin word or a placeholder


def demo_words(test):
    """Every Arabic word a factory demo adds: the tour's, and the names and notes of the records it makes."""

    from accounts.models import UserProfile
    from master_data.models import Supplier
    from staff.models import Employee

    from .models import MaintenancePlan, OrderStageLog, QualityCriterion

    texts = [step[index] for steps in (showcase.DEMO_TOURS["manufacturing"], showcase.DEMO_TOURS[("manufacturing", "furniture")]) for step in steps for index in (0, 2)]
    texts += [value for centre in WorkCentre.objects.all() for value in (centre.name_ar,)]
    texts += [value for order in ProductionOrder.objects.all() for value in (order.notes,)]
    texts += [plan.title for plan in MaintenancePlan.objects.all()]
    texts += [value for job in MaintenanceJob.objects.all() for value in (job.cause, job.note)]
    texts += [criterion.name_ar for criterion in QualityCriterion.objects.all()]
    texts += [value for row in OrderMaterialIssue.objects.all() for value in (row.reason,)]
    texts += [row.description for row in OrderCostLine.objects.all()]
    texts += [job.note for job in OutsideJob.objects.all()] + [check.note for check in QualityCheck.objects.all()] + [log.note for log in OrderStageLog.objects.all()]
    texts += [item.item_name for item in Item.objects.filter(item_code__startswith="DEMO-BYP-") | Item.objects.filter(item_code__startswith="DEMO-PART-")]
    texts += [supplier.name for supplier in Supplier.objects.filter(supplier_code__startswith="DEMO-SUP-")]
    texts += [value for employee in Employee.objects.filter(user__username="worker") for value in (employee.name, employee.title)]
    texts += [profile.display_name for profile in UserProfile.objects.filter(user__username="worker")]
    from orders.models import SalesOrder, SalesOrderLine

    mine = SalesOrder.objects.filter(lines__production_orders__isnull=False) | SalesOrder.objects.filter(lines__cost_sheet__isnull=False)  # not the sample's own quotations
    texts += [order.terms for order in mine.distinct()] + [line.description for line in SalesOrderLine.objects.filter(cost_sheet__isnull=False)]
    sheets = CostSheet.objects.all()
    texts += [str(answer) for sheet in sheets for answer in sheet.spec.values()]
    texts += [row.description for sheet in sheets for row in sheet.lines.all()]
    test.assertTrue(texts)
    return [text for text in texts if text]


class FurnitureDemo(FactoryDemo, TestCase):
    sub = "furniture"
    steps = 9

    def test_a_quotation_offered_with_its_specifications_and_cost_sheet_and_the_price_from_the_margin(self):
        sheet = CostSheet.objects.get(job_item__isnull=True)
        order = sheet.sales_order_line.order
        self.assertEqual((order.status, sheet.template, sheet.margin_percent), ("sent", "furniture", D("30.00")))
        self.assertEqual((sheet.spec["width"], sheet.spec["wood"], sheet.spec["finish"]), ("220", "زان", "ورنيش لامع"))
        from . import quotes

        figures = quotes.figures(sheet, sheet.sales_order_line.quantity)
        self.assertEqual(sheet.sales_order_line.unit_price, figures["unit_price"])
        self.assertEqual(sheet.lines.count(), 6)  # three materials, labour, overhead and the upholstery done outside
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            self.assertEqual(self.client.get(reverse("manufacturing:quote_sheet", args=[sheet.sales_order_line_id]), {"lang": lang}).status_code, 200)

    def test_an_accepted_line_became_a_custom_job_whose_item_and_recipe_are_its_own_and_it_is_finished(self):
        sheet = CostSheet.objects.get(job_item__isnull=False)
        line = sheet.sales_order_line
        job = ProductionOrder.objects.get(sales_order_line=line)
        self.assertEqual((sheet.sales_order_line.order.status, line.item, sheet.job_item, job.recipe, sheet.job_recipe), ("confirmed", sheet.job_item, sheet.job_item, sheet.job_recipe, sheet.job_recipe))
        self.assertTrue(sheet.job_item.item_code.startswith("JOB-"))
        self.assertEqual((job.status, job.quantity, job.labor_cost, job.overhead_cost), (OrderStatus.DONE, D("6.000"), D("2880.00"), D("400.00")))
        from inventory.services import get_item_location_stock_quantity

        self.assertEqual(get_item_location_stock_quantity(sheet.job_item, job.location), D("6.000"))  # made: the job's item is not at zero on the alerts

    def test_the_saw_is_down_with_a_blade_changed_and_its_plan_is_due(self):
        saw = WorkCentre.objects.get(code="DEMO-SAW")
        job = maintenance.open_job_of(saw)
        self.assertEqual((job.cause, MaintenancePart.objects.get(job=job).operation.item.item_name), ("الشفرة اتكسرت", "شفرة منشار"))
        self.assertTrue(next(state for state in maintenance.plan_states(saw) if state["plan"].work_centre == saw)["due"])

    def test_the_chairs_run_made_sawdust(self):
        run = ProductionRun.objects.get(outputs__isnull=False)
        output = RunOutput.objects.get(run=run)
        self.assertEqual((run.recipe.product.item_name, output.item.item_name, output.quantity, output.cost), ("كرسي خشب", "نشارة خشب", D("5.000"), D("75.00")))

    @override_settings(DEMO_MODE=True)
    def test_the_card_has_nine_steps_and_the_ones_of_a_record_open_the_demos_own(self):
        urls = [step["url"] for step in self.card("ar")["steps"]]
        sheet = CostSheet.objects.get(job_item__isnull=True)
        job_sheet = CostSheet.objects.get(job_item__isnull=False)
        job = ProductionOrder.objects.get(sales_order_line=job_sheet.sales_order_line)
        saw = WorkCentre.objects.get(code="DEMO-SAW")
        self.assertEqual(len(urls), 9)
        self.assertEqual(urls[0], f"/manufacturing/quotes/lines/{sheet.sales_order_line_id}/?lang=ar")
        self.assertEqual(urls[1], f"/manufacturing/quotes/lines/{job_sheet.sales_order_line_id}/?lang=ar")
        self.assertEqual(urls[2], f"/manufacturing/orders/{job.pk}/?lang=ar")
        self.assertEqual(urls[6], f"/manufacturing/work-centres/{saw.pk}/maintenance/?lang=ar")
        self.assertEqual(urls[7], f"/manufacturing/runs/{ProductionRun.objects.get(outputs__isnull=False).pk}/?lang=ar")

    def test_every_step_opens_for_the_owner_in_arabic_and_in_english(self):
        self.every_step_opens(9)

    def test_the_card_of_the_furniture_workshop_is_not_the_food_factorys(self):
        self.assertNotEqual(showcase.demo_tour("manufacturing", "furniture"), showcase.demo_tour("manufacturing", "food"))
        self.assertIs(showcase.demo_tour("manufacturing", "food"), showcase.DEMO_TOURS["manufacturing"])

    def test_the_tour_and_the_demo_say_it_plainly(self):
        from reports.tests_demo_names import SLANG, SLANG_FRAGMENTS, UNWANTED_WORDS

        texts = demo_words(self)
        self.assertGreater(len(texts), 50)
        for text in texts:
            for word in BANNED + UNWANTED_WORDS:
                self.assertNotIn(word, text)
            for pattern, word in zip(SLANG_FRAGMENTS, SLANG):
                self.assertIsNone(pattern.search(text), f"{word}: {text}")
            if ARABIC.search(text):
                self.assertIsNone(LATIN_EDGE.search(text.strip()), text)


class OtherFactories(TestCase):
    """The other factories keep what they had: the catalogue and the production orders, no work centres and no new records; the demo builds with balanced books."""

    def test_a_garments_factory_has_no_f14_records_and_its_tour_shows_only_the_steps_that_need_none(self):
        from config.demo_templates import complete_demo_setup, seed_sample_business

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_demo_setup("manufacturing", "garments")
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        self.assertTrue(seed_sample_business())
        self.assertEqual((WorkCentre.objects.count(), CostSheet.objects.count(), MaintenanceJob.objects.count(), QualityCheck.objects.count()), (0, 0, 0, 0))
        self.assertEqual(sorted(ProductionOrder.objects.values_list("stage_index", flat=True)), [0, 1, 2])
        books_balance(self)
        owner = get_user_model().objects.get(username="owner")
        self.client.force_login(owner)
        shown = 0
        for _ar, _en, _hint_ar, _hint_en, url_name in showcase.demo_tour("manufacturing", "garments"):
            path = first_run.step_path(url_name)
            if path is None:
                self.assertIn(url_name, showcase.STEPS_OF_A_RECORD)
                continue
            shown += 1
            for lang in ("ar", "en"):
                self.assertEqual(self.client.get(first_run.step_url(path, lang)).status_code, 200, (url_name, lang))
        self.assertEqual(shown, 5)  # the requirements, «شغلي», quality, the schedule and the cost report: the steps that need no record of the demo's own
        from batches.models import Batch

        self.assertFalse(Batch.objects.filter(production_run__isnull=True, item__item_code__startswith="DEMO-ITEM-").exists())  # its lots are from runs too


class AnEmptiedDemo(TestCase):
    """A demo with no sample (the visitor pressed «ابدأ فاضي»): the steps that belong to a record are dropped, the others still open."""

    def test_no_record_step_has_a_path(self):
        for name in ("tour_sales_order", "order", "run", "trace", "maintenance_centre", "quote_sheet", "tour_job_sheet"):
            self.assertIsNone(demo.tour_path(f"manufacturing:{name}"), name)
        self.assertIsNone(demo.tour_path("manufacturing:unknown"))

    def test_the_other_businesses_tours_are_not_touched(self):
        self.assertEqual(len(showcase.demo_tour("commercial", "retail")), 3)

    def test_the_worker_login_is_dropped_by_a_business_without_manufacturing(self):
        from settings_core.management.commands.prepare_demo import Command as Prepare

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        self.assertTrue(get_user_model().objects.filter(username="worker").exists())
        Prepare()._fit_users({"customers", "sales_operations"})
        self.assertFalse(get_user_model().objects.filter(username="worker").exists())
        self.assertTrue(get_user_model().objects.filter(username="cashier").exists())
