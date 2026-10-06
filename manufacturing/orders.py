"""MFG-002: production orders followed stage by stage.

An order is a plan until it is finished: no stock moves while it is on the
floor. Finishing posts one ordinary production run (``services.produce``) for
the order's quantity, so materials, cost and the product's average cost go
through exactly the path MFG-001 already proves. Labour and overhead entered
on the order give the full cost per unit on its cost card; stock keeps the
material cost (HG-003), so they are shown, not posted.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import cost_round, money_round

from . import services
from .models import OrderStageLog, OrderStatus, ProductionOrder
from .stages import current_stages

MESSAGES = {
    "ar": {"recipe": "اختار الوصفة (المنتج).", "quantity": "الكمية لازم تكون رقم أكبر من صفر.", "location": "اختار مكان الإنتاج.",
           "inactive": "الوصفة دي متوقفة.", "money": "تكلفة العمالة والمصاريف لازم تكون رقم مش سالب.", "state": "الأمر ده مش في المرحلة دي.",
           "good": "الكمية السليمة لازم تكون رقم بين صفر وكمية الأمر.", "defect": "الكمية المعيبة لازم تكون رقم مش سالب.",
           "not_last": "لسه في مراحل متخلصتش.", "reason": "اكتب سبب الإلغاء.", "done": "الأمر خلص ومينفعش يتلغي؛ ألغِ التشغيلة نفسها لو محتاج."},
    "en": {"recipe": "Choose the recipe (product).", "quantity": "Quantity must be a number above zero.", "location": "Choose where it is made.",
           "inactive": "This recipe is inactive.", "money": "Labour and overhead must be numbers, not negative.", "state": "The order is not at that step.",
           "good": "Good quantity must be between zero and the order quantity.", "defect": "Defect quantity cannot be negative.",
           "not_last": "There are stages still to finish.", "reason": "Write the reason for cancelling.", "done": "A finished order cannot be cancelled; cancel its run instead."},
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
        customer=data.get("customer"), due_date=data.get("due_date"), stages=[list(stage) for stage in current_stages()],
        labor_cost=_money(data.get("labor_cost"), words), overhead_cost=_money(data.get("overhead_cost"), words),
        notes=(data.get("notes") or "")[:255], created_by=user)
    _audit(order, user, "create_production_order", {"number": order.number, "recipe": recipe.code, "quantity": str(quantity)})
    return order


def _locked(order):
    return ProductionOrder.objects.select_for_update().select_related("recipe").get(pk=order.pk)


@transaction.atomic
def advance(order, user, *, good=None, defect=None, note="", lang="ar"):
    """Close the current stage (starting the order if needed) and move to the next."""

    words = MESSAGES[lang]
    order = _locked(order)
    if order.status not in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS) or order.stage_index >= len(order.stages):
        raise ValidationError(words["state"])
    good_qty = order.quantity if good in (None, "") else services._decimal(good)
    if good_qty is None or good_qty < 0 or good_qty > order.quantity:
        raise ValidationError(words["good"])
    defect_qty = Decimal("0") if defect in (None, "") else services._decimal(defect)
    if defect_qty is None or defect_qty < 0:
        raise ValidationError(words["defect"])
    if order.status == OrderStatus.PLANNED:
        order.status, order.started_at = OrderStatus.IN_PROGRESS, timezone.now()
    stage = order.stages[order.stage_index]
    OrderStageLog.objects.create(order=order, stage_key=stage[0], good_quantity=good_qty, defect_quantity=defect_qty, note=(note or "")[:255], done_by=user)
    order.stage_index += 1
    order.save(update_fields=["status", "started_at", "stage_index"])
    _audit(order, user, "advance_production_order", {"stage": stage[0], "good": str(good_qty), "defect": str(defect_qty)})
    return order


@transaction.atomic
def finish(order, user, lang="ar"):
    """All stages done: post the production run for the order's quantity."""

    words = MESSAGES[lang]
    order = _locked(order)
    if order.status != OrderStatus.IN_PROGRESS:
        raise ValidationError(words["state"])
    if order.stage_index < len(order.stages):
        raise ValidationError(words["not_last"])
    batches = (order.quantity / order.recipe.output_quantity).quantize(Decimal("0.001"))
    run = services.produce(order.recipe, user, batches=batches, location=order.location,
                           notes=f"{order.number}{' · ' + order.notes if order.notes else ''}"[:255], lang=lang)
    order.run, order.status, order.finished_at = run, OrderStatus.DONE, timezone.now()
    order.save(update_fields=["run", "status", "finished_at"])
    _audit(order, user, "finish_production_order", {"run": run.number, "output": str(run.output_quantity), "material_cost": str(run.total_cost)})
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
    order.status = OrderStatus.CANCELLED
    order.notes = f"{order.notes} · {reason.strip()}"[:255] if order.notes else reason.strip()[:255]
    order.save(update_fields=["status", "notes"])
    _audit(order, user, "cancel_production_order", {"reason": reason.strip()[:200]})
    return order


def cost_card(order):
    """Materials (posted cost once finished, today's averages before), plus
    labour and overhead, per order and per unit."""

    if order.run_id:
        material, output = order.run.total_cost, order.run.output_quantity
    else:
        batches = (order.quantity / order.recipe.output_quantity) if order.recipe.output_quantity else Decimal("0")
        estimate = services.plan(order.recipe, batches, order.location)
        material, output = estimate["cost"], estimate["output"]
    total = money_round(material + order.labor_cost + order.overhead_cost)
    per_unit = cost_round(total / output) if output else Decimal("0")
    material_unit = cost_round(material / output) if output else Decimal("0")
    return {"material": money_round(material), "labor": order.labor_cost, "overhead": order.overhead_cost, "total": total,
            "output": output, "per_unit": per_unit, "material_per_unit": material_unit, "estimate": not order.run_id}


def quality(order):
    logs = list(order.stage_logs.all())
    defects = sum((log.defect_quantity for log in logs), Decimal("0"))
    return {"defects": defects, "rate": (defects / order.quantity * 100).quantize(Decimal("0.1")) if order.quantity else Decimal("0")}
