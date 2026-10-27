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
        "check": "راجع كل فاتورة قبل الترحيل: لو إجمالي المتجر مختلف، صحّح الملف وارفعه تاني مع «بدّل الطلبات اللي لسه مسودة».",
        "shipping_item": "صنف مصاريف الشحن (خدمة)", "no_shipping_item": "من غير — تتكتب في الملاحظات", "with_vat": "أسعار المتجر شاملة الضريبة",
        "replace": "بدّل الطلبات اللي اتسجلت قبل كده ولسه مسودة", "replaced": "اتبدّلت",
        "pull": "اسحب الطلبات من المتجر", "pull_intro": "حساب المتجر متربط: طلبات آخر 7 أيام بتتسحب على طول وتبقى فواتير مسودة، والطلب مش بيدخل مرتين.",
        "pull_shopify": "اسحب من Shopify", "pull_woocommerce": "اسحب من WooCommerce",
        "more": "المتجر فيه طلبات أكتر من اللي بيتسحب مرة واحدة؛ اللي اتسحب دخل. دوس «كمّل السحب» عشان الباقي يدخل من مكان ما وقفنا.",
        "pull_more": "كمّل السحب",
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
        "check": "Check each invoice before posting: if the store's total differs, correct the file and upload it again with “Replace orders still in draft”.",
        "shipping_item": "Shipping charge item (a service)", "no_shipping_item": "None — noted on the invoice", "with_vat": "Store prices include VAT",
        "replace": "Replace orders imported before that are still drafts", "replaced": "Replaced",
        "pull": "Pull the orders from the store", "pull_intro": "The store's account is connected: the last 7 days' orders come straight in as draft invoices, each once.",
        "pull_shopify": "Pull from Shopify", "pull_woocommerce": "Pull from WooCommerce",
        "more": "The store has more orders than one pull reads; those read are in. Press “Carry on pulling” for the rest, from where this pull stopped.",
        "pull_more": "Carry on pulling",
    },
}


def _stores():
    from . import connect

    return connect.connected()


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
            store = request.POST.get("pull", "")
            shipping_item = services.shipping_items().filter(pk=request.POST.get("shipping_item") or 0).first()
            if store:  # T13 live: the store's recent orders straight from its API
                from . import connect

                result = connect.pull(store, location, request.user, lang, shipping_item=shipping_item, replace=request.POST.get("replace") == "1",
                                      more=request.POST.get("more", ""))
                # Codex #283: carrying on keeps the same location and shipping item for every page
                result.update(store=store, location=location.pk if location else None, shipping_item=shipping_item.pk if shipping_item else None,
                              replace=request.POST.get("replace") == "1")
            else:
                if upload is None:
                    raise ValidationError(services.MESSAGES[lang]["file"])
                if upload.size > services.MAX_IMPORT_BYTES:
                    raise ValidationError(services.MESSAGES[lang]["big"])
                result = services.import_orders(upload.read(), location, request.user, lang, shipping_item=shipping_item,
                                                with_vat=request.POST.get("with_vat") == "1", replace=request.POST.get("replace") == "1")
        except ValidationError as exc:
            error = " ".join(exc.messages)
        if result is not None:
            result["made_text"] = words["made"].format(n=len(result["made"]))
            if result["again"]:
                result["again_text"] = words["again"].format(numbers=("، " if lang == "ar" else ", ").join(result["again"][:30]))
    return render(request, "onlinestore/board.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "error": error, "result": result,
        # Codex #273: the imported orders are sales (customers, totals): shown only to who may see sales invoices.
        "locations": locations, "can_import": services.can_import(request.user), "shipping_items": services.shipping_items(), "stores": _stores(),
        "recent": services.recent_orders() if user_has_permission(request.user, "sales.view_sales_invoices") else None,
    })
