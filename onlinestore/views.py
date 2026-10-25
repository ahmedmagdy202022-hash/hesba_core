"""T13 screen: the catalogue out as a store's product CSV, and the store's orders in as draft sales invoices."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import render

from entities import scope as entity_scope
from master_data.models import Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import services

WORDS = {
    "ar": {
        "title": "المتجر الأونلاين", "intro": "لحد ما حساب المتجر يتربط: نزّل الأصناف بملف المتجر نفسه وارفعه هناك، ونزّل الطلبات من المتجر وارفعها هنا تبقى فواتير مسودة تراجعها وترحّلها.",
        "export": "الأصناف للمتجر", "export_intro": "ملف بالأكواد والأسعار والكميات اللي في مخازن البيع دلوقتي. المقاسات والألوان بتطلع منتج واحد باختياراته.",
        "shopify": "ملف Shopify", "woo": "ملف WooCommerce", "orders": "طلبات المتجر",
        "orders_intro": "ارفع ملف الطلبات من Shopify (Orders ← Export) أو قالب حسبة (لـ WooCommerce أو أي متجر تاني). كل طلب بيبقى فاتورة مسودة؛ الطلب مش بيدخل مرتين.",
        "file": "ملف الطلبات (CSV)", "location": "المخزن اللي الطلبات هتطلع منه", "upload": "ارفع الطلبات", "template": "نزّل القالب",
        "made": "اتعمل {n} فاتورة مسودة.", "again": "طلبات اتسجلت قبل كده ومادخلتش تاني: {numbers}", "skipped": "طلبات مادخلتش",
        "order": "الطلب", "invoice": "الفاتورة", "customer": "العميل", "total": "إجمالي الفاتورة", "store_total": "إجمالي المتجر", "reason": "السبب",
        "new_customer": "عميل جديد", "recent": "آخر الطلبات اللي دخلت", "status": "الحالة", "none": "لسه مفيش.",
        "check": "راجع كل فاتورة: لو إجمالي المتجر مختلف (مصاريف شحن أو ضريبة جوه السعر)، عدّلها قبل الترحيل.",
    },
    "en": {
        "title": "Online store", "intro": "Until a store account is linked: download the items in the store's own file and upload it there, and download the orders from the store and upload them here as draft invoices to check and post.",
        "export": "Items for the store", "export_intro": "A file with the codes, prices and what the selling locations hold now. Sizes and colours come out as one product with its options.",
        "shopify": "Shopify file", "woo": "WooCommerce file", "orders": "Store orders",
        "orders_intro": "Upload Shopify's orders export (Orders → Export) or the Hesba template (for WooCommerce or another store). Each order becomes a draft invoice; an order never comes in twice.",
        "file": "Orders file (CSV)", "location": "Location the orders leave from", "upload": "Upload the orders", "template": "Download the template",
        "made": "{n} draft invoices made.", "again": "Orders imported before and not again: {numbers}", "skipped": "Orders not imported",
        "order": "Order", "invoice": "Invoice", "customer": "Customer", "total": "Invoice total", "store_total": "Store total", "reason": "Reason",
        "new_customer": "New customer", "recent": "Latest orders imported", "status": "Status", "none": "None yet.",
        "check": "Check each invoice: if the store's total differs (shipping, or tax inside the price), correct it before posting.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _download(text, name, content_type="text/csv; charset=utf-8"):
    response = HttpResponse(text, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@require_permission("master_data.view_master_data")
def board(request):
    lang = _lang(request)
    words = WORDS[lang]
    kind = request.GET.get("export", "")
    if kind in services.KINDS:
        return _download(services.export_csv(kind, request.user), f"hesba-{kind}-products.csv")
    if request.GET.get("template") == "1":
        return _download(services.template_csv(), "hesba-store-orders-template.csv")
    locations = entity_scope.locations(Location.objects.filter(active=True, is_selling_location=True))
    error, result = "", None
    if request.method == "POST":
        if not services.can_import(request.user):
            raise PermissionDenied("Importing store orders needs sales.create_sales_invoice.")
        upload = request.FILES.get("file")
        location = locations.filter(pk=request.POST.get("location") or 0).first()
        try:
            if upload is None:
                raise ValidationError(services.MESSAGES[lang]["file"])
            if upload.size > services.MAX_IMPORT_BYTES:
                raise ValidationError(services.MESSAGES[lang]["big"])
            result = services.import_orders(upload.read(), location, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            result["made_text"] = words["made"].format(n=len(result["made"]))
            if result["again"]:
                result["again_text"] = words["again"].format(numbers=("، " if lang == "ar" else ", ").join(result["again"][:30]))
    return render(request, "onlinestore/board.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "error": error, "result": result,
        # Codex #273: the imported orders are sales (customers, totals): shown only to who may see sales invoices.
        "locations": locations, "can_import": services.can_import(request.user),
        "recent": services.recent_orders() if user_has_permission(request.user, "sales.view_sales_invoices") else None,
    })
