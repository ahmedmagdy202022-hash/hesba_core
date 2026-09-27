"""SERIAL-001 screens: look up a serial, units in stock, register, return, retire; item tracking."""

from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from master_data.models import Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from sales.models import SalesReturnLine

from .models import SerialNumber, SerialSetting
from .services import available_serials, history, normalize, record_return, register_serials, retire_serial, state_of


WORDS = {
    "ar": {
        "page_title": "السيريال والضمان", "title": "السيريال / IMEI والضمان",
        "intro": "كل قطعة برقمها: اتشرت من مين وامتى، اتباعت لمين وامتى، والضمان لحد امتى. السيريال بيدخل مع فاتورة الشراء وبيخرج مع البيع أو الكاشير.",
        "lookup": "ابحث بالسيريال أو IMEI", "find": "بحث", "not_found": "مفيش سيريال بالرقم ده.", "in_stock_list": "القطع الموجودة في المخزون", "item": "الصنف",
        "serial": "السيريال", "state": "الحالة", "received": "تاريخ الدخول", "open": "تفاصيل", "empty": "مفيش قطع في المخزون.", "all_items": "كل الأصناف",
        "states": {"in_stock": "في المخزون", "sold": "مباع", "pending": "على فاتورة شراء مسودة", "void": "فاتورة الشراء اتلغت", "retired": "خارج المتابعة"},
        "register": "تسجيل سيريالات لقطع موجودة", "item_code": "كود الصنف أو الباركود", "serials": "السيريالات (سطر أو فاصلة بين كل واحد)", "save": "تسجيل",
        "registered": "اتسجل {n} سيريال.", "bad_item": "الصنف مش موجود أو مش متتبع بالسيريال.", "bad_date": "التاريخ مش صحيح.",
        "settings": "أصناف السيريال والضمان", "back": "السيريال والضمان", "purchase": "الشراء", "sales": "البيع", "returns": "المرتجعات", "warranty": "الضمان",
        "warranty_until": "ساري لحد {date}", "warranty_over": "انتهى في {date}", "no_warranty": "مفيش ضمان متسجل للصنف.", "months": "شهر",
        "retire": "إخراج من المتابعة", "reason": "السبب (رجع للمورد، تالف...)", "retired": "السيريال اتشال من المتابعة.",
        "return": "تسجيل رجوعه من العميل", "return_line": "مرتجع البيع", "returned": "اتسجل رجوع السيريال للمخزون.", "no_return": "مفيش مرتجع مرحّل على الفاتورة دي لسه؛ اعمل مرتجع البيع الأول.",
        "manual": "مسجل يدويًا", "view_only": "تقدر تشوف بس؛ التعديل لصاحب الصلاحية.", "prev": "السابق", "next": "التالي",
        "tracked": "متتبع بالسيريال", "warranty_months": "الضمان بالشهور", "search": "بحث بالكود أو الاسم", "saved": "اتحفظ.",
    },
    "en": {
        "page_title": "Serials & warranty", "title": "Serial / IMEI & warranty",
        "intro": "Every unit by its number: bought from whom and when, sold to whom and when, and its warranty. Serials come in on purchase invoices and go out on sales and at the till.",
        "lookup": "Search a serial or IMEI", "find": "Search", "not_found": "No serial with that number.", "in_stock_list": "Units in stock", "item": "Item",
        "serial": "Serial", "state": "State", "received": "Received", "open": "Details", "empty": "No units in stock.", "all_items": "All items",
        "states": {"in_stock": "In stock", "sold": "Sold", "pending": "On a draft purchase", "void": "Purchase cancelled", "retired": "Out of tracking"},
        "register": "Register serials for units on hand", "item_code": "Item code or barcode", "serials": "Serials (one per line or comma separated)", "save": "Register",
        "registered": "{n} serials registered.", "bad_item": "No such item, or it is not tracked by serial.", "bad_date": "Invalid date.",
        "settings": "Serial items & warranty", "back": "Serials & warranty", "purchase": "Purchase", "sales": "Sales", "returns": "Returns", "warranty": "Warranty",
        "warranty_until": "Valid until {date}", "warranty_over": "Ended on {date}", "no_warranty": "No warranty set for this item.", "months": "months",
        "retire": "Take out of tracking", "reason": "Reason (returned to supplier, damaged...)", "retired": "Serial taken out of tracking.",
        "return": "Record it back from the customer", "return_line": "Sales return", "returned": "Serial recorded back in stock.", "no_return": "No posted return on that invoice yet; make the sales return first.",
        "manual": "Registered by hand", "view_only": "You can view; changes need the right permission.", "prev": "Previous", "next": "Next",
        "tracked": "Tracked by serial", "warranty_months": "Warranty (months)", "search": "Search code or name", "saved": "Saved.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "inventory",
               "can_manage": user_has_permission(request.user, "inventory.adjust_stock")}
    context.update(extra)
    return context


@require_permission("inventory.view_stock")
def serial_index(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST":
        if not user_has_permission(request.user, "inventory.adjust_stock"):
            raise PermissionDenied("Registering serials needs inventory.adjust_stock.")
        code = (request.POST.get("item_code") or "").strip()
        item = Item.objects.filter(active=True, serial_setting__tracked=True).filter(Q(item_code=code) | Q(barcode=code)).first() if code else None
        try:
            if item is None:
                raise ValidationError(words["bad_item"])
            try:
                received = date.fromisoformat(request.POST.get("received_on") or "") if request.POST.get("received_on") else timezone.localdate()
            except ValueError:
                raise ValidationError(words["bad_date"])
            created = register_serials(item, request.POST.get("serials", ""), received, request.user, lang, request.POST.get("note", ""))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, words["registered"].format(n=len(created)))
        return redirect(f"{reverse('serials:index')}?lang={lang}")

    query = normalize(request.GET.get("q"))
    if query:
        found = list(SerialNumber.objects.filter(serial__icontains=query).select_related("item").order_by("serial")[:20])
        if len(found) == 1:
            return redirect(f"{reverse('serials:detail', args=[found[0].pk])}?lang={lang}")
        for row in found:
            row.state = state_of(row)
            row.state_label = words["states"][row.state]
    else:
        found = None
    item_filter = request.GET.get("item") or ""
    stock = available_serials().select_related("item").order_by("item__item_code", "serial")
    if item_filter.isdigit():
        stock = stock.filter(item_id=item_filter)
    page = Paginator(stock, 50).get_page(request.GET.get("page"))
    tracked_items = Item.objects.filter(serial_setting__tracked=True).order_by("item_code")
    return render(request, "serials/index.html", _context(request, q=request.GET.get("q", ""), found=found, page=page, tracked_items=tracked_items,
                                                          item_filter=item_filter, today=timezone.localdate()))


@require_permission("inventory.view_stock")
def serial_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    serial = get_object_or_404(SerialNumber.objects.select_related("item", "purchase_line__invoice__supplier"), pk=pk)
    info = history(serial)
    return_lines = []
    if info["state"] == "sold" and info["last_sale"]:
        return_lines = list(SalesReturnLine.objects.filter(sales_return__status="posted", source_line__serials__serial=serial,
                                                           source_line__invoice=info["last_sale"]).select_related("sales_return").distinct())
    if request.method == "POST":
        if not user_has_permission(request.user, "inventory.adjust_stock"):
            raise PermissionDenied("Changing serials needs inventory.adjust_stock.")
        try:
            if request.POST.get("action") == "retire":
                retire_serial(serial, request.user, request.POST.get("reason", ""))
                messages.success(request, words["retired"])
            elif request.POST.get("action") == "return":
                line = next((row for row in return_lines if str(row.pk) == request.POST.get("return_line")), None)
                if line is None:
                    raise ValidationError(words["no_return"])
                record_return(serial, line, request.user)
                messages.success(request, words["returned"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{reverse('serials:detail', args=[serial.pk])}?lang={lang}")
    today = timezone.localdate()
    warranty_text = ""
    if info["warranty_until"]:
        key = "warranty_until" if info["warranty_until"] >= today else "warranty_over"
        warranty_text = words[key].format(date=info["warranty_until"].isoformat())
    return render(request, "serials/detail.html", _context(request, info=info, serial=serial, return_lines=return_lines, warranty_text=warranty_text,
                                                           state_label=words["states"][info["state"]]))


@require_permission("master_data.view_master_data")
def item_settings(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = user_has_permission(request.user, "master_data.manage_items")
    query = (request.GET.get("q") or request.POST.get("q") or "").strip()
    items = Item.objects.filter(active=True).order_by("item_code")
    if query:
        items = items.filter(Q(item_code__icontains=query) | Q(item_name__icontains=query) | Q(barcode=query))
    page = Paginator(items, 40).get_page(request.GET.get("page") or request.POST.get("page"))
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Setting serial tracking needs master_data.manage_items.")
        changed = {}
        with transaction.atomic():
            for item in page.object_list:
                if f"shown_{item.pk}" not in request.POST:
                    continue
                tracked = request.POST.get(f"tracked_{item.pk}") == "1"
                try:
                    months = max(0, min(int(request.POST.get(f"months_{item.pk}") or 0), 240))
                except ValueError:
                    months = 0
                current = SerialSetting.objects.filter(item=item).first()
                before = [current.tracked, current.warranty_months] if current else None
                if before != [tracked, months] and (current or tracked or months):
                    SerialSetting.objects.update_or_create(item=item, defaults={"tracked": tracked, "warranty_months": months})
                    changed[item.item_code] = [before, [tracked, months]]
            if changed:
                AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=request.user, module="serials", action="set_serial_items", object_type="serials.SerialSetting",
                                        object_id="items", before_data={k: v[0] for k, v in changed.items()}, after_data={k: v[1] for k, v in changed.items()})
        messages.success(request, words["saved"])
        return redirect(f"{reverse('serials:items')}?lang={lang}&q={query}&page={page.number}")
    settings = {row.item_id: row for row in SerialSetting.objects.filter(item__in=page.object_list)}
    rows = [{"item": item, "setting": settings.get(item.pk)} for item in page.object_list]
    return render(request, "serials/items.html", _context(request, rows=rows, page=page, q=query, can_manage=can_manage, section="items"))
