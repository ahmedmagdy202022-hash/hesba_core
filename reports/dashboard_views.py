"""The daily dashboard.

Every figure comes from reports.selectors, which docs/dashboard_kpis.md
requires: "Dashboard cards must read from report logic only." Which cards
appear is decided by permission, so the per-role sets in that document fall out
of the seeded matrix rather than being restated here.

Run seed_demo_business to fill a local database with enough trade for this
screen to look like a working business.
"""

from django.shortcuts import render
from django.utils import timezone, translation
from django.utils.formats import date_format

from permissions.decorators import permitted_codes
from permissions.services import user_has_permission
from settings_core.models import ClientProfile
from settings_core.setup_services import usable_modules
from settings_core.templatetags.hesba_format import money as display_money

from .dashboard_data import (
    DashboardFigures,
    SharedReads,
    build_alerts,
    has_any_business_data,
    health_band,
    health_score,
    onboarding_progress,
)
from .dashboard_kpis import (
    ALL_KPI_PERMISSIONS,
    COUNT,
    CURRENCY,
    LEVEL,
    SCOPE_OWN,
    visible_kpis,
)


CHECKPOINT_CODE = "120F_DASHBOARD_LIVE_DATA"

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
    {"key": "operations", "ar": "عمليات البيع", "en": "Sales operations", "url_name": "sales:list", "module": "sales_operations", "permission": "sales.view_sales_invoices"},
    {"key": "purchases", "ar": "المشتريات", "en": "Purchases", "url_name": "purchases:list", "module": "purchases", "permission": "purchases.view_purchase_invoices"},
    {"key": "inventory", "ar": "المخزون", "en": "Inventory", "url_name": "inventory:stock", "module": "inventory", "permission": "inventory.view_stock"},
    {"key": "customers", "ar": "العملاء", "en": "Customers", "url_name": "master_data:customers", "module": "customers"},
    {"key": "suppliers", "ar": "الموردون", "en": "Suppliers", "url_name": "master_data:suppliers", "module": "suppliers"},
    {"key": "items", "ar": "الأصناف والخدمات", "en": "Items & services", "url_name": "master_data:items", "module": "items_services"},
    {"key": "cashboxes", "ar": "الخزائن", "en": "Cashboxes", "url_name": "cashboxes:list", "module": "cashboxes", "permission": "cashboxes.view_cashboxes"},
    {"key": "reports", "ar": "التقارير", "en": "Reports", "url_name": "report_hub", "module": "reports"},
    {"key": "closing", "ar": "إقفال الفترات", "en": "Period closing", "url_name": "closing:list", "module": None, "permission": "closing.run_closing"},
    {"key": "profile", "ar": "ملفي", "en": "My profile", "url_name": "accounts:profile", "module": None},
    {"key": "settings", "ar": "الإعدادات", "en": "Settings", "url_name": "settings_core:overview", "module": None, "permission": "settings.view_settings"},
)

# Read-only shortcuts. business_rules.md keeps dashboards read-only, so these
# navigate and never post.
QUICK_ACTIONS = (
    {"key": "record_sale", "ar": "تسجيل عملية بيع", "en": "Record a sale", "primary": False, "module": "sales_operations", "url_name": "sales:create", "permission": "sales.create_sales_invoice"},
    {"key": "record_purchase", "ar": "تسجيل فاتورة شراء", "en": "Record a purchase", "primary": False, "module": "purchases", "url_name": "purchases:create", "permission": "purchases.create_purchase_invoice"},
    {"key": "new_customer", "ar": "عميل جديد", "en": "New customer", "primary": False, "module": "customers", "url_name": "master_data:customer_create", "permission": "master_data.manage_parties"},
    {"key": "new_supplier", "ar": "مورد جديد", "en": "New supplier", "primary": False, "module": "suppliers", "url_name": "master_data:supplier_create", "permission": "master_data.manage_parties"},
    {"key": "new_item", "ar": "صنف / خدمة جديدة", "en": "New item or service", "primary": False, "module": "items_services", "url_name": "master_data:item_create", "permission": "master_data.manage_items"},
    {"key": "collect", "ar": "تحصيل من عميل", "en": "Collect from a customer", "primary": False, "module": "customers", "url_name": "sales:payment_create", "permission": "sales.receive_customer_payment"},
    {"key": "pay_supplier", "ar": "سداد لمورد", "en": "Pay a supplier", "primary": False, "module": "suppliers", "url_name": "purchases:payment_create", "permission": "purchases.pay_supplier"},
    {"key": "open_reports", "ar": "فتح التقارير", "en": "Open reports", "primary": False, "module": "reports", "url_name": "report_hub"},
    {"key": "close_day", "ar": "إقفال الفترة", "en": "Close a period", "primary": True, "module": None, "url_name": "closing:list", "permission": "closing.run_closing"},
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
        "kpi_title": "أرقام اليوم",
        "alerts_title": "محتاج انتباهك",
        "alerts_empty": "لا توجد تنبيهات تحتاج متابعة.",
        "actions_title": "ابدأ من هنا",
        "onboarding_title": "ابدأ تشغيل حِسْبَة في ٤ خطوات",
        "onboarding_note": "اتبع الخطوات لبدء تشغيل نشاطك على حِسْبَة.",
        "no_cards": "لا توجد أرقام متاحة لصلاحياتك الحالية.",
        "step_done": "تم",
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
        "kpi_title": "Today's numbers",
        "alerts_title": "Needs your attention",
        "alerts_empty": "Nothing needs following up.",
        "actions_title": "Start here",
        "onboarding_title": "Start using Hesba in 4 steps",
        "onboarding_note": "Follow these steps to get your business running on Hesba.",
        "no_cards": "No figures are available for your permissions.",
        "step_done": "Done",
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


def _format_value(kpi, raw, lang):
    # Branch order is load-bearing: int() below raises on a level label, and
    # only the currency branch may fall through to the shared money filter.
    if kpi.unit == LEVEL:
        return USAGE_LEVEL_LABELS.get(raw, {}).get(lang, str(raw))
    if kpi.unit == COUNT:
        return f"{int(raw):,}"
    # Money keeps its piastres. Rendering 1234.56 as "1,235" showed the owner
    # more than was stored, with nothing to say it had been rounded. The shared
    # filter is used rather than a second formatter here so the dashboard can
    # never drift from the rest of the application.
    return display_money(raw)


def _build_cards(user, lang, held, figures):
    cards = []

    for kpi, scope in visible_kpis(held):
        raw = figures.value_for(kpi.key, scope)
        cards.append(
            {
                "key": kpi.key,
                "label": kpi.label(lang, scope),
                "value": _format_value(kpi, raw, lang),
                "raw": raw,
                "unit": kpi.unit,
                "is_count": kpi.unit == COUNT,
                "is_level": kpi.unit == LEVEL,
                "is_money": kpi.unit == CURRENCY,
                # A currency card reading zero is worth saying out loud rather
                # than leaving the owner to wonder whether it failed to load.
                "is_zero": kpi.unit != LEVEL and raw == 0,
                "scope": scope,
                "sensitive": kpi.sensitive,
            }
        )
    return cards


def _nav(user, lang, modules):
    items = []
    for item in NAV_ITEMS:
        if item["module"] is not None and item["module"] not in modules:
            continue
        permission = item.get("permission")
        if permission and not user_has_permission(user, permission):
            continue
        items.append({"key": item["key"], "label": item[lang], "url_name": item["url_name"]})
    return items


def _quick_actions(user, lang, modules):
    actions = []
    for action in QUICK_ACTIONS:
        if action["module"] is not None and action["module"] not in modules:
            continue
        permission = action.get("permission")
        if permission and not user_has_permission(user, permission):
            continue
        actions.append({"key": action["key"], "label": action[lang], "primary": action["primary"], "url_name": action["url_name"]})
    return actions


def _alerts(lang, strings, held, today, shared):
    return [
        {
            "key": alert["key"],
            "severity": alert["severity"],
            "severity_label": strings[f"severity_{alert['severity']}"],
            "title": alert[lang],
            "detail": alert["detail_en"] if lang == "en" else alert["detail_ar"],
            "amount": alert["amount"],
        }
        for alert in build_alerts(held, today, shared)
    ]


def _onboarding(lang):
    """The four starting steps, with the ones already done marked off."""

    done = onboarding_progress()
    return [
        {"label": step[lang], "done": is_done}
        for step, is_done in zip(ONBOARDING_STEPS, done)
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
    today = now.date()
    profile = ClientProfile.get_active()
    modules = set(usable_modules())

    held = permitted_codes(request.user, ALL_KPI_PERMISSIONS)
    # One shared read set for the cards, the alerts and the score, so the three
    # do not each re-run the same stock and party queries.
    shared = SharedReads()
    figures = DashboardFigures(request.user, today, shared)
    cards = _build_cards(request.user, lang, held, figures)

    health = health_score(today, held, shared)
    band_key, band_words = health_band(health["score"])
    has_data = has_any_business_data()

    context = {
        "checkpoint_code": CHECKPOINT_CODE,
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "greeting": _greeting(lang, now),
        "display_name": _display_name(request.user),
        "name_separator": ", " if lang == "en" else "، ",
        "now": now,
        "now_parts": _formatted_now(lang, now),
        "client_name": profile.display_name if profile is not None else "",
        "activity_slug": profile.activity_slug if profile is not None else "",
        "health_score": health["score"],
        "health_band": band_key,
        "show_health": health["available"],
        "health_note": band_words[lang],
        "health_reasons": health["reasons"],
        "nav_items": _nav(request.user, lang, modules),
        "cards": cards,
        "alerts": _alerts(lang, strings, held, today, shared),
        "quick_actions": _quick_actions(request.user, lang, modules),
        "onboarding_steps": _onboarding(lang),
        # Guide someone whose installation has seen no trade yet, and anyone who
        # can see nothing at all. A working business does not need the steps.
        "show_onboarding": not has_data or not cards,
        "has_business_data": has_data,
        **strings,
    }
    return render(request, "reports/dashboard.html", context)
