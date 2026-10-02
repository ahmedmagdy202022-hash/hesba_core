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

from .analytics import PERIODS, build_analytics
from permissions.services import user_has_permission
from settings_core.models import ClientProfile
from settings_core.setup_services import usable_modules
from settings_core.templatetags.hesba_format import money as display_money

from .dashboard_data import (
    DashboardFigures,
    SharedReads,
    build_alerts,
    has_any_business_data,
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
from .navigation import display_name, nav_items


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

# Read-only shortcuts. business_rules.md keeps dashboards read-only, so these
# navigate and never post.
QUICK_ACTIONS = (
    {"key": "record_sale", "ar": "تسجيل عملية بيع", "en": "Record a sale", "primary": True, "module": "sales_operations", "url_name": "sales:create", "permission": "sales.create_sales_invoice"},
    {"key": "record_purchase", "ar": "تسجيل فاتورة شراء", "en": "Record a purchase", "primary": False, "module": "purchases", "url_name": "purchases:create", "permission": "purchases.create_purchase_invoice"},
    {"key": "new_customer", "ar": "عميل جديد", "en": "New customer", "primary": False, "module": "customers", "url_name": "master_data:customer_create", "permission": "master_data.manage_parties"},
    {"key": "new_supplier", "ar": "مورد جديد", "en": "New supplier", "primary": False, "module": "suppliers", "url_name": "master_data:supplier_create", "permission": "master_data.manage_parties"},
    {"key": "new_item", "ar": "صنف / خدمة جديدة", "en": "New item or service", "primary": False, "module": "items_services", "url_name": "master_data:item_create", "permission": "master_data.manage_items"},
    {"key": "collect", "ar": "تحصيل من عميل", "en": "Collect from a customer", "primary": False, "module": "customers", "url_name": "sales:payment_create", "permission": "sales.receive_customer_payment"},
    {"key": "pay_supplier", "ar": "سداد لمورد", "en": "Pay a supplier", "primary": False, "module": "suppliers", "url_name": "purchases:payment_create", "permission": "purchases.pay_supplier"},
    {"key": "open_reports", "ar": "فتح التقارير", "en": "Open reports", "primary": False, "module": "reports", "url_name": "report_hub"},
    {"key": "close_day", "ar": "إقفال الشهر المحاسبي", "en": "Close the accounting month", "primary": False, "module": None, "url_name": "closing:list", "permission": "closing.run_closing"},
)

# DEMO-FEEDBACK: "close a period" did not say which period. It is the accounting
# month (closing app): once closed, nothing dated in it can be posted or changed.
CLOSE_HINT = {
    "ar": "بيقفل الشهر بعد ما تراجعه، فمحدش يقدر يسجّل أو يعدّل حاجة بتاريخ جوّاه. إقفال اليوم للكاشير من «الورديات».",
    "en": "Locks a month once you have reviewed it, so nothing dated in it can be recorded or changed. The cashier's end of day is under Shifts.",
}


def _alert_path(key):
    """Where an alert leads, so the bell and the list are links, not dead text."""

    if key in ("out_of_stock", "low_stock"):
        return "/reports/inventory/"
    if key.startswith("batches_"):
        return "/batches/"
    if key.startswith("customer_over_limit_"):
        return f"/parties/customer/{key.rsplit('_', 1)[1]}/"
    if key == "instalments_overdue":
        return "/instalments/"
    if key.startswith(("cashbox_negative_", "cashbox_low_")):
        return f"/cashboxes/{key.rsplit('_', 1)[1]}/"
    return ""


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
        "notifications": "التنبيهات",
        "analytics_title": "أداء النشاط",
        "how_label": "اتحسب إزاي؟",
        "vs_previous": "عن الفترة اللي قبلها",
        "daily_title": "المبيعات يوم بيوم",
        "daily_legend_now": "الفترة دي",
        "daily_legend_prev": "الفترة اللي قبلها",
        "best_day": "أعلى يوم",
        "hours_title": "ساعات الذروة (آخر 30 يوم)",
        "hours_peak": "أكتر ساعة بيع",
        "invoices_word": "فاتورة",
        "top_title_profit": "أكتر الأصناف ربحًا",
        "top_title_sales": "أكتر الأصناف مبيعًا",
        "slow_title": "بضاعة راكدة",
        "slow_note": "أصناف في المخزن ما اتباعتش من 60 يوم أو أكتر — فلوس نايمة على الرف.",
        "running_title": "هتخلص قريب",
        "running_note": "بمعدل بيعها في آخر 30 يوم، الكمية دي تكفي أسبوع أو أقل.",
        "days_word": "يوم",
        "never_sold": "ما اتباعش",
        "margin": "هامش",
        "overdue_word": "منه متأخر",
        "no_sales_chart": "مفيش مبيعات في الفترة دي لسه.",
        "table_view": "عرض كجدول",
        "date_col": "اليوم",
        "value_col": "المبيعات",
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
        "language": "English",
        "bell_empty": "مفيش تنبيهات دلوقتي.",
        "bell_all": "كل التنبيهات",
        "insights_title": "تحليلات",
        "tab_sales": "المبيعات",
        "tab_profit": "الربح",
        "tab_weekdays": "أيام الأسبوع",
        "tab_hours": "ساعات الذروة",
        "profit_daily_title": "مجمل الربح يوم بيوم",
        "weekdays_title": "متوسط البيع حسب يوم الأسبوع (آخر 4 أسابيع)",
        "weekdays_best": "أقوى يوم",
        "split_title": "اتدفع ولا آجل؟",
        "split_paid": "اتدفع وقت البيع",
        "split_credit": "آجل على العملاء",
        "split_note": "من فواتير البيع المرحّلة في الفترة.",
        "split_empty": "مفيش فواتير بيع في الفترة دي.",
        "pause": "إيقاف التقليب",
        "play": "تشغيل التقليب",
        "prev_range": "من {start} لـ {end}",
        "hero_headline": "صافي مبيعات {period}",
    },
    "en": {
        "page_title": "Dashboard - Hesba",
        "screen_title": "Dashboard",
        "notifications": "Notifications",
        "analytics_title": "Business performance",
        "how_label": "How is this calculated?",
        "vs_previous": "vs the previous period",
        "daily_title": "Sales by day",
        "daily_legend_now": "This period",
        "daily_legend_prev": "Previous period",
        "best_day": "Best day",
        "hours_title": "Peak hours (last 30 days)",
        "hours_peak": "Busiest hour",
        "invoices_word": "invoices",
        "top_title_profit": "Most profitable items",
        "top_title_sales": "Best-selling items",
        "slow_title": "Slow stock",
        "slow_note": "Items on the shelf with no sale for 60 days or more: money tied up.",
        "running_title": "Running out",
        "running_note": "At the last 30 days' rate, this stock lasts a week or less.",
        "days_word": "days",
        "never_sold": "never sold",
        "margin": "margin",
        "overdue_word": "overdue",
        "no_sales_chart": "No sales in this period yet.",
        "table_view": "Show as table",
        "date_col": "Day",
        "value_col": "Sales",
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
        "language": "العربية",
        "bell_empty": "Nothing needs attention right now.",
        "bell_all": "All alerts",
        "insights_title": "Insights",
        "tab_sales": "Sales",
        "tab_profit": "Profit",
        "tab_weekdays": "Weekdays",
        "tab_hours": "Peak hours",
        "profit_daily_title": "Gross profit by day",
        "weekdays_title": "Average sales by weekday (last 4 weeks)",
        "weekdays_best": "Strongest day",
        "split_title": "Paid or on credit?",
        "split_paid": "Paid at the till",
        "split_credit": "On customer credit",
        "split_note": "From posted sales invoices in the period.",
        "split_empty": "No sales invoices in this period.",
        "pause": "Pause rotation",
        "play": "Resume rotation",
        "prev_range": "{start} to {end}",
        "hero_headline": "Net sales · {period}",
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


def _quick_actions(user, lang, modules):
    actions = []
    for action in QUICK_ACTIONS:
        if action["module"] is not None and action["module"] not in modules:
            continue
        permission = action.get("permission")
        if permission and not user_has_permission(user, permission):
            continue
        actions.append({"key": action["key"], "label": action[lang], "primary": action["primary"], "url_name": action["url_name"],
                        "hint": CLOSE_HINT[lang] if action["key"] == "close_day" else ""})
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
            "path": _alert_path(alert["key"]),
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

    period = request.GET.get("period", "month")
    analytics = build_analytics(permitted_codes(request.user, ANALYTICS_PERMISSIONS), period, today, lang)
    has_data = has_any_business_data()
    period_label = PERIOD_LABELS[lang].get(analytics["period"], "")
    previous_range = (analytics.get("daily") or {}).get("previous_range")
    headline = None
    if analytics.get("available") and analytics["metrics"]:
        headline = analytics["metrics"][0]

    context = {
        "checkpoint_code": CHECKPOINT_CODE,
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "greeting": _greeting(lang, now),
        "display_name": display_name(request.user),
        "name_separator": ", " if lang == "en" else "، ",
        "now": now,
        "now_parts": _formatted_now(lang, now),
        "client_name": profile.display_name if profile is not None else "",
        "activity_slug": profile.activity_slug if profile is not None else "",
        # The installation's own currency, not a hard-coded "EGP". Empty before
        # bootstrap, in which case the template shows no unit at all.
        "currency": profile.default_currency if profile is not None else "",
        # DASH-002: explained analytics replace the old penalty-based score.
        "analytics": analytics,
        "periods": [(key, PERIOD_LABELS[lang][key]) for key in PERIODS],
        "hero_headline_text": strings["hero_headline"].format(period=period_label),
        "headline": headline,
        "previous_range_text": strings["prev_range"].format(start=previous_range[0].strftime("%d/%m"), end=previous_range[1].strftime("%d/%m")) if previous_range else "",
        # The app shell draws the navigation from the same list; it stays in
        # the context so the section set can be asserted without parsing HTML.
        "nav_items": nav_items(request.user, lang, modules),
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


ANALYTICS_PERMISSIONS = (
    "reports.view_sales_report",
    "reports.view_all_sales_report",
    "reports.view_profit_report",
    "reports.view_customer_report",
    "reports.view_supplier_report",
    "cashboxes.view_finance",
    "cashboxes.view_expenses",
    "inventory.view_cost",
)

PERIOD_LABELS = {
    "ar": {"today": "النهارده", "7d": "آخر 7 أيام", "month": "الشهر ده", "30d": "آخر 30 يوم"},
    "en": {"today": "Today", "7d": "Last 7 days", "month": "This month", "30d": "Last 30 days"},
}
