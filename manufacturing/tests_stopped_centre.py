"""Manufacturing video findings F4 and F12 (approved 23 Nov 2026): a work centre that is down for maintenance.

«Down» is what F10 (HG-141) already calls it: the centre has an open breakdown (``MaintenanceJob`` of kind breakdown with no
``closed_at``). A planned maintenance job takes no capacity (HG-141) and stops nothing here. A breakdown has no expected end
(it is opened at a clock time no later than now and closed when the machine is back), so a stage on a down centre waits
until the job is closed and the schedule is arranged again.

* **F4, the schedule.** «رتّب» and a move to a day never plan a stage on a down centre: that stage and the stages after it on
  the same order wait (no planned day), and the screens say why. A stage cannot be moved onto a down centre. The load table
  counts a waiting stage apart, never against a day.
* **F12, the start.** «ابدأ» is not offered on a stage whose centre is down; a note says so. ``floor.start`` refuses it, so a
  direct POST does nothing. A stage started before the breakdown is still finished (work in progress is never trapped). On
  the order page, a stage on a down centre that was never started is not recorded either; one started on the floor is.

The bakery of F10 (``tests_maintenance.OvenPlant``) with a mixer before the oven: mixing 30 minutes, baking 45.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from entities.models import EntityMembership
from entities.services import save_entity
from hesba_testing.factories import make_item, make_location, stock_in
from inventory.models import StockOperation
from permissions.models import RoleCode

from . import floor, maintenance, orders, outside, schedule, services, workcentres
from .models import OrderStatus, OrderStep, OutsideStatus
from .tests import person
from .tests_maintenance import TODAY, OvenPlant
from .tests_quality import limited

CAUSE = "سير الخلاط اتقطع"


class StoppedPlant(OvenPlant):
    def setUp(self):
        super().setUp()
        self.mixer = self.centre("MIX", "الخلاط", "Mixer")
        self.cake = make_item(item_code="FG-CAKE", item_name="كيك", is_stock_tracked=True, default_sale_price="9")
        self.mixed = services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(self.flour, "1")], self.owner)
        workcentres.save_routing(self.mixed, [
            {"name_ar": "خلط", "name_en": "Mixing", "work_centre": self.mixer.pk, "minutes_per_batch": "30", "setup_minutes": "0"},
            {"name_ar": "خَبز", "name_en": "Baking", "work_centre": self.oven.pk, "minutes_per_batch": "45", "setup_minutes": "0"},
        ], self.owner)
        self.worker = person(RoleCode.PRODUCTION_WORKER, "stop_worker")
        self.floor_url = reverse("manufacturing:floor")

    def cake_order(self, due=None):
        return orders.create_order({"recipe": self.mixed, "location": self.plant, "quantity": "10", "due_date": due}, self.owner)

    def bread_order(self, due=None):
        return orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "10", "due_date": due}, self.owner)

    def down(self, centre=None):
        return self.breakdown(centre or self.mixer, cause=CAUSE)

    def plan(self, order):
        """[(stage key, work centre code, planned start day or None, planned end or None)] of the order's steps."""

        return [(step.stage_key, step.work_centre.code if step.work_centre_id else None,
                 schedule.day_of(step.planned_start) if step.planned_start else None, step.planned_end)
                for step in OrderStep.objects.filter(order=order).select_related("work_centre").order_by("index")]

    def state(self, order):
        order.refresh_from_db()
        return (order.status, order.stage_index, order.stage_logs.count(), order.issues.count(), StockOperation.objects.count(),
                list(order.steps.order_by("index").values_list("started_at", "finished_at")))


# ---------------------------------------------------------------------------------------------------------------
# F4: the schedule never books a down centre
# ---------------------------------------------------------------------------------------------------------------


class ScheduleTests(StoppedPlant):
    def test_arrange_leaves_the_stage_on_the_down_mixer_and_the_stage_after_it_waiting_and_says_why(self):
        order = self.cake_order(due=TODAY + timedelta(days=2))
        job = self.down()
        schedule.arrange(TODAY, self.owner)
        self.assertEqual([(key, code, day) for key, code, day, _end in self.plan(order)], [(order.stages[0][0], "MIX", None), (order.stages[1][0], "OVEN", None)])
        self.assertTrue(all(end is None for *_rest, end in self.plan(order)))
        data = schedule.grid(TODAY, 7)
        mixer_lane = next(lane for lane in data["lanes"] if lane["centre"] and lane["centre"].pk == self.mixer.pk)
        self.assertEqual([row["items"] for row in mixer_lane["days"]], [[]] * 7)              # nothing is booked on the mixer
        self.assertFalse(mixer_lane["over"])
        row = next(row for row in data["orders"] if row["order"].pk == order.pk)
        self.assertEqual((row["planned"], row["waiting"]["centre"], row["waiting"]["job"]), (False, self.mixer, job))
        self.assertIn("الخلاط", row["waiting"]["text"])
        self.assertIn(CAUSE, row["waiting"]["text"])
        self.client.force_login(self.owner)
        page = self.client.get(reverse("manufacturing:schedule"), {"lang": "ar"})
        self.assertContains(page, f'data-waiting="{order.pk}"')
        self.assertContains(page, "واقف للصيانة")
        self.assertContains(self.client.get(reverse("manufacturing:schedule"), {"lang": "en"}), "down for maintenance")
        plan = self.client.get(reverse("manufacturing:order", args=[order.pk]), {"lang": "ar"})
        self.assertContains(plan, "data-plan-waiting")
        self.assertNotContains(plan, "data-unplanned")                                        # it is not «press Arrange»: it waits for the machine

    def test_a_plan_made_before_the_breakdown_is_taken_off_the_down_mixer_at_the_next_arrange(self):
        order = self.cake_order()
        schedule.arrange(TODAY, self.owner)
        self.assertEqual([day for _key, _code, day, _end in self.plan(order)], [TODAY, TODAY])
        self.down()
        schedule.arrange(TODAY, self.owner)
        self.assertEqual([day for _key, _code, day, _end in self.plan(order)], [None, None])
        self.assertEqual([step.pk for step in schedule.planned_steps()], [])

    def test_a_running_centre_is_planned_exactly_as_before(self):
        bread = self.bread_order()
        schedule.arrange(TODAY, self.owner)
        before = [(day, end) for _key, _code, day, end in self.plan(bread)]
        self.down()
        result = schedule.arrange(TODAY, self.owner)
        self.assertEqual([(day, end) for _key, _code, day, end in self.plan(bread)], before)
        self.assertEqual(before[0][0], TODAY)
        self.assertEqual((result["orders"], result["steps"], result["waiting"]), (1, 1, []))
        # A cake whose mixing was done before the mixer stopped is baked on the oven, which runs: the down mixer does not hold it back.
        maintenance.close_job(maintenance.open_job_of(self.mixer), self.owner)
        cake = self.cake_order()
        orders.advance(cake, self.owner, minutes=30)
        self.down()
        schedule.arrange(TODAY, self.owner)
        self.assertEqual([day for _key, _code, day, _end in self.plan(cake)][1], TODAY)

    def test_when_the_breakdown_is_closed_arrange_plans_the_stage_again(self):
        order = self.cake_order()
        job = self.down()
        schedule.arrange(TODAY, self.owner)
        self.assertEqual([day for _key, _code, day, _end in self.plan(order)], [None, None])
        maintenance.close_job(job, self.owner)
        schedule.arrange(TODAY, self.owner)
        self.assertEqual([day for _key, _code, day, _end in self.plan(order)], [TODAY, TODAY])
        self.assertIsNone(next(row for row in schedule.grid(TODAY)["orders"] if row["order"].pk == order.pk)["waiting"])

    def test_a_stage_cannot_be_moved_onto_a_down_centre_and_a_move_to_a_day_leaves_it_waiting(self):
        bread = self.bread_order()
        schedule.arrange(TODAY, self.owner)
        spare = self.centre("OVEN2", "الفرن الصغير", "Small oven")
        self.down(spare)
        with self.assertRaises(ValidationError) as refused:
            schedule.move_order(bread, self.owner, step_index=0, centre=spare)
        self.assertIn("واقف للصيانة", " ".join(refused.exception.messages))
        with self.assertRaises(ValidationError) as refused_en:
            schedule.move_order(bread, self.owner, step_index=0, centre=spare, lang="en")
        self.assertIn("down for maintenance", " ".join(refused_en.exception.messages))
        self.assertEqual(self.plan(bread)[0][1], "OVEN")                                    # nothing moved
        cake = self.cake_order()
        self.down()
        result = schedule.move_order(cake, self.owner, day=TODAY + timedelta(days=1))
        self.assertEqual([day for _key, _code, day, _end in self.plan(cake)], [None, None])
        self.assertEqual([(row["centre"], row["index"]) for row in result["waiting"]], [(self.mixer, 0)])
        self.assertEqual(result["warnings"], [])

    def test_the_load_counts_a_stage_on_a_down_centre_apart_and_never_against_a_day(self):
        self.bread_order(due=TODAY)
        self.breakdown()                                                                    # the oven is down
        block = next(b for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk)
        today = block["days"][0]
        self.assertEqual((today["minutes"], today["capacity"], today["over"], today["down"]), (D("0"), D("0"), False, True))
        self.assertEqual(block["waiting"], D("45.00"))
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("manufacturing:work_centres"), {"lang": "ar"}), "data-load-waiting")


# ---------------------------------------------------------------------------------------------------------------
# F12: no start on a down centre
# ---------------------------------------------------------------------------------------------------------------


class StartTests(StoppedPlant):
    def setUp(self):
        super().setUp()
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=TODAY)

    def page(self, user=None, lang="ar"):
        self.client.force_login(user or self.worker)
        return self.client.get(self.floor_url, {"lang": lang})

    def test_the_floor_offers_no_start_on_a_down_centre_and_says_why_in_both_languages(self):
        cake, bread = self.cake_order(), self.bread_order()
        page = self.page().content.decode()
        self.assertEqual(page.count("data-floor-start"), 2)
        self.down()
        page = self.page().content.decode()
        card = page[page.index(f'data-floor-card="{cake.pk}"'):]
        card = card[:card.index("</article>")]
        self.assertNotIn("data-floor-start", card)
        self.assertIn("data-floor-stopped", card)
        self.assertIn("المركز واقف للصيانة", card)
        self.assertIn(CAUSE, card)
        other = page[page.index(f'data-floor-card="{bread.pk}"'):]
        self.assertIn("data-floor-start", other[:other.index("</article>")])                 # the oven runs: its stage still starts
        english = self.page(lang="en").content.decode()
        self.assertIn("The work centre is down for maintenance", english)

    def test_the_service_and_a_direct_post_refuse_the_start_in_both_languages(self):
        cake = self.cake_order()
        self.down()
        before = self.state(cake)
        with self.assertRaises(ValidationError) as refused:
            floor.start(cake, self.worker)
        self.assertIn("واقف للصيانة", " ".join(refused.exception.messages))
        self.assertIn("الخلاط", " ".join(refused.exception.messages))
        with self.assertRaises(ValidationError) as refused_en:
            floor.start(cake, self.worker, lang="en")
        self.assertIn("down for maintenance", " ".join(refused_en.exception.messages))
        self.client.force_login(self.worker)
        response = self.client.post(self.floor_url, {"action": "start", "order": cake.pk, "stage": "0", "lang": "ar"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-floor-card-error")
        self.assertContains(response, "واقف للصيانة")
        self.assertEqual(self.state(cake), before)                                          # nothing was written

    def test_a_stage_started_before_the_breakdown_is_still_finished_and_its_materials_issued(self):
        cake = self.cake_order()
        floor.start(cake, self.worker)
        self.down()
        page = self.page().content.decode()
        card = page[page.index(f'data-floor-card="{cake.pk}"'):]
        self.assertIn("data-floor-done", card[:card.index("</article>")])                    # the work in progress is not trapped
        floor.done(cake, self.worker)
        cake.refresh_from_db()
        self.assertEqual((cake.status, cake.stage_index), (OrderStatus.IN_PROGRESS, 1))
        self.assertEqual(orders.issued_value(cake), D("20.00"))                              # one batch: 1 kg of flour at 20.00, as always

    def test_a_running_centre_starts_as_before(self):
        bread = self.bread_order()
        self.down()
        step = floor.start(bread, self.worker)
        self.assertIsNotNone(step.started_at)

    def test_someone_without_the_floor_permission_gets_the_same_refusal_as_before(self):
        cake = self.cake_order()
        self.down()
        clerk = limited("stop_clerk", "inventory.view_stock")
        with self.assertRaises(PermissionDenied):
            floor.start(cake, clerk)
        self.client.force_login(clerk)
        self.assertEqual(self.client.post(self.floor_url, {"action": "start", "order": cake.pk, "stage": "0"}).status_code, 403)

    def test_another_entitys_worker_still_cannot_reach_the_order(self):
        cake = self.cake_order()
        self.down()
        branch = save_entity({"code": "BRS", "name_ar": "فرع أكتوبر", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        make_location(location_code="BR-PLANT-S", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        outsider = person(RoleCode.PRODUCTION_WORKER, "stop_branch_worker")
        EntityMembership.objects.create(user=outsider, entity=branch, is_default=True)
        self.client.force_login(outsider)
        self.assertNotIn(f'data-floor-card="{cake.pk}"', self.client.get(self.floor_url).content.decode())
        self.assertEqual(self.client.post(self.floor_url, {"action": "start", "order": cake.pk, "stage": "0"}).status_code, 404)

    def test_the_order_page_offers_no_recording_of_a_stage_on_a_down_centre_that_never_started(self):
        cake = self.cake_order()
        self.down()
        url = reverse("manufacturing:order", args=[cake.pk])
        self.client.force_login(self.owner)
        page = self.client.get(url, {"lang": "ar"})
        self.assertNotContains(page, "data-advance")
        self.assertContains(page, "data-stage-stopped")
        self.assertContains(page, "المركز واقف للصيانة")
        self.assertContains(self.client.get(url, {"lang": "en"}), "The work centre is down for maintenance")
        before = self.state(cake)
        with self.assertRaises(ValidationError) as refused:
            orders.advance(cake, self.owner)
        self.assertIn("واقف للصيانة", " ".join(refused.exception.messages))
        response = self.client.post(url, {"action": "advance", "lang": "ar"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-order-error")
        self.assertEqual(self.state(cake), before)                                          # no stage, no issue, no stock operation
        # Started on the floor before the machine stopped: the supervisor records it as before.
        maintenance.close_job(maintenance.open_job_of(self.mixer), self.owner)
        floor.start(cake, self.worker)
        self.down()
        self.assertContains(self.client.get(url, {"lang": "ar"}), "data-advance")
        orders.advance(cake, self.owner, minutes=30)
        cake.refresh_from_db()
        self.assertEqual((cake.status, cake.stage_index, orders.issued_value(cake)), (OrderStatus.IN_PROGRESS, 1, D("20.00")))

    def test_a_stage_sent_to_an_outside_supplier_while_its_centre_is_down_is_closed_by_its_return(self):
        cake = self.cake_order()
        orders.advance(cake, self.owner, minutes=30)                                         # mixed; the baking is next, on the oven
        self.breakdown()                                                                    # the oven is down
        job = outside.send(cake, self.owner, supplier=self.technician, unit_price="2")       # the way round a down machine still works
        outside.receive(job, self.owner, good="10")
        cake.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual((job.status, cake.stage_index, cake.stage_logs.count()), (OutsideStatus.BACK, 2, 2))
