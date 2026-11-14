"""RESTO-004 (HG-044, design C1): a dish made in the kitchen takes its ingredients out of stock.

Opt-in per dish. A dish switched to "made in the kitchen" becomes a stock item
with a manufacturing recipe (``DISH-<pk>``) built from its ingredients
(``DishIngredient``). Producing it runs the manufacturing engine unchanged: the
ingredients go out of stock at their cost and the dish comes in at that cost,
so selling it costs it like any stock item (HG-003). Sales posting is not
touched.

A dish is produced before it is sold:
- ahead of time, from the dish's page ("prepare a quantity"), for the day's
  batch; or
- at payment, automatically, when the person paying may adjust stock (the
  stock engine's own permission, unchanged): the missing plates are made from
  their ingredients first. When an ingredient is short, the payment is refused
  and says which one, so a sale never fails half-way for lack of stock. Someone
  without that permission (a cashier) sells what the kitchen prepared ahead,
  and a dish not ready stops the bill with a clear message.

A dish already sold as a service is not switched in place. The sales, returns
and cancellations engine decides whether to move stock by the item's flag at
the time, so returning an old invoice of a dish switched in place would bring
stock in that never went out. Instead, such a dish gets a new stock item with
the same name, price, category, tax and ingredients; it takes the old one's
place on the menu, and the old one is kept, inactive, for its invoices.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from master_data.models import Item
from permissions.services import user_has_permission

RECIPE_PREFIX = "DISH-"
MANAGE = "master_data.manage_items"
PREPARE_AT_PAYMENT = "restaurant.prepare_at_payment"  # HG-048

MESSAGES = {
    "ar": {"manage": "تحويل الأطباق محتاج صلاحية إدارة الأصناف، وتحضيرها محتاج كمان صلاحية تسوية المخزون.", "no_ingredients": "ضيف مكونات الطبق الأول.",
           "already": "الطبق ده بيتحضّر في المطبخ بالفعل.", "not_kitchen": "الطبق ده مش متحضّر في المطبخ؛ حوّله الأول.",
           "quantity": "الكمية لازم تكون رقم أكبر من صفر ولحد 10000.", "setup": "لازم يكون فيه مخزن بيع نشط.",
           "short": "مينفعش نحضّر «{dish}»: {detail}", "recipe": "طبق {dish}", "prepared": "تحضير «{dish}»",
           "not_ready": "«{dish}» مش جاهز: المطلوب {need} والجاهز {have}. المطبخ يحضّره من صفحة الطبق الأول.",
           "cancel_manage": "إلغاء التحضير محتاج صلاحية إدارة الأصناف وصلاحية تسوية المخزون.",
           "not_preparation": "التحضير ده مش من تحضيرات الطبق ده.",
           "cannot": "مينفعش يتلغي: في أطباق من «{dish}» خرجت من المخزن بعد التحضير ده (بيع أو تسوية أو تحويل). لو الرصيد محتاج تصحيح، اعمل تسوية مخزون عادية."},
    "en": {"manage": "Switching a dish needs the manage-items permission; preparing it also needs the adjust-stock permission.", "no_ingredients": "Add the dish's ingredients first.",
           "already": "This dish is already made in the kitchen.", "not_kitchen": "This dish is not made in the kitchen; switch it first.",
           "quantity": "The quantity must be a number above zero and up to 10000.", "setup": "An active selling location is needed.",
           "short": "“{dish}” cannot be prepared: {detail}", "recipe": "Dish {dish}", "prepared": "Preparing “{dish}”",
           "not_ready": "“{dish}” is not ready: {need} needed, {have} ready. The kitchen prepares it from the dish's page first.",
           "cancel_manage": "Cancelling a preparation needs the manage-items and the adjust-stock permissions.",
           "not_preparation": "This is not one of this dish's preparations.",
           "cannot": "Cannot be cancelled: some “{dish}” left stock after this preparation (a sale, an adjustment or a transfer). To correct the stock, record an ordinary stock adjustment."},
}
CANCEL_RUN = "manufacturing.cancel_run"  # FB-27: the same held action as the run's own page (HG-107)
RECENT = 10  # preparations listed on the dish's page
MAX_QUANTITY = Decimal("10000")


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def recipe_of(dish):
    from manufacturing.models import Recipe

    return Recipe.objects.filter(product=dish, code__startswith=RECIPE_PREFIX, active=True).first()


def is_kitchen_made(dish):
    return bool(dish.is_stock_tracked and recipe_of(dish) is not None)


def kitchen_dish_ids(items=None):
    """The pks of the dishes made in the kitchen (among ``items`` when given)."""

    from manufacturing.models import Recipe

    rows = Recipe.objects.filter(code__startswith=RECIPE_PREFIX, active=True, product__is_stock_tracked=True)
    if items is not None:
        rows = rows.filter(product__in=items)
    return set(rows.values_list("product_id", flat=True))


def has_history(item):
    """Whether any document or stock movement names the item: then its stock flag must not change."""

    from inventory.models import StockMovement
    from purchases.models import PurchaseLine
    from sales.models import SalesLine

    return SalesLine.objects.filter(item=item).exists() or PurchaseLine.objects.filter(item=item).exists() or StockMovement.objects.filter(item=item).exists()


def _audit(user, action, item, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="restaurant", action=action, object_type="master_data.Item", object_id=str(item.pk),
                            before_data=before or {}, after_data=after)


def _sync_recipe(dish, user, lang="ar"):
    """The dish's recipe, one plate a batch, with exactly its ingredients now."""

    from manufacturing.models import Recipe, RecipeLine

    code = f"{RECIPE_PREFIX}{dish.pk}"
    recipe, _ = Recipe.objects.get_or_create(code=code, defaults={"product": dish, "name": _words(lang)["recipe"].format(dish=dish.item_name)[:255],
                                                                  "output_quantity": Decimal("1"), "created_by": user})
    if not recipe.active or recipe.product_id != dish.pk or recipe.output_quantity != 1:
        recipe.active, recipe.product, recipe.output_quantity = True, dish, Decimal("1")
        recipe.save(update_fields=["active", "product", "output_quantity"])
    wanted = {row.ingredient_id: row.quantity for row in dish.dish_ingredients.all()}
    current = {line.component_id: line for line in recipe.lines.all()}
    for component, line in current.items():
        if component not in wanted:
            line.delete()
        elif line.quantity != wanted[component]:
            line.quantity = wanted[component]
            line.save(update_fields=["quantity"])
    RecipeLine.objects.bulk_create([RecipeLine(recipe=recipe, component_id=component, quantity=quantity)
                                    for component, quantity in wanted.items() if component not in current])
    return recipe


def _free_code(base):
    code, n = f"{base}-K"[:80], 2
    while Item.objects.filter(item_code=code).exists():
        code, n = f"{base}-K{n}"[:80], n + 1
    return code


def _copy_as_stock_item(dish):
    """A stock item in the dish's place: same name, price, category, tax, e-invoice code and ingredients. The old dish goes inactive."""

    from taxes.models import ItemTaxRate

    from .models import DishIngredient

    new = Item.objects.create(item_code=_free_code(dish.item_code), barcode=dish.barcode, item_name=dish.item_name, category=dish.category,
                              size=dish.size, color=dish.color, unit=dish.unit, default_sale_price=dish.default_sale_price,
                              default_purchase_price=dish.default_purchase_price, is_stock_tracked=True, active=True)
    link = ItemTaxRate.objects.filter(item=dish).first()
    if link is not None:
        ItemTaxRate.objects.create(item=new, tax_rate=link.tax_rate)
    from einvoice.models import ItemCode

    code = ItemCode.objects.filter(item=dish).first()
    if code is not None:
        ItemCode.objects.create(item=new, code_type=code.code_type, code=code.code, unit_type=code.unit_type)
    DishIngredient.objects.filter(dish=dish).update(dish=new)
    dish.active = False
    dish.barcode = ""  # the scanner now finds the new item
    dish.save(update_fields=["active", "barcode"])
    return new


@transaction.atomic
def make_in_kitchen(dish, user, lang="ar"):
    """Switch a dish to "made in the kitchen". Returns the dish that is now on the menu (the same one, or its stock copy)."""

    words = _words(lang)
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied(words["manage"])
    dish = Item.objects.select_for_update().get(pk=dish.pk)
    if is_kitchen_made(dish):
        raise ValidationError(words["already"])
    if not dish.dish_ingredients.exists():
        raise ValidationError(words["no_ingredients"])
    before = {"item": dish.item_code, "stock_tracked": dish.is_stock_tracked}
    if dish.is_stock_tracked:
        target = dish  # already a stock item: it only gets its recipe
    elif has_history(dish):
        target = _copy_as_stock_item(dish)  # its old invoices keep the old item, never stocked
    else:
        dish.is_stock_tracked = True
        dish.save(update_fields=["is_stock_tracked"])
        target = dish
    recipe = _sync_recipe(target, user, lang)
    _audit(user, "make_dish_in_kitchen", target, {"item": target.item_code, "recipe": recipe.code, "replaces": dish.item_code if target != dish else ""},
           before)
    return target


def _location():
    from entities import scope as entity_scope
    from master_data.models import Location

    return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


def _quantity(raw, words):
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except Exception:
        raise ValidationError(words["quantity"])
    if not value.is_finite() or value <= 0 or value > MAX_QUANTITY:
        raise ValidationError(words["quantity"])
    value = value.quantize(Decimal("0.001"))
    if value <= 0:
        raise ValidationError(words["quantity"])
    return value


def _produce(dish, quantity, location, user, lang, notes="", stand_in=None):
    from manufacturing.services import produce

    words = _words(lang)
    recipe = _sync_recipe(dish, user, lang)  # the ingredients as they are now
    try:
        return produce(recipe, user, batches=quantity, location=location, run_date=timezone.localdate(),
                       notes=(notes or words["prepared"].format(dish=dish.item_name))[:255], lang=lang, stand_in=stand_in)
    except ValidationError as exc:
        raise ValidationError(words["short"].format(dish=dish.item_name, detail=" ".join(exc.messages)))


@transaction.atomic
def prepare(dish, raw_quantity, user, lang="ar", location=None):
    """Make ``raw_quantity`` plates ahead of time (the day's batch). Returns the production run."""

    words = _words(lang)
    if not (user_has_permission(user, MANAGE) and user_has_permission(user, "inventory.adjust_stock")):
        raise PermissionDenied(words["manage"])
    if not is_kitchen_made(dish):
        raise ValidationError(words["not_kitchen"])
    location = location or _location()
    if location is None:
        raise ValidationError(words["setup"])
    return _produce(dish, _quantity(raw_quantity, words), location, user, lang)


# ---- HG-107: «إلغاء التحضير», a preparation cancelled whole from the dish's page ---------------------------------
#
# A preparation is an ordinary production run, and a restaurant has the manufacturing module off, so the run's own
# page is closed. The dish's page lists the dish's recent preparations and cancels one through
# ``manufacturing.services.cancel_run``, unchanged: the plates leave stock and every ingredient comes back at the cost
# it left at, and once some plates have left since (HG-100's rule) it is refused.


def _recipe_code(dish_id):
    return f"{RECIPE_PREFIX}{dish_id}"


def _scoped_runs():
    """Production runs in the entity being worked in (the run page's own scope)."""

    from entities import scope as entity_scope
    from manufacturing.models import ProductionRun

    return entity_scope.scope(ProductionRun.objects, entity_scope.PRODUCTION_RUN)


def preparations(dish, limit=RECENT):
    """The dish's recent preparations here, newest first."""

    return list(_scoped_runs().filter(recipe__code=_recipe_code(dish.pk), recipe__product=dish)
                .select_related("recipe__product", "output_operation", "created_by").order_by("-pk")[:limit])


def preparation_of(dish, raw_run_id):
    """One of the dish's preparations here, by a pk as a form sends it, or None."""

    raw = str(raw_run_id or "").strip()
    if not raw.isdigit():
        return None
    return (_scoped_runs().filter(pk=int(raw), recipe__code=_recipe_code(dish.pk), recipe__product=dish)
            .select_related("recipe__product", "output_operation").first())


def on_menu(dish):
    """Whether the dish's page lists it (``restaurant.views._dishes``): an active dish made in the kitchen."""

    return bool(dish.active and is_kitchen_made(dish))


def preparation_dishes(run_ids):
    """{run pk: dish} for those runs that are preparations of a kitchen dish still on the menu (no entity filter: the
    caller has already found the run in its own scope)."""

    from manufacturing.models import ProductionRun

    runs = (ProductionRun.objects.filter(pk__in=[int(pk) for pk in run_ids], recipe__code__startswith=RECIPE_PREFIX, recipe__active=True,
                                         recipe__product__active=True, recipe__product__is_stock_tracked=True)
            .select_related("recipe__product"))
    return {run.pk: run.recipe.product for run in runs if run.recipe.code == _recipe_code(run.recipe.product_id)}


def dish_page(dish, run_id=None):
    """The dish's page; with ``run_id``, the same page with that preparation listed first, however old it is (the
    list holds the last ``RECENT`` only, and the stock screen sends a person here to cancel a particular one)."""

    from django.urls import reverse

    if run_id is None:
        return reverse("restaurant:dish", args=[dish.pk])
    return reverse("restaurant:dish_preparation", args=[dish.pk, int(run_id)])


def dish_pages_open():
    """Whether the dish's page can open here (the module gate, ``settings_core.module_gate``)."""

    from django.urls import reverse

    from settings_core.module_gate import closed_module

    return closed_module(reverse("restaurant:costing")) is None


def may_cancel(user):
    """The two permissions ``prepare`` asks; never the payment's stand-in (HG-048)."""

    return user_has_permission(user, MANAGE) and user_has_permission(user, "inventory.adjust_stock")


def why_not(run, lang="ar"):
    """Why a posted preparation cannot be cancelled now (HG-100's rule), or "" when it can."""

    from manufacturing.models import RunStatus
    from manufacturing.services import check_cancellable

    if run.status != RunStatus.POSTED:
        return ""
    try:
        check_cancellable(run, "-", lang)
    except ValidationError:
        return _words(lang)["cannot"].format(dish=run.recipe.product.item_name)
    return ""


def checked_preparation(run, user, lang="ar", dish=None):
    """The run, found again here, when ``user`` may cancel it and it is a preparation of ``dish`` (of its own dish when
    none is said) in the entity being worked in. Refuses otherwise; nothing is read from the request but the run."""

    words = _words(lang)
    if not may_cancel(user):
        raise PermissionDenied(words["cancel_manage"])
    run_id = str(getattr(run, "pk", run) or "")
    if dish is None and run_id.isdigit():
        dish = preparation_dishes([run_id]).get(int(run_id))
    found = preparation_of(dish, run_id) if dish is not None and on_menu(dish) else None
    if found is None:
        raise ValidationError(words["not_preparation"])
    return found


@transaction.atomic
def cancel_preparation(run, user, reason, lang="ar", dish=None):
    """Cancel a preparation whole: its plates leave stock and every ingredient comes back, through ``cancel_run``
    unchanged (which refuses once some plates have left since, HG-100). Returns the run."""

    from manufacturing.services import cancel_run

    found = checked_preparation(run, user, lang, dish)
    return cancel_run(found, user, reason=reason, lang=lang)


def prepare_shortfall(lines, location, user, lang="ar"):
    """Before an order is paid: make the plates of kitchen dishes that are not in stock yet. Runs inside the payment's transaction.

    ``lines``: the order lines (item, quantity). Returns the production runs made."""

    from inventory.services import get_item_location_stock_quantity
    from settings_core.templatetags.hesba_format import qty

    words = _words(lang)
    # Making plates moves stock, which needs inventory.adjust_stock, or (HG-048) restaurant.prepare_at_payment, which the stock
    # engine accepts in its place for this flow only. Someone with neither sells what the kitchen prepared ahead; anything not
    # ready stops the bill with a clear message.
    stand_in = PREPARE_AT_PAYMENT if not user_has_permission(user, "inventory.adjust_stock") else None
    may_make = stand_in is None or user_has_permission(user, PREPARE_AT_PAYMENT)
    need = {}
    for line in lines:
        if line.item.is_stock_tracked:
            need[line.item_id] = need.get(line.item_id, Decimal("0")) + Decimal(line.quantity)
    kitchen = kitchen_dish_ids(Item.objects.filter(pk__in=need))
    runs = []
    for item in Item.objects.filter(pk__in=kitchen).order_by("pk"):
        have = get_item_location_stock_quantity(item, location)
        missing = (need[item.pk] - have).quantize(Decimal("0.001"))
        if missing <= 0:
            continue
        if not may_make:
            raise ValidationError(words["not_ready"].format(dish=item.item_name, need=qty(need[item.pk]), have=qty(have)))
        runs.append(_produce(item, missing, location, user, lang, stand_in=stand_in))
    return runs
