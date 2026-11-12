"""F2: material requirements (MRP) from open orders, into draft purchase orders. No schema.

``calculate()`` only reads. It answers, for the entity being worked in: what must be bought, and which semi-finished
items must be made, so that the open orders and the minimum stock are covered.

Demand (every quantity is a Decimal with three places, worked out the way the production order issues it, so the need
shown is what ``orders.advance`` will really take out of stock):

* production orders planned and not started: their recipe times their batches (an order already started has its materials
  out of stock, so it asks for nothing more);
* confirmed sales order lines whose item has an active recipe: what is uncovered (F1's ``make_to_order``, which already
  leaves out what is invoiced, planned, on the floor or made) less the product's free stock, the stock being handed out
  by delivery date, earliest first. Units made for a line and not yet invoiced are not free;
* the minimum stock of every material a recipe uses.

Explosion: a component that has its own active recipe is a semi-finished item. Each one is netted once, against its total
gross need, after every item that uses it has been netted (levels run from the top, a depth of ten is the guard, and a
recipe loop saved before ``save_recipe`` refused them is reported, never walked). Its need is netted against its own stock
and what is already coming (purchase orders, and its own production orders still open), then rounded UP to whole batches
of its recipe, then exploded into its components. Raw materials are exact.

Supply: stock on hand in the entity's locations; confirmed purchase orders not yet received (ordered less what a posted
purchase invoice took; a draft receipt is not stock yet); drafts and orders merely sent are NOT counted, they are only
noted beside the row so nobody orders the same thing twice.

Nothing here posts, moves stock, a cashbox or a ledger. The two actions go through the unchanged ``orders.purchasing``
(draft purchase orders) and ``manufacturing.orders.create_order`` (a planned production order).
"""

from collections import defaultdict, deque
from decimal import ROUND_CEILING, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from entities import scope as entity_scope
from master_data.models import Item, Location, Supplier
from orders import purchasing as po
from orders import services as sales
from orders.models import OrderReceiptLine, PurchaseOrder, PurchaseOrderLine, PurchaseStatus
from orders.models import Status as SalesStatus
from settings_core.templatetags.hesba_format import qty as fmt_qty

from . import make_to_order
from . import orders as production
from .models import OrderStatus, ProductionOrder, Recipe

ZERO = Decimal("0")
STEP = Decimal("0.001")
MAX_DEPTH = 10
#: An order is "open" for the product it makes while it is planned or on the floor: its goods are still to come.
OPEN = (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)

MESSAGES = {
    "ar": {"nothing_to_buy": "اختار خامة واحدة على الأقل.", "gone": "«{item}» مش ناقص دلوقتي؛ حدّث الصفحة وشوف الاحتياج من جديد.",
           "quantity": "كمية «{item}» لازم تكون رقم أكبر من صفر.", "supplier": "اختار مورد لـ«{item}».",
           "no_buy_right": "عمل أمر الشراء محتاج صلاحية فواتير الشراء.", "make_gone": "«{item}» مش محتاج تصنيع دلوقتي؛ حدّث الصفحة.",
           "place": "اختار مكان الإنتاج من مخازن الجهة اللي شغال فيها.", "no_place": "مفيش مخزن شغال للجهة دي؛ ضيف مخزن الأول.",
           "note": "من احتياج الخامات"},
    "en": {"nothing_to_buy": "Tick at least one material.", "gone": "“{item}” is not short any more; refresh the page and look at the requirements again.",
           "quantity": "The quantity of “{item}” must be a number above zero.", "supplier": "Choose a supplier for “{item}”.",
           "no_buy_right": "Making a purchase order needs the purchase invoices permission.", "make_gone": "“{item}” does not need making now; refresh the page.",
           "place": "Choose where it is made from the locations of the entity you are working in.", "no_place": "This entity has no active location; add one first.",
           "note": "From the material requirements"},
}


def _q(value):
    """Three places, the way ``orders._batches`` and the issue round them."""

    return value.quantize(STEP)


# ---- what can be made ----

def active_recipes():
    """Product pk to the active recipe an order uses (the first by code), kitchen dishes left out (trap 12: a dish is made
    at the till, not planned)."""

    from restaurant.kitchen import RECIPE_PREFIX

    found = {}
    for recipe in (Recipe.objects.filter(active=True).exclude(code__startswith=RECIPE_PREFIX)
                   .select_related("product").prefetch_related("lines__component").order_by("code")):
        found.setdefault(recipe.product_id, recipe)
    return found


def _need_of(recipe, quantity):
    """What making ``quantity`` of the recipe's product takes: (component pk, quantity) per line."""

    batches = _q(quantity / recipe.output_quantity)
    return [(line.component_id, _q(line.quantity * batches)) for line in recipe.lines.all()]


# ---- supply, each in a few queries and inside the entity ----

def _on_hand(item_ids):
    from inventory.models import StockMovement
    from inventory.services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES

    total = defaultdict(lambda: ZERO)
    rows = (entity_scope.scope(StockMovement.objects, entity_scope.STOCK_MOVEMENT).filter(item_id__in=item_ids)
            .values("item_id", "movement_type").annotate(total=Sum("quantity")))
    for row in rows:
        sign = 1 if row["movement_type"] in IN_MOVEMENT_TYPES else -1 if row["movement_type"] in OUT_MOVEMENT_TYPES else 0
        total[row["item_id"]] += sign * (row["total"] or ZERO)
    return {pk: max(quantity, ZERO) for pk, quantity in total.items()}  # a negative count is a mistake to fix, not stock to plan with


def _incoming(item_ids):
    """Per item, what confirmed purchase orders still owe: ordered less what posted purchase invoices received.

    ``orders.purchasing.quantities`` also takes off quantities on a draft invoice, which are not in stock yet, so it would
    leave them neither on hand nor coming."""

    orders = po.scoped(PurchaseOrder.objects.filter(status=PurchaseStatus.CONFIRMED))
    lines = list(PurchaseOrderLine.objects.filter(order__in=orders, item_id__in=item_ids).values("pk", "item_id", "quantity"))
    received = defaultdict(lambda: ZERO)
    links = (OrderReceiptLine.objects.filter(order_line_id__in=[line["pk"] for line in lines], purchase_line__invoice__status="posted")
             .values("order_line_id").annotate(total=Sum("quantity")))
    for row in links:
        received[row["order_line_id"]] += row["total"] or ZERO
    coming = defaultdict(lambda: ZERO)
    for line in lines:
        coming[line["item_id"]] += max(line["quantity"] - received[line["pk"]], ZERO)
    return coming


def _making(item_ids):
    """Per item, what the entity's own production orders (planned or on the floor) will still bring into stock."""

    rows = (entity_scope.scope(ProductionOrder.objects, "location__entity").filter(status__in=OPEN, recipe__product_id__in=item_ids)
            .values("recipe__product_id").annotate(total=Sum("quantity")))
    return {row["recipe__product_id"]: row["total"] or ZERO for row in rows}


def _drafts(item_ids):
    """Per item, the purchase orders not confirmed yet (draft or sent) that already ask for it. Noted, never counted."""

    orders = po.scoped(PurchaseOrder.objects.filter(status__in=(PurchaseStatus.DRAFT, PurchaseStatus.SENT)))
    found = defaultdict(list)
    for line in (PurchaseOrderLine.objects.filter(order__in=orders, item_id__in=item_ids).select_related("order").order_by("order__pk")):
        found[line.item_id].append({"pk": line.order_id, "number": line.order.number, "quantity": line.quantity, "status": line.order.status})
    return found


def _last_purchases(item_ids):
    """The last posted purchase of each item inside the entity: its supplier, price and date.

    Not ``inventory.reorder._last_purchases``, which reads every entity."""

    from purchases.models import PurchaseLine

    latest = {}
    lines = (PurchaseLine.objects.filter(item_id__in=item_ids, invoice__status="posted")
             .filter(entity_scope.q(entity_scope.PURCHASE_INVOICE, "invoice__")).select_related("invoice__supplier")
             .order_by("item_id", "-invoice__invoice_date", "-id"))
    for line in lines:
        latest.setdefault(line.item_id, line)
    return latest


# ---- demand ----

def _sales_demand(recipes, on_hand_of):
    """Confirmed sales order lines that still need making, after the product's free stock: a list of source dicts.

    ``on_hand_of`` reads stock for the products named (a callable, so the products are known before the query)."""

    if not sales.enabled():
        return []
    lines = []
    for order in sales.scoped().filter(status=SalesStatus.CONFIRMED).select_related("customer").order_by("pk"):
        rows = sales.quantities(order)
        states = make_to_order.line_states(order, rows, "en", can_make=False)
        for row in rows:
            state = states[row["line"].pk]
            lines.append({"order": order, "line": row["line"], "free": state["free"], "waiting": state["waiting"]})
    products = {entry["line"].item_id for entry in lines}
    stock = on_hand_of(products)
    earmarked = defaultdict(lambda: ZERO)  # made for a line and not invoiced yet: not free for another one
    for entry in lines:
        earmarked[entry["line"].item_id] += entry["waiting"]
    left = {pk: max(stock.get(pk, ZERO) - earmarked[pk], ZERO) for pk in products}
    found = []
    for entry in sorted(lines, key=lambda e: (e["order"].deliver_by is None, e["order"].deliver_by or 0, e["order"].pk, e["line"].pk)):
        item_id, free = entry["line"].item_id, entry["free"]
        recipe = recipes.get(item_id)
        if recipe is None or free <= 0:
            continue
        from_stock = min(free, left[item_id])
        left[item_id] -= from_stock
        if free - from_stock > 0:
            found.append({"kind": "sale", "number": entry["order"].display_number, "pk": entry["order"].pk, "item": entry["line"].item,
                          "recipe": recipe, "uncovered": free, "from_stock": from_stock, "quantity": free - from_stock,
                          "date": entry["order"].deliver_by, "party": entry["order"].customer.name})
    return found


def _planned_demand():
    found = []
    for order in (entity_scope.scope(ProductionOrder.objects, "location__entity").filter(status=OrderStatus.PLANNED)
                  .select_related("recipe__product", "customer").prefetch_related("recipe__lines__component").order_by("due_date", "pk")):
        found.append({"kind": "order", "number": order.number, "pk": order.pk, "item": order.recipe.product, "recipe": order.recipe,
                      "quantity": order.quantity, "date": order.due_date, "party": order.customer.name if order.customer else ""})
    return found


# ---- the calculation ----

def _closure(seeds, recipes):
    """The semi-finished items reachable from ``seeds`` through their recipes, and each one's parents inside that set."""

    seen, parents, stack = set(), defaultdict(set), list(seeds)
    while stack:
        pk = stack.pop()
        if pk in seen:
            continue
        seen.add(pk)
        for line in recipes[pk].lines.all():
            if line.component_id in recipes:
                parents[line.component_id].add(pk)
                stack.append(line.component_id)
    return seen, parents


def calculate():
    """The requirements of the entity being worked in. Read only.

    Returns ``{"materials": [...], "semis": [...], "sources": [...], "warnings": [...], "counts": {...}}``."""

    recipes = active_recipes()
    uses = {line.component_id for recipe in recipes.values() for line in recipe.lines.all()}
    sources = _planned_demand()
    sources += _sales_demand(recipes, _on_hand)
    need = defaultdict(lambda: ZERO)
    for source in sources:
        for component, quantity in _need_of(source["recipe"], source["quantity"]):
            need[component] += quantity
    minimum_items = {item.pk: item for item in Item.objects.filter(pk__in=uses, active=True, is_stock_tracked=True, min_stock__gt=0)}
    # the semi-finished items that matter: used by the demand, or held at a minimum of their own
    seeds = {pk for pk in set(need) | set(minimum_items) if pk in recipes}
    members, parents = _closure(seeds, recipes)
    ids = set(need) | set(minimum_items) | members | {line.component_id for pk in members for line in recipes[pk].lines.all()}
    items = {item.pk: item for item in Item.objects.filter(pk__in=ids)}
    on_hand, coming, making = _on_hand(ids), _incoming(ids), _making(ids)

    def minimum(pk):
        item = items.get(pk)
        return item.min_stock if item is not None and item.active and item.is_stock_tracked else ZERO

    warnings, semis = [], []
    # level by level from the top: an item is netted once, after every item that uses it
    waiting_on = {pk: len(parents[pk]) for pk in members}
    level = {pk: 0 for pk in members}
    queue = deque(sorted((pk for pk in members if waiting_on[pk] == 0), key=lambda pk: items[pk].item_name))
    done = set()
    while queue:
        pk = queue.popleft()
        done.add(pk)
        recipe = recipes[pk]
        gross = need[pk]
        arriving = coming[pk] + making.get(pk, ZERO)
        net = max(gross + minimum(pk) - on_hand.get(pk, ZERO) - arriving, ZERO)
        batches = (net / recipe.output_quantity).to_integral_value(rounding=ROUND_CEILING)
        if level[pk] >= MAX_DEPTH and batches > 0:
            warnings.append({"kind": "depth", "item": items[pk]})
            batches = ZERO
        semis.append({"item": items[pk], "recipe": recipe, "level": level[pk], "need": gross, "on_hand": on_hand.get(pk, ZERO), "coming": coming[pk],
                      "making": making.get(pk, ZERO), "incoming": arriving, "minimum": minimum(pk), "net": net, "batches": batches,
                      "make": batches * recipe.output_quantity, "drafts": []})
        for line in recipe.lines.all():
            need[line.component_id] += line.quantity * batches
            if line.component_id in members:
                level[line.component_id] = max(level[line.component_id], level[pk] + 1)
                waiting_on[line.component_id] -= 1
                if waiting_on[line.component_id] == 0:
                    queue.append(line.component_id)
    for pk in sorted(members - done, key=lambda pk: items[pk].item_name):
        warnings.append({"kind": "loop", "item": items[pk]})  # a loop (or something below one): nothing is exploded through it
    semi_ids = set(members)
    materials = []
    for pk in set(need) | set(minimum_items):
        if pk in semi_ids or pk in recipes or pk not in items:
            continue
        item = items[pk]
        gross, held, arriving = need[pk], on_hand.get(pk, ZERO), coming[pk]
        to_buy = max(gross + minimum(pk) - held - arriving, ZERO)
        if gross <= 0 and to_buy <= 0:
            continue
        materials.append({"item": item, "need": gross, "on_hand": held, "incoming": arriving, "minimum": minimum(pk), "to_buy": to_buy,
                          "supplier": None, "last_price": None, "last_date": None, "drafts": []})
    semis = [row for row in semis if row["need"] > 0 or row["minimum"] > 0 or row["make"] > 0]
    buying = [row["item"].pk for row in materials if row["to_buy"] > 0]
    last = _last_purchases(buying)
    draft_notes = _drafts({row["item"].pk for row in materials})
    for row in materials:
        line = last.get(row["item"].pk)
        if line is not None and line.invoice.supplier.active:
            row.update(supplier=line.invoice.supplier, last_price=line.unit_purchase_price, last_date=line.invoice.invoice_date)
        row["drafts"] = draft_notes.get(row["item"].pk, [])
    for row in semis:
        row["drafts"] = []
    materials.sort(key=lambda row: (row["to_buy"] <= 0, -row["to_buy"], row["item"].item_name))
    semis.sort(key=lambda row: (row["make"] <= 0, row["level"], row["item"].item_name))
    return {"materials": materials, "semis": semis, "sources": sources, "warnings": warnings,
            "counts": {"planned": sum(1 for s in sources if s["kind"] == "order"), "sales": sum(1 for s in sources if s["kind"] == "sale"),
                       "to_buy": sum(1 for row in materials if row["to_buy"] > 0), "to_make": sum(1 for row in semis if row["make"] > 0)}}


# ---- the two actions ----

def _typed(raw, default):
    """A typed quantity: the default when empty, None when it is not a number."""

    if raw in (None, ""):
        return default
    from .services import _decimal

    return _decimal(raw)


@transaction.atomic
def make_purchase_orders(picks, user, lang="ar"):
    """«اعمل أمر شراء»: one DRAFT purchase order per supplier for the ticked materials, through the unchanged
    ``orders.purchasing.create_order`` and ``add_line``. Posts nothing.

    ``picks``: [{"item": pk, "quantity": typed or "", "supplier": Supplier or None}]. The figures come from a fresh
    calculation, never from the page: an item that is no longer short is refused. Returns [(PurchaseOrder, [(item, quantity, price)])]."""

    words = MESSAGES[lang]
    if not picks:
        raise ValidationError(words["nothing_to_buy"])
    short = {row["item"].pk: row for row in calculate()["materials"] if row["to_buy"] > 0}
    by_supplier, wanted = {}, []
    for pick in picks:
        row = short.get(pick["item"])
        if row is None:
            item = Item.objects.filter(pk=pick["item"]).first()
            raise ValidationError(words["gone"].format(item=item.item_name if item else "—"))
        name = row["item"].item_name
        quantity = _typed(pick.get("quantity"), row["to_buy"])
        if quantity is None or quantity <= 0:
            raise ValidationError(words["quantity"].format(item=name))
        supplier = pick.get("supplier") or row["supplier"]
        if supplier is None:
            raise ValidationError(words["supplier"].format(item=name))
        wanted.append((supplier, row, quantity))
    for supplier, row, quantity in wanted:
        by_supplier.setdefault(supplier.pk, (supplier, []))[1].append((row, quantity))
    made = []
    for supplier, rows in by_supplier.values():
        order = po.create_order({"supplier": supplier, "notes": words["note"]}, user, lang)
        added = []
        for row, quantity in rows:
            line = po.add_line(order, row["item"], user, quantity=quantity, unit_price=row["last_price"], lang=lang)
            added.append((row["item"], line.quantity, line.unit_price))
        made.append((order, added))
    return made


def places():
    """The active locations of the entity being worked in, the default first."""

    return entity_scope.locations(Location.objects).filter(active=True).order_by("-is_default", "location_code")


@transaction.atomic
def make_production_order(item_pk, user, data, lang="ar"):
    """«اعمل أمر إنتاج» for a semi-finished row: a planned production order through the unchanged
    ``manufacturing.orders.create_order``. Posts nothing. The quantity is the whole batches the calculation asks for unless typed."""

    words = MESSAGES[lang]
    row = next((r for r in calculate()["semis"] if r["item"].pk == item_pk and r["make"] > 0), None)
    if row is None:
        item = Item.objects.filter(pk=item_pk).first()
        raise ValidationError(words["make_gone"].format(item=item.item_name if item else "—"))
    available = places()
    raw = data.get("location")
    location = available.filter(pk=raw).first() if raw not in (None, "") else available.first()
    if location is None:
        raise ValidationError(words["place"] if raw not in (None, "") else words["no_place"])
    return production.create_order({"recipe": row["recipe"], "location": location, "quantity": _typed(data.get("quantity"), row["make"]),
                                    "labor_cost": data.get("labor_cost"), "overhead_cost": data.get("overhead_cost"), "notes": words["note"]}, user, lang)


def plain(number):
    """For an input value: no grouping commas, no trailing zeros."""

    return f"{number.normalize():f}"


def text(number):
    return fmt_qty(number)
