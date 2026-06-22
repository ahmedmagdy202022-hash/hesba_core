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
            "page_title": "خريطة تشغيل أول شاشة UI",
            "page_description": "شاشة بسيطة وآمنة للتنقل داخل حِسْبَة. الهدف منها ترتيب دورة العمل قبل بناء شاشات الإدخال الحقيقية، بدون تغيير أي منطق داتا أو حسابات مالية.",
            "business_cycle": _business_cycle(),
            "sections": sections,
            "protected_rules": _protected_rules(),
            "admin_index_url": reverse("admin:index"),
            "dashboard_url": reverse("dashboard_snapshot"),
            "footer_note": "هذه الشاشة Navigation Map فقط. الإدخال الفعلي ما زال من Admin لحد ما نثبت أول شاشة Transaction آمنة.",
        },
    )


def dashboard_snapshot(request):
    """Read-only dashboard snapshot for the first user-facing dashboard step.

    This checkpoint is static and permission-safe. It does not query operational
    data yet, so it can render before migrations or seed data are applied in a
    fresh Codespace.
    """

    sections = [
        {
            "title": "١) حالة دورة العمل",
            "description": "الدورة الأساسية جاهزة كمسار واحد قابل للتوسع.",
            "items": [
                {"label": "الدورة الكاملة", "url": "#reports", "note": "مورد → شراء → مخزون → بيع → عميل → خزنة → تقارير"},
                {"label": "حالة الإدخال", "url": reverse("home"), "note": "Admin مؤقتًا حتى شاشة Transaction آمنة"},
            ],
        },
        {
            "title": "٢) KPIs آمنة لاحقًا",
            "description": "تجهيز أماكن الأرقام بدون عرض ربح أو تكلفة قبل الصلاحيات.",
            "items": [
                {"label": "عدد الموردين", "url": "#reports", "note": "قراءة فقط"},
                {"label": "عدد العملاء", "url": "#reports", "note": "قراءة فقط"},
                {"label": "حالة المخزون", "url": "#reports", "note": "حسب الصنف والموقع"},
                {"label": "حالة الخزن", "url": "#reports", "note": "بالمدفوع فعليًا فقط"},
            ],
        },
        {
            "title": "٣) حماية الأرقام الحساسة",
            "description": "التكلفة والربح لا يظهروا قبل صلاحيات حقيقية.",
            "items": [
                {"label": "الربح", "url": "#reports", "note": "Owner فقط لاحقًا"},
                {"label": "التكلفة", "url": "#reports", "note": "محمية من Cashier"},
            ],
        },
        {
            "title": "٤) الخطوة الجاية",
            "description": "ربط أرقام قراءة فقط بعد ثبات reports/views والمايجريشن.",
            "items": [
                {"label": "تقارير Read-only", "url": "#reports", "note": "قبل أي شاشة إدخال جديدة"},
                {"label": "صلاحيات", "url": "#reports", "note": "قبل إظهار finance حساس"},
            ],
        },
    ]

    return render(
        request,
        "reports/home.html",
        {
            "checkpoint_code": DASHBOARD_CHECKPOINT_CODE,
            "page_title": "Dashboard Snapshot قراءة فقط",
            "page_description": "أول لقطة داشبورد آمنة قبل ربط الأرقام الحقيقية. الهدف تثبيت شكل الملخص من غير إدخال أو تعديل بيانات.",
            "business_cycle": _business_cycle(),
            "sections": sections,
            "protected_rules": _protected_rules(),
            "admin_index_url": reverse("admin:index"),
            "dashboard_url": reverse("dashboard_snapshot"),
            "footer_note": "هذه الشاشة Read-only Snapshot فقط. الربح والتكلفة وأي بيانات مالية حساسة ستظهر بعد صلاحيات حقيقية.",
        },
    )
