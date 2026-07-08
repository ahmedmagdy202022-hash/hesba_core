from django.shortcuts import render
from django.urls import reverse


CHECKPOINT_CODE = "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP"
DASHBOARD_CHECKPOINT_CODE = "120D_DASHBOARD_LIVE_VISUAL_PREVIEW"
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


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


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
                {"label": "تقرير المخزون", "url": reverse("report_hub"), "note": "قراءة فقط من الحركات"},
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
                {"label": "Dashboard", "url": reverse("dashboard_snapshot"), "note": "ملخص قراءة فقط"},
                {"label": "Reports", "url": reverse("report_hub"), "note": "مركز التقارير"},
                {"label": "Status", "url": reverse("status_counts_report"), "note": "أعداد آمنة"},
            ],
        },
    ]

    context = _shared_template_context()
    context.update(
        {
            "checkpoint_code": CHECKPOINT_CODE,
            "page_title": "خريطة تشغيل أول شاشة UI",
            "page_description": "شاشة بسيطة وآمنة للتنقل داخل حِسْبَة. الهدف منها ترتيب دورة العمل قبل بناء شاشات الإدخال الحقيقية، بدون تغيير أي منطق داتا أو حسابات مالية.",
            "sections": sections,
            "footer_note": "هذه الشاشة Navigation Map فقط. الإدخال الفعلي ما زال من Admin لحد ما نثبت أول شاشة Transaction آمنة.",
        }
    )
    return render(request, "reports/home.html", context)


def dashboard_snapshot(request):
    """Live visual preview for Dashboard approval.

    Preview-only: no writes, no migrations, no accounting calculations, and no
    sensitive real profit/cost exposure. The UI uses safe sample data so Ahmed can
    approve the responsive visual direction live before production implementation.
    """

    lang = _lang(request)
    is_en = lang == "en"
    text = {
        "ar": {
            "title": "الرئيسية",
            "subtitle": "نظرة عامة على أداء أعمالك",
            "owner": "المالك",
            "welcome": "مرحبًا أحمد",
            "headline": "أعمالك تسير في الاتجاه الصحيح",
            "hero_note": "أداء ممتاز هذا الشهر؛ استمرت مبيعاتك في النمو والتحسن.",
            "live": "مباشر",
            "updated": "آخر تحديث: منذ 20 ثانية",
            "currency": "عملة النشاط",
            "auto": "تحديث تلقائي",
            "quick": "إجراءات سريعة",
            "charts": "تحليلات متغيرة",
            "recent": "آخر العمليات",
            "insights": "رؤى وتنبيهات ذكية",
            "show_all": "عرض الكل",
            "customize": "تخصيص",
            "menu": "القائمة",
            "home": "الرئيسية",
            "sales": "المبيعات",
            "purchases": "المشتريات",
            "customers": "العملاء",
            "suppliers": "الموردون",
            "inventory": "المخزون",
            "cashbox": "الصندوق",
            "reports": "التقارير",
            "settings": "الإعدادات",
            "new_invoice": "فاتورة جديدة",
            "new_customer": "عميل جديد",
            "new_item": "منتج جديد",
            "new_service": "خدمة جديدة",
            "new_expense": "إضافة مصروف",
            "print_report": "طباعة تقرير",
            "more": "المزيد",
            "new_action": "إجراء جديد",
        },
        "en": {
            "title": "Dashboard",
            "subtitle": "Overview of your business performance",
            "owner": "Owner",
            "welcome": "Hi Ahmed",
            "headline": "Your business is moving in the right direction",
            "hero_note": "Strong month so far; sales keep improving steadily.",
            "live": "Live",
            "updated": "Updated: 20 seconds ago",
            "currency": "Activity currency",
            "auto": "Auto refresh",
            "quick": "Quick actions",
            "charts": "Live analytics",
            "recent": "Recent operations",
            "insights": "Smart insights & alerts",
            "show_all": "Show all",
            "customize": "Customize",
            "menu": "Menu",
            "home": "Home",
            "sales": "Sales",
            "purchases": "Purchases",
            "customers": "Customers",
            "suppliers": "Suppliers",
            "inventory": "Inventory",
            "cashbox": "Cashbox",
            "reports": "Reports",
            "settings": "Settings",
            "new_invoice": "New invoice",
            "new_customer": "New customer",
            "new_item": "New item",
            "new_service": "New service",
            "new_expense": "Add expense",
            "print_report": "Print report",
            "more": "More",
            "new_action": "New action",
        },
    }[lang]

    kpis = [
        {"label": "إجمالي المبيعات (اليوم)" if not is_en else "Sales today", "value": "156,300", "delta": "+12%", "note": "عن أمس" if not is_en else "vs yesterday", "icon": "bag"},
        {"label": "إجمالي المبيعات (الشهر)" if not is_en else "Sales this month", "value": "986,540", "delta": "+15%", "note": "عن الشهر الماضي" if not is_en else "vs last month", "icon": "trend"},
        {"label": "إجمالي المدفوعات" if not is_en else "Total payments", "value": "78,640", "delta": "+8%", "note": "عن الشهر الماضي" if not is_en else "vs last month", "icon": "wallet"},
        {"label": "إجمالي العملاء" if not is_en else "Total customers", "value": "9,250", "delta": "+6%", "note": "عن الشهر الماضي" if not is_en else "vs last month", "icon": "users"},
        {"label": "المنتجات المباعة (اليوم)" if not is_en else "Items sold today", "value": "476", "delta": "+9%", "note": "عن أمس" if not is_en else "vs yesterday", "icon": "box"},
        {"label": "المنتجات المتاحة" if not is_en else "Available items", "value": "78,640", "delta": "+11%", "note": "عن الشهر الماضي" if not is_en else "vs last month", "icon": "cube"},
        {"label": "إجمالي المصروفات (الشهر)" if not is_en else "Monthly expenses", "value": "32,850", "delta": "+6%", "note": "عن الشهر الماضي" if not is_en else "vs last month", "icon": "coin"},
    ]

    hero_metrics = [
        {"label": "نمو المبيعات (الشهر)" if not is_en else "Monthly sales growth", "value": "+18%", "note": "مقارنة بالشهر الماضي" if not is_en else "vs last month", "icon": "trend"},
        {"label": "الفواتير المعلقة" if not is_en else "Pending invoices", "value": "23", "note": "فاتورة" if not is_en else "invoices", "icon": "invoice"},
        {"label": "تنبيهات المخزون" if not is_en else "Inventory alerts", "value": "7", "note": "تنبيهات" if not is_en else "alerts", "icon": "alert"},
        {"label": "رصيد الخزينة" if not is_en else "Cashbox balance", "value": "986,540", "note": text["currency"], "icon": "wallet"},
    ]

    quick_actions = [
        (text["new_invoice"], "invoice"),
        (text["new_customer"], "users"),
        (text["new_item"], "cube"),
        (text["new_service"], "gear"),
        (text["new_expense"], "wallet"),
        (text["print_report"], "pdf"),
    ]

    recent = [
        ("#INV-1045", "فاتورة بيع" if not is_en else "Sales invoice", "عميل نقدي" if not is_en else "Cash customer", "10:42 ص" if not is_en else "10:42 AM", "3,250", "sale"),
        ("#PUR-1032", "فاتورة شراء" if not is_en else "Purchase invoice", "مورد الخليج" if not is_en else "Gulf supplier", "10:35 ص" if not is_en else "10:35 AM", "12,000", "purchase"),
        ("#BIL-1021", "فاتورة شراء" if not is_en else "Bill", "عميل مؤسسة النور" if not is_en else "Al Noor customer", "10:28 ص" if not is_en else "10:28 AM", "8,750", "purchase"),
        ("#EXP-1001", "مصروفات" if not is_en else "Expense", "مصروفات تشغيلية" if not is_en else "Operating expense", "10:21 ص" if not is_en else "10:21 AM", "350", "expense"),
        ("#INV-1044", "فاتورة بيع" if not is_en else "Sales invoice", "شركة الإبداع" if not is_en else "Ebdaa company", "10:15 ص" if not is_en else "10:15 AM", "2,950", "sale"),
    ]

    insights = [
        ("مخزون منخفض" if not is_en else "Low stock", "12 منتج بحاجة لإعادة طلب" if not is_en else "12 items need reorder", "danger", "alert"),
        ("فواتير متأخرة" if not is_en else "Late invoices", "18 فاتورة متأخرة" if not is_en else "18 overdue invoices", "warning", "alert"),
        ("أداء المبيعات" if not is_en else "Sales performance", "+15% عن الشهر الماضي" if not is_en else "+15% vs last month", "info", "trend"),
        ("منتج مميز" if not is_en else "Top product", "سماعات لاسلكية الأعلى مبيعًا" if not is_en else "Wireless headset is top seller", "success", "check"),
        ("تحصيل أسرع" if not is_en else "Faster collection", "3 عملاء لديهم مستحقات" if not is_en else "3 customers have dues", "info", "bolt"),
        ("فرص نمو" if not is_en else "Growth chances", "+16% منتج جديد" if not is_en else "+16% new product", "purple", "star"),
    ]

    context = {
        "lang": lang,
        "dir": "ltr" if is_en else "rtl",
        "checkpoint_code": DASHBOARD_CHECKPOINT_CODE,
        "text": text,
        "hero_metrics": hero_metrics,
        "kpis": kpis,
        "quick_actions": quick_actions,
        "recent": recent,
        "insights": insights,
    }
    return render(request, "reports/dashboard_live_preview.html", context)


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
            "title": "٣) تقارير الخزن والمخزون",
            "description": "الخزنة والمخزون لا يتغيران من التقارير؛ التقارير قراءة فقط.",
            "items": [
                {"label": "Cashbox Movements", "url": _admin_changelist("cashboxes", "cashboxmovement"), "note": "Cash in / out"},
                {"label": "Stock Movements", "url": _admin_changelist("inventory", "stockmovement"), "note": "كل حركة مخزون قابلة للتتبع"},
            ],
        },
        {
            "title": "٤) تقارير الربح الحساسة",
            "description": "الربح يعتمد على تكلفة محمية وصلاحيات، ولا يظهر للكاشير.",
            "items": [
                {"label": "Profit Report", "url": reverse("report_hub"), "note": "Owner/Manager فقط لاحقًا"},
                {"label": "Permissions", "url": reverse("admin:index"), "note": "قبل فتح أي تكلفة أو ربح"},
            ],
        },
    ]

    context = _shared_template_context()
    context.update(
        {
            "checkpoint_code": REPORTS_CHECKPOINT_CODE,
            "page_title": "مركز التقارير Read-only",
            "page_description": "خريطة تقارير آمنة قبل بناء استعلامات التقارير النهائية. لا توجد أي كتابة أو تعديل أرصدة من هنا.",
            "sections": sections,
            "footer_note": "أي تقرير ربح أو تكلفة سيحتاج صلاحيات واضحة قبل الظهور.",
        }
    )
    return render(request, "reports/home.html", context)
