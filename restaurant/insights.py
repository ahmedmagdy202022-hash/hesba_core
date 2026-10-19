"""RESTO-002: what each dish costs and earns, and how the floor is doing.

Read-only. Dish cost is the sum of its ingredients (``DishIngredient``) at each
ingredient's current average cost; it is an estimate for pricing, and nothing
here takes ingredients out of stock (HG-044's deduction waits for Ahmed).

Sales figures come from paid orders whose invoice is posted, in the entity
being worked in (HG-034):

- dish figures are the order lines at their menu price, before the order's
  discount;
- order totals are the invoice's net sales (before tax, after posted returns).

Waste is what was taken out of stock by an adjustment in the period, with its
reason, at movement cost.
"""

from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope

from .models import DishIngredient, Order, OrderKind, OrderStatus

ZERO = Decimal("0")
MESSAGES = {
    "ar": {"ingredient": "المكوّن لازم يكون صنف مخزني (خامة).", "self": "الطبق مينفعش يبقى مكوّن لنفسه.", "qty": "كمية «{item}» لازم تكون رقم أكبر من صفر.",
           "twice": "«{item}» متكرر."},
    "en": {"ingredient": "An ingredient must be a stocked item (a raw material).", "self": "A dish cannot be its own ingredient.",
           "qty": "The quantity of “{item}” must be a number above zero.", "twice": "“{item}” appears twice."},
}


# ---- dish cost ----

def plate_cost(dish, ingredients=None):
    """Cost of one plate at the ingredients' current average cost, or None when the dish has no ingredients."""

    rows = ingredients if ingredients is not None else list(dish.dish_ingredients.select_related("ingredient"))
    if not rows:
        return None
    return money_round(sum((Decimal(row.quantity) * Decimal(row.ingredient.average_cost or 0) for row in rows), ZERO))


def menu_costing(dishes):
    """[{dish, price, cost, margin, margin_pct, ingredients}] for the given dishes (items), most profitable share first."""

    lines = {}
    for row in DishIngredient.objects.filter(dish__in=dishes).select_related("ingredient"):
        lines.setdefault(row.dish_id, []).append(row)
    result = []
    for dish in dishes:
        ingredients = lines.get(dish.pk, [])
        cost = plate_cost(dish, ingredients)
        price = Decimal(dish.default_sale_price or 0)
        margin = price - cost if cost is not None else None
        pct = round(margin / price * 100) if margin is not None and price > 0 else None
        result.append({"dish": dish, "price": price, "cost": cost, "margin": margin, "margin_pct": pct, "ingredients": ingredients})
    return sorted(result, key=lambda row: (row["margin_pct"] is None, -(row["margin_pct"] or 0), row["dish"].item_name))


@transaction.atomic
def save_ingredients(dish, lines, user, lang="ar"):
    """Replace the dish's ingredients. ``lines``: [(item, raw quantity)]; blank rows are skipped."""

    words = MESSAGES["en" if lang == "en" else "ar"]
    clean, seen = [], set()
    for ingredient, raw in lines:
        if ingredient is None and not (raw or "").strip():
            continue
        if ingredient is None or not ingredient.is_stock_tracked:
            raise ValidationError(words["ingredient"])
        if ingredient.pk == dish.pk:
            raise ValidationError(words["self"])
        if ingredient.pk in seen:
            raise ValidationError(words["twice"].format(item=ingredient.item_name))
        try:
            quantity = Decimal(str(raw or "").strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            raise ValidationError(words["qty"].format(item=ingredient.item_name))
        if not quantity.is_finite() or quantity >= 100000 or quantity.quantize(Decimal("0.001")) <= 0:  # 0.0004 rounds to nothing
            raise ValidationError(words["qty"].format(item=ingredient.item_name))
        seen.add(ingredient.pk)
        clean.append((ingredient, quantity.quantize(Decimal("0.001"))))
    before = [{"item": row.ingredient.item_code, "quantity": str(row.quantity)} for row in dish.dish_ingredients.select_related("ingredient")]
    dish.dish_ingredients.all().delete()
    DishIngredient.objects.bulk_create([DishIngredient(dish=dish, ingredient=item, quantity=qty) for item, qty in clean])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="restaurant", action="save_dish_ingredients", object_type="master_data.Item",
                            object_id=str(dish.pk), before_data={"ingredients": before},
                            after_data={"ingredients": [{"item": item.item_code, "quantity": str(qty)} for item, qty in clean]})
    return clean


# ---- the floor ----

def _span(first, last):
    start = timezone.make_aware(timezone.datetime.combine(first, timezone.datetime.min.time()))
    return start, timezone.make_aware(timezone.datetime.combine(last + timedelta(days=1), timezone.datetime.min.time()))


def _paid(first, last):
    start, end = _span(first, last)
    return (entity_scope.scope(Order.objects, "invoice__" + entity_scope.SALES_INVOICE)
            .filter(status=OrderStatus.PAID, invoice__status="posted", closed_at__gte=start, closed_at__lt=end)
            .select_related("invoice", "table", "waiter").prefetch_related("lines__item"))


def period_report(first, last):
    """The period's orders by hour, top dishes, tables, waiters and kinds."""

    from django.db.models import Sum

    from sales.models import SalesReturn

    orders = list(_paid(first, last))
    returned = dict(SalesReturn.objects.filter(source_invoice__in=[order.invoice_id for order in orders], status="posted")
                    .values("source_invoice").annotate(total=Sum("total_amount")).values_list("source_invoice", "total"))

    def net_sales(invoice):  # appointments.services.net_sales, with the returns read once for the whole period
        total = Decimal(invoice.total_amount)
        if total <= 0:
            return Decimal("0.00")
        before_tax = total - Decimal(invoice.tax_amount or 0)
        back = Decimal(returned.get(invoice.pk) or 0)
        return money_round(max(before_tax - back * before_tax / total, Decimal("0")))

    days = (last - first).days + 1
    hours = OrderedDict((hour, {"hour": hour, "orders": 0, "sales": ZERO}) for hour in range(24))
    dishes, tables, waiters, kinds = {}, {}, {}, {}
    total, guests, minutes = ZERO, 0, []
    for order in orders:
        sales = net_sales(order.invoice)
        total += sales
        hour = timezone.localtime(order.closed_at).hour
        hours[hour]["orders"] += 1
        hours[hour]["sales"] += sales
        for line in order.lines.all():
            if line.voided:
                continue
            row = dishes.setdefault(line.item_id, {"item": line.item, "quantity": ZERO, "sales": ZERO})
            row["quantity"] += line.quantity
            row["sales"] += money_round(line.quantity * line.unit_price)
        kind = kinds.setdefault(order.kind, {"kind": order.kind, "orders": 0, "sales": ZERO})
        kind["orders"] += 1
        kind["sales"] += sales
        if order.kind == OrderKind.DINE_IN and order.table_id:
            table = tables.setdefault(order.table_id, {"table": order.table, "orders": 0, "sales": ZERO, "guests": 0, "minutes": []})
            table["orders"] += 1
            table["sales"] += sales
            table["guests"] += order.guests
            table["minutes"].append((order.closed_at - order.opened_at).total_seconds() / 60)
            guests += order.guests
            minutes.append((order.closed_at - order.opened_at).total_seconds() / 60)
        waiter = waiters.setdefault(order.waiter_id, {"waiter": order.waiter, "orders": 0, "sales": ZERO})
        waiter["orders"] += 1
        waiter["sales"] += sales
    for row in tables.values():
        row["per_day"] = round(row["orders"] / days, 1)
        row["average"] = money_round(row["sales"] / row["orders"])
        row["minutes"] = round(sum(row["minutes"]) / len(row["minutes"]))
    for row in list(waiters.values()) + list(kinds.values()):
        row["average"] = money_round(row["sales"] / row["orders"]) if row["orders"] else ZERO
    busy = [row for row in hours.values() if row["orders"]]
    count = len(orders)
    return {
        "hours": busy,
        "peak": max(busy, key=lambda row: row["orders"]) if busy else None,
        "dishes": sorted(dishes.values(), key=lambda row: (-row["quantity"], row["item"].item_name))[:20],
        "tables": sorted(tables.values(), key=lambda row: (-row["orders"], row["table"].sort_order, row["table"].name)),
        "waiters": sorted(waiters.values(), key=lambda row: -row["sales"]),
        "kinds": sorted(kinds.values(), key=lambda row: -row["orders"]),
        "totals": {"orders": count, "sales": money_round(total), "average": money_round(total / count) if count else ZERO,
                   "guests": guests, "minutes": round(sum(minutes) / len(minutes)) if minutes else None},
    }


def waste(first, last):
    """Stock thrown away in the period: [{movement, value}] and the total value.

    An outbound adjustment that a production run or an order consumed is cooking, not waste."""

    from inventory.models import StockMovement, StockMovementType

    rows = list(entity_scope.scope(StockMovement.objects, entity_scope.STOCK_MOVEMENT)
                .filter(movement_type=StockMovementType.ADJUSTMENT_OUT, movement_date__gte=first, movement_date__lte=last)
                .exclude(stock_operation__status="cancelled")  # a cancelled adjustment put the stock back
                # W16: ingredients cooked into plates (RESTO-004) or issued to an order are used, not wasted
                .exclude(stock_operation__production_consumption__isnull=False)
                .exclude(stock_operation__order_issue__isnull=False)
                .select_related("item", "stock_operation").order_by("-movement_date", "-pk"))
    out = [{"movement": row, "value": money_round(abs(Decimal(row.quantity)) * Decimal(row.unit_cost or 0)),
            "reason": getattr(row.stock_operation, "reason", "") if getattr(row, "stock_operation_id", None) else ""} for row in rows]
    return out, money_round(sum((row["value"] for row in out), ZERO))


def deliveries(first, last):
    """RESTO-003: paid deliveries per driver: count, delivery fees, average minutes from leaving to closing."""

    rows = {}
    for order in _paid(first, last).filter(kind=OrderKind.DELIVERY).select_related("driver"):
        row = rows.setdefault(order.driver_id, {"driver": order.driver, "orders": 0, "fees": ZERO, "minutes": []})
        row["orders"] += 1
        row["fees"] += order.delivery_fee or ZERO
        if order.dispatched_at and order.closed_at and order.closed_at >= order.dispatched_at:
            row["minutes"].append((order.closed_at - order.dispatched_at).total_seconds() / 60)
    for row in rows.values():
        row["fees"] = money_round(row["fees"])
        row["minutes"] = round(sum(row["minutes"]) / len(row["minutes"])) if row["minutes"] else None
    return sorted(rows.values(), key=lambda row: (row["driver"] is None, -row["orders"]))
