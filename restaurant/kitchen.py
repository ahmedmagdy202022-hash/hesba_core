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

MESSAGES = {
    "ar": {"manage": "تحويل الأطباق محتاج صلاحية إدارة الأصناف، وتحضيرها محتاج كمان صلاحية تسوية المخزون.", "no_ingredients": "ضيف مكونات الطبق الأول.",
           "already": "الطبق ده بيتحضّر في المطبخ بالفعل.", "not_kitchen": "الطبق ده مش متحضّر في المطبخ؛ حوّله الأول.",
           "quantity": "الكمية لازم تكون رقم أكبر من صفر ولحد 10000.", "setup": "لازم يكون فيه مخزن بيع نشط.",
           "short": "مينفعش نحضّر «{dish}»: {detail}", "recipe": "طبق {dish}", "prepared": "تحضير «{dish}»",
           "not_ready": "«{dish}» مش جاهز: المطلوب {need} والجاهز {have}. المطبخ يحضّره من صفحة الطبق الأول."},
    "en": {"manage": "Switching a dish needs the manage-items permission; preparing it also needs the adjust-stock permission.", "no_ingredients": "Add the dish's ingredients first.",
           "already": "This dish is already made in the kitchen.", "not_kitchen": "This dish is not made in the kitchen; switch it first.",
           "quantity": "The quantity must be a number above zero and up to 10000.", "setup": "An active selling location is needed.",
           "short": "“{dish}” cannot be prepared: {detail}", "recipe": "Dish {dish}", "prepared": "Preparing “{dish}”",
           "not_ready": "“{dish}” is not ready: {need} needed, {have} ready. The kitchen prepares it from the dish's page first."},
}
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


def _produce(dish, quantity, location, user, lang, notes=""):
    from manufacturing.services import produce

    words = _words(lang)
    recipe = _sync_recipe(dish, user, lang)  # the ingredients as they are now
    try:
        return produce(recipe, user, batches=quantity, location=location, run_date=timezone.localdate(),
                       notes=(notes or words["prepared"].format(dish=dish.item_name))[:255], lang=lang)
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


def prepare_shortfall(lines, location, user, lang="ar"):
    """Before an order is paid: make the plates of kitchen dishes that are not in stock yet. Runs inside the payment's transaction.

    ``lines``: the order lines (item, quantity). Returns the production runs made."""

    from inventory.services import get_item_location_stock_quantity
    from settings_core.templatetags.hesba_format import qty

    words = _words(lang)
    # Making plates moves stock, which needs inventory.adjust_stock (the stock engine's own rule, unchanged). Someone without it
    # (a cashier) sells what the kitchen prepared ahead; anything not ready stops the bill with a clear message.
    may_make = user_has_permission(user, "inventory.adjust_stock")
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
        runs.append(_produce(item, missing, location, user, lang))
    return runs
