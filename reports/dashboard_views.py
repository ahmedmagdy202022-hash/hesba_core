"""The daily dashboard.

This is the mock stage docs/120A asks for: the layout and the role-aware
structure are real, the figures are not. Every card here is chosen by the same
permission resolver the wired version will use, so replacing MOCK_VALUES with
calls into reports.selectors is the only change the next stage needs.
"""

from django.shortcuts import render
from django.utils import timezone, translation
from django.utils.formats import date_format

from permissions.decorators import permitted_codes
from settings_core.models import ClientProfile
from settings_core.setup_services import usable_modules

from .dashboard_kpis import (
    ALL_KPI_PERMISSIONS,
    COUNT,
    LEVEL,
    SCOPE_OWN,
    visible_kpis,
)


CHECKPOINT_CODE = "120D_DASHBOARD_MOCK"

#: Placeholder figures. Replaced by reports.selectors in the wiring stage; the
#: keys are the contract between the two.
MOCK_VALUES = {
    "sales_today": "4,820",
    "invoice_count_today": "17",
    "purchases_today": "2,150",
    "profit_today": "1,340",
    "cashbox_balance": "18,600",
    "customer_dues": "9,480",
    "supplier_dues": "6,200",
    "receipts_today": "3,100",
    "supplier_payments_today": "1,800",
    "low_stock_count": "6",
    "out_of_stock_count": "2",
    "usage_status": "green",
}

MOCK_OWN_VALUES = {
    "sales_today": "1,260",
    "invoice_count_today": "5",
}

MOCK_TRENDS = {
    "sales_today": "+12%",
    "purchases_today": "-4%",
    "profit_today": "+8%",
    "customer_dues": "+3%",
}

USAGE_LEVEL_LABELS = {
    "green": {"ar": "طبيعي", "en": "Normal"},
    "yellow": {"ar": "في ارتفاع", "en": "Rising"},
    "orange": {"ar": "قريب من الحد", "en": "Near limit"},
    "red": {"ar": "يحتاج تصرف", "en": "Needs action"},
}

GREETINGS = (
    (5, 12, {"ar": "صباح الخير", "en": "Good morning"}),
    (12, 17, {"ar": "نهارك سعيد", "en": "Good afternoon"}),
    (17, 24, {"ar": "مساء الخير", "en": "Good evening"}),
    (0, 5, {"ar": "أهلًا", "en": "Hello"}),
)

NAV_ITEMS = (
    {"key": "dashboard", "ar": "لوحة القيادة", "en": "Dashboard", "url_name": "dashboard_snapshot", "module": None},
    {"key": "operations", "ar": "العمليات", "en": "Operations", "url_name": "home", "module": "sales_operations"},
    {"key": "customers", "ar": "العملاء", "en": "Customers", "url_name": "home", "module": "customers"},
    {"key": "suppliers", "ar": "الموردون", "en": "Suppliers", "url_name": "home", "module": "suppliers"},
    {"key": "items", "ar": "الأصناف والخدمات", "en": "Items & services", "url_name": "home", "module": "items_services"},
    {"key": "cashboxes", "ar": "الخزائن", "en": "Cashboxes", "url_name": "home", "module": "cashboxes"},
    {"key": "reports", "ar": "التقارير", "en": "Reports", "url_name": "report_hub", "module": "reports"},
    {"key": "settings", "ar": "الإعدادات", "en": "Settings", "url_name": "status_counts_report", "module": None},
)

# Read-only shortcuts. business_rules.md keeps dashboards read-only, so these
# navigate and never post.
QUICK_ACTIONS = (
    {"key": "record_sale", "ar": "تسجيل عملية", "en": "Record a sale", "primary": False, "module": "sales_operations"},
    {"key": "new_customer", "ar": "عميل جديد", "en": "New customer", "primary": False, "module": "customers"},
    {"key": "new_supplier", "ar": "مورد جديد", "en": "New supplier", "primary": False, "module": "suppliers"},
    {"key": "new_item", "ar": "صنف / خدمة جديدة", "en": "New item or service", "primary": False, "module": "items_services"},
    {"key": "collect", "ar": "تحصيل من عميل", "en": "Collect from a customer", "primary": False, "module": "customers"},
    {"key": "pay_supplier", "ar": "سداد لمورد", "en": "Pay a supplier", "primary": False, "module": "suppliers"},
    {"key": "print_reports", "ar": "طباعة التقارير", "en": "Print reports", "primary": False, "module": "reports"},
    {"key": "close_day", "ar": "إقفال اليوم", "en": "Close the day", "primary": True, "module": None},
)

MOCK_ALERTS = (
    {"key": "stock_below_min", "severity": "urgent", "ar": "٣ أصناف تحت الحد الأدنى", "en": "3 items below minimum",
     "detail_ar": "راجع الأصناف قبل نفاذها.", "detail_en": "Review before they run out.", "amount": ""},
    {"key": "customer_overdue", "severity": "urgent", "ar": "عميل متأخر في السداد", "en": "Customer payment overdue",
     "detail_ar": "مديونية متجاوزة الحد المسموح.", "detail_en": "Balance above the agreed limit.", "amount": "2,400"},
    {"key": "customer_due_soon", "severity": "soon", "ar": "دفعة عميل مستحقة قريبًا", "en": "Customer payment due soon",
     "detail_ar": "خلال ٣ أيام.", "detail_en": "Within 3 days.", "amount": "900"},
    {"key": "cashbox_low", "severity": "watch", "ar": "رصيد خزنة منخفض", "en": "Cashbox balance is low",
     "detail_ar": "الخزنة الفرعية تحت الحد المعتاد.", "detail_en": "Below its usual level.", "amount": "180"},
)

ONBOARDING_STEPS = (
    {"ar": "أضف خزنة", "en": "Add a cashbox"},
    {"ar": "أضف عميل أو مورد", "en": "Add a customer or supplier"},
    {"ar": "أضف صنف أو خدمة", "en": "Add an item or service"},
    {"ar": "سجل أول عملية", "en": "Record your first transaction"},
)

STRINGS = {
    "ar": {
        "page_title": "لوحة القيادة - حِسْبَة",
        "screen_title": "لوحة القيادة",
        "logout": "تسجيل الخروج",
        "language": "العربية",
        "menu": "القائمة",
        "notifications": "التنبيهات",
        "health_title": "مؤشر النشاط",
        "health_note": "نشاطك مستقر",
        "kpi_title": "أرقام اليوم",
        "alerts_title": "محتاج انتباهك",
        "alerts_empty": "لا توجد تنبيهات تحتاج متابعة.",
        "actions_title": "ابدأ من هنا",
        "onboarding_title": "ابدأ تشغيل حِسْبَة في ٤ خطوات",
        "onboarding_note": "لا توجد بيانات بعد. اتبع الخطوات لبدء التشغيل.",
        "no_cards": "لا توجد أرقام متاحة لصلاحياتك الحالية.",
        "mock_notice": "أرقام هذه الشاشة تجريبية للمراجعة البصرية فقط.",
        "severity_urgent": "عاجل",
        "severity_soon": "قريبًا",
        "severity_watch": "للمتابعة",
    },
    "en": {
        "page_title": "Dashboard - Hesba",
        "screen_title": "Dashboard",
        "logout": "Logout",
        "language": "English",
        "menu": "Menu",
        "notifications": "Notifications",
        "health_title": "Business health",
        "health_note": "Your business is steady",
        "kpi_title": "Today's numbers",
        "alerts_title": "Needs your attention",
        "alerts_empty": "Nothing needs following up.",
        "actions_title": "Start here",
        "onboarding_title": "Start using Hesba in 4 steps",
        "onboarding_note": "No data yet. Follow the steps to get going.",
        "no_cards": "No figures are available for your permissions.",
        "mock_notice": "The figures on this screen are placeholders for visual review.",
        "severity_urgent": "Urgent",
        "severity_soon": "Soon",
        "severity_watch": "Follow up",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _greeting(lang, now):
    for start, end, words in GREETINGS:
        if start <= now.hour < end:
            return words[lang]
    return GREETINGS[-1][2][lang]


def _formatted_now(lang, now):
    """Date and time in the language of the page, not the project default.

    LANGUAGE_CODE is Arabic and there is no LocaleMiddleware, so Django's date
    filter would name the month in Arabic even on the English page.
    """

    with translation.override(lang):
        return {
            "date": date_format(now, "l, d M Y"),
            "time": date_format(now, "h:i A"),
        }


def _display_name(user):
    profile = getattr(user, "hesba_profile", None)
    if profile is not None and profile.display_name:
        return profile.display_name
    return user.get_short_name() or user.get_username()


def _build_cards(user, lang):
    held = permitted_codes(user, ALL_KPI_PERMISSIONS)
    cards = []

    for kpi, scope in visible_kpis(held):
        if scope == SCOPE_OWN and kpi.key in MOCK_OWN_VALUES:
            value = MOCK_OWN_VALUES[kpi.key]
        else:
            value = MOCK_VALUES.get(kpi.key, "0")

        if kpi.unit == LEVEL:
            value = USAGE_LEVEL_LABELS.get(value, {}).get(lang, value)

        cards.append(
            {
                "key": kpi.key,
                "label": kpi.label(lang, scope),
                "value": value,
                "unit": kpi.unit,
                "is_count": kpi.unit == COUNT,
                "is_level": kpi.unit == LEVEL,
                "trend": MOCK_TRENDS.get(kpi.key, ""),
                "scope": scope,
                "sensitive": kpi.sensitive,
            }
        )
    return cards


def _nav(lang, modules):
    items = []
    for item in NAV_ITEMS:
        if item["module"] is not None and item["module"] not in modules:
            continue
        items.append({"key": item["key"], "label": item[lang], "url_name": item["url_name"]})
    return items


def _quick_actions(lang, modules):
    actions = []
    for action in QUICK_ACTIONS:
        if action["module"] is not None and action["module"] not in modules:
            continue
        actions.append({"key": action["key"], "label": action[lang], "primary": action["primary"]})
    return actions


def _alerts(lang, strings):
    return [
        {
            "key": alert["key"],
            "severity": alert["severity"],
            "severity_label": strings[f"severity_{alert['severity']}"],
            "title": alert[lang],
            "detail": alert["detail_en"] if lang == "en" else alert["detail_ar"],
            "amount": alert["amount"],
        }
        for alert in MOCK_ALERTS
    ]


def dashboard(request):
    """Render whatever this viewer is allowed to see, which may be nothing.

    Deliberately not gated as a whole. Every card checks its own permission, so
    someone holding none gets an empty dashboard and a note saying so — turning
    that into a 403 would put a wall on the page people land on after signing in,
    which is the trap the setup flow used to be.
    """

    lang = _lang(request)
    strings = STRINGS[lang]
    now = timezone.localtime()
    profile = ClientProfile.get_active()
    modules = set(usable_modules())

    cards = _build_cards(request.user, lang)

    context = {
        "checkpoint_code": CHECKPOINT_CODE,
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "is_mock": True,
        "greeting": _greeting(lang, now),
        "display_name": _display_name(request.user),
        "name_separator": ", " if lang == "en" else "، ",
        "now": now,
        "now_parts": _formatted_now(lang, now),
        "client_name": profile.display_name if profile is not None else "",
        "activity_slug": profile.activity_slug if profile is not None else "",
        "health_score": 82,
        "nav_items": _nav(lang, modules),
        "cards": cards,
        "alerts": _alerts(lang, strings),
        "quick_actions": _quick_actions(lang, modules),
        "onboarding_steps": [step[lang] for step in ONBOARDING_STEPS],
        # A mock always has figures, so onboarding only shows when the viewer can
        # see no cards at all. The wired version decides this from real counts.
        "show_onboarding": not cards,
        **strings,
    }
    return render(request, "reports/dashboard.html", context)
