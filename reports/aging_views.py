from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone

from permissions.services import user_has_permission

from .aging import BUCKETS, aging_rows, aging_totals, credit_days, reminder_link, set_credit_days


WORDS = {
    "ar": {
        "page_title": "أعمار الديون",
        "customers_title": "أعمار ديون العملاء والتحصيل",
        "suppliers_title": "أعمار مستحقات الموردين",
        "customers_intro": "الفلوس اللي برّه عند العملاء، متقسمة حسب عمرها. أي دفعة بتسدّد أقدم دين الأول.",
        "suppliers_intro": "اللي عليك للموردين، متقسم حسب عمره، علشان تخطط الدفع.",
        "customers": "العملاء",
        "suppliers": "الموردون",
        "as_of": "لحد تاريخ",
        "apply": "تطبيق",
        "name": "الاسم",
        "phone": "التليفون",
        "b0_30": "0–30 يوم",
        "b31_60": "31–60",
        "b61_90": "61–90",
        "b90_plus": "أكتر من 90",
        "total": "الإجمالي",
        "overdue": "متأخر",
        "oldest": "أقدم مبلغ من",
        "last_payment": "آخر سداد",
        "remind": "تذكير واتساب",
        "credit": "رصيد دائن (دفع زيادة)",
        "empty": "مفيش أرصدة مفتوحة.",
        "terms": "مدة الآجل المسموحة (يوم)",
        "terms_note": "أي مبلغ عدّى المدة دي بيتحسب «متأخر».",
        "save": "حفظ",
        "saved": "تم حفظ مدة الآجل.",
        "totals": "الإجمالي",
        "never": "—",
    },
    "en": {
        "page_title": "Aging",
        "customers_title": "Customer aging and collections",
        "suppliers_title": "Supplier aging",
        "customers_intro": "Money customers owe, split by age. Every payment settles the oldest amount first.",
        "suppliers_intro": "What you owe suppliers, split by age, to plan payments.",
        "customers": "Customers",
        "suppliers": "Suppliers",
        "as_of": "As of",
        "apply": "Apply",
        "name": "Name",
        "phone": "Phone",
        "b0_30": "0–30 days",
        "b31_60": "31–60",
        "b61_90": "61–90",
        "b90_plus": "90+",
        "total": "Total",
        "overdue": "Overdue",
        "oldest": "Oldest since",
        "last_payment": "Last payment",
        "remind": "WhatsApp reminder",
        "credit": "Credit balance (overpaid)",
        "empty": "No open balances.",
        "terms": "Allowed credit days",
        "terms_note": "Anything older than this counts as overdue.",
        "save": "Save",
        "saved": "Credit days saved.",
        "totals": "Total",
        "never": "—",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def aging_report(request):
    lang = _lang(request)
    words = WORDS[lang]
    kind = "suppliers" if request.GET.get("party") == "suppliers" else "customers"
    permission = "reports.view_supplier_report" if kind == "suppliers" else "reports.view_customer_report"
    if not user_has_permission(request.user, permission):
        raise PermissionDenied(f"This report needs the {permission} permission.")
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Changing credit days needs settings.manage_settings.")
        try:
            set_credit_days(request.POST.get("credit_days", ""), request.user)
        except ValueError:
            messages.error(request, words["terms"])
        else:
            messages.success(request, words["saved"])
        return redirect(f"/reports/aging/?lang={lang}&party={kind}")
    raw = request.GET.get("as_of", "")
    try:
        as_of = date.fromisoformat(raw) if raw else timezone.localdate()
    except ValueError:
        as_of = timezone.localdate()
    rows = aging_rows(kind, as_of)
    from printing.company import company_details

    company = company_details()
    if kind == "customers":
        for row in rows:
            row["reminder"] = reminder_link(row, company["name"], company["currency"], lang) if row["total"] > 0 else ""
    return render(
        request,
        "reports/aging.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": words,
            "page_title": words["page_title"],
            "kind": kind,
            "rows": rows,
            "totals": aging_totals(rows),
            "buckets": [(key, words[key]) for key, *_ in BUCKETS],
            "as_of": as_of.isoformat(),
            "terms": credit_days(),
            "can_manage": can_manage,
            "can_suppliers": user_has_permission(request.user, "reports.view_supplier_report"),
            "can_customers": user_has_permission(request.user, "reports.view_customer_report"),
            "currency": company["currency"],
        },
    )
