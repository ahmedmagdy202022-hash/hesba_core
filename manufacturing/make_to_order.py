"""F1 (HG-098): a confirmed sales order line makes a production order (make to order).

The link is one nullable column, ``ProductionOrder.sales_order_line``. Everything else is read, never stored:

* what a line still needs made (``uncovered``) is the line's quantity less what is already live in production or on
  an invoice. A cancelled production order is not live, so cancelling it frees its quantity again;
* opening the production order goes through the unchanged ``orders.create_order`` and posts nothing: no stock
  movement, no cashbox row, no journal, no invoice. The stages, the issue of materials and the finish are the ordinary
  production order's, and the sale is the ordinary invoice of the sales order;
* the goods made are not reserved for the customer (there is no reservation in the system). The sales order line
  shows what was made and what is in stock, and says so when the till sold them first;
* the delivery date promised on the sales order is copied to the production order, and follows the sales order while
  the production order is still on the floor (``sync_due``), so the board's late flag and every report that reads
  ``due_date`` keep working unchanged.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from inventory.services import get_item_location_stock_quantity
from master_data.models import Location
from orders import services as sales
from orders.models import SalesOrder, SalesOrderLine, Status
from settings_core.templatetags.hesba_format import qty as fmt_qty

from . import orders as production
from . import services
from .models import OrderStatus, ProductionOrder, Recipe, RunStatus

ZERO = Decimal("0")
#: A production order that still counts against what a line needs: planned, on the floor or done. A cancelled one frees it.
LIVE = (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS, OrderStatus.DONE)
ON_THE_FLOOR = (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)

MESSAGES = {
    "ar": {"not_confirmed": "أمر البيع لسه مش مؤكد؛ العميل لازم يوافق الأول.",
           "no_recipe": "مفيش وصفة شغالة للصنف ده؛ اعمل وصفة له من صفحة التصنيع الأول.",
           "covered": "الكمية دي بتتصنع بالفعل.", "quantity": "الكمية لازم تكون رقم أكبر من صفر.",
           "too_many": "الكمية أكتر من اللي لسه ما اتغطاش ({max}).",
           "location": "اختار مكان الإنتاج من مخازن جهة الأمر.", "no_location": "مفيش مخزن شغال لجهة أمر البيع ده؛ ضيف مخزن الأول."},
    "en": {"not_confirmed": "This sales order is not confirmed yet; the customer has to accept it first.",
           "no_recipe": "This item has no active recipe; make one on the manufacturing page first.",
           "covered": "This quantity is already being made.", "quantity": "The quantity must be a number above zero.",
           "too_many": "The quantity is more than what is still not covered ({max}).",
           "location": "Choose where it is made from this order's own locations.", "no_location": "This sales order's entity has no active location; add one first."},
}

#: The words of the «الإنتاج» column and its form on the sales order page, and of the link on the production screens.
WORDS = {
    "ar": {"column": "الإنتاج", "make": "اعمل أمر إنتاج", "quantity": "الكمية", "location": "مكان الإنتاج", "labor": "تكلفة العمالة",
           "overhead": "مصاريف تشغيل", "recipe": "الوصفة", "open": "افتح الأمر", "order_word": "أمر الإنتاج",
           "made_ok": "أمر الإنتاج {number} اتفتح للطلب ده.", "for_sale": "لطلب", "delivery": "تسليم للعميل",
           "none": "لسه ما اتصنعش", "planned": "لسه مبدأش", "ready": "جاهز للإنهاء", "done": "خلص", "at_stage": "في مرحلة {stage}",
           "uncovered": "لسه ما اتغطاش: {qty}", "made_stock": "اتصنع {made} · في المخزن {stock}",
           "short": "المخزون ناقص: المتاح {stock}، و{waiting} مصنّعين لسه ما اتفوترش.",
           "no_recipe": "مفيش وصفة للصنف ده", "cancel_warn": "في أمر إنتاج مفتوح مربوط بالأمر ده. لو ألغيت أمر البيع، أمر الإنتاج مش هيتلغي لوحده.",
           "cancelled_warn": "أمر البيع اتلغى، لكن أمر الإنتاج {numbers} لسه شغال. ألغيه من شاشته لو مش محتاجه.",
           "no_stock_hint": "المنتج مش بيتحجز للعميل؛ أي بيع تاني بياخد من نفس المخزن."},
    "en": {"column": "Production", "make": "Make a production order", "quantity": "Quantity", "location": "Made at", "labor": "Labour cost",
           "overhead": "Overhead", "recipe": "Recipe", "open": "Open the order", "order_word": "Production order",
           "made_ok": "Production order {number} opened for this order.", "for_sale": "For order", "delivery": "Delivery to the customer",
           "none": "Not made yet", "planned": "Not started", "ready": "Ready to finish", "done": "Done", "at_stage": "Stage: {stage}",
           "uncovered": "Not covered yet: {qty}", "made_stock": "Made {made} · in stock {stock}",
           "short": "Stock is short: {stock} available, and {waiting} made but not invoiced yet.",
           "no_recipe": "No recipe for this item", "cancel_warn": "An open production order is linked to this order. Cancelling the sales order does not cancel it.",
           "cancelled_warn": "The sales order is cancelled, but production order {numbers} is still running. Cancel it from its own screen if it is not needed.",
           "no_stock_hint": "The product is not reserved for the customer; any other sale takes from the same stock."},
}


def manufacturing_on():
    """The manufacturing module is switched on (its screens are not closed by the module gate)."""

    from settings_core.module_gate import closed_module

    return closed_module("/manufacturing/") is None


# ---- what a line needs ----

def recipe_for(item):
    """The recipe a make-to-order production order uses: the first active one by code (owner question 2)."""

    return Recipe.objects.filter(product=item, active=True).order_by("code").first()


def _live_links(order):
    """Every live production order linked to a line of ``order``, inside the entity being worked in.

    A finished order whose run was reversed (``services.cancel_run`` takes the goods back out of stock and leaves the
    order «done») made nothing: it is not live, so its quantity is free to be made again."""

    return (entity_scope.scope(ProductionOrder.objects, "location__entity").filter(sales_order_line__order=order, status__in=LIVE)
            .exclude(status=OrderStatus.DONE, run__status=RunStatus.CANCELLED).select_related("run"))


def _counts_for(link):
    """What a live production order covers of its line: what it plans while it is planned or on the floor, and the good
    units it really made once it is done (a run that lost five to defects covers five fewer)."""

    return link.run.output_quantity if link.status == OrderStatus.DONE and link.run_id else link.quantity


def made(order):
    """Per line pk: what the live production orders linked to it cover (planned, on the floor and done)."""

    total = {}
    for link in _live_links(order):
        total[link.sales_order_line_id] = total.get(link.sales_order_line_id, ZERO) + _counts_for(link)
    return total


def _uncovered(row, in_production):
    """What a line still needs made: the quantity left to invoice, and never more than the order less what is in production.

    The plan's own formula subtracted what is invoiced and what is made, which counts a made unit twice once it
    is invoiced; the units already invoiced are taken to be the made ones first."""

    return max(min(row["left"], row["line"].quantity - in_production), ZERO)


def uncovered(line):
    """What ``line`` still needs made."""

    row = next(row for row in sales.quantities(line.order) if row["line"].pk == line.pk)
    return _uncovered(row, made(line.order).get(line.pk, ZERO))


# ---- the entity's places ----

def locations_for(order):
    """The active locations of the sales order's own entity (an order without one belongs to the main entity), the default first."""

    from entities.services import main_entity

    entity = order.entity or main_entity()
    places = Location.objects.filter(active=True)
    places = places.filter(Q(entity=entity) | Q(entity__isnull=True)) if entity.is_main else places.filter(entity=entity)
    return places.order_by("-is_default", "location_code")


# ---- the action ----

def _audit(order_line, user, production_order, quantity):
    AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="manufacturing", action="make_production_order_for_sale",
                            object_type="manufacturing.ProductionOrder", object_id=str(production_order.pk),
                            after_data={"number": production_order.number, "sales_order": order_line.order.display_number,
                                        "item": order_line.item.item_code, "quantity": str(quantity)})


@transaction.atomic
def make(line, user, data, lang="ar"):
    """«اعمل أمر إنتاج»: a production order for (part of) what a confirmed order line still needs made.

    ``data``: quantity (default: all that is uncovered), location (default: the entity's default one), labour and overhead
    planned. Posts nothing. Returns the new ProductionOrder."""

    words = MESSAGES[lang]
    order = SalesOrder.objects.select_for_update().get(pk=line.order_id)  # one at a time per order: a double tap cannot make it twice
    line = SalesOrderLine.objects.select_related("item", "order__customer").get(pk=line.pk)
    if order.status != Status.CONFIRMED:
        raise ValidationError(words["not_confirmed"])
    recipe = recipe_for(line.item)
    if recipe is None:
        raise ValidationError(words["no_recipe"])
    free = uncovered(line)
    if free <= 0:
        raise ValidationError(words["covered"])
    raw = data.get("quantity")
    quantity = free if raw in (None, "") else services._decimal(raw)
    if quantity is None or quantity <= 0:
        raise ValidationError(words["quantity"])
    if quantity > free:
        raise ValidationError(words["too_many"].format(max=fmt_qty(free)))
    places = locations_for(order)
    location = data.get("location") or places.first()
    if location is None:
        raise ValidationError(words["no_location"])
    if not places.filter(pk=location.pk).exists():
        raise ValidationError(words["location"])
    created = production.create_order(
        {"recipe": recipe, "location": location, "quantity": quantity, "customer": order.customer, "due_date": order.deliver_by,
         "labor_cost": data.get("labor_cost"), "overhead_cost": data.get("overhead_cost")}, user, lang)
    created.sales_order_line = line
    created.save(update_fields=["sales_order_line"])
    _audit(line, user, created, quantity)
    return created


def sync_due(order):
    """The sales order's delivery date moved: a production order still on the floor for it takes the new date."""

    ProductionOrder.objects.filter(sales_order_line__order=order, status__in=ON_THE_FLOOR).update(due_date=order.deliver_by)


def open_for(order):
    """The production orders linked to ``order`` that are still planned or on the floor."""

    return list(_live_links(order).filter(status__in=ON_THE_FLOOR).order_by("pk"))


# ---- what the sales order page shows ----

def _stage(order, lang):
    words = WORDS[lang]
    if order.status == OrderStatus.DONE:
        return "done", words["done"]
    if order.status == OrderStatus.PLANNED:
        return "planned", words["planned"]
    if order.stage_index >= len(order.stages):
        return "ready", words["ready"]
    stage = order.stages[order.stage_index]
    return "running", words["at_stage"].format(stage=stage[2] if lang == "en" else stage[1])


def line_states(order, rows, lang, *, can_make):
    """Per line pk, what the «الإنتاج» column shows: its production orders and where they stand, what is not covered yet, what
    was made against what is in stock (and a warning when the stock was sold first), and whether the action is offered."""

    words = WORDS[lang]
    links = {}
    for link in _live_links(order).select_related("run", "location").order_by("pk"):
        links.setdefault(link.sales_order_line_id, []).append(link)
    items = [row["line"].item_id for row in rows]
    recipes = {}
    for recipe in Recipe.objects.filter(product_id__in=items, active=True).order_by("code"):
        recipes.setdefault(recipe.product_id, recipe)
    confirmed = order.status == Status.CONFIRMED
    states = {}
    for row in rows:
        line = row["line"]
        mine = links.get(line.pk, [])
        in_production = sum((_counts_for(link) for link in mine), ZERO)
        free = _uncovered(row, in_production) if confirmed else ZERO
        recipe = recipes.get(line.item_id)
        done = [link for link in mine if link.status == OrderStatus.DONE]
        made_done = sum((link.run.output_quantity if link.run_id else link.quantity for link in done), ZERO)
        stock = ZERO
        if done:
            for place in {link.location_id: link.location for link in done}.values():
                stock += get_item_location_stock_quantity(line.item, place)
        waiting = min(row["left"], max(made_done - row["invoiced"] - row["drafted"], ZERO))
        states[line.pk] = {
            "orders": [{"order": link, "state": _stage(link, lang)[0], "label": _stage(link, lang)[1]} for link in mine],
            "free": free, "free_plain": f"{free.normalize():f}", "recipe": recipe, "can_make": bool(can_make and confirmed and recipe is not None and free > 0),
            "no_recipe": bool(confirmed and recipe is None and not mine), "made": made_done, "stock": stock, "waiting": waiting,
            "short": bool(done and waiting > 0 and stock < waiting), "has_made": bool(done),
            "uncovered_text": words["uncovered"].format(qty=fmt_qty(free)) if free > 0 and mine else "",
            "made_text": words["made_stock"].format(made=fmt_qty(made_done), stock=fmt_qty(stock)) if done else "",
            "short_text": words["short"].format(stock=fmt_qty(stock), waiting=fmt_qty(waiting)) if done and waiting > 0 and stock < waiting else "",
        }
    return states
