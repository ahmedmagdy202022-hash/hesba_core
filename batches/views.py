"""BATCH-001 screen: batches on hand, what expires soon, and registering old stock."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Item, Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .models import Batch
from .services import DEFAULT_WARN_DAYS, batch_positions, expiry_state, register_batch, retire_batch
from entities import scope as entity_scope


WARN_CHOICES = (30, 60, 90, 180)
WORDS = {
    "ar": {
        "page_title": "التشغيلات والصلاحية", "title": "التشغيلات وتاريخ الصلاحية",
        "intro": "كل تشغيلة داخلة بتاريخ صلاحيتها. الكمية الباقية من كل تشغيلة محسوبة من رصيد المخزون الفعلي على أساس إن الأقرب انتهاءً بيتباع الأول.",
        "item": "الصنف", "batch_no": "رقم التشغيلة", "expiry": "تاريخ الصلاحية", "state": "الحالة", "location": "الموقع", "received": "تاريخ الاستلام",
        "received_qty": "الكمية المستلمة", "remaining": "الباقي", "all": "الكل", "expired": "منتهية", "soon": "قربت تنتهي", "ok": "سليمة", "none": "بدون تاريخ",
        "on_hand_only": "اللي لسه في المخزون بس", "within": "التنبيه قبل", "days": "يوم", "filter": "عرض", "search": "بحث بالكود أو الاسم أو رقم التشغيلة",
        "uncovered": "كميات في المخزون من غير تشغيلة", "uncovered_hint": "غالبًا رصيد قبل تشغيل الخاصية؛ سجّل تشغيلتها من الفورم تحت.",
        "register": "تسجيل تشغيلة لرصيد موجود", "item_code": "كود الصنف أو الباركود", "quantity": "الكمية", "note": "ملاحظة", "save": "تسجيل",
        "saved": "اتسجلت التشغيلة.", "bad_item": "الصنف مش موجود.", "bad_qty": "الكمية لازم رقم أكبر من صفر.", "bad_date": "التاريخ مش صحيح.", "need_one": "اكتب رقم التشغيلة أو تاريخ الصلاحية.",
        "retire": "إلغاء", "retired": "اتلغت التشغيلة من المتابعة.", "from_purchase": "من فاتورة شراء", "manual": "مسجلة يدويًا",
        "summary_expired": "تشغيلات منتهية لسه في المخزون", "summary_soon": "تشغيلات قربت تنتهي", "empty": "مفيش تشغيلات.", "prev": "السابق", "next": "التالي",
    },
    "en": {
        "page_title": "Batches & expiry", "title": "Batches & expiry dates",
        "intro": "Every batch received, with its expiry date. What is left of each batch comes from the real stock on hand, assuming the batch that expires first is sold first.",
        "item": "Item", "batch_no": "Batch no.", "expiry": "Expiry date", "state": "State", "location": "Location", "received": "Received",
        "received_qty": "Received qty", "remaining": "Left", "all": "All", "expired": "Expired", "soon": "Expiring soon", "ok": "Fine", "none": "No date",
        "on_hand_only": "Still in stock only", "within": "Warn", "days": "days ahead", "filter": "Show", "search": "Search code, name or batch no.",
        "uncovered": "Stock on hand with no batch", "uncovered_hint": "Usually stock from before batches were switched on; register it with the form below.",
        "register": "Register a batch for stock on hand", "item_code": "Item code or barcode", "quantity": "Quantity", "note": "Note", "save": "Register",
        "saved": "Batch registered.", "bad_item": "No such item.", "bad_qty": "Quantity must be a number above zero.", "bad_date": "Invalid date.", "need_one": "Enter a batch number or an expiry date.",
        "retire": "Remove", "retired": "Batch removed from tracking.", "from_purchase": "From a purchase", "manual": "Registered by hand",
        "summary_expired": "Expired batches still in stock", "summary_soon": "Batches expiring soon", "empty": "No batches.", "prev": "Previous", "next": "Next",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _date(raw):
    raw = (raw or "").strip()
    return date.fromisoformat(raw) if raw else None


def _register(request, words):
    code = (request.POST.get("item_code") or "").strip()
    item = Item.objects.filter(active=True, item_code=code).first() or (Item.objects.filter(active=True, barcode=code).first() if code else None)
    if item is None:
        raise ValidationError(words["bad_item"])
    try:
        quantity = Decimal((request.POST.get("quantity") or "").replace(",", "."))
    except InvalidOperation:
        quantity = None
    if quantity is None or quantity <= 0:
        raise ValidationError(words["bad_qty"])
    try:
        expiry, received = _date(request.POST.get("expiry_date")), _date(request.POST.get("received_on")) or timezone.localdate()
    except ValueError:
        raise ValidationError(words["bad_date"])
    batch_no = (request.POST.get("batch_no") or "").strip()
    if not batch_no and expiry is None:
        raise ValidationError(words["need_one"])
    location = entity_scope.locations(Location.objects).filter(active=True, pk=request.POST.get("location") or 0).first() or entity_scope.locations(Location.objects).filter(active=True, is_default=True).first() or entity_scope.locations(Location.objects).filter(active=True).first()
    return register_batch(item=item, location=location, batch_no=batch_no, expiry_date=expiry, quantity=quantity.quantize(Decimal("0.001")),
                          received_on=received, user=request.user, note=request.POST.get("note", "")[:255])


@require_permission("inventory.view_stock")
def batch_index(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = user_has_permission(request.user, "inventory.adjust_stock")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Registering batches needs inventory.adjust_stock.")
        if request.POST.get("retire"):
            batch = get_object_or_404(Batch, pk=request.POST["retire"], purchase_line__isnull=True, active=True)
            retire_batch(batch, request.user)
            messages.success(request, words["retired"])
        else:
            try:
                _register(request, words)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(request, words["saved"])
        return redirect(f"{reverse('batches:index')}?lang={lang}")

    today = timezone.localdate()
    try:
        warn_days = int(request.GET.get("days") or DEFAULT_WARN_DAYS)
    except ValueError:
        warn_days = DEFAULT_WARN_DAYS
    warn_days = warn_days if warn_days in WARN_CHOICES else DEFAULT_WARN_DAYS
    state_filter = request.GET.get("state") if request.GET.get("state") in ("expired", "soon", "ok", "none") else ""
    on_hand_only = request.GET.get("all") != "1"
    query = (request.GET.get("q") or "").strip().lower()

    rows, uncovered = batch_positions()
    counts = {"expired": 0, "soon": 0}
    shown = []
    for row in rows:
        batch = row["batch"]
        row["state"] = expiry_state(batch.expiry_date, today, warn_days)
        if row["remaining"] > 0 and row["state"] in counts:
            counts[row["state"]] += 1
        if on_hand_only and row["remaining"] <= 0:
            continue
        if state_filter and row["state"] != state_filter:
            continue
        if query and query not in f"{batch.item.item_code} {batch.item.item_name} {batch.batch_no} {batch.item.barcode}".lower():
            continue
        shown.append(row)
    page = Paginator(shown, 50).get_page(request.GET.get("page"))
    uncovered_items = [{"item": item, "quantity": uncovered[item.pk]} for item in Item.objects.filter(pk__in=uncovered).order_by("item_code")]
    context = {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"], "section": "inventory",
        "page": page, "counts": counts, "uncovered": uncovered_items, "can_manage": can_manage, "warn_days": warn_days, "warn_choices": WARN_CHOICES,
        "state_filter": state_filter, "on_hand_only": on_hand_only, "q": request.GET.get("q", ""), "today": today,
        "locations": entity_scope.locations(Location.objects).filter(active=True).order_by("location_code"),
    }
    return render(request, "batches/index.html", context)
