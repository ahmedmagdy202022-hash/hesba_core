"""UNITS-001 screens: which items have bigger units, and editing one item's units."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from audit.models import AuditEventType, AuditLog
from master_data.models import Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .models import ItemUnit


WORDS = {
    "ar": {
        "page_title": "وحدات القياس", "title": "وحدات القياس",
        "intro": "بيع واشتري بالكرتونة أو العلبة أو الدستة، والمخزون يفضل متسجّل بالوحدة الأساسية للصنف. مثال: كرتونة = 12 قطعة، تشتري 5 كراتين فالمخزون يزيد 60 قطعة.",
        "item": "الصنف", "base": "الوحدة الأساسية", "count": "وحدات إضافية", "edit": "تعديل", "search": "بحث بالكود أو الاسم", "filter": "بحث",
        "name_ar": "اسم الوحدة", "name_en": "بالإنجليزي", "factor": "فيها كام وحدة أساسية", "barcode": "باركود الوحدة", "sale_price": "سعر البيع", "purchase_price": "سعر الشراء",
        "active": "مفعّلة", "save": "حفظ", "saved": "اتحفظت الوحدات.", "bad": "بيانات غير صحيحة في سطر «{name}»: المعامل لازم أكبر من 1 والأسعار أرقام.", "dup": "فيه وحدتين بنفس الاسم لنفس الصنف.",
        "price_hint": "سيب السعر فاضي عشان يتحسب من سعر الوحدة الأساسية × المعامل.", "back": "العودة للوحدات", "view_only": "تقدر تشوف بس؛ التعديل لمسؤول الأصناف.", "prev": "السابق", "next": "التالي",
    },
    "en": {
        "page_title": "Units of measure", "title": "Units of measure",
        "intro": "Sell and buy by the carton, box or dozen while stock stays in the item's base unit. Example: carton = 12 pieces; buying 5 cartons adds 60 pieces.",
        "item": "Item", "base": "Base unit", "count": "Extra units", "edit": "Edit", "search": "Search code or name", "filter": "Search",
        "name_ar": "Unit name", "name_en": "English", "factor": "Base units inside", "barcode": "Unit barcode", "sale_price": "Sale price", "purchase_price": "Purchase price",
        "active": "Active", "save": "Save", "saved": "Units saved.", "bad": "Invalid row “{name}”: the factor must be above 1 and prices must be numbers.", "dup": "Two units of this item have the same name.",
        "price_hint": "Leave a price empty to use the base price × the factor.", "back": "Back to units", "view_only": "You can view; editing is for item managers.", "prev": "Previous", "next": "Next",
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


@require_permission("master_data.view_master_data")
def unit_index(request):
    query = (request.GET.get("q") or "").strip()
    items = Item.objects.filter(active=True).annotate(unit_count=Count("alt_units", filter=Q(alt_units__active=True))).order_by("-unit_count", "item_code")
    if query:
        items = items.filter(Q(item_code__icontains=query) | Q(item_name__icontains=query) | Q(barcode=query))
    page = Paginator(items, 50).get_page(request.GET.get("page"))
    return render(request, "units/index.html", _context(request, page=page, q=query))


def _decimal(raw):
    raw = (raw or "").strip().replace(",", ".")
    return Decimal(raw) if raw else None


@require_permission("master_data.view_master_data")
def item_units(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    item = get_object_or_404(Item, pk=pk)
    existing = list(ItemUnit.objects.filter(item=item).order_by("factor"))
    if request.method == "POST":
        if not user_has_permission(request.user, "master_data.manage_items"):
            raise PermissionDenied("Managing units needs master_data.manage_items.")
        rows = [(str(unit.pk), unit) for unit in existing] + [(f"new{n}", None) for n in range(3)]
        before = [str(unit) for unit in existing]
        try:
            with transaction.atomic():
                for key, unit in rows:
                    name = (request.POST.get(f"name_ar_{key}") or "").strip()[:60]
                    if not name:
                        if unit is not None:
                            unit.active = False
                            unit.save(update_fields=["active"])
                        continue
                    try:
                        factor = _decimal(request.POST.get(f"factor_{key}"))
                        sale, purchase = _decimal(request.POST.get(f"sale_price_{key}")), _decimal(request.POST.get(f"purchase_price_{key}"))
                    except InvalidOperation:
                        factor = None
                    if factor is None or factor <= 1 or (sale is not None and sale < 0) or (purchase is not None and purchase < 0):
                        messages.error(request, words["bad"].format(name=name))
                        raise IntegrityError("invalid row")
                    unit = unit or ItemUnit(item=item)
                    unit.name_ar, unit.name_en = name, (request.POST.get(f"name_en_{key}") or "").strip()[:60]
                    unit.factor, unit.sale_price, unit.purchase_price = factor.quantize(Decimal("0.001")), sale, purchase
                    unit.barcode = (request.POST.get(f"barcode_{key}") or "").strip()[:120]
                    unit.active = request.POST.get(f"active_{key}", "1") == "1"
                    unit.save()
                after = [str(unit) for unit in ItemUnit.objects.filter(item=item, active=True).order_by("factor")]
                AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=request.user, module="units", action="set_item_units", object_type="master_data.Item", object_id=str(item.pk), before_data={"units": before}, after_data={"units": after})
        except IntegrityError as exc:
            if "invalid row" not in str(exc):
                messages.error(request, words["dup"])
        else:
            messages.success(request, words["saved"])
        return redirect(f"{reverse('units:item', args=[item.pk])}?lang={lang}")
    return render(request, "units/item.html", _context(request, item=item, units=existing, new_rows=["new0", "new1", "new2"]))
