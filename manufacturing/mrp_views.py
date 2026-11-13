"""F2: the material requirements screen, ``/manufacturing/mrp/``, and its two actions.

One view. The screen needs ``inventory.view_stock`` (the same as the manufacturing board). «اعمل أمر شراء» needs
``purchases.create_purchase_invoice`` and the purchases module; «اعمل أمر إنتاج» needs ``inventory.adjust_stock``. Both read a fresh
calculation on the server, so a stale page cannot order what is no longer short."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Supplier
from orders import purchasing as po
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import mrp
from .models import OrderStatus, ProductionOrder
from .views import COST, PRODUCE, VIEW, _lang
from entities import scope as entity_scope

WORDS = {
    "ar": {
        "title": "احتياج الخامات", "intro": "بنحسب الخامات اللي ناقصة عشان أوامر الإنتاج المخططة وأوامر البيع المؤكدة والحد الأدنى للمخزون، وتعمل منها أوامر شراء مسودة بضغطة واحدة.",
        "board": "لوحة التصنيع", "sources": "إيه اللي بيحسب عليه الاحتياج", "tile_sources": "أوامر محسوبة", "tile_buy": "خامات ناقصة", "tile_make": "لازم تتصنع",
        "planned": "أمر إنتاج لسه مبدأش", "sale": "أمر بيع مؤكد", "source": "المصدر", "product": "المنتج", "quantity": "الكمية", "date": "الميعاد",
        "party": "العميل", "stock_covers": "المخزون غطى", "to_make": "يتصنع",
        "empty": "مفيش احتياج خامات دلوقتي: مفيش أوامر إنتاج مخططة ولا أوامر بيع مؤكدة محتاجة تصنيع، وكل خامة فوق حدها الأدنى.",
        "materials": "الخامات المطلوب شراؤها", "material": "الخامة", "item": "الصنف", "need": "المطلوب", "on_hand": "الموجود", "incoming": "جاي", "minimum": "الحد الأدنى",
        "to_buy": "ناقص", "enough": "مكفي", "last_price": "آخر سعر", "supplier": "المورد", "buy_qty": "الكمية", "pick": "اختار", "pick_supplier": "اختار المورد",
        "buy": "اعمل أمر شراء", "buy_hint": "هيتعمل أمر شراء مسودة لكل مورد للخامات اللي علّمت عليها. المسودة مبتسجلش فاتورة ولا بتحرّك مخزون ولا نقدية، وتعدّلها وتبعتها للمورد من شاشة أوامر الشراء.",
        "draft_note": "أمر الشراء المسودة {numbers} بيطلبها بالفعل.", "sent_note": "أمر الشراء {numbers} اتبعت للمورد ولسه ما اتأكدش.", "po_word": "أمر الشراء", "order_word": "أمر الإنتاج",
        "no_buy_right": "عشان تعمل أمر شراء محتاج صلاحية فواتير الشراء.", "purchases_off": "وحدة المشتريات مقفولة، فمفيش أوامر شراء.",
        "bought": "اتعملت أوامر الشراء المسودة:", "semis": "الأصناف نصف المصنّعة", "semis_hint": "كل صنف بيتحسب مرة واحدة على مخزونه، وبعدين بيتقرّب لأقرب دفعة كاملة من وصفته وبيتفتح لمكوّناته.",
        "recipe": "الوصفة", "net": "ناقص", "batches": "دفعات", "make_qty": "هيتصنع", "make": "اعمل أمر إنتاج", "location": "مكان الإنتاج", "labor": "تكلفة العمالة",
        "overhead": "مصاريف تشغيل", "made": "اتفتحت أوامر الإنتاج:", "making_note": "أمر إنتاج شغال: {qty}", "reserved_note": "محجوز لأوامر بيع: {qty}", "no_make_right": "عشان تعمل أمر إنتاج محتاج صلاحية تسوية المخزون.",
        "warn_loop": "وصفة «{item}» بتدور على نفسها، فمفتحناش مكوّناتها. صلّح الوصفة.", "warn_depth": "«{item}» عمقه فوق الحد (10 مستويات)، فمفتحناش مكوّناته.",
        "open_order": "افتح", "open_po": "افتح أمر الشراء", "back": "التصنيع",
    },
    "en": {
        "title": "Material requirements", "intro": "The materials that are short for the planned production orders, the confirmed sales orders and the minimum stock, turned into draft purchase orders in one tap.",
        "board": "Manufacturing board", "sources": "What the requirements are based on", "tile_sources": "Orders counted", "tile_buy": "Materials short", "tile_make": "To make",
        "planned": "Production order not started", "sale": "Confirmed sales order", "source": "Source", "product": "Product", "quantity": "Quantity", "date": "Date",
        "party": "Customer", "stock_covers": "Stock covers", "to_make": "To make",
        "empty": "No material requirements now: no planned production orders, no confirmed sales orders that need making, and every material is above its minimum.",
        "materials": "Materials to buy", "material": "Material", "item": "Item", "need": "Needed", "on_hand": "On hand", "incoming": "Coming", "minimum": "Minimum",
        "to_buy": "To buy", "enough": "Covered", "last_price": "Last price", "supplier": "Supplier", "buy_qty": "Quantity", "pick": "Pick", "pick_supplier": "Choose the supplier",
        "buy": "Make a purchase order", "buy_hint": "One draft purchase order per supplier is made for the materials you ticked. A draft posts no invoice and moves no stock or money; edit it and send it to the supplier from the purchase orders screen.",
        "draft_note": "Draft purchase order {numbers} already asks for it.", "sent_note": "Purchase order {numbers} was sent and is not confirmed yet.", "po_word": "Purchase order", "order_word": "Production order",
        "no_buy_right": "Making a purchase order needs the purchase invoices permission.", "purchases_off": "The purchases module is off, so there are no purchase orders.",
        "bought": "Draft purchase orders made:", "semis": "Semi-finished items", "semis_hint": "Each item is netted once against its own stock, then rounded up to whole batches of its recipe and opened into its components.",
        "recipe": "Recipe", "net": "Short", "batches": "Batches", "make_qty": "To make", "make": "Make a production order", "location": "Made at", "labor": "Labour cost",
        "overhead": "Overhead", "made": "Production orders opened:", "making_note": "A production order is open: {qty}", "reserved_note": "Held for sales orders: {qty}", "no_make_right": "Making a production order needs the adjust-stock permission.",
        "warn_loop": "The recipe of “{item}” goes round in a circle, so its components were not opened. Fix the recipe.", "warn_depth": "“{item}” is deeper than the limit (10 levels), so its components were not opened.",
        "open_order": "Open", "open_po": "Open the purchase order", "back": "Manufacturing",
    },
}

BUY, ISSUE = "purchases.create_purchase_invoice", "inventory.adjust_stock"


def _ids(raw):
    return [int(part) for part in str(raw or "").split(",") if part.strip().isdigit()][:50]


def _buy_picks(request):
    picks = []
    for raw in request.POST.getlist("pick"):
        if not raw.isdigit():
            continue
        supplier = request.POST.get(f"supplier_{raw}", "")
        picks.append({"item": int(raw), "quantity": request.POST.get(f"qty_{raw}", ""),
                      "supplier": Supplier.objects.filter(pk=supplier, active=True).first() if supplier.isdigit() else None})
    return picks


def _act(request, lang):
    """The POST of either action: the address to go back to (with what was made), or None after an error."""

    action = request.POST.get("action", "")
    back = reverse("manufacturing:mrp")
    if action == "purchase":
        if not po.enabled():
            raise Http404("Purchases are switched off.")
        if not user_has_permission(request.user, BUY):
            raise PermissionDenied("Purchase orders need purchases.create_purchase_invoice.")
        made = mrp.make_purchase_orders(_buy_picks(request), request.user, lang)
        return f"{back}?lang={lang}&bought={','.join(str(order.pk) for order, _ in made)}"
    if action == "produce":
        if not user_has_permission(request.user, ISSUE):
            raise PermissionDenied("Production orders need inventory.adjust_stock.")
        item = request.POST.get("item", "")
        order = mrp.make_production_order(int(item) if item.isdigit() else 0, request.user, request.POST, lang)
        return f"{back}?lang={lang}&made={order.pk}"
    raise Http404("Unknown action.")


@require_permission(VIEW)
def screen(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST":
        try:
            return redirect(_act(request, lang))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(f"{reverse('manufacturing:mrp')}?lang={lang}")
    result = mrp.calculate()
    can_buy = po.enabled() and user_has_permission(request.user, BUY)
    can_make = user_has_permission(request.user, ISSUE)
    can_price = user_has_permission(request.user, COST) or user_has_permission(request.user, BUY)
    open_po = po.can_view(request.user)
    for warning in result["warnings"]:
        warning["text"] = words["warn_loop" if warning["kind"] == "loop" else "warn_depth"].format(item=warning["item"].item_name)
    for row in result["materials"] + result["semis"]:
        row["reserved_text"] = words["reserved_note"].format(qty=mrp.text(row["reserved"])) if row["reserved"] > 0 else ""
    for row in result["materials"]:
        drafts = ", ".join(d["number"] for d in row["drafts"] if d["status"] == "draft")
        sent = ", ".join(d["number"] for d in row["drafts"] if d["status"] == "sent")
        row["draft_text"] = words["draft_note"].format(numbers=drafts) if drafts else ""
        row["sent_text"] = words["sent_note"].format(numbers=sent) if sent else ""
        row["qty_plain"] = mrp.plain(row["to_buy"]) if row["to_buy"] > 0 else ""
    for row in result["semis"]:
        row["qty_plain"] = mrp.plain(row["make"]) if row["make"] > 0 else ""
        row["making_text"] = words["making_note"].format(qty=mrp.text(row["making"])) if row["making"] > 0 else ""
    # what was just made is shown only to someone who may make or open purchase orders (the screen itself needs only stock view)
    bought = ([order for order in po.scoped().filter(pk__in=_ids(request.GET.get("bought"))).select_related("supplier").order_by("pk")]
              if (can_buy or open_po) else [])
    for order in bought:
        order.mrp_lines = list(order.lines.select_related("item").order_by("pk"))
        order.mrp_total = po.totals(order)["total"]
    made = list(entity_scope.scope(ProductionOrder.objects, "location__entity").filter(pk__in=_ids(request.GET.get("made")))
                .select_related("recipe__product").order_by("pk"))
    locations = mrp.places()
    return render(request, "manufacturing/mrp.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "result": result, "today": timezone.localdate(),
        "can_buy": can_buy, "can_make": can_make, "can_price": can_price, "purchases_on": po.enabled(), "open_po": open_po, "bought": bought, "made": made,
        "suppliers": Supplier.objects.filter(active=True).order_by("name")[:500], "locations": locations, "default_location": locations.first(),
        "can_cost": user_has_permission(request.user, COST), "can_recipes": user_has_permission(request.user, "master_data.manage_items"),
        "can_produce": can_make})
