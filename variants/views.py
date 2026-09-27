"""VARIANT-001 screens: models with sizes and colours, and one model's grid."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master_data.models import Category
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .models import VariantGroup
from .services import create_group, extend_group, matrix, parse_values, set_group_prices


WORDS = {
    "ar": {
        "page_title": "المقاسات والألوان", "title": "المقاسات والألوان",
        "intro": "اعمل الموديل مرة واحدة بمقاساته وألوانه، وحسبة تعمل صنف لكل مقاس ولون بكوده وباركوده ومخزونه. البيع والشراء بالباركود زي أي صنف.",
        "new": "موديل جديد", "code": "كود الموديل", "name": "اسم الموديل", "sizes": "المقاسات", "colors": "الألوان", "sizes_hint": "افصل بفاصلة: S, M, L, XL",
        "colors_hint": "افصل بفاصلة: أسود، أبيض، كحلي", "sale_price": "سعر البيع", "purchase_price": "سعر الشراء", "category": "التصنيف", "unit": "الوحدة",
        "barcodes": "اعمل باركود لكل صنف", "create": "إنشاء الأصناف", "created": "اتعمل {n} صنف.", "models": "الموديلات", "count": "عدد الأصناف", "open": "فتح",
        "search": "بحث بالكود أو الاسم", "filter": "بحث", "empty": "مفيش موديلات لسه.", "size": "المقاس", "total": "الإجمالي", "stock_grid": "المخزون بالمقاس واللون",
        "extend": "إضافة مقاسات أو ألوان", "add": "إضافة", "added": "اتضاف {n} صنف.", "nothing_added": "مفيش حاجة جديدة.",
        "prices": "سعر موحّد لكل الموديل", "apply": "تطبيق", "priced": "اتحدّث سعر {n} صنف.", "back": "الموديلات", "view_only": "تقدر تشوف بس؛ التعديل لمسؤول الأصناف.",
        "bad_price": "السعر لازم رقم مش سالب.", "codes": "أكواد الأصناف", "none": "—", "prev": "السابق", "next": "التالي",
    },
    "en": {
        "page_title": "Sizes & colours", "title": "Sizes & colours",
        "intro": "Create a model once with its sizes and colours, and Hesba makes one item per size and colour, each with its own code, barcode and stock. Sell and buy them by barcode like any item.",
        "new": "New model", "code": "Model code", "name": "Model name", "sizes": "Sizes", "colors": "Colours", "sizes_hint": "Comma separated: S, M, L, XL",
        "colors_hint": "Comma separated: Black, White, Navy", "sale_price": "Sale price", "purchase_price": "Purchase price", "category": "Category", "unit": "Unit",
        "barcodes": "Give every item a barcode", "create": "Create items", "created": "{n} items created.", "models": "Models", "count": "Items", "open": "Open",
        "search": "Search code or name", "filter": "Search", "empty": "No models yet.", "size": "Size", "total": "Total", "stock_grid": "Stock by size and colour",
        "extend": "Add sizes or colours", "add": "Add", "added": "{n} items added.", "nothing_added": "Nothing new.",
        "prices": "One price for the whole model", "apply": "Apply", "priced": "{n} item prices updated.", "back": "Models", "view_only": "You can view; editing is for item managers.",
        "bad_price": "Prices must be numbers, not negative.", "codes": "Item codes", "none": "—", "prev": "Previous", "next": "Next",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "items",
               "can_manage": user_has_permission(request.user, "master_data.manage_items")}
    context.update(extra)
    return context


def _price(raw, words, required=False):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        if required:
            return Decimal("0.00")
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValidationError(words["bad_price"])
    if value < 0:
        raise ValidationError(words["bad_price"])
    return value.quantize(Decimal("0.01"))


@require_permission("master_data.view_master_data")
def group_index(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST":
        if not user_has_permission(request.user, "master_data.manage_items"):
            raise PermissionDenied("Creating variant items needs master_data.manage_items.")
        try:
            category = Category.objects.filter(pk=request.POST.get("category") or 0).first()
            group, created = create_group(
                code=request.POST.get("code", ""), name=request.POST.get("name", ""),
                sizes=parse_values(request.POST.get("sizes")), colors=parse_values(request.POST.get("colors")),
                sale_price=_price(request.POST.get("sale_price"), words, True), purchase_price=_price(request.POST.get("purchase_price"), words, True),
                unit=request.POST.get("unit") or "unit", category=category, barcodes=request.POST.get("barcodes") == "1", user=request.user,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(f"{reverse('variants:index')}?lang={lang}")
        messages.success(request, words["created"].format(n=len(created)))
        return redirect(f"{reverse('variants:group', args=[group.pk])}?lang={lang}")
    query = (request.GET.get("q") or "").strip()
    groups = VariantGroup.objects.filter(active=True).annotate(item_count=Count("variants")).order_by("code")
    if query:
        groups = groups.filter(Q(code__icontains=query) | Q(name__icontains=query))
    page = Paginator(groups, 50).get_page(request.GET.get("page"))
    return render(request, "variants/index.html", _context(request, page=page, q=query, categories=Category.objects.filter(active=True).order_by("category_code")))


@require_permission("master_data.view_master_data")
def group_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    group = get_object_or_404(VariantGroup, pk=pk)
    if request.method == "POST":
        if not user_has_permission(request.user, "master_data.manage_items"):
            raise PermissionDenied("Changing variant items needs master_data.manage_items.")
        try:
            if request.POST.get("action") == "prices":
                count = set_group_prices(group, sale_price=_price(request.POST.get("sale_price"), words), purchase_price=_price(request.POST.get("purchase_price"), words), user=request.user)
                messages.success(request, words["priced"].format(n=count))
            else:
                created = extend_group(group, sizes=parse_values(request.POST.get("sizes")), colors=parse_values(request.POST.get("colors")),
                                       barcodes=request.POST.get("barcodes") == "1", user=request.user)
                messages.success(request, words["added"].format(n=len(created)) if created else words["nothing_added"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{reverse('variants:group', args=[group.pk])}?lang={lang}")
    return render(request, "variants/group.html", _context(request, group=group, grid=matrix(group)))
