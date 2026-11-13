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
* cancelling a run reverses the product first, then every component, again
  through the engine's ``cancel_stock_operation``. The engine refuses only
  when the place holds fewer units than the run made, so this module refuses
  first when any of the product has left the place since the run (F11,
  HG-100, ``_left_since``). The stock operations screen does not reverse a
  run's operations one by one: it sends the person here (F11b, HG-102,
  ``guards``);
* no stock, cost or accounting rule is changed here (see HG-030).

HG-036 (approved) adds three things on top, still only through the engine:

* work in progress: a production order issues its materials when it starts
  (``issue``); they leave stock then and their value waits in work in
  progress until the order finishes, when ``produce`` uses those same
  operations instead of consuming again;
* labour and overhead (``conversion_cost``) go into the product's stock cost,
  so its average cost and the cost of each later sale include them;
* scrap: units that failed a stage never enter stock; the good units carry the
  whole cost (normal loss), and the run records how many were scrapped.
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
        "moved_out": "مينفعش تلغي التشغيلة دي، عشان في كمية من «{item}» خرجت من المخزن بعد ما التشغيلة اتسجلت (بيع أو تحويل أو تسوية أو استخدام في تشغيلة تانية). لو الرصيد محتاج تصحيح، اعمل تسوية مخزون عادية.",
        "reversed": "الخامات المصروفة للأمر ده اترجعت من شاشة المخزون؛ ألغِ الأمر وافتح أمر جديد.",
        "on_floor": "في أمر إنتاج شغال في المصنع بالوصفة دي وخاماته اتصرفت؛ خلّصه أو ألغيه الأول قبل ما تعدّل الوصفة.",
        "no_good": "مفيش ولا وحدة سليمة تدخل المخزن؛ لو الكمية كلها باظت ألغِ الأمر أو سجّل الهالك كتسوية.",
        "cycle": "مينفعش تضيف «{component}» لوصفة «{product}»: «{component}» نفسه بيتصنع من «{product}» (مباشرة أو من خلال صنف تاني)، فالوصفتين هيبقوا في دايرة مقفولة.",
    },
    "en": {
        "product": "Choose the product.", "product_stock": "The product must be a stock-tracked item.", "output": "The output per batch must be above zero.",
        "lines": "Add at least one component.", "component": "The component “{item}” must be a stock-tracked item.", "self": "The product cannot be its own component.",
        "qty": "The quantity of “{item}” must be above zero.", "twice": "“{item}” is listed twice; put its quantity on one line.", "inactive": "This recipe is inactive.",
        "batches": "The number of batches must be above zero.", "location": "Choose the location.", "short": "Not enough “{item}”: {need} needed, {have} available.",
        "cancelled": "This run is already cancelled.", "reason": "Enter the reason for cancelling.",
        "moved_out": "This run cannot be cancelled: some “{item}” has left the location since the run was recorded (a sale, a transfer, an adjustment or use in another run). To correct the stock, record an ordinary stock adjustment.",
        "reversed": "The materials issued for this order were reversed from the inventory screen; cancel the order and open a new one.",
        "on_floor": "An order on the floor was issued from this recipe; finish or cancel it before changing the recipe.",
        "no_good": "No good units are left to put into stock; if the whole lot was lost, cancel the order or record the loss as an adjustment.",
        "cycle": "“{component}” cannot go into the recipe of “{product}”: “{component}” is itself made from “{product}” (directly or through another item), so the two recipes would go in a closed circle.",
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


def _makes(component_pk, target_pk, skip):
    """F2: whether ``component_pk`` is itself made, at any depth, from ``target_pk`` by an active recipe (``skip``, the
    recipe being replaced, does not count)."""

    seen, stack = set(), [component_pk]
    while stack:
        pk = stack.pop()
        if pk == target_pk:
            return True
        if pk in seen:
            continue
        seen.add(pk)
        lines = RecipeLine.objects.filter(recipe__product_id=pk, recipe__active=True)
        if skip is not None:
            lines = lines.exclude(recipe=skip)
        stack.extend(lines.values_list("component_id", flat=True))
    return False


@transaction.atomic
def save_recipe(data, lines, user, recipe=None, lang="ar"):
    """Create or replace a recipe. ``lines`` is [(component item, quantity per batch)]."""

    words = MESSAGES[lang]
    if recipe is not None and recipe.orders.filter(status="in_progress", issues__isnull=False).exists():
        # HG-036: an order on the floor was issued from this recipe; its finish must use the same one.
        raise ValidationError(words["on_floor"])
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
    if data.get("active", True) not in (False, "", "0", None):
        # F2: A uses B and B uses A, at any depth, would send the requirements calculation round in a circle. An inactive
        # recipe is out of every calculation, so only an active one is checked (and again when it is switched on).
        for component, _quantity in clean:
            if _makes(component.pk, product.pk, recipe):
                raise ValidationError(words["cycle"].format(component=component.item_name, product=product.item_name))
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


def _check_stock(lines, batches, location, words):
    from inventory.services import get_item_location_stock_quantity, lock_items_for_stock_check

    lock_items_for_stock_check([line.component_id for line in lines])
    for line in lines:  # one clear message before anything moves
        need = (line.quantity * batches).quantize(Decimal("0.001"))
        have = get_item_location_stock_quantity(line.component, location)
        if have < need:
            raise ValidationError(words["short"].format(item=line.component.item_name, need=fmt_qty(need), have=fmt_qty(have)))


def _consume(lines, batches, location, prefix, reason, run_date, user, stand_in=None):
    from inventory.models import StockAdjustmentDirection
    from inventory.services import adjust_stock

    return [adjust_stock(f"{prefix}{index:02d}", run_date, line.component, location, StockAdjustmentDirection.OUT,
                         (line.quantity * batches).quantize(Decimal("0.001")), reason, user, stand_in=stand_in)
            for index, line in enumerate(lines, start=1)]


def _checked(recipe, batches, location, words):
    recipe = Recipe.objects.select_for_update().select_related("product").get(pk=recipe.pk)
    if not recipe.active:
        raise ValidationError(words["inactive"])
    batches = _decimal(batches)
    if batches is None or batches <= 0:
        raise ValidationError(words["batches"])
    if location is None:
        raise ValidationError(words["location"])
    return recipe, batches, list(recipe.lines.select_related("component").order_by("component_id"))


@transaction.atomic
def issue(recipe, user, *, batches, location, reference, issue_date=None, lang="ar"):
    """HG-036: issue a batch's materials to the floor (work in progress)."""

    words = MESSAGES[lang]
    recipe, batches, lines = _checked(recipe, batches, location, words)
    _check_stock(lines, batches, location, words)
    reason = f"صرف خامات {reference} / Materials issued {reference}"
    return _consume(lines, batches, location, f"{reference}-I", reason, issue_date or timezone.localdate(), user)


@transaction.atomic
def produce(recipe, user, *, batches, location, run_date=None, notes="", lang="ar", conversion_cost=Decimal("0"), scrap=Decimal("0"), issued=None,
            stand_in=None, output=None):
    """Post a run. ``issued`` are the operations an order already issued at
    its start; without them the components are consumed now, as before.
    ``stand_in``: HG-048, the narrower permission the caller's flow allows in place of inventory.adjust_stock.
    ``output`` (F11, HG-100): the good units that enter stock, said by the caller (an order's finish says its good
    units). Without it the run makes the recipe's output times its batches, less the scrap, as before."""

    from inventory.models import StockAdjustmentDirection
    from inventory.services import adjust_stock

    words = MESSAGES[lang]
    recipe, batches, lines = _checked(recipe, batches, location, words)
    run_date = run_date or timezone.localdate()
    conversion_cost = money_round(Decimal(conversion_cost or 0))
    scrap = (Decimal(scrap or 0)).quantize(Decimal("0.001"))
    output = (recipe.output_quantity * batches).quantize(Decimal("0.001")) - scrap if output is None else _decimal(output)
    if output is None or output <= 0:
        raise ValidationError(words["no_good"])
    number = _next(ProductionRun, "number", f"MO-{run_date:%Y%m%d}-", 3)
    reason = f"تصنيع {number} / Production {number}"
    if issued is None:
        _check_stock(lines, batches, location, words)
        consumed = _consume(lines, batches, location, f"{number}-C", reason, run_date, user, stand_in=stand_in)
    else:
        consumed = list(issued)
        if any(operation.status != "posted" for operation in consumed):
            raise ValidationError(words["reversed"])
    total = sum((operation.quantity * operation.unit_cost for operation in consumed), Decimal("0"))
    produced = adjust_stock(f"{number}-OUT", run_date, recipe.product, location, StockAdjustmentDirection.IN, output, reason, user,
                            unit_cost=cost_round((total + conversion_cost) / output), stand_in=stand_in)
    run = ProductionRun.objects.create(number=number, recipe=recipe, batches=batches, location=location, run_date=run_date, output_quantity=output,
                                       total_cost=money_round(total), conversion_cost=conversion_cost, scrap_quantity=scrap,
                                       output_operation=produced, notes=(notes or "").strip()[:255], created_by=user)
    ProductionConsumption.objects.bulk_create([ProductionConsumption(run=run, operation=operation) for operation in consumed])
    _audit(run, user, "produce", {"recipe": recipe.code, "batches": str(batches), "output": str(output), "cost": str(run.total_cost),
                                  "conversion": str(conversion_cost), "scrap": str(scrap), "from_wip": issued is not None}, AuditEventType.CREATE)
    return run


def _left_since(run):
    """F11 (HG-100): whether any of the product left the run's location after the run put it there.

    The engine reverses the output at the run's own unit cost and refuses only when the place holds fewer units than
    the run made. If some were sold (at an average that included this run) and a later run refilled the place, that
    reversal goes through and leaves the units that remain at a cost nobody paid. So a run is cancelled only while
    its units have not moved.

    "After" is the order the movements were written in (the movement's pk), not their dates: cakes sold this morning
    do not stop the noon run being cancelled, and a sale typed in later with yesterday's date does.

    Two kinds of outgoing rows are not the product leaving: a reversal row (it takes back what one incoming operation
    put in, at that operation's own cost, as when a later run is cancelled), and the row of an adjustment or transfer
    that has itself been reversed since (the units came back whole, at the cost they left at)."""

    from inventory.models import StockMovement, StockOperationStatus
    from inventory.services import OUT_MOVEMENT_TYPES

    made = run.output_operation.movements.filter(reversal_of__isnull=True).order_by("pk").first()
    if made is None:
        return False
    return (StockMovement.objects.filter(item_id=made.item_id, location_id=made.location_id, movement_type__in=OUT_MOVEMENT_TYPES,
                                         pk__gt=made.pk, reversal_of__isnull=True)
            .exclude(stock_operation__status=StockOperationStatus.CANCELLED).exists())


def check_cancellable(run, reason, lang="ar"):
    """What ``cancel_run`` refuses before anything moves. The run page asks this first, so a cancel that cannot
    happen is not sent for a second person's approval either. Returns the reason as it will be kept."""

    words = MESSAGES[lang]
    if run.status != RunStatus.POSTED:
        raise ValidationError(words["cancelled"])
    reason = (reason or "").strip()[:255]
    if not reason:
        raise ValidationError(words["reason"])
    if _left_since(run):
        raise ValidationError(words["moved_out"].format(item=run.output_operation.item.item_name))
    return reason


@transaction.atomic
def cancel_run(run, user, *, reason, reversal_date=None, lang="ar"):
    from inventory.models import StockOperation
    from inventory.services import cancel_stock_operation, lock_items_for_stock_check

    run = ProductionRun.objects.select_for_update().get(pk=run.pk)
    # The engine's own reversal locks the operation and then its item. The same order here: a reversal of this very
    # operation from the stock screen, running at the same moment, then waits its turn instead of deadlocking.
    output = StockOperation.objects.select_for_update().get(pk=run.output_operation_id)
    lock_items_for_stock_check([output.item_id])  # nothing of it leaves between the check and the reversal
    reason = check_cancellable(run, reason, lang)
    reversal_date = reversal_date or timezone.localdate()
    cancel_stock_operation(run.output_operation_id, reversal_date, reason, user)  # the engine: refuses when the place is short
    for consumption in run.consumptions.all():
        cancel_stock_operation(consumption.operation_id, reversal_date, reason, user)
    run.status = RunStatus.CANCELLED
    run.cancelled_at = timezone.now()
    run.cancellation_reason = reason
    run.save(update_fields=["status", "cancelled_at", "cancellation_reason"])
    _audit(run, user, "cancel_production", {"reason": reason})
    return run
