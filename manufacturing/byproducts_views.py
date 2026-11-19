"""Manufacturing F9 (HG-139): what the recipe, run and order pages show of by-products and co-products.

Money (a by-product's value per unit, a co-product's percent, every cost) is put in the context only for someone who
may see cost (``inventory.view_cost``); everyone else who may open the page sees the items, their kinds and quantities.
"""

from decimal import Decimal

from config.money import money_round
from master_data.models import Item
from permissions.services import user_has_permission

from . import byproducts
from .models import OutputKind
from .views import COST, RECIPES, _lang, _pick, _plain

ZERO = Decimal("0")

WORDS = {
    "ar": {
        "title": "منتجات ثانوية ومشتركة",
        "hint": "لو الدفعة بتطلّع حاجة تانية مع المنتج الأساسي سجّلها هنا. المنتج الثانوي (زي الردة أو النشارة أو القصاقيص اللي بتتباع بالوزن) بيدخل المخزن بقيمة ثابتة للوحدة، والقيمة دي بتتخصم من تكلفة المنتج الأساسي. المنتج المشترك بياخد نصيبه من باقي التكلفة بنسبة، ولو النسب فاضية التقسيم بيبقى بقيمة البيع.",
        "none": "الوصفة دي بتطلّع المنتج الأساسي بس.",
        "item": "الصنف", "kind": "النوع", "by_product": "منتج ثانوي", "co_product": "منتج مشترك", "main": "المنتج الأساسي",
        "per_batch": "الكمية لكل دفعة", "value": "قيمة الوحدة", "percent": "النسبة من التكلفة", "by_sale": "بقيمة البيع", "rest": "الباقي",
        "planned_qty": "الكمية المتوقعة", "cost": "التكلفة", "unit_cost": "تكلفة الوحدة", "quantity": "الكمية", "stock_value": "القيمة في المخزون",
        "conversion": "نصيب العمالة والمصاريف", "status": "الحالة", "posted": "في المخزون", "reversed": "اترجع",
        "plan_title": "تقسيم تكلفة التشغيل على المخرجات", "main_unit": "تكلفة وحدة المنتج الأساسي", "deducted": "قيمة المنتجات الثانوية المخصومة",
        "plan_note": "الحساب بتكلفة الخامات النهارده من غير عمالة ومصاريف؛ الأرقام بتتثبت لما التشغيلة تتسجل.",
        "scrap_note": "الهالك اللي بيتباع يتسجّل هنا منتج ثانوي بقيمته، ومايتسجّلش كمان معيب في المراحل، عشان قيمته متتخصمش مرتين.",
        "finished_note": "الصنف اللي يتسجّل هنا بيظهر في الميزانية ضمن مخزون المنتج التام.",
        "release_note": "الوصفة دي محتاجة إفراج جودة: الفحص بيخص المنتج الأساسي، والمنتج الثانوي بيدخل مخزن الأمر من غير حجز.",
        "add": "صنف كمان", "kind_pick": "اختار النوع", "value_field": "قيمة الوحدة (للمنتج الثانوي)", "percent_field": "النسبة % (للمنتج المشترك، اختياري)",
        "save": "احفظ المنتجات الثانوية والمشتركة", "saved": "اتحفظت المنتجات الثانوية والمشتركة.",
        "view_only": "التعديل هنا لصاحب صلاحية إدارة الأصناف ورؤية التكلفة مع بعض.",
        "actual_title": "الكميات اللي طلعت فعلًا", "actual_hint": "الخانة فيها الكمية المتوقعة من الوصفة؛ عدّلها لو اللي طلع غير كده، واكتب صفر لو مطلعش من الصنف حاجة.",
        "run_title": "مخرجات التشغيلة", "order_title": "مخرجات الأمر", "expected_title": "المخرجات المتوقعة من الأمر",
        "split_note": "قيمة المنتج الثانوي اتخصمت من تكلفة التشغيلة الأول، والباقي اتقسّم على المنتج الأساسي والمنتجات المشتركة.",
        "expected_note": "أرقام متوقعة بحالة الأمر دلوقتي؛ بتتثبت عند إنهاء الأمر.",
        "card_extras": "منتجات ثانوية ومشتركة (مخصومة من تكلفة المنتج)",
    },
    "en": {
        "title": "By-products and co-products",
        "hint": "If a batch yields something else beside the main product, record it here. A by-product (bran, sawdust, offcuts sold by weight) enters stock at a fixed value per unit, and that value comes off the main product's cost. A co-product takes its share of the rest of the cost by a percent; with the percents left empty the split is by sale value.",
        "none": "This recipe yields the main product only.",
        "item": "Item", "kind": "Kind", "by_product": "By-product", "co_product": "Co-product", "main": "Main product",
        "per_batch": "Quantity per batch", "value": "Value per unit", "percent": "Share of the cost", "by_sale": "By sale value", "rest": "The rest",
        "planned_qty": "Expected quantity", "cost": "Cost", "unit_cost": "Unit cost", "quantity": "Quantity", "stock_value": "Value in stock",
        "conversion": "Share of labour and overhead", "status": "Status", "posted": "In stock", "reversed": "Reversed",
        "plan_title": "How the run's cost is shared between the outputs", "main_unit": "Main product's unit cost", "deducted": "By-products' value taken off",
        "plan_note": "Worked out at today's material costs with no labour or overhead; the figures are fixed when the run is recorded.",
        "scrap_note": "Scrap that is sold is recorded here as a by-product at its value, and not also as defective at a stage, so its value does not come off twice.",
        "finished_note": "An item recorded here shows on the balance sheet under finished goods.",
        "release_note": "This recipe needs a quality release: the check covers the main product, and a by-product enters the order's location without a hold.",
        "add": "Add an item", "kind_pick": "Choose the kind", "value_field": "Value per unit (by-product)", "percent_field": "Percent % (co-product, optional)",
        "save": "Save by-products and co-products", "saved": "By-products and co-products saved.",
        "view_only": "Only someone with both the items permission and the permission to see costs can change this.",
        "actual_title": "What really came out", "actual_hint": "Each box holds the quantity the recipe expects; change it if something else came out, and enter zero if none of the item came out.",
        "run_title": "The run's outputs", "order_title": "The order's outputs", "expected_title": "The outputs this order is expected to yield",
        "split_note": "The by-product's value came off the run's cost first; the rest was shared between the main product and the co-products.",
        "expected_note": "Expected figures as the order stands now; they are fixed when the order finishes.",
        "card_extras": "By-products and co-products (taken off the product's cost)",
    },
}


def _kind(kind, words):
    return words["by_product"] if kind == OutputKind.BY_PRODUCT else words["co_product"]


def _percent(number):
    return f"{number.normalize():f}" if number is not None else ""


# ---- the recipe page ----

def posted_outputs(request):
    """The rows of the recipe page's form as the person typed them (to show again after a refusal)."""

    return [{"item": request.POST.get(f"out_item_{i}", ""), "kind": request.POST.get(f"out_kind_{i}", ""), "quantity": request.POST.get(f"out_qty_{i}", ""),
             "value_per_unit": request.POST.get(f"out_value_{i}", ""), "share_percent": request.POST.get(f"out_percent_{i}", "")}
            for i in range(byproducts.OUTPUT_ROWS)]


def recipe_outputs_post(request, recipe, lang):
    rows = [{**row, "item": _pick(Item, row["item"], active=True)} for row in posted_outputs(request)]
    return byproducts.save_outputs(recipe, request.user, rows, lang)


def recipe_outputs_context(request, recipe, batches, plan, posted=None):
    """The recipe page's section: the recipe's extra outputs, how a run of ``batches`` would share its cost (for someone
    who may see cost), the form, and the quantity inputs of the plain-run form."""

    lang = _lang(request)
    words = WORDS[lang]
    can_cost = user_has_permission(request.user, COST)
    outputs = list(recipe.outputs.select_related("item"))
    shared, why = byproducts.estimate(recipe, batches, plan["cost"], ZERO, plan["output"], lang=lang, outputs=outputs) if outputs else (None, "")
    by_output = {share.output.pk: share for share in shared.rows} if shared else {}
    rows = []
    for output in outputs:
        share = by_output.get(output.pk)
        row = {"output": output, "kind": _kind(output.kind, words), "planned": byproducts.planned_quantity(output, batches)}
        if can_cost:
            row.update(value=output.value_per_unit, percent=_percent(share.percent if share and share.percent is not None else output.share_percent),
                       by_sale=output.kind == OutputKind.CO_PRODUCT and output.share_percent is None,
                       cost=share.cost if share else None, unit_cost=share.unit_cost if share else None)
        rows.append(row)
    form_rows = posted
    if form_rows is None:
        form_rows = [{"item": str(output.item_id), "kind": output.kind, "quantity": _plain(output.quantity),
                      "value_per_unit": _plain(output.value_per_unit) if output.value_per_unit is not None else "",
                      "share_percent": _percent(output.share_percent)} for output in outputs]
        form_rows += [{"item": "", "kind": "", "quantity": "", "value_per_unit": "", "share_percent": ""}] * (byproducts.OUTPUT_ROWS - len(form_rows))
    return {
        "bw": words, "output_rows": rows, "output_form_rows": form_rows[:byproducts.OUTPUT_ROWS],
        "output_plan": shared if can_cost else None, "output_plan_error": why if can_cost else "",
        "output_main_percent": _percent(shared.main_percent) if shared and can_cost else "",
        "can_outputs": user_has_permission(request.user, RECIPES) and can_cost,
        "output_inputs": [{"output": output, "kind": _kind(output.kind, words), "value": _plain(byproducts.planned_quantity(output, batches))} for output in outputs],
    }


def typed_quantities(request, recipe):
    """{RecipeOutput pk: what the person typed} from the quantity inputs of a run form (``output_qty_<pk>``); None when
    the form carried none, so a caller that never shows them changes nothing."""

    found = {output.pk: request.POST.get(f"output_qty_{output.pk}") for output in recipe.outputs.all() if f"output_qty_{output.pk}" in request.POST}
    return found or None


# ---- the run page and the order page ----

def _posted_rows(run, words, can_cost):
    """One row per output of a posted or cancelled run, the product first."""

    rows = []
    operation = run.output_operation
    extras = byproducts.run_outputs(run)
    main = {"item": run.recipe.product, "kind": words["main"], "quantity": run.output_quantity, "posted": operation.status == "posted", "main": True}
    if can_cost:
        main.update(unit_cost=operation.unit_cost, value=money_round(operation.quantity * operation.unit_cost),
                    conversion=run.conversion_cost - sum((row.conversion_share for row in extras), ZERO))
    rows.append(main)
    for extra in extras:
        row = {"item": extra.item, "kind": _kind(extra.kind, words), "quantity": extra.quantity, "posted": extra.operation.status == "posted", "main": False}
        if can_cost:
            row.update(unit_cost=extra.operation.unit_cost, value=money_round(extra.operation.quantity * extra.operation.unit_cost),
                       conversion=extra.conversion_share)
        rows.append(row)
    return rows


def run_outputs_context(request, run):
    """The run page: every output with its quantity and, for someone who may see cost, its cost. Empty for a run with
    no extra output, whose page stays as it was."""

    lang = _lang(request)
    words = WORDS[lang]
    if not run.outputs.exists():
        return {"bw": words, "outputs_table": None}
    return {"bw": words, "outputs_table": {"title": words["run_title"], "rows": _posted_rows(run, words, user_has_permission(request.user, COST)),
                                           "note": words["split_note"], "posted": True}}


def order_outputs_context(request, order, cost):
    """The order page: the run's outputs once it is finished; before that, what the order is expected to yield and the
    quantity inputs of its finish form. ``cost`` is the order's cost card (``orders.cost_card``)."""

    from . import actuals, orders
    from . import outside as outside_rules

    lang = _lang(request)
    words = WORDS[lang]
    can_cost = user_has_permission(request.user, COST)
    context = {"bw": words, "outputs_table": None, "output_inputs": []}
    if order.run_id:
        if order.run.outputs.exists():
            context["outputs_table"] = {"title": words["order_title"], "rows": _posted_rows(order.run, words, can_cost), "note": words["split_note"], "posted": True}
        return context
    outputs = list(order.recipe.outputs.select_related("item"))
    if not outputs or order.status == "cancelled" or not order.recipe.output_quantity:
        return context
    batches = orders._batches(order)
    spent = actuals.conversion(order)
    conversion = spent["labour"] + spent["overhead"] + outside_rules.expected(order)
    shared, why = byproducts.estimate(order.recipe, batches, cost["material"], conversion, cost["output"], lang=lang, outputs=outputs)
    by_output = {share.output.pk: share for share in shared.rows} if shared else {}
    rows = [{"item": order.recipe.product, "kind": words["main"], "quantity": cost["output"], "posted": False, "main": True,
             **({"unit_cost": shared.main_unit_cost, "value": money_round(cost["output"] * shared.main_unit_cost), "conversion": shared.main_conversion}
                if can_cost and shared else {})}]
    for output in outputs:
        share = by_output.get(output.pk)
        row = {"item": output.item, "kind": _kind(output.kind, words), "quantity": byproducts.planned_quantity(output, batches), "posted": False, "main": False}
        if can_cost and share:
            row.update(unit_cost=share.unit_cost, value=share.value, conversion=share.conversion)
        rows.append(row)
    context["outputs_table"] = {"title": words["expected_title"], "rows": rows, "note": words["expected_note"], "posted": False,
                                "error": why if can_cost else ""}
    context["output_inputs"] = [{"output": output, "kind": _kind(output.kind, words), "value": _plain(byproducts.planned_quantity(output, batches))}
                                for output in outputs]
    return context
