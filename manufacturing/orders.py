"""MFG-002: production orders followed stage by stage.

HG-036 (approved):

* starting an order (its first stage) issues the materials to the floor: they
  leave stock then, through the inventory engine, and their value waits in
  work in progress. Short materials stop the start, not the finish;
* finishing posts one ordinary production run (``services.produce``) from
  those issued materials, with labour and overhead absorbed into the
  product's stock cost;
* units recorded as defective at any stage are scrap: only the good units
  enter stock, and they carry the whole cost. The finish puts exactly those
  good units into stock (F11, HG-100), never the recipe's output times the
  rounded batches;
* cancelling an order on the floor returns every issued material.

Orders started before HG-036 have no issued materials; they consume at finish
as before.

Manufacturing F12 (HG-132) adds, through ``actuals``: extra issues and returns
while the order is on the floor (materials = issues + extras − returns), actual
labour and overhead lines (each kind's live lines replace its planned figure at
the finish), the order's standard written at the start, and an over-yield of up
to 10% at the final stage with a reason (owner question 8).
"""

from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import cost_round, money_round

from . import actuals, lots, quality, schedule, services, workcentres
from inventory.models import StockOperationStatus

from .models import OrderMaterialIssue, OrderStageLog, OrderStatus, ProductionOrder
from .stages import current_stages

MESSAGES = {
    "ar": {"recipe": "اختار الوصفة (المنتج).", "quantity": "الكمية لازم تكون رقم أكبر من صفر.", "location": "اختار مكان الإنتاج.",
           "inactive": "الوصفة دي متوقفة.", "money": "تكلفة العمالة والمصاريف لازم تكون رقم مش سالب.", "state": "الأمر ده مش في المرحلة دي.",
           "good": "الكمية السليمة لازم تكون رقم بين صفر وكمية الأمر.", "defect": "الكمية المعيبة لازم تكون رقم مش سالب.",
           "not_last": "لسه في مراحل متخلصتش.", "yield": "السليم والمعيب مع بعض مينفعش يزيدوا عن {units} وحدة داخلة المرحلة دي.",
           "reversed": "خامات الأمر ده اترجعت من شاشة المخزون؛ ألغِ الأمر وافتح أمر جديد.", "reason": "اكتب سبب الإلغاء.", "done": "الأمر خلص ومينفعش يتلغي؛ ألغِ التشغيلة نفسها لو محتاج.",
           "over": "في آخر مرحلة السليم ممكن يزيد عن كمية الأمر لحد 10% بس، يعني {cap} وحدة على الأكتر.", "over_reason": "السليم أكتر من كمية الأمر؛ اكتب السبب في الملاحظات."},
    "en": {"recipe": "Choose the recipe (product).", "quantity": "Quantity must be a number above zero.", "location": "Choose where it is made.",
           "inactive": "This recipe is inactive.", "money": "Labour and overhead must be numbers, not negative.", "state": "The order is not at that step.",
           "good": "Good quantity must be between zero and the order quantity.", "defect": "Defect quantity cannot be negative.",
           "not_last": "There are stages still to finish.", "yield": "Good and defective together cannot be more than the {units} units entering this stage.",
           "reversed": "This order's materials were reversed from the inventory screen; cancel the order and open a new one.", "reason": "Write the reason for cancelling.", "done": "A finished order cannot be cancelled; cancel its run instead.",
           "over": "At the final stage good units may be up to 10% more than the order, {cap} units at most.", "over_reason": "Good units are more than the order; write the reason in the notes."},
}


def _audit(order, user, action, after):
    AuditLog.objects.create(event_type=AuditEventType.CREATE if action == "create_production_order" else AuditEventType.UPDATE, actor=user,
                            module="manufacturing", action=action, object_type="manufacturing.ProductionOrder", object_id=str(order.pk), after_data=after)


def _money(value, words):
    if value in (None, ""):
        return Decimal("0")
    number = services._decimal(value)
    if number is None or number < 0:
        raise ValidationError(words["money"])
    return money_round(number)


@transaction.atomic
def create_order(data, user, lang="ar"):
    words = MESSAGES[lang]
    recipe, location = data.get("recipe"), data.get("location")
    if recipe is None:
        raise ValidationError(words["recipe"])
    if not recipe.active:
        raise ValidationError(words["inactive"])
    if location is None:
        raise ValidationError(words["location"])
    quantity = services._decimal(data.get("quantity"))
    if quantity is None or quantity <= 0:
        raise ValidationError(words["quantity"])
    today = timezone.localdate()
    order = ProductionOrder.objects.create(
        number=services._next(ProductionOrder, "number", f"PO-{today:%Y%m%d}-", 3), recipe=recipe, quantity=quantity, location=location,
        customer=data.get("customer"), due_date=data.get("due_date"),
        # Manufacturing F6a (HG-133): the recipe's routing when it has one (a work centre and planned minutes per stage), else the factory's list as ever.
        stages=workcentres.stages_for(recipe, quantity, location) or [list(stage) for stage in current_stages()],
        labor_cost=_money(data.get("labor_cost"), words), overhead_cost=_money(data.get("overhead_cost"), words),
        notes=(data.get("notes") or "")[:255], created_by=user)
    schedule.ensure_steps(order)  # Manufacturing F6b (HG-134): one schedule step per stage; planned dates stay empty until «رتّب» or a move
    _audit(order, user, "create_production_order", {"number": order.number, "recipe": recipe.code, "quantity": str(quantity)})
    return order


def _batches(order):
    return (order.quantity / order.recipe.output_quantity).quantize(Decimal("0.001"))


def good_units(order):
    """Units that passed every stage: the order less every defect, and never
    more than the fewest good units any stage recorded.

    Manufacturing F12 (HG-132): when every stage is done and the final one
    recorded more good units than were ordered (an over-yield, at most 10% and
    with a reason, owner question 8), those are the good units."""

    logs = list(order.stage_logs.all())
    if logs and len(logs) >= len(order.stages) and logs[-1].good_quantity > order.quantity:
        return logs[-1].good_quantity
    defects = sum((log.defect_quantity for log in logs), Decimal("0"))
    good = order.quantity - defects
    if logs:
        good = min(good, min(log.good_quantity for log in logs))
    return max(good, Decimal("0"))


def issued_value(order):
    """Materials on the order: issues and extras less returns (F12), at the cost each one moved at."""

    return money_round(sum((actuals.sign(issue.operation) * issue.operation.quantity * issue.operation.unit_cost
                            for issue in order.issues.select_related("operation")), Decimal("0")))


def _locked(order):
    return ProductionOrder.objects.select_for_update().select_related("recipe").get(pk=order.pk)


@transaction.atomic
def advance(order, user, *, good=None, defect=None, note="", minutes=None, lang="ar"):
    """Close the current stage (starting the order if needed) and move to the next.

    Manufacturing F6a (HG-133): ``minutes`` is how long the stage took (optional, above zero); it prices a suggested labour line."""

    words = MESSAGES[lang]
    minutes = workcentres.parse_minutes(minutes, lang)
    order = _locked(order)
    if order.status not in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS) or order.stage_index >= len(order.stages):
        raise ValidationError(words["state"])
    defect_qty = Decimal("0") if defect in (None, "") else services._decimal(defect)
    if defect_qty is None or defect_qty < 0:
        raise ValidationError(words["defect"])
    # A stage works on the units that passed the stages before it: good and
    # defective together cannot be more than that.
    units_in = good_units(order)
    good_qty = max(units_in - defect_qty, Decimal("0")) if good in (None, "") else services._decimal(good)
    if good_qty is None or good_qty < 0:
        raise ValidationError(words["good"])
    if order.stage_index == len(order.stages) - 1 and good_qty > order.quantity:
        # Manufacturing F12 (HG-132): an over-yield at the final stage, up to 10% more than ordered, with a reason.
        cap = (order.quantity * (1 + actuals.OVER_YIELD)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        if good_qty > cap:
            raise ValidationError(words["over"].format(cap=services.fmt_qty(cap)))
        if not (note or "").strip():
            raise ValidationError(words["over_reason"])
    else:
        if good_qty > order.quantity:
            raise ValidationError(words["good"])
        if good_qty + defect_qty > units_in:
            raise ValidationError(words["yield"].format(units=services.fmt_qty(units_in)))
    if order.status == OrderStatus.PLANNED:
        order.status, order.started_at = OrderStatus.IN_PROGRESS, timezone.now()
        # Manufacturing F3 (HG-131): the same issue, plus the ingredient lots it used (and a refusal for an expired one).
        operations = lots.issue(order.recipe, user, batches=_batches(order), location=order.location, reference=order.number, lang=lang, order=order)
        OrderMaterialIssue.objects.bulk_create([OrderMaterialIssue(order=order, operation=operation) for operation in operations])
        actuals.write_standard(order, operations)  # F12 (HG-132): what the variance report measures this order against
    stage = order.stages[order.stage_index]
    OrderStageLog.objects.create(order=order, stage_key=stage[0], good_quantity=good_qty, defect_quantity=defect_qty, note=(note or "")[:255], minutes=minutes, done_by=user)
    schedule.step_done(order, order.stage_index)  # Manufacturing F6b (HG-134): the stage's schedule step is finished now
    order.stage_index += 1
    order.save(update_fields=["status", "started_at", "stage_index"])
    _audit(order, user, "advance_production_order", {"stage": stage[0], "good": str(good_qty), "defect": str(defect_qty), "minutes": str(minutes or "")})
    return order


@transaction.atomic
def finish(order, user, lang="ar", lot_no="", lot_expiry=None):
    """All stages done: post the production run for the order's quantity.

    Manufacturing F3 (HG-131): ``lot_no`` and ``lot_expiry`` are the product lot's own number and expiry when the person
    typed them (otherwise the run number and run date + the recipe's shelf life)."""

    words = MESSAGES[lang]
    lot_no, lot_expiry = lots.check_lot(lot_no, lot_expiry, lang)
    order = _locked(order)
    if order.status != OrderStatus.IN_PROGRESS:
        raise ValidationError(words["state"])
    if order.stage_index < len(order.stages):
        raise ValidationError(words["not_last"])
    # F12 (HG-132): issues and extras, then returns, so the run's consumption rows (and its cancellation) follow suit.
    issued = actuals.out_first([issue.operation for issue in order.issues.select_related("operation")])
    if any(operation.status != StockOperationStatus.POSTED for operation in issued):
        raise ValidationError(words["reversed"])
    # F11 (HG-100): the order's own good units enter stock, and the rest of the order is its scrap. The batches
    # (quantity over the recipe's output, kept to three decimals) only say how much material the order takes:
    # 100 units with a batch of 3 are 33.333 batches, and 3 x 33.333 would put 99.999 into stock.
    good = good_units(order)
    # Manufacturing F5 (HG-136): a recipe that needs release puts its product into the entity's quality hold place
    # (not a selling location); the materials still come from the order's location. None: the order's location, as ever.
    hold = quality.hold_place_for(order)
    run = services.produce(order.recipe, user, batches=_batches(order), location=order.location,
                           notes=f"{order.number}{' · ' + order.notes if order.notes else ''}"[:255], lang=lang,
                           conversion_cost=actuals.conversion(order)["total"], scrap=max(order.quantity - good, Decimal("0")),
                           issued=issued or None, output=good, output_location=hold)
    order.run, order.status, order.finished_at = run, OrderStatus.DONE, timezone.now()
    order.save(update_fields=["run", "status", "finished_at"])
    lots.after_finish(order, run, user, lot_no, lot_expiry, location=hold)
    _audit(order, user, "finish_production_order", {"run": run.number, "output": str(run.output_quantity), "material_cost": str(run.total_cost),
                                                    **({"on_hold": hold.location_code} if hold is not None else {})})
    return order


@transaction.atomic
def cancel(order, user, reason, lang="ar"):
    words = MESSAGES[lang]
    order = _locked(order)
    if order.status == OrderStatus.DONE:
        raise ValidationError(words["done"])
    if order.status == OrderStatus.CANCELLED:
        raise ValidationError(words["state"])
    if not (reason or "").strip():
        raise ValidationError(words["reason"])
    from inventory.services import cancel_stock_operation

    # The materials come back from the floor: what went out first (issues, extras), then the returns leave again (F12).
    posted = [issue.operation for issue in order.issues.filter(operation__status=StockOperationStatus.POSTED).select_related("operation")]
    for operation in actuals.out_first(posted):
        cancel_stock_operation(operation.pk, timezone.localdate(), f"{order.number}: {reason.strip()}"[:255], user)
    order.status = OrderStatus.CANCELLED
    order.notes = f"{order.notes} · {reason.strip()}"[:255] if order.notes else reason.strip()[:255]
    order.save(update_fields=["status", "notes"])
    _audit(order, user, "cancel_production_order", {"reason": reason.strip()[:200]})
    return order


def cost_card(order):
    """Materials (posted once finished, as issued while on the floor, today's
    averages before it starts), plus labour and overhead, per order and per
    good unit."""

    if order.run_id:
        material, output = order.run.total_cost, order.run.output_quantity
        stage = "posted"
    elif order.issues.exists():
        material, output, stage = issued_value(order), good_units(order), "issued"
    else:
        estimate = services.plan(order.recipe, _batches(order) if order.recipe.output_quantity else Decimal("0"), order.location)
        material, output, stage = estimate["cost"], estimate["output"], "estimate"
    spent = actuals.conversion(order)  # F12 (HG-132): each kind's actual lines, else its planned figure
    total = money_round(material + spent["total"])
    per_unit = cost_round(total / output) if output else Decimal("0")
    material_unit = cost_round(material / output) if output else Decimal("0")
    absorbed = bool(order.run_id and order.run.conversion_cost)
    return {"material": money_round(material), "labor": spent["labour"], "overhead": spent["overhead"], "outside": spent["outside"], "total": total,
            "output": output, "per_unit": per_unit, "material_per_unit": material_unit, "estimate": stage == "estimate",
            "stage": stage, "absorbed": absorbed, "scrap": order.run.scrap_quantity if order.run_id else order.quantity - good_units(order)}


def quality(order):
    logs = list(order.stage_logs.all())
    defects = sum((log.defect_quantity for log in logs), Decimal("0"))
    return {"defects": defects, "rate": (defects / order.quantity * 100).quantize(Decimal("0.1")) if order.quantity else Decimal("0")}
