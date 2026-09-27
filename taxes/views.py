"""TAX-001 screens: rates, which rate each item uses, and the sales VAT report."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from master_data.models import Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .models import ItemTaxRate, TaxRate
from .services import default_rate, vat_report


VIEW_PERMISSION = "master_data.view_master_data"
MANAGE_PERMISSION = "master_data.manage_items"
REPORT_PERMISSION = "reports.view_sales_report"
PAGE_SIZE = 50

WORDS = {
    "ar": {
        "page_title": "ضريبة القيمة المضافة", "title": "ضريبة القيمة المضافة",
        "intro": "الأسعار بتتكتب قبل الضريبة، والضريبة بتتحسب لكل سطر حسب نسبة الصنف. الصنف اللي مالوش نسبة بياخد النسبة الافتراضية. خصم الفاتورة بيتطبق بعد الضريبة.",
        "rates": "النسب", "code": "الكود", "name": "الاسم", "rate": "النسبة (%)", "eta": "كود المنظومة", "default": "افتراضية", "active": "مفعّلة",
        "save": "حفظ", "add": "إضافة نسبة", "items": "نسبة كل صنف", "item": "الصنف", "use_default": "الافتراضية", "search": "بحث بالكود أو الاسم", "filter": "بحث",
        "saved": "اتحفظ.", "bad": "بيانات النسبة مش صحيحة.", "report": "تقرير ضريبة المبيعات", "view_only": "تقدر تشوف بس؛ التعديل لمسؤول الأصناف.",
        "prev": "السابق", "next": "التالي", "from": "من", "to": "إلى", "show": "عرض", "taxable": "قيمة المبيعات الخاضعة", "tax": "الضريبة المحصّلة",
        "returned": "ضريبة مرتجعات", "net": "صافي الضريبة المستحقة", "untracked": "ضريبة مكتوبة يدويًا على فواتير قديمة", "empty": "مفيش مبيعات بضريبة في الفترة دي.",
        "report_intro": "ضريبة المخرجات على المبيعات المرحّلة ناقص الضريبة اللي رجعت في المرتجعات. ضريبة المشتريات (المدخلات) لسه مش متحسبة هنا.",
    },
    "en": {
        "page_title": "VAT", "title": "Value-added tax",
        "intro": "Prices are entered before tax, and tax is charged per line at the item's rate. An item without its own rate uses the default rate. The invoice discount applies after tax.",
        "rates": "Rates", "code": "Code", "name": "Name", "rate": "Rate (%)", "eta": "ETA code", "default": "Default", "active": "Active",
        "save": "Save", "add": "Add a rate", "items": "Rate per item", "item": "Item", "use_default": "Default", "search": "Search code or name", "filter": "Search",
        "saved": "Saved.", "bad": "The rate details are not valid.", "report": "Sales VAT report", "view_only": "You can view; editing is for item managers.",
        "prev": "Previous", "next": "Next", "from": "From", "to": "To", "show": "Show", "taxable": "Taxable sales", "tax": "Tax charged",
        "returned": "Tax on returns", "net": "Net tax due", "untracked": "Tax typed by hand on older invoices", "empty": "No taxed sales in this period.",
        "report_intro": "Output tax on posted sales minus the tax given back on returns. Purchase (input) tax is not counted here yet.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "items",
               "can_manage": user_has_permission(request.user, MANAGE_PERMISSION)}
    context.update(extra)
    return context


def _audit(user, action, object_id, before, after):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="taxes", action=action, object_type="taxes.TaxRate", object_id=str(object_id), before_data=before, after_data=after)


@transaction.atomic
def _save_rate(post, user):
    pk = post.get("rate_id")
    rate_obj = TaxRate.objects.filter(pk=pk).first() if pk else TaxRate()
    try:
        value = Decimal((post.get("rate") or "").replace(",", "."))
    except InvalidOperation:
        return False
    code = (post.get("code") or "").strip().upper()
    if not code or not (post.get("name_ar") or "").strip() or not Decimal("0") <= value <= Decimal("100"):
        return False
    if TaxRate.objects.filter(code=code).exclude(pk=rate_obj.pk).exists():
        return False
    before = {"code": rate_obj.code, "rate": str(rate_obj.rate), "is_default": rate_obj.is_default} if rate_obj.pk else {}
    rate_obj.code, rate_obj.name_ar, rate_obj.name_en = code, post["name_ar"].strip(), (post.get("name_en") or "").strip()
    rate_obj.rate, rate_obj.eta_subtype = value, (post.get("eta_subtype") or "").strip().upper()[:8]
    rate_obj.active = post.get("active", "1") == "1"
    rate_obj.is_default = post.get("is_default") == "1" and rate_obj.active
    rate_obj.save()
    if rate_obj.is_default:
        TaxRate.objects.exclude(pk=rate_obj.pk).update(is_default=False)
    _audit(user, "save_tax_rate", rate_obj.pk, before, {"code": code, "rate": str(value), "is_default": rate_obj.is_default, "active": rate_obj.active})
    return True


@require_permission(VIEW_PERMISSION)
def tax_settings(request):
    lang = _lang(request)
    words = WORDS[lang]
    query = (request.GET.get("q") or request.POST.get("q") or "").strip()
    items = Item.objects.filter(active=True).order_by("item_code")
    if query:
        items = items.filter(Q(item_code__icontains=query) | Q(item_name__icontains=query) | Q(barcode=query))
    page = Paginator(items, PAGE_SIZE).get_page(request.GET.get("page") or request.POST.get("page"))
    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE_PERMISSION):
            raise PermissionDenied("Managing tax rates needs master_data.manage_items.")
        if request.POST.get("action") == "rate":
            messages.success(request, words["saved"]) if _save_rate(request.POST, request.user) else messages.error(request, words["bad"])
        elif request.POST.get("action") == "items":
            rates = {str(rate.pk): rate for rate in TaxRate.objects.filter(active=True)}
            changed = {}
            with transaction.atomic():
                for item in page.object_list:
                    if f"shown_{item.pk}" not in request.POST:
                        continue
                    chosen = rates.get(request.POST.get(f"rate_{item.pk}", ""))
                    link = ItemTaxRate.objects.filter(item=item).first()
                    before = link.tax_rate.code if link else None
                    if chosen is None and link is not None:
                        link.delete()
                    elif chosen is not None and (link is None or link.tax_rate_id != chosen.pk):
                        ItemTaxRate.objects.update_or_create(item=item, defaults={"tax_rate": chosen})
                    after = chosen.code if chosen else None
                    if before != after:
                        changed[item.item_code] = [before, after]
                if changed:
                    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=request.user, module="taxes", action="set_item_tax_rates", object_type="taxes.ItemTaxRate", object_id="items",
                                            before_data={code: pair[0] for code, pair in changed.items()}, after_data={code: pair[1] for code, pair in changed.items()})
            messages.success(request, words["saved"])
        return redirect(f"{reverse('taxes:settings')}?lang={lang}&q={query}&page={page.number}")
    links = dict(ItemTaxRate.objects.filter(item__in=page.object_list).values_list("item_id", "tax_rate_id"))
    rows = [{"item": item, "rate_id": links.get(item.pk)} for item in page.object_list]
    return render(request, "taxes/settings.html", _context(request, rates=TaxRate.objects.all(), active_rates=TaxRate.objects.filter(active=True), default=default_rate(), rows=rows, page=page, q=query))


def _date(raw, fallback):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return fallback


@require_permission(REPORT_PERMISSION)
def tax_report(request):
    today = timezone.localdate()
    date_from = _date(request.GET.get("date_from"), today.replace(day=1))
    date_to = _date(request.GET.get("date_to"), today)
    return render(request, "taxes/report.html", _context(request, report=vat_report(date_from, date_to), date_from=date_from.isoformat(), date_to=date_to.isoformat(), page_title=WORDS[_lang(request)]["report"]))
