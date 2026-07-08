from django.shortcuts import render
from django.urls import reverse


CHECKPOINT_CODE = "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP"
DASHBOARD_CHECKPOINT_CODE = "DASHBOARD_V3_VISUAL_PREVIEW"
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


def home(request):
    """First safe UI navigation map."""

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
    """Dashboard v3 visual preview.

    Preview-only route: no database queries, no accounting calculations, no writes.
    All values are static mock values so Ahmed can judge the live visual result.
    """

    lang = "en" if request.GET.get("lang") == "en" else "ar"
    is_en = lang == "en"

    if is_en:
        context = {
            "lang": "en",
            "dir": "ltr",
            "other_lang": "ar",
            "page_title": "Hesba Dashboard v3 Preview",
            "brand_title": "Hesba",
            "owner_role": "Business owner",
            "language_label": "English",
            "current_time": "09:42",
            "current_date": "Sat, 17 May 2025",
            "greeting": "Good morning, Ahmed",
            "hero_subtitle": "Your business is improving. Keep the same momentum.",
            "status_chip": "Excellent performance",
            "out_of_100": "out of 100",
            "score_items": ["Sales", "Profit", "Customer dues", "Customer satisfaction"],
            "currency": "SAR",
            "kpis": [
                {"label": "Today sales", "value": "32,450.00", "delta": "↑ 18%", "direction": "", "icon": "▮"},
                {"label": "Net profit", "value": "6,250.75", "delta": "↑ 14%", "direction": "", "icon": "↗"},
                {"label": "Cashbox balance", "value": "78,920.50", "delta": "↑ 5%", "direction": "", "icon": "▣"},
                {"label": "Customer dues", "value": "56,340.00", "delta": "↓ 3%", "direction": "down", "icon": "♙"},
                {"label": "Supplier payables", "value": "34,780.00", "delta": "↓ 2%", "direction": "", "icon": "▰"},
                {"label": "Today expenses", "value": "4,120.30", "delta": "↑ 8%", "direction": "", "icon": "□"},
            ],
            "alerts_title": "Smart alerts",
            "alerts": [
                {"title": "Main cashbox balance is low", "note": "Updated 35 minutes ago", "level": "Urgent", "color": "red", "icon": "🔔"},
                {"title": "3 customer invoices due today", "note": "Total value 8,750", "level": "Medium", "color": "orange", "icon": "⏱"},
                {"title": "12 items near stock-out", "note": "Check inventory", "level": "Info", "color": "blue", "icon": "ⓘ"},
            ],
            "view_all_alerts": "View all alerts",
            "actions_title": "Quick actions",
            "actions": [
                {"label": "New invoice", "icon": "✚"},
                {"label": "New customer", "icon": "♙"},
                {"label": "New supplier", "icon": "▰"},
                {"label": "Add item", "icon": "□"},
                {"label": "Cash movement", "icon": "▣"},
                {"label": "Print report", "icon": "▤"},
            ],
            "view_all_actions": "View all actions",
            "sales_trend": "Sales trend (7 days)",
            "cash_credit": "Cash vs credit",
            "top_items": "Top products / services",
            "customer_dues": "Customer dues",
            "supplier_dues": "Supplier payables",
            "top_list": [
                {"name": "Design service", "value": "12,450"},
                {"name": "Product B", "value": "8,750"},
                {"name": "Product C", "value": "6,300"},
                {"name": "Consulting", "value": "4,895"},
                {"name": "Product D", "value": "3,250"},
            ],
            "overdue": "Overdue",
            "current": "Current",
            "pending": "Pending",
            "onboarding_title": "Start your Hesba experience in 4 steps",
            "onboarding_subtitle": "Set up your account and connect your business easily.",
            "steps": [
                {"label": "Business data", "icon": "🏪"},
                {"label": "Customers & suppliers", "icon": "♙"},
                {"label": "Items & services", "icon": "◼"},
                {"label": "First transaction", "icon": "▤"},
            ],
            "start_now": "Start now",
        }
    else:
        context = {
            "lang": "ar",
            "dir": "rtl",
            "other_lang": "en",
            "page_title": "لوحة تحكم حِسْبَة v3",
            "brand_title": "حِسْبَة",
            "owner_role": "صاحب الحساب",
            "language_label": "العربية",
            "current_time": "09:42",
            "current_date": "السبت 17 مايو 2025",
            "greeting": "صباح الخير، أحمد",
            "hero_subtitle": "أداء أعمالك في تحسن مستمر، استمر بنفس الزخم!",
            "status_chip": "أداء ممتاز",
            "out_of_100": "من 100",
            "score_items": ["المبيعات", "الربحية", "استحقاقات العملاء", "رضا العملاء"],
            "currency": "ريال سعودي",
            "kpis": [
                {"label": "مبيعات اليوم", "value": "32,450.00", "delta": "↑ 18%", "direction": "", "icon": "▮"},
                {"label": "صافي الربح", "value": "6,250.75", "delta": "↑ 14%", "direction": "", "icon": "↗"},
                {"label": "رصيد الخزنة", "value": "78,920.50", "delta": "↑ 5%", "direction": "", "icon": "▣"},
                {"label": "مستحقات العملاء", "value": "56,340.00", "delta": "↓ 3%", "direction": "down", "icon": "♙"},
                {"label": "مستحقات الموردين", "value": "34,780.00", "delta": "↓ 2%", "direction": "", "icon": "▰"},
                {"label": "مصروفات اليوم", "value": "4,120.30", "delta": "↑ 8%", "direction": "", "icon": "□"},
            ],
            "alerts_title": "التنبيهات الذكية",
            "alerts": [
                {"title": "رصيد الخزينة الرئيسي منخفض", "note": "تحديث قبل 35 دقيقة", "level": "عاجل", "color": "red", "icon": "🔔"},
                {"title": "3 فواتير عملاء مستحقة اليوم", "note": "بقيمة 8,750 ريال", "level": "متوسطة", "color": "orange", "icon": "⏱"},
                {"title": "12 صنفًا على وشك نفاد المخزون", "note": "تحقق من المخزون", "level": "معلومة", "color": "blue", "icon": "ⓘ"},
            ],
            "view_all_alerts": "عرض جميع التنبيهات",
            "actions_title": "إجراءات سريعة",
            "actions": [
                {"label": "فاتورة جديدة", "icon": "✚"},
                {"label": "عميل جديد", "icon": "♙"},
                {"label": "مورد جديد", "icon": "▰"},
                {"label": "إضافة صنف", "icon": "□"},
                {"label": "حركة خزنة", "icon": "▣"},
                {"label": "طباعة تقرير", "icon": "▤"},
            ],
            "view_all_actions": "عرض كل الإجراءات",
            "sales_trend": "اتجاه المبيعات (7 أيام)",
            "cash_credit": "نقدي مقابل آجل",
            "top_items": "أعلى المنتجات / الخدمات",
            "customer_dues": "مستحقات العملاء",
            "supplier_dues": "مستحقات الموردين",
            "top_list": [
                {"name": "خدمة تصميم", "value": "12,450"},
                {"name": "منتج ب", "value": "8,750"},
                {"name": "منتج ج", "value": "6,300"},
                {"name": "خدمة استشارية", "value": "4,895"},
                {"name": "منتج د", "value": "3,250"},
            ],
            "overdue": "متأخرة",
            "current": "جارية",
            "pending": "لم يحل",
            "onboarding_title": "ابدأ تجربة حِسْبَة في 4 خطوات",
            "onboarding_subtitle": "قم بإعداد حسابك وربط أعمالك بسهولة",
            "steps": [
                {"label": "بيانات نشاطك", "icon": "🏪"},
                {"label": "عملاء وموردين", "icon": "♙"},
                {"label": "منتجات وخدمات", "icon": "◼"},
                {"label": "أول عملية يومية", "icon": "▤"},
            ],
            "start_now": "ابدأ الآن",
        }

    context["checkpoint_code"] = DASHBOARD_CHECKPOINT_CODE
    return render(request, "reports/dashboard_v3_preview.html", context)


def report_hub(request):
    """Read-only report hub."""

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
