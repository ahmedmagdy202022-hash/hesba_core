"""Manufacturing F7 (HG-138) screens: one quotation line's specifications and cost sheet, and «حوّله لأمر إنتاج خاص».

Every rule is in ``manufacturing.quotes``; the views read, call and render. The page opens for whoever may see quotations
(``sales.view_sales_invoices``, the quotations capability on). Writing the specifications needs
``sales.create_sales_invoice``. The cost sheet is shown, and written, only with ``inventory.view_cost`` (writing also
needs ``sales.create_sales_invoice``); everyone else sees the specifications and the price. Making the line a custom
production order needs ``inventory.adjust_stock`` and ``master_data.manage_items``. Another entity's line returns 404.

The sales order page (``orders.views.detail``) shows each line's specifications under it and a link here
(``line_context``); the quotation's print shows them too (``quotes.spec_rows``)."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master_data.models import Item, Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import make_to_order, quotes
from .models import CostSheetLine, CostSheetLineKind
from .views import _lang, _pick

WORDS = {
    "ar": {
        "title": "مواصفات وتكلفة البند", "back": "أمر البيع", "quote": "عرض السعر", "order": "أمر البيع", "customer": "العميل", "line": "البند", "qty": "الكمية",
        "price": "سعر الوحدة على البند",
        "intro": "المواصفات بتتطبع تحت البند في عرض السعر. بيان التكلفة هو الحساب اللي طلع منه السعر، ومحدش بيشوفه غير صاحب صلاحية رؤية التكلفة.",
        "spec_title": "المواصفات", "template": "نوع المواصفات", "spec_save": "احفظ المواصفات", "spec_saved": "المواصفات اتحفظت.", "no_spec": "لسه مفيش مواصفات للبند ده.",
        "sheet_title": "بيان التكلفة", "kind": "النوع", "item_desc": "الخامة أو البيان", "line_qty": "الكمية", "unit_cost": "تكلفة الوحدة", "amount": "القيمة",
        "no_lines": "لسه مفيش سطور في بيان التكلفة.", "remove": "شيل", "add_title": "ضيف سطر", "material": "الخامة", "choose": "اختار",
        "description": "البيان", "description_hint": "للعمالة والمصاريف والتشغيل لدى الغير، مثلًا: تجليد بالدبوس.",
        "qty_hint": "كمية البند كله مش للوحدة الواحدة.", "cost_hint": "للخامة: لو سيبتها فاضية بتتحسب بمتوسط تكلفتها النهارده.", "add": "ضيف السطر",
        "line_added": "السطر اتضاف.", "line_removed": "السطر اتشال.",
        "materials": "الخامات", "labour": "العمالة", "overhead": "مصاريف التشغيل", "outside": "تشغيل لدى الغير", "cost": "إجمالي التكلفة",
        "unit_cost_total": "تكلفة الوحدة", "margin": "نسبة الربح %", "margin_amount": "الربح", "price_total": "سعر البند كله", "computed": "سعر الوحدة المحسوب",
        "override": "سعر الوحدة اللي هيتكتب (اختياري)", "override_hint": "لو سيبته فاضي بيتكتب السعر المحسوب.", "price_save": "احفظ وحط السعر على البند",
        "price_saved": "السعر اتحط على البند.", "price_locked": "العميل وافق على السعر ده، فمبقاش بيتغيّر من هنا.", "used": "السعر المستخدم",
        "convert_title": "أمر إنتاج خاص",
        "convert_hint": "بيتعمل صنف للشغل ده لوحده بوصفته من خامات البيان، والبند بيتربط بيه عشان الفاتورة تبيعه بتكلفته هو. بعدها بيتفتح أمر إنتاج بالكمية كلها، والعمالة والمصاريف المخططة من البيان.",
        "outside_hint": "سطور التشغيل لدى الغير بتتكتب ملاحظة على الأمر، وكل مرحلة بتتسجل عند جهة التشغيل من صفحة الأمر لما توصل لها.",
        "location": "مكان الإنتاج", "convert": "حوّله لأمر إنتاج خاص", "converted_ok": "اتفتح أمر الإنتاج {number} للشغل ده.",
        "wait_confirm": "التحويل لأمر إنتاج خاص بيبقى بعد موافقة العميل على العرض.", "need_materials": "ضيف خامة واحدة على الأقل لبيان التكلفة عشان الوصفة تتعمل منها.",
        "done_title": "الشغل ده بقى أمر إنتاج خاص", "job_item": "صنف الشغل", "recipe": "الوصفة", "production": "أمر الإنتاج", "open": "افتح",
        "view_only": "تقدر تشوف المواصفات بس.", "specs_link": "المواصفات والتكلفة", "specs_only_link": "المواصفات",
    },
    "en": {
        "title": "Line specifications and cost", "back": "Sales order", "quote": "Quotation", "order": "Sales order", "customer": "Customer", "line": "Line", "qty": "Quantity",
        "price": "Unit price on the line",
        "intro": "The specifications print under the line on the quotation. The cost sheet is how the price was worked out; only people who may see costs see it.",
        "spec_title": "Specifications", "template": "Kind of specifications", "spec_save": "Save the specifications", "spec_saved": "The specifications were saved.", "no_spec": "No specifications for this line yet.",
        "sheet_title": "Cost sheet", "kind": "Kind", "item_desc": "Material or description", "line_qty": "Quantity", "unit_cost": "Unit cost", "amount": "Amount",
        "no_lines": "The cost sheet has no lines yet.", "remove": "Remove", "add_title": "Add a line", "material": "Material", "choose": "Choose",
        "description": "Description", "description_hint": "For labour, overhead and outside work, for example: saddle-stitch binding.",
        "qty_hint": "For the whole line, not per unit.", "cost_hint": "For a material: left empty, it is today's average cost.", "add": "Add the line",
        "line_added": "The line was added.", "line_removed": "The line was removed.",
        "materials": "Materials", "labour": "Labour", "overhead": "Overhead", "outside": "Outside work", "cost": "Total cost",
        "unit_cost_total": "Cost per unit", "margin": "Margin %", "margin_amount": "Margin", "price_total": "Price of the whole line", "computed": "Computed unit price",
        "override": "Unit price to use (optional)", "override_hint": "Left empty, the computed price is used.", "price_save": "Save and put the price on the line",
        "price_saved": "The price is on the line.", "price_locked": "The customer accepted this price, so it no longer changes here.", "used": "Price used",
        "convert_title": "Custom production order",
        "convert_hint": "An item of its own is made for this job, with a recipe from the sheet's materials, and the line points to it so the invoice sells it at its own cost. Then a production order opens for the whole quantity, with planned labour and overhead from the sheet.",
        "outside_hint": "Outside work lines become a note on the order; each stage is recorded at the outside supplier from the order page when it gets there.",
        "location": "Made at", "convert": "Make it a custom production order", "converted_ok": "Production order {number} opened for this job.",
        "wait_confirm": "A line becomes a custom production order once the customer accepts the quotation.", "need_materials": "Add at least one material to the cost sheet so the recipe can be made from it.",
        "done_title": "This job is a custom production order", "job_item": "Job item", "recipe": "Recipe", "production": "Production order", "open": "Open",
        "view_only": "You can view the specifications only.", "specs_link": "Specifications and cost", "specs_only_link": "Specifications",
    },
}


def _translated(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(exc.messages), lang)


def _guard(request):
    from orders import services as sales

    if not sales.enabled():
        raise Http404("Quotations are switched off.")


def line_context(request, order, rows, lang):
    """For the sales order page: per line pk, the specifications to show under it and the link to its page."""

    sheets = {sheet.sales_order_line_id: sheet for sheet in quotes.CostSheet.objects.filter(sales_order_line__order=order)}
    can_cost = user_has_permission(request.user, quotes.COST)
    words = WORDS[lang]
    states = {}
    for row in rows:
        line = row["line"]
        sheet = sheets.get(line.pk)
        states[line.pk] = {"specs": quotes.spec_rows(sheet, lang), "url": reverse("manufacturing:quote_sheet", args=[line.pk]),
                           "label": words["specs_link"] if can_cost else words["specs_only_link"],
                           "show": sheet is not None or quotes.editable(order, None),
                           "job": sheet.job_item if quotes.converted(sheet) else None}
    return {"qw": words, "lines": states}


def _sheet_context(request, line, lang):
    from orders.models import Status

    user = request.user
    order = line.order
    sheet = quotes.sheet_of(line)
    can_manage, can_cost = user_has_permission(user, quotes.MANAGE), user_has_permission(user, quotes.COST)
    editable = quotes.editable(order, sheet)
    template = sheet.template if sheet else quotes.default_template()
    answers = sheet.spec if sheet else {}
    context = {
        "order": order, "line": line, "sheet": sheet, "template": template, "templates": quotes.template_choices(lang),
        "fields": [{"key": key, "label": label, "value": answers.get(key, "")} for key, label in quotes.fields_of(template, lang)],
        "spec_rows": quotes.spec_rows(sheet, lang), "can_spec": can_manage and editable, "can_cost": can_cost,
        "can_edit_cost": can_cost and can_manage and editable, "kinds": [(kind, quotes.KIND_LABELS[lang][kind]) for kind in quotes.CostSheetLineKind.values],
        "kind_labels": quotes.KIND_LABELS[lang], "price_open": quotes.price_open(order), "converted": quotes.converted(sheet),
        "is_order": order.status == Status.CONFIRMED,
    }
    if can_cost and sheet is not None:
        rows = list(sheet.lines.select_related("item").order_by("pk"))
        context["cost_rows"] = [{"row": row, "amount": quotes.line_amount(row), "cost_plain": f"{row.unit_cost.normalize():f}"} for row in rows]
        context["numbers"] = quotes.figures(sheet, line.quantity)
        context["margin_plain"] = f"{sheet.margin_percent.normalize():f}"
        context["override_plain"] = f"{sheet.price_override.normalize():f}" if sheet.price_override is not None else ""
        context["has_materials"] = any(row.kind == CostSheetLineKind.MATERIAL for row in rows)
        context["has_outside"] = any(row.kind == CostSheetLineKind.OUTSIDE for row in rows)
    if context["can_edit_cost"]:
        context["stock_items"] = list(Item.objects.filter(active=True, is_stock_tracked=True).exclude(item_code__startswith=quotes.JOB_PREFIX)
                                      .order_by("item_name")[:500])
    can_convert = (sheet is not None and not context["converted"] and order.status == Status.CONFIRMED
                   and user_has_permission(user, quotes.PRODUCE) and user_has_permission(user, quotes.ITEMS))
    context["can_convert"] = can_convert
    context["convert_ready"] = can_convert and sheet.lines.filter(kind=CostSheetLineKind.MATERIAL).exists()
    context["locations"] = list(make_to_order.locations_for(order)) if can_convert else []
    if context["converted"]:
        context["productions"] = list(line.production_orders.order_by("pk"))
        context["can_open"] = user_has_permission(user, "inventory.view_stock")
    return context


@require_permission(quotes.VIEW)
def quote_sheet(request, line_pk):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    line = get_object_or_404(quotes.scoped_lines().select_related("order__customer", "item"), pk=line_pk)
    here = f"{reverse('manufacturing:quote_sheet', args=[line.pk])}?lang={lang}"
    if request.method == "POST":
        action = request.POST.get("action")
        needed = {"spec": (quotes.MANAGE,), "add_line": (quotes.COST, quotes.MANAGE), "remove_line": (quotes.COST, quotes.MANAGE),
                  "price": (quotes.COST, quotes.MANAGE), "convert": (quotes.PRODUCE, quotes.ITEMS)}.get(action)
        if needed is None:
            raise Http404("Unknown action.")
        if not all(user_has_permission(request.user, code) for code in needed):
            raise PermissionDenied(f"This action needs {', '.join(needed)}.")
        try:
            if action == "spec":
                answers = {key: request.POST.get(f"spec_{key}", "") for key, _label in quotes.fields_of(request.POST.get("template", ""))}
                quotes.save_spec(line, request.user, request.POST.get("template", ""), answers, lang)
                done = words["spec_saved"]
            elif action == "add_line":
                kind = request.POST.get("kind", "")
                quotes.add_cost_line(line, request.user, kind=kind, item=_pick(Item, request.POST.get("item"), active=True) if kind == CostSheetLineKind.MATERIAL else None,
                                     description=request.POST.get("description", ""), quantity=request.POST.get("quantity"),
                                     unit_cost=request.POST.get("unit_cost"), lang=lang)
                done = words["line_added"]
            elif action == "remove_line":
                raw = str(request.POST.get("row", ""))
                row = get_object_or_404(CostSheetLine, pk=raw if raw.isdigit() else 0, sheet__sales_order_line=line)
                quotes.remove_cost_line(row, request.user, lang)
                done = words["line_removed"]
            elif action == "price":
                quotes.set_price(line, request.user, margin=request.POST.get("margin"), override=request.POST.get("override"), lang=lang)
                done = words["price_saved"]
            else:
                location = _pick(Location, request.POST.get("location"), active=True)
                if request.POST.get("location") and location is None:  # a place that does not exist is refused, never swapped for the default
                    raise ValidationError(make_to_order.MESSAGES[lang]["location"])
                production = quotes.convert(line, request.user, {"location": location}, lang)
                done = words["converted_ok"].format(number=production.number)
        except ValidationError as exc:
            messages.error(request, _translated(exc, lang))
        else:
            messages.success(request, done)
        return redirect(here)
    return render(request, "manufacturing/quote_sheet.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "qw": words, "page_title": words["title"], "section": "quotes",
        **_sheet_context(request, line, lang)})
