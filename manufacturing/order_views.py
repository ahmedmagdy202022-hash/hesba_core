"""MFG-002 screens: the production board and one order."""

from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.display_labels import choice_label

from . import orders
from .models import OrderStatus, ProductionOrder, Recipe
from .stages import current_stages
from .views import COST, PRODUCE, VIEW, _lang, _pick

WORDS = {
    "ar": {"title": "أوامر الإنتاج", "intro": "كل أمر إنتاج بيمشي على مراحل المصنع لحد ما يخلص؛ أول ما يخلص، الخامات بتطلع من المخزن والمنتج بيدخل بتكلفته.",
           "new": "أمر إنتاج جديد", "recipe": "المنتج (الوصفة)", "quantity": "الكمية", "location": "مكان الإنتاج", "customer": "لعميل (اختياري)",
           "due": "مطلوب يوم", "labor": "تكلفة العمالة", "overhead": "مصاريف تشغيل (كهرباء، إيجار...)", "notes": "ملاحظات", "save": "افتح الأمر",
           "planned": "لسه مبدأش", "late": "متأخر", "done": "خلص", "number": "الأمر", "product": "المنتج", "stage": "المرحلة",
           "advance": "خلّص المرحلة دي", "good": "سليم", "defect": "معيب", "finish": "إنهاء الأمر وإدخال المنتج المخزن", "cancel": "إلغاء الأمر",
           "reason": "سبب الإلغاء", "cost_card": "تكلفة الأمر", "material": "الخامات", "total": "الإجمالي", "per_unit": "تكلفة القطعة",
           "material_unit": "تكلفة الخامات للقطعة", "estimate": "تقدير بأسعار النهارده؛ بيتثبت لما الأمر يخلص.", "posted_note": "تكلفة الخامات المسجلة في المخزون؛ العمالة والمصاريف بتظهر هنا لحساب سعر البيع.",
           "history": "سجل المراحل", "by": "بواسطة", "defects": "إجمالي المعيب", "rate": "نسبة العيوب", "back": "أوامر الإنتاج", "run": "التشغيلة",
           "created": "اتفتح أمر الإنتاج.", "advanced": "المرحلة اتسجلت.", "finished": "الأمر خلص والمنتج دخل المخزن.", "cancelled_msg": "الأمر اتلغى.",
           "empty": "مفيش أوامر هنا.", "all_done": "كل المراحل خلصت — جاهز للإنهاء.", "for": "لـ", "materials_short": "الخامات مش كفاية حاليًا",
           "recipes_link": "الوصفات والتشغيلات", "no_recipes": "لازم تعمل وصفة للمنتج الأول من صفحة التصنيع."},
    "en": {"title": "Production orders", "intro": "Each production order moves through the factory's stages until it is done; when it finishes, materials leave stock and the product comes in at their cost.",
           "new": "New production order", "recipe": "Product (recipe)", "quantity": "Quantity", "location": "Made at", "customer": "For customer (optional)",
           "due": "Due on", "labor": "Labour cost", "overhead": "Overhead (power, rent...)", "notes": "Notes", "save": "Open order",
           "planned": "Not started", "late": "Late", "done": "Done", "number": "Order", "product": "Product", "stage": "Stage",
           "advance": "Finish this stage", "good": "Good", "defect": "Defective", "finish": "Finish order and put the product into stock", "cancel": "Cancel order",
           "reason": "Reason for cancelling", "cost_card": "Order cost", "material": "Materials", "total": "Total", "per_unit": "Cost per unit",
           "material_unit": "Materials per unit", "estimate": "Estimated at today's costs; fixed when the order finishes.", "posted_note": "Material cost as posted to stock; labour and overhead are shown here for pricing.",
           "history": "Stage history", "by": "By", "defects": "Total defects", "rate": "Defect rate", "back": "Production orders", "run": "Run",
           "created": "Production order opened.", "advanced": "Stage recorded.", "finished": "Order finished and the product is in stock.", "cancelled_msg": "Order cancelled.",
           "empty": "No orders here.", "all_done": "All stages done — ready to finish.", "for": "for", "materials_short": "Not enough materials right now",
           "recipes_link": "Recipes and runs", "no_recipes": "Make a recipe for the product first on the manufacturing page."},
}


def _ctx(request, **extra):
    lang = _lang(request)
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"],
            "can_produce": user_has_permission(request.user, PRODUCE), "can_cost": user_has_permission(request.user, COST), **extra}


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _stage_name(stage, lang):
    return stage[2] if lang == "en" else stage[1]


@require_permission(VIEW)
def board(request):
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, PRODUCE):
            raise PermissionDenied("Production orders need inventory.adjust_stock.")
        data = {"recipe": _pick(Recipe, request.POST.get("recipe"), active=True), "location": _pick(Location, request.POST.get("location"), active=True),
                "customer": _pick(Customer, request.POST.get("customer"), active=True), "due_date": _date(request.POST.get("due_date")),
                **{key: request.POST.get(key) for key in ("quantity", "labor_cost", "overhead_cost", "notes")}}
        try:
            order = orders.create_order(data, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["created"])
            return redirect(f"{reverse('manufacturing:order', args=[order.pk])}?lang={lang}")
    today = timezone.localdate()
    active = list(ProductionOrder.objects.filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS))
                  .select_related("recipe__product", "customer").order_by("due_date", "pk"))
    stage_list = [list(s) for s in current_stages()]
    columns = [{"key": "planned", "label": words["planned"], "orders": []}]
    columns += [{"key": s[0], "label": _stage_name(s, lang), "orders": []} for s in stage_list]
    columns.append({"key": "ready", "label": words["all_done"].split(" — ")[0], "orders": []})
    index = {c["key"]: c for c in columns}
    for order in active:
        order.late = bool(order.due_date and order.due_date < today)
        if order.status == OrderStatus.PLANNED:
            index["planned"]["orders"].append(order)
        elif order.stage_index >= len(order.stages):
            index["ready"]["orders"].append(order)
        else:
            key = order.stages[order.stage_index][0]
            index.setdefault(key, index["planned"])["orders"].append(order)
    recent = ProductionOrder.objects.filter(status__in=(OrderStatus.DONE, OrderStatus.CANCELLED)).select_related("recipe__product", "run")[:15]
    locations = Location.objects.filter(active=True)
    return render(request, "manufacturing/orders.html", _ctx(
        request, columns=columns, recent=[{"order": o, "label": choice_label(o, "status", lang)} for o in recent], error=error, post=request.POST,
        recipes=Recipe.objects.filter(active=True).select_related("product"), locations=locations,
        default_location=(locations.filter(is_default=True).first() or locations.first()),
        customers=Customer.objects.filter(active=True).exclude(customer_code="WALK-IN").order_by("name")[:300],
        late_count=sum(1 for o in active if o.late)))


@require_permission(VIEW)
def order_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    order = get_object_or_404(ProductionOrder.objects.select_related("recipe__product", "location", "customer", "run"), pk=pk)
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, PRODUCE):
            raise PermissionDenied("Production orders need inventory.adjust_stock.")
        action = request.POST.get("action")
        try:
            if action == "advance":
                orders.advance(order, request.user, good=request.POST.get("good"), defect=request.POST.get("defect"), note=request.POST.get("note", ""), lang=lang)
                messages.success(request, words["advanced"])
            elif action == "finish":
                if not user_has_permission(request.user, COST):
                    raise PermissionDenied("Finishing posts cost; it needs inventory.view_cost.")
                orders.finish(order, request.user, lang)
                messages.success(request, words["finished"])
            elif action == "cancel":
                orders.cancel(order, request.user, request.POST.get("reason", ""), lang)
                messages.success(request, words["cancelled_msg"])
        except ValidationError as exc:
            from settings_core.ui_messages import translate

            error = translate(" ".join(exc.messages), lang)
        else:
            return redirect(f"{reverse('manufacturing:order', args=[order.pk])}?lang={lang}")
        order.refresh_from_db()
    from . import services

    stage_rows = []
    for i, stage in enumerate(order.stages):
        state = "done" if i < order.stage_index else ("current" if i == order.stage_index and order.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS) else "todo")
        stage_rows.append({"key": stage[0], "label": _stage_name(stage, lang), "state": state})
    names = {s[0]: _stage_name(s, lang) for s in order.stages}
    logs = [{"log": log, "label": names.get(log.stage_key, log.stage_key)} for log in order.stage_logs.select_related("done_by")]
    batches = order.quantity / order.recipe.output_quantity if order.recipe.output_quantity else 0
    plan = services.plan(order.recipe, batches, order.location) if order.status != OrderStatus.DONE else None
    return render(request, "manufacturing/order.html", _ctx(
        request, order=order, status_label=choice_label(order, "status", lang), stages=stage_rows, logs=logs, error=error,
        cost=orders.cost_card(order), quality=orders.quality(order), plan=plan,
        short=bool(plan and any(row["short"] for row in plan["rows"])),
        current_label=stage_rows[order.stage_index]["label"] if order.stage_index < len(stage_rows) else "",
        ready=order.status == OrderStatus.IN_PROGRESS and order.stage_index >= len(order.stages),
        open=order.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS), late=bool(order.due_date and order.due_date < timezone.localdate() and order.status != OrderStatus.DONE)))
