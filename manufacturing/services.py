"""MFG-001: recipes (bills of materials) and production runs.

Rules, stated once:

* a recipe says what one batch of a stock-tracked product takes, in
  stock-tracked components;
* a production run goes entirely through the inventory engine's own
  ``adjust_stock``. Each component leaves the chosen location (direction
  out) at its authoritative average cost, under the engine's permission,
  period and stock-lock rules. The product then enters the same location
  (direction in) at the consumed cost divided by the units made. Setting that
  cost needs ``inventory.view_cost``, which the engine checks. All of it is
  one transaction: a missing component leaves nothing behind;
* cancelling a run reverses the product first (the engine refuses when the
  product has already been sold), then every component, again through the
  engine's ``cancel_stock_operation``;
* no stock, cost or accounting rule is changed here (see HG-030).
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import cost_round, money_round
from settings_core.templatetags.hesba_format import qty as fmt_qty

from .models import ProductionConsumption, ProductionRun, Recipe, RecipeLine, RunStatus


MESSAGES = {
    "ar": {
        "product": "اختار المنتج.", "product_stock": "المنتج لازم يكون صنف متتبع في المخزون.", "output": "كمية الإنتاج لكل دفعة لازم أكبر من صفر.",
        "lines": "ضيف مكوّن واحد على الأقل.", "component": "المكوّن «{item}» لازم يكون صنف متتبع في المخزون.", "self": "المنتج مينفعش يكون مكوّن في نفسه.",
        "qty": "كمية «{item}» لازم أكبر من صفر.", "twice": "«{item}» متكرر؛ اجمع كميته في سطر واحد.", "inactive": "الوصفة دي متوقفة.",
        "batches": "عدد الدفعات لازم أكبر من صفر.", "location": "اختار المخزن.", "short": "«{item}» مش كفاية: محتاج {need} والموجود {have}.",
        "cancelled": "التشغيلة دي اتلغت بالفعل.", "reason": "اكتب سبب الإلغاء.",
    },
    "en": {
        "product": "Choose the product.", "product_stock": "The product must be a stock-tracked item.", "output": "The output per batch must be above zero.",
        "lines": "Add at least one component.", "component": "The component “{item}” must be a stock-tracked item.", "self": "The product cannot be its own component.",
        "qty": "The quantity of “{item}” must be above zero.", "twice": "“{item}” is listed twice; put its quantity on one line.", "inactive": "This recipe is inactive.",
        "batches": "The number of batches must be above zero.", "location": "Choose the location.", "short": "Not enough “{item}”: {need} needed, {have} available.",
        "cancelled": "This run is already cancelled.", "reason": "Enter the reason for cancelling.",
    },
}


def _audit(obj, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="manufacturing", action=action, object_type=f"manufacturing.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data={}, after_data=after)


def _decimal(value):
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        return None
    return number.quantize(Decimal("0.001")) if number.is_finite() else None


def _next(model, field, prefix, width):
    number = model.objects.filter(**{f"{field}__startswith": prefix}).count() + 1
    while model.objects.filter(**{field: f"{prefix}{number:0{width}d}"}).exists():
        number += 1
    return f"{prefix}{number:0{width}d}"


@transaction.atomic
def save_recipe(data, lines, user, recipe=None, lang="ar"):
    """Create or replace a recipe. ``lines`` is [(component item, quantity per batch)]."""

    words = MESSAGES[lang]
    product = data.get("product")
    if product is None:
        raise ValidationError(words["product"])
    if not product.is_stock_tracked:
        raise ValidationError(words["product_stock"])
    output = _decimal(data.get("output_quantity") or "1")
    if output is None or output <= 0:
        raise ValidationError(words["output"])
    clean, seen = [], set()
    for component, quantity in lines:
        if component is None:
            continue
        name = component.item_name
        if component.pk == product.pk:
            raise ValidationError(words["self"])
        if not component.is_stock_tracked:
            raise ValidationError(words["component"].format(item=name))
        if component.pk in seen:
            raise ValidationError(words["twice"].format(item=name))
        quantity = _decimal(quantity)
        if quantity is None or quantity <= 0:
            raise ValidationError(words["qty"].format(item=name))
        seen.add(component.pk)
        clean.append((component, quantity))
    if not clean:
        raise ValidationError(words["lines"])
    created = recipe is None
    recipe = recipe or Recipe(code=_next(Recipe, "code", "BOM-", 4), created_by=user)
    recipe.product = product
    recipe.name = (data.get("name") or "").strip()[:255]
    recipe.output_quantity = output
    recipe.active = data.get("active", True) not in (False, "", "0", None)
    recipe.notes = (data.get("notes") or "").strip()[:255]
    recipe.save()
    recipe.lines.all().delete()
    RecipeLine.objects.bulk_create([RecipeLine(recipe=recipe, component=component, quantity=quantity) for component, quantity in clean])
    _audit(recipe, user, "create_recipe" if created else "change_recipe",
           {"product": product.item_code, "output": str(output), "lines": [[c.item_code, str(q)] for c, q in clean]},
           AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return recipe


def plan(recipe, batches, location):
    """What a run of ``batches`` needs, what is there, and the cost at today's averages."""

    from inventory.services import get_item_authoritative_average_cost, get_item_location_stock_quantity

    rows, cost, possible = [], Decimal("0"), None
    for line in recipe.lines.select_related("component"):
        need = (line.quantity * batches).quantize(Decimal("0.001"))
        have = get_item_location_stock_quantity(line.component, location) if location else Decimal("0")
        unit_cost = get_item_authoritative_average_cost(line.component)
        cost += need * unit_cost
        can = (have / line.quantity).quantize(Decimal("0.001")) if line.quantity > 0 else Decimal("0")
        possible = can if possible is None else min(possible, can)
        rows.append({"line": line, "need": need, "have": have, "short": have < need, "unit_cost": unit_cost, "cost": money_round(need * unit_cost)})
    output = (recipe.output_quantity * batches).quantize(Decimal("0.001"))
    return {"rows": rows, "cost": money_round(cost), "output": output, "unit_cost": cost_round(cost / output) if output > 0 else Decimal("0"),
            "possible_batches": max(possible or Decimal("0"), Decimal("0"))}


@transaction.atomic
def produce(recipe, user, *, batches, location, run_date=None, notes="", lang="ar"):
    from inventory.models import StockAdjustmentDirection
    from inventory.services import adjust_stock, get_item_location_stock_quantity, lock_items_for_stock_check

    words = MESSAGES[lang]
    recipe = Recipe.objects.select_for_update().select_related("product").get(pk=recipe.pk)
    if not recipe.active:
        raise ValidationError(words["inactive"])
    batches = _decimal(batches)
    if batches is None or batches <= 0:
        raise ValidationError(words["batches"])
    if location is None:
        raise ValidationError(words["location"])
    run_date = run_date or timezone.localdate()
    lines = list(recipe.lines.select_related("component").order_by("component_id"))
    lock_items_for_stock_check([line.component_id for line in lines])
    for line in lines:  # one clear message before anything moves
        need = (line.quantity * batches).quantize(Decimal("0.001"))
        have = get_item_location_stock_quantity(line.component, location)
        if have < need:
            raise ValidationError(words["short"].format(item=line.component.item_name, need=fmt_qty(need), have=fmt_qty(have)))
    number = _next(ProductionRun, "number", f"MO-{run_date:%Y%m%d}-", 3)
    reason = f"تصنيع {number} / Production {number}"
    consumed, total = [], Decimal("0")
    for index, line in enumerate(lines, start=1):
        operation = adjust_stock(f"{number}-C{index:02d}", run_date, line.component, location, StockAdjustmentDirection.OUT,
                                 (line.quantity * batches).quantize(Decimal("0.001")), reason, user)
        consumed.append(operation)
        total += operation.quantity * operation.unit_cost
    output = (recipe.output_quantity * batches).quantize(Decimal("0.001"))
    produced = adjust_stock(f"{number}-OUT", run_date, recipe.product, location, StockAdjustmentDirection.IN, output, reason, user,
                            unit_cost=cost_round(total / output))
    run = ProductionRun.objects.create(number=number, recipe=recipe, batches=batches, location=location, run_date=run_date, output_quantity=output,
                                       total_cost=money_round(total), output_operation=produced, notes=(notes or "").strip()[:255], created_by=user)
    ProductionConsumption.objects.bulk_create([ProductionConsumption(run=run, operation=operation) for operation in consumed])
    _audit(run, user, "produce", {"recipe": recipe.code, "batches": str(batches), "output": str(output), "cost": str(run.total_cost)}, AuditEventType.CREATE)
    return run


@transaction.atomic
def cancel_run(run, user, *, reason, reversal_date=None, lang="ar"):
    from inventory.services import cancel_stock_operation

    words = MESSAGES[lang]
    run = ProductionRun.objects.select_for_update().get(pk=run.pk)
    if run.status != RunStatus.POSTED:
        raise ValidationError(words["cancelled"])
    reason = (reason or "").strip()[:255]
    if not reason:
        raise ValidationError(words["reason"])
    reversal_date = reversal_date or timezone.localdate()
    cancel_stock_operation(run.output_operation_id, reversal_date, reason, user)  # refuses when the product is gone
    for consumption in run.consumptions.all():
        cancel_stock_operation(consumption.operation_id, reversal_date, reason, user)
    run.status = RunStatus.CANCELLED
    run.cancelled_at = timezone.now()
    run.cancellation_reason = reason
    run.save(update_fields=["status", "cancelled_at", "cancellation_reason"])
    _audit(run, user, "cancel_production", {"reason": reason})
    return run
