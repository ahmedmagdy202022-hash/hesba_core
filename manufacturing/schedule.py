"""Manufacturing F6b (HG-134): the production schedule, «رتّب», moving an order, and delay warnings.

Rules, stated once:

* **Steps**: every order has one ``OrderStep`` per stage, made with the order from its stage list (an order that existed before
  gets them lazily, the first time anything asks: ``ensure_steps``). A step names the stage, the work centre it is planned on
  (NULL for a stage with none), its planned minutes, and ``planned_start`` / ``planned_end`` (NULL until planned). Advancing a
  stage fills that step's ``finished_at``.
* **The planning clock** is virtual: a working day is the centre's ``hours_per_day`` of working minutes, stored on the day's
  local midnight plus those minutes. Read a step's day through ``day_of`` / ``end_day``; never as wall-clock time.
* **«رتّب»** (``arrange``) forward-schedules every open order of the entity from a start day. Orders are taken by due date
  (the sales order's ``deliver_by`` when there is one, else the order's own date; no date goes last), then the user's own rank
  (``ProductionOrder.schedule_rank``, lowest first), then the order they were opened in. Each step starts when the previous
  step of its order ends and its work centre is free; a centre is one queue (no gap is back-filled).
  - A stage is **never split across days**: if it does not fit in what is left of the day it starts the next working day.
  - A stage **longer than a day** takes whole working days: it starts on a day the centre has not touched, holds that many
    working days, and the centre is next free the working day after (the last day's remainder is not offered to another stage).
  - A **holiday** (``attendance.Holiday``) has no capacity and is skipped. Weekly days off are not read: enter them as holidays.
  - Steps already finished are left as they are. Finished stages do not move, but a planned order's unfinished stages do.
* **Default for a stage with no work centre** (an unrouted order, or a stage the routing gave none): it sits on its own lane that
  uses no centre's capacity and is never queued behind another order; with no planned minutes it is given one whole day
  (``DEFAULT_MINUTES``), a placeholder until a routing says better.
* **Moving** (``move_order``): the user picks another day (the order's unfinished steps are laid forward from it, queueing only
  behind the order's own earlier steps) and/or another work centre of the same kind for one step. A move over capacity is
  **allowed**: the result carries a warning per centre-day that is now over (owner question 16). A moved step is still priced
  at its stage's original work centre by F6a's suggestions: the move changes where and when, never the cost path.
* **Late**: an order is late when it is past its due date and not done, or when its last planned end is after the due date.
* **Load**: ``workcentres.load`` reads a step's planned days and centre where it has them, else the due date as before.
* **Breakdowns** (F10, HG-141): a day's capacity on a work centre is its hours less the minutes a breakdown was open on that
  day, and nothing today while one is still open (``maintenance.downtime``). The grid and the over-capacity warnings read it;
  «رتّب» and a move do not plan around it: the day shows over capacity and a person moves the order.

Nothing here touches stock, cost, the inventory engine, average cost, the projector or F12's finish.
"""

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope

from . import workcentres
from .models import OrderStageLog, OrderStatus, OrderStep, ProductionOrder, WorkCentre

ZERO = Decimal("0")
PRODUCE = "inventory.adjust_stock"
OPEN = (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)
DEFAULT_MINUTES = Decimal("480")   # a stage with no centre and no minutes: one whole day of 8 hours
DEFAULT_CAPACITY = Decimal("480")  # the day's length on the lane with no centre
HORIZON = 366                      # days past the start a search may go before it gives up
DAYS = 14

MESSAGES = {
    "ar": {"state": "الأمر ده مش مفتوح.", "day": "اختار يوم صحيح.", "past": "اختار النهارده أو يوم بعده.", "nothing": "اختار يوم أو مركز عمل.",
           "step": "المرحلة دي مش على الأمر ده أو خلصت.", "no_centre": "المرحلة دي مالهاش مركز عمل عشان تتنقل.",
           "kind": "المركز الجديد لازم يكون شغال ومن نفس نوع المركز الحالي.", "horizon": "مفيش يوم شغل فاضي في الـ 366 يوم الجايين: راجع الإجازات.",
           "rank": "الترتيب لازم يكون رقم صحيح من صفر وأكتر، أو سيبه فاضي."},
    "en": {"state": "That order is not open.", "day": "Choose a valid day.", "past": "Choose today or a later day.", "nothing": "Choose a day or a work centre.",
           "step": "That stage is not on this order, or it is finished.", "no_centre": "That stage has no work centre to move.",
           "kind": "The new centre must be active and of the same kind as the current one.", "horizon": "No free working day in the next 366 days: check the holidays.",
           "rank": "The rank must be a whole number, zero or more, or left empty."},
}


def _audit(action, user, order, before, after):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="manufacturing", action=action, object_type="manufacturing.ProductionOrder",
                            object_id=str(order.pk) if order else "", before_data=before, after_data=after)


# ---- the planning clock ----

def _at(day, minutes):
    """The virtual time ``minutes`` into ``day``'s working time: local midnight plus the minutes."""

    return timezone.make_aware(datetime.combine(day, datetime.min.time()) + timedelta(minutes=float(minutes)))


def day_of(moment):
    return timezone.localtime(moment).date()


def end_day(step):
    """The day a step's planned work ends on (an end exactly at the day's close still belongs to that day)."""

    if step.planned_end is None:
        return None
    return day_of(step.planned_end - timedelta(seconds=1)) if step.planned_start and step.planned_end > step.planned_start else day_of(step.planned_end)


def holidays():
    from attendance.models import Holiday

    return set(Holiday.objects.values_list("date", flat=True))


class Calendar:
    """Working days: every day except an ``attendance.Holiday``."""

    def __init__(self, lang="ar", closed=None):
        self.lang, self.closed = lang, holidays() if closed is None else closed

    def working(self, day):
        return day not in self.closed

    def next_working(self, day):
        """The first working day on or after ``day``."""

        for _ in range(HORIZON + 1):
            if day not in self.closed:
                return day
            day += timedelta(days=1)
        raise ValidationError(MESSAGES[self.lang]["horizon"])


def _lay(cal, minutes, earliest, capacity, force=False):
    """Place ``minutes`` of work on a centre of ``capacity`` minutes a day, not before ``earliest`` = (day, minutes into the day).
    Returns (start, end, next_free), each (day, minutes into the day). ``force`` keeps ``earliest``'s day even when it is a
    holiday (a person's own pick)."""

    day, offset = earliest
    if not force:
        working = cal.next_working(day)
        if working != day:
            day, offset = working, ZERO
    if minutes > capacity:  # whole working days, from the start of a day
        if offset > 0:
            day, offset = cal.next_working(day + timedelta(days=1)), ZERO
        days = math.ceil(minutes / capacity)
        last = day
        for _ in range(days - 1):
            last = cal.next_working(last + timedelta(days=1))
        end = (last, minutes - capacity * (days - 1))
        return (day, ZERO), end, (cal.next_working(last + timedelta(days=1)), ZERO)
    if offset > 0 and offset + minutes > capacity:  # never split across days: it starts the next working day
        day, offset = cal.next_working(day + timedelta(days=1)), ZERO
    end = (day, offset + minutes)
    return (day, offset), end, end


# ---- the steps ----

def ensure_steps(order):
    """The order's steps, made from its stage list when any is missing (an order opened before F6b gets them here, lazily).
    Planned dates are never set here. A stage already done takes its finish time from the stage log. Returns the steps."""

    steps = list(order.steps.select_related("work_centre"))
    new = _missing(order, steps)
    if new:
        OrderStep.objects.bulk_create(new, ignore_conflicts=True)
        steps = list(order.steps.select_related("work_centre"))
    return steps


def _missing(order, steps):
    """The steps the order's stage list has and ``steps`` does not, unsaved."""

    have = {step.index for step in steps}
    if len(have) >= len(order.stages):
        return []
    known = set(WorkCentre.objects.filter(pk__in=[s[3] for s in order.stages if len(s) >= 5 and s[3]]).values_list("pk", flat=True))
    done = {}
    for log in OrderStageLog.objects.filter(order=order).order_by("done_at", "pk"):
        done[log.stage_key] = log.done_at
    new = []
    for index, stage in enumerate(order.stages):
        if index in have:
            continue
        centre_id, minutes = workcentres.stage_plan(stage)
        new.append(OrderStep(order=order, index=index, stage_key=stage[0], work_centre_id=centre_id if centre_id in known else None, planned_minutes=minutes,
                             finished_at=done.get(stage[0]) if index < order.stage_index else None))
    return new


def step_done(order, index, when=None):
    """A stage was advanced: its step is finished now (the steps are made first for an order that has none)."""

    ensure_steps(order)
    OrderStep.objects.filter(order=order, index=index, finished_at__isnull=True).update(finished_at=when or timezone.now())


def _capacity(step):
    return (step.work_centre.hours_per_day * 60).quantize(Decimal("0.01")) if step.work_centre_id else DEFAULT_CAPACITY


def _minutes(step):
    return step.planned_minutes if step.planned_minutes else (ZERO if step.work_centre_id else DEFAULT_MINUTES)


def _open_orders():
    return (entity_scope.scope(ProductionOrder.objects, "location__entity").filter(status__in=OPEN)
            .select_related("recipe__product", "customer", "sales_order_line__order", "location"))


def effective_due(order):
    """The date the order is promised for: its sales order's ``deliver_by`` (F1) when there is one, else its own due date."""

    line = order.sales_order_line if order.sales_order_line_id else None
    return (line.order.deliver_by if line is not None and line.order.deliver_by else None) or order.due_date


def priority_key(order):
    due = effective_due(order)
    return (due is None, due or date.max, order.schedule_rank is None, order.schedule_rank or 0, order.pk)


def _pending(order, steps):
    return [s for s in steps if s.index >= order.stage_index and s.finished_at is None]


def _place_order(cal, order, steps, first, cursors, force_first, lane_shared=True):
    """Lay the unfinished ``steps`` of one order forward from day ``first`` (None = the plan's start already in hand)."""

    ready = (first, ZERO)
    placed = []
    for n, step in enumerate(steps):
        capacity, minutes = _capacity(step), _minutes(step)
        earliest = max(ready, cursors[step.work_centre_id]) if (lane_shared and step.work_centre_id) else ready
        start, end, free = _lay(cal, minutes, earliest, capacity, force=force_first and n == 0)
        if lane_shared and step.work_centre_id:
            cursors[step.work_centre_id] = free
        step.planned_start, step.planned_end = _at(*start), _at(*end)
        ready = end
        placed.append(step)
    return placed


@transaction.atomic
def arrange(start, user, lang="ar"):
    """«رتّب»: forward-schedule every open order of the entity from ``start`` (see the module's rules). Replaces earlier planned
    dates of unfinished steps, a person's moves included. Returns {"orders": n, "steps": n}."""

    if not isinstance(start, date):
        raise ValidationError(MESSAGES[lang]["day"])
    if not _can(user):
        raise PermissionDenied("The schedule needs inventory.adjust_stock.")
    cal, cursors = Calendar(lang), defaultdict(lambda: (date.min, ZERO))
    changed = []
    count = 0
    for order in sorted(_open_orders(), key=priority_key):
        pending = _pending(order, ensure_steps(order))
        if not pending:
            continue
        for step in pending:  # the step's work centre is read for its hours: fetch them once
            step.order = order
        changed += _place_order(cal, order, pending, start, cursors, False)
        count += 1
    OrderStep.objects.bulk_update(changed, ["planned_start", "planned_end"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="manufacturing", action="arrange_schedule", object_type="manufacturing.OrderStep",
                            object_id="", after_data={"start": str(start), "orders": count, "steps": len(changed)})
    return {"orders": count, "steps": len(changed)}


def _can(user):
    from permissions.services import user_has_permission

    return user_has_permission(user, PRODUCE)


# ---- moving ----

@transaction.atomic
def move_order(order, user, *, day=None, step_index=None, centre=None, lang="ar"):
    """Move an open order to another ``day`` (its unfinished steps are laid forward from it, queueing only behind the order's own
    earlier steps) and/or move step ``step_index`` to another work centre ``centre`` of the same kind. A move over capacity is
    allowed. Returns {"warnings": [{centre, day, minutes, capacity}]} for every centre-day the move touched that is now over."""

    words = MESSAGES[lang]
    if not _can(user):
        raise PermissionDenied("Moving an order needs inventory.adjust_stock.")
    order = ProductionOrder.objects.select_for_update(of=("self",)).select_related("sales_order_line__order").get(pk=order.pk)
    if order.status not in OPEN:
        raise ValidationError(words["state"])
    if day is None and centre is None:
        raise ValidationError(words["nothing"])
    if day is not None and day < timezone.localdate():
        raise ValidationError(words["past"])
    steps = ensure_steps(order)
    pending = _pending(order, steps)
    before = {s.index: [str(s.planned_start or ""), str(s.planned_end or ""), s.work_centre_id] for s in pending}
    touched = set()
    if centre is not None:
        step = next((s for s in pending if s.index == step_index), None)
        if step is None:
            raise ValidationError(words["step"])
        if not step.work_centre_id:
            raise ValidationError(words["no_centre"])
        old = step.work_centre
        if not workcentres.scoped().filter(pk=centre.pk, active=True, kind=old.kind).exists():
            raise ValidationError(words["kind"])
        step.work_centre = centre
        step.save(update_fields=["work_centre"])
        if step.planned_start:
            touched |= {(centre.pk, d) for d, _ in _step_days(step, holidays())}
    if day is not None:
        cal = Calendar(lang)
        for step in pending:
            step.order = order
        placed = _place_order(cal, order, pending, day, defaultdict(lambda: (date.min, ZERO)), True, lane_shared=False)
        OrderStep.objects.bulk_update(placed, ["planned_start", "planned_end"])
        for step in placed:
            if step.work_centre_id:
                touched |= {(step.work_centre_id, d) for d, _ in _step_days(step, cal.closed)}
    after = {s.index: [str(s.planned_start or ""), str(s.planned_end or ""), s.work_centre_id] for s in pending}
    _audit("move_order", user, order, before, after)
    return {"warnings": warnings_for(touched)}


def set_rank(order, raw, user, lang="ar"):
    """The user's own rank among orders with the same due date (lowest first); blank clears it."""

    if not _can(user):
        raise PermissionDenied("The schedule needs inventory.adjust_stock.")
    text = str(raw if raw is not None else "").strip()
    if text and not (text.isdigit() and len(text) <= 6):
        raise ValidationError(MESSAGES[lang]["rank"])
    value = int(text) if text else None
    before = order.schedule_rank
    ProductionOrder.objects.filter(pk=order.pk).update(schedule_rank=value)
    order.schedule_rank = value
    _audit("set_schedule_rank", user, order, {"rank": before}, {"rank": value})
    return value


# ---- reading the plan ----

def _step_days(step, closed):
    """[(day, minutes)] a planned step loads on its centre: all on one day when it fits one; a longer one fills each working day
    up to the centre's capacity as it is now and puts what is left on its last, so the days always sum to the planned minutes."""

    start, end = day_of(step.planned_start), end_day(step)
    minutes = step.planned_minutes or ZERO
    if end <= start:
        return [(start, minutes)]
    days, day = [start], start
    while day < end:
        day += timedelta(days=1)
        if day not in closed:
            days.append(day)
    if len(days) == 1:
        return [(start, minutes)]
    capacity = (step.work_centre.hours_per_day * 60).quantize(Decimal("0.01")) if step.work_centre_id else DEFAULT_CAPACITY
    # The days always sum to the planned minutes, whatever the centre's hours are now (a step moved to a centre with longer
    # days, or a centre whose hours were edited): each day takes what is left up to its capacity, the last takes the rest.
    rows, left = [], minutes
    for d in days[:-1]:
        take = min(capacity, left)
        rows.append((d, take))
        left -= take
    rows.append((days[-1], left))
    return rows


def day_minutes(step, closed):
    """The days and minutes a planned step loads (see ``_step_days``); exposed for ``workcentres.load``."""

    return _step_days(step, closed)


def planned_steps(order_ids=None):
    """Unfinished planned steps of open orders of the entity (the stage still ahead on the order), with centre and order."""

    steps = (OrderStep.objects.filter(order__in=entity_scope.scope(ProductionOrder.objects, "location__entity").filter(status__in=OPEN),
                                      planned_start__isnull=False, planned_end__isnull=False, finished_at__isnull=True)
             .select_related("order__recipe__product", "work_centre").order_by("planned_start", "order_id", "index"))
    if order_ids is not None:
        steps = steps.filter(order_id__in=order_ids)
    return [s for s in steps if s.index >= s.order.stage_index]


def planned_totals():
    """{(centre id, day): planned minutes} over every planned step of the entity's open orders."""

    closed, totals = holidays(), defaultdict(lambda: ZERO)
    for step in planned_steps():
        if step.work_centre_id:
            for day, minutes in _step_days(step, closed):
                totals[(step.work_centre_id, day)] += minutes
    return totals


def warnings_for(pairs):
    """[{centre, day, minutes, capacity}] for each (centre id, day) of ``pairs`` whose planned minutes pass the day's capacity."""

    if not pairs:
        return []
    from . import maintenance

    closed, totals = holidays(), planned_totals()
    centres = {c.pk: c for c in WorkCentre.objects.filter(pk__in={p[0] for p in pairs})}
    down = maintenance.downtime(list(centres), min(p[1] for p in pairs), max(p[1] for p in pairs))  # F10 (HG-141)
    result = []
    for centre_id, day in sorted(pairs, key=lambda p: (p[1], p[0])):
        centre = centres[centre_id]
        capacity = ZERO if day in closed else maintenance.capacity_left((centre.hours_per_day * 60).quantize(Decimal("0.01")), down[(centre_id, day)])
        if totals[(centre_id, day)] > capacity:
            result.append({"centre": centre, "day": day, "minutes": totals[(centre_id, day)], "capacity": capacity})
    return result


def late_orders(orders, today):
    """{order pk: "overdue" | "planned"} for the late ones of ``orders`` (see the module's rules). One query for all their steps."""

    orders = list(orders)
    ends = {}
    for step in OrderStep.objects.filter(order__in=[o.pk for o in orders], planned_end__isnull=False):
        day = end_day(step)
        if day and (step.order_id not in ends or day > ends[step.order_id]):
            ends[step.order_id] = day
    late = {}
    for order in orders:
        due = effective_due(order)
        if due is None or order.status == OrderStatus.DONE:
            continue
        if due < today:
            late[order.pk] = "overdue"
        elif order.pk in ends and ends[order.pk] > due:
            late[order.pk] = "planned"
    return late


def grid(start, days=DAYS, centre=None, lang="ar"):
    """The schedule screen's data: the ``days`` days from ``start``; one lane per active work centre of the entity (or just
    ``centre``), each day with its planned bars, minutes, capacity and whether it is over; a lane for stages with no centre; and
    the open orders with their planned span, whether they are late, and their rank."""

    window = [start + timedelta(days=n) for n in range(days)]
    closed, today = holidays(), timezone.localdate()
    steps = planned_steps()
    orders = sorted(_open_orders(), key=priority_key)
    late = late_orders(orders, today)
    centres = list(workcentres.scoped().filter(active=True).order_by("code"))
    if centre is not None:
        centres = [c for c in centres if c.pk == centre.pk]
    cells = defaultdict(list)
    for step in steps:
        for day, minutes in _step_days(step, closed):
            if start <= day <= window[-1]:
                stages = step.order.stages
                label = (stages[step.index][2 if lang == "en" else 1]) if step.index < len(stages) else step.stage_key
                cells[(step.work_centre_id, day)].append({"step": step, "order": step.order, "minutes": minutes, "late": step.order_id in late, "label": label})
    from . import maintenance

    # F10 (HG-141): a breakdown takes the minutes it was open out of its day's capacity; an open one, the whole of today.
    down = maintenance.downtime([c.pk for c in centres], window[0], window[-1]) if window else {}
    broken = maintenance.open_breakdowns([c.pk for c in centres])
    lanes = []
    for lane_centre in centres:
        capacity = (lane_centre.hours_per_day * 60).quantize(Decimal("0.01"))
        rows = []
        for day in window:
            items = cells[(lane_centre.pk, day)]
            minutes = sum((item["minutes"] for item in items), ZERO)
            cap = ZERO if day in closed else maintenance.capacity_left(capacity, down[(lane_centre.pk, day)])
            for item in items:
                item["share"] = min(int(item["minutes"] * 100 / capacity), 100) if capacity else 0
            rows.append({"date": day, "items": items, "minutes": minutes, "capacity": cap, "holiday": day in closed, "over": minutes > cap,
                         "down": day not in closed and cap < capacity})
        lanes.append({"centre": lane_centre, "days": rows, "over": any(r["over"] for r in rows), "broken": broken.get(lane_centre.pk)})
    if centre is None:
        loose = [{"date": day, "items": cells[(None, day)], "minutes": ZERO, "capacity": None, "holiday": day in closed, "over": False} for day in window]
        for row in loose:
            for item in row["items"]:
                item["share"] = 0
        lanes.append({"centre": None, "days": loose, "over": False})
    day_list = [{"date": day, "holiday": day in closed,
                 "lines": [{"centre": lane["centre"], "row": row} for lane in lanes for row in lane["days"] if row["date"] == day and row["items"]]} for day in window]
    by_order = defaultdict(list)
    for step in OrderStep.objects.filter(order__in=[o.pk for o in orders]).order_by("index"):
        by_order[step.order_id].append(step)
    rows = []
    for order in orders:
        planned = [s for s in by_order[order.pk] if s.planned_start and s.index >= order.stage_index and s.finished_at is None]
        rows.append({"order": order, "due": effective_due(order), "late": late.get(order.pk, ""), "rank": order.schedule_rank,
                     "first": min((day_of(s.planned_start) for s in planned), default=None), "last": max((end_day(s) for s in planned), default=None),
                     "planned": bool(planned)})
    return {"days": window, "lanes": lanes, "orders": rows, "late_count": len(late), "start": start, "closed": closed, "day_list": day_list}


def order_plan(order, lang="ar"):
    """The order page's planned dates: one row per step with its centre, planned minutes, planned span and finish."""

    steps = list(order.steps.select_related("work_centre"))
    steps = sorted(steps + _missing(order, steps), key=lambda s: s.index)  # a read never writes: an order with no steps yet shows its stages unplanned
    centres = {c.pk: c for c in WorkCentre.objects.filter(pk__in=[s.work_centre_id for s in steps if s.work_centre_id])}
    names = {s[0]: (s[2] if lang == "en" else s[1]) for s in order.stages}
    rows = []
    for step in steps:
        rows.append({"step": step, "label": names.get(step.stage_key, step.stage_key), "centre": centres.get(step.work_centre_id),
                     "start": day_of(step.planned_start) if step.planned_start else None, "end": end_day(step) if step.planned_end else None,
                     "open": step.index >= order.stage_index and step.finished_at is None and order.status in OPEN})
    return rows
