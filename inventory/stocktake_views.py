"""STOCKTAKE-001: the count screen. Sheet → review differences → post."""

from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone

from master_data.models import Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.ui_messages import translate

from .stocktake import SHEET_LIMIT, count_sheet, parse_counts, post_stock_count, sheet_items, variance_totals, variances


WORDS = {
    "ar": {
        "title": "جرد المخزون",
        "intro": "اختار المخزن، اكتب الكمية اللي اتعدّت فعلاً قدام كل صنف، وراجع الفروق قبل الترحيل. الخانة الفاضية معناها إن الصنف ما اتعدّش ومش هيتغير.",
        "back": "العودة للمخزون",
        "location": "المخزن",
        "search": "بحث بالكود أو الاسم أو الباركود",
        "show": "عرض الأصناف",
        "date": "تاريخ الجرد",
        "note": "ملاحظة (اختياري)",
        "code": "الكود",
        "item": "الصنف",
        "unit": "الوحدة",
        "system": "رصيد النظام",
        "counted": "المعدود",
        "diff": "الفرق",
        "value": "قيمة الفرق",
        "review": "مراجعة الفروق",
        "post": "ترحيل الجرد",
        "edit": "تعديل العدّ",
        "print": "طباعة كشف الجرد",
        "too_many": "ظاهر أول {n} صنف بس. ضيّق البحث عشان تجرد الباقي على دفعات.",
        "empty": "مفيش أصناف مخزنية مطابقة.",
        "bad": "كميات غير صحيحة للأصناف: {codes}",
        "none": "اكتب كمية معدودة لصنف واحد على الأقل.",
        "counted_n": "أصناف معدودة",
        "changed_n": "أصناف بها فرق",
        "gain": "زيادة",
        "loss": "عجز",
        "net": "صافي الفرق",
        "no_diff": "كل الكميات المعدودة مطابقة للنظام، مفيش حاجة تترحّل.",
        "posted": "تم ترحيل الجرد {number}: {n} تسوية.",
        "matched": "الجرد مطابق، مفيش تسويات.",
        "signature": "العدّ بواسطة: ____________    المراجعة: ____________",
    },
    "en": {
        "title": "Stock count",
        "intro": "Pick the location, type the quantity actually counted for each item, and review the differences before posting. A blank box means the item was not counted and stays as it is.",
        "back": "Back to inventory",
        "location": "Location",
        "search": "Search code, name or barcode",
        "show": "Show items",
        "date": "Count date",
        "note": "Note (optional)",
        "code": "Code",
        "item": "Item",
        "unit": "Unit",
        "system": "System qty",
        "counted": "Counted",
        "diff": "Difference",
        "value": "Difference value",
        "review": "Review differences",
        "post": "Post count",
        "edit": "Edit counts",
        "print": "Print count sheet",
        "too_many": "Only the first {n} items are shown. Narrow the search to count the rest in batches.",
        "empty": "No matching stock items.",
        "bad": "Invalid quantities for: {codes}",
        "none": "Enter a counted quantity for at least one item.",
        "counted_n": "Items counted",
        "changed_n": "Items with a difference",
        "gain": "Surplus",
        "loss": "Shortage",
        "net": "Net difference",
        "no_diff": "Every counted quantity matches the system; nothing to post.",
        "posted": "Count {number} posted: {n} adjustments.",
        "matched": "The count matches; no adjustments.",
        "signature": "Counted by: ____________    Checked by: ____________",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _date(raw):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return timezone.localdate()


@require_permission("inventory.adjust_stock")
def stocktake(request):
    lang = _lang(request)
    words = WORDS[lang]
    data = request.POST if request.method == "POST" else request.GET
    locations = list(Location.objects.filter(active=True).order_by("-is_default", "location_code"))
    location = next((loc for loc in locations if str(loc.pk) == data.get("location")), locations[0] if locations else None)
    query = (data.get("q") or "").strip()
    count_date = _date(data.get("count_date"))
    note = (data.get("note") or "").strip()[:200]
    items = sheet_items(query)
    context = {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "section": "inventory", "page_title": words["title"],
        "words": words, "locations": locations, "location": location, "q": query, "count_date": count_date.isoformat(), "note": note,
        "can_view_cost": user_has_permission(request.user, "inventory.view_cost"),
        "too_many": words["too_many"].format(n=SHEET_LIMIT) if len(items) > SHEET_LIMIT else "",
        "stage": "sheet", "errors": [],
    }
    if location is None:
        return render(request, "inventory/stocktake.html", context)

    counts, bad = parse_counts(data, items) if request.method == "POST" else ({}, [])
    entered = {str(item.pk): str(value) for item, value in counts.items()}
    if bad:
        context["errors"].append(words["bad"].format(codes="، ".join(bad) if lang == "ar" else ", ".join(bad)))
    action = data.get("action")
    if request.method == "POST" and not bad and action in {"review", "post"}:
        if not counts:
            context["errors"].append(words["none"])
        elif action == "post":
            try:
                number, operations = post_stock_count(location=location, count_date=count_date, counts=counts, user=request.user, note=note)
            except (ValidationError, PermissionDenied) as exc:
                context["errors"].extend(translate(message, lang) for message in getattr(exc, "messages", [str(exc)]))
            else:
                messages.success(request, words["posted"].format(number=number, n=len(operations)) if operations else words["matched"])
                return redirect(f"/inventory/operations/?lang={lang}")
        if not context["errors"]:
            rows = variances(location, counts)
            context.update(stage="review", rows=rows, totals=variance_totals(rows), entered=entered)
            return render(request, "inventory/stocktake.html", context)

    sheet = count_sheet(location, items)
    for row in sheet:
        row["entered"] = entered.get(str(row["item"].pk), "")
    context.update(sheet=sheet, entered=entered)
    return render(request, "inventory/stocktake.html", context)
