"""Manufacturing F9 (HG-139): by-products and co-products from one run.

Rules, stated once:

* **Joint cost** of a run = its materials (issues + extras − returns for an order) + its labour and overhead.
* A **by-product** (bran from milling, sawdust, offcuts sold by weight) enters stock at the fixed value per unit the
  recipe gives it. That value comes off the joint cost, and a by-product carries no share of the labour and overhead.
* **Co-products** share what is left with the recipe's product: each takes its percent, the product takes the rest;
  when no co-product of the recipe has a percent, the split is by sale value (quantity x the item's default sale
  price, the product's own included) on the day of the run. A recipe mixes neither: every co-product has a percent, or
  none has (owner question 18).
* The recipe's product (``Recipe.product``) is the main output. Its cost is the joint cost, less the by-products, less
  what the co-products took, over its good units, kept to four decimals as ever.
* Every extra output enters the run's location through the engine's own ``adjust_stock`` in, at its cost over its
  quantity, linked by ``RunOutput``. It never enters the quality hold place: a quality release (F5) checks the recipe's
  product only, so a recipe that needs release takes by-products (they go straight to the order's location) and
  refuses co-products.
* **Scrap with a sale value is a by-product, never also scrap** (trap 6). A run's scrap is counted in units of the
  product that never entered stock and carries no value; a by-product is another stock item with a value. An extra
  output can be neither the recipe's product nor one of its components, so the same units are never both, and each
  by-product's value comes off the joint cost exactly once.
* Cancelling a run reverses every output first (``services.cancel_run``, each one only while none of it has left its
  place since, F11's rule), then the materials.

A recipe with no extra output is not touched by any of this: ``split`` returns None and ``services.produce`` runs the
lines it always ran. Nothing here changes the inventory engine, average cost, or a posting service.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.models import AuditEventType
from config.money import cost_round, money_round
from permissions.services import user_has_permission
from settings_core.templatetags.hesba_format import money as fmt_money

from .models import OutputKind, RecipeOutput, RunOutput

ZERO = Decimal("0")
HUNDRED = Decimal("100")
RECIPES, COST = "master_data.manage_items", "inventory.view_cost"
OUTPUT_ROWS = 6

MESSAGES = {
    "ar": {
        "permission": "تعديل المنتجات الثانوية والمشتركة محتاج صلاحية إدارة الأصناف وصلاحية رؤية التكلفة، عشان القيمة والنسبة بيحددوا تكلفة المخزون.",
        "item_stock": "الصنف «{item}» لازم يكون متتبع في المخزون.",
        "product": "المنتج الأساسي للوصفة مينفعش يتكتب كمان منتج ثانوي أو مشترك.",
        "component": "الصنف «{item}» مكوّن في الوصفة نفسها؛ اللي بيفضل منه بيرجع المخزن من «رجّع خامات» على أمر الإنتاج.",
        "twice": "الصنف «{item}» متكرر؛ اكتبه في سطر واحد.",
        "qty": "كمية «{item}» لكل دفعة لازم تكون رقم أكبر من صفر.",
        "kind": "اختار نوع «{item}»: منتج ثانوي أو منتج مشترك.",
        "value": "اكتب قيمة الوحدة من «{item}» برقم أكبر من صفر؛ دي القيمة اللي المنتج الثانوي بيدخل بيها المخزن.",
        "percent": "نسبة «{item}» لازم تكون رقم أكبر من صفر وأقل من 100، أو سيبها فاضية.",
        "percent_mixed": "اكتب نسبة لكل منتج مشترك، أو سيبهم كلهم فاضيين عشان التقسيم يبقى بقيمة البيع.",
        "percent_total": "نسب المنتجات المشتركة مجموعها {total}%، ولازم يفضل للمنتج الأساسي نصيب؛ خلّي المجموع أقل من 100.",
        "release": "الوصفة دي محتاجة إفراج جودة، والفحص بيخص المنتج الأساسي بس؛ عشان كده المنتج المشترك مينفعش يتضاف عليها.",
        "release_on": "الوصفة دي فيها منتج مشترك، والفحص بيخص المنتج الأساسي بس؛ شيل المنتج المشترك الأول أو سيب الوصفة من غير إفراج.",
        "out_qty": "كمية «{item}» اللي طلعت لازم تكون رقم مش سالب.",
        "by_over": "قيمة المنتجات الثانوية {value} وتكلفة التشغيلة كلها {joint}، فمفيش تكلفة تفضل للمنتج الأساسي؛ راجع قيمة الوحدة في الوصفة.",
        "sale_price": "التقسيم بقيمة البيع محتاج سعر بيع للصنف «{item}»؛ اكتب سعره أو حدد نسبة في الوصفة.",
    },
    "en": {
        "permission": "Changing by-products and co-products needs the items permission and the permission to see costs: the value and the percent set the stock cost.",
        "item_stock": "“{item}” must be a stock-tracked item.",
        "product": "The recipe's own product cannot also be a by-product or a co-product.",
        "component": "“{item}” is a component of this recipe; what is left of it goes back to stock with “Return materials” on the production order.",
        "twice": "“{item}” is listed twice; put it on one line.",
        "qty": "The quantity of “{item}” per batch must be a number above zero.",
        "kind": "Choose what “{item}” is: a by-product or a co-product.",
        "value": "Enter the value per unit of “{item}” as a number above zero; a by-product enters stock at that value.",
        "percent": "The percent of “{item}” must be a number above zero and below 100, or left empty.",
        "percent_mixed": "Give every co-product a percent, or leave them all empty so the split is by sale value.",
        "percent_total": "The co-products' percents add up to {total}%, and the main product must keep a share; make the total less than 100.",
        "release": "This recipe needs a quality release, and the check covers the main product only; so a co-product cannot be added to it.",
        "release_on": "This recipe has a co-product, and the check covers the main product only; remove the co-product first or leave the recipe without release.",
        "out_qty": "The quantity of “{item}” that came out must be a number, not negative.",
        "by_over": "The by-products are worth {value} and the whole run cost {joint}, so no cost is left for the main product; check the value per unit on the recipe.",
        "sale_price": "A split by sale value needs a sale price for “{item}”; enter its price or set a percent on the recipe.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


# ---- the recipe's extra outputs ----

def clean_outputs(recipe, rows, lang="ar"):
    """The rows of the recipe page's form as [{item, quantity, kind, value_per_unit, share_percent}], or a refusal.
    A row with no item is skipped."""

    from .services import _decimal

    words = _words(lang)
    components = set(recipe.lines.values_list("component_id", flat=True))
    clean, seen = [], set()
    for row in rows:
        item = row.get("item")
        if item is None:
            continue
        name = item.item_name
        if not item.is_stock_tracked:
            raise ValidationError(words["item_stock"].format(item=name))
        if item.pk == recipe.product_id:
            raise ValidationError(words["product"])
        if item.pk in components:
            raise ValidationError(words["component"].format(item=name))
        if item.pk in seen:
            raise ValidationError(words["twice"].format(item=name))
        seen.add(item.pk)
        quantity = _decimal(row.get("quantity"))
        if quantity is None or quantity <= 0:
            raise ValidationError(words["qty"].format(item=name))
        kind = row.get("kind")
        if kind not in OutputKind.values:
            raise ValidationError(words["kind"].format(item=name))
        value, percent = None, None
        if kind == OutputKind.BY_PRODUCT:
            value = _number(row.get("value_per_unit"))
            if value is None or cost_round(value) <= 0:
                raise ValidationError(words["value"].format(item=name))
            value = cost_round(value)
        elif str(row.get("share_percent") or "").strip():
            percent = _number(row.get("share_percent"))
            if percent is None or percent.quantize(Decimal("0.01")) <= 0 or percent.quantize(Decimal("0.01")) >= HUNDRED:
                raise ValidationError(words["percent"].format(item=name))
            percent = percent.quantize(Decimal("0.01"))
        clean.append({"item": item, "quantity": quantity, "kind": kind, "value_per_unit": value, "share_percent": percent})
    shares = [row["share_percent"] for row in clean if row["kind"] == OutputKind.CO_PRODUCT]
    if shares and any(share is None for share in shares) and any(share is not None for share in shares):
        raise ValidationError(words["percent_mixed"])
    total = sum((share for share in shares if share is not None), ZERO)
    if total >= HUNDRED:
        raise ValidationError(words["percent_total"].format(total=f"{total.normalize():f}"))
    if shares and recipe.needs_release:
        raise ValidationError(words["release"])
    return clean


def _number(raw):
    """A typed value or percent: None when it is not a finite number (kept at full precision; the caller rounds)."""

    from decimal import InvalidOperation

    try:
        number = Decimal(str(raw if raw is not None else "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


@transaction.atomic
def save_outputs(recipe, user, rows, lang="ar"):
    """The recipe page's section for extra outputs: the whole list is replaced. Needs the items permission (it is part
    of the recipe) and the permission to see costs (a value or a percent sets a stock cost)."""

    from . import services
    from .models import Recipe

    if not (user_has_permission(user, RECIPES) and user_has_permission(user, COST)):
        raise PermissionDenied(_words(lang)["permission"])
    recipe = Recipe.objects.select_for_update().get(pk=recipe.pk)
    if recipe.orders.filter(status="in_progress", issues__isnull=False).exists():
        # As for the recipe itself (HG-036): an order on the floor finishes with the outputs it started with.
        raise ValidationError(services.MESSAGES[lang]["on_floor"])
    clean = clean_outputs(recipe, rows, lang)
    recipe.outputs.all().delete()
    RecipeOutput.objects.bulk_create([RecipeOutput(recipe=recipe, **row) for row in clean])
    services._audit(recipe, user, "save_recipe_outputs", {"outputs": [[row["item"].item_code, row["kind"], str(row["quantity"]),
                                                                    str(row["value_per_unit"] or ""), str(row["share_percent"] or "")] for row in clean]})
    return recipe


def check_recipe(recipe, product, components, lang="ar"):
    """What ``services.save_recipe`` asks before it changes a recipe that has extra outputs: none of them may become the
    product or a component."""

    words = _words(lang)
    wanted = {component.pk for component, _quantity in components}
    for output in recipe.outputs.select_related("item"):
        if output.item_id == product.pk:
            raise ValidationError(words["product"])
        if output.item_id in wanted:
            raise ValidationError(words["component"].format(item=output.item.item_name))


def check_release(recipe, needs_release, lang="ar"):
    """What the recipe's quality section asks before it switches release on: a quality check covers the recipe's product
    only, so a recipe with a co-product (goods for sale that no check would hold) cannot need release."""

    if needs_release and recipe.outputs.filter(kind=OutputKind.CO_PRODUCT).exists():
        raise ValidationError(_words(lang)["release_on"])


# ---- the split of one run's joint cost ----

@dataclass
class Share:
    """One extra output of a run: what enters stock and what it takes of the joint cost."""

    output: RecipeOutput
    quantity: Decimal
    cost: Decimal          # to the piastre: a by-product's quantity x value, a co-product's share of what was left
    unit_cost: Decimal     # four decimals: what its stock operation carries
    conversion: Decimal    # the part of the run's labour and overhead inside ``cost`` (0 for a by-product)
    percent: Decimal = None  # a co-product's share of what was left, in percent (as set, or worked out from sale values)

    @property
    def item(self):
        return self.output.item

    @property
    def kind(self):
        return self.output.kind

    @property
    def value(self):
        """What it is worth in stock: quantity x the four-decimal unit cost, to the piastre (as the ledger reads it)."""

        return money_round(self.quantity * self.unit_cost)


@dataclass
class Split:
    joint: Decimal         # materials + labour and overhead
    deducted: Decimal      # the by-products' value, taken off the joint cost
    main_cost: Decimal     # what is left for the recipe's product
    main_unit_cost: Decimal
    main_conversion: Decimal
    main_percent: Decimal = None  # the product's share of what was left after the by-products, when there are co-products
    by_sale_value: bool = False
    rows: list = field(default_factory=list)

    @property
    def extras_cost(self):
        return sum((row.cost for row in self.rows), ZERO)


def planned_quantity(output, batches):
    return (output.quantity * batches).quantize(Decimal("0.001"))


def split(recipe, batches, materials, conversion, output, quantities=None, lang="ar", outputs=None):
    """How a run of ``recipe`` shares its joint cost; None when the recipe has no extra output (or none came out).

    ``materials`` and ``conversion`` are the run's own figures and ``output`` the good units of the product.
    ``quantities`` ({RecipeOutput pk: quantity}) says what really came out of each extra output when the person typed
    it; without an entry it is the recipe's quantity x ``batches``. An output that did not come out (0) is left out."""

    from .services import _decimal

    words = _words(lang)
    outputs = list(recipe.outputs.select_related("item")) if outputs is None else outputs
    if not outputs:
        return None
    present = []
    for row in outputs:
        raw = (quantities or {}).get(row.pk)
        quantity = planned_quantity(row, batches) if raw in (None, "") else _decimal(raw)
        if quantity is None or quantity < 0:
            raise ValidationError(words["out_qty"].format(item=row.item.item_name))
        if quantity > 0:
            present.append((row, quantity))
    if not present:
        return None
    materials, conversion = Decimal(materials), Decimal(conversion or 0)
    joint = materials + conversion
    shares, deducted = [], ZERO
    for row, quantity in present:
        if row.kind == OutputKind.BY_PRODUCT:
            share = Share(row, quantity, money_round(quantity * row.value_per_unit), cost_round(row.value_per_unit), ZERO)
            deducted += share.cost
            shares.append(share)
    left = joint - deducted
    if left <= 0:
        raise ValidationError(words["by_over"].format(value=fmt_money(deducted), joint=fmt_money(money_round(joint))))
    co = [(row, quantity) for row, quantity in present if row.kind == OutputKind.CO_PRODUCT]
    by_sale_value = bool(co) and all(row.share_percent is None for row, _quantity in co)
    main_percent = None
    if co:
        if by_sale_value:
            priced = [(recipe.product, output)] + [(row.item, quantity) for row, quantity in co]
            for item, _quantity in priced:
                if not item.default_sale_price or item.default_sale_price <= 0:
                    raise ValidationError(words["sale_price"].format(item=item.item_name))
            total = sum((quantity * item.default_sale_price for item, quantity in priced), ZERO)
            parts = [quantity * row.item.default_sale_price / total for row, quantity in co]
        else:
            parts = [(row.share_percent or ZERO) / HUNDRED for row, _quantity in co]
        for (row, quantity), part in zip(co, parts):
            cost = money_round(left * part)
            shares.append(Share(row, quantity, cost, cost_round(cost / quantity), money_round(conversion * part),
                                (part * HUNDRED).quantize(Decimal("0.01"))))
        main_percent = ((1 - sum(parts, ZERO)) * HUNDRED).quantize(Decimal("0.01"))
    order = {row.pk: index for index, row in enumerate(outputs)}
    shares.sort(key=lambda share: order[share.output.pk])
    main_cost = left - sum((share.cost for share in shares if share.kind == OutputKind.CO_PRODUCT), ZERO)
    return Split(joint=joint, deducted=deducted, main_cost=main_cost, main_unit_cost=cost_round(main_cost / output),
                 main_conversion=conversion - sum((share.conversion for share in shares), ZERO), main_percent=main_percent,
                 by_sale_value=by_sale_value, rows=shares)


def estimate(recipe, batches, materials, conversion, output, lang="ar", outputs=None):
    """``split`` for a screen that only reads: (the split or None, why it cannot be worked out or "")."""

    if not output or output <= 0:
        return None, ""
    try:
        return split(recipe, batches, materials, conversion, output, lang=lang, outputs=outputs), ""
    except ValidationError as exc:
        return None, " ".join(exc.messages)


def post(run, shared, location, run_date, reason, user, stand_in=None):
    """Put each extra output of ``shared`` into stock at the run's location and link it to ``run``. Called by
    ``services.produce`` inside its transaction, right after the product itself entered stock."""

    from inventory.models import StockAdjustmentDirection
    from inventory.services import adjust_stock

    from . import services

    made = []
    for index, share in enumerate(shared.rows, start=2):
        operation = adjust_stock(f"{run.number}-OUT{index:02d}", run_date, share.item, location, StockAdjustmentDirection.IN, share.quantity, reason, user,
                                 unit_cost=share.unit_cost, stand_in=stand_in)
        made.append(RunOutput.objects.create(run=run, item=share.item, kind=share.kind, operation=operation, quantity=share.quantity,
                                             cost=share.cost, conversion_share=share.conversion))
    services._audit(run, user, "produce_outputs", {"outputs": [[row.item.item_code, row.kind, str(row.quantity), str(row.cost), str(row.conversion_share)]
                                                                for row in made]}, AuditEventType.CREATE)
    return made


# ---- what the pages read ----

def run_outputs(run):
    """The run's extra outputs with their stock operation and item."""

    return list(run.outputs.select_related("operation", "item"))


def extras_cost(run):
    """What the run's extra outputs took of its joint cost."""

    return sum((row.cost for row in run.outputs.all()), ZERO)


def main_materials_per_unit(recipe, batch_materials, outputs=None):
    """The materials one unit of the recipe's product carries, per batch at today's costs: the batch's materials less its
    by-products, the product's share of the rest, over the batch's output. None when it cannot be worked out."""

    shared, _why = estimate(recipe, Decimal("1"), batch_materials, ZERO, recipe.output_quantity, outputs=outputs)
    if shared is None:
        return None
    return shared.main_unit_cost
