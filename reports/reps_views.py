"""PERF-001: the sales-rep performance screen."""

from datetime import date

from django.shortcuts import render
from django.utils import timezone

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .reps import HIGH_RETURNS, LOW_COLLECTION, rep_performance

WORDS = {
    "ar": {
        "page_title": "أداء المناديب", "title": "أداء المناديب والبائعين",
        "intro": "من الفواتير والمرتجعات والتحصيلات المرحّلة بس، للفترة والكيان اللي شغال فيه. العمولة على صافي المبيعات بعد المرتجعات وقبل الضريبة.",
        "back": "العودة للتقارير", "from": "من", "to": "إلى", "show": "عرض",
        "rep": "المندوب", "unassigned": "بدون مندوب", "invoices": "الفواتير", "net_sales": "صافي المبيعات",
        "returns": "المرتجعات", "profit": "مجمل الربح", "average": "متوسط الفاتورة", "collected": "المحصّل",
        "credit": "آجل على فواتيره", "rate": "النسبة", "commission": "العمولة", "customers": "عملاؤه", "alerts": "تنبيهات",
        "total": "الإجمالي", "empty": "مفيش مبيعات في الفترة دي.",
        "high_returns": "مرتجعات عالية: {value}% من مبيعاته (الحد {limit}%).",
        "low_collection": "تحصيل ضعيف: اتحصّل {value}% بس من اللي باعه (المتوقع {limit}% على الأقل).",
        "no_sales": "عنده {value} عميل ومفيش ولا فاتورة في الفترة.",
        "note": "الصرف الفعلي للعمولة بيتسجل كمصروف من شاشة المصروفات.",
    },
    "en": {
        "page_title": "Sales reps", "title": "Sales rep performance",
        "intro": "From posted invoices, returns and collections only, for the period and the entity you are working in. Commission is on net sales after returns, before VAT.",
        "back": "Back to reports", "from": "From", "to": "To", "show": "Show",
        "rep": "Rep", "unassigned": "No rep", "invoices": "Invoices", "net_sales": "Net sales",
        "returns": "Returns", "profit": "Gross profit", "average": "Average invoice", "collected": "Collected",
        "credit": "On credit", "rate": "Rate", "commission": "Commission", "customers": "Customers", "alerts": "Alerts",
        "total": "Total", "empty": "No sales in this period.",
        "high_returns": "High returns: {value}% of their sales (limit {limit}%).",
        "low_collection": "Low collection: only {value}% of what they sold was collected (expected at least {limit}%).",
        "no_sales": "Looks after {value} customers but has no invoice in the period.",
        "note": "Paying a commission is recorded as an expense from the expenses screen.",
    },
}


def _day(raw, default):
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


@require_permission("reports.view_all_sales_report")
def rep_report(request):
    lang = "en" if request.GET.get("lang") == "en" else "ar"
    words = WORDS[lang]
    today = timezone.localdate()
    date_from = _day(request.GET.get("from"), today.replace(day=1))
    date_to = _day(request.GET.get("to"), today)
    rows, totals = rep_performance(date_from, date_to)
    for row in rows:
        row["alert_texts"] = [words[code].format(value=value, limit=limit) for code, value, limit in row["alerts"]]
    return render(request, "reports/reps.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"],
        "rows": rows, "totals": totals, "date_from": date_from, "date_to": date_to,
        "can_view_profit": user_has_permission(request.user, "reports.view_profit_report"),
        "limits": {"returns": HIGH_RETURNS, "collection": LOW_COLLECTION},
    })
