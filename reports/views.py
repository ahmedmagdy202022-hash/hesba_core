from django.shortcuts import render
from django.urls import reverse


CHECKPOINT_CODE = "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP"


def _admin_changelist(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_changelist")


def home(request):
    """First safe UI navigation map.

    This page is intentionally read/navigation only. It does not post invoices,
    change balances, create stock movements, or calculate profit. The controlled
    business logic stays in services, reports, and admin-backed data screens.
    """

    business_cycle = [
        "Supplier",
        "Purchase Invoice",
        "Inventory by Location",
        "Sales Invoice",
        "Customer",
        "Cashbox",
        "Reports",
    ]

    sections = [
        {
            "title": "١) البيانات الأساسية",
            "description": "تجهيز الموردين والعملاء والأصناف والمواقع والخزن قبل أي حركة.",
            "items": [
                {"label": "الموردين", "url": _admin_changelist("master_data", "supplier"), "note": "طرف الشراء فقط"},
                {"label": "العملاء", "url": _admin_changelist("master_data", "customer"), "note": "طرف البيع فقط"},
                {"label": "الأصناف", "url": _admin_changelist("master_data", "item"), "note": "كود / اسم / تكلفة محمية"},
                {"label": "المخازن / المواقع", "url": _admin_changelist("master_data", "location"), "note": "المخزون = صنف + موقع"},
                {"label": "الخزن", "url": _admin_changelist("cashboxes", "cashbox"), "note": "تتأثر بالمدفوع فقط"},
            ],
        },
        {
            "title": "٢) الشراء من المورد",
            "description": "فاتورة شراء متعددة السطور تزود المخزون وتثبت مستحق المورد فقط بالمتبقي.",
            "items": [
                {"label": "فواتير الشراء", "url": _admin_changelist("purchases", "purchaseinvoice"), "note": "Header"},
                {"label": "سطور الشراء", "url": _admin_changelist("purchases", "purchaseline"), "note": "Multi-line"},
                {"label": "مدفوعات الموردين", "url": _admin_changelist("purchases", "supplierpayment"), "note": "تقلل مستحق المورد"},
            ],
        },
        {
            "title": "٣) المخزون حسب الموقع",
            "description": "أي زيادة أو نقص مخزون لازم يظهر كحركة قابلة للتتبع.",
            "items": [
                {"label": "حركات المخزون", "url": _admin_changelist("inventory", "stockmovement"), "note": "شراء / بيع / تحويل / تسوية"},
                {"label": "تقرير المخزون", "url": "#reports", "note": "قراءة فقط من الحركات"},
            ],
        },
        {
            "title": "٤) البيع للعميل",
            "description": "فاتورة بيع متعددة السطور تخصم المخزون وتثبت مديونية العميل فقط بالمتبقي.",
            "items": [
                {"label": "فواتير البيع", "url": _admin_changelist("sales", "salesinvoice"), "note": "Header"},
                {"label": "سطور البيع", "url": _admin_changelist("sales", "salesline"), "note": "تكلفة وربح محميين"},
                {"label": "مدفوعات العملاء", "url": _admin_changelist("sales", "customerpayment"), "note": "تقلل مديونية العميل"},
            ],
        },
        {
            "title": "٥) الخزنة والتقارير",
            "description": "الخزنة تتأثر بالمبلغ المدفوع فعليًا فقط، والتقارير قراءة فقط.",
            "items": [
                {"label": "حركات الخزن", "url": _admin_changelist("cashboxes", "cashboxmovement"), "note": "Cash in / Cash out"},
                {"label": "تقارير العملاء والموردين", "url": "#reports", "note": "قراءة فقط من القيود"},
                {"label": "تقرير الربح", "url": "#reports", "note": "Sales - COGS"},
            ],
        },
    ]

    protected_rules = [
        "المبيعات لا تنشئ مستحقات للموردين.",
        "المشتريات لا تنشئ مديونية للعملاء.",
        "الخزن تتحرك بالمبلغ المدفوع فعليًا فقط.",
        "المخزون يتحرك من خلال حركات مخزون قابلة للتتبع.",
        "التقارير قراءة فقط وليست مكان إدخال بيانات.",
    ]

    return render(
        request,
        "reports/home.html",
        {
            "checkpoint_code": CHECKPOINT_CODE,
            "business_cycle": business_cycle,
            "sections": sections,
            "protected_rules": protected_rules,
            "admin_index_url": reverse("admin:index"),
        },
    )
