"""Manufacturing F3 (HG-131): the lot trace screen, ``/manufacturing/trace/?lot=<id>``. Reading only."""

from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from batches.models import Batch
from batches.services import batch_positions, counted_batches, expiry_state
from entities import scope as entity_scope
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import lots, trace
from .models import ProductionRun
from .views import VIEW, _lang

SEE_PURCHASES, SEE_SALES = "purchases.view_purchase_invoices", "sales.view_sales_invoices"
WORDS = {
    "ar": {
        "page_title": "تتبّع التشغيلة", "title": "تتبّع التشغيلة", "intro": "اكتب رقم تشغيلة أو اسم صنف: من تشغيلة المنتج لخاماتها وفواتير شرائها، ومن تشغيلة الخامة للتشغيلات اللي دخلت فيها والعملاء اللي اشتروا المنتج.",
        "search": "رقم التشغيلة أو اسم الصنف أو رقم الإنتاج", "find": "بحث", "back": "التصنيع", "latest": "آخر التشغيلات", "none": "مفيش تشغيلات مطابقة.",
        "item": "الصنف", "lot": "رقم التشغيلة", "expiry": "تاريخ الصلاحية", "qty": "الكمية", "location": "الموقع", "source": "المصدر", "left": "الباقي",
        "from_run": "من تشغيلة إنتاج", "from_purchase": "من فاتورة شراء", "manual": "مسجلة يدويًا", "no_lot": "من غير تشغيلة", "not_counted": "التشغيلة دي مش بتتعدّ في المخزون (التشغيلة اتلغت).",
        "backward": "الخامات اللي دخلت في التشغيلة دي", "backward_hint": "التشغيلات اتسجلت وقت صرف الخامات، الأقرب انتهاءً الأول.", "no_backward": "التشغيلة دي مش من إنتاج؛ مفيش خامات وراها.",
        "component": "الخامة", "invoice": "فاتورة الشراء", "supplier": "المورد", "run": "التشغيلة", "order": "أمر الإنتاج",
        "forward": "راح فين", "forward_label": "حسب أقرب انتهاء", "forward_hint": "المبيعات مبتسجّلش التشغيلة، فالقايمة دي كل مبيعات المنتج من نفس المخزن من يوم التشغيلة لحد ما التشغيلة تخلص على أساس إن الأقرب انتهاءً بيتباع الأول.",
        "product_lot": "تشغيلة المنتج", "customer": "العميل", "sale": "فاتورة البيع", "sold_qty": "الكمية المباعة", "date": "التاريخ", "no_sales": "مفيش مبيعات للمنتج ده في الفترة دي.",
        "window_from": "من", "until_date": "لحد", "until_open": "والتشغيلة لسه فيها رصيد", "open_order": "أمر شغّال لسه متخلصش", "no_forward": "مفيش تشغيلات استخدمت التشغيلة دي.",
        "hide_purchases": "الموردين وفواتير الشراء بتظهر لصاحب صلاحية فواتير الشراء بس.", "hide_sales": "العملاء وفواتير البيع بيظهروا لصاحب صلاحية فواتير البيع بس.",
        "trace_this": "تتبّع", "open_run": "افتح التشغيلة", "status": "الحالة", "expired": "منتهية", "soon": "قربت تنتهي", "ok": "سليمة", "nodate": "بدون تاريخ",
    },
    "en": {
        "page_title": "Lot trace", "title": "Lot trace", "intro": "Type a lot number or an item name: from a product lot to its materials and purchase invoices, and from a material's lot to the runs it went into and the customers who bought the product.",
        "search": "Lot number, item name or run number", "find": "Search", "back": "Manufacturing", "latest": "Latest lots", "none": "No matching lots.",
        "item": "Item", "lot": "Lot", "expiry": "Expiry date", "qty": "Quantity", "location": "Location", "source": "Source", "left": "Left",
        "from_run": "From a production run", "from_purchase": "From a purchase", "manual": "Registered by hand", "no_lot": "No lot", "not_counted": "This lot no longer counts in stock (its run was cancelled).",
        "backward": "Materials that went into this lot", "backward_hint": "Lots were recorded when the materials were issued, earliest expiry first.", "no_backward": "This lot is not from a run; there are no materials behind it.",
        "component": "Material", "invoice": "Purchase invoice", "supplier": "Supplier", "run": "Run", "order": "Production order",
        "forward": "Where it went", "forward_label": "By earliest expiry", "forward_hint": "Sales do not record a lot, so this lists every sale of the product from the same location from the run until the lot is used up, taking the earliest-expiring lot to be sold first.",
        "product_lot": "Product lot", "customer": "Customer", "sale": "Sales invoice", "sold_qty": "Quantity sold", "date": "Date", "no_sales": "No sales of this product in that period.",
        "window_from": "From", "until_date": "until", "until_open": "and the lot still has stock", "open_order": "An order on the floor, not finished yet", "no_forward": "No run has used this lot.",
        "hide_purchases": "Suppliers and purchase invoices show only to a holder of the purchase invoices permission.", "hide_sales": "Customers and sales invoices show only to a holder of the sales invoices permission.",
        "trace_this": "Trace", "open_run": "Open the run", "status": "State", "expired": "Expired", "soon": "Expiring soon", "ok": "Fine", "nodate": "No date",
    },
}
SEARCH_LIMIT = 50


def _lots(request):
    """Every active lot in the working entity (another entity's lot is a 404)."""

    return entity_scope.scope(Batch.objects.filter(active=True).select_related("item", "location", "production_run", "purchase_line__invoice__supplier"), "location__entity")


@require_permission(VIEW)
def trace_screen(request):
    lang = _lang(request)
    words = WORDS[lang]
    lot_raw, run_raw, query = request.GET.get("lot", "").strip(), request.GET.get("run", "").strip(), request.GET.get("q", "").strip()
    scoped = _lots(request)
    lot = None
    if lot_raw.isdigit():
        lot = get_object_or_404(scoped, pk=int(lot_raw))
    elif run_raw.isdigit():
        run = get_object_or_404(entity_scope.scope(ProductionRun.objects, "location__entity"), pk=int(run_raw))
        lot = scoped.filter(production_run=run).first()
    elif lot_raw:
        query = query or lot_raw
    can_purchases, can_sales = user_has_permission(request.user, SEE_PURCHASES), user_has_permission(request.user, SEE_SALES)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"], "q": query, "lot": lot,
               "can_purchases": can_purchases, "can_sales": can_sales, "section": "inventory"}
    if lot is None:
        found = scoped
        if query:
            found = found.filter(Q(batch_no__icontains=query) | Q(item__item_name__icontains=query) | Q(item__item_code__icontains=query)
                                 | Q(production_run__number__icontains=query))
        context["found"] = list(found.order_by("-pk")[:SEARCH_LIMIT])
        return render(request, "manufacturing/trace.html", context)
    today = timezone.localdate()
    rows, _uncovered = batch_positions(items=[lot.item], locations=[lot.location])
    left = next((row["remaining"] for row in rows if row["batch"].pk == lot.pk), None)
    counted = counted_batches().filter(pk=lot.pk).exists()
    context.update({
        "counted": counted, "left": left, "state": expiry_state(lot.expiry_date, today, 60) if counted else "",
        "source_kind": trace.source_of(lot)[0], "backward": trace.backward(lot), "purchase": trace.purchase_of(lot),
        "paths": trace.forward(lot), "lot_name": lots.lot_name(lot),
    })
    return render(request, "manufacturing/trace.html", context)
