"""Manufacturing F10 (HG-141): machine maintenance, planned and breakdowns, with its cost.

The bakery's oven (the plan's examples, to the piastre):

* a plan every 30 days is not due on day 30 and is due on day 31; a plan every 200 running hours is due when the stage
  minutes recorded on the oven reach 12,000;
* a breakdown from 10:00 to 14:00 is 240 minutes of downtime, and the oven's day in the schedule loses those minutes;
* 2 heating elements at 350.00 leave stock for the job: debit maintenance and repairs (5206) 700.00, credit the
  item's inventory account 700.00, and nothing on stock-count losses (5211); taking them back mirrors it;
* an outside technician's bill of 500.00 + VAT 70.00 is an ordinary purchase (570.00 owed to the supplier, 500.00 in
  general expenses, 70.00 of input VAT), and the oven's month shows 700.00 + 500.00 = 1,200.00.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from expenses.models import ExpenseCategory
from hesba_testing.factories import make_item, make_location, make_supplier, stock_in
from inventory.models import StockMovement, StockOperation, StockOperationStatus
from inventory.services import get_item_authoritative_average_cost, get_item_location_stock_quantity
from ledger import reports as gl
from ledger.models import Account, ExpenseAccount, JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from permissions.services import user_has_permission
from purchases.models import PurchaseInvoice, SupplierLedgerEntry
from purchases.services import cancel_posted_purchase_invoice, post_purchase_invoice
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled

from . import guards, maintenance, maintenance_views, orders, outside, quality, schedule, services, workcentres
from .models import MaintenanceJob, MaintenanceKind, MaintenancePart, MaintenancePlan, MaintenancePurchase
from .tests import person
from .tests_f11 import BakeryChart
from .tests_quality import BANNED, LATIN_WORD, edges, limited, sentences

TODAY = timezone.localdate()
YESTERDAY = TODAY - timedelta(days=1)
BEFORE = TODAY - timedelta(days=3)
BANNED_WORDS = BANNED + ("شغلانة",)


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def lines(operation):
    """[(days after the operation's date, account code, debit, credit)] of a stock operation's journal lines."""

    Projector().rebuild()
    rows = (JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id=operation.pk)
            .select_related("account", "entry").order_by("entry__entry_date", "account__code", "debit", "credit"))
    return [((line.entry.entry_date - operation.operation_date).days, line.account.code, str(line.debit), str(line.credit)) for line in rows]


def balance(code):
    """An account's debit balance by code, after a rebuild (5206 has no control key to ask by)."""

    Projector().rebuild()
    return sum((line.debit - line.credit for line in JournalLine.objects.filter(account__code=code)), D("0.00"))


class OvenPlant(TestCase):
    ACTIVITY, SUB = "manufacturing", "food"
    MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,expenses,manufacturing"
    VAT = True
    STOCK = "110501"            # a spare part is not a recipe's product: raw materials on the manufacturing chart

    def setUp(self):
        self.profile = prepared_client(activity=self.ACTIVITY, sub_activity=self.SUB, modules=self.MODULES)
        set_capability_enabled(self.profile, "vat", self.VAT)
        self.owner = person(RoleCode.OWNER, "mnt_owner")
        self.plant = make_location(location_code="PLANT", name_ar="المصنع", name_en="Plant", is_default=True)
        self.element = make_item(item_code="SP-HEAT", item_name="سخان فرن", is_stock_tracked=True)
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق (كيلو)", is_stock_tracked=True)
        self.bread = make_item(item_code="FG-BREAD", item_name="عيش", is_stock_tracked=True, default_sale_price="5")
        stock_in(self.element, self.plant, 10, "350.00", movement_date=BEFORE)
        stock_in(self.flour, self.plant, 100, "20.00", movement_date=BEFORE)
        self.oven = self.centre("OVEN", "الفرن", "Oven")
        self.technician = make_supplier("SUP-TECH", name="مركز صيانة الأفران")
        self.recipe = services.save_recipe({"product": self.bread, "output_quantity": "10"}, [(self.flour, "1")], self.owner)
        workcentres.save_routing(self.recipe, [{"name_ar": "خَبز", "name_en": "Baking", "work_centre": self.oven.pk, "minutes_per_batch": "45", "setup_minutes": "0"}],
                                 self.owner)

    def centre(self, code, name_ar, name_en, hours="8"):
        return workcentres.save_work_centre({"code": code, "name_ar": name_ar, "name_en": name_en, "kind": "machine", "hours_per_day": hours,
                                             "rate_per_hour": "0"}, self.owner)

    def breakdown(self, centre=None, opened_at=None, cause="السخان اتحرق"):
        return maintenance.open_job(centre or self.oven, self.owner, kind="breakdown", cause=cause, opened_at=opened_at)

    def baked(self, minutes, centre=None):
        """An order of 10 loaves whose baking stage is closed with ``minutes`` recorded (moved to ``centre`` first, if given)."""

        order = orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "10"}, self.owner)
        if centre is not None:
            schedule.move_order(order, self.owner, step_index=0, centre=centre)
        orders.advance(order, self.owner, minutes=minutes)
        return order

    def oven_day(self, day, start=None):
        block = next(b for b in workcentres.load(start or day) if b["centre"].pk == self.oven.pk)
        return next(row for row in block["days"] if row["date"] == day)

    def clean_books(self):
        self.assertTrue(gl.trial_balance()["balanced"])
        check = gl.reconciliation()
        self.assertTrue(check["ok"], check["rows"])


# ---------------------------------------------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------------------------------------------


class PlanTests(OvenPlant):
    def test_a_plan_every_30_days_is_due_on_day_31_not_on_day_30(self):
        plan = maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30", "last_done": (TODAY - timedelta(days=30)).isoformat()}, self.owner)
        state = maintenance.plan_state(plan)
        self.assertEqual((state["days"], state["next_day"], state["by_days"], state["due"]), (30, TODAY, False, False))
        tomorrow = maintenance.plan_state(plan, now=timezone.now() + timedelta(days=1))
        self.assertEqual((tomorrow["days"], tomorrow["by_days"], tomorrow["due"]), (31, True, True))
        self.assertEqual(maintenance.counts(), (0, 0))
        self.assertEqual(maintenance.counts(now=timezone.now() + timedelta(days=1)), (0, 1))

    def test_a_plan_every_200_running_hours_is_due_when_the_recorded_minutes_reach_12000(self):
        plan = maintenance.save_plan(self.oven, {"title": "تغيير زيت الموتور", "every_hours": "200"}, self.owner)
        self.assertEqual(maintenance.plan_state(plan)["minutes"], D("0"))
        self.baked("11999")
        state = maintenance.plan_state(plan)
        self.assertEqual((state["minutes"], state["due_minutes"], state["by_hours"], state["due"]), (D("11999.00"), D("12000"), False, False))
        self.baked("1")
        state = maintenance.plan_state(plan)
        self.assertEqual((state["minutes"], state["hours"], state["by_hours"], state["due"]), (D("12000.00"), D("200.0"), True, True))
        self.assertEqual(maintenance.marks()[self.oven.pk]["due"], 1)

    def test_minutes_count_on_the_work_centre_the_stage_ran_on_and_a_stage_without_minutes_counts_nothing(self):
        second = self.centre("OVEN2", "الفرن التاني", "Second oven")
        plan = maintenance.save_plan(self.oven, {"title": "تغيير زيت الموتور", "every_hours": "200"}, self.owner)
        other = maintenance.save_plan(second, {"title": "تغيير زيت الموتور", "every_hours": "200"}, self.owner)
        self.baked("300", centre=second)                                            # moved on the schedule to the second oven
        self.baked(None)                                                            # no minutes recorded
        self.assertEqual((maintenance.plan_state(plan)["minutes"], maintenance.plan_state(other)["minutes"]), (D("0"), D("300.00")))

    def test_closing_a_planned_job_stamps_the_plan_and_the_hours_start_again(self):
        plan = maintenance.save_plan(self.oven, {"title": "تغيير زيت الموتور", "every_hours": "200", "every_days": "90",
                                                 "last_done": (TODAY - timedelta(days=100)).isoformat()}, self.owner)
        self.baked("12000")
        state = maintenance.plan_state(plan)
        self.assertEqual((state["by_days"], state["by_hours"], state["due"]), (True, True, True))
        job = maintenance.open_job(self.oven, self.owner, kind="planned", plan=plan)
        self.assertTrue(maintenance.plan_state(plan)["due"])                         # still due until the job is closed
        closed = maintenance.close_job(job, self.owner)
        plan.refresh_from_db()
        self.assertEqual(plan.last_done_at, closed.closed_at)
        state = maintenance.plan_state(plan)
        self.assertEqual((state["days"], state["minutes"], state["due"]), (0, D("0"), False))

    def test_a_breakdown_and_a_planned_job_without_a_plan_stamp_nothing(self):
        plan = maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30", "last_done": BEFORE.isoformat()}, self.owner)
        before = plan.last_done_at
        maintenance.close_job(self.breakdown(), self.owner)
        maintenance.close_job(maintenance.open_job(self.oven, self.owner, kind="planned"), self.owner)
        plan.refresh_from_db()
        self.assertEqual(plan.last_done_at, before)

    def test_a_plan_is_checked_and_changed(self):
        for data, text in (({"title": "", "every_days": "30"}, "اكتب اسم خطة الصيانة"), ({"title": "تنظيف"}, "كل كام يوم أو كل كام ساعة"),
                           ({"title": "تنظيف", "every_days": "0"}, "رقم صحيح أكبر من صفر"), ({"title": "تنظيف", "every_hours": "1.5"}, "رقم صحيح أكبر من صفر"),
                           ({"title": "تنظيف", "every_days": "30", "last_done": (TODAY + timedelta(days=1)).isoformat()}, "بعد النهارده"),
                           ({"title": "تنظيف", "every_days": "30", "last_done": "امبارح"}, "التاريخ مش صحيح")):
            with self.subTest(data=data):
                with self.assertRaisesMessage(ValidationError, text):
                    maintenance.save_plan(self.oven, data, self.owner)
        self.assertEqual(MaintenancePlan.objects.count(), 0)
        plan = maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30"}, self.owner)
        self.assertEqual((plan.every_days, plan.every_hours, timezone.localtime(plan.last_done_at).date(), plan.active), (30, None, TODAY, True))
        job = maintenance.open_job(self.oven, self.owner, kind="planned", plan=plan)
        stamp = maintenance.close_job(job, self.owner).closed_at
        plan.refresh_from_db()
        changed = maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات والمدخنة", "every_days": "45", "every_hours": "100", "last_done": TODAY.isoformat(),
                                                    "active": False}, self.owner, plan)
        self.assertEqual((changed.pk, changed.title, changed.every_days, changed.every_hours, changed.active), (plan.pk, "تنظيف الشعلات والمدخنة", 45, 100, False))
        self.assertEqual(changed.last_done_at, stamp)                                # the same day typed again keeps the time the job stamped
        self.assertFalse(maintenance.plan_state(changed)["due"])
        self.assertEqual(maintenance.plan_states(), [])                              # a plan switched off is on no list of what is due
        self.assertEqual(AuditLog.objects.filter(action="save_maintenance_plan").count(), 2)
        self.assertEqual(MaintenancePlan.objects.count(), 1)

    def test_a_plan_switched_off_is_never_due(self):
        plan = maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30", "last_done": (TODAY - timedelta(days=60)).isoformat()}, self.owner)
        self.assertTrue(maintenance.plan_state(plan)["due"])
        plan = maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30", "active": False}, self.owner, plan)
        self.assertEqual((maintenance.plan_state(plan)["by_days"], maintenance.plan_state(plan)["due"]), (True, False))
        self.assertEqual(maintenance.counts(), (0, 0))


# ---------------------------------------------------------------------------------------------------------------
# Jobs, downtime and the schedule
# ---------------------------------------------------------------------------------------------------------------


class JobTests(OvenPlant):
    def test_a_breakdown_from_10_to_14_is_240_minutes_and_the_schedule_loses_them(self):
        job = self.breakdown(opened_at=at(YESTERDAY, 10))
        self.assertEqual((job.number, job.kind, job.closed_at, job.downtime_minutes), (f"MJ-{YESTERDAY:%Y%m%d}-001", MaintenanceKind.BREAKDOWN, None, None))
        closed = maintenance.close_job(job, self.owner, closed_at=at(YESTERDAY, 14))
        self.assertEqual((closed.downtime_minutes, closed.closed_by), (240, self.owner))
        self.assertEqual(maintenance.downtime([self.oven.pk], YESTERDAY, TODAY), {(self.oven.pk, YESTERDAY): D("240.00")})
        day = self.oven_day(YESTERDAY)
        self.assertEqual((day["capacity"], day["down"], day["over"]), (D("240.00"), True, False))        # 8 hours less the 4 it was down
        self.assertEqual((self.oven_day(TODAY, start=YESTERDAY)["capacity"], self.oven_day(TODAY, start=YESTERDAY)["down"]), (D("480.00"), False))
        lane = next(lane for lane in schedule.grid(YESTERDAY, days=2)["lanes"] if lane["centre"] and lane["centre"].pk == self.oven.pk)
        self.assertEqual([(row["capacity"], row["down"]) for row in lane["days"]], [(D("240.00"), True), (D("480.00"), False)])
        self.assertIsNone(lane["broken"])                                           # closed: the oven is not down now

    def test_a_breakdown_longer_than_the_working_day_leaves_no_capacity(self):
        maintenance.close_job(self.breakdown(opened_at=at(BEFORE, 6)), self.owner, closed_at=at(YESTERDAY, 20))
        taken = maintenance.downtime([self.oven.pk], BEFORE, TODAY)
        self.assertEqual(taken[(self.oven.pk, BEFORE)], D("1080.00"))                 # 06:00 to midnight
        self.assertEqual(taken[(self.oven.pk, BEFORE + timedelta(days=1))], D("1440.00"))
        self.assertEqual(taken[(self.oven.pk, YESTERDAY)], D("1200.00"))              # midnight to 20:00
        for day in (BEFORE, BEFORE + timedelta(days=1), YESTERDAY):
            self.assertEqual(self.oven_day(day, start=BEFORE)["capacity"], D("0"))    # never below zero
        self.assertEqual(self.oven_day(TODAY, start=BEFORE)["capacity"], D("480.00"))
        self.assertEqual(MaintenanceJob.objects.get().downtime_minutes, 3720)

    def test_an_open_breakdown_takes_the_whole_of_today_and_leaves_tomorrow(self):
        job = self.breakdown()
        self.assertEqual((self.oven_day(TODAY)["capacity"], self.oven_day(TODAY)["down"]), (D("0"), True))
        tomorrow = TODAY + timedelta(days=1)
        self.assertEqual((self.oven_day(tomorrow, start=TODAY)["capacity"], self.oven_day(tomorrow, start=TODAY)["down"]), (D("480.00"), False))
        lane = next(lane for lane in schedule.grid(TODAY)["lanes"] if lane["centre"] and lane["centre"].pk == self.oven.pk)
        self.assertEqual((lane["broken"], lane["days"][0]["capacity"], lane["days"][1]["capacity"]), (job, D("0"), D("480.00")))
        self.assertEqual(maintenance.counts(), (1, 0))
        self.assertEqual(maintenance.marks()[self.oven.pk]["down"], job)
        # Video finding F4 (approved 23 Nov 2026): «رتّب» books nothing on the down oven. The stage waits for it, with no planned
        # day, so the day is not over capacity and nothing warns; the load counts its 45 minutes apart.
        order = orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "10"}, self.owner)
        result = schedule.arrange(TODAY, self.owner)
        self.assertEqual([(w["order"], w["centre"], w["job"]) for w in result["waiting"]], [(order, self.oven, job)])
        self.assertEqual((order.steps.get().planned_start, order.steps.get().planned_end), (None, None))
        self.assertEqual((self.oven_day(TODAY)["minutes"], self.oven_day(TODAY)["over"]), (D("0"), False))
        self.assertEqual(next(b for b in workcentres.load(TODAY) if b["centre"].pk == self.oven.pk)["waiting"], D("45.00"))
        self.assertEqual(schedule.warnings_for({(self.oven.pk, TODAY)}), [])
        # Back in service: the next «رتّب» plans it on the oven today, within the day's full capacity.
        maintenance.close_job(job, self.owner)
        schedule.arrange(TODAY, self.owner)
        self.assertEqual(schedule.day_of(order.steps.get().planned_start), TODAY)
        self.assertEqual((self.oven_day(TODAY)["capacity"], self.oven_day(TODAY)["minutes"], self.oven_day(TODAY)["over"]), (D("480.00"), D("45.00"), False))
        self.assertEqual(schedule.warnings_for({(self.oven.pk, TODAY)}), [])
        self.assertEqual(order.steps.get().work_centre, self.oven)

    def test_a_planned_job_takes_no_capacity(self):
        job = maintenance.open_job(self.oven, self.owner, kind="planned", opened_at=at(YESTERDAY, 10))
        self.assertEqual(self.oven_day(TODAY)["capacity"], D("480.00"))
        marks = maintenance.marks()[self.oven.pk]
        self.assertEqual((marks["down"], marks["open"]), (None, job))
        maintenance.close_job(job, self.owner, closed_at=at(YESTERDAY, 12))
        self.assertEqual(maintenance.downtime([self.oven.pk], YESTERDAY, TODAY), {})
        self.assertEqual(MaintenanceJob.objects.get().downtime_minutes, 120)           # its downtime is still recorded on the job

    def test_a_holiday_stays_a_holiday_and_another_centre_is_untouched(self):
        from attendance.models import Holiday

        second = self.centre("OVEN2", "الفرن التاني", "Second oven")
        Holiday.objects.create(date=TODAY, name="إجازة")
        self.breakdown()
        today = self.oven_day(TODAY)
        self.assertEqual((today["capacity"], today["holiday"], today["down"]), (D("0"), True, False))
        other = next(b for b in workcentres.load(TODAY, days=2) if b["centre"].pk == second.pk)
        self.assertEqual([(row["capacity"], row["down"]) for row in other["days"]], [(D("0"), False), (D("480.00"), False)])

    def test_a_number_taken_at_the_same_moment_by_another_centres_job_is_tried_again(self):
        """Numbers are per day across work centres and the lock is per centre: a job opened on another centre at the same
        moment can take the number this one picked. That is not «عليه أمر صيانة مفتوح»: the next number is taken."""

        from unittest import mock

        mixer = self.centre("MIX", "الخلاط", "Mixer")
        first = self.breakdown()
        real = maintenance._number
        picked = iter([first.number])                                     # the number another centre's job has just taken

        def number(day):
            return next(picked, None) or real(day)

        with mock.patch.object(maintenance, "_number", side_effect=number):
            second = maintenance.open_job(mixer, self.owner, kind="breakdown", cause="السير اتقطع")
        self.assertNotEqual(second.number, first.number)
        self.assertEqual((second.work_centre, MaintenanceJob.objects.count()), (mixer, 2))
        # A centre that really has an open job still says so, whatever the number.
        with self.assertRaisesMessage(ValidationError, "عليه أمر صيانة مفتوح"):
            maintenance.open_job(mixer, self.owner, kind="planned")
        # And when every try collides, the words are about the number, not about the centre.
        third = self.centre("PACK", "خط التعبئة", "Packing line")
        with mock.patch.object(maintenance, "_number", return_value=first.number):
            with self.assertRaisesMessage(ValidationError, "في نفس اللحظة"):
                maintenance.open_job(third, self.owner, kind="planned")
        self.assertFalse(third.maintenance_jobs.exists())

    def test_the_job_is_checked(self):
        plan = maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30"}, self.owner)
        second = self.centre("OVEN2", "الفرن التاني", "Second oven")
        for kwargs, text in (({"kind": "repair"}, "اختار نوع الأمر"), ({"kind": "breakdown", "cause": "  "}, "اكتب العطل"),
                             ({"kind": "breakdown", "cause": "السير اتقطع", "opened_at": timezone.now() + timedelta(hours=2)}, "مينفعش يكون بعد دلوقتي"),
                             ({"kind": "breakdown", "cause": "السير اتقطع", "opened_at": "الصبح"}, "اكتب التاريخ والساعة")):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesMessage(ValidationError, text):
                    maintenance.open_job(self.oven, self.owner, **kwargs)
        with self.assertRaisesMessage(ValidationError, "مش على مركز العمل ده"):
            maintenance.open_job(second, self.owner, kind="planned", plan=plan)
        self.assertEqual(MaintenanceJob.objects.count(), 0)
        job = self.breakdown(opened_at=at(YESTERDAY, 10))
        with self.assertRaisesMessage(ValidationError, "عليه أمر صيانة مفتوح"):
            self.breakdown()
        with self.assertRaisesMessage(ValidationError, "عليه أمر صيانة مفتوح"):
            maintenance.open_job(self.oven, self.owner, kind="planned")
        self.breakdown(second)                                                      # another machine opens its own
        with self.assertRaisesMessage(ValidationError, "قبل وقت التوقف"):
            maintenance.close_job(job, self.owner, closed_at=at(YESTERDAY, 9))
        with self.assertRaisesMessage(ValidationError, "مينفعش يكون بعد دلوقتي"):
            maintenance.close_job(job, self.owner, closed_at=timezone.now() + timedelta(hours=2))
        job.refresh_from_db()
        self.assertIsNone(job.closed_at)
        maintenance.close_job(job, self.owner, closed_at=at(YESTERDAY, 10, 30), supplier=self.technician, note="اتغيّر السخان")
        job.refresh_from_db()
        self.assertEqual((job.downtime_minutes, job.supplier, job.employee, job.note), (30, self.technician, None, "اتغيّر السخان"))
        with self.assertRaisesMessage(ValidationError, "اتقفل قبل كده"):
            maintenance.close_job(job, self.owner)
        again = self.breakdown(cause="السير اتقطع")                                  # the machine is free for a new job
        self.assertEqual(again.number, f"MJ-{TODAY:%Y%m%d}-002")
        self.assertEqual(maintenance.minutes_down(job), 30)

    def test_a_time_typed_on_the_form_is_the_business_clock(self):
        job = maintenance.open_job(self.oven, self.owner, kind="breakdown", cause="السير اتقطع", opened_at=f"{YESTERDAY.isoformat()}T10:00")
        self.assertEqual(job.opened_at, at(YESTERDAY, 10))
        closed = maintenance.close_job(job, self.owner, closed_at=f"{YESTERDAY.isoformat()}T14:00")
        self.assertEqual(closed.downtime_minutes, 240)

    def test_who_did_the_work_is_kept_and_every_step_is_audited(self):
        from staff.models import Employee

        fitter = Employee.objects.create(code="E-FIT", name="محمود الفني")
        job = self.breakdown(opened_at=at(YESTERDAY, 10))
        maintenance.close_job(job, self.owner, closed_at=at(YESTERDAY, 11), employee=fitter, supplier=self.technician)
        job.refresh_from_db()
        self.assertEqual((job.employee, job.supplier), (fitter, self.technician))
        fitter.active = False
        fitter.save(update_fields=["active"])
        with self.assertRaisesMessage(ValidationError, "اختار موظف شغال"):
            maintenance.close_job(self.breakdown(), self.owner, employee=fitter)
        rows = AuditLog.objects.filter(module="manufacturing", object_type="manufacturing.MaintenanceJob", object_id=str(job.pk))
        self.assertEqual(sorted(rows.values_list("action", flat=True)), ["close_maintenance_job", "open_maintenance_job"])
        self.assertEqual(rows.get(action="close_maintenance_job").after_data["downtime_minutes"], 60)


# ---------------------------------------------------------------------------------------------------------------
# Spare parts and the ledger
# ---------------------------------------------------------------------------------------------------------------


class PartTests(OvenPlant):
    def taken(self, quantity="2", **extra):
        job = extra.pop("job", None) or self.breakdown()
        return maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity=quantity, **extra)

    def test_two_heating_elements_book_to_maintenance_expense_not_to_stock_count_loss(self):
        part = self.taken(taken_on=BEFORE)
        operation = part.operation
        self.assertEqual((operation.reference_number, operation.operation_type, operation.adjustment_direction, operation.quantity, operation.unit_cost,
                          operation.source_location, operation.operation_date),
                         (f"{part.job.number}-P01", "adjustment", "out", D("2.000"), D("350.0000"), self.plant, BEFORE))
        self.assertIn(part.job.number, operation.reason)
        self.assertEqual(lines(operation), [(0, self.STOCK, "0.00", "700.00"), (0, "5206", "700.00", "0.00")])
        self.assertEqual((balance("5206"), balance("5211"), balance("5299")), (D("700.00"), D("0.00"), D("0.00")))
        self.assertEqual(gl.control_balance("stock_loss"), D("0.00"))
        self.assertEqual((get_item_location_stock_quantity(self.element, self.plant), get_item_authoritative_average_cost(self.element)), (D("8.000"), D("350.0000")))
        self.assertEqual(maintenance.job_cost(part.job), {"parts": D("700.00"), "bills": D("0.00"), "total": D("700.00")})
        self.clean_books()

    def test_taking_the_parts_back_on_a_later_day_mirrors_the_entry(self):
        part = self.taken(taken_on=BEFORE)
        maintenance.return_part(part, self.owner, reason="القطعة مقاسها غلط")
        operation = StockOperation.objects.get(pk=part.operation_id)
        self.assertEqual((operation.status, operation.reversal_date, operation.cancelled_by), (StockOperationStatus.CANCELLED, TODAY, self.owner))
        self.assertIn("القطعة مقاسها غلط", operation.cancellation_reason)
        self.assertEqual(lines(operation), [(0, self.STOCK, "0.00", "700.00"), (0, "5206", "700.00", "0.00"),
                                            (3, self.STOCK, "700.00", "0.00"), (3, "5206", "0.00", "700.00")])
        self.assertEqual((balance("5206"), balance("5211"), balance("4202")), (D("0.00"), D("0.00"), D("-3500.00") - D("2000.00")))   # only the opening stock's gain
        self.assertEqual((get_item_location_stock_quantity(self.element, self.plant), get_item_authoritative_average_cost(self.element)), (D("10.000"), D("350.0000")))
        self.assertEqual(maintenance.job_cost(part.job)["parts"], D("0.00"))
        self.assertEqual([row["out"] for row in maintenance.part_rows(part.job)], [False])
        with self.assertRaisesMessage(ValidationError, "رجعت المخزن قبل كده"):
            maintenance.return_part(part, self.owner)
        self.clean_books()

    def test_a_part_taken_and_returned_the_same_day_leaves_nothing_in_the_books(self):
        part = self.taken()
        maintenance.return_part(part, self.owner)
        self.assertEqual(lines(part.operation), [(0, self.STOCK, "0.00", "700.00"), (0, self.STOCK, "700.00", "0.00")])
        self.assertEqual((balance("5206"), balance("5211")), (D("0.00"), D("0.00")))
        self.clean_books()

    def test_a_plain_adjustment_out_still_books_to_stock_count_loss(self):
        from inventory.models import StockAdjustmentDirection
        from inventory.services import adjust_stock

        self.taken("2")
        plain = adjust_stock("ADJ-COUNT", TODAY, self.element, self.plant, StockAdjustmentDirection.OUT, "1", "جرد: عجز", self.owner)
        self.assertEqual(lines(plain), [(0, self.STOCK, "0.00", "350.00"), (0, "5211", "350.00", "0.00")])
        self.assertEqual((balance("5206"), balance("5211")), (D("700.00"), D("350.00")))
        self.clean_books()

    def test_parts_follow_the_account_the_maintenance_expense_category_points_at(self):
        # The owner keeps machine repairs in an account of their own: an expense of the category and a spare part both go there.
        Projector().rebuild()                                                       # the chart and the category's default link (5206) are made first
        self.assertEqual(ExpenseAccount.objects.get(category__code="maintenance").account.code, "5206")
        own = Account.objects.create(code="520601", name_ar="صيانة الأفران", account_type="expense", parent=Account.objects.get(code="52"), is_postable=True)
        ExpenseAccount.objects.filter(category__code="maintenance").update(account=own)
        part = self.taken()
        self.assertEqual(lines(part.operation), [(0, self.STOCK, "0.00", "700.00"), (0, "520601", "700.00", "0.00")])
        self.assertEqual((balance("520601"), balance("5206"), balance("5211")), (D("700.00"), D("0.00"), D("0.00")))
        self.clean_books()

    def test_without_the_maintenance_category_parts_book_to_general_expenses(self):
        ExpenseCategory.objects.filter(code="maintenance").delete()                 # its link to 5206 goes with it
        part = self.taken()
        self.assertFalse(ExpenseAccount.objects.filter(category__code="maintenance").exists())
        self.assertEqual(lines(part.operation), [(0, self.STOCK, "0.00", "700.00"), (0, "5299", "700.00", "0.00")])
        self.assertEqual((balance("5299"), balance("5206"), balance("5211")), (D("700.00"), D("0.00"), D("0.00")))
        maintenance.return_part(MaintenancePart.objects.get(), self.owner)
        self.assertEqual(balance("5299"), D("0.00"))
        self.clean_books()

    def test_the_spare_part_keeps_its_inventory_account(self):
        # Raw materials by today's rule (an item no recipe makes); an item a recipe makes leaves finished goods.
        self.assertEqual(Projector().acc.inventory(self.element.pk).code, self.STOCK)
        stock_in(self.bread, self.plant, 20, "4.00", movement_date=BEFORE)
        job = self.breakdown()
        part = maintenance.take_part(job, self.owner, item=self.bread, location=self.plant, quantity="5")
        made = Projector().acc.inventory(self.bread.pk).code
        self.assertEqual(lines(part.operation), [(0, made, "0.00", "20.00"), (0, "5206", "20.00", "0.00")])
        self.assertEqual(made, "110503" if self.STOCK == "110501" else "1105")

    def test_a_part_is_checked_and_nothing_is_written_when_it_is_refused(self):
        job = self.breakdown()
        service = make_item(item_code="SRV-FIX", item_name="خدمة إصلاح", is_stock_tracked=False)
        hold = quality.hold_place(self.plant.entity_id)
        before = (StockOperation.objects.count(), StockMovement.objects.count())
        for kwargs, text in (({"item": None, "location": self.plant, "quantity": "1"}, "اختار قطعة الغيار"),
                             ({"item": service, "location": self.plant, "quantity": "1"}, "صنف متتبع في المخزون"),
                             ({"item": self.element, "location": None, "quantity": "1"}, "اختار مخزن من نفس كيان"),
                             ({"item": self.element, "location": hold, "quantity": "1"}, "اختار مخزن من نفس كيان"),
                             ({"item": self.element, "location": self.plant, "quantity": "0"}, "رقم أكبر من صفر"),
                             ({"item": self.element, "location": self.plant, "quantity": "اتنين"}, "رقم أكبر من صفر"),
                             ({"item": self.element, "location": self.plant, "quantity": "11"}, "الكمية مش كفاية من «سخان فرن»: المطلوب 11 والموجود 10 بس"),
                             ({"item": self.element, "location": self.plant, "quantity": "1", "taken_on": TODAY + timedelta(days=1)}, "بعد النهارده")):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesMessage(ValidationError, text):
                    maintenance.take_part(job, self.owner, **kwargs)
        maintenance.close_job(job, self.owner)
        with self.assertRaisesMessage(ValidationError, "وأمر الصيانة مفتوح بس"):
            maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="1")
        self.assertEqual((StockOperation.objects.count(), StockMovement.objects.count(), MaintenancePart.objects.count()), (*before, 0))

    def test_a_part_goes_back_after_the_job_is_closed_and_parts_are_numbered(self):
        job = self.breakdown()
        first = self.taken("1", job=job)
        second = self.taken("3", job=job)
        self.assertEqual([part.operation.reference_number for part in (first, second)], [f"{job.number}-P01", f"{job.number}-P02"])
        maintenance.close_job(job, self.owner)
        maintenance.return_part(second, self.owner)
        self.assertEqual(get_item_location_stock_quantity(self.element, self.plant), D("9.000"))
        self.assertEqual(maintenance.job_cost(job)["parts"], D("350.00"))
        actions = list(AuditLog.objects.filter(module="manufacturing", object_id=str(job.pk), action__contains="maintenance_part").values_list("action", flat=True))
        self.assertEqual(sorted(actions), ["return_maintenance_part", "take_maintenance_part", "take_maintenance_part"])

    def test_where_reversals_wait_for_approval_only_an_approver_takes_a_part_back(self):
        from approvals.services import set_held_actions

        part = self.taken()
        keeper = limited("mnt_hold_keeper", "inventory.view_stock", "inventory.adjust_stock")
        set_held_actions(["inventory.reverse_operation"], self.owner)
        with self.assertRaisesMessage(ValidationError, "محتاج موافقة"):
            maintenance.return_part(part, keeper)
        self.assertEqual(StockOperation.objects.get(pk=part.operation_id).status, StockOperationStatus.POSTED)
        maintenance.return_part(part, self.owner)                                   # the owner approves, so the owner may
        self.assertEqual(StockOperation.objects.get(pk=part.operation_id).status, StockOperationStatus.CANCELLED)
        set_held_actions([], self.owner)
        maintenance.return_part(self.taken("1", job=part.job), keeper)              # with nothing held, the stock right is enough


class BakeryPartTests(BakeryChart, PartTests):
    """The same parts on a restaurant bakery's chart with the manufacturing module on: stock is one account (1105)."""

    MODULES = BakeryChart.MODULES + ",suppliers,purchases,expenses"
    STOCK = "1105"


# ---------------------------------------------------------------------------------------------------------------
# An outside technician's bill, and the machine's cost history
# ---------------------------------------------------------------------------------------------------------------


class BillTests(OvenPlant):
    def job_with_parts(self):
        job = self.breakdown()
        maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="2")
        return job

    def month(self, centre=None):
        rows = maintenance.history(centre or self.oven)
        return rows[0] if rows else None

    def test_a_technicians_bill_of_500_plus_vat_is_an_ordinary_purchase_and_the_month_shows_1200(self):
        job = self.job_with_parts()
        link = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="500", description="تغيير السخانات")
        invoice = link.purchase_invoice
        self.assertEqual((invoice.invoice_number, invoice.status, invoice.supplier, invoice.receiving_location, invoice.subtotal, invoice.tax_amount, invoice.total_amount),
                         (f"MNT-{job.number.removeprefix('MJ-')}-01", "draft", self.technician, self.plant, D("500.00"), D("70.00"), D("570.00")))
        line = invoice.lines.get()
        self.assertEqual((line.item.item_code, line.item.is_stock_tracked, line.quantity, line.unit_purchase_price), ("MFG-MNT", False, D("1.000"), D("500.00")))
        self.assertTrue(link.made_here)
        # A draft is nothing yet: no supplier debt, no journal row, and the month shows the parts alone.
        Projector().rebuild()
        self.assertFalse(SupplierLedgerEntry.objects.exists())
        self.assertFalse(JournalLine.objects.filter(entry__source_type="purchase_invoice").exists())
        self.assertEqual((self.month()["parts"], self.month()["bills"], self.month()["total"]), (D("700.00"), D("0.00"), D("700.00")))
        post_purchase_invoice(invoice.pk, self.owner)                               # from the purchases screen, by its own service
        Projector().rebuild()
        booked = {line.account.code: line.debit - line.credit for line in JournalLine.objects.filter(entry__source_type="purchase_invoice", entry__source_id=invoice.pk)}
        self.assertEqual(booked, {"2101": D("-570.00"), "1106": D("70.00"), "5299": D("500.00")})
        entry = SupplierLedgerEntry.objects.get()
        self.assertEqual((entry.supplier, entry.due_increase), (self.technician, D("570.00")))
        month = self.month()
        self.assertEqual((month["month"], month["parts"], month["bills"], month["total"], month["jobs"]), (TODAY.replace(day=1), D("700.00"), D("500.00"), D("1200.00"), 1))
        self.assertEqual(maintenance.job_cost(job), {"parts": D("700.00"), "bills": D("500.00"), "total": D("1200.00")})
        self.assertEqual((balance("5206"), balance("5299"), balance("5211")), (D("700.00"), D("500.00"), D("0.00")))
        self.assertFalse(StockMovement.objects.filter(purchase_invoice=invoice).exists())   # a service line moves no stock
        self.clean_books()

    def test_the_month_reads_the_bill_net_of_its_returns_and_a_cancelled_bill_counts_nothing(self):
        from purchases.services import create_purchase_return

        job = self.job_with_parts()
        invoice = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="500").purchase_invoice
        self.assertEqual(maintenance.bill_cost(invoice), D("0"))                       # a draft counts nothing
        post_purchase_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        self.assertEqual(self.month()["bills"], D("500.00"))
        self.assertEqual(maintenance.bill_cost(invoice), outside.bill_cost(invoice))
        cancel_posted_purchase_invoice(invoice.pk, self.owner, "اتسجلت غلط")
        invoice.refresh_from_db()
        self.assertEqual((maintenance.bill_cost(invoice), self.month()["bills"], self.month()["total"]), (D("0.00"), D("0.00"), D("700.00")))
        self.assertEqual([(row["cancelled"], row["cost"]) for row in maintenance.bill_rows(job)], [(True, D("0.00"))])
        # A new bill of two visits at 250.00; one visit goes back on a purchase return: 250.00 is left.
        from taxes.services import create_purchase_draft_with_tax

        visits = create_purchase_draft_with_tax(
            {"invoice_number": "TECH-77", "invoice_date": TODAY, "supplier": self.technician, "receiving_location": self.plant, "cashbox": None,
             "discount_amount": D("0"), "tax_amount": D("0"), "paid_now": D("0"), "notes": ""},
            [{"item": maintenance.service_item(), "quantity": D("2"), "unit_purchase_price": D("250.00"), "line_discount_amount": D("0"), "description": "زيارة فني"}], self.owner)
        post_purchase_invoice(visits.pk, self.owner)
        maintenance.link_bill(job, self.owner, invoice_number="tech-77")
        self.assertEqual(self.month()["bills"], D("500.00"))
        create_purchase_return("PR-MNT-1", TODAY, visits.pk, [{"source_line": visits.lines.get(), "quantity": D("1")}], "زيارة اتلغت", self.owner)
        self.assertEqual((self.month()["bills"], self.month()["total"]), (D("250.00"), D("950.00")))
        self.clean_books()

    def test_an_existing_purchase_invoice_is_linked_and_the_wrong_ones_are_refused(self):
        from taxes.services import create_purchase_draft_with_tax

        job = self.job_with_parts()

        def purchase(number, item, price, quantity="1"):
            return create_purchase_draft_with_tax(
                {"invoice_number": number, "invoice_date": TODAY, "supplier": self.technician, "receiving_location": self.plant, "cashbox": None,
                 "discount_amount": D("0"), "tax_amount": D("0"), "paid_now": D("0"), "notes": ""},
                [{"item": item, "quantity": D(quantity), "unit_purchase_price": D(price), "line_discount_amount": D("0"), "description": ""}], self.owner)

        repair = purchase("TECH-01", maintenance.service_item(), "300.00")
        elements = purchase("PARTS-01", self.element, "350.00", "4")
        for number, text in (("", "مفيش فاتورة شراء بالرقم ده"), ("TECH-99", "مفيش فاتورة شراء بالرقم ده"), ("PARTS-01", "فيها أصناف مخزون")):
            with self.subTest(number=number):
                with self.assertRaisesMessage(ValidationError, text):
                    maintenance.link_bill(job, self.owner, invoice_number=number)
        link = maintenance.link_bill(job, self.owner, invoice_number="TECH-01")
        self.assertEqual((link.purchase_invoice, link.made_here), (repair, False))
        second = maintenance.open_job(self.centre("MIX", "الخلاط", "Mixer"), self.owner, kind="planned")
        with self.assertRaisesMessage(ValidationError, "مربوطة قبل كده"):
            maintenance.link_bill(second, self.owner, invoice_number="TECH-01")        # one bill counts on one machine
        post_purchase_invoice(repair.pk, self.owner)
        self.assertEqual(self.month()["bills"], D("300.00"))
        # Removing a linked invoice leaves it in purchases as it is; removing a draft made here deletes the draft.
        self.assertFalse(maintenance.unlink_bill(link, self.owner))
        self.assertEqual((PurchaseInvoice.objects.get(pk=repair.pk).status, self.month()["bills"]), ("posted", D("0.00")))
        draft = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="120")
        number = draft.purchase_invoice.invoice_number
        self.assertTrue(maintenance.unlink_bill(draft, self.owner))
        self.assertFalse(PurchaseInvoice.objects.filter(invoice_number=number).exists())
        self.assertEqual((MaintenancePurchase.objects.count(), elements.status), (0, "draft"))
        posted = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="120")
        post_purchase_invoice(posted.purchase_invoice_id, self.owner)
        self.assertFalse(maintenance.unlink_bill(posted, self.owner))                # a posted bill is never deleted from here
        self.assertEqual(PurchaseInvoice.objects.get(pk=posted.purchase_invoice_id).status, "posted")
        cancelled = purchase("TECH-02", maintenance.service_item(), "90.00")
        post_purchase_invoice(cancelled.pk, self.owner)
        cancel_posted_purchase_invoice(cancelled.pk, self.owner, "اتسجلت غلط")
        with self.assertRaisesMessage(ValidationError, "الفاتورة دي ملغية"):
            maintenance.link_bill(job, self.owner, invoice_number="TECH-02")

    def test_a_bill_that_counts_on_a_machine_is_not_linked_to_a_project_as_well(self):
        """One bill counts in one place. ``link_bill`` already refuses a project's invoice; the project side refuses a
        maintenance job's bill (and an outside processing bill, F4) and does not offer either in its picker."""

        from hesba_testing.factories import make_customer
        from projects import costs as project_costs, services as project_services

        job = self.job_with_parts()
        bill = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="500")
        post_purchase_invoice(bill.purchase_invoice_id, self.owner)
        invoice = PurchaseInvoice.objects.get(pk=bill.purchase_invoice_id)
        project = project_services.save_project({"name": "توسعة المصنع", "customer": make_customer(customer_code="CUS-PRJ", name="مصنع النور")}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مربوطة بأمر صيانة أو بأمر إنتاج"):
            project_costs.link_purchase(project, invoice, self.owner)
        with self.assertRaisesMessage(ValidationError, "the bill of a maintenance job or a production order"):
            project_costs.link_purchase(project, invoice, self.owner, lang="en")
        self.assertFalse(project.purchases.exists())
        self.assertEqual(self.month()["bills"], D("500.00"))              # still the machine's, once
        # The picker on the project's page: a plain service invoice is offered, the machine's bill is not.
        from taxes.services import create_purchase_draft_with_tax

        plain = create_purchase_draft_with_tax(
            {"invoice_number": "SRV-01", "invoice_date": TODAY, "supplier": self.technician, "receiving_location": self.plant, "cashbox": None,
             "discount_amount": D("0"), "tax_amount": D("0"), "paid_now": D("0"), "notes": ""},
            [{"item": maintenance.service_item(), "quantity": D("1"), "unit_purchase_price": D("200"), "line_discount_amount": D("0"), "description": ""}], self.owner)
        offered = (PurchaseInvoice.objects.exclude(status="cancelled")
                   .filter(project_purchase__isnull=True, **{f"{name}__isnull": True for name in project_costs.FACTORY_BILLS}))
        self.assertEqual(list(offered), [plain])
        # Once the bill is taken off the job it is an ordinary invoice again, and a project may take it.
        maintenance.unlink_bill(bill, self.owner)
        project_costs.link_purchase(project, PurchaseInvoice.objects.get(pk=invoice.pk), self.owner)
        self.assertEqual(project.purchases.get().invoice, invoice)

    def test_a_bill_received_at_a_location_that_was_switched_off_since_is_still_linked(self):
        from taxes.services import create_purchase_draft_with_tax

        old_store = make_location(location_code="OLD", name_ar="المخزن القديم", name_en="Old store")
        invoice = create_purchase_draft_with_tax(
            {"invoice_number": "TECH-OLD", "invoice_date": TODAY, "supplier": self.technician, "receiving_location": old_store, "cashbox": None,
             "discount_amount": D("0"), "tax_amount": D("0"), "paid_now": D("0"), "notes": ""},
            [{"item": maintenance.service_item(), "quantity": D("1"), "unit_purchase_price": D("300.00"), "line_discount_amount": D("0"), "description": ""}], self.owner)
        old_store.active = False
        old_store.save(update_fields=["active"])
        self.assertNotIn(old_store, list(maintenance.places(self.oven)))               # no part leaves it any more
        self.assertEqual(maintenance.link_bill(self.breakdown(), self.owner, invoice_number="TECH-OLD").purchase_invoice, invoice)

    def test_a_bill_is_checked(self):
        job = self.breakdown()
        self.technician.active = False
        self.technician.save(update_fields=["active"])
        with self.assertRaisesMessage(ValidationError, "اختار مورّد شغال"):
            maintenance.make_bill(job, self.owner, supplier=self.technician, amount="500")
        active = make_supplier("SUP-TECH2", name="فني صيانة من برّه")
        for amount in ("", "0", "-5", "كتير"):
            with self.assertRaisesMessage(ValidationError, "أكبر من صفر"):
                maintenance.make_bill(job, self.owner, supplier=active, amount=amount)
        with self.assertRaisesMessage(ValidationError, "بعد النهارده"):
            maintenance.make_bill(job, self.owner, supplier=active, amount="500", bill_date=TODAY + timedelta(days=1))
        self.assertEqual((PurchaseInvoice.objects.count(), MaintenancePurchase.objects.count()), (0, 0))
        maintenance.close_job(job, self.owner)
        late = maintenance.make_bill(job, self.owner, supplier=active, amount="500")  # the bill often comes after the machine is back
        self.assertEqual(late.purchase_invoice.total_amount, D("570.00"))
        self.assertEqual(maintenance.make_bill(job, self.owner, supplier=active, amount="80").purchase_invoice.invoice_number, f"MNT-{job.number.removeprefix('MJ-')}-02")

    def test_the_history_adds_parts_bills_and_downtime_per_month_and_leaves_out_what_went_back(self):
        self.assertEqual(maintenance.history(self.oven), [])
        job = self.breakdown(opened_at=at(YESTERDAY, 10))
        kept = maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="2", taken_on=YESTERDAY)
        back = maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="1", taken_on=YESTERDAY)
        maintenance.return_part(back, self.owner)
        maintenance.close_job(job, self.owner, closed_at=at(YESTERDAY, 14))
        planned = maintenance.open_job(self.oven, self.owner, kind="planned", opened_at=at(YESTERDAY, 15))
        maintenance.close_job(planned, self.owner, closed_at=at(YESTERDAY, 15, 30))
        rows = maintenance.history(self.oven)
        months = {YESTERDAY.replace(day=1)}
        self.assertEqual({row["month"] for row in rows}, months)
        row = rows[0]
        self.assertEqual((row["parts"], row["bills"], row["total"], row["minutes"], row["hours"], row["jobs"]), (D("700.00"), D("0.00"), D("700.00"), 270, D("4.5"), 2))
        self.assertEqual(maintenance.part_value(kept.operation), D("700.00"))
        self.assertEqual(maintenance.history(self.centre("MIX", "الخلاط", "Mixer")), [])   # another machine's history is its own


class NoVatBillTests(OvenPlant):
    VAT = False

    def test_without_vat_the_bill_is_500_and_the_month_is_the_same(self):
        job = self.breakdown()
        maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="2")
        invoice = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="500").purchase_invoice
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("0.00"), D("500.00")))
        post_purchase_invoice(invoice.pk, self.owner)
        self.assertEqual(maintenance.history(self.oven)[0]["total"], D("1200.00"))
        self.clean_books()


# ---------------------------------------------------------------------------------------------------------------
# The stock screen's guard
# ---------------------------------------------------------------------------------------------------------------


class GuardTests(OvenPlant):
    def setUp(self):
        super().setUp()
        self.job = self.breakdown()
        self.part = maintenance.take_part(self.job, self.owner, item=self.element, location=self.plant, quantity="2")
        self.client.force_login(self.owner)

    def test_the_stock_screen_leaves_a_parts_operation_to_the_job(self):
        operation = self.part.operation
        owner = guards.guarded([operation.pk])[operation.pk]
        self.assertEqual((owner.kind, owner.number, owner.url), ("maintenance", self.job.number, reverse("manufacturing:maintenance_job", args=[self.job.pk])))
        with self.assertRaisesMessage(ValidationError, f"spare part of maintenance job {self.job.number}"):
            guards.before_stock_operation_reversal(operation.pk)
        page = self.client.get(reverse("inventory:operations"))
        self.assertContains(page, 'data-owned-by="maintenance"')
        self.assertContains(page, "قطعة غيار لأمر الصيانة")
        self.assertContains(page, f'href="{reverse("manufacturing:maintenance_job", args=[self.job.pk])}?lang=ar"')
        self.assertContains(self.client.get(reverse("inventory:operations"), {"lang": "en"}), "A spare part of maintenance job")
        refused = self.client.post(reverse("inventory:operation_cancel", args=[operation.pk]), {"reversal_date": TODAY.isoformat(), "reason": "تجربة"}, follow=True)
        self.assertContains(refused, f"الحركة دي قطعة غيار لأمر الصيانة {self.job.number}")
        self.assertContains(refused, "رجّع القطعة من صفحة أمر الصيانة")
        operation.refresh_from_db()
        self.assertEqual((operation.status, get_item_location_stock_quantity(self.element, self.plant)), (StockOperationStatus.POSTED, D("8.000")))

    def test_an_approval_that_was_waiting_for_it_is_refused_too(self):
        from approvals.actions import _reverse_stock_operation

        class Waiting:
            object_id, data, requested_by = self.part.operation_id, {"reversal_date": TODAY.isoformat(), "reason": "تجربة"}, self.owner

        with self.assertRaises(ValidationError):
            _reverse_stock_operation(Waiting)
        self.assertEqual(StockOperation.objects.get(pk=self.part.operation_id).status, StockOperationStatus.POSTED)

    def test_a_plain_adjustment_is_still_reversed_from_the_stock_screen(self):
        from inventory.models import StockAdjustmentDirection
        from inventory.services import adjust_stock

        plain = adjust_stock("ADJ-COUNT", TODAY, self.element, self.plant, StockAdjustmentDirection.OUT, "1", "جرد: عجز", self.owner)
        self.assertEqual(guards.guarded([plain.pk]), {})
        self.client.post(reverse("inventory:operation_cancel", args=[plain.pk]), {"reversal_date": TODAY.isoformat(), "reason": "غلطة"})
        plain.refresh_from_db()
        self.assertEqual(plain.status, StockOperationStatus.CANCELLED)

    def test_once_the_part_is_back_the_stock_screen_shows_nothing_to_reverse(self):
        maintenance.return_part(self.part, self.owner)
        page = self.client.get(reverse("inventory:operations")).content.decode()
        self.assertNotIn('data-owned-by="maintenance"', page)


# ---------------------------------------------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------------------------------------------


class ScreenTests(OvenPlant):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.centre_url = reverse("manufacturing:maintenance_centre", args=[self.oven.pk])

    def job_url(self, job):
        return reverse("manufacturing:maintenance_job", args=[job.pk])

    def test_a_breakdown_is_walked_from_the_work_centres_page_to_its_close(self):
        page = self.client.get(self.centre_url)
        self.assertContains(page, "data-open-job")
        self.assertContains(page, "data-plans-empty")
        self.assertContains(page, "data-history-empty")
        opened = self.client.post(self.centre_url, {"action": "open", "kind": "breakdown", "cause": "السخان اتحرق", "opened_at": f"{YESTERDAY.isoformat()}T10:00"}, follow=True)
        self.assertContains(opened, "أمر الصيانة اتفتح.")
        job = MaintenanceJob.objects.get()
        self.assertContains(opened, f'data-maintenance-job="{job.pk}"')
        self.assertContains(opened, "data-centre-down")
        self.assertContains(opened, "data-down-note")
        self.assertNotContains(opened, "data-open-job")
        took = self.client.post(self.job_url(job), {"action": "part", "next": "centre", "item": self.element.pk, "location": self.plant.pk, "quantity": "2"}, follow=True)
        self.assertEqual(took.redirect_chain[-1][0], f"{self.centre_url}?lang=ar")
        self.assertContains(took, "القطعة اتصرفت من المخزن على أمر الصيانة.")
        self.assertContains(took, 'data-part-state="out"')
        self.assertContains(took, "700.00")
        billed = self.client.post(self.job_url(job), {"action": "bill", "next": "centre", "supplier": self.technician.pk, "amount": "500"}, follow=True)
        invoice = PurchaseInvoice.objects.get()
        self.assertContains(billed, f"فاتورة الصيانة {invoice.invoice_number} اتعملت مسودة")
        self.assertContains(billed, 'data-bill-state="draft"')
        self.assertContains(billed, "data-post-hint")
        self.assertContains(billed, reverse("purchases:detail", args=[invoice.pk]))
        post_purchase_invoice(invoice.pk, self.owner)
        closed = self.client.post(self.job_url(job), {"action": "close", "next": "centre", "closed_at": f"{YESTERDAY.isoformat()}T14:00", "supplier": self.technician.pk}, follow=True)
        self.assertEqual(closed.redirect_chain[-1][0], f"{self.job_url(job)}?lang=ar")
        self.assertContains(closed, "أمر الصيانة اتقفل، ومدة التوقف 240 دقيقة.")
        self.assertContains(closed, 'data-job-state="closed"')
        self.assertContains(closed, "data-parts-closed")
        self.assertNotContains(closed, "data-close-job")
        self.assertNotContains(closed, "data-take-part")
        self.assertContains(closed, "data-make-bill")                               # a late bill is still taken
        self.assertContains(closed, "1,200.00")
        page = self.client.get(self.centre_url)
        self.assertContains(page, "data-open-job")                                  # the machine is free again
        self.assertNotContains(page, "data-centre-down")
        self.assertContains(page, f'data-latest-job="{job.pk}"')
        self.assertContains(page, "data-machine-history")
        self.assertContains(page, "1,200.00")
        self.assertContains(page, "<bdi dir=\"ltr\">4</bdi>")                        # four hours down this month

    def test_a_part_is_returned_and_a_bill_removed_from_the_jobs_page(self):
        job = self.breakdown()
        part = maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="2")
        link = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="500")
        page = self.client.get(self.job_url(job))
        self.assertContains(page, f'data-return-part="{part.pk}"')
        self.assertContains(page, f'data-unlink-bill="{link.pk}"')
        back = self.client.post(self.job_url(job), {"action": "return_part", "part": part.pk, "reason": "مقاس غلط"}, follow=True)
        self.assertContains(back, "القطعة رجعت المخزن.")
        self.assertContains(back, 'data-part-state="back"')
        self.assertNotContains(back, f'data-return-part="{part.pk}"')
        gone = self.client.post(self.job_url(job), {"action": "unlink", "bill": link.pk}, follow=True)
        self.assertContains(gone, "الفاتورة المسودة اتسحبت.")
        self.assertContains(gone, "data-bills-empty")
        self.assertFalse(PurchaseInvoice.objects.exists())
        linked = self.client.post(self.job_url(job), {"action": "link", "invoice_number": "مش موجودة"}, follow=True)
        self.assertContains(linked, "مفيش فاتورة شراء بالرقم ده في الكيان ده.")

    def test_a_refusal_is_said_on_the_page_and_nothing_is_written(self):
        refused = self.client.post(self.centre_url, {"action": "open", "kind": "breakdown", "cause": ""}, follow=True)
        self.assertContains(refused, "اكتب العطل اللي حصل في كلمتين.")
        self.assertEqual(MaintenanceJob.objects.count(), 0)
        job = self.breakdown()
        short = self.client.post(self.job_url(job), {"action": "part", "item": self.element.pk, "location": self.plant.pk, "quantity": "50"}, follow=True)
        self.assertContains(short, "الكمية مش كفاية من «سخان فرن»")
        self.assertEqual(MaintenancePart.objects.count(), 0)
        english = self.client.post(self.job_url(job), {"action": "part", "lang": "en", "item": self.element.pk, "location": self.plant.pk, "quantity": "50"}, follow=True)
        self.assertContains(english, "Not enough of “سخان فرن”")
        self.assertEqual(self.client.post(self.job_url(job), {"action": "delete"}).status_code, 404)
        self.assertEqual(self.client.post(self.centre_url, {"action": "close"}).status_code, 404)

    def test_plans_are_made_and_changed_on_the_page_and_a_due_one_is_marked(self):
        made = self.client.post(self.centre_url, {"action": "plan", "title": "تنظيف الشعلات", "every_days": "30",
                                                  "last_done": (TODAY - timedelta(days=45)).isoformat()}, follow=True)
        self.assertContains(made, "خطة الصيانة اتحفظت.")
        plan = MaintenancePlan.objects.get()
        self.assertContains(made, f'data-plan="{plan.pk}" data-plan-state="due"')
        self.assertContains(made, "آخر مرة من 45 يوم")
        self.assertContains(made, "كل 30 يوم")
        self.assertContains(made, "data-centre-due")
        self.assertContains(made, f'data-edit-plan="{plan.pk}"')
        changed = self.client.post(self.centre_url, {"action": "plan", "plan": plan.pk, "title": "تنظيف الشعلات", "every_days": "60", "active": "on"}, follow=True)
        self.assertContains(changed, f'data-plan="{plan.pk}" data-plan-state="ok"')
        off = self.client.post(self.centre_url, {"action": "plan", "plan": plan.pk, "title": "تنظيف الشعلات", "every_days": "60"}, follow=True)
        self.assertContains(off, f'data-plan="{plan.pk}" data-plan-state="off"')
        self.assertEqual(MaintenancePlan.objects.count(), 1)
        bad = self.client.post(self.centre_url, {"action": "plan", "title": "تغيير الزيت"}, follow=True)
        self.assertContains(bad, "حدّد الصيانة كل كام يوم أو كل كام ساعة تشغيل")
        # A planned job offers the active plans, and closing it stamps the one it carried out.
        hours = maintenance.save_plan(self.oven, {"title": "تغيير زيت الموتور", "every_hours": "200"}, self.owner)
        page = self.client.get(self.centre_url)
        self.assertContains(page, f'<option value="{hours.pk}">تغيير زيت الموتور</option>')
        self.assertNotContains(page, f'<option value="{plan.pk}">')                  # switched off
        self.client.post(self.centre_url, {"action": "open", "kind": "planned", "plan": hours.pk})
        job = MaintenanceJob.objects.get()
        self.assertEqual((job.kind, job.plan), (MaintenanceKind.PLANNED, hours))
        self.assertContains(self.client.get(self.centre_url), "data-centre-open")

    def test_the_list_says_what_is_down_and_what_is_due_in_both_languages(self):
        empty = self.client.get(reverse("manufacturing:maintenance"))
        self.assertContains(empty, "data-down-empty")
        self.assertContains(empty, "data-due-empty")
        self.assertContains(empty, 'data-maintenance-centre="OVEN"')
        self.assertContains(empty, "data-mark-ok")
        mixer = self.centre("MIX", "الخلاط", "Mixer")
        maintenance.save_plan(mixer, {"title": "تشحيم المحاور", "every_days": "7", "last_done": (TODAY - timedelta(days=10)).isoformat()}, self.owner)
        self.breakdown()
        page = self.client.get(reverse("manufacturing:maintenance"))
        self.assertContains(page, 'data-down-centre="OVEN"')
        self.assertContains(page, "السخان اتحرق")
        self.assertContains(page, "data-tile-down style=\"color:var(--hs-danger)\">1<")
        self.assertContains(page, "data-tile-due>1<")
        self.assertContains(page, "data-tile-open>1<")
        self.assertContains(page, "تشحيم المحاور")
        self.assertContains(page, "آخر مرة من 10 يوم")
        self.assertContains(page, "data-mark-down")
        self.assertContains(page, "data-mark-due")
        english = self.client.get(reverse("manufacturing:maintenance"), {"lang": "en"})
        for text in ("Machine maintenance", "Down now", "Maintenance due", "Last done 10 days ago", "Oven", "Mixer"):
            self.assertContains(english, text)
        page = self.client.get(self.centre_url, {"lang": "en"})
        for text in ("Close the maintenance job", "Breakdown", "Take a spare part", "Close the job", "Maintenance plans", "An outside technician"):
            self.assertContains(page, text)

    def test_the_home_the_work_centres_page_and_the_schedule_carry_the_marks(self):
        home = self.client.get(reverse("manufacturing:home"))
        self.assertContains(home, "data-maintenance-tile")
        self.assertContains(home, f'href="{reverse("manufacturing:maintenance")}?lang=ar" data-maintenance-down')
        self.assertContains(home, "data-maintenance-down>0<")
        maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30", "last_done": (TODAY - timedelta(days=40)).isoformat()}, self.owner)
        centres = self.client.get(reverse("manufacturing:work_centres"))
        self.assertContains(centres, "data-wc-due")
        self.assertContains(centres, f'href="{self.centre_url}?lang=ar" data-wc-maintenance')
        self.assertNotContains(centres, "data-wc-down")
        self.assertNotContains(centres, "data-load-down")
        self.breakdown()
        home = self.client.get(reverse("manufacturing:home"))
        self.assertContains(home, "data-maintenance-down>1<")
        self.assertContains(home, "data-maintenance-due>1<")
        centres = self.client.get(reverse("manufacturing:work_centres"))
        self.assertContains(centres, "data-wc-down")
        self.assertContains(centres, "data-load-down", count=1)                      # today only
        board = self.client.get(reverse("manufacturing:schedule"))
        self.assertContains(board, "data-lane-down")
        self.assertContains(board, "data-day-down", count=1)
        self.assertContains(board, "متوقف للصيانة")
        planned = maintenance.open_job(self.centre("MIX", "الخلاط", "Mixer"), self.owner, kind="planned")
        centres = self.client.get(reverse("manufacturing:work_centres"))
        self.assertContains(centres, "data-wc-open", count=1)
        self.assertEqual(planned.kind, MaintenanceKind.PLANNED)

    def test_the_pages_use_no_banned_word_and_no_arabic_line_starts_or_ends_with_a_latin_word(self):
        import pathlib

        from django.conf import settings

        root = pathlib.Path(settings.BASE_DIR)
        files = ["manufacturing/maintenance.py", "manufacturing/maintenance_views.py"] + [f"templates/manufacturing/{name}" for name in (
            "maintenance.html", "maintenance_centre.html", "maintenance_job.html", "_maintenance_job.html")]
        for name in files:
            text = (root / name).read_text(encoding="utf-8")
            for word in BANNED_WORDS:
                self.assertNotIn(word, text, name)
        for label, words in (("screens", maintenance_views.WORDS["ar"]), ("rules", maintenance.MESSAGES["ar"])):
            for key, text in words.items():
                first, last = edges(text)
                with self.subTest(words=label, key=key):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)
                    self.assertFalse(first.startswith("{") or last.endswith("}"), text)   # a code or a number never opens or closes the line
        self.assertEqual(set(maintenance_views.WORDS["ar"]), set(maintenance_views.WORDS["en"]))
        self.assertEqual(set(maintenance.MESSAGES["ar"]), set(maintenance.MESSAGES["en"]))
        # Every state on the pages: a due plan, a plan that is not due, an open breakdown with a part out, one back and a
        # draft bill, and a closed job with a posted bill.
        mixer = self.centre("MIX", "الخلاط", "Mixer")
        maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30", "last_done": (TODAY - timedelta(days=40)).isoformat()}, self.owner)
        maintenance.save_plan(self.oven, {"title": "تغيير زيت الموتور", "every_hours": "200", "every_days": "90"}, self.owner)
        done = self.breakdown(mixer, opened_at=at(YESTERDAY, 10))
        maintenance.take_part(done, self.owner, item=self.element, location=self.plant, quantity="1")
        post_purchase_invoice(maintenance.make_bill(done, self.owner, supplier=self.technician, amount="500").purchase_invoice_id, self.owner)
        maintenance.close_job(done, self.owner, closed_at=at(YESTERDAY, 14), supplier=self.technician, note="اتغيّر السير")
        job = self.breakdown()
        maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="2")
        maintenance.return_part(maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="1"), self.owner)
        maintenance.make_bill(job, self.owner, supplier=self.technician, amount="250")
        pages = [reverse("manufacturing:maintenance"), self.centre_url, reverse("manufacturing:maintenance_centre", args=[mixer.pk]), self.job_url(job), self.job_url(done)]
        for url in pages:
            html = self.client.get(url).content.decode()
            for word in BANNED_WORDS:
                self.assertNotIn(word, html, url)
            for text in sentences(html):
                first, last = edges(text)
                with self.subTest(url=url, text=text[:50]):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)
        # The marks this change put on the older pages carry no banned word either.
        for url in (reverse("manufacturing:home"), reverse("manufacturing:work_centres"), reverse("manufacturing:schedule"), reverse("inventory:operations")):
            html = self.client.get(url).content.decode()
            for word in BANNED_WORDS:
                self.assertNotIn(word, html[html.index('class="op-title-row"'):], url)


# ---------------------------------------------------------------------------------------------------------------
# Permissions and entities
# ---------------------------------------------------------------------------------------------------------------


class PermissionTests(OvenPlant):
    MONEY = ("700.00", "350.00", "500.00", "570.00", "1,200.00")
    FORMS = ("data-open-job", "data-take-part", "data-return-part", "data-make-bill", "data-link-bill", "data-unlink-bill", "data-close-job", "data-new-plan", "data-edit-plan")
    COSTS = ("data-part-value", "data-bill-cost", "data-job-cost", "data-history-total", "data-history-parts", "data-history-bills")

    def setUp(self):
        super().setUp()
        self.centre_url = reverse("manufacturing:maintenance_centre", args=[self.oven.pk])
        self.plan = maintenance.save_plan(self.oven, {"title": "تنظيف الشعلات", "every_days": "30"}, self.owner)

    def story(self):
        """An open breakdown on the oven with a part out and a posted bill, after a closed one."""

        closed = self.breakdown(opened_at=at(YESTERDAY, 10))
        maintenance.take_part(closed, self.owner, item=self.element, location=self.plant, quantity="1")
        maintenance.close_job(closed, self.owner, closed_at=at(YESTERDAY, 14))
        job = self.breakdown()
        part = maintenance.take_part(job, self.owner, item=self.element, location=self.plant, quantity="2")
        link = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="500")
        post_purchase_invoice(link.purchase_invoice_id, self.owner)
        return job, part, link

    def pages(self, job):
        return (reverse("manufacturing:maintenance"), self.centre_url, reverse("manufacturing:maintenance_job", args=[job.pk]),
                reverse("manufacturing:work_centres"), reverse("manufacturing:home"))

    def posts(self, job, part, link):
        job_url = reverse("manufacturing:maintenance_job", args=[job.pk])
        return ((self.centre_url, {"action": "open", "kind": "breakdown", "cause": "السير اتقطع"}),
                (self.centre_url, {"action": "plan", "title": "خطة", "every_days": "5"}),
                (job_url, {"action": "close"}), (job_url, {"action": "part", "item": self.element.pk, "location": self.plant.pk, "quantity": "1"}),
                (job_url, {"action": "return_part", "part": part.pk}), (job_url, {"action": "bill", "supplier": self.technician.pk, "amount": "90"}),
                (job_url, {"action": "link", "invoice_number": "X"}), (job_url, {"action": "unlink", "bill": link.pk}))

    def untouched(self, job, part, link):
        self.assertEqual((MaintenanceJob.objects.filter(closed_at__isnull=True).get(), MaintenancePart.objects.count(), MaintenancePurchase.objects.get(),
                          MaintenancePlan.objects.get(), StockOperation.objects.get(pk=part.operation_id).status),
                         (job, 2, link, self.plan, StockOperationStatus.POSTED))

    def test_the_rules_ask_for_the_right_permission_themselves(self):
        viewer = person(RoleCode.SITE_ENGINEER, "mnt_viewer")                        # sees stock, nothing else
        worker = person(RoleCode.PRODUCTION_WORKER, "mnt_worker")                    # the floor screen and stock, nothing else
        keeper = limited("mnt_keeper", "inventory.view_stock", "inventory.adjust_stock")
        buyer = limited("mnt_buyer", "inventory.view_stock", "purchases.create_purchase_invoice")
        planner = limited("mnt_planner", "inventory.view_stock", "master_data.manage_items")
        for user in (viewer, worker, buyer, planner):
            with self.assertRaises(PermissionDenied):
                maintenance.open_job(self.oven, user, kind="breakdown", cause="السير اتقطع")
        for user in (viewer, worker, keeper, buyer):
            with self.assertRaises(PermissionDenied):
                maintenance.save_plan(self.oven, {"title": "خطة", "every_days": "5"}, user)
        maintenance.save_plan(self.oven, {"title": "خطة المخطِّط", "every_days": "5"}, planner)
        job = maintenance.open_job(self.oven, keeper, kind="breakdown", cause="السير اتقطع")   # the stock right opens a job
        for user in (viewer, worker, buyer, planner):
            with self.assertRaises(PermissionDenied):
                maintenance.take_part(job, user, item=self.element, location=self.plant, quantity="1")
            with self.assertRaises(PermissionDenied):
                maintenance.close_job(job, user)
        part = maintenance.take_part(job, keeper, item=self.element, location=self.plant, quantity="2")   # and takes parts, at a cost it never types
        self.assertEqual((part.operation.unit_cost, part.operation.created_by), (D("350.0000"), keeper))
        for user in (viewer, worker, buyer, planner):
            with self.assertRaises(PermissionDenied):
                maintenance.return_part(part, user)
        for user in (viewer, worker, keeper, planner):
            with self.assertRaises(PermissionDenied):
                maintenance.make_bill(job, user, supplier=self.technician, amount="500")
            with self.assertRaises(PermissionDenied):
                maintenance.link_bill(job, user, invoice_number="X")
        link = maintenance.make_bill(job, buyer, supplier=self.technician, amount="500")   # the purchase right alone makes the bill
        self.assertEqual((link.purchase_invoice.total_amount, link.purchase_invoice.created_by), (D("570.00"), buyer))
        for user in (viewer, worker, keeper, planner):
            with self.assertRaises(PermissionDenied):
                maintenance.unlink_bill(link, user)
        maintenance.return_part(part, keeper)
        maintenance.close_job(job, keeper)
        self.assertEqual(MaintenanceJob.objects.get().closed_by, keeper)

    def test_a_cashier_is_refused_and_a_viewer_and_a_worker_read_without_any_form_or_cost(self):
        job, part, link = self.story()
        self.client.force_login(person(RoleCode.CASHIER, "mnt_cashier"))
        for url in self.pages(job)[:3]:
            self.assertEqual(self.client.get(url).status_code, 403)
        for url, data in self.posts(job, part, link):
            self.assertEqual(self.client.post(url, data).status_code, 403)
        for role, name in ((RoleCode.SITE_ENGINEER, "mnt_reader"), (RoleCode.PRODUCTION_WORKER, "mnt_floor_worker")):
            user = person(role, name)
            self.assertEqual([user_has_permission(user, code) for code in (maintenance.VIEW, maintenance.PRODUCE, maintenance.COST, maintenance.BILL, maintenance.MANAGE)],
                             [True, False, False, False, False])
            self.client.force_login(user)
            for url in self.pages(job):
                for lang in ("ar", "en"):
                    page = self.client.get(url, {"lang": lang})
                    self.assertEqual(page.status_code, 200, url)
                    text = page.content.decode()
                    for marker in self.FORMS + self.COSTS + ("data-bill-link",):
                        self.assertNotIn(marker, text, (url, marker))
                    for figure in self.MONEY:
                        self.assertNotIn(figure, text, (url, figure))
            page = self.client.get(self.centre_url)
            self.assertContains(page, "data-centre-down")                           # the state is read by everyone who sees stock
            self.assertContains(page, "data-view-only")
            self.assertContains(page, "data-no-cost")
            self.assertContains(page, link.purchase_invoice.invoice_number)          # the bill's number is said, the way into purchases is not offered
            for url, data in self.posts(job, part, link):
                with self.subTest(role=role, action=data["action"]):
                    self.assertEqual(self.client.post(url, data).status_code, 403)
        self.untouched(job, part, link)

    def test_the_worker_sees_on_the_floor_screen_that_the_work_centre_is_down(self):
        worker = person(RoleCode.PRODUCTION_WORKER, "mnt_line_worker")
        order = orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "10"}, self.owner)
        self.client.force_login(worker)
        floor = self.client.get(reverse("manufacturing:floor"))
        self.assertContains(floor, f'data-floor-card="{order.pk}"')
        self.assertNotContains(floor, "data-floor-down")
        job = self.breakdown()
        floor = self.client.get(reverse("manufacturing:floor"))
        self.assertContains(floor, "data-floor-down")
        self.assertContains(floor, "المركز متوقف للصيانة")
        self.assertContains(self.client.get(reverse("manufacturing:floor"), {"lang": "en"}), "The work centre is down")
        self.assertEqual(self.client.post(reverse("manufacturing:maintenance_job", args=[job.pk]), {"action": "close"}).status_code, 403)
        maintenance.close_job(job, self.owner)
        self.assertNotContains(self.client.get(reverse("manufacturing:floor")), "data-floor-down")

    def test_a_stock_keeper_runs_the_job_without_seeing_any_cost_or_touching_a_bill_or_a_plan(self):
        keeper = limited("mnt_store_keeper", "inventory.view_stock", "inventory.adjust_stock")
        self.client.force_login(keeper)
        self.assertContains(self.client.get(self.centre_url), "data-open-job")
        self.client.post(self.centre_url, {"action": "open", "kind": "breakdown", "cause": "السير اتقطع"})
        job = MaintenanceJob.objects.get()
        job_url = reverse("manufacturing:maintenance_job", args=[job.pk])
        self.client.post(job_url, {"action": "part", "item": self.element.pk, "location": self.plant.pk, "quantity": "2"})
        part = MaintenancePart.objects.get()
        self.assertEqual((part.operation.quantity, part.operation.unit_cost), (D("2.000"), D("350.0000")))
        link = maintenance.make_bill(job, self.owner, supplier=self.technician, amount="500")
        post_purchase_invoice(link.purchase_invoice_id, self.owner)
        for url in self.pages(job):
            text = self.client.get(url).content.decode()
            for marker in self.COSTS + ("data-make-bill", "data-link-bill", "data-unlink-bill", "data-new-plan", "data-edit-plan", "data-bill-link"):
                self.assertNotIn(marker, text, (url, marker))
            for figure in self.MONEY:
                self.assertNotIn(figure, text, (url, figure))
        page = self.client.get(self.centre_url)
        for marker in ("data-take-part", "data-return-part", "data-close-job", "data-no-cost"):
            self.assertContains(page, marker)
        for data in ({"action": "bill", "supplier": self.technician.pk, "amount": "90"}, {"action": "link", "invoice_number": "X"}, {"action": "unlink", "bill": link.pk}):
            self.assertEqual(self.client.post(job_url, data).status_code, 403)
        self.assertEqual(self.client.post(self.centre_url, {"action": "plan", "title": "خطة", "every_days": "5"}).status_code, 403)
        self.client.post(job_url, {"action": "return_part", "part": part.pk})
        self.client.post(job_url, {"action": "close"})
        job.refresh_from_db()
        self.assertEqual((job.closed_by, StockOperation.objects.get(pk=part.operation_id).status), (keeper, StockOperationStatus.CANCELLED))

    def test_a_manager_without_cost_makes_the_bill_and_sees_no_cost(self):
        manager = person(RoleCode.MANAGER, "mnt_manager")                            # adjusts stock and makes purchase invoices; may not see cost
        self.assertEqual([user_has_permission(manager, code) for code in (maintenance.PRODUCE, maintenance.COST, maintenance.BILL)], [True, False, True])
        job, part, link = self.story()
        self.client.force_login(manager)
        for url in self.pages(job):
            text = self.client.get(url).content.decode()
            for marker in self.COSTS:
                self.assertNotIn(marker, text, (url, marker))
            for figure in ("700.00", "350.00", "1,200.00"):
                self.assertNotIn(figure, text, (url, figure))
        page = self.client.get(self.centre_url)
        for marker in ("data-take-part", "data-make-bill", "data-link-bill", "data-close-job", "data-no-cost"):
            self.assertContains(page, marker)
        made = self.client.post(reverse("manufacturing:maintenance_job", args=[job.pk]), {"action": "bill", "supplier": self.technician.pk, "amount": "90"}, follow=True)
        self.assertContains(made, "اتعملت مسودة")
        self.assertEqual(MaintenancePurchase.objects.count(), 2)
        self.assertEqual(PurchaseInvoice.objects.order_by("-pk").first().created_by, manager)


class EntityTests(OvenPlant):
    def test_another_entitys_machines_jobs_and_stock_are_out_of_reach(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BRM", "name_ar": "فرع العبور", "activity_slug": "manufacturing", "sub_activity_slug": "food"}, self.owner)
        branch_plant = make_location(location_code="BR-PLANT-M", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        stock_in(self.element, branch_plant, 5, "350.00", movement_date=BEFORE)
        with working_in(branch):
            far_oven = self.centre("BR-OVEN", "فرن الفرع", "Branch oven")
        self.assertEqual(far_oven.entity, branch)
        near = self.breakdown()
        far = self.breakdown(far_oven)
        maintenance.save_plan(far_oven, {"title": "تنظيف فرن الفرع", "every_days": "5", "last_done": (TODAY - timedelta(days=10)).isoformat()}, self.owner)
        # A part leaves only a location of the job's own entity, whichever entity the person is working in.
        with self.assertRaisesMessage(ValidationError, "اختار مخزن من نفس كيان"):
            maintenance.take_part(far, self.owner, item=self.element, location=self.plant, quantity="1")
        with self.assertRaisesMessage(ValidationError, "اختار مخزن من نفس كيان"):
            maintenance.take_part(near, self.owner, item=self.element, location=branch_plant, quantity="1")
        self.assertEqual(list(maintenance.places(far_oven)), [branch_plant])
        self.assertNotIn(branch_plant, list(maintenance.places(self.oven)))
        main = Entity.objects.get(is_main=True)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": main.pk, "next": "/dashboard/"})
        screen = self.client.get(reverse("manufacturing:maintenance"))
        self.assertContains(screen, 'data-down-centre="OVEN"')
        self.assertNotContains(screen, "BR-OVEN")
        self.assertNotContains(screen, "تنظيف فرن الفرع")
        self.assertContains(screen, "data-tile-due>0<")
        with working_in(main):
            self.assertEqual((list(maintenance.scoped_jobs()), list(maintenance.scoped_plans()), maintenance.counts()), ([near], [], (1, 0)))
        far_centre, far_job = reverse("manufacturing:maintenance_centre", args=[far_oven.pk]), reverse("manufacturing:maintenance_job", args=[far.pk])
        self.assertEqual((self.client.get(far_centre).status_code, self.client.get(far_job).status_code), (404, 404))
        for url, data in ((far_centre, {"action": "open", "kind": "breakdown", "cause": "عطل"}), (far_centre, {"action": "plan", "title": "خطة", "every_days": "5"}),
                          (far_job, {"action": "close"}), (far_job, {"action": "part", "item": self.element.pk, "location": branch_plant.pk, "quantity": "1"}),
                          (far_job, {"action": "bill", "supplier": self.technician.pk, "amount": "90"})):
            with self.subTest(action=data["action"]):
                self.assertEqual(self.client.post(url, data).status_code, 404)
        far.refresh_from_db()
        self.assertEqual((far.closed_at, MaintenancePart.objects.count(), PurchaseInvoice.objects.count()), (None, 0, 0))
        # In the branch the two swap places; the part and the bill made there are the branch's own.
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        screen = self.client.get(reverse("manufacturing:maintenance"))
        self.assertContains(screen, 'data-down-centre="BR-OVEN"')
        self.assertNotContains(screen, 'data-down-centre="OVEN"')
        self.assertContains(screen, "data-tile-due>1<")
        self.assertEqual(self.client.get(reverse("manufacturing:maintenance_job", args=[near.pk])).status_code, 404)
        self.client.post(far_job, {"action": "part", "item": self.element.pk, "location": branch_plant.pk, "quantity": "1"})
        self.client.post(far_job, {"action": "bill", "supplier": self.technician.pk, "amount": "90"})
        part, invoice = MaintenancePart.objects.get(), PurchaseInvoice.objects.get()
        self.assertEqual((part.job, part.operation.source_location, invoice.receiving_location), (far, branch_plant, branch_plant))
        self.assertEqual(get_item_location_stock_quantity(self.element, self.plant), D("10.000"))
        # A purchase invoice of the main entity is not linked to the branch's job.
        with self.assertRaisesMessage(ValidationError, "مفيش فاتورة شراء بالرقم ده في الكيان ده"):
            maintenance.link_bill(near, self.owner, invoice_number=invoice.invoice_number)
        # The schedule of each entity reads its own breakdowns.
        with working_in(branch):
            self.assertEqual([b["centre"] for b in workcentres.load(TODAY, days=1)], [far_oven])
            self.assertEqual(workcentres.load(TODAY, days=1)[0]["days"][0]["capacity"], D("0"))
