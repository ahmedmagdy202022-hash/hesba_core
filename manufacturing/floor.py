"""Manufacturing F8 (HG-140): the floor screen of the production worker («شغلي»).

Rules, stated once:

* **Who.** The screen and its two actions need ``manufacturing.record_stage`` (the production worker's permission) or
  ``inventory.adjust_stock`` (the supervisor, who can already do all of it from the order page). Nothing here asks for
  or shows a cost.
* **My work.** The current stage of every open order of the entity being worked in, earliest promised date first (the
  sales order's delivery date when the order is made for one, else its own due date; orders with no date last). A
  worker whose employee is ticked on one or more work centres (``WorkCentre.workers``) sees the stages of those centres,
  and the stages that have no work centre at all, which belong to nobody else. A worker on no centre sees every stage.
  The stage's centre is its schedule step's (a move on the schedule changes it), else the one its routing gave it.
* **Start.** «ابدأ» writes ``OrderStep.started_at`` and nothing else: no stock moves and the order's status does not
  change. On an order that has not begun, the start is refused while a material is short at the order's place, so the
  worker hears it before the work and not after it.
* **Done.** «خلصت» closes the stage through the same ``orders.advance`` the order page uses: good and defective units, a
  note, and the minutes since the start (the clock's minutes, to two decimals, never typed here). On the first stage that
  ``advance`` issues the order's materials; this is the only place that passes the stand-in permission
  (``inventory.services.ADJUSTMENT_STAND_INS``), so a worker without ``inventory.adjust_stock`` can do it and can do no
  other stock adjustment. A stage has to be started here before it is closed here.
* **Not from the floor.** A quality stage whose recipe has criteria (F5, HG-136) is recorded by the supervisor on the
  order page with its criteria; a stage that is at an outside supplier (F4, HG-137) is closed by its return. Both show
  on the screen as waiting, with who handles them, and both actions refuse them. Finishing or cancelling an order, extra
  issues, returns, costs and labour lines are not here at all.
* **Photos.** A photo of a defect is kept on the production order through the attachments module (owner
  ``manufacturing.productionorder``); it books nothing.

The inventory engine, average cost, the projector and ``orders.finish`` are not touched.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope

from . import orders, outside, quality, schedule, services, workcentres
from .models import OrderStatus, OrderStep, OutsideJob, OutsideStatus, ProductionOrder, QualityCriterion, WorkCentre

RECORD = "manufacturing.record_stage"
PRODUCE = "inventory.adjust_stock"
OPEN = (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)
ORDER_OWNER = "manufacturing.productionorder"

#: a card's state
READY, RUNNING, QUALITY, OUTSIDE = "ready", "running", "quality", "outside"

MESSAGES = {
    "ar": {
        "moved": "الأمر ده مبقاش في المرحلة دي؛ حدّث الصفحة.",
        "started": "المرحلة دي اتبدأت قبل كده.",
        "start_first": "اضغط «ابدأ» الأول، وبعدها سجّل إن المرحلة خلصت.",
        "quality": "دي مرحلة فحص الجودة، والمشرف هو اللي بيسجّل نتيجتها من صفحة الأمر.",
        "outside": "المرحلة دي عند المورّد «{supplier}»، والمشرف هو اللي بيسجّل رجوعها.",
        "short": "خامة «{item}» مش كفاية في المخزن: الأمر محتاج {need} والموجود {have}. بلّغ المشرف.",
    },
    "en": {
        "moved": "This order is no longer at that stage; reload the page.",
        "started": "This stage was already started.",
        "start_first": "Press “Start” first, then record that the stage is done.",
        "quality": "This is the quality check stage; the supervisor records its result on the order page.",
        "outside": "This stage is at the supplier “{supplier}”; the supervisor records its return.",
        "short": "Not enough “{item}” in stock: the order needs {need} and {have} is available. Tell the supervisor.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def can_use(user):
    from permissions.services import user_has_permission

    return user_has_permission(user, RECORD) or user_has_permission(user, PRODUCE)


def _require(user):
    if not can_use(user):
        raise PermissionDenied("The floor screen needs manufacturing.record_stage.")


def scoped_orders():
    """Production orders of the entity being worked in (every order for the whole group), as the order pages see them."""

    return entity_scope.scope(ProductionOrder.objects, "location__entity")


def my_centres(user):
    """The active work centres, of the entity being worked in, that this user's employee is ticked on ([] when none)."""

    from staff.models import Employee

    employee = Employee.objects.filter(user=user, active=True).first() if getattr(user, "is_authenticated", False) else None
    if employee is None:
        return []
    return list(workcentres.scoped().filter(active=True, workers=employee))


def stage_label(stage, lang="ar"):
    return stage[2] if lang == "en" and len(stage) > 2 and stage[2] else stage[1]


def elapsed_minutes(started, finished):
    """The clock's minutes between two moments, to two decimals: at least 0.01 and at most what a stage log can hold."""

    minutes = (Decimal(str((finished - started).total_seconds())) / 60).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return min(max(minutes, Decimal("0.01")), workcentres.MAX_MINUTES)


def waiting_reason(order, lang="ar"):
    """(state, sentence) when the order's current stage is not closed from the floor, else (None, "")."""

    words = _words(lang)
    job = outside.current_job(order)
    if job is not None:  # F4 (HG-137): closed by its return
        return OUTSIDE, words["outside"].format(supplier=job.supplier.name)
    if quality.stage_criteria(order):  # F5 (HG-136): recorded with its criteria, on the order page
        return QUALITY, words["quality"]
    return None, ""


# ---- what the screen reads ----

def cards(user, lang="ar", today=None):
    """One card per open order whose current stage this user works on, earliest promised date first."""

    today = today or timezone.localdate()
    mine = {centre.pk for centre in my_centres(user)}
    rows = [order for order in scoped_orders().filter(status__in=OPEN).select_related("recipe__product", "sales_order_line__order", "location")
            .prefetch_related("stage_logs") if order.stage_index < len(order.stages)]
    pks = [order.pk for order in rows]
    steps = {(step.order_id, step.index): step for step in OrderStep.objects.filter(order__in=pks)}
    out = {job.order_id: job for job in OutsideJob.objects.filter(order__in=pks, status=OutsideStatus.SENT).select_related("supplier")}
    with_criteria = set(QualityCriterion.objects.filter(recipe__in={order.recipe_id for order in rows}).values_list("recipe_id", flat=True))
    centre_of = {}
    for order in rows:
        step = steps.get((order.pk, order.stage_index))
        centre_of[order.pk] = step.work_centre_id if step is not None else workcentres.stage_plan(order.stages[order.stage_index])[0]
    centres = WorkCentre.objects.in_bulk([pk for pk in centre_of.values() if pk])
    words = _words(lang)
    found = []
    for order in rows:
        centre_id = centre_of[order.pk]
        if mine and centre_id is not None and centre_id not in mine:
            continue
        stage = order.stages[order.stage_index]
        step = steps.get((order.pk, order.stage_index))
        started = step.started_at if step is not None else None
        if order.pk in out:
            state, wait = OUTSIDE, words["outside"].format(supplier=out[order.pk].supplier.name)
        elif stage[0] == quality.QUALITY_STAGE and order.recipe_id in with_criteria:
            state, wait = QUALITY, words["quality"]
        else:
            state, wait = (RUNNING if started is not None else READY), ""
        due = schedule.effective_due(order)
        centre = centres.get(centre_id)
        found.append({
            "order": order, "pk": order.pk, "number": order.number, "product": order.recipe.product.item_name, "quantity": order.quantity,
            "units": orders.good_units(order), "stage": stage_label(stage, lang), "stage_index": order.stage_index, "stage_number": order.stage_index + 1,
            "stage_count": len(order.stages), "centre": centre.name(lang) if centre is not None else "", "due": due, "late": due is not None and due < today,
            "state": state, "wait": wait, "started_at": started, "first": order.status == OrderStatus.PLANNED,
            "last": order.stage_index == len(order.stages) - 1,
        })
    found.sort(key=lambda card: (card["due"] is None, card["due"] or today, card["pk"]))
    return found


# ---- the two actions ----

def _locked(order):
    return ProductionOrder.objects.select_for_update(of=("self",)).select_related("recipe", "location").get(pk=order.pk)


def _current_step(order, stage, words):
    """The schedule step of the order's current stage, locked. ``stage`` is the stage index the page showed: a page that is
    behind the order (someone else closed the stage meanwhile) is refused, so nobody closes a stage they were not looking at."""

    if order.status not in OPEN or order.stage_index >= len(order.stages):
        raise ValidationError(words["moved"])
    if stage not in (None, "") and str(stage) != str(order.stage_index):
        raise ValidationError(words["moved"])
    schedule.ensure_steps(order)
    return OrderStep.objects.select_for_update().get(order=order, index=order.stage_index)


def _check_floor(order, lang):
    state, sentence = waiting_reason(order, lang)
    if state is not None:
        raise ValidationError(sentence)


def _check_materials(order, words):
    """A start is refused while a material of an order that has not begun is short at its place (nothing is locked or moved:
    the issue itself checks again, under its own locks, when the first stage is closed)."""

    from inventory.services import get_item_location_stock_quantity

    batches = orders._batches(order)
    for line in order.recipe.lines.select_related("component").order_by("component_id"):
        need = (line.quantity * batches).quantize(Decimal("0.001"))
        have = get_item_location_stock_quantity(line.component, order.location)
        if have < need:
            raise ValidationError(words["short"].format(item=line.component.item_name, need=services.fmt_qty(need), have=services.fmt_qty(have)))


@transaction.atomic
def start(order, user, *, stage=None, lang="ar"):
    """«ابدأ»: the current stage's start time. No stock moves and the order's status does not change."""

    _require(user)
    words = _words(lang)
    order = _locked(order)
    step = _current_step(order, stage, words)
    _check_floor(order, lang)
    if step.started_at is not None:
        raise ValidationError(words["started"])
    if order.status == OrderStatus.PLANNED and order.recipe.output_quantity:
        _check_materials(order, words)
    step.started_at = timezone.now()
    step.save(update_fields=["started_at"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="manufacturing", action="start_production_stage",
                            object_type="manufacturing.ProductionOrder", object_id=str(order.pk),
                            after_data={"number": order.number, "stage": step.stage_key, "index": step.index, "started_at": step.started_at.isoformat()})
    return step


@transaction.atomic
def done(order, user, *, stage=None, good=None, defect=None, note="", lang="ar"):
    """«خلصت»: close the current stage through ``orders.advance``, with the minutes since its start.

    The only caller that passes the stand-in: on the first stage ``advance`` issues the order's materials, and a worker who
    holds ``manufacturing.record_stage`` may do that without ``inventory.adjust_stock``."""

    _require(user)
    words = _words(lang)
    order = _locked(order)
    step = _current_step(order, stage, words)
    _check_floor(order, lang)
    if step.started_at is None:
        raise ValidationError(words["start_first"])
    minutes = elapsed_minutes(step.started_at, timezone.now())
    return orders.advance(order, user, good=good, defect=defect, note=note, minutes=minutes, lang=lang, stand_in=RECORD)


# ---- the photos of defects ----

def photos(order_pks, user):
    """{order pk: [attachment, ...]} oldest first, for the orders this user may see (the attachments module's own rules)."""

    from attachments import services as attachments

    found = {}
    for row in reversed(attachments.attachments_for_many(ORDER_OWNER, list(order_pks), user, limit=120)):
        found.setdefault(row.object_id, []).append(row)
    return found
