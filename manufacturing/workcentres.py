"""Manufacturing F6a (HG-133): work centres, routing and capacity.

Rules, stated once:

* **Work centre**: a machine, a line or a manual table, with the hours it works in a day and one fully loaded rate per
  hour (labour, power and the machine's wear: owner question 15). It belongs to an entity (NULL is the main entity) and
  the screens and the routing only ever see the current entity's centres. Seeing a rate needs ``inventory.view_cost``;
  setting one needs ``master_data.manage_items`` and ``inventory.view_cost`` (you cannot set what you cannot see).
* **Routing**: a recipe's steps. A new order copies them as its stages, ``[key, name_ar, name_en, work_centre_id,
  planned_minutes]``, planned minutes = setup + minutes per batch x batches (the order's batches unrounded; the minutes kept to
  two). A recipe without a step keeps the factory's stage list exactly as ``stages.py`` gives it (three items each).
  An order keeps the list it was opened with; editing the routing later never touches an open order.
* **Load**: the planned minutes of the stages still ahead on open orders, per work centre and day, against
  ``hours_per_day`` x 60. The day is the stage's planned day once F6b has planned it (``schedule``), else the order's due
  date; an overdue order is counted today;
  an order due after the window or with no date is counted apart and never against a day. A holiday
  (``attendance.Holiday``) has no capacity, so any minute planned on it is over capacity.
* **Cost**: the minutes typed on a finished stage, times the stage's work centre's rate per hour, are a *suggested*
  labour line (never a booking). Confirming one adds an ordinary F12 cost line through ``actuals.add_cost_line`` with the
  amount ``minutes x rate / 60`` rounded once (never hours rounded first: 70 minutes is 70.00 at 60.00 an hour, not
  1.17 h x 60.00 = 70.20). A stage is confirmed once: a live line pointing at its log hides the suggestion, and
  cancelling that line brings it back. Nothing reaches stock or the books before F12's finish.

Nothing here touches the inventory engine, average cost, the projector or F12's finish rules.
"""

import re
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope

from .models import CostLineKind, OrderStatus, ProductionOrder, RecipeStep, WorkCentre, WorkCentreKind

ZERO = Decimal("0")
COST, MANAGE = "inventory.view_cost", "master_data.manage_items"
DAYS = 14
STEP_ROWS = 10
MAX_MINUTES = Decimal("99999.99")
OPEN = (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)

MESSAGES = {
    "ar": {
        "code": "اكتب كود مركز العمل (حتى 20 حرف).", "code_dup": "كود مركز العمل ده مستخدم قبل كده.", "name": "اكتب اسم مركز العمل بالعربي.",
        "kind": "اختار نوع مركز العمل.", "hours": "ساعات العمل في اليوم لازم تكون رقم أكبر من صفر ومتعداش 24.",
        "rate": "سعر الساعة لازم يكون رقم مش سالب.", "asset": "الأصل ده مش موجود في سجل الأصول.",
        "step_name": "الصف {n}: اكتب اسم المرحلة.", "step_centre": "الصف {n}: اختار مركز عمل شغال.",
        "step_minutes": "الصف {n}: دقايق الدفعة لازم تكون رقم أكبر من صفر.", "step_setup": "الصف {n}: دقايق التجهيز لازم تكون رقم مش سالب.",
        "minutes": "دقايق المرحلة لازم تكون رقم أكبر من صفر.", "log": "المرحلة دي مش على الأمر ده.", "no_suggestion": "مفيش تكلفة مقترحة للمرحلة دي.",
        "confirmed": "التكلفة دي اتسجلت قبل كده على الأمر.", "line_note": "{name} · {minutes} دقيقة",
        "routing_other": "مراحل الوصفة دي متسجّلة على مراكز عمل كيان تاني، وتتعدّل من الكيان ده.",
        "routing_mixed": "كل مراحل الوصفة لازم تكون على مراكز عمل من نفس الكيان.",
    },
    "en": {
        "code": "Enter the work centre's code (up to 20 characters).", "code_dup": "This work centre code is already used.", "name": "Enter the work centre's Arabic name.",
        "kind": "Choose the kind of work centre.", "hours": "Hours per day must be a number above zero and not more than 24.",
        "rate": "The hourly rate must be a number, not negative.", "asset": "That asset is not in the asset register.",
        "step_name": "Row {n}: enter the stage's name.", "step_centre": "Row {n}: choose an active work centre.",
        "step_minutes": "Row {n}: minutes per batch must be a number above zero.", "step_setup": "Row {n}: setup minutes must be a number, not negative.",
        "minutes": "The stage's minutes must be a number above zero.", "log": "That stage is not on this order.", "no_suggestion": "There is no suggested cost for that stage.",
        "confirmed": "This cost is already on the order.", "line_note": "{name} · {minutes} min",
        "routing_other": "This recipe's stages are on another entity's work centres; edit them from that entity.",
        "routing_mixed": "Every stage of a recipe must be on work centres of the same entity.",
    },
}


def _audit(action, user, obj_type, obj_id, before, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="manufacturing", action=action, object_type=obj_type, object_id=str(obj_id),
                            before_data=before, after_data=after)


def _number(value):
    from .services import _decimal

    return None if value in (None, "") else _decimal(value)


def scoped():
    """The work centres of the entity being worked in (NULL is the main entity)."""

    return entity_scope.scope(WorkCentre.objects, "entity")


def _entity_key(entity_id):
    """An entity id with NULL read as the main entity (records made before ENT-001 belong to it)."""

    if entity_id is not None:
        return entity_id
    from entities.models import Entity

    return Entity.objects.filter(is_main=True).values_list("pk", flat=True).first()


def routing_entity(recipe):
    """The entity a recipe's routing belongs to (the entity of its work centres), or None when it has no step.

    A recipe is shared by every entity, but its routing names machines and their rates, so it belongs to one entity:
    an order opened elsewhere takes the factory's stage list, and another entity can neither see nor replace it."""

    step = recipe.steps.select_related("work_centre").first()
    return _entity_key(step.work_centre.entity_id) if step else None


def routing_visible(recipe):
    """True when the routing (if any) belongs to the entity being worked in, or no single entity is chosen."""

    owner = routing_entity(recipe)
    chosen = entity_scope.entity()
    return owner is None or chosen is None or owner == _entity_key(chosen.pk)


def can_see_rates(user):
    from permissions.services import user_has_permission

    return user_has_permission(user, COST)


# ---- the work centre ----

@transaction.atomic
def save_work_centre(data, user, centre=None, lang="ar"):
    """Create or change a work centre. ``rate_per_hour`` is only read when the key is present, and then it needs the cost
    permission; a form that cannot show the rate leaves it out and the stored rate stays."""

    from fixed_assets.models import FixedAsset
    from permissions.services import user_has_permission

    words = MESSAGES[lang]
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied("Work centres need master_data.manage_items.")
    if "rate_per_hour" in data and not user_has_permission(user, COST):
        raise PermissionDenied("A work centre's rate is a cost; setting it needs inventory.view_cost.")
    code = (data.get("code") or "").strip()
    if not code or len(code) > 20:
        raise ValidationError(words["code"])
    if WorkCentre.objects.filter(code__iexact=code).exclude(pk=centre.pk if centre else None).exists():
        raise ValidationError(words["code_dup"])
    name_ar = (data.get("name_ar") or "").strip()
    if not name_ar:
        raise ValidationError(words["name"])
    kind = data.get("kind")
    if kind not in WorkCentreKind.values:
        raise ValidationError(words["kind"])
    hours = _number(data.get("hours_per_day"))
    if hours is None or hours <= 0 or hours > 24:
        raise ValidationError(words["hours"])
    asset = None
    if data.get("fixed_asset") not in (None, ""):
        asset = FixedAsset.objects.filter(pk=data["fixed_asset"]).first() if str(data["fixed_asset"]).isdigit() else None
        if asset is None:
            raise ValidationError(words["asset"])
    rate = None
    if "rate_per_hour" in data:
        rate = _number(data.get("rate_per_hour")) if data.get("rate_per_hour") not in (None, "") else ZERO
        if rate is None or rate < 0 or rate > Decimal("999999999999.99"):
            raise ValidationError(words["rate"])
        rate = money_round(rate)
    before = {}
    if centre is None:
        centre = WorkCentre(entity=entity_scope.entity())
    else:
        before = {"code": centre.code, "hours_per_day": str(centre.hours_per_day), "rate_per_hour": str(centre.rate_per_hour), "active": centre.active}
    centre.code, centre.name_ar, centre.name_en, centre.kind = code, name_ar, (data.get("name_en") or "").strip()[:120], kind
    centre.hours_per_day, centre.fixed_asset, centre.active = hours.quantize(Decimal("0.01")), asset, bool(data.get("active", True))
    if rate is not None:
        centre.rate_per_hour = rate
    new = centre.pk is None
    centre.save()
    _audit("save_work_centre", user, "manufacturing.WorkCentre", centre.pk, before,
           {"code": centre.code, "hours_per_day": str(centre.hours_per_day), "rate_per_hour": str(centre.rate_per_hour), "active": centre.active},
           AuditEventType.CREATE if new else AuditEventType.UPDATE)
    return centre


# ---- the routing ----

def _key(name_en, position, taken):
    base = re.sub(r"[^a-z0-9]+", "-", (name_en or "").lower()).strip("-")[:24] or f"s{position}"
    key, n = base, 2
    while key in taken:
        key, n = f"{base[:24]}-{n}", n + 1
    return key


@transaction.atomic
def save_routing(recipe, rows, user, lang="ar"):
    """Replace ``recipe``'s routing with ``rows`` (dicts: name_ar, name_en, work_centre, minutes_per_batch, setup_minutes);
    blank rows are skipped, and no row at all removes the routing (the factory's stage list applies again)."""

    from permissions.services import user_has_permission

    words = MESSAGES[lang]
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied("The routing needs master_data.manage_items.")
    if not routing_visible(recipe):
        raise ValidationError(words["routing_other"])
    used = set(recipe.steps.values_list("work_centre_id", flat=True))
    available = {str(c.pk): c for c in scoped().filter(Q(active=True) | Q(pk__in=used))}  # a centre switched off since stays usable on the recipe that has it
    clean, taken = [], set()
    for n, row in enumerate(rows, 1):
        if not any(str(row.get(k) or "").strip() for k in ("name_ar", "name_en", "work_centre", "minutes_per_batch", "setup_minutes")):
            continue
        name_ar = (row.get("name_ar") or "").strip()
        if not name_ar:
            raise ValidationError(words["step_name"].format(n=n))
        centre = available.get(str(row.get("work_centre") or ""))
        if centre is None:
            raise ValidationError(words["step_centre"].format(n=n))
        minutes = _number(row.get("minutes_per_batch"))
        if minutes is None or minutes <= 0 or minutes > MAX_MINUTES:
            raise ValidationError(words["step_minutes"].format(n=n))
        setup = ZERO if row.get("setup_minutes") in (None, "") else _number(row.get("setup_minutes"))
        if setup is None or setup < 0 or setup > MAX_MINUTES:
            raise ValidationError(words["step_setup"].format(n=n))
        key = _key((row.get("name_en") or "").strip(), len(clean) + 1, taken)
        taken.add(key)
        clean.append(RecipeStep(recipe=recipe, order=len(clean) + 1, stage_key=key, name_ar=name_ar[:120], name_en=(row.get("name_en") or "").strip()[:120],
                                work_centre=centre, minutes_per_batch=minutes.quantize(Decimal("0.01")), setup_minutes=setup.quantize(Decimal("0.01"))))
    if len({_entity_key(step.work_centre.entity_id) for step in clean}) > 1:  # the whole group view lists every entity's centres
        raise ValidationError(words["routing_mixed"])
    before = [step.stage_key for step in recipe.steps.all()]
    recipe.steps.all().delete()
    RecipeStep.objects.bulk_create(clean)
    _audit("save_routing", user, "manufacturing.Recipe", recipe.pk, {"steps": before}, {"steps": [step.stage_key for step in clean]})
    return clean


def batches_of(quantity, recipe):
    """The order's batches, unrounded: planned minutes round once, at the end (Codex review on #440: one unit of a
    3,000-unit batch at 900 minutes is 0.30 minutes, not 0.000 batches x 900 = 0)."""

    return Decimal(quantity) / recipe.output_quantity if recipe.output_quantity else ZERO


def planned_minutes(step, batches):
    """Setup + minutes per batch x batches, kept to two decimals."""

    return (step.setup_minutes + step.minutes_per_batch * batches).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def stages_for(recipe, quantity, location=None):
    """The stages a new order of ``quantity`` units of ``recipe`` at ``location`` is opened with, or None when the recipe
    has no routing, or its routing belongs to another entity than the location's (the caller then copies the factory's
    list as it always has): an order never takes another entity's machines or rates."""

    steps = list(recipe.steps.select_related("work_centre"))
    if not steps:
        return None
    if location is not None and _entity_key(steps[0].work_centre.entity_id) != _entity_key(location.entity_id):
        return None
    batches = batches_of(quantity, recipe)
    return [[step.stage_key, step.name_ar, step.name_en or step.name_ar, step.work_centre_id, str(planned_minutes(step, batches))] for step in steps]


def stage_plan(stage):
    """(work centre id, planned minutes) of an order's stage; (None, None) for a stage with no work centre."""

    if len(stage) >= 5 and stage[3]:
        return stage[3], Decimal(str(stage[4]))
    return None, None


def plan_rows(order, lang="ar"):
    """[{key, label, centre, minutes}] for an order's routed stages, in order (empty when the order has no routing)."""

    centres = {c.pk: c for c in WorkCentre.objects.filter(pk__in=[s[3] for s in order.stages if len(s) >= 5 and s[3]])}
    rows = []
    for stage in order.stages:
        centre_id, minutes = stage_plan(stage)
        if centre_id:
            rows.append({"key": stage[0], "label": stage[2] if lang == "en" else stage[1], "centre": centres.get(centre_id), "minutes": minutes})
    return rows


def planned_conversion(order):
    """What the routing would cost at the work centres' current rates: the sum of each stage's planned minutes x rate / 60.
    A figure to read (it needs the cost permission to show); it is not written to the order and books nothing."""

    return money_round(sum((money_round(row["minutes"] * row["centre"].rate_per_hour / 60) for row in plan_rows(order) if row["centre"]), ZERO))


# ---- the suggested lines ----

def _live(order):
    return {line.stage_log_id: line for line in order.cost_lines.filter(cancelled_at__isnull=True, stage_log__isnull=False)}


def suggestions(order, lang="ar"):
    """[{log, label, centre, minutes, amount, line}] for every stage that recorded minutes on a routed stage; ``line`` is
    the live cost line that confirmed it (None while it is only a suggestion)."""

    plans = {stage[0]: stage_plan(stage)[0] for stage in order.stages}
    names = {stage[0]: (stage[2] if lang == "en" else stage[1]) for stage in order.stages}
    centres = {c.pk: c for c in WorkCentre.objects.filter(pk__in=[p for p in plans.values() if p])}
    live, rows = _live(order), []
    for log in order.stage_logs.all():
        centre = centres.get(plans.get(log.stage_key))
        if log.minutes is None or centre is None:
            continue
        line = live.get(log.pk)
        # A confirmed stage shows what its cost line saved (and the finish absorbs), not today's rate (Codex review on #440).
        amount = line.amount if line is not None else money_round(log.minutes * centre.rate_per_hour / 60)
        rows.append({"log": log, "label": names.get(log.stage_key, log.stage_key), "centre": centre, "minutes": log.minutes, "amount": amount,
                     "line": line})
    return rows


@transaction.atomic
def confirm(order, user, log_pk, lang="ar"):
    """«أكّد التكلفة»: one stage's suggestion becomes an ordinary labour line of the order (F12), once."""

    from permissions.services import user_has_permission

    from . import actuals

    words = MESSAGES[lang]
    if not user_has_permission(user, COST):
        raise PermissionDenied("Actual costs go into the product's stock cost; they need inventory.view_cost.")
    order = actuals._open_for_lines(order, actuals.MESSAGES[lang])  # locks the order: two clicks cannot both pass the check below
    wanted = str(log_pk or "")
    row = next((r for r in suggestions(order, lang) if wanted.isdigit() and r["log"].pk == int(wanted)), None)
    if row is None:
        raise ValidationError(words["no_suggestion"] if wanted.isdigit() and order.stage_logs.filter(pk=wanted).exists() else words["log"])
    if row["line"] is not None:
        raise ValidationError(words["confirmed"])
    if row["amount"] <= 0:
        raise ValidationError(words["no_suggestion"])
    note = words["line_note"].format(name=f"{row['centre'].name(lang)} · {row['label']}", minutes=f"{row['minutes'].normalize():f}")
    return actuals.add_cost_line(order, user, kind=CostLineKind.LABOUR, description=note, amount=row["amount"], work_centre=row["centre"],
                                 stage_log=row["log"], lang=lang)


def parse_minutes(value, lang="ar"):
    """The minutes typed on a stage: None when blank, else a number above zero."""

    if value in (None, ""):
        return None
    number = _number(value)
    if number is None or number <= 0 or number > MAX_MINUTES:
        raise ValidationError(MESSAGES[lang]["minutes"])
    return number.quantize(Decimal("0.01"))


# ---- the load ----

def load(today, days=DAYS):
    """Per active work centre of the entity: ``days`` days from ``today`` with the planned minutes, the capacity in
    minutes and whether it is over, plus the minutes with no date and those due after the window."""

    from . import schedule

    last = today + timedelta(days=days - 1)
    holidays = schedule.holidays()
    centres = list(scoped().filter(active=True).order_by("code"))
    per_day, unscheduled, later = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    orders = entity_scope.scope(ProductionOrder.objects, "location__entity").filter(status__in=OPEN).prefetch_related("steps__work_centre")
    for order in orders:
        # Manufacturing F6b (HG-134): a stage with planned dates loads the days and the centre it is planned on; the rest keep the due-date rule.
        planned = {step.index: step for step in order.steps.all() if step.planned_start and step.planned_end and step.finished_at is None and step.work_centre_id}
        for index, stage in enumerate(order.stages):
            if index < order.stage_index:
                continue
            if index in planned:
                for day, minutes in schedule.day_minutes(planned[index], holidays):
                    if day > last:
                        later[planned[index].work_centre_id] += minutes
                    else:
                        per_day[(planned[index].work_centre_id, max(day, today))] += minutes
                continue
            centre_id, minutes = stage_plan(stage)
            if not centre_id:
                continue
            if order.due_date is None:
                unscheduled[centre_id] += minutes
            elif order.due_date > last:
                later[centre_id] += minutes
            else:
                per_day[(centre_id, max(order.due_date, today))] += minutes
    result = []
    for centre in centres:
        capacity = (centre.hours_per_day * 60).quantize(Decimal("0.01"))
        rows = []
        for offset in range(days):
            day = today + timedelta(days=offset)
            minutes, holiday = per_day[(centre.pk, day)], day in holidays
            cap = ZERO if holiday else capacity
            rows.append({"date": day, "minutes": minutes, "capacity": cap, "holiday": holiday, "over": minutes > cap})
        result.append({"centre": centre, "days": rows, "unscheduled": unscheduled[centre.pk], "later": later[centre.pk],
                       "over": any(row["over"] for row in rows)})
    return result
