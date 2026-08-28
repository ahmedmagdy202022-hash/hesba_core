from datetime import date

from django.core.paginator import Paginator
from django.shortcuts import render

from cashboxes.models import Cashbox
from master_data.models import Customer, Location, Supplier
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .selectors import (
    cashbox_report,
    customer_report,
    profit_report,
    profit_totals,
    purchase_report,
    sales_report,
    stock_report,
    supplier_report,
)


STRINGS = {
    "ar": {"page_title": "التقارير", "dashboard": "لوحة القيادة", "language": "English", "reports": "مركز التقارير قراءة فقط", "search": "تطبيق الفلاتر", "empty": "لا توجد نتائج مطابقة.", "back": "العودة للتقارير"},
    "en": {"page_title": "Reports", "dashboard": "Dashboard", "language": "العربية", "reports": "Read-only report center", "search": "Apply filters", "empty": "No matching results.", "back": "Back to reports"},
}

REPORT_CARDS = (
    ("reports:sales", "reports.view_sales_report", "Sales Report", "تقرير المبيعات", "Posted, draft, and cancelled sales invoices", "فواتير البيع المرحلة والمسودة والملغاة"),
    ("reports:purchases", "reports.view_purchase_report", "Purchase Report", "تقرير المشتريات", "Purchase invoice history and due context", "سجل فواتير الشراء والمتبقي"),
    ("reports:inventory", "reports.view_inventory_report", "Inventory Report", "تقرير المخزون", "Stock by item and active location", "المخزون حسب الصنف والموقع"),
    ("reports:customers", "reports.view_customer_report", "Customer Report", "تقرير العملاء", "Balances and customer statement entries", "أرصدة وحركات كشف حساب العملاء"),
    ("reports:suppliers", "reports.view_supplier_report", "Supplier Report", "تقرير الموردين", "Balances and supplier statement entries", "أرصدة وحركات كشف حساب الموردين"),
    ("reports:cashboxes", "reports.view_cashbox_report", "Cashbox Report", "تقرير الخزن", "Opening balance and real cash movements", "الرصيد الافتتاحي وحركات النقد الفعلية"),
    ("reports:profit", "reports.view_profit_report", "Profit Report", "تقرير الأرباح", "Sales - Cost of Goods Sold", "المبيعات - تكلفة البضاعة المباعة"),
)


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": STRINGS[lang], "page_title": STRINGS[lang]["page_title"]}
    context.update(extra)
    return context


def _date_filter(request, name):
    raw = request.GET.get(name, "").strip()
    if not raw:
        return None, ""
    try:
        return date.fromisoformat(raw), raw
    except ValueError:
        return None, raw


def report_hub(request):
    lang = _lang(request)
    cards = [
        {"url_name": url_name, "allowed": user_has_permission(request.user, permission), "title": title_en if lang == "en" else title_ar, "title_alt": title_ar if lang == "en" else title_en, "description": description_en if lang == "en" else description_ar}
        for url_name, permission, title_en, title_ar, description_en, description_ar in REPORT_CARDS
    ]
    return render(request, "reports/functional_hub.html", _context(request, cards=cards, checkpoint_code="096_FOUNDATION_READ_ONLY_REPORT_HUB"))


@require_permission("reports.view_sales_report")
def sales_report_view(request):
    date_from, date_from_raw = _date_filter(request, "date_from")
    date_to, date_to_raw = _date_filter(request, "date_to")
    status = request.GET.get("status", "")
    if status not in {"draft", "posted", "cancelled"}:
        status = ""
    rows = sales_report(date_from, date_to, status or None)
    if not user_has_permission(request.user, "reports.view_all_sales_report"):
        rows = rows.filter(created_by=request.user)
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(request, "reports/invoices.html", _context(request, title="Sales Report" if _lang(request) == "en" else "تقرير المبيعات", kind="sales", page=page, date_from=date_from_raw, date_to=date_to_raw, status=status))


@require_permission("reports.view_purchase_report")
def purchase_report_view(request):
    date_from, date_from_raw = _date_filter(request, "date_from")
    date_to, date_to_raw = _date_filter(request, "date_to")
    status = request.GET.get("status", "")
    if status not in {"draft", "posted", "cancelled"}:
        status = ""
    page = Paginator(purchase_report(date_from, date_to, status or None), 50).get_page(request.GET.get("page"))
    return render(request, "reports/invoices.html", _context(request, title="Purchase Report" if _lang(request) == "en" else "تقرير المشتريات", kind="purchases", page=page, date_from=date_from_raw, date_to=date_to_raw, status=status))


@require_permission("reports.view_inventory_report")
def inventory_report_view(request):
    location = None
    location_id = request.GET.get("location", "")
    if location_id.isdigit():
        location = Location.objects.filter(pk=location_id, active=True).first()
    rows = stock_report(location)
    return render(request, "reports/inventory.html", _context(request, title="Inventory Report" if _lang(request) == "en" else "تقرير المخزون", rows=rows, locations=Location.objects.filter(active=True), selected_location=location, can_view_cost=user_has_permission(request.user, "inventory.view_cost")))


def _party_context(request, party_kind):
    model = Customer if party_kind == "customer" else Supplier
    selected = None
    party_id = request.GET.get(party_kind, "")
    if party_id.isdigit():
        selected = model.objects.filter(pk=party_id, active=True).first()
    rows = customer_report(selected) if party_kind == "customer" else supplier_report(selected)
    entries = selected.ledger_entries.select_related("sales_invoice", "customer_payment") if selected and party_kind == "customer" else None
    if selected and party_kind == "supplier":
        entries = selected.ledger_entries.select_related("purchase_invoice", "supplier_payment")
    return {"rows": rows, "parties": model.objects.filter(active=True), "selected": selected, "entries": entries, "party_kind": party_kind}


@require_permission("reports.view_customer_report")
def customer_report_view(request):
    return render(request, "reports/parties.html", _context(request, title="Customer Report" if _lang(request) == "en" else "تقرير العملاء", **_party_context(request, "customer")))


@require_permission("reports.view_supplier_report")
def supplier_report_view(request):
    return render(request, "reports/parties.html", _context(request, title="Supplier Report" if _lang(request) == "en" else "تقرير الموردين", **_party_context(request, "supplier")))


@require_permission("reports.view_cashbox_report")
def cashbox_report_view(request):
    selected = None
    cashbox_id = request.GET.get("cashbox", "")
    if cashbox_id.isdigit():
        selected = Cashbox.objects.filter(pk=cashbox_id, active=True).first()
    date_from, date_from_raw = _date_filter(request, "date_from")
    date_to, date_to_raw = _date_filter(request, "date_to")
    return render(request, "reports/cashboxes.html", _context(request, title="Cashbox Report" if _lang(request) == "en" else "تقرير الخزن", rows=cashbox_report(selected, date_from, date_to), cashboxes=Cashbox.objects.filter(active=True), selected=selected, date_from=date_from_raw, date_to=date_to_raw))


@require_permission("reports.view_profit_report")
def profit_report_view(request):
    date_from, date_from_raw = _date_filter(request, "date_from")
    date_to, date_to_raw = _date_filter(request, "date_to")
    rows = profit_report(date_from, date_to)
    totals = profit_totals(date_from, date_to)
    return render(request, "reports/profit.html", _context(request, title="Profit Report" if _lang(request) == "en" else "تقرير الأرباح", rows=rows, totals=totals, date_from=date_from_raw, date_to=date_to_raw))
