from django.shortcuts import render
from django.urls import reverse


CHECKPOINT_CODE = "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP"
REPORTS_CHECKPOINT_CODE = "096_FOUNDATION_READ_ONLY_REPORT_HUB"


def _admin_changelist(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_changelist")


def _business_cycle():
    return [
        "Supplier",
        "Purchase Invoice",
        "Inventory by Location",
        "Sales Invoice",
        "Customer",
        "Cashbox",
        "Reports",
    ]


def _protected_rules():
    return [
        "المبيعات لا تنشئ مستحقات للموردين.",
        "المشتريات لا تنشئ مديونية للعملاء.",
        "الخزن تتحرك بالمبلغ المدفوع فعليًا فقط.",
        "المخزون يتحرك من خلال حركات مخزون قابلة للتتبع.",
        "التقارير قراءة فقط وليست مكان إدخال بيانات.",
    ]


def _shared_template_context():
    return {
        "business_cycle": _business_cycle(),
        "protected_rules": _protected_rules(),
        "admin_index_url": reverse("admin:index"),
        "dashboard_url": reverse("dashboard_snapshot"),
        "reports_url": reverse("report_hub"),
        "status_url": reverse("status_counts_report"),
    }


def report_hub(request):
    """Read-only report hub.

    This checkpoint defines the safe report map before adding live report queries.
    Reports stay navigation/definition only here: no writes, no balance updates,
    no invoice posting, and no sensitive cost/profit exposure before permissions.
    """

    sections = [
        {
            "title": "١) تقارير الأطراف",
            "description": "أرصدة العملاء والموردين تأتي من الفواتير والمدفوعات والمرتجعات فقط.",
            "items": [
                {"label": "Customer Report", "url": _admin_changelist("sales", "customerledgerentry"), "note": "مبيعات + مدفوعات عملاء فقط"},
                {"label": "Supplier Report", "url": _admin_changelist("purchases", "supplierledgerentry"), "note": "مشتريات + مدفوعات موردين فقط"},
            ],
        },
        {
            "title": "٢) تقارير الفواتير",
            "description": "الفواتير Header + Lines، والحسابات لا تتغير من التقرير.",
            "items": [
                {"label": "Sales Report", "url": _admin_changelist("sales", "salesinvoice"), "note": "مبيعات مدفوعة / جزئية / آجلة"},
                {"label": "Purchase Report", "url": _admin_changelist("purchases", "purchaseinvoice"), "note": "مشتريات مدفوعة / جزئية / آجلة"},
                {"label": "Status Counts", "url": reverse("status_counts_report"), "note": "أعداد فعلية غير حساسة"},
            ],
        },
        {
            "title": "٣) تقارير التشغيل",
            "description": "المخزون والخزن مبنيين على حركات فعلية قابلة للتتبع.",
            "items": [
                {"label": "Inventory Report", "url": _admin_changelist("inventory", "stockmovement"), "note": "Item + Location"},
                {"label": "Cashbox Report", "url": _admin_changelist("cashboxes", "cashboxmovement"), "note": "Paid_Now فقط"},
            ],
        },
        {
            "title": "٤) تقارير محمية",
            "description": "الربح والتكلفة والتمويل الحساس لا يظهروا قبل صلاحيات حقيقية.",
            "items": [
                {"label": "Profit Report", "url": "#reports", "note": "Sales - Cost of Goods Sold"},
                {"label": "Usage Status Report", "url": "#reports", "note": "تحكم تكلفة التشغيل"},
                {"label": "Closed Period Report", "url": "#reports", "note": "مراجعة فقط بعد الإقفال"},
            ],
        },
    ]

    context = _shared_template_context()
    context.update(
        {
            "checkpoint_code": REPORTS_CHECKPOINT_CODE,
            "page_title": "مركز التقارير قراءة فقط",
            "page_description": "خريطة آمنة للتقارير قبل ربط الأرقام الحية. التقارير هنا للتصفح والمراجعة فقط، ولا تنشئ فواتير أو أرصدة أو حركات مخزون أو حركات خزنة.",
            "sections": sections,
            "footer_note": "هذه الشاشة Report Hub فقط. الربح والتكلفة سيظلوا محميين لحين تطبيق الصلاحيات الحقيقية.",
        }
    )
    return render(request, "reports/home.html", context)
