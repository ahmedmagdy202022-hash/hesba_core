"""Manufacturing F12 (HG-132): what an order really used and really spent.

Rules, stated once:

* **Extra issue** («صرف إضافي»): while an order is on the floor, any stock-tracked item (a substitute too) leaves the
  order's location through the engine's ``adjust_stock`` out, at the authoritative average at that moment, and is
  linked by ``OrderMaterialIssue(kind=extra)``. The projector already sends an order's material operations to work
  in progress. While batches are on, the lots it used are written to ``LotUse`` exactly as the start's issue does
  (``lots.record_uses``), and an expired lot is refused the same way.
* **Return** («رجّع خامات»): a component the order was issued and did not use comes back through ``adjust_stock`` in,
  **at the cost this order was issued at** (weighted over its issue and extra rows), never today's average (trap 3).
  The cost is passed as ``unit_cost``, so the engine itself asks for ``inventory.view_cost``. Linked by
  ``OrderMaterialIssue(kind=return)``; the projector credits work in progress from the movement's direction. At most
  what was issued + extra − returned. A return writes no ``LotUse`` row (rows are positive quantities used, and the
  batch screens hand returned stock to lots by expiry, not by where it came from).
* **Actual cost lines**: labour and overhead, hours × rate or an amount, added and cancelled until the finish.
* **Conversion at the finish, per kind**: a kind's live lines if it has any, else the planned figure typed when the
  order was opened (owner question 7); outside processing (F4) counts its lines only.

No line of the inventory engine or of average cost is changed here; every stock movement is ``adjust_stock``.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import cost_round, money_round
from settings_core.templatetags.hesba_format import qty as fmt_qty

from .models import CostLineKind, IssueKind, OrderCostLine, OrderMaterialIssue, OrderStandardLine, OrderStatus

ZERO = Decimal("0")
COST = "inventory.view_cost"
#: Owner question 8: a final stage may record up to this share more good units than ordered, with a reason.
OVER_YIELD = Decimal("0.10")

MESSAGES = {
    "ar": {
        "on_floor": "الصرف الإضافي ورجوع الخامات بيكونوا على أمر شغال في المصنع بس.",
        "closed": "مينفعش ترجّع خامات على أمر خلص أو اتلغى.",
        "old_order": "الأمر ده بدأ قبل ما الخامات تتصرف مع أول مرحلة، فمينفعش صرف إضافي أو رجوع عليه؛ خلّصه زي ما هو.",
        "item": "اختار الصنف.", "item_stock": "الصنف لازم يكون متتبع في المخزون.", "product": "مينفعش تصرف المنتج نفسه على أمر تصنيعه.",
        "qty": "الكمية لازم تكون رقم أكبر من صفر.", "reason": "اكتب سبب الصرف الإضافي.",
        "short": "«{item}» مش كفاية: محتاج {need} والموجود {have}.",
        "not_issued": "«{item}» ماتصرفش على الأمر ده، فمفيش منه حاجة ترجع.",
        "too_much": "مينفعش ترجّع أكتر من اللي اتصرف على الأمر: المتاح للرجوع من «{item}» {left}.",
        "line_state": "التكاليف الفعلية بتتسجل وتتلغي لحد ما الأمر يخلص بس.",
        "line_kind": "اختار نوع التكلفة: عمالة أو مصاريف تشغيل.",
        "line_amount": "اكتب الساعات وسعر الساعة، أو المبلغ، بأرقام أكبر من صفر.",
        "line_cancelled": "السطر ده اتلغى قبل كده.",
    },
    "en": {
        "on_floor": "Extra issues and returns are only for an order on the floor.",
        "closed": "Materials cannot be returned on a finished or cancelled order.",
        "old_order": "This order started before materials were issued at the first stage, so it takes no extra issue or return; finish it as it is.",
        "item": "Choose the item.", "item_stock": "The item must be stock-tracked.", "product": "The order's own product cannot be issued to it.",
        "qty": "The quantity must be a number above zero.", "reason": "Write the reason for the extra issue.",
        "short": "Not enough “{item}”: {need} needed, {have} available.",
        "not_issued": "“{item}” was not issued to this order, so none of it can come back.",
        "too_much": "No more can come back than was issued to the order: “{item}” can return {left} at most.",
        "line_state": "Actual costs can be added and cancelled only until the order finishes.",
        "line_kind": "Choose the kind of cost: labour or overhead.",
        "line_amount": "Enter the hours and the hourly rate, or the amount, as numbers above zero.",
        "line_cancelled": "This line is already cancelled.",
    },
}


def _audit(order, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="manufacturing", action=action, object_type="manufacturing.ProductionOrder",
                            object_id=str(order.pk), before_data={}, after_data=after)


def _quantity(value, words):
    from .services import _decimal

    number = _decimal(value)
    if number is None or number <= 0:
        raise ValidationError(words["qty"])
    return number


def sign(operation):
    """+1 for a material that went to the order (an adjustment out), −1 for one that came back (an adjustment in)."""

    from inventory.models import StockAdjustmentDirection

    return -1 if operation.adjustment_direction == StockAdjustmentDirection.IN else 1


def out_first(operations):
    """Materials that went out (issue, extra) before those that came back: the order the consumption rows are written
    in, and so the order a cancellation reverses them in (what went out comes back before a return leaves again)."""

    return sorted(operations, key=lambda operation: (sign(operation) < 0, operation.pk))


def position(order, item):
    """(quantity issued + extra, their value, quantity returned) of ``item`` on ``order``, posted rows only."""

    from inventory.models import StockOperationStatus

    issued, value, back = ZERO, ZERO, ZERO
    for row in order.issues.filter(operation__item=item, operation__status=StockOperationStatus.POSTED).select_related("operation"):
        operation = row.operation
        if sign(operation) < 0:
            back += operation.quantity
        else:
            issued += operation.quantity
            value += operation.quantity * operation.unit_cost
    return issued, value, back


def net_left(order, item):
    """(quantity of ``item`` still on ``order``, its value): the issues and extras less the returns, each return at the cost it
    came back at. A return is priced from this, so a return after an earlier return and a cheaper extra takes only what is
    left on the order, never the average of every issue (Codex on #439)."""

    from inventory.models import StockOperationStatus

    left, value = ZERO, ZERO
    for row in order.issues.filter(operation__item=item, operation__status=StockOperationStatus.POSTED).select_related("operation"):
        operation = row.operation
        left += sign(operation) * operation.quantity
        value += sign(operation) * operation.quantity * operation.unit_cost
    return left, value


def return_cost(order, item):
    """The unit cost a return of ``item`` comes back at: the value still on the order over the quantity still on it."""

    left, value = net_left(order, item)
    return cost_round(max(value, ZERO) / left) if left > 0 else ZERO


def issued_cost(order, item):
    """The cost ``item`` was issued to ``order`` at, weighted over its issue and extra rows (trap 3)."""

    issued, value, _back = position(order, item)
    return cost_round(value / issued) if issued > 0 else ZERO


def returnable(order):
    """[(item, quantity that may still come back, its issued cost)] for every item issued to ``order``."""

    from inventory.models import StockOperationStatus

    seen, rows = set(), []
    for row in order.issues.filter(operation__status=StockOperationStatus.POSTED).select_related("operation__item").order_by("operation__item__item_name", "pk"):
        item = row.operation.item
        if item.pk in seen:
            continue
        seen.add(item.pk)
        issued, value, back = position(order, item)
        if issued - back > 0:
            rows.append({"item": item, "left": issued - back, "unit_cost": return_cost(order, item)})
    return rows


def _floor(order, words, closed_key="on_floor"):
    from .orders import _locked

    order = _locked(order)
    if order.status in (OrderStatus.DONE, OrderStatus.CANCELLED):
        raise ValidationError(words[closed_key])
    if order.status != OrderStatus.IN_PROGRESS:
        raise ValidationError(words["on_floor"])
    if not order.issues.filter(kind=IssueKind.ISSUE).exists():
        # An order started before HG-036 consumes the recipe at its finish only when it holds no material row at all.
        raise ValidationError(words["old_order"])
    return order


@transaction.atomic
def extra_issue(order, user, *, item, quantity, reason, lang="ar"):
    """«صرف إضافي»: ``quantity`` of ``item`` from the order's location to work in progress, at today's average."""

    from batches.services import batches_enabled
    from inventory.models import StockAdjustmentDirection, StockOperation
    from inventory.services import adjust_stock, get_item_location_stock_quantity, lock_items_for_stock_check

    from . import lots, services

    words = MESSAGES[lang]
    order = _floor(order, words)
    if item is None:
        raise ValidationError(words["item"])
    if not item.is_stock_tracked:
        raise ValidationError(words["item_stock"])
    if item.pk == order.recipe.product_id:
        raise ValidationError(words["product"])
    quantity = _quantity(quantity, words)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(words["reason"])
    today = timezone.localdate()
    lock_items_for_stock_check([item.pk])  # before the lot snapshot, so nothing else leaves between the two
    have = get_item_location_stock_quantity(item, order.location)
    if have < quantity:
        raise ValidationError(words["short"].format(item=item.item_name, need=fmt_qty(quantity), have=fmt_qty(have)))
    tracking = batches_enabled()
    before = lots._positions([item], order.location) if tracking else None
    operation = adjust_stock(services._next(StockOperation, "reference_number", f"{order.number}-X", 2), today, item, order.location,
                             StockAdjustmentDirection.OUT, quantity, f"صرف إضافي {order.number} / Extra issue {order.number}: {reason}"[:255], user)
    if tracking:
        lots.record_uses([operation], before, lots._positions([item], order.location), today, lang, order=order)
    OrderMaterialIssue.objects.create(order=order, operation=operation, kind=IssueKind.EXTRA, reason=reason[:255])
    _audit(order, user, "extra_issue", {"item": item.item_code, "quantity": str(quantity), "unit_cost": str(operation.unit_cost), "reason": reason[:200]})
    return operation


@transaction.atomic
def return_materials(order, user, *, item, quantity, reason="", lang="ar"):
    """«رجّع خامات»: ``quantity`` of ``item`` back from work in progress to the order's location, at the cost still on the
    order (``return_cost``: the issued value less what earlier returns took, over the quantity left)."""

    from inventory.models import StockAdjustmentDirection, StockOperation
    from inventory.services import adjust_stock
    from permissions.services import user_has_permission

    from . import services

    words = MESSAGES[lang]
    if not user_has_permission(user, COST):
        raise PermissionDenied("Returning materials sets their cost; it needs inventory.view_cost.")
    order = _floor(order, words, "closed")
    if item is None:
        raise ValidationError(words["item"])
    quantity = _quantity(quantity, words)
    issued, value, back = position(order, item)
    if issued <= 0:
        raise ValidationError(words["not_issued"].format(item=item.item_name))
    if quantity > issued - back:
        raise ValidationError(words["too_much"].format(item=item.item_name, left=fmt_qty(issued - back)))
    reason = (reason or "").strip()
    unit_cost = return_cost(order, item)
    operation = adjust_stock(services._next(StockOperation, "reference_number", f"{order.number}-R", 2), timezone.localdate(), item, order.location,
                             StockAdjustmentDirection.IN, quantity,
                             f"رجوع خامات {order.number} / Materials returned {order.number}{': ' + reason if reason else ''}"[:255], user,
                             unit_cost=unit_cost)
    OrderMaterialIssue.objects.create(order=order, operation=operation, kind=IssueKind.RETURN, reason=reason[:255])
    _audit(order, user, "return_materials", {"item": item.item_code, "quantity": str(quantity), "unit_cost": str(unit_cost), "reason": reason[:200]})
    return operation


# ---- actual labour and overhead ----

def _number(value, words):
    """A typed hours, rate or amount: None when blank; refused when it is not a number above zero."""

    from .services import _decimal

    if value in (None, ""):
        return None
    number = _decimal(value)
    if number is None or number <= 0:
        raise ValidationError(words["line_amount"])
    return number


def _open_for_lines(order, words):
    from .orders import _locked

    order = _locked(order)
    if order.status not in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS):
        raise ValidationError(words["line_state"])
    return order


@transaction.atomic
def add_cost_line(order, user, *, kind, description="", employee=None, hours=None, rate=None, amount=None, work_centre=None, stage_log=None, lang="ar"):
    """One actual labour or overhead line: hours × rate, or an amount when no hours are given.

    Manufacturing F6a (HG-133): ``work_centre`` and ``stage_log`` are set only by a confirmed suggestion (``workcentres.confirm``)."""

    from permissions.services import user_has_permission

    words = MESSAGES[lang]
    if not user_has_permission(user, COST):
        raise PermissionDenied("Actual costs go into the product's stock cost; they need inventory.view_cost.")
    order = _open_for_lines(order, words)
    if kind not in (CostLineKind.LABOUR, CostLineKind.OVERHEAD):  # outside processing lines belong to F4
        raise ValidationError(words["line_kind"])
    hours, rate, typed = _number(hours, words), _number(rate, words), _number(amount, words)
    if hours is not None or rate is not None:
        if hours is None or rate is None:
            raise ValidationError(words["line_amount"])
        hours, rate = hours.quantize(Decimal("0.01")), rate.quantize(Decimal("0.01"))
        total = money_round(hours * rate)
    else:
        total = money_round(typed) if typed is not None else ZERO
    if total <= 0:
        raise ValidationError(words["line_amount"])
    line = OrderCostLine.objects.create(order=order, kind=kind, description=(description or "").strip()[:255], employee=employee,
                                        hours=hours, rate=rate, amount=total, work_centre=work_centre, stage_log=stage_log, created_by=user)
    _audit(order, user, "add_order_cost_line", {"line": line.pk, "kind": kind, "amount": str(total), "hours": str(hours or ""),
                                                "rate": str(rate or ""), "employee": employee.code if employee else "",
                                                "work_centre": work_centre.code if work_centre else "", "stage_log": stage_log.pk if stage_log else ""})
    return line


@transaction.atomic
def cancel_cost_line(line, user, lang="ar"):
    from permissions.services import user_has_permission

    words = MESSAGES[lang]
    if not user_has_permission(user, COST):
        raise PermissionDenied("Actual costs go into the product's stock cost; they need inventory.view_cost.")
    order = _open_for_lines(line.order, words)
    line = OrderCostLine.objects.select_for_update().get(pk=line.pk, order=order)
    if line.cancelled_at is not None:
        raise ValidationError(words["line_cancelled"])
    line.cancelled_at = timezone.now()
    line.save(update_fields=["cancelled_at"])
    _audit(order, user, "cancel_order_cost_line", {"line": line.pk, "kind": line.kind, "amount": str(line.amount)})
    return line


def conversion(order):
    """Labour, overhead and outside processing for ``order``, per kind: a kind's live lines if it has any, else its
    planned figure (outside: its lines only)."""

    sums, has = defaultdict(lambda: ZERO), set()
    for kind, amount in order.cost_lines.filter(cancelled_at__isnull=True).values_list("kind", "amount"):
        sums[kind] += amount
        has.add(kind)
    labour = money_round(sums[CostLineKind.LABOUR]) if CostLineKind.LABOUR in has else order.labor_cost
    overhead = money_round(sums[CostLineKind.OVERHEAD]) if CostLineKind.OVERHEAD in has else order.overhead_cost
    outside = money_round(sums[CostLineKind.OUTSIDE])
    return {"labour": labour, "overhead": overhead, "outside": outside, "total": money_round(labour + overhead + outside),
            "labour_actual": CostLineKind.LABOUR in has, "overhead_actual": CostLineKind.OVERHEAD in has}


# ---- the standard, written at the start ----

def write_standard(order, operations):
    """The order's standard per component: the recipe's quantity × the order's batches, at the cost it was issued at."""

    from .orders import _batches

    costs = {operation.item_id: operation.unit_cost for operation in operations}
    batches = _batches(order)
    OrderStandardLine.objects.bulk_create([
        OrderStandardLine(order=order, component_id=line.component_id, quantity=(line.quantity * batches).quantize(Decimal("0.001")),
                          unit_cost=costs.get(line.component_id, ZERO))
        for line in order.recipe.lines.all()])
