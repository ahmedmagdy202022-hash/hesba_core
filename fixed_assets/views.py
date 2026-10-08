"""ASSET-001 screens: asset register, a new asset, one asset's schedule, disposal and cancel."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from config.money import money_round
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .models import AssetCategory, AssetStatus, FixedAsset
from .services import accumulated, book_value, cancel_asset, depreciation_between, dispose_asset, disposal_result, month_start, register_asset, schedule
from entities import scope as entity_scope


MANAGE = "cashboxes.record_expenses"
CATEGORY_AR = {"vehicles": "عربيات", "equipment": "معدات وماكينات", "computers": "كمبيوتر وأجهزة", "furniture": "أثاث وديكور", "buildings": "مباني", "other": "أخرى"}
STATUS_AR = {"active": "مستخدم", "disposed": "اتباع / اتشال", "cancelled": "ملغي"}
LIFE_HINT = {"vehicles": 60, "equipment": 60, "computers": 36, "furniture": 60, "buildings": 240, "other": 60}
WORDS = {
    "ar": {
        "page_title": "الأصول الثابتة", "title": "الأصول الثابتة والإهلاك",
        "intro": "سجل العربيات والأجهزة والديكور. كل أصل بيقل من قيمته كل شهر بالتساوي (قسط ثابت) لحد آخر عمره، والإهلاك بيظهر في تقرير الأرباح تحت صافي الربح.",
        "code": "الكود", "name": "الأصل", "category": "النوع", "in_service": "بدأ استخدامه", "cost": "التكلفة", "accumulated": "مجمع الإهلاك", "book": "القيمة الدفترية",
        "status": "الحالة", "open": "فتح", "empty": "مفيش أصول مسجلة.", "totals": "الإجمالي", "this_month": "إهلاك الشهر ده", "new": "أصل جديد",
        "salvage": "قيمة الخردة في الآخر", "months": "العمر الإنتاجي بالشهور", "paid_from": "اتدفع من خزنة", "not_paid": "لا (أصل موجود قبل كده)", "notes": "ملاحظات", "save": "تسجيل",
        "saved": "اتسجل الأصل {code}.", "bad": "بيانات غير صحيحة.", "life_hint": "المعتاد: عربيات وماكينات 5 سنين (60 شهر)، كمبيوتر 3 سنين (36)، مباني 20 سنة (240).",
        "schedule": "جدول الإهلاك", "month": "الشهر", "amount": "الإهلاك", "charged": "اتحسب", "future": "جاي",
        "dispose": "بيع أو استبعاد الأصل", "disposed_on": "التاريخ", "proceeds": "مبلغ البيع", "into": "المبلغ دخل خزنة", "none": "لا", "disposed": "اتسجل استبعاد الأصل.",
        "result": "نتيجة البيع (ربح + / خسارة −)", "cancel": "إلغاء الأصل (اتسجل غلط)", "reason": "السبب", "cancelled": "اتلغى الأصل، ولو كان اتدفع من خزنة الفلوس رجعت.",
        "back": "الأصول", "view_only": "تقدر تشوف بس؛ التسجيل لصاحب صلاحية المصروفات.", "payment": "الدفع",
    },
    "en": {
        "page_title": "Fixed assets", "title": "Fixed assets & depreciation",
        "intro": "Record vehicles, equipment and fit-out. Each asset loses the same amount of value every month (straight line) over its life, and depreciation shows in the profit report under net profit.",
        "code": "Code", "name": "Asset", "category": "Type", "in_service": "In service since", "cost": "Cost", "accumulated": "Accumulated depreciation", "book": "Book value",
        "status": "Status", "open": "Open", "empty": "No assets yet.", "totals": "Total", "this_month": "This month's depreciation", "new": "New asset",
        "salvage": "Salvage value at the end", "months": "Useful life (months)", "paid_from": "Paid from cashbox", "not_paid": "No (already owned)", "notes": "Notes", "save": "Register",
        "saved": "Asset {code} registered.", "bad": "Invalid input.", "life_hint": "Typical: vehicles and machines 5 years (60 months), computers 3 years (36), buildings 20 years (240).",
        "schedule": "Depreciation schedule", "month": "Month", "amount": "Depreciation", "charged": "Charged", "future": "Upcoming",
        "dispose": "Sell or dispose of the asset", "disposed_on": "Date", "proceeds": "Sale amount", "into": "Money came into cashbox", "none": "No", "disposed": "Disposal recorded.",
        "result": "Sale result (gain + / loss −)", "cancel": "Cancel asset (entered by mistake)", "reason": "Reason", "cancelled": "Asset cancelled; a cash payment for it was reversed.",
        "back": "Assets", "view_only": "You can view; recording needs the expenses permission.", "payment": "Payment",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "cashboxes",
               "can_manage": user_has_permission(request.user, MANAGE)}
    context.update(extra)
    return context


def _labels(lang):
    return {value: (label if lang == "en" else CATEGORY_AR[value]) for value, label in AssetCategory.choices}


def _status_label(asset, lang):
    return asset.get_status_display() if lang == "en" else STATUS_AR[asset.status]


def _money(raw, required=True):
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        if required:
            raise ValidationError("")
        return Decimal("0")
    return Decimal(raw)


@require_permission("cashboxes.view_expenses")
def asset_list(request):
    lang = _lang(request)
    words = WORDS[lang]
    today = timezone.localdate()
    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE):
            raise PermissionDenied(f"Registering assets needs {MANAGE}.")
        try:
            try:
                in_service = date.fromisoformat(request.POST.get("in_service_on") or "")
                cost, salvage = _money(request.POST.get("cost")), _money(request.POST.get("salvage_value"), required=False)
                months = int(request.POST.get("useful_months") or 0)
            except (ValueError, InvalidOperation, ValidationError):
                raise ValidationError(words["bad"])
            category = request.POST.get("category") if request.POST.get("category") in AssetCategory.values else AssetCategory.OTHER
            cashbox = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=request.POST.get("cashbox") or 0).first()
            asset = register_asset(name=request.POST.get("name", ""), category=category, in_service_on=in_service, cost=cost, salvage_value=salvage,
                                   useful_months=months, user=request.user, cashbox=cashbox, notes=request.POST.get("notes", ""), lang=lang)
        except ValidationError as exc:
            messages.error(request, " ".join(message for message in exc.messages if message) or words["bad"])
        else:
            messages.success(request, words["saved"].format(code=asset.code))
            return redirect(f"{reverse('fixed_assets:detail', args=[asset.pk])}?lang={lang}")
        return redirect(f"{reverse('fixed_assets:list')}?lang={lang}")
    labels = _labels(lang)
    rows, totals = [], {"cost": Decimal("0"), "accumulated": Decimal("0"), "book": Decimal("0")}
    for asset in FixedAsset.objects.exclude(status=AssetStatus.CANCELLED):
        row = {"asset": asset, "category": labels[asset.category], "status": _status_label(asset, lang), "accumulated": accumulated(asset, today),
               "book": book_value(asset, today) if asset.status == AssetStatus.ACTIVE else Decimal("0.00")}
        rows.append(row)
        if asset.status == AssetStatus.ACTIVE:
            totals["cost"] += asset.cost
            totals["accumulated"] += row["accumulated"]
            totals["book"] += row["book"]
    this_month = depreciation_between(month_start(today), today)
    return render(request, "fixed_assets/list.html", _context(request, rows=rows, totals={k: money_round(v) for k, v in totals.items()}, this_month=this_month,
                                                             categories=list(labels.items()), cashboxes=entity_scope.cashboxes(Cashbox.objects).filter(active=True), today=today, life_hint=LIFE_HINT))


@require_permission("cashboxes.view_expenses")
def asset_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    asset = get_object_or_404(FixedAsset, pk=pk)
    today = timezone.localdate()
    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE):
            raise PermissionDenied(f"Changing assets needs {MANAGE}.")
        try:
            if request.POST.get("action") == "cancel":
                cancel_asset(asset, request.user, request.POST.get("reason", ""), lang)
                messages.success(request, words["cancelled"])
            else:
                try:
                    when = date.fromisoformat(request.POST.get("disposed_on") or "")
                    proceeds = _money(request.POST.get("proceeds"), required=False)
                except (ValueError, InvalidOperation):
                    raise ValidationError(words["bad"])
                cashbox = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=request.POST.get("cashbox") or 0).first()
                dispose_asset(asset, disposed_on=when, proceeds=proceeds, user=request.user, cashbox=cashbox, lang=lang)
                messages.success(request, words["disposed"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{reverse('fixed_assets:detail', args=[asset.pk])}?lang={lang}")
    return render(request, "fixed_assets/detail.html", _context(
        request, asset=asset, category=_labels(lang)[asset.category], status=_status_label(asset, lang), rows=schedule(asset, today),
        accumulated=accumulated(asset, today), book=book_value(asset, today), result=disposal_result(asset), cashboxes=entity_scope.cashboxes(Cashbox.objects).filter(active=True), today=today,
    ))
