from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone


DASHBOARD_CHECKPOINT_CODE = "094_FOUNDATION_DASHBOARD_SNAPSHOT"


def _admin_changelist(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_changelist")


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _date_time(lang):
    now = timezone.localtime(timezone.now())
    if lang == "en":
        return {"date": now.strftime("%d %b %Y"), "time": now.strftime("%I:%M %p").lstrip("0"), "locale": "en-GB"}
    months = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
    suffix = "ص" if now.hour < 12 else "م"
    hour = now.hour % 12 or 12
    return {"date": f"{now.day} {months[now.month - 1]} {now.year}", "time": f"{hour}:{now.minute:02d} {suffix}", "locale": "ar-EG"}


def _copy(lang):
    return {
        "ar": {
            "header_label": "رأس لوحة التحكم", "profile_label": "بيانات المستخدم", "notifications": "الإشعارات", "language": "اللغة", "date_time": "التاريخ والوقت", "page_heading": "عنوان الصفحة", "dashboard": "لوحة التحكم", "page_title_short": "الرئيسية التشغيلية", "open_menu": "فتح القائمة", "close_menu": "إغلاق القائمة", "main_label": "محتوى لوحة التحكم", "live_status": "حالة الاتصال", "live": "مباشر", "last_updated": "آخر تحديث", "greeting": "مرحبًا أحمد 👋", "hero_title": "أعمالك تسير في الاتجاه الصحيح", "hero_subtitle_prefix": "مبيعات هذا الشهر ارتفعت", "hero_growth": "18%", "hero_subtitle_suffix": "مقارنة بالشهر الماضي", "sample_badge": "بيانات عرض آمنة للمعاينة فقط", "auto_refresh": "تحديث تلقائي", "kpi_label": "مؤشرات الأداء", "quick_actions": "إجراءات سريعة", "show_all": "عرض الكل", "analytics": "تحليلات لوحة التحكم", "sales_last_7_days": "المبيعات خلال آخر 7 أيام", "total_sales": "إجمالي المبيعات", "safe_sample": "عينة آمنة", "sales_by_category": "توزيع المبيعات حسب الفئة", "monthly_sales": "المبيعات والمصروفات الشهرية", "recent_operations": "آخر العمليات", "smart_insights": "تنبيهات ذكية", "refresh_note": "تحديث تلقائي كل 30 ثانية", "auto": "تلقائي", "bottom_nav": "تنقل الموبايل", "more": "المزيد", "reports": "تقارير", "new_action": "إجراء جديد", "customers": "العملاء", "home": "الرئيسية", "drawer_label": "قائمة لوحة التحكم", "read_only": "قراءة فقط",
        },
        "en": {
            "header_label": "Dashboard header", "profile_label": "User profile", "notifications": "Notifications", "language": "Language", "date_time": "Date and time", "page_heading": "Page heading", "dashboard": "Dashboard", "page_title_short": "Operational home", "open_menu": "Open menu", "close_menu": "Close menu", "main_label": "Dashboard content", "live_status": "Live status", "live": "Live", "last_updated": "Updated", "greeting": "Welcome Ahmed 👋", "hero_title": "Your business is moving in the right direction", "hero_subtitle_prefix": "This month sales increased", "hero_growth": "18%", "hero_subtitle_suffix": "versus last month", "sample_badge": "Safe preview sample data only", "auto_refresh": "Auto refresh", "kpi_label": "Key performance indicators", "quick_actions": "Quick actions", "show_all": "Show all", "analytics": "Dashboard analytics", "sales_last_7_days": "Sales over the last 7 days", "total_sales": "Total sales", "safe_sample": "Safe sample", "sales_by_category": "Sales distribution by category", "monthly_sales": "Monthly sales and expenses", "recent_operations": "Recent operations", "smart_insights": "Smart insights", "refresh_note": "Auto refresh every 30 seconds", "auto": "Auto", "bottom_nav": "Mobile navigation", "more": "More", "reports": "Reports", "new_action": "New action", "customers": "Customers", "home": "Home", "drawer_label": "Dashboard menu", "read_only": "Read-only",
        },
    }[lang]


def _dashboard_data(lang, currency):
    reports = reverse("report_hub")
    status = reverse("status_counts_report")
    sales = _admin_changelist("sales", "salesinvoice")
    customers = _admin_changelist("master_data", "customer")
    suppliers = _admin_changelist("master_data", "supplier")
    items = _admin_changelist("master_data", "item")
    if lang == "en":
        return {
            "hero_metrics": [
                {"label": "Sales growth (month)", "value": "+18%", "note": "vs last month", "icon": "sales-growth", "variant": "success"},
                {"label": "Cashbox balance", "value": "78,640", "note": currency, "icon": "cashbox", "variant": ""},
                {"label": "Inventory alerts", "value": "7", "note": "Alerts", "icon": "inventory-alert", "variant": "warning"},
                {"label": "Active customers", "value": "9,250", "note": "+6%", "icon": "customers", "variant": "success"},
            ],
            "kpis": [
                {"label": "Sales today", "value": "156,300", "note": "+12% vs yesterday", "icon": "sales-today", "variant": "success"},
                {"label": "Sales this month", "value": "986,540", "note": "+15% vs last month", "icon": "sales-growth", "variant": "success"},
                {"label": "Payments", "value": "78,640", "note": currency, "icon": "cashbox", "variant": ""},
                {"label": "Customers", "value": "9,250", "note": "+6% vs last month", "icon": "customers", "variant": "success"},
                {"label": "Low stock", "value": "476", "note": "Needs review", "icon": "inventory-alert", "variant": "warning"},
                {"label": "Available items", "value": "78,640", "note": "+11% vs last month", "icon": "items", "variant": ""},
                {"label": "Expenses this month", "value": "32,850", "note": "Masked by role later", "icon": "expenses", "variant": "blue"},
            ],
            "quick_actions": [
                {"label": "New invoice", "icon": "invoice", "url": sales}, {"label": "New customer", "icon": "customers", "url": customers}, {"label": "New product", "icon": "items", "url": items}, {"label": "Add expense", "icon": "expenses", "url": "#dashboard-expense-placeholder"}, {"label": "Print report", "icon": "pdf", "url": reports}, {"label": "Customize", "icon": "settings", "url": "#dashboard-settings-placeholder"}, {"label": "Reports", "icon": "reports", "url": reports},
            ],
            "categories": [{"label": "Electronics", "percent": "35%"}, {"label": "Accessories", "percent": "25%"}, {"label": "Clothing", "percent": "20%"}, {"label": "Services", "percent": "12%"}, {"label": "Other", "percent": "8%"}],
            "recent_operations": [{"code": "#INV-10045", "label": "Sales invoice", "time": "10:42", "icon": "sales-today", "variant": "success", "url": status}, {"code": "#RC-10032", "label": "Payment received", "time": "10:35", "icon": "cashbox", "variant": "success", "url": status}, {"code": "#BIL-10201", "label": "Purchase invoice", "time": "10:28", "icon": "invoice", "variant": "warning", "url": status}, {"code": "#EXP-10015", "label": "Expense", "time": "10:21", "icon": "expenses", "variant": "danger", "url": status}, {"code": "#INV-10044", "label": "Sales invoice", "time": "10:15", "icon": "sales-today", "variant": "success", "url": status}],
            "insights": [{"title": "Low stock", "note": "12 products need reorder", "icon": "warning", "variant": "danger", "url": reports, "link_label": "View products"}, {"title": "Late invoices", "note": "18 invoices overdue", "icon": "warning", "variant": "warning", "url": reports, "link_label": "View invoices"}, {"title": "Sales performance", "note": "+15% vs last month", "icon": "sales-growth", "variant": "blue", "url": reports, "link_label": "Details"}, {"title": "Top product", "note": "Highest monthly sales", "icon": "success", "variant": "success", "url": reports, "link_label": "Details"}, {"title": "Faster collection", "note": "3 customers have dues", "icon": "fast", "variant": "blue", "url": reports, "link_label": "Customers"}, {"title": "Growth chance", "note": "New product is growing", "icon": "opportunity", "variant": "purple", "url": reports, "link_label": "Details"}],
            "drawer_items": [{"label": "Dashboard", "icon": "sales-today", "url": reverse("dashboard_snapshot")}, {"label": "Reports", "icon": "reports", "url": reports}, {"label": "Status", "icon": "success", "url": status}, {"label": "Customers", "icon": "customers", "url": customers}, {"label": "Suppliers", "icon": "suppliers", "url": suppliers}, {"label": "Items", "icon": "items", "url": items}],
        }
    return {
        "hero_metrics": [
            {"label": "نمو المبيعات (الشهر)", "value": "+18%", "note": "مقارنة بالشهر الماضي", "icon": "sales-growth", "variant": "success"}, {"label": "رصيد الصندوق", "value": "78,640", "note": currency, "icon": "cashbox", "variant": ""}, {"label": "تنبيهات المخزون", "value": "7", "note": "تنبيهات", "icon": "inventory-alert", "variant": "warning"}, {"label": "العملاء النشطون", "value": "9,250", "note": "+6%", "icon": "customers", "variant": "success"},
        ],
        "kpis": [
            {"label": "إجمالي المبيعات (اليوم)", "value": "156,300", "note": "+12% عن أمس", "icon": "sales-today", "variant": "success"}, {"label": "إجمالي المبيعات (الشهر)", "value": "986,540", "note": "+15% عن الشهر الماضي", "icon": "sales-growth", "variant": "success"}, {"label": "إجمالي المدفوعات", "value": "78,640", "note": currency, "icon": "cashbox", "variant": ""}, {"label": "إجمالي العملاء", "value": "9,250", "note": "+6% عن الشهر الماضي", "icon": "customers", "variant": "success"}, {"label": "المنتجات المنخفضة", "value": "476", "note": "تحتاج مراجعة", "icon": "inventory-alert", "variant": "warning"}, {"label": "المنتجات المتاحة", "value": "78,640", "note": "+11% عن الشهر الماضي", "icon": "items", "variant": ""}, {"label": "إجمالي المصروفات (الشهر)", "value": "32,850", "note": "الأرقام الحساسة ستُحجب حسب الدور", "icon": "expenses", "variant": "blue"},
        ],
        "quick_actions": [{"label": "فاتورة جديدة", "icon": "invoice", "url": sales}, {"label": "عميل جديد", "icon": "customers", "url": customers}, {"label": "منتج جديد", "icon": "items", "url": items}, {"label": "إضافة مصروف", "icon": "expenses", "url": "#dashboard-expense-placeholder"}, {"label": "طباعة تقرير", "icon": "pdf", "url": reports}, {"label": "تخصيص", "icon": "settings", "url": "#dashboard-settings-placeholder"}, {"label": "التقارير", "icon": "reports", "url": reports}],
        "categories": [{"label": "الإلكترونيات", "percent": "35%"}, {"label": "الإكسسوارات", "percent": "25%"}, {"label": "الملابس", "percent": "20%"}, {"label": "الخدمات", "percent": "12%"}, {"label": "أخرى", "percent": "8%"}],
        "recent_operations": [{"code": "#INV-10045", "label": "فاتورة مبيعات", "time": "10:42 ص", "icon": "sales-today", "variant": "success", "url": status}, {"code": "#RC-10032", "label": "إيصال دفعة", "time": "10:35 ص", "icon": "cashbox", "variant": "success", "url": status}, {"code": "#BIL-10201", "label": "فاتورة شراء", "time": "10:28 ص", "icon": "invoice", "variant": "warning", "url": status}, {"code": "#EXP-10015", "label": "مصروفات", "time": "10:21 ص", "icon": "expenses", "variant": "danger", "url": status}, {"code": "#INV-10044", "label": "فاتورة مبيعات", "time": "10:15 ص", "icon": "sales-today", "variant": "success", "url": status}],
        "insights": [{"title": "مخزون منخفض", "note": "12 منتج بحاجة لإعادة طلب", "icon": "warning", "variant": "danger", "url": reports, "link_label": "عرض المنتجات"}, {"title": "فواتير متأخرة", "note": "18 فاتورة متأخرة", "icon": "warning", "variant": "warning", "url": reports, "link_label": "عرض الفواتير"}, {"title": "أداء المبيعات", "note": "+15% عن الشهر الماضي", "icon": "sales-growth", "variant": "blue", "url": reports, "link_label": "عرض التفاصيل"}, {"title": "منتج مميز", "note": "الأعلى مبيعًا هذا الشهر", "icon": "success", "variant": "success", "url": reports, "link_label": "عرض التفاصيل"}, {"title": "تحصيل أسرع", "note": "3 عملاء لديهم مستحقات", "icon": "fast", "variant": "blue", "url": reports, "link_label": "عرض العملاء"}, {"title": "فرصة نمو", "note": "منتج جديد يحقق نموًا", "icon": "opportunity", "variant": "purple", "url": reports, "link_label": "عرض التفاصيل"}],
        "drawer_items": [{"label": "الرئيسية", "icon": "sales-today", "url": reverse("dashboard_snapshot")}, {"label": "التقارير", "icon": "reports", "url": reports}, {"label": "Status", "icon": "success", "url": status}, {"label": "العملاء", "icon": "customers", "url": customers}, {"label": "الموردون", "icon": "suppliers", "url": suppliers}, {"label": "الأصناف", "icon": "items", "url": items}],
    }


def dashboard_120d(request):
    lang = _lang(request)
    dt = _date_time(lang)
    currency = "Activity currency" if lang == "en" else "عملة النشاط"
    data = _dashboard_data(lang, currency)
    user = getattr(request, "user", None)
    is_known_user = bool(getattr(user, "is_authenticated", False))
    user_display_name = (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or ("Ahmed" if lang == "en" else "أحمد")) if is_known_user else ("Ahmed" if lang == "en" else "أحمد")
    context = {
        "checkpoint_code": DASHBOARD_CHECKPOINT_CODE,
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "time_locale": dt["locale"],
        "page_title": "Hesba Dashboard" if lang == "en" else "لوحة التحكم - حِسْبَة",
        "t": _copy(lang),
        "formatted_date": dt["date"],
        "formatted_time": dt["time"],
        "activity_currency": currency,
        "user_display_name": user_display_name,
        "user_initial": (user_display_name.strip()[:1] or ("A" if lang == "en" else "أ")).upper(),
        "user_role_label": "Owner" if lang == "en" else "المالك",
        "notification_count": "3" if lang == "en" else "٣",
        "chart_total": "986,540",
        "dashboard_url": reverse("dashboard_snapshot"),
        "reports_url": reverse("report_hub"),
        "status_url": reverse("status_counts_report"),
        "report_hub_url": reverse("report_hub"),
        "arabic_url": f"{reverse('dashboard_snapshot')}?lang=ar",
        "english_url": f"{reverse('dashboard_snapshot')}?lang=en",
        "primary_action_url": _admin_changelist("sales", "salesinvoice"),
        "customers_url": _admin_changelist("master_data", "customer"),
        **data,
    }
    return render(request, "reports/dashboard.html", context)
