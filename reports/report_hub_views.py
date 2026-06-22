from django.shortcuts import render
from django.urls import reverse


REPORTS_CHECKPOINT_CODE = "096_FOUNDATION_READ_ONLY_REPORT_HUB"


def _admin_changelist(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_changelist")


def report_hub(request):
    """Read-only report hub.

    This screen is a safe map for reports only. It does not post invoices,
    change balances, create stock movements, or expose sensitive profit/cost
    values before real permissions are in place.
    """

    report_groups = [
        {
            "title": "تقارير الأطراف",
            "description": "أرصدة العملاء والموردين من الفواتير والمدفوعات والمرتجعات فقط.",
            "reports": [
                {"name": "Customer Report", "url": _admin_changelist("sales", "customerledgerentry"), "rule": "العميل يتأثر بالمبيعات ومدفوعات العملاء فقط."},
                {"name": "Supplier Report", "url": _admin_changelist("purchases", "supplierledgerentry"), "rule": "المورد يتأثر بالمشتريات ومدفوعات الموردين فقط."},
            ],
        },
        {
            "title": "تقارير الفواتير",
            "description": "الفواتير Header + Lines، والتقرير لا يغيّر أي رصيد.",
            "reports": [
                {"name": "Sales Report", "url": _admin_changelist("sales", "salesinvoice"), "rule": "البيع يخصم المخزون ويثبت مديونية العميل بالمتبقي فقط."},
                {"name": "Purchase Report", "url": _admin_changelist("purchases", "purchaseinvoice"), "rule": "الشراء يزيد المخزون ويثبت مستحق المورد بالمتبقي فقط."},
            ],
        },
        {
            "title": "تقارير التشغيل",
            "description": "المخزون والخزن مبنيين على حركات فعلية قابلة للتتبع.",
            "reports": [
                {"name": "Inventory Report", "url": _admin_changelist("inventory", "stockmovement"), "rule": "المخزون = Item + Location من حركات المخزون فقط."},
                {"name": "Cashbox Report", "url": _admin_changelist("cashboxes", "cashboxmovement"), "rule": "الخزنة تتأثر بالمبلغ المدفوع فعليًا فقط."},
            ],
        },
        {
            "title": "تقارير محمية",
            "description": "الربح والتكلفة والتمويل الحساس لا يظهروا قبل صلاحيات حقيقية.",
            "reports": [
                {"name": "Profit Report", "url": "#protected", "rule": "Profit = Sales - Cost of Goods Sold."},
                {"name": "Usage Status Report", "url": "#protected", "rule": "مراقبة تكلفة التشغيل قبل أي ترقية مدفوعة."},
                {"name": "Closed Period Report", "url": "#protected", "rule": "الفترات المغلقة قراءة فقط، والتعديل يكون بإجراء مضبوط."},
            ],
        },
    ]

    return render(
        request,
        "reports/report_hub.html",
        {
            "checkpoint_code": REPORTS_CHECKPOINT_CODE,
            "report_groups": report_groups,
            "business_cycle": [
                "Supplier",
                "Purchase Invoice",
                "Inventory by Location",
                "Sales Invoice",
                "Customer",
                "Cashbox",
                "Reports",
            ],
            "home_url": reverse("home"),
            "dashboard_url": reverse("dashboard_snapshot"),
            "admin_index_url": reverse("admin:index"),
        },
    )
