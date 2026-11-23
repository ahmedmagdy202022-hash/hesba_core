"""MFG-002 screens: the production board and one order."""

from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from master_data.models import Customer, Item, Location
from batches.services import batches_enabled
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.display_labels import choice_label

from . import actuals, byproducts_views, floor, floor_views, make_to_order, orders, outside_views, quality, quality_views, schedule, workcentres
from .models import OrderCostLine, OrderStatus, ProductionOrder, Recipe
from .stages import current_stages
from .schedule_views import schedule_context
from .views import COST, PRODUCE, VIEW, _lang, _pick
from entities import scope as entity_scope

WORDS = {
    "ar": {"title": "أوامر الإنتاج", "intro": "كل أمر إنتاج بيمشي على مراحل المصنع: أول مرحلة بتصرف الخامات من المخزن للإنتاج تحت التشغيل، ولما الأمر يخلص المنتج السليم بيدخل المخزن بتكلفة الخامات والعمالة والمصاريف.",
           "new": "أمر إنتاج جديد", "recipe": "المنتج (الوصفة)", "quantity": "الكمية", "location": "مكان الإنتاج", "customer": "لعميل (اختياري)",
           "due": "مطلوب يوم", "labor": "تكلفة العمالة", "overhead": "مصاريف تشغيل (كهرباء، إيجار...)", "notes": "ملاحظات", "save": "افتح الأمر",
           "planned": "لسه مبدأش", "late": "متأخر", "done": "خلص", "number": "الأمر", "product": "المنتج", "stage": "المرحلة",
           "advance": "خلّص المرحلة دي", "good": "سليم", "defect": "معيب", "finish": "إنهاء الأمر وإدخال المنتج المخزن", "cancel": "إلغاء الأمر",
           "reason": "سبب الإلغاء", "cost_card": "تكلفة الأمر", "material": "الخامات", "total": "الإجمالي", "per_unit": "تكلفة القطعة",
           "material_unit": "تكلفة الخامات للقطعة", "estimate": "تقدير بأسعار النهارده؛ بيتثبت لما الأمر يخلص.", "posted_note": "تكلفة الخامات المسجلة في المخزون؛ العمالة والمصاريف بتظهر هنا لحساب سعر البيع.", "absorbed_note": "الخامات والعمالة والمصاريف كلها داخلة في تكلفة المنتج في المخزون، ومتقسمة على القطع السليمة بس.", "issued_note": "الخامات اتصرفت للإنتاج تحت التشغيل بالتكلفة دي؛ العمالة والمصاريف هتدخل في تكلفة المنتج لما الأمر يخلص.", "scrap": "هالك (مدخلش المخزن)", "issue_hint": "أول مرحلة هتصرف الخامات من المخزن.",
           "history": "سجل المراحل", "by": "بواسطة", "defects": "إجمالي المعيب", "rate": "نسبة العيوب", "back": "أوامر الإنتاج", "run": "التشغيلة",
           "created": "اتفتح أمر الإنتاج.", "advanced": "المرحلة اتسجلت.", "finished": "الأمر خلص والمنتج دخل المخزن.", "cancelled_msg": "الأمر اتلغى.",
           "empty": "مفيش أوامر هنا.", "all_done": "كل المراحل خلصت — جاهز للإنهاء.", "for": "لـ", "materials_short": "الخامات مش كفاية حاليًا",
           "recipes_link": "الوصفات والتشغيلات", "no_recipes": "لازم تعمل وصفة للمنتج الأول من صفحة التصنيع.",
           "lot_no": "رقم تشغيلة المنتج (اختياري)", "lot_expiry": "تاريخ صلاحية المنتج (اختياري)", "lot_hint": "لو سيبتهم فاضيين: رقم التشغيلة هو رقم الإنتاج، والصلاحية من مدة صلاحية الوصفة.", "lot_date": "تاريخ صلاحية المنتج مش صحيح.",
           "for_sale": "لطلب", "delivery": "تسليم للعميل", "delivery_short": "تسليم", "open_sale": "افتح أمر البيع",
           "materials": "الخامات على الأمر", "kind": "النوع", "item": "الصنف", "qty": "الكمية", "unit_cost": "تكلفة الوحدة", "value": "القيمة", "why": "السبب",
           "extra": "صرف إضافي", "extra_hint": "لو الخط احتاج خامة زيادة أو بديلة: بتطلع من المخزن على الأمر بمتوسط تكلفتها النهارده.", "extra_save": "اصرف على الأمر",
           "extra_done": "الخامة اتصرفت على الأمر.", "return": "رجّع خامات", "return_hint": "اللي فضل من خامات الأمر بيرجع المخزن بنفس التكلفة اللي اتصرف بيها على الأمر ده.",
           "return_save": "رجّع للمخزن", "return_done": "الخامات رجعت المخزن.", "can_return": "متاح للرجوع", "nothing_back": "مفيش خامات متاحة للرجوع.",
           "actual": "التكاليف الفعلية", "actual_hint": "لكل نوع: لو سجلت سطور فعلية بتحل محل الرقم المخطط عند إنهاء الأمر، ولو مفيش بيتحسب المخطط.",
           "planned_fig": "المخطط", "actual_fig": "الفعلي", "lines_none": "لسه مفيش تكاليف فعلية.", "cost_kind": "نوع التكلفة", "labour_kind": "عمالة", "overhead_kind": "مصاريف تشغيل",
           "employee": "الموظف (اختياري)", "nobody": "—", "hours": "الساعات", "hour_rate": "سعر الساعة", "amount": "أو المبلغ", "description": "البيان",
           "add_line": "ضيف التكلفة", "line_added": "التكلفة اتسجلت.", "line_cancel": "إلغاء السطر", "line_cancelled": "السطر اتلغى.", "cancelled_line": "ملغي",
           "over_hint": "في آخر مرحلة: لو السليم طلع أكتر من كمية الأمر (لحد 10%)، اكتب السبب في الملاحظات.", "outside": "تشغيل لدى الغير",
           "costs_link": "تقرير الفروق",
           "minutes": "الدقايق اللي خدتها المرحلة (اختياري)", "routing": "المراحل ومراكز العمل", "centre": "مركز العمل", "planned_min": "دقايق مخططة", "recorded_min": "دقايق فعلية",
           "min_unit": "دقيقة", "planned_conv": "تكلفة المراحل المخططة بسعر مراكز العمل النهارده", "planned_conv_note": "رقم للقراءة بس: مش بيتسجل على الأمر ولا بيدخل المخزون.",
           "suggested": "تكلفة مقترحة من دقايق المراحل", "suggested_hint": "الدقايق × سعر ساعة مركز العمل. مفيش حاجة بتتسجل لحد ما تأكد السطر، ومفيش حاجة بتدخل المخزون قبل إنهاء الأمر.",
           "confirm": "أكّد التكلفة", "confirmed_tag": "اتسجلت", "no_suggested": "لسه مفيش دقايق متسجلة على مراحل ليها مركز عمل.", "cost_confirmed": "التكلفة اتسجلت على الأمر."},
    "en": {"title": "Production orders", "intro": "Each production order moves through the factory's stages: the first stage issues the materials from stock to work in progress, and when the order finishes the good units go into stock at the cost of materials, labour and overhead.",
           "new": "New production order", "recipe": "Product (recipe)", "quantity": "Quantity", "location": "Made at", "customer": "For customer (optional)",
           "due": "Due on", "labor": "Labour cost", "overhead": "Overhead (power, rent...)", "notes": "Notes", "save": "Open order",
           "planned": "Not started", "late": "Late", "done": "Done", "number": "Order", "product": "Product", "stage": "Stage",
           "advance": "Finish this stage", "good": "Good", "defect": "Defective", "finish": "Finish order and put the product into stock", "cancel": "Cancel order",
           "reason": "Reason for cancelling", "cost_card": "Order cost", "material": "Materials", "total": "Total", "per_unit": "Cost per unit",
           "material_unit": "Materials per unit", "estimate": "Estimated at today's costs; fixed when the order finishes.", "posted_note": "Material cost as posted to stock; labour and overhead are shown here for pricing.", "absorbed_note": "Materials, labour and overhead are all in the product's stock cost, spread over the good units only.", "issued_note": "Materials were issued to work in progress at this cost; labour and overhead join the product's cost when the order finishes.", "scrap": "Scrap (not put into stock)", "issue_hint": "The first stage issues the materials from stock.",
           "history": "Stage history", "by": "By", "defects": "Total defects", "rate": "Defect rate", "back": "Production orders", "run": "Run",
           "created": "Production order opened.", "advanced": "Stage recorded.", "finished": "Order finished and the product is in stock.", "cancelled_msg": "Order cancelled.",
           "empty": "No orders here.", "all_done": "All stages done — ready to finish.", "for": "for", "materials_short": "Not enough materials right now",
           "recipes_link": "Recipes and runs", "no_recipes": "Make a recipe for the product first on the manufacturing page.",
           "lot_no": "Product lot number (optional)", "lot_expiry": "Product expiry date (optional)", "lot_hint": "Left empty, the lot number is the run number and the expiry comes from the recipe's shelf life.", "lot_date": "The product's expiry date is not valid.",
           "for_sale": "For order", "delivery": "Delivery to the customer", "delivery_short": "Delivery", "open_sale": "Open the sales order",
           "materials": "Materials on the order", "kind": "Kind", "item": "Item", "qty": "Quantity", "unit_cost": "Unit cost", "value": "Value", "why": "Reason",
           "extra": "Extra issue", "extra_hint": "When the line needs more of a material, or a substitute: it leaves stock to this order at today's average cost.", "extra_save": "Issue to the order",
           "extra_done": "The material was issued to the order.", "return": "Return materials", "return_hint": "What is left of the order's materials goes back to stock at the cost it was issued to this order at.",
           "return_save": "Return to stock", "return_done": "The materials are back in stock.", "can_return": "Can return", "nothing_back": "No materials can come back.",
           "actual": "Actual costs", "actual_hint": "Per kind: actual lines replace the planned figure when the order finishes; with none, the planned figure counts.",
           "planned_fig": "Planned", "actual_fig": "Actual", "lines_none": "No actual costs yet.", "cost_kind": "Kind of cost", "labour_kind": "Labour", "overhead_kind": "Overhead",
           "employee": "Employee (optional)", "nobody": "—", "hours": "Hours", "hour_rate": "Hourly rate", "amount": "Or the amount", "description": "Description",
           "add_line": "Add the cost", "line_added": "The cost was recorded.", "line_cancel": "Cancel the line", "line_cancelled": "The line was cancelled.", "cancelled_line": "Cancelled",
           "over_hint": "At the final stage: if good units are more than the order (up to 10%), write the reason in the notes.", "outside": "Outside processing",
           "costs_link": "Variance report",
           "minutes": "Minutes the stage took (optional)", "routing": "Stages and work centres", "centre": "Work centre", "planned_min": "Planned minutes", "recorded_min": "Recorded minutes",
           "min_unit": "min", "planned_conv": "Planned stage cost at today's work centre rates", "planned_conv_note": "A figure to read only: it is not put on the order and does not enter stock.",
           "suggested": "Suggested cost from the stages' minutes", "suggested_hint": "Minutes x the work centre's hourly rate. Nothing is recorded until you confirm a line, and nothing enters stock before the order finishes.",
           "confirm": "Confirm the cost", "confirmed_tag": "Recorded", "no_suggested": "No minutes are recorded yet on a stage that has a work centre.", "cost_confirmed": "The cost was recorded on the order."},
}


def _ctx(request, **extra):
    lang = _lang(request)
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"],
            "can_produce": user_has_permission(request.user, PRODUCE), "can_cost": user_has_permission(request.user, COST),
            "lots_on": batches_enabled(), **extra}


def _can_open_sale(user):
    """F1: the link to a production order's sales order, for someone who may see sales orders (and only while they are switched on)."""

    from orders import services as sales

    return sales.enabled() and sales.can_view(user)


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _stock_items():
    return Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name")


def _employees():
    from staff.models import Employee

    return Employee.objects.filter(active=True).order_by("name")[:300]


def _employee(raw):
    from staff.models import Employee

    return _pick(Employee, raw, active=True)


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
    active = list(entity_scope.scope(ProductionOrder.objects, "location__entity").filter(status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS))
                  .select_related("recipe__product", "customer", "sales_order_line__order").order_by("due_date", "pk"))
    stage_list = [list(s) for s in current_stages()]
    columns = [{"key": "planned", "label": words["planned"], "orders": []}]
    columns += [{"key": s[0], "label": _stage_name(s, lang), "orders": []} for s in stage_list]
    columns.append({"key": "ready", "label": words["all_done"].split(" — ")[0], "orders": []})
    index = {c["key"]: c for c in columns}
    for order in active:  # F6a (HG-133): a routed order's stages are its own, so each gets a column of its own, before "ready"
        for stage in order.stages:
            column = f"{stage[0]}:{stage[1]}" if len(stage) >= 5 else stage[0]
            if column not in index and stage[0] not in index:
                index[column] = {"key": column, "label": _stage_name(stage, lang), "orders": []}
                columns.insert(len(columns) - 1, index[column])
    outside_views.mark_board(active)  # F4 (HG-137): «عند <المورّد>: الكمية» on the card of an order whose stage is out
    late = schedule.late_orders(active, today)  # F6b (HG-134): past its due date, or its last planned end is after it
    for order in active:
        order.late, order.late_reason = order.pk in late, late.get(order.pk, "")
        if order.status == OrderStatus.PLANNED:
            index["planned"]["orders"].append(order)
        elif order.stage_index >= len(order.stages):
            index["ready"]["orders"].append(order)
        else:
            stage = order.stages[order.stage_index]
            key = f"{stage[0]}:{stage[1]}" if len(stage) >= 5 else stage[0]
            index.setdefault(key if key in index else stage[0], index["planned"])["orders"].append(order)
    recent = entity_scope.scope(ProductionOrder.objects, "location__entity").filter(status__in=(OrderStatus.DONE, OrderStatus.CANCELLED)).select_related("recipe__product", "run")[:15]
    locations = quality.production_places(entity_scope.locations(Location.objects).filter(active=True))  # F5 (HG-136): never the quality hold place
    return render(request, "manufacturing/orders.html", _ctx(
        request, columns=columns, recent=[{"order": o, "label": choice_label(o, "status", lang)} for o in recent], error=error, post=request.POST,
        recipes=Recipe.objects.filter(active=True).select_related("product"), locations=locations,
        default_location=(locations.filter(is_default=True).first() or locations.first()), can_open_sale=_can_open_sale(request.user),
        customers=Customer.objects.filter(active=True).exclude(customer_code="WALK-IN").order_by("name")[:300],
        late_count=sum(1 for o in active if o.late), ow=outside_views.WORDS[lang],
        can_floor=floor.can_use(request.user), fw=floor_views.WORDS[lang]))  # F8 (HG-140): the way into the floor screen


@require_permission(VIEW)
def order_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    order = get_object_or_404(entity_scope.scope(ProductionOrder.objects, "location__entity").select_related("recipe__product", "location", "customer", "run", "sales_order_line__order__customer"), pk=pk)
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, PRODUCE):
            raise PermissionDenied("Production orders need inventory.adjust_stock.")
        action = request.POST.get("action")
        try:
            if action == "advance":
                # F5 (HG-136): the same advance; at the quality stage of a recipe with criteria they are recorded with it.
                quality_views.advance_order(request, order, lang)
                messages.success(request, words["advanced"])
            elif action == "finish":
                if not user_has_permission(request.user, COST):
                    raise PermissionDenied("Finishing posts cost; it needs inventory.view_cost.")
                try:
                    lot_expiry = date.fromisoformat(request.POST.get("lot_expiry", "")) if request.POST.get("lot_expiry") else None
                except ValueError:
                    raise ValidationError(words["lot_date"])
                # Manufacturing F9 (HG-139): what really came out of each by-product and co-product, when the form showed them.
                orders.finish(order, request.user, lang, lot_no=request.POST.get("lot_no", ""), lot_expiry=lot_expiry,
                              extra_outputs=byproducts_views.typed_quantities(request, order.recipe))
                messages.success(request, words["finished"])
            elif action == "cancel":
                orders.cancel(order, request.user, request.POST.get("reason", ""), lang)
                messages.success(request, words["cancelled_msg"])
            elif action == "extra":  # F12 (HG-132)
                actuals.extra_issue(order, request.user, item=_pick(Item, request.POST.get("item"), active=True), quantity=request.POST.get("quantity"),
                                    reason=request.POST.get("why", ""), lang=lang)
                messages.success(request, words["extra_done"])
            elif action == "return":
                actuals.return_materials(order, request.user, item=_pick(Item, request.POST.get("item")), quantity=request.POST.get("quantity"),
                                         reason=request.POST.get("why", ""), lang=lang)
                messages.success(request, words["return_done"])
            elif action == "add_cost":
                actuals.add_cost_line(order, request.user, kind=request.POST.get("kind", ""), description=request.POST.get("description", ""),
                                      employee=_employee(request.POST.get("employee")), hours=request.POST.get("hours"), rate=request.POST.get("rate"),
                                      amount=request.POST.get("amount"), lang=lang)
                messages.success(request, words["line_added"])
            elif action == "confirm_cost":  # F6a (HG-133): a suggested labour line from a stage's minutes, once
                workcentres.confirm(order, request.user, request.POST.get("log"), lang)
                messages.success(request, words["cost_confirmed"])
            elif action == "cancel_cost":
                line = get_object_or_404(OrderCostLine, pk=request.POST.get("line") if str(request.POST.get("line", "")).isdigit() else 0, order=order)
                actuals.cancel_cost_line(line, request.user, lang)
                messages.success(request, words["line_cancelled"])
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
    can_cost = user_has_permission(request.user, COST)
    on_floor = order.status == OrderStatus.IN_PROGRESS and order.issues.filter(kind="issue").exists()
    material_rows = []
    for issue in order.issues.select_related("operation__item"):
        operation = issue.operation
        material_rows.append({"issue": issue, "label": choice_label(issue, "kind", lang), "operation": operation, "sign": actuals.sign(operation),
                              "value": operation.quantity * operation.unit_cost})
    lines = [{"line": line, "label": choice_label(line, "kind", lang)} for line in order.cost_lines.select_related("employee", "work_centre")] if can_cost else []
    late_reason = schedule.late_orders([order], timezone.localdate()).get(order.pk, "")  # F6b (HG-134)
    plan_context = schedule_context(request, order, lang, late_reason)
    routing = workcentres.plan_rows(order, lang)  # F6a (HG-133)
    recorded = {log.stage_key: log.minutes for log in order.stage_logs.all()}
    for row in routing:
        row["recorded"] = recorded.get(row["key"])
    cost = orders.cost_card(order)
    from . import maintenance

    # Video finding F12: a stage on a work centre that is down, and not started before it stopped, is not recorded here.
    stopped = maintenance.stage_down(order) if order.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS) else None
    return render(request, "manufacturing/order.html", _ctx(
        request, order=order, status_label=choice_label(order, "status", lang), stages=stage_rows, logs=logs, error=error,
        material_rows=material_rows, on_floor=on_floor, routing=routing,
        planned_conversion=workcentres.planned_conversion(order) if routing and can_cost else None,
        suggested=workcentres.suggestions(order, lang) if can_cost else [], cost_lines=lines, spent=actuals.conversion(order) if can_cost else None,
        lines_open=order.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS),
        returnable=actuals.returnable(order) if on_floor and can_cost else [],
        extra_items=_stock_items().exclude(pk=order.recipe.product_id) if on_floor else [],
        employees=_employees() if can_cost and order.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS) else [],
        final_stage=order.stage_index == len(order.stages) - 1,
        cost=cost, quality=orders.quality(order), plan=plan, **byproducts_views.order_outputs_context(request, order, cost),
        sale=order.sales_order_line.order if order.sales_order_line_id else None, can_open_sale=_can_open_sale(request.user),
        short=bool(plan and any(row["short"] for row in plan["rows"])),
        current_label=stage_rows[order.stage_index]["label"] if order.stage_index < len(stage_rows) else "",
        ready=order.status == OrderStatus.IN_PROGRESS and order.stage_index >= len(order.stages),
        open=order.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS), late=late_reason != "", late_reason=late_reason, **plan_context,
        stage_stopped=maintenance.down_text(stopped, "down_note", lang) if stopped else "",
        **quality_views.order_quality_context(request, order, lang), **outside_views.order_context(request, order, lang),
        **floor_views.order_photos_context(request, order, lang)))  # F8 (HG-140): the photos of defects taken on the floor screen


@require_permission(PRODUCE)
@require_POST
def make_for_sale(request, line_pk):
    """F1 (HG-098): «اعمل أمر إنتاج» on one line of a confirmed sales order. Opens the production order and posts nothing."""

    from django.http import Http404

    from orders import services as sales
    from orders.models import SalesOrderLine

    lang = _lang(request)
    if not sales.enabled():
        raise Http404("Quotations are switched off.")
    if not sales.can_view(request.user):
        raise PermissionDenied("Making an order for a sale needs sales.view_sales_invoices.")
    line = get_object_or_404(SalesOrderLine.objects.select_related("order__customer", "item").filter(order__in=sales.scoped()), pk=line_pk)
    back = f"{reverse('orders:detail', args=[line.order_id])}?lang={lang}"
    location = _pick(Location, request.POST.get("location"), active=True)
    if request.POST.get("location") and location is None:  # a place that does not exist is refused, never swapped for the default
        messages.error(request, make_to_order.MESSAGES[lang]["location"])
        return redirect(back)
    data = {"quantity": request.POST.get("quantity"), "labor_cost": request.POST.get("labor_cost"), "overhead_cost": request.POST.get("overhead_cost"),
            "location": location}
    try:
        order = make_to_order.make(line, request.user, data, lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, make_to_order.WORDS[lang]["made_ok"].format(number=order.number))
    return redirect(back)
