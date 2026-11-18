"""Manufacturing F6a (HG-133): work centres, routing and capacity.

The reference plant (``MANUFACTURING_PLAN.md``, F6a): a mixer at 60.00 an hour and an oven at 120.00 an hour, each 8 hours a
day (480 minutes). The cake's routing is mixing, 30 minutes per batch on the mixer, then baking, 45 minutes per batch on the
oven. The cake recipe makes 10 units a batch, so an order of 20 cakes is 2 batches: 60 and 90 planned minutes, planned
conversion 60.00 + 180.00 = 240.00. Recorded 70 and 95 minutes are suggested lines of 70.00 and 190.00 (260.00).
"""

import pathlib
from datetime import timedelta
from decimal import Decimal as D

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from attendance.models import Holiday
from hesba_testing.factories import make_item, make_location
from inventory.models import StockMovement, StockOperation
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from permissions.services import user_has_permission

from . import actuals, orders, services, workcentres
from .models import OrderCostLine, OrderStageLog, OrderStatus, RecipeStep, WorkCentre
from .stages import STAGES
from .tests import TODAY, person
from .tests_actuals import ActualsSetup


class RoutedPlant(ActualsSetup):
    def setUp(self):
        super().setUp()
        self.mixer = self.centre("MIX", "الخلاط", "Mixer", "60.00")
        self.oven = self.centre("OVEN", "الفرن", "Oven", "120.00")
        self.route(self.recipe, [("خلط", "Mixing", self.mixer, "30", "0"), ("خَبز", "Baking", self.oven, "45", "0")])

    def centre(self, code, name_ar, name_en, rate, **extra):
        data = {"code": code, "name_ar": name_ar, "name_en": name_en, "kind": "machine", "hours_per_day": "8", "rate_per_hour": rate, **extra}
        return workcentres.save_work_centre(data, self.owner)

    def route(self, recipe, steps):
        return workcentres.save_routing(recipe, [{"name_ar": ar, "name_en": en, "work_centre": c.pk, "minutes_per_batch": m, "setup_minutes": s} for ar, en, c, m, s in steps],
                                        self.owner)

    def cake_order(self, quantity="20", due=None, recipe=None, labor="0"):
        return orders.create_order({"recipe": recipe or self.recipe, "location": self.plant, "quantity": quantity, "labor_cost": labor, "due_date": due}, self.owner)

    def counts(self):
        Projector().rebuild()
        return (StockOperation.objects.count(), StockMovement.objects.count(), JournalLine.objects.count())

    def oven_day(self, day):
        block = next(b for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk)
        return next(row for row in block["days"] if row["date"] == day)


class RoutingTests(RoutedPlant):
    def test_an_order_of_two_batches_gets_the_routing_with_planned_minutes(self):
        order = self.cake_order("20")
        self.assertEqual(order.stages, [["mixing", "خلط", "Mixing", self.mixer.pk, "60.00"], ["baking", "خَبز", "Baking", self.oven.pk, "90.00"]])
        rows = workcentres.plan_rows(order)
        self.assertEqual([(r["key"], r["centre"].code, r["minutes"]) for r in rows], [("mixing", "MIX", D("60.00")), ("baking", "OVEN", D("90.00"))])
        self.assertEqual(workcentres.planned_conversion(order), D("240.00"))      # 60.00 + 180.00
        self.assertEqual(order.labor_cost, D("0.00"))                              # a figure to read: never written to the order

    def test_setup_minutes_and_fractional_batches(self):
        self.route(self.recipe, [("خلط", "Mixing", self.mixer, "30", "10"), ("خَبز", "Baking", self.oven, "45", "0")])
        self.assertEqual(self.cake_order("20").stages[0][4], "70.00")             # 10 + 30 x 2
        self.assertEqual(self.cake_order("25").stages[1][4], "112.50")            # 45 x 2.5
        self.assertEqual(self.cake_order("1").stages[1][4], "4.50")               # 45 x 0.1
        # The batches are not rounded before the minutes: one unit of a 3,000-unit batch at 900 minutes is 0.30, not 0.
        self.recipe.output_quantity = D("3000")
        self.recipe.save(update_fields=["output_quantity"])
        self.route(self.recipe, [("خَبز", "Baking", self.oven, "900", "0")])
        self.assertEqual(self.cake_order("1").stages[0][4], "0.30")

    def test_a_recipe_without_steps_keeps_the_food_stage_list(self):
        plain = services.save_recipe({"product": make_item(item_code="FG-PLAIN", item_name="خبز", is_stock_tracked=True), "output_quantity": "10"}, [(self.flour, "1")], self.owner)
        order = self.cake_order("10", recipe=plain)
        self.assertEqual(order.stages, [list(stage) for stage in STAGES["food"]])
        self.assertEqual(workcentres.plan_rows(order), [])
        self.assertEqual(workcentres.load(TODAY)[0]["days"][0]["minutes"], D("0"))
        self.route(self.recipe, [])                                                # clearing the routing returns the recipe to the factory's list
        self.assertEqual(self.cake_order("20").stages, [list(stage) for stage in STAGES["food"]])

    def test_an_open_order_keeps_the_stages_it_was_opened_with(self):
        order = self.cake_order("20")
        self.route(self.recipe, [("عجن", "Kneading", self.mixer, "99", "0")])
        order.refresh_from_db()
        self.assertEqual([s[0] for s in order.stages], ["mixing", "baking"])
        self.assertEqual(self.cake_order("20").stages[0][:3], ["kneading", "عجن", "Kneading"])

    def test_routing_rules(self):
        for rows, text in (
            ([{"name_ar": "", "work_centre": self.mixer.pk, "minutes_per_batch": "5"}], "اسم المرحلة"),
            ([{"name_ar": "خلط", "work_centre": "", "minutes_per_batch": "5"}], "مركز عمل شغال"),
            ([{"name_ar": "خلط", "work_centre": self.mixer.pk, "minutes_per_batch": "0"}], "أكبر من صفر"),
            ([{"name_ar": "خلط", "work_centre": self.mixer.pk, "minutes_per_batch": "abc"}], "أكبر من صفر"),
            ([{"name_ar": "خلط", "work_centre": self.mixer.pk, "minutes_per_batch": "5", "setup_minutes": "-1"}], "مش سالب"),
        ):
            with self.subTest(text=text), self.assertRaisesMessage(ValidationError, text):
                workcentres.save_routing(self.recipe, rows, self.owner)
        self.assertEqual(self.recipe.steps.count(), 2)                              # a refused save changes nothing
        with self.assertRaisesMessage(ValidationError, "Row 1: choose an active work centre."):
            workcentres.save_routing(self.recipe, [{"name_ar": "x", "work_centre": 999999, "minutes_per_batch": "5"}], self.owner, "en")
        self.oven.active = False
        self.oven.save()
        self.assertEqual(len(workcentres.save_routing(self.recipe, [{"name_ar": "خَبز", "name_en": "Baking", "work_centre": self.oven.pk, "minutes_per_batch": "45"}], self.owner)), 1)
        self.assertEqual([s.stage_key for s in self.recipe.steps.all()], ["baking"])  # a centre switched off since stays on the recipe that has it
        with self.assertRaises(IntegrityError), transaction.atomic():
            RecipeStep.objects.create(recipe=self.recipe, order=1, stage_key="dup", name_ar="x", work_centre=self.oven, minutes_per_batch=1)

    def test_work_centre_rules(self):
        for data, text in (({"code": ""}, "كود"), ({"code": "MIX"}, "مستخدم"), ({"code": "N1", "name_ar": ""}, "اسم مركز العمل"), ({"code": "N1", "name_ar": "س", "kind": "x"}, "نوع"),
                           ({"code": "N1", "name_ar": "س", "kind": "line", "hours_per_day": "0"}, "ساعات"), ({"code": "N1", "name_ar": "س", "kind": "line", "hours_per_day": "25"}, "ساعات"),
                           ({"code": "N1", "name_ar": "س", "kind": "line", "hours_per_day": "8", "rate_per_hour": "-1"}, "مش سالب"),
                           ({"code": "N1", "name_ar": "س", "kind": "line", "hours_per_day": "8", "fixed_asset": "999999"}, "سجل الأصول")):
            with self.subTest(text=text), self.assertRaisesMessage(ValidationError, text):
                workcentres.save_work_centre(data, self.owner)

    def test_a_work_centre_can_point_at_the_machine_in_the_asset_register(self):
        from fixed_assets.models import FixedAsset

        asset = FixedAsset.objects.create(code="FA-OVEN", name="فرن الصاج", in_service_on=TODAY, cost="50000", useful_months=120)
        self.assertEqual(self.centre("OVEN2", "فرن ٢", "Oven 2", "90", fixed_asset=asset.pk).fixed_asset, asset)


class MinutesAndSuggestionTests(RoutedPlant):
    def test_recorded_minutes_become_suggested_lines_and_the_finish_absorbs_260(self):
        order = self.cake_order("20")
        orders.advance(order, self.owner, minutes="70")
        orders.advance(order, self.owner, minutes="95")
        order.refresh_from_db()
        self.assertEqual([log.minutes for log in order.stage_logs.all()], [D("70.00"), D("95.00")])
        rows = workcentres.suggestions(order)
        self.assertEqual([(r["label"], r["centre"].code, r["amount"], r["line"]) for r in rows], [("خلط", "MIX", D("70.00"), None), ("خَبز", "OVEN", D("190.00"), None)])
        self.assertEqual(sum(r["amount"] for r in rows), D("260.00"))
        # Nothing is booked by a suggestion: no cost line, and the card still shows the planned (typed) figure.
        self.assertEqual(order.cost_lines.count(), 0)
        before = self.counts()
        self.assertEqual(actuals.conversion(order)["total"], D("0.00"))

        first, second = workcentres.confirm(order, self.owner, rows[0]["log"].pk), workcentres.confirm(order, self.owner, rows[1]["log"].pk)
        self.assertEqual((first.kind, first.amount, first.work_centre, first.stage_log), ("labour", D("70.00"), self.mixer, rows[0]["log"]))
        self.assertEqual((second.amount, second.work_centre), (D("190.00"), self.oven))
        self.assertIsNone(first.hours)                                             # priced from minutes, never from rounded hours (1.17 h x 60.00 = 70.20)
        # A rate changed later does not change what a confirmed stage shows: the saved 190.00 the finish absorbs.
        self.oven.rate_per_hour = D("240.00")
        self.oven.save(update_fields=["rate_per_hour"])
        self.assertEqual([r["amount"] for r in workcentres.suggestions(order)], [D("70.00"), D("190.00")])
        self.assertEqual(actuals.conversion(order)["labour"], D("260.00"))
        self.assertEqual(self.counts(), before)                                    # stock, cost and journals are unchanged until the finish

        # A second click on a confirmed stage is refused; the order still carries 260.00.
        with self.assertRaisesMessage(ValidationError, "اتسجلت قبل كده"):
            workcentres.confirm(order, self.owner, rows[0]["log"].pk)
        self.assertEqual(order.cost_lines.filter(cancelled_at__isnull=True).count(), 2)
        # Cancelling the line brings the suggestion back, and it can be confirmed again.
        actuals.cancel_cost_line(first, self.owner)
        self.assertIsNone(workcentres.suggestions(order)[0]["line"])
        workcentres.confirm(order, self.owner, rows[0]["log"].pk)
        self.assertEqual(actuals.conversion(order)["labour"], D("260.00"))

        orders.finish(order, self.owner)
        order.refresh_from_db()
        run = order.run
        self.assertEqual((order.status, run.total_cost, run.conversion_cost), (OrderStatus.DONE, D("190.00"), D("260.00")))
        Projector().rebuild()
        self.assertEqual(self.output_journal(run.output_operation), self.expected_output_journal("450.00", "190.00", "260.00"))

    def test_a_stage_without_minutes_or_without_a_rate_has_no_suggestion(self):
        self.centre("FREE", "ترابيزة", "Table", "0")
        self.route(self.recipe, [("خلط", "Mixing", WorkCentre.objects.get(code="FREE"), "30", "0"), ("خَبز", "Baking", self.oven, "45", "0")])
        order = self.cake_order("20")
        orders.advance(order, self.owner, minutes="30")
        orders.advance(order, self.owner)                                          # baking: no minutes typed
        order.refresh_from_db()
        rows = workcentres.suggestions(order)
        self.assertEqual([(r["label"], r["amount"]) for r in rows], [("خلط", D("0.00"))])
        with self.assertRaisesMessage(ValidationError, "مفيش تكلفة مقترحة"):
            workcentres.confirm(order, self.owner, rows[0]["log"].pk)
        with self.assertRaisesMessage(ValidationError, "مش على الأمر ده"):
            workcentres.confirm(order, self.owner, 999999)

    def test_minutes_are_optional_and_must_be_above_zero(self):
        order = self.cake_order("20")
        for bad in ("0", "-5", "abc"):
            with self.subTest(bad=bad), self.assertRaisesMessage(ValidationError, "دقايق المرحلة"):
                orders.advance(order, self.owner, minutes=bad)
        self.assertEqual(OrderStageLog.objects.count(), 0)
        orders.advance(order, self.owner, minutes="")
        self.assertIsNone(order.stage_logs.get().minutes)

    def test_confirming_needs_the_cost_permission_and_an_open_order(self):
        order = self.cake_order("20")
        orders.advance(order, self.owner, minutes="70")
        log = order.stage_logs.get()
        keeper = person(RoleCode.STOCK_KEEPER, "f6a_keeper")
        with self.assertRaises(PermissionDenied):
            workcentres.confirm(order, keeper, log.pk)
        orders.advance(order, self.owner, minutes="95")
        orders.finish(order, self.owner)
        with self.assertRaisesMessage(ValidationError, "لحد ما الأمر يخلص"):
            workcentres.confirm(order, self.owner, log.pk)
        self.assertEqual(OrderCostLine.objects.count(), 0)


class LoadTests(RoutedPlant):
    def setUp(self):
        super().setUp()
        self.bread = services.save_recipe({"product": make_item(item_code="FG-BREAD", item_name="عيش", is_stock_tracked=True), "output_quantity": "10"}, [(self.flour, "1")], self.owner)
        self.route(self.bread, [("خَبز", "Baking", self.oven, "100", "0")])
        self.day = TODAY + timedelta(days=3)

    def test_three_orders_on_the_oven_in_one_day_are_over_capacity(self):
        self.cake_order("20", due=self.day)                                        # baking 45 x 2 = 90
        self.cake_order("40", due=self.day)                                        # 45 x 4 = 180
        self.cake_order("30", due=self.day, recipe=self.bread)                     # 100 x 3 = 300
        row = self.oven_day(self.day)
        self.assertEqual((row["minutes"], row["capacity"], row["over"], row["holiday"]), (D("570"), D("480"), True, False))
        block = next(b for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk)
        self.assertTrue(block["over"])
        self.assertEqual(len(block["days"]), 14)
        mixer = next(b for b in workcentres.load(TODAY) if b["centre"].pk == self.mixer.pk)
        self.assertEqual(sum(r["minutes"] for r in mixer["days"]), D("180"))      # 60 + 120 on the mixer; the bread has no mixing
        self.assertFalse(mixer["over"])
        self.assertEqual(self.oven_day(self.day + timedelta(days=1))["minutes"], D("0"))

    def test_a_day_inside_capacity_is_not_marked(self):
        self.cake_order("40", due=self.day)
        row = self.oven_day(self.day)
        self.assertEqual((row["minutes"], row["capacity"], row["over"]), (D("180"), D("480"), False))

    def test_a_holiday_has_no_capacity(self):
        Holiday.objects.create(date=self.day, name="عيد")
        empty = self.oven_day(self.day)
        self.assertEqual((empty["minutes"], empty["capacity"], empty["holiday"], empty["over"]), (D("0"), D("0"), True, False))   # nothing planned: not over
        self.cake_order("20", due=self.day)
        row = self.oven_day(self.day)
        self.assertEqual((row["minutes"], row["capacity"], row["over"]), (D("90"), D("0"), True))                                  # any minute is over zero
        self.assertEqual(self.oven_day(self.day + timedelta(days=1))["capacity"], D("480"))

    def test_dates_and_stages_that_do_not_load_a_day(self):
        self.cake_order("20", due=TODAY - timedelta(days=2))                        # overdue: counted today
        self.cake_order("20", due=TODAY + timedelta(days=30))                       # after the window
        self.cake_order("20")                                                       # no due date
        started = self.cake_order("20", due=self.day)
        orders.advance(started, self.owner)                                         # mixing done: only baking (90) is left
        block = next(b for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk)
        self.assertEqual((block["days"][0]["minutes"], block["later"], block["unscheduled"], self.oven_day(self.day)["minutes"]), (D("90"), D("90"), D("90"), D("90")))
        mixer = next(b for b in workcentres.load(TODAY) if b["centre"].pk == self.mixer.pk)
        self.assertEqual(mixer["days"][3]["minutes"], D("0"))                      # the started order's mixing no longer loads
        orders.cancel(started, self.owner, "x")
        self.assertEqual(self.oven_day(self.day)["minutes"], D("0"))               # a cancelled order loads nothing


class ScreenTests(RoutedPlant):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_screens_open_in_both_languages(self):
        order = self.cake_order("20", due=TODAY + timedelta(days=1))
        orders.advance(order, self.owner, minutes="70")
        for lang, word in (("ar", "مراكز العمل"), ("en", "Work centres")):
            for url in (reverse("manufacturing:work_centres"), reverse("manufacturing:work_centre_new"), reverse("manufacturing:work_centre", args=[self.oven.pk]),
                        reverse("manufacturing:recipe", args=[self.recipe.pk]), reverse("manufacturing:order", args=[order.pk]), reverse("manufacturing:home")):
                with self.subTest(url=url, lang=lang):
                    self.assertEqual(self.client.get(url, {"lang": lang}).status_code, 200)
        self.assertContains(self.client.get(reverse("manufacturing:work_centres"), {"lang": "ar"}), "مراكز العمل")
        self.assertContains(self.client.get(reverse("manufacturing:work_centres"), {"lang": "en"}), "Work centres")
        self.assertContains(self.client.get(reverse("manufacturing:home")), "data-work-centres-nav")

    def test_the_order_page_shows_the_routing_planned_conversion_and_confirm_button(self):
        order = self.cake_order("20")
        page = self.client.get(reverse("manufacturing:order", args=[order.pk]))
        self.assertContains(page, 'data-routing-stage="mixing"')
        self.assertContains(page, "data-planned-conversion")
        self.assertContains(page, "240.00")
        self.assertContains(page, "data-stage-minutes")
        url = reverse("manufacturing:order", args=[order.pk])
        self.client.post(url, {"action": "advance", "minutes": "70", "lang": "ar"})
        self.client.post(url, {"action": "advance", "minutes": "95", "lang": "ar"})
        page = self.client.get(url)
        self.assertContains(page, 'data-suggested-line="mixing"')
        self.assertContains(page, "data-confirm-cost", count=2)
        # Each row has one cell per heading: the oven's 95 minutes, then its 190.00 (95 x 120.00 / 60).
        self.assertContains(page, '<td dir="ltr" data-suggested-minutes>95</td><td dir="ltr" data-suggested-amount>190.00</td>', html=False)
        log = order.stage_logs.get(stage_key="mixing")
        self.client.post(url, {"action": "confirm_cost", "log": log.pk, "lang": "ar"})
        self.client.post(url, {"action": "confirm_cost", "log": log.pk, "lang": "ar"})      # a second click
        self.assertEqual(order.cost_lines.filter(cancelled_at__isnull=True).count(), 1)
        page = self.client.get(url)
        self.assertContains(page, "data-suggested-confirmed", count=1)
        self.assertContains(page, "data-confirm-cost", count=1)
        self.assertEqual(actuals.conversion(order)["labour"], D("70.00"))
        self.assertContains(self.client.post(url, {"action": "confirm_cost", "log": "x", "lang": "en"}), "data-order-error")

    def test_a_routed_order_has_its_own_column_on_the_board(self):
        order = self.cake_order("20")
        orders.advance(order, self.owner)                                           # mixing done: the order waits at baking
        columns = {chunk.split('"', 1)[0]: chunk for chunk in self.client.get(reverse("manufacturing:orders")).content.decode().split('data-column="')[1:]}
        self.assertIn("baking:خَبز", columns)
        self.assertIn(order.number, columns["baking:خَبز"])
        self.assertNotIn(order.number, columns["planned"])                         # not dumped into «لسه مبدأش» as before
        self.assertNotIn(order.number, columns["cooking"])
        self.assertTrue(list(columns).index("baking:خَبز") < list(columns).index("ready"))
        other = self.cake_order("20")                                              # an order not started yet is in «planned»
        columns = {chunk.split('"', 1)[0]: chunk for chunk in self.client.get(reverse("manufacturing:orders")).content.decode().split('data-column="')[1:]}
        self.assertIn(other.number, columns["planned"])

    def test_the_routing_names_the_centre_in_the_page_language(self):
        order = self.cake_order("20")
        orders.advance(order, self.owner, minutes="70")
        english = self.client.get(reverse("manufacturing:order", args=[order.pk]), {"lang": "en"}).content.decode()
        self.assertIn("Mixer", english)
        self.assertNotIn("الخلاط", english)
        self.assertIn("الخلاط", self.client.get(reverse("manufacturing:order", args=[order.pk]), {"lang": "ar"}).content.decode())

    def test_the_load_page_marks_the_day_over_capacity(self):
        day = TODAY + timedelta(days=2)
        for quantity in ("40", "40", "40"):
            self.cake_order(quantity, due=day)                                      # 3 x 180 = 540 of 480
        page = self.client.get(reverse("manufacturing:work_centres"))
        self.assertContains(page, f'data-load-day="{day:%Y-%m-%d}" data-over')
        self.assertContains(page, 'data-load-centre="OVEN" data-over')
        self.assertNotContains(page, 'data-load-centre="MIX" data-over')

    def test_saving_a_work_centre_and_a_routing_from_the_screens(self):
        response = self.client.post(reverse("manufacturing:work_centre_new"), {"code": "PACK", "name_ar": "خط التعبئة", "kind": "line", "hours_per_day": "10",
                                                                                "rate_per_hour": "45.50", "active": "on", "lang": "ar"})
        self.assertEqual(response.status_code, 302)
        pack = WorkCentre.objects.get(code="PACK")
        self.assertEqual((pack.hours_per_day, pack.rate_per_hour, pack.kind, pack.entity_id), (D("10.00"), D("45.50"), "line", None))
        bad = self.client.post(reverse("manufacturing:work_centre", args=[pack.pk]), {"code": "PACK", "name_ar": "", "kind": "line", "hours_per_day": "10", "lang": "ar"})
        self.assertContains(bad, "data-work-centre-error")
        url = reverse("manufacturing:recipe", args=[self.recipe.pk])
        done = self.client.post(url, {"action": "routing", "lang": "ar", "step_name_ar_0": "تعبئة", "step_name_en_0": "Packing", "step_centre_0": pack.pk,
                                      "step_minutes_0": "5", "step_setup_0": "15"})
        self.assertEqual(done.status_code, 302)
        self.assertEqual([(s.stage_key, s.setup_minutes) for s in self.recipe.steps.all()], [("packing", D("15.00"))])
        page = self.client.post(url, {"action": "routing", "lang": "ar", "step_name_ar_0": "تعبئة", "step_centre_0": "", "step_minutes_0": "5"})
        self.assertContains(page, "data-routing-error")
        self.assertContains(page, 'value="تعبئة"')                                   # what was typed stays on the form
        self.assertEqual(self.recipe.steps.count(), 1)                              # and the refused save changed nothing
        self.assertContains(self.client.get(url), 'data-recipe-step="packing"')

    def test_a_switched_off_centre_stays_chosen_and_an_unchanged_routing_saves(self):
        self.oven.active = False
        self.oven.save()
        url = reverse("manufacturing:recipe", args=[self.recipe.pk])
        self.assertContains(self.client.get(url), f'<option value="{self.oven.pk}" selected>الفرن</option>')   # the saved row keeps its centre
        done = self.client.post(url, {"action": "routing", "lang": "ar",
                                      "step_name_ar_0": "خلط", "step_name_en_0": "Mixing", "step_centre_0": self.mixer.pk, "step_minutes_0": "30", "step_setup_0": "0",
                                      "step_name_ar_1": "خَبز", "step_name_en_1": "Baking", "step_centre_1": self.oven.pk, "step_minutes_1": "40", "step_setup_1": "0"})
        self.assertEqual(done.status_code, 302)
        self.assertEqual([(s.work_centre_id, s.minutes_per_batch) for s in self.recipe.steps.all()], [(self.mixer.pk, D("30.00")), (self.oven.pk, D("40.00"))])
        other = self.recipe.__class__.objects.exclude(pk=self.recipe.pk).first()
        if other is not None:                                                       # a recipe that never had it is still not offered a switched-off centre
            self.assertNotContains(self.client.get(reverse("manufacturing:recipe", args=[other.pk])), f'<option value="{self.oven.pk}">الفرن</option>')

    def test_the_new_screens_use_no_banned_word(self):
        banned = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")
        for name in ("templates/manufacturing/work_centres.html", "templates/manufacturing/work_centre_form.html", "templates/manufacturing/_routing.html",
                     "templates/manufacturing/recipe.html", "manufacturing/workcentres.py", "manufacturing/workcentre_views.py"):
            text = (pathlib.Path(settings.BASE_DIR) / name).read_text(encoding="utf-8")
            for word in banned:
                with self.subTest(name=name, word=word):
                    self.assertNotIn(word, text)
        order = self.cake_order("20")
        orders.advance(order, self.owner, minutes="70")
        for url in (reverse("manufacturing:work_centres"), reverse("manufacturing:order", args=[order.pk]), reverse("manufacturing:recipe", args=[self.recipe.pk])):
            text = self.client.get(url).content.decode()
            for word in banned:
                self.assertNotIn(word, text)


class PermissionTests(RoutedPlant):
    def test_a_role_without_the_cost_permission_sees_no_rate(self):
        order = self.cake_order("20", due=TODAY + timedelta(days=1))
        orders.advance(order, self.owner, minutes="70")
        for code, name in ((RoleCode.STOCK_KEEPER, "f6a_keeper"), (RoleCode.SITE_ENGINEER, "f6a_engineer"), (RoleCode.MANAGER, "f6a_manager")):
            user = person(code, name)
            self.assertTrue(user_has_permission(user, "inventory.view_stock"))
            self.assertFalse(user_has_permission(user, "inventory.view_cost"))
            self.client.force_login(user)
            for lang in ("ar", "en"):
                page = self.client.get(reverse("manufacturing:work_centres"), {"lang": lang})
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "data-work-centre=")                        # the centres themselves are listed
                self.assertNotContains(page, "data-wc-rate")
                self.assertNotContains(page, "60.00")
                self.assertNotContains(page, "120.00")
            routing = self.client.get(reverse("manufacturing:recipe", args=[self.recipe.pk]))
            self.assertNotContains(routing, "data-routing-cost")
            page = self.client.get(reverse("manufacturing:order", args=[order.pk]))
            self.assertEqual(page.status_code, 200)
            for marker in ("data-planned-conversion", "data-suggested-lines", "data-suggested-amount"):
                self.assertNotContains(page, marker)
            self.assertContains(page, 'data-routing-stage="baking"')                  # the routing and minutes are not costs
            self.assertEqual(self.client.post(reverse("manufacturing:order", args=[order.pk]), {"action": "confirm_cost", "log": order.stage_logs.get().pk}).status_code, 403)

    def test_setting_a_rate_needs_manage_items_and_the_cost_permission(self):
        keeper = person(RoleCode.STOCK_KEEPER, "f6a_keeper")                        # may manage items, may not see costs
        self.assertTrue(user_has_permission(keeper, "master_data.manage_items"))
        with self.assertRaises(PermissionDenied):
            workcentres.save_work_centre({"code": "MIX", "name_ar": "الخلاط", "kind": "machine", "hours_per_day": "8", "rate_per_hour": "1.00"}, keeper, self.mixer)
        self.mixer.refresh_from_db()
        self.assertEqual(self.mixer.rate_per_hour, D("60.00"))
        # Without the rate key the rest is saved and the stored rate stays (it is not reset to zero).
        workcentres.save_work_centre({"code": "MIX", "name_ar": "الخلاط الكبير", "kind": "machine", "hours_per_day": "6"}, keeper, self.mixer)
        self.mixer.refresh_from_db()
        self.assertEqual((self.mixer.name_ar, self.mixer.hours_per_day, self.mixer.rate_per_hour), ("الخلاط الكبير", D("6.00"), D("60.00")))
        self.client.force_login(keeper)
        form = self.client.get(reverse("manufacturing:work_centre", args=[self.mixer.pk]))
        self.assertEqual(form.status_code, 200)
        self.assertNotContains(form, "data-wc-rate-input")
        self.client.post(reverse("manufacturing:work_centre", args=[self.mixer.pk]), {"code": "MIX", "name_ar": "الخلاط", "kind": "machine", "hours_per_day": "8",
                                                                                     "rate_per_hour": "1.00", "active": "on", "lang": "ar"})
        self.mixer.refresh_from_db()
        self.assertEqual((self.mixer.rate_per_hour, self.mixer.hours_per_day), (D("60.00"), D("8.00")))   # the posted rate was ignored: the form never offered it
        # An engineer may not manage items at all.
        engineer = person(RoleCode.SITE_ENGINEER, "f6a_engineer")
        with self.assertRaises(PermissionDenied):
            workcentres.save_work_centre({"code": "X", "name_ar": "س", "kind": "line", "hours_per_day": "8"}, engineer)
        with self.assertRaises(PermissionDenied):
            workcentres.save_routing(self.recipe, [], engineer)
        self.client.force_login(engineer)
        self.assertEqual(self.client.get(reverse("manufacturing:work_centre_new")).status_code, 403)
        self.assertEqual(self.client.post(reverse("manufacturing:recipe", args=[self.recipe.pk]), {"action": "routing"}).status_code, 403)
        self.assertEqual(self.recipe.steps.count(), 2)
        self.client.force_login(person(RoleCode.CASHIER, "f6a_cashier"))             # no stock permission at all: not even the list
        self.assertEqual(self.client.get(reverse("manufacturing:work_centres")).status_code, 403)


class EntityTests(RoutedPlant):
    def test_another_entitys_work_centre_is_not_offered_and_returns_404(self):
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BR6A", "name_ar": "فرع العبور", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        make_location(location_code="BR-PLANT", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        far = WorkCentre.objects.create(code="BR-OVEN", name_ar="فرن الفرع", kind="machine", hours_per_day=8, rate_per_hour="99.00", entity=branch)
        self.client.force_login(self.owner)
        main = Entity.objects.get(is_main=True)
        self.client.post(reverse("entities:switch"), {"entity": main.pk, "next": "/dashboard/"})
        page = self.client.get(reverse("manufacturing:work_centres"))
        self.assertContains(page, 'data-work-centre="OVEN"')
        self.assertNotContains(page, "BR-OVEN")
        self.assertEqual(self.client.get(reverse("manufacturing:work_centre", args=[far.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("manufacturing:work_centre", args=[far.pk]), {"code": "BR-OVEN", "name_ar": "x", "kind": "line", "hours_per_day": "8"}).status_code, 404)
        recipe_page = self.client.get(reverse("manufacturing:recipe", args=[self.recipe.pk]))
        self.assertContains(recipe_page, f'<option value="{self.oven.pk}"')
        self.assertNotContains(recipe_page, "فرن الفرع")                               # not offered in the routing form
        self.assertContains(page, 'data-load-centre="OVEN"')
        self.assertNotContains(page, 'data-load-centre="BR-OVEN"')                    # nor in the load table
        # A posted id from another entity is refused by the service, and the saved routing is untouched.
        from entities.current import working_in

        with working_in(main), self.assertRaisesMessage(ValidationError, "مركز عمل شغال"):
            workcentres.save_routing(self.recipe, [{"name_ar": "خَبز", "work_centre": far.pk, "minutes_per_batch": "5"}], self.owner)
        self.assertEqual(self.recipe.steps.count(), 2)
        with working_in(main):
            self.assertEqual([b["centre"].code for b in workcentres.load(TODAY)], ["MIX", "OVEN"])
        # Switched to the branch: the main entity's centres are the ones that are not there, and a new centre belongs to the branch.
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        page = self.client.get(reverse("manufacturing:work_centres"))
        self.assertContains(page, 'data-work-centre="BR-OVEN"')
        self.assertNotContains(page, 'data-work-centre="OVEN"')
        self.assertEqual(self.client.get(reverse("manufacturing:work_centre", args=[self.oven.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("manufacturing:work_centre", args=[far.pk])).status_code, 200)
        self.client.post(reverse("manufacturing:work_centre_new"), {"code": "BR-NEW", "name_ar": "جديد", "kind": "manual", "hours_per_day": "6", "rate_per_hour": "10", "lang": "ar"})
        self.assertEqual(WorkCentre.objects.get(code="BR-NEW").entity, branch)
        self.client.post(reverse("entities:switch"), {"entity": "", "next": "/dashboard/"})
        self.assertContains(self.client.get(reverse("manufacturing:work_centres")), 'data-work-centre="BR-OVEN"')   # the whole group sees both


    def test_a_routing_belongs_to_the_entity_of_its_work_centres(self):
        from entities.current import working_in
        from entities.services import save_entity

        branch = save_entity({"code": "BR6C", "name_ar": "فرع العاشر", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        branch_plant = make_location(location_code="BR-PLANT3", name_ar="مصنع العاشر", name_en="Tenth plant", entity=branch)
        # An order opened at the branch never takes the main entity's mixer, oven and their rates: it gets the factory's list.
        there = orders.create_order({"recipe": self.recipe, "location": branch_plant, "quantity": "20", "labor_cost": "0"}, self.owner)
        self.assertTrue(all(len(stage) == 3 for stage in there.stages))
        self.assertEqual(workcentres.plan_rows(there), [])
        self.assertEqual([stage[3] for stage in self.cake_order().stages], [self.mixer.pk, self.oven.pk])   # the main plant still takes it
        # The branch neither sees nor replaces it.
        with working_in(branch):
            self.assertFalse(workcentres.routing_visible(self.recipe))
            with self.assertRaisesMessage(ValidationError, "كيان تاني"):
                workcentres.save_routing(self.recipe, [], self.owner)
        self.assertEqual(self.recipe.steps.count(), 2)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        page = self.client.get(reverse("manufacturing:recipe", args=[self.recipe.pk]))
        self.assertContains(page, "data-routing-other")
        for hidden in ("data-recipe-step", "data-routing-form", "data-routing-cost"):
            self.assertNotContains(page, hidden)
        self.client.post(reverse("entities:switch"), {"entity": "", "next": "/dashboard/"})
        # Seen from the whole group every centre is offered, but one routing cannot mix two entities' centres.
        far = WorkCentre.objects.create(code="BR-OVEN3", name_ar="فرن العاشر", kind="machine", hours_per_day=8, rate_per_hour="99.00", entity=branch)
        with self.assertRaisesMessage(ValidationError, "نفس الكيان"):
            workcentres.save_routing(self.recipe, [{"name_ar": "خلط", "work_centre": self.mixer.pk, "minutes_per_batch": "5"},
                                                   {"name_ar": "خَبز", "work_centre": far.pk, "minutes_per_batch": "5"}], self.owner)
        self.assertEqual(self.recipe.steps.count(), 2)


class UnchangedUntilTheFinishTests(RoutedPlant):
    def test_routing_minutes_and_suggestions_move_no_stock_cost_or_journal(self):
        order = self.cake_order("20")
        orders.advance(order, self.owner, minutes="70")                             # the start issues the materials (HG-036), as before F6a
        baseline = self.counts()
        orders.advance(order, self.owner, minutes="95")
        for row in workcentres.suggestions(order):
            workcentres.confirm(order, self.owner, row["log"].pk)
        self.assertEqual(self.counts(), baseline)
        self.assertEqual(self.position(self.cake)[0], D("0"))                        # no cakes in stock yet
        orders.finish(order, self.owner)
        after = self.counts()
        self.assertNotEqual(after, baseline)                                        # only the finish moves them
