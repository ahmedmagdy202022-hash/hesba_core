"""MFG-001 screens: recipes, one recipe with its production plan, and production runs."""

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master_data.models import Item, Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.display_labels import choice_label
from settings_core.templatetags.hesba_format import qty as fmt_qty

from . import services
from .models import ProductionRun, Recipe
from entities import scope as entity_scope


VIEW, RECIPES, PRODUCE, COST = "inventory.view_stock", "master_data.manage_items", "inventory.adjust_stock", "inventory.view_cost"
LINE_ROWS = 12
WORDS = {
    "ar": {
        "page_title": "التصنيع", "title": "التصنيع", "intro": "اكتب وصفة كل منتج (مكوّناته لكل دفعة)، وبعدين سجّل التشغيلة: الخامات بتطلع من المخزن والمنتج بيدخل بتكلفتها.",
        "recipes": "الوصفات", "new": "وصفة جديدة", "code": "الكود", "product": "المنتج", "name": "اسم الوصفة (اختياري)", "output": "إنتاج الدفعة الواحدة",
        "components": "المكوّنات لكل دفعة", "component": "المكوّن", "qty": "الكمية", "active": "شغالة", "notes": "ملاحظات", "save": "حفظ",
        "saved": "اتحفظت الوصفة.", "empty": "لسه مفيش وصفات.", "back": "التصنيع", "edit": "تعديل الوصفة", "runs": "التشغيلات", "no_runs": "لسه مفيش تشغيلات.",
        "plan": "خطة التشغيل", "batches": "عدد الدفعات", "location": "المخزن", "check": "احسب", "need": "المطلوب", "have": "الموجود", "unit_cost": "تكلفة الوحدة",
        "cost": "التكلفة", "total_cost": "تكلفة التشغيلة", "will_make": "هيتنتج", "possible": "أقصى عدد دفعات بالخامات الموجودة", "short": "ناقص",
        "produce": "سجّل التشغيلة", "produced": "اتسجلت التشغيلة {number}: {output} من {product}.", "date": "التاريخ", "number": "رقم التشغيلة",
        "status": "الحالة", "cancel": "إلغاء التشغيلة", "reason": "السبب", "cancelled": "اتلغت التشغيلة ورجعت الخامات للمخزن.",
        "cancel_note": "الإلغاء بيرجّع المنتج من المخزن الأول (لو اتباع مينفعش)، وبعدين بيرجّع كل الخامات.", "consumed": "الخامات اللي اتصرفت", "made": "المنتج اللي دخل",
        "cost_needed": "تسجيل التشغيلة محتاج صلاحية رؤية التكلفة، عشان المنتج يدخل بتكلفته.", "view_only": "التعديل لصاحب الصلاحية بس.",
        "stock_only": "المنتج والمكوّنات لازم يكونوا أصناف متتبعة في المخزون.",
    },
    "en": {
        "page_title": "Manufacturing", "title": "Manufacturing", "intro": "Write each product's recipe (its components per batch), then record a run: materials leave stock and the product enters at their cost.",
        "recipes": "Recipes", "new": "New recipe", "code": "Code", "product": "Product", "name": "Recipe name (optional)", "output": "Output of one batch",
        "components": "Components per batch", "component": "Component", "qty": "Qty", "active": "Active", "notes": "Notes", "save": "Save",
        "saved": "Recipe saved.", "empty": "No recipes yet.", "back": "Manufacturing", "edit": "Edit recipe", "runs": "Production runs", "no_runs": "No runs yet.",
        "plan": "Run plan", "batches": "Batches", "location": "Location", "check": "Calculate", "need": "Needed", "have": "Available", "unit_cost": "Unit cost",
        "cost": "Cost", "total_cost": "Run cost", "will_make": "Will make", "possible": "Most batches the stock allows", "short": "Short",
        "produce": "Record the run", "produced": "Run {number} recorded: {output} of {product}.", "date": "Date", "number": "Run",
        "status": "Status", "cancel": "Cancel the run", "reason": "Reason", "cancelled": "Run cancelled; materials are back in stock.",
        "cancel_note": "Cancelling takes the product back out first (not possible once sold), then returns every material.", "consumed": "Materials used", "made": "Product made",
        "cost_needed": "Recording a run needs permission to see costs, so the product enters at its cost.", "view_only": "Only users with the permission can change this.",
        "stock_only": "The product and components must be stock-tracked items.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _pick(model, raw, **filters):
    raw = str(raw or "")
    return model.objects.filter(pk=raw, **filters).first() if raw.isdigit() else None


def _base(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"],
               "can_recipes": user_has_permission(request.user, RECIPES), "can_produce": user_has_permission(request.user, PRODUCE),
               "can_cost": user_has_permission(request.user, COST)}
    context.update(extra)
    return context


def _stock_items():
    return Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name")


def _recipe_post(request):
    data = {"product": _pick(Item, request.POST.get("product"), active=True), "name": request.POST.get("name", ""),
            "output_quantity": request.POST.get("output_quantity", "1"), "active": request.POST.get("active") == "on", "notes": request.POST.get("notes", "")}
    lines = [(_pick(Item, request.POST.get(f"component_{i}"), active=True), request.POST.get(f"qty_{i}", "")) for i in range(LINE_ROWS)]
    return data, lines


def _plain(number):
    """For an input value: no grouping commas, no trailing zeros."""

    return f"{number.normalize():f}"


def _rows(pairs):
    pairs = list(pairs)[:LINE_ROWS]
    return pairs + [("", "")] * (LINE_ROWS - len(pairs))


def _save_new_recipe(request, lang):
    """POST of the new-recipe form: the saved recipe, or (error, form, rows) to show again."""

    if not user_has_permission(request.user, RECIPES):
        raise PermissionDenied("Recipes need master_data.manage_items.")
    data, lines = _recipe_post(request)
    try:
        return services.save_recipe(data, lines, request.user, lang=lang), None
    except ValidationError as exc:
        rows = _rows([(request.POST.get(f"component_{i}", ""), request.POST.get(f"qty_{i}", "")) for i in range(LINE_ROWS)])
        return None, (" ".join(exc.messages), request.POST, rows)


def _overview(request, lang):
    """R2-9: the production control board: what is being made, what is short, what each product costs.

    Reads in bulk: one stock aggregate for every material and product, and one
    movement read for average costs, whatever the number of orders."""

    from collections import defaultdict
    from datetime import timedelta

    from django.db.models import F, Count, Sum
    from django.utils import timezone

    from inventory.models import StockMovement
    from inventory.services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES
    from inventory.warehouses import _costs

    from .models import OrderStatus, ProductionOrder

    today = timezone.localdate()
    can_cost = user_has_permission(request.user, COST)
    open_orders = list(entity_scope.scope(ProductionOrder.objects, "location__entity")
                       .filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS))
                       .select_related("recipe__product", "customer", "location").prefetch_related("recipe__lines__component").order_by("due_date", "pk"))
    recipes = list(Recipe.objects.select_related("product").prefetch_related("lines__component").order_by("-active", "code"))
    item_ids = {line.component_id for recipe in recipes for line in recipe.lines.all()}
    item_ids |= {line.component_id for order in open_orders for line in order.recipe.lines.all()}
    item_ids |= {recipe.product_id for recipe in recipes}
    # On hand per (item, location), within the entity being worked in.
    held, per_item = defaultdict(lambda: Decimal("0")), defaultdict(lambda: Decimal("0"))
    rows = (entity_scope.scope(StockMovement.objects, entity_scope.STOCK_MOVEMENT).filter(item_id__in=item_ids)
            .values("item_id", "location_id", "movement_type").annotate(total=Sum("quantity")))
    for row in rows:
        sign = 1 if row["movement_type"] in IN_MOVEMENT_TYPES else -1 if row["movement_type"] in OUT_MOVEMENT_TYPES else 0
        held[(row["item_id"], row["location_id"])] += sign * (row["total"] or Decimal("0"))
        per_item[row["item_id"]] += sign * (row["total"] or Decimal("0"))

    # HG-036: an order on the floor already has its materials (work in progress).
    from .models import OrderMaterialIssue

    issues = list(OrderMaterialIssue.objects.filter(order__in=open_orders).select_related("operation"))
    issued_orders = {issue.order_id for issue in issues}
    wip_value = sum((issue.operation.quantity * issue.operation.unit_cost for issue in issues), Decimal("0")) if can_cost else None
    shortages = {}
    for order in open_orders:
        total = len(order.stages) or 1
        order.progress = int(min(order.stage_index, total) * 100 / total)
        order.stage_label = (order.stages[order.stage_index][2 if lang == "en" else 1] if order.status == OrderStatus.IN_PROGRESS and order.stage_index < len(order.stages)
                             else "")
        order.late = bool(order.due_date and order.due_date < today)
        batches = (order.quantity / order.recipe.output_quantity) if order.recipe.output_quantity else Decimal("0")
        # Every open order together, per material and warehouse: two orders
        # that each fit can still be short side by side.
        for line in (() if order.pk in issued_orders else order.recipe.lines.all()):
            key = (line.component_id, order.location_id)
            entry = shortages.setdefault(key, {"item": line.component, "location": order.location, "need": Decimal("0"), "have": held[key], "orders": []})
            entry["need"] += (line.quantity * batches).quantize(Decimal("0.001"))
            entry["orders"].append(order.number)
    for entry in shortages.values():
        entry["missing"] = max(entry["need"] - entry["have"], Decimal("0"))
    shortages = [entry for entry in shortages.values() if entry["missing"] > 0]

    runs30 = entity_scope.scope(ProductionRun.objects, "location__entity").filter(status="posted", run_date__gte=today - timedelta(days=29))
    made = runs30.aggregate(units=Sum("output_quantity"), cost=Sum(F("total_cost") + F("conversion_cost")), count=Count("id"))
    costs = _costs(item_ids) if can_cost else {}
    products = []
    for recipe in recipes:
        row = {"recipe": recipe, "stock": per_item[recipe.product_id], "price": recipe.product.default_sale_price}
        if can_cost:
            from config.money import cost_round

            batch = sum((line.quantity * costs.get(line.component_id, Decimal("0")) for line in recipe.lines.all()), Decimal("0"))
            row["unit_cost"] = cost_round(batch / recipe.output_quantity) if recipe.output_quantity else Decimal("0")
            row["margin"] = round(float((row["price"] - row["unit_cost"]) / row["price"] * 100)) if row["price"] else None
        products.append(row)
    runs = entity_scope.scope(ProductionRun.objects.select_related("recipe", "recipe__product", "location"), "location__entity")[:10]
    return {
        "open_orders": open_orders, "planned_count": sum(1 for o in open_orders if o.status == OrderStatus.PLANNED),
        "running_count": sum(1 for o in open_orders if o.status == OrderStatus.IN_PROGRESS), "late_count": sum(1 for o in open_orders if o.late),
        "shortages": sorted(shortages, key=lambda e: -e["missing"]), "made": made, "products": products,
        "wip_value": wip_value, "wip_orders": len(issued_orders),
        "runs": [{"run": run, "status_label": choice_label(run, "status", lang)} for run in runs],
    }


@require_permission(VIEW)
def home(request):
    lang = _lang(request)
    if request.method == "POST":  # the recipe form used to live here; still accepted
        recipe, failed = _save_new_recipe(request, lang)
        if recipe:
            messages.success(request, WORDS[lang]["saved"])
            return redirect(f"{reverse('manufacturing:recipe', args=[recipe.pk])}?lang={lang}")
        error, form, rows = failed
        return render(request, "manufacturing/recipe_new.html", _base(request, items=_stock_items(), form=form, rows=rows, error=error))
    return render(request, "manufacturing/home.html", _base(request, **_overview(request, lang)))


@require_permission(VIEW)
def recipe_new(request):
    lang = _lang(request)
    if not user_has_permission(request.user, RECIPES):
        raise PermissionDenied("Recipes need master_data.manage_items.")
    error, form, rows = "", {"output_quantity": "1", "active": True}, _rows([])
    if request.method == "POST":
        recipe, failed = _save_new_recipe(request, lang)
        if recipe:
            messages.success(request, WORDS[lang]["saved"])
            return redirect(f"{reverse('manufacturing:recipe', args=[recipe.pk])}?lang={lang}")
        error, form, rows = failed
    return render(request, "manufacturing/recipe_new.html", _base(request, items=_stock_items(), form=form, rows=rows, error=error))


@require_permission(VIEW)
def recipe_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    recipe = get_object_or_404(Recipe.objects.select_related("product"), pk=pk)
    error = ""
    locations = entity_scope.locations(Location.objects).filter(active=True)
    location = _pick(Location, request.GET.get("location") or request.POST.get("location"), active=True) or locations.filter(is_default=True).first() or locations.first()
    batches = services._decimal(request.GET.get("batches") or request.POST.get("batches") or "1") or Decimal("1")
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "produce":
                if not user_has_permission(request.user, PRODUCE):
                    raise PermissionDenied("Production needs inventory.adjust_stock.")
                if not user_has_permission(request.user, COST):
                    raise ValidationError(words["cost_needed"])
                run_date = None
                try:
                    run_date = date.fromisoformat(request.POST.get("run_date", "")) if request.POST.get("run_date") else None
                except ValueError:
                    run_date = None
                run = services.produce(recipe, request.user, batches=request.POST.get("batches"), location=location, run_date=run_date,
                                       notes=request.POST.get("notes", ""), lang=lang)
                messages.success(request, words["produced"].format(number=run.number, output=fmt_qty(run.output_quantity), product=recipe.product.item_name))
                return redirect(f"{reverse('manufacturing:run', args=[run.pk])}?lang={lang}")
            if not user_has_permission(request.user, RECIPES):
                raise PermissionDenied("Recipes need master_data.manage_items.")
            data, lines = _recipe_post(request)
            services.save_recipe(data, lines, request.user, recipe, lang)
            messages.success(request, words["saved"])
            return redirect(f"{reverse('manufacturing:recipe', args=[recipe.pk])}?lang={lang}")
        except ValidationError as exc:
            from settings_core.ui_messages import translate

            error = translate(" ".join(exc.messages), lang)
    lines = list(recipe.lines.select_related("component"))
    form = {"product": str(recipe.product_id), "name": recipe.name, "output_quantity": _plain(recipe.output_quantity), "active": recipe.active, "notes": recipe.notes}
    return render(request, "manufacturing/recipe.html", _base(
        request, recipe=recipe, lines=lines, plan=services.plan(recipe, batches, location), batches=_plain(batches), location=location,
        locations=locations, items=_stock_items(), form=form, rows=_rows([(str(line.component_id), _plain(line.quantity)) for line in lines]), error=error,
        runs=[{"run": run, "status_label": choice_label(run, "status", lang)} for run in recipe.runs.select_related("location")[:20]],
    ))


@require_permission(VIEW)
def run_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    run = get_object_or_404(entity_scope.scope(ProductionRun.objects, "location__entity").select_related("recipe", "recipe__product", "location", "output_operation"), pk=pk)
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, PRODUCE):
            raise PermissionDenied("Cancelling a run needs inventory.adjust_stock.")
        try:
            services.cancel_run(run, request.user, reason=request.POST.get("reason", ""), lang=lang)
        except ValidationError as exc:
            from settings_core.ui_messages import translate

            error = translate(" ".join(exc.messages), lang)
        else:
            messages.success(request, words["cancelled"])
            return redirect(f"{reverse('manufacturing:run', args=[run.pk])}?lang={lang}")
    return render(request, "manufacturing/run.html", _base(
        request, run=run, status_label=choice_label(run, "status", lang), error=error,
        consumed=[c.operation for c in run.consumptions.select_related("operation", "operation__item")],
    ))
