"""Manufacturing F6b (HG-134): the production schedule, «رتّب», moving an order, delay warnings.

The oven works 8 hours a day (480 minutes). Three orders need 90, 180 and 300 minutes of oven time and are due in two days. The
test days are computed from the test clock: ``DAY1`` is the first Thursday after today, a Friday holiday follows it, so ``DAY2``
(the second working day) is the Saturday after. «رتّب» puts the 90 and the 180 on day 1 (270 minutes) and the 300, which does
not fit the 210 left, on day 2.
"""

import pathlib
from datetime import timedelta
from decimal import Decimal as D

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from attendance.models import Holiday
from hesba_testing.factories import make_customer, make_item, make_location
from orders import services as sales
from orders.models import Status
from permissions.models import RoleCode
from permissions.services import user_has_permission
from settings_core import capabilities as caps

from . import make_to_order, orders, schedule, services, workcentres
from .models import OrderStatus, OrderStep, ProductionOrder, WorkCentre
from .tests import TODAY, person
from .tests_workcentres import RoutedPlant


def first_thursday():
    day = TODAY + timedelta(days=1)
    while day.weekday() != 3:
        day += timedelta(days=1)
    return day


DAY1 = first_thursday()
FRIDAY = DAY1 + timedelta(days=1)
DAY2 = DAY1 + timedelta(days=2)       # Saturday: the second working day
DAY3, DAY4 = DAY1 + timedelta(days=3), DAY1 + timedelta(days=4)


class SchedulePlant(RoutedPlant):
    """The reference plant of F6a (mixer, oven) plus recipes that need only the oven, one per minutes-per-batch."""

    def setUp(self):
        super().setUp()
        Holiday.objects.create(date=FRIDAY, name="الجمعة")
        self.bakers = {}

    def baker(self, minutes):
        """A recipe whose only stage is baking on the oven, ``minutes`` for one batch of 10."""

        if minutes not in self.bakers:
            item = make_item(item_code=f"FG-B{minutes}", item_name=f"خبيز {minutes}", is_stock_tracked=True)
            recipe = services.save_recipe({"product": item, "output_quantity": "10"}, [(self.flour, "1")], self.owner)
            self.route(recipe, [("خَبز", "Baking", self.oven, str(minutes), "0")])
            self.bakers[minutes] = recipe
        return self.bakers[minutes]

    def bake(self, minutes, due=None, **extra):
        return orders.create_order({"recipe": self.baker(minutes), "location": self.plant, "quantity": "10", "due_date": due, **extra}, self.owner)

    def three(self, due=DAY2):
        return self.bake(90, due), self.bake(180, due), self.bake(300, due)

    def totals(self):
        return {key: value for key, value in schedule.planned_totals().items() if key[0] == self.oven.pk}

    def span(self, order):
        step = order.steps.get(index=0)
        return schedule.day_of(step.planned_start), schedule.end_day(step)


class ArrangeTests(SchedulePlant):
    def test_steps_are_made_with_the_order_and_have_no_dates_yet(self):
        order = self.cake_order("20")
        steps = list(order.steps.all())
        self.assertEqual([(s.index, s.stage_key, s.work_centre_id, s.planned_minutes) for s in steps],
                         [(0, "mixing", self.mixer.pk, D("60.00")), (1, "baking", self.oven.pk, D("90.00"))])
        self.assertTrue(all(s.planned_start is None and s.planned_end is None and s.finished_at is None for s in steps))

    def test_three_orders_on_the_oven_two_fit_day_one_and_the_third_goes_to_day_two(self):
        first, second, third = self.three()
        self.assertEqual(schedule.arrange(DAY1, self.owner), {"orders": 3, "steps": 3, "waiting": []})
        self.assertEqual((self.span(first), self.span(second), self.span(third)), ((DAY1, DAY1), (DAY1, DAY1), (DAY2, DAY2)))
        self.assertEqual(self.totals(), {(self.oven.pk, DAY1): D("270.00"), (self.oven.pk, DAY2): D("300.00")})
        self.assertNotIn((self.oven.pk, FRIDAY), self.totals())                       # the Friday holiday is skipped
        self.assertEqual(schedule.late_orders([first, second, third], TODAY), {})     # all three end by the due day

    def test_a_holiday_is_skipped_and_a_second_start_day_on_the_holiday_moves_on(self):
        order = self.bake(90)
        schedule.arrange(FRIDAY, self.owner)                                           # asking for the holiday gives the next working day
        self.assertEqual(self.span(order), (DAY2, DAY2))

    def test_orders_are_taken_by_due_date_then_the_users_rank_then_the_order_they_were_opened(self):
        later = self.bake(300, DAY2)                                                   # opened first, due later
        sooner = self.bake(300, DAY1)
        none = self.bake(300)                                                          # no date goes last
        schedule.arrange(DAY1, self.owner)
        self.assertEqual((self.span(sooner)[0], self.span(later)[0], self.span(none)[0]), (DAY1, DAY2, DAY3))
        a, b = self.bake(90, DAY2), self.bake(90, DAY2)                                # same date: the lower rank first
        self.assertEqual(schedule.set_rank(b, "1", self.owner), 1)
        self.assertEqual(ProductionOrder.objects.get(pk=b.pk).schedule_rank, 1)
        schedule.arrange(DAY1, self.owner)
        self.assertLess(b.steps.get().planned_start, a.steps.get().planned_start)
        schedule.set_rank(b, "", self.owner)                                           # blank clears it
        self.assertIsNone(ProductionOrder.objects.get(pk=b.pk).schedule_rank)
        for bad in ("-1", "x", "1.5", "1234567"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                schedule.set_rank(b, bad, self.owner)

    def test_deliver_by_of_the_sales_order_comes_before_the_orders_own_date(self):
        caps.set_capability_enabled(self.profile, "quotations", True)
        customer = make_customer(customer_code="CUS-F6B", name="مخبز النور", phone="01001234568")
        quote = sales.create_quote({"customer": customer, "deliver_by": TODAY + timedelta(days=1)}, self.owner)
        sales.add_line(quote, self.cake, self.owner, quantity="20", unit_price="25")
        sale = sales.move(quote, Status.CONFIRMED, self.owner)
        order = make_to_order.make(sale.lines.get(), self.owner, {})
        ProductionOrder.objects.filter(pk=order.pk).update(due_date=TODAY + timedelta(days=30))     # the order's own date is far off
        order = ProductionOrder.objects.select_related("sales_order_line__order").get(pk=order.pk)
        self.assertEqual(schedule.effective_due(order), TODAY + timedelta(days=1))
        schedule.arrange(DAY1 + timedelta(days=7), self.owner)                         # planned well after the promised delivery
        self.assertEqual(schedule.late_orders([order], TODAY), {order.pk: "planned"})

    def test_a_stage_longer_than_a_day_takes_whole_days_and_is_never_split(self):
        long = self.bake(1200)                                                         # 1200 minutes: three oven days of 480 (480, 480, 240)
        schedule.arrange(DAY1, self.owner)
        self.assertEqual(self.span(long), (DAY1, DAY3))                                # Thursday, Saturday, Sunday: the Friday holiday is skipped
        self.assertEqual(self.totals(), {(self.oven.pk, DAY1): D("480.00"), (self.oven.pk, DAY2): D("480.00"), (self.oven.pk, DAY3): D("240.00")})
        step = long.steps.get()
        self.assertEqual(schedule.day_of(step.planned_start), DAY1)
        self.assertEqual(schedule.end_day(step), DAY3)

    def test_a_long_stage_starts_on_a_day_the_oven_has_not_touched_and_the_next_stage_waits_for_it(self):
        short = self.bake(90, DAY1)
        long = self.bake(1200, DAY2)
        after = self.bake(30, DAY3)
        schedule.arrange(DAY1, self.owner)
        self.assertEqual(self.span(short), (DAY1, DAY1))
        self.assertEqual(self.span(long), (DAY2, DAY4))                                # not squeezed into Thursday's 390 free minutes: whole days from Saturday
        self.assertEqual(self.span(after)[0], DAY4 + timedelta(days=1))                # the oven is next free the working day after the block
        self.assertEqual(schedule.late_orders([long], TODAY), {long.pk: "planned"})    # due on the Saturday, planned to end on the Monday

    def test_a_stage_that_does_not_fit_the_rest_of_the_day_is_not_split(self):
        self.bake(300, DAY2)
        other = self.bake(300, DAY2)                                                   # 300 + 300 > 480: the second starts the next working day, whole
        schedule.arrange(DAY1, self.owner)
        step = other.steps.get()
        self.assertEqual((schedule.day_of(step.planned_start), schedule.end_day(step)), (DAY2, DAY2))
        self.assertEqual(self.totals()[(self.oven.pk, DAY2)], D("300.00"))
        self.assertEqual(self.totals()[(self.oven.pk, DAY1)], D("300.00"))

    def test_a_routed_order_runs_its_stages_in_order_each_on_its_own_centre(self):
        order = self.cake_order("20", due=DAY2)                                        # mixing 60 on the mixer, then baking 90 on the oven
        schedule.arrange(DAY1, self.owner)
        mixing, baking = order.steps.get(index=0), order.steps.get(index=1)
        self.assertEqual((schedule.day_of(mixing.planned_start), schedule.day_of(baking.planned_start)), (DAY1, DAY1))
        self.assertEqual(baking.planned_start, mixing.planned_end)                     # baking starts when mixing ends
        self.assertEqual(baking.planned_end - baking.planned_start, timedelta(minutes=90))

    def test_an_order_with_no_work_centre_gets_one_day_per_stage_and_no_capacity(self):
        plain = services.save_recipe({"product": make_item(item_code="FG-PLN", item_name="عيش", is_stock_tracked=True), "output_quantity": "10"}, [(self.flour, "1")], self.owner)
        order = orders.create_order({"recipe": plain, "location": self.plant, "quantity": "10"}, self.owner)
        steps = list(order.steps.all())
        self.assertEqual(len(steps), len(order.stages))
        self.assertTrue(all(s.work_centre_id is None and s.planned_minutes is None for s in steps))
        schedule.arrange(DAY1, self.owner)
        days = [(schedule.day_of(s.planned_start), schedule.end_day(s)) for s in order.steps.all()]
        day5 = DAY4 + timedelta(days=1)                                                 # F5 (HG-136): the food list has five stages now
        self.assertEqual(days, [(DAY1, DAY1), (DAY2, DAY2), (DAY3, DAY3), (DAY4, DAY4), (day5, day5)])   # one working day each, the Friday skipped
        self.assertEqual(self.totals(), {})                                            # it loads no work centre

    def test_arranging_leaves_finished_stages_alone(self):
        order = self.cake_order("20", due=DAY2)
        orders.advance(order, self.owner)                                              # mixing is done now
        done = order.steps.get(index=0)
        self.assertIsNotNone(done.finished_at)
        schedule.arrange(DAY1, self.owner)
        again = order.steps.get(index=0)
        self.assertIsNone(again.planned_start)                                         # a finished stage is not planned again
        self.assertIsNotNone(order.steps.get(index=1).planned_start)

    def test_a_day_with_no_working_day_in_a_year_is_refused(self):
        Holiday.objects.bulk_create([Holiday(date=DAY1 + timedelta(days=n), name="h") for n in range(0, 400) if DAY1 + timedelta(days=n) != FRIDAY])
        self.bake(90)
        with self.assertRaises(ValidationError):
            schedule.arrange(DAY1, self.owner)
        self.assertFalse(OrderStep.objects.filter(planned_start__isnull=False).exists())


class MoveTests(SchedulePlant):
    def setUp(self):
        super().setUp()
        self.first, self.second, self.third = self.three()
        schedule.arrange(DAY1, self.owner)

    def test_moving_the_third_order_back_to_day_one_is_allowed_and_warns(self):
        result = schedule.move_order(self.third, self.owner, day=DAY1)
        self.assertEqual(self.span(self.third), (DAY1, DAY1))
        self.assertEqual(self.totals(), {(self.oven.pk, DAY1): D("570.00")})
        (warning,) = result["warnings"]
        self.assertEqual((warning["centre"], warning["day"], warning["minutes"], warning["capacity"]), (self.oven, DAY1, D("570.00"), D("480.00")))

    def test_a_move_inside_capacity_has_no_warning(self):
        self.assertEqual(schedule.move_order(self.second, self.owner, day=DAY2)["warnings"], [])        # 180 + 300 = 480, exactly full
        self.assertEqual(self.span(self.second), (DAY2, DAY2))
        self.assertEqual(self.totals()[(self.oven.pk, DAY2)], D("480.00"))

    def test_moving_onto_a_holiday_is_allowed_with_a_warning_because_it_has_no_capacity(self):
        result = schedule.move_order(self.first, self.owner, day=FRIDAY)
        self.assertEqual([(w["day"], w["minutes"], w["capacity"]) for w in result["warnings"]], [(FRIDAY, D("90.00"), D("0"))])

    def test_a_move_lays_the_orders_later_stages_forward_from_the_day(self):
        order = self.cake_order("20", due=DAY4)
        schedule.arrange(DAY1, self.owner)
        schedule.move_order(order, self.owner, day=DAY3)
        mixing, baking = order.steps.get(index=0), order.steps.get(index=1)
        self.assertEqual((schedule.day_of(mixing.planned_start), schedule.day_of(baking.planned_start)), (DAY3, DAY3))
        self.assertEqual(baking.planned_start, mixing.planned_end)

    def test_a_step_moves_to_another_centre_of_the_same_kind_and_not_to_another_kind(self):
        oven2 = self.centre("OVEN2", "الفرن التاني", "Oven 2", "100.00")
        line = workcentres.save_work_centre({"code": "PACK", "name_ar": "التعبئة", "kind": "manual", "hours_per_day": "8"}, self.owner)
        result = schedule.move_order(self.third, self.owner, step_index=0, centre=oven2)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(self.third.steps.get().work_centre, oven2)
        self.assertEqual(self.totals(), {(self.oven.pk, DAY1): D("270.00")})           # the oven lost the 300
        with self.assertRaisesMessage(ValidationError, "نفس نوع"):
            schedule.move_order(self.third, self.owner, step_index=0, centre=line)
        self.assertEqual(self.third.steps.get().work_centre, oven2)
        oven2.active = False
        oven2.save()
        with self.assertRaises(ValidationError):
            schedule.move_order(self.first, self.owner, step_index=0, centre=oven2)

    def test_a_move_changes_where_and_when_but_never_the_cost_path(self):
        oven2 = self.centre("OVEN2", "الفرن التاني", "Oven 2", "1.00")
        schedule.move_order(self.third, self.owner, step_index=0, centre=oven2)
        self.third.refresh_from_db()
        self.assertEqual(self.third.stages[0][3], self.oven.pk)                        # the stage still names (and is priced at) its first centre
        self.assertEqual(workcentres.plan_rows(self.third)[0]["centre"], self.oven)

    def test_refusals(self):
        with self.assertRaisesMessage(ValidationError, "النهارده أو يوم بعده"):
            schedule.move_order(self.third, self.owner, day=TODAY - timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, "اختار يوم أو مركز"):
            schedule.move_order(self.third, self.owner)
        with self.assertRaisesMessage(ValidationError, "مش على الأمر"):
            schedule.move_order(self.third, self.owner, step_index=7, centre=self.oven)
        orders.cancel(self.third, self.owner, "ملغي")
        with self.assertRaisesMessage(ValidationError, "مش مفتوح"):
            schedule.move_order(self.third, self.owner, day=DAY1)
        for step in OrderStep.objects.filter(order=self.third):
            self.assertIsNotNone(step.planned_start)                                   # nothing was changed

    def test_an_order_with_no_centre_cannot_change_centre_but_can_change_day(self):
        plain = services.save_recipe({"product": make_item(item_code="FG-PLN", item_name="عيش", is_stock_tracked=True), "output_quantity": "10"}, [(self.flour, "1")], self.owner)
        order = orders.create_order({"recipe": plain, "location": self.plant, "quantity": "10"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مالهاش مركز"):
            schedule.move_order(order, self.owner, step_index=0, centre=self.oven)
        schedule.move_order(order, self.owner, day=DAY2)
        self.assertEqual(schedule.day_of(order.steps.get(index=0).planned_start), DAY2)

    def test_the_move_forms_post_through_the_screen_and_the_warning_is_shown(self):
        self.client.force_login(self.owner)
        url = reverse("manufacturing:schedule")
        reply = self.client.post(url, {"action": "move", "order": self.third.pk, "day": DAY1.isoformat(), "lang": "ar"})
        self.assertRedirects(reply, f"{url}?lang=ar", fetch_redirect_response=False)
        text = [str(m) for m in get_messages(reply.wsgi_request)]
        self.assertTrue(any("570" in m and "480" in m for m in text), text)
        page = self.client.get(url, {"lang": "ar", "start": DAY1.isoformat()})
        self.assertContains(page, "data-over")
        self.assertContains(page, "570 من 480")
        back = self.client.post(url, {"action": "move", "order": self.third.pk, "day": DAY2.isoformat(), "back": "order", "lang": "en"})
        self.assertRedirects(back, f"{reverse('manufacturing:order', args=[self.third.pk])}?lang=en", fetch_redirect_response=False)
        bad = self.client.post(url, {"action": "move", "order": self.third.pk, "day": "2026-99-99", "lang": "ar"})
        self.assertEqual(bad.status_code, 302)
        self.assertEqual(self.span(self.third), (DAY2, DAY2))                          # the bad date changed nothing


class LateTests(SchedulePlant):
    def test_an_order_planned_to_end_after_its_due_date_is_late_on_the_board_the_tile_and_the_schedule(self):
        first, second, third = self.three(due=DAY1)                                    # all due Thursday; only 480 minutes fit that day
        schedule.arrange(DAY1, self.owner)
        late = schedule.late_orders([first, second, third], TODAY)
        self.assertEqual(late, {third.pk: "planned"})
        self.client.force_login(self.owner)
        board = self.client.get(reverse("manufacturing:orders"), {"lang": "ar"})
        self.assertEqual(board.context["late_count"], 1)
        self.assertContains(board, "data-late-orders")
        self.assertEqual(board.content.decode().count("is-late"), 1)
        self.assertContains(board, f'class="mfg-card is-late" href="{reverse("manufacturing:order", args=[third.pk])}')
        home = self.client.get(reverse("manufacturing:home"), {"lang": "ar"})
        self.assertEqual(home.context["late_count"], 1)                                # the dashboard tile
        grid = self.client.get(reverse("manufacturing:schedule"), {"lang": "ar", "start": DAY1.isoformat()})
        self.assertEqual(grid.context["data"]["late_count"], 1)
        self.assertContains(grid, 'data-late="planned"')
        self.assertContains(grid, "is-late")
        detail = self.client.get(reverse("manufacturing:order", args=[third.pk]), {"lang": "en"})
        self.assertContains(detail, 'data-late-reason="planned"')
        self.assertContains(detail, DAY2.isoformat())
        self.assertNotContains(self.client.get(reverse("manufacturing:order", args=[first.pk])), "data-late-reason")

    def test_moving_it_to_a_day_that_ends_in_time_clears_the_flag_and_the_old_rule_still_holds(self):
        first, second, third = self.three(due=DAY2)
        schedule.arrange(DAY1, self.owner)
        self.assertEqual(schedule.late_orders([third], TODAY), {})
        schedule.move_order(third, self.owner, day=DAY3)
        self.assertEqual(schedule.late_orders([third], TODAY), {third.pk: "planned"})
        old = self.bake(90, TODAY - timedelta(days=1))                                 # overdue with no plan at all: the board's own rule
        self.assertEqual(schedule.late_orders([old], TODAY), {old.pk: "overdue"})
        done = ProductionOrder.objects.get(pk=old.pk)
        done.status = OrderStatus.DONE
        self.assertEqual(schedule.late_orders([done], TODAY), {})

    def test_the_dashboard_counts_late_orders_the_same_way_as_the_factory_home_and_the_schedule(self):
        """Manufacturing video finding F1: the dashboard said one late order where the factory home and the schedule said two.
        It counted only the orders past their own due date; an order planned to end after its date was left out."""

        from reports.activity_panel import WORDS, _production

        first, second, third = self.three(due=DAY2)
        schedule.arrange(DAY1, self.owner)
        schedule.move_order(third, self.owner, day=DAY3)                               # now planned to end after its due day
        self.bake(90, TODAY - timedelta(days=1))                                       # past its date, with no plan
        open_orders = ProductionOrder.objects.filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS))
        expected = len(schedule.late_orders(open_orders, TODAY))
        self.assertEqual(expected, 2)
        tile = {t["key"]: t for t in _production(WORDS["ar"], TODAY)[0]}["open_orders_mfg"]
        self.assertEqual((tile["value"], tile["sub"], tile["tone"]), (4, f"2 {WORDS['ar']['late_orders']}", "bad"))

    def test_an_order_with_a_date_and_no_plan_is_late_only_when_overdue(self):
        order = self.bake(90, DAY1)
        self.assertEqual(schedule.late_orders([order], TODAY), {})


class LoadTests(SchedulePlant):
    def test_the_load_reads_the_planned_days_once_they_exist(self):
        self.three(due=None)
        block = lambda: next(b for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk)  # noqa: E731
        self.assertEqual((block()["unscheduled"], sum(r["minutes"] for r in block()["days"])), (D("570"), D("0")))
        schedule.arrange(DAY1, self.owner)
        after = block()
        self.assertEqual(after["unscheduled"], D("0"))
        by_day = {row["date"]: row for row in after["days"]}
        self.assertEqual((by_day[DAY1]["minutes"], by_day[DAY2]["minutes"], by_day[DAY1]["over"], by_day[DAY2]["over"]), (D("270"), D("300"), False, False))
        self.assertEqual(by_day[FRIDAY]["minutes"], D("0"))
        self.assertTrue(by_day[FRIDAY]["holiday"])

    def test_a_move_over_capacity_shows_on_the_load_table(self):
        first, second, third = self.three(due=None)
        schedule.arrange(DAY1, self.owner)
        schedule.move_order(third, self.owner, day=DAY1)
        row = next(r for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk for r in b["days"] if r["date"] == DAY1)
        self.assertEqual((row["minutes"], row["capacity"], row["over"]), (D("570"), D("480"), True))

    def test_a_long_stage_spreads_its_minutes_over_its_days(self):
        self.bake(1200, due=None)
        schedule.arrange(DAY1, self.owner)
        days = {r["date"]: r["minutes"] for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk for r in b["days"]}
        self.assertEqual((days[DAY1], days[DAY2], days[DAY3]), (D("480"), D("480"), D("240")))

    def test_the_days_of_a_long_stage_sum_to_its_minutes_when_the_centres_hours_change(self):
        self.bake(1200, due=None)
        schedule.arrange(DAY1, self.owner)                                          # three working days at 480 a day
        oven_days = lambda: {r["date"]: r["minutes"] for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk for r in b["days"]}  # noqa: E731
        WorkCentre.objects.filter(pk=self.oven.pk).update(hours_per_day=D("16"))    # longer days, the same planned dates
        days = oven_days()
        self.assertEqual((days[DAY1], days[DAY2], days[DAY3]), (D("960"), D("240"), D("0")))   # 1,200 in all, not 960 + 960
        WorkCentre.objects.filter(pk=self.oven.pk).update(hours_per_day=D("4"))     # shorter days: what does not fit lands on the last, over capacity
        days = oven_days()
        self.assertEqual((days[DAY1], days[DAY2], days[DAY3]), (D("240"), D("240"), D("720")))
        row = next(r for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk for r in b["days"] if r["date"] == DAY3)
        self.assertTrue(row["over"])

    def test_an_order_not_yet_planned_keeps_the_due_date_rule(self):
        self.bake(300, DAY2)
        row = next(r for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk for r in b["days"] if r["date"] == DAY2)
        self.assertEqual(row["minutes"], D("300"))


class AdvanceTests(SchedulePlant):
    def test_advancing_a_stage_fills_finished_at(self):
        order = self.cake_order("20")
        orders.advance(order, self.owner)
        mixing, baking = order.steps.get(index=0), order.steps.get(index=1)
        self.assertIsNotNone(mixing.finished_at)
        self.assertIsNone(baking.finished_at)
        self.assertIsNone(mixing.started_at)                                           # the floor screen (F8) fills this one
        orders.advance(order, self.owner)
        self.assertIsNotNone(order.steps.get(index=1).finished_at)

    def test_an_order_from_before_the_schedule_gets_its_steps_lazily_and_finished_stages_keep_their_time(self):
        order = self.cake_order("20")
        orders.advance(order, self.owner)
        log = order.stage_logs.get()
        order.steps.all().delete()                                                      # as an order opened before F6b
        self.assertEqual(order.steps.count(), 0)
        schedule.arrange(DAY1, self.owner)                                              # asks for the steps: made now, no data migration
        steps = {s.index: s for s in order.steps.all()}
        self.assertEqual(set(steps), {0, 1})
        self.assertEqual(steps[0].finished_at, log.done_at)                             # a stage done before keeps the time its log has
        self.assertIsNone(steps[0].planned_start)
        self.assertIsNotNone(steps[1].planned_start)
        order.steps.all().delete()
        orders.advance(order, self.owner)                                               # advancing an old order makes them too
        self.assertIsNotNone(order.steps.get(index=1).finished_at)

    def test_the_order_page_shows_its_planned_dates_in_both_languages(self):
        order = self.cake_order("20", due=DAY2)
        self.client.force_login(self.owner)
        before = self.client.get(reverse("manufacturing:order", args=[order.pk]))
        self.assertContains(before, "data-unplanned")
        self.assertEqual(order.steps.count(), 2)
        schedule.arrange(DAY1, self.owner)
        for lang in ("ar", "en"):
            page = self.client.get(reverse("manufacturing:order", args=[order.pk]), {"lang": lang})
            self.assertContains(page, 'data-plan-stage="mixing"')
            self.assertContains(page, f'data-plan-start>{DAY1.isoformat()}<')
            self.assertContains(page, "data-move-order")
            self.assertNotContains(page, "data-unplanned")
        self.assertContains(self.client.get(reverse("manufacturing:order", args=[order.pk]), {"lang": "en"}), "Move the stage to another centre")


class FinishedOrderWordsTests(SchedulePlant):
    """Manufacturing video findings F2 and F3: an order with every stage done read «مخطط» at 100% on the factory home, and a
    finished order's page said «الأمر ده لسه مترتبش؛ اضغط «رتّب»…»."""

    def test_an_order_with_every_stage_done_reads_ready_to_finish_not_planned(self):
        order = self.cake_order("20")
        for _stage in order.stages:
            order = orders.advance(order, self.owner)
        self.client.force_login(self.owner)
        for lang, word in (("ar", "جاهز للإنهاء"), ("en", "ready to finish")):
            page = self.client.get(reverse("manufacturing:home"), {"lang": lang})
            self.assertContains(page, "data-ready-to-finish")
            self.assertContains(page, word)
        untouched = self.cake_order("10")                                               # not started: still «مخطط»
        self.assertEqual(untouched.status, OrderStatus.PLANNED)
        self.assertContains(self.client.get(reverse("manufacturing:home")), "مخطط")

    def test_a_finished_order_never_asks_to_be_arranged(self):
        order = self.cake_order("20")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("manufacturing:order", args=[order.pk])), "data-unplanned")  # open, no plan: asked
        for _stage in order.stages:
            order = orders.advance(order, self.owner)
        order = orders.finish(order, self.owner)
        self.assertEqual(order.status, OrderStatus.DONE)
        for lang in ("ar", "en"):
            page = self.client.get(reverse("manufacturing:order", args=[order.pk]), {"lang": lang})
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, "data-unplanned")


class NothingMovesTests(SchedulePlant):
    def test_arranging_and_moving_change_no_stock_cost_or_journal(self):
        started = self.cake_order("20", due=DAY2)
        orders.advance(started, self.owner)                                             # the start issues the materials (HG-036), as before F6b
        other = self.bake(300, DAY2)
        oven2 = self.centre("OVEN2", "الفرن التاني", "Oven 2", "100.00")
        before = (self.counts(), self.position(self.cake), self.on_hand(self.flour, self.sugar, self.box), started.labor_cost, started.overhead_cost)
        schedule.arrange(DAY1, self.owner)
        schedule.move_order(other, self.owner, day=DAY1)
        schedule.move_order(other, self.owner, step_index=0, centre=oven2)
        schedule.set_rank(other, "3", self.owner)
        started.refresh_from_db()
        after = (self.counts(), self.position(self.cake), self.on_hand(self.flour, self.sugar, self.box), started.labor_cost, started.overhead_cost)
        self.assertEqual(before, after)
        self.client.force_login(self.owner)
        self.client.post(reverse("manufacturing:schedule"), {"action": "arrange", "start": DAY1.isoformat()})
        self.client.get(reverse("manufacturing:schedule"))
        self.assertEqual(self.counts(), before[0])


class ScreenTests(SchedulePlant):
    def test_the_schedule_screen_shows_a_row_per_centre_with_bars_in_both_languages(self):
        first, second, third = self.three()
        schedule.arrange(DAY1, self.owner)
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            page = self.client.get(reverse("manufacturing:schedule"), {"lang": lang, "start": DAY1.isoformat()})
            self.assertEqual(page.status_code, 200)
            for marker in ("data-schedule-grid", 'data-lane="OVEN"', 'data-lane="MIX"', 'data-lane="none"', f'data-cell-day="{DAY1.isoformat()}"',
                           f'data-bar="{first.number}"', f'data-bar="{third.number}"', "data-schedule-days", f'data-day-list="{DAY1.isoformat()}"', "data-arrange"):
                self.assertContains(page, marker)
            self.assertContains(page, "270 ")
        self.assertContains(self.client.get(reverse("manufacturing:schedule"), {"lang": "en", "start": DAY1.isoformat()}), "Production schedule")
        self.assertContains(self.client.get(reverse("manufacturing:schedule"), {"lang": "ar"}), "جدول الإنتاج")
        self.assertContains(self.client.get(reverse("manufacturing:home")), 'data-schedule-nav')

    def test_the_phone_layout_is_a_day_list_and_the_grid_is_hidden_by_css(self):
        css = (pathlib.Path(settings.BASE_DIR) / "static/hesba/css/operations.css").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:700px){.sch-wrap{display:none}.sch-days{display:block}", css)
        self.assertIn(".sch-days{display:none", css)
        start = css.index("/* Manufacturing F6b")
        self.assertNotRegex(css[start:], r"#[0-9a-fA-F]{3,8}\b|rgb\(")               # tokens only: no colour literal

    def test_arrange_through_the_screen_and_a_filter_by_centre(self):
        self.three()
        self.client.force_login(self.owner)
        url = reverse("manufacturing:schedule")
        reply = self.client.post(url, {"action": "arrange", "start": DAY1.isoformat(), "lang": "ar"})
        self.assertRedirects(reply, f"{url}?lang=ar", fetch_redirect_response=False)
        self.assertEqual(OrderStep.objects.filter(planned_start__isnull=False).count(), 3)
        self.assertTrue(any("3 أمر" in str(m) for m in get_messages(reply.wsgi_request)))
        page = self.client.get(url, {"centre": self.oven.pk, "start": DAY1.isoformat()})
        self.assertContains(page, 'data-lane="OVEN"')
        self.assertNotContains(page, 'data-lane="MIX"')
        self.assertNotContains(page, 'data-lane="none"')
        bad = self.client.post(url, {"action": "arrange", "start": "nope", "lang": "ar"})
        self.assertEqual(bad.status_code, 302)
        self.assertEqual(OrderStep.objects.filter(planned_start__isnull=False).count(), 3)

    def test_the_new_screens_use_no_banned_word(self):
        banned = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")
        for name in ("templates/manufacturing/schedule.html", "templates/manufacturing/_plan.html", "manufacturing/schedule.py", "manufacturing/schedule_views.py"):
            text = (pathlib.Path(settings.BASE_DIR) / name).read_text(encoding="utf-8")
            for word in banned:
                with self.subTest(name=name, word=word):
                    self.assertNotIn(word, text)
        order = self.cake_order("20", due=DAY2)
        schedule.arrange(DAY1, self.owner)
        self.client.force_login(self.owner)
        for url in (reverse("manufacturing:schedule"), reverse("manufacturing:order", args=[order.pk])):
            for lang in ("ar", "en"):
                text = self.client.get(url, {"lang": lang}).content.decode()
                for word in banned:
                    self.assertNotIn(word, text)


class PermissionTests(SchedulePlant):
    def test_viewing_needs_stock_view_and_changing_needs_the_order_permission(self):
        order = self.bake(90, DAY2)
        viewers = []
        for code in RoleCode.values:
            user = person(code, f"f6b_{code}")
            if user_has_permission(user, "inventory.view_stock") and not user_has_permission(user, "inventory.adjust_stock"):
                viewers.append(user)
        self.assertTrue(viewers, "a role that can see stock but not adjust it is needed for this test")
        url = reverse("manufacturing:schedule")
        for viewer in viewers:
            self.client.force_login(viewer)
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, "data-arrange-form")                          # read-only: no «رتّب» and no move forms
            self.assertNotContains(self.client.get(reverse("manufacturing:order", args=[order.pk])), "data-move-order")
            for data in ({"action": "arrange", "start": DAY1.isoformat()}, {"action": "move", "order": order.pk, "day": DAY1.isoformat()}, {"action": "rank", "order": order.pk, "rank": "1"}):
                self.assertEqual(self.client.post(url, data).status_code, 403)
            with self.assertRaises(PermissionDenied):
                schedule.arrange(DAY1, viewer)
            with self.assertRaises(PermissionDenied):
                schedule.move_order(order, viewer, day=DAY1)
        self.assertFalse(OrderStep.objects.filter(planned_start__isnull=False).exists())
        self.client.force_login(person(RoleCode.CASHIER, "f6b_cashier_two"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_schedule_shows_no_cost(self):
        self.three()
        schedule.arrange(DAY1, self.owner)
        keeper = person(RoleCode.STOCK_KEEPER, "f6b_keeper")
        self.assertFalse(user_has_permission(keeper, "inventory.view_cost"))
        self.client.force_login(keeper)
        for lang in ("ar", "en"):
            text = self.client.get(reverse("manufacturing:schedule"), {"lang": lang, "start": DAY1.isoformat()}).content.decode()
            self.assertNotIn("120.00", text)


class EntityTests(SchedulePlant):
    def test_another_entitys_order_and_centre_are_not_offered_and_return_404(self):
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BR6B", "name_ar": "فرع العبور", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        branch_plant = make_location(location_code="BR-PLANT6B", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        far_centre = WorkCentre.objects.create(code="BR-OVEN", name_ar="فرن الفرع", kind="machine", hours_per_day=8, rate_per_hour="99.00", entity=branch)
        far = orders.create_order({"recipe": self.baker(90), "location": branch_plant, "quantity": "10", "due_date": DAY2}, self.owner)
        near = self.bake(90, DAY2)
        main = Entity.objects.get(is_main=True)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": main.pk, "next": "/dashboard/"})
        from entities.current import working_in

        with working_in(main):
            schedule.arrange(DAY1, self.owner)                                          # plans the main entity's orders only
        self.assertFalse(far.steps.exclude(planned_start=None).exists())  # F6a: the branch order took the factory's stages, none planned here
        url = reverse("manufacturing:schedule")
        page = self.client.get(url, {"start": DAY1.isoformat()})
        self.assertContains(page, f'data-bar="{near.number}"')
        self.assertNotContains(page, far.number)                                        # not offered
        self.assertNotContains(page, "BR-OVEN")
        self.assertNotContains(page, "فرن الفرع")
        self.assertEqual(self.client.get(url, {"centre": far_centre.pk}).status_code, 404)
        for data in ({"action": "move", "order": far.pk, "day": DAY1.isoformat()}, {"action": "rank", "order": far.pk, "rank": "1"},
                     {"action": "move", "order": near.pk, "step": "0", "centre": far_centre.pk}):
            self.assertEqual(self.client.post(url, data).status_code, 404)
        self.assertEqual(self.client.get(reverse("manufacturing:order", args=[far.pk])).status_code, 404)
        self.assertEqual(self.span(near), (DAY1, DAY1))                                 # nothing of the far order's plan shows up here, and the near one is untouched
        with self.assertRaises(ValidationError), working_in(main):                      # the service checks too: a far centre is not a valid new centre
            schedule.move_order(near, self.owner, step_index=0, centre=far_centre)
        self.assertEqual(near.steps.get().work_centre, self.oven)
        # In the branch the two swap places; «رتّب» there plans only the branch's order.
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        page = self.client.get(url, {"start": DAY1.isoformat()})
        self.assertContains(page, 'data-schedule-order="%s"' % far.number)
        self.assertNotContains(page, 'data-schedule-order="%s"' % near.number)
        self.assertEqual(self.client.get(url, {"centre": self.oven.pk}).status_code, 404)
        self.assertEqual(self.client.post(url, {"action": "move", "order": near.pk, "day": DAY1.isoformat()}).status_code, 404)
