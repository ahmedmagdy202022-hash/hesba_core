from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe

from master_data.models import Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission

import json

from django.http import Http404

from . import designs as label_designs
from .services import assign_missing_barcodes
from .label_templates import FIELDS, TEMPLATES, offset, spec
from .label_templates import choices as label_choices
from .symbology import barcode_modules, barcode_svg


WORDS = {
    "ar": {
        "page_title": "ملصقات الباركود",
        "title": "ملصقات الباركود",
        "intro": "اختار الأصناف وعدد الملصقات لكل صنف، وبعدين اطبع. الملصق فيه اسم الصنف وسعره والباركود.",
        "search": "بحث بالاسم أو الكود أو الباركود",
        "find": "بحث",
        "code": "الكود",
        "item": "الصنف",
        "barcode": "الباركود",
        "price": "السعر",
        "copies": "عدد الملصقات",
        "no_barcode": "مفيش باركود — هيتطبع الكود",
        "layout": "شكل الطباعة",
        "a4": "ورقة A4 (24 ملصق)",
        "roll": "رول ملصقات 50×25 مم",
        "show_price": "اطبع السعر",
        "print": "طباعة الملصقات",
        "generate": "توليد باركود للأصناف اللي معندهاش",
        "generated": "تم توليد باركود لعدد {count} صنف.",
        "none_missing": "كل الأصناف ليها باركود بالفعل.",
        "missing_count": "{count} صنف من غير باركود.",
        "empty": "مفيش أصناف مطابقة.",
        "nothing_selected": "اختار صنف واحد على الأقل وحط عدد الملصقات.",
        "back": "رجوع",
        "pdf_hint": "لحفظها PDF اختار «حفظ كـ PDF» من نافذة الطباعة.",
        "template": "مقاس الملصق", "fields": "على الملصق", "show_name": "اسم الصنف", "show_code": "كود الصنف", "show_shop": "اسم المحل",
        "offset": "ضبط الطابعة (مم)", "offset_x": "يمين/شمال", "offset_y": "فوق/تحت", "offset_hint": "لو الطباعة مزحزحة، زوّد أو قلّل مليمترات لحد ما تيجي في النص.",
        "pdf": "تنزيل PDF", "pdf_working": "بيتجهز…", "pdf_failed": "معرفناش نعمل الملف على الجهاز ده. جرّب «حفظ كـ PDF» من نافذة الطباعة.",
        "designs": "تصميمات الملصقات", "new_design": "تصميم جديد", "designs_intro": "اعمل مقاس ملصق على قد الورق أو الرول اللي عندك: العرض والطول، وعدد الملصقات في ورقة A4، والهوامش والمسافات، وحجم الكلام.",
        "built_in": "المقاسات الجاهزة", "my_designs": "تصميماتي", "no_designs": "لسه معملتش تصميم.", "edit": "تعديل", "delete": "حذف", "use": "استخدم",
        "design_name": "اسم التصميم", "kind": "نوع الورق", "kind_roll": "رول (ملصق في كل صفحة)", "kind_a4": "ورقة A4 (شبكة ملصقات)",
        "size": "مقاس الملصق (مم)", "width": "العرض", "height": "الطول", "sheet": "ترتيب الورقة", "columns": "أعمدة", "rows": "صفوف",
        "top": "هامش فوق", "side": "هامش جانبي", "gap_x": "مسافة بين الأعمدة", "gap_y": "مسافة بين الصفوف",
        "text": "الكلام", "name_pt": "اسم الصنف (pt)", "price_pt": "السعر (pt)", "small_pt": "السطور الصغيرة (pt)", "barcode_pct": "الباركود (% من طول الملصق)",
        "barcode_text": "اطبع الأرقام تحت الباركود", "defaults": "يظهر على الملصق", "preview": "معاينة", "save": "حفظ التصميم", "cancel": "إلغاء",
        "saved": "اتحفظ التصميم.", "deleted": "اتحذف التصميم.", "sheet_fit": "الورقة: {used_w} × {used_h} مم من 210 × 297", "per_sheet": "{count} ملصق في الورقة",
        "sample_name": "قميص قطن مقاس L", "confirm_delete": "تحذف التصميم ده؟",
    },
    "en": {
        "page_title": "Barcode labels",
        "title": "Barcode labels",
        "intro": "Pick the items and how many labels each, then print. Each label shows the item name, price and barcode.",
        "search": "Search by name, code or barcode",
        "find": "Search",
        "code": "Code",
        "item": "Item",
        "barcode": "Barcode",
        "price": "Price",
        "copies": "Labels",
        "no_barcode": "No barcode — the item code is printed",
        "layout": "Layout",
        "a4": "A4 sheet (24 labels)",
        "roll": "Label roll 50×25 mm",
        "show_price": "Print the price",
        "print": "Print labels",
        "generate": "Generate barcodes for items without one",
        "generated": "Generated barcodes for {count} items.",
        "none_missing": "Every item already has a barcode.",
        "missing_count": "{count} items have no barcode.",
        "empty": "No matching items.",
        "nothing_selected": "Pick at least one item and a number of labels.",
        "back": "Back",
        "pdf_hint": "To save it as a PDF, choose “Save as PDF” in the print dialog.",
        "template": "Label size", "fields": "On the label", "show_name": "Item name", "show_code": "Item code", "show_shop": "Shop name",
        "offset": "Printer adjustment (mm)", "offset_x": "Sideways", "offset_y": "Up/down", "offset_hint": "If the print lands off-centre, nudge it a few millimetres.",
        "pdf": "Download PDF", "pdf_working": "Preparing…", "pdf_failed": "This device could not build the file. Use “Save as PDF” in the print dialog instead.",
        "designs": "Label designs", "new_design": "New design", "designs_intro": "Make a label to fit the paper or roll you have: width and height, how many on an A4 sheet, margins and gaps, and text sizes.",
        "built_in": "Ready-made sizes", "my_designs": "My designs", "no_designs": "No designs yet.", "edit": "Edit", "delete": "Delete", "use": "Use",
        "design_name": "Design name", "kind": "Paper", "kind_roll": "Roll (one label per page)", "kind_a4": "A4 sheet (grid of labels)",
        "size": "Label size (mm)", "width": "Width", "height": "Height", "sheet": "Sheet layout", "columns": "Columns", "rows": "Rows",
        "top": "Top margin", "side": "Side margin", "gap_x": "Gap between columns", "gap_y": "Gap between rows",
        "text": "Text", "name_pt": "Item name (pt)", "price_pt": "Price (pt)", "small_pt": "Small lines (pt)", "barcode_pct": "Barcode (% of label height)",
        "barcode_text": "Print the digits under the barcode", "defaults": "Shown on the label", "preview": "Preview", "save": "Save design", "cancel": "Cancel",
        "saved": "Design saved.", "deleted": "Design deleted.", "sheet_fit": "Sheet: {used_w} × {used_h} mm of 210 × 297", "per_sheet": "{count} labels per sheet",
        "sample_name": "Cotton shirt size L", "confirm_delete": "Delete this design?",
    },
}

MAX_LABELS = 500


def _currency():
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return (profile.default_currency if profile else "") or "EGP"


def _shop_name():
    from printing.company import company_details

    return company_details()["name"]


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "items"}
    context.update(extra)
    return context


@require_permission("barcode.print_labels")
def labels(request):
    query = request.GET.get("q", "").strip()
    items = Item.objects.filter(active=True)
    if query:
        items = items.filter(Q(item_code__icontains=query) | Q(barcode__icontains=query) | Q(item_name__icontains=query))
    page = Paginator(items, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "barcode/labels.html",
        _context(
            request,
            page=page,
            query=query,
            missing=(missing := Item.objects.filter(active=True, barcode="").count()),
            missing_text=WORDS[_lang(request)]["missing_count"].format(count=missing),
            can_generate=user_has_permission(request.user, "master_data.manage_items"),
            templates=[(key, label, json.dumps(spec(key)["defaults"])) for key, label in label_choices(_lang(request))],
            can_design=user_has_permission(request.user, "master_data.manage_items"),
            selected_template=request.GET.get("template", ""),
        ),
    )


@require_permission("master_data.manage_items")
def generate_missing(request):
    lang = _lang(request)
    if request.method == "POST":
        count = assign_missing_barcodes(request.user)
        words = WORDS[lang]
        messages.success(request, words["generated"].format(count=count) if count else words["none_missing"])
    return redirect(f"/barcode/labels/?lang={lang}")


@require_permission("barcode.print_labels")
def labels_print(request):
    lang = _lang(request)
    wanted = {}
    for key, value in request.GET.items():
        if key.startswith("copies_") and key[7:].isdigit():
            try:
                copies = max(0, min(int(value or 0), MAX_LABELS))
            except ValueError:
                copies = 0
            if copies:
                wanted[int(key[7:])] = copies
    if not wanted:
        messages.error(request, WORDS[lang]["nothing_selected"])
        return redirect(f"/barcode/labels/?lang={lang}")
    template = spec(request.GET.get("template") or request.GET.get("layout"))
    layout = template["kind"]
    show_price = request.GET.get("show_price", "1") == "1"
    # LABEL-002: which lines go on the label; name and price stay on unless unticked.
    shown = {field: request.GET.get(f"show_{field}", "1" if field in ("name", "price") else "0") == "1" for field in FIELDS}
    shown["price"] = show_price
    labels_out, pdf_labels = [], []
    currency = _currency()
    from settings_core.templatetags.hesba_format import money

    for item in Item.objects.filter(pk__in=wanted, active=True).order_by("item_code"):
        code = item.barcode or item.item_code
        svg = mark_safe(barcode_svg(code, module_width=2, height=50, show_text=template["barcode_text"]))
        kind, modules = barcode_modules(code)
        name = item.item_name + (f" · {item.size}" if item.size else "") + (f" · {item.color}" if item.color else "")
        entry = {"name": name, "price": f"{money(item.default_sale_price)} {currency}", "code": item.item_code, "text": code,
                 "modules": modules, "quiet": 11 if kind == "ean13" else 10}
        for _ in range(wanted[item.pk]):
            labels_out.append({"item": item, "svg": svg})
            pdf_labels.append(entry)
            if len(labels_out) >= MAX_LABELS:
                break
    return render(
        request,
        "barcode/labels_print.html",
        _context(request, labels=labels_out, layout=layout, show_price=show_price, currency=currency, back_url=f"/barcode/labels/?lang={lang}",
                 pdf_data={"spec": {k: template[k] for k in ("kind", "width", "height", "columns", "rows", "top", "side", "gap_x", "gap_y", "barcode_width",
                                                             "name_pt", "price_pt", "small_pt", "barcode_pct", "barcode_text", "key")},
                           "labels": pdf_labels, "shown": shown, "shop": _shop_name(), "rtl": lang != "en",
                           "offset": [offset(request.GET.get("offset_x")), offset(request.GET.get("offset_y"))],
                           "file": f"labels-{template['key']}.pdf"},
                 template=template, shown=shown, shop_name=_shop_name(), offset_x=offset(request.GET.get("offset_x")), offset_y=offset(request.GET.get("offset_y"))),
    )


# ---- LABEL-003: label designs ----

@require_permission("barcode.print_labels")
def designs(request):
    lang = _lang(request)
    built_in = [{"key": key, "label": value[7] if lang == "en" else value[6]} for key, value in TEMPLATES.items()]
    mine = [{"design": d, "summary": label_designs.summary(d, lang)} for d in label_designs.all_designs()]
    return render(request, "barcode/designs.html", _context(request, built_in=built_in, mine=mine,
                                                             can_design=user_has_permission(request.user, "master_data.manage_items")))


@require_permission("master_data.manage_items")
def design_edit(request, pk=None):
    lang = _lang(request)
    words = WORDS[lang]
    existing = label_designs.get(pk) if pk is not None else None
    if pk is not None and existing is None:
        raise Http404("No such label design.")
    errors = []
    design = dict(existing) if existing else label_designs.blank()
    if request.method == "POST":
        design, errors = label_designs.clean(request.POST, lang)
        if not errors:
            saved = label_designs.save(design, request.user, design_id=pk)
            messages.success(request, words["saved"])
            return redirect(f"/barcode/labels/?lang={lang}&template=d{saved['id']}")
    return render(request, "barcode/design_form.html", _context(request, design=design, errors=errors, editing=existing is not None,
                                                                 limits=label_designs.NUMBERS, shop_name=_shop_name()))


@require_permission("master_data.manage_items")
def design_delete(request, pk):
    lang = _lang(request)
    if request.method == "POST" and label_designs.delete(pk, request.user):
        messages.success(request, WORDS[lang]["deleted"])
    return redirect(f"/barcode/designs/?lang={lang}")
