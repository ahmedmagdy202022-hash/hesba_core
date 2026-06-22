from django.shortcuts import render
from django.urls import reverse


CHECKPOINT_CODE = "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP"
DASHBOARD_CHECKPOINT_CODE = "094_FOUNDATION_DASHBOARD_SNAPSHOT"


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


def home(request):
    """First safe UI navigation map.

    This page is intentionally read/navigation only. It does not post invoices,
    change balances, create stock movements, or calculate profit. The controlled
    business logic stays in services, reports, and admin-backed data screens.
    """

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

    return render(
        request,
        "reports/home.html",
        {
            "checkpoint_code": CHECKPOINT_CODE,
            "business_cycle": _business_cycle(),
            "sections": sections,
            "protected_rules": _protected_rules(),
            "admin_index_url": reverse("admin:index"),
            "dashboard_url": reverse("dashboard_snapshot"),
        },
    )


def dashboard_snapshot(request):
    """Read-only dashboard snapshot for the first user-facing dashboard step.

    This checkpoint keeps the dashboard static and permission-safe. It does not
    query operational data yet, so it can render before migrations or seed data
    are applied in a fresh Codespace. Real KPIs will be connected only after the
    report views and role permissions are stable.
    """

    readiness_cards = [
        {
            "title": "دورة العمل الأساسية",
            "value": "جاهزة",
            "note": "مورد → شراء → مخزون → بيع → عميل → خزنة → تقارير",
        },
        {
            "title": "حالة الإدخال",
            "value": "Admin مؤقتًا",
            "note": "لا توجد شاشة Transaction حقيقية حتى الآن.",
        },
        {
            "title": "حالة التقارير",
            "value": "قراءة فقط",
            "note": "لا يتم تعديل أي بيانات من التقارير أو الداشبورد.",
        },
        {
            "title": "حماية الأرقام الحساسة",
            "value": "مؤجلة للصلاحيات",
            "note": "الربح والتكلفة لن يظهروا قبل تثبيت صلاحيات حقيقية.",
        },
    ]

    safe_kpi_placeholders = [
        "عدد الموردين",
        "عدد فواتير الشراء",
        "حالة المخزون حسب الموقع",
        "عدد فواتير البيع",
        "عدد العملاء",
        "حالة الخزن",
    ]

    next_steps = [
        "ربط أرقام قراءة فقط من views آمنة بعد تثبيت المايجريشن والبيانات.",
        "تجهيز صلاحيات عرض التكلفة والربح قبل أي KPI مالي حساس.",
        "عدم بناء شاشات إدخال جديدة قبل حماية دورة البيع والشراء بالكامل.",
    ]

    return render(
        request,
        "reports/dashboard_snapshot.html",
        {
            "checkpoint_code": DASHBOARD_CHECKPOINT_CODE,
            "business_cycle": _business_cycle(),
            "readiness_cards": readiness_cards,
            "safe_kpi_placeholders": safe_kpi_placeholders,
            "protected_rules": _protected_rules(),
            "next_steps": next_steps,
            "home_url": reverse("home"),
            "admin_index_url": reverse("admin:index"),
        },
    )
