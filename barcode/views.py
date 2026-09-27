from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe

from master_data.models import Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .services import assign_missing_barcodes
from .symbology import barcode_svg


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
    },
}

MAX_LABELS = 500


def _currency():
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return (profile.default_currency if profile else "") or "EGP"


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
    layout = "roll" if request.GET.get("layout") == "roll" else "a4"
    show_price = request.GET.get("show_price", "1") == "1"
    labels_out = []
    for item in Item.objects.filter(pk__in=wanted, active=True).order_by("item_code"):
        code = item.barcode or item.item_code
        svg = mark_safe(barcode_svg(code, module_width=2, height=50))
        for _ in range(wanted[item.pk]):
            labels_out.append({"item": item, "svg": svg})
            if len(labels_out) >= MAX_LABELS:
                break
    return render(
        request,
        "barcode/labels_print.html",
        _context(request, labels=labels_out, layout=layout, show_price=show_price, currency=_currency(), back_url=f"/barcode/labels/?lang={lang}"),
    )
