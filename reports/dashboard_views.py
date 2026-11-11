"""The daily dashboard.

Every figure comes from reports.selectors, which docs/dashboard_kpis.md
requires: "Dashboard cards must read from report logic only." Which cards
appear is decided by permission, so the per-role sets in that document fall out
of the seeded matrix rather than being restated here.

Run seed_demo_business to fill a local database with enough trade for this
screen to look like a working business.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.shortcuts import render
from django.utils import timezone, translation
from django.utils.formats import date_format

from permissions.decorators import permitted_codes

from .analytics import PERIODS, build_analytics
from permissions.services import user_has_permission
from settings_core.models import ClientProfile
from settings_core.setup_services import usable_modules
from settings_core.templatetags.hesba_format import day_text, money as display_money

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
from .activity_panel import build_activity_panel
from .navigation import DASHBOARD_SHORTCUTS, display_name, nav_items, shortcut_actions
from settings_core.currency import currency_label


logger = logging.getLogger(__name__)

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
        "huge_up": "زيادة كبيرة جدًا عن الفترة اللي قبلها",  # MT-06: «▲ 1396%» says nothing; above HUGE_DELTA_PCT words replace the number. No "N times": the change is measured against the size of the previous figure, which may have been a loss
        "huge_down": "هبوط حاد عن الفترة اللي قبلها",
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
        "today_more": "أرقام النهارده",
        "top_actions_title": "اختصارات",
        "flat": "زي الفترة اللي قبلها",
        "alerts_title": "محتاج انتباهك",
        "alerts_empty": "مفيش تنبيهات محتاجة متابعة.",
        "phero_none": "لسه مفيش بيع النهارده؛ أول فاتورة هتظهر هنا.",  # UX Wave 4: the phone money card on a quiet day
        "actions_title": "ابدأ من هنا",
        "onboarding_title": "ابدأ تشغيل حِسْبَة في ٤ خطوات",
        "onboarding_note": "اتبع الخطوات لبدء تشغيل نشاطك على حِسْبَة.",
        "no_cards": "مفيش أرقام تقدر تشوفها بصلاحياتك دلوقتي.",
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
        "split_note": "من فواتير البيع النهائية في الفترة.",
        "split_empty": "مفيش فواتير بيع في الفترة دي.",
        "pause": "إيقاف التقليب",
        "play": "تشغيل التقليب",
        "prev_range": "من {start} لـ {end}",
        "hero_headline": "صافي مبيعات {period}",
        "hero_live": "شغّال دلوقتي",
        "hero_trend": "اتجاه المبيعات",
        "hero_last_7_days": "آخر ٧ أيام",
        "hero_today": "مبيعات النهارده",
        "hero_vs_avg": "من متوسط يومك",
        "hero_avg": "متوسط اليوم",
        "hero_invoices": "فاتورة النهارده",
        "hero_cash": "في الخزن",
        "hero_lines": {"morning": "يوم جديد وفرصة جديدة. خلّينا نبدأ.", "afternoon": "نص اليوم عدّى، شوف وصلت لفين.", "evening": "قرّبنا نقفل اليوم، دي الصورة كاملة.", "night": "سهران؟ الأرقام كلها قدامك."},
    },
    "en": {
        "page_title": "Dashboard - Hesba",
        "screen_title": "Dashboard",
        "notifications": "Notifications",
        "analytics_title": "Business performance",
        "how_label": "How is this calculated?",
        "vs_previous": "vs the previous period",
        "huge_up": "far above the previous period",
        "huge_down": "a sharp fall vs the previous period",
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
        "today_more": "Today's numbers",
        "top_actions_title": "Shortcuts",
        "flat": "same as the previous period",
        "alerts_title": "Needs your attention",
        "alerts_empty": "Nothing needs following up.",
        "phero_none": "No sales yet today; the first invoice shows here.",
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
        "hero_live": "Live now",
        "hero_trend": "Sales trend",
        "hero_last_7_days": "Last 7 days",
        "hero_today": "Sales today",
        "hero_vs_avg": "of your average day",
        "hero_avg": "Average day",
        "hero_invoices": "invoices today",
        "hero_cash": "Cash on hand",
        "hero_lines": {"morning": "A new day, a fresh start. Let's go.", "afternoon": "Halfway through the day: here's where you are.", "evening": "Nearly closing time: here's the full picture.", "night": "Working late? Everything is right here."},
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _day_part(now):
    if 5 <= now.hour < 12:
        return "morning"
    if 12 <= now.hour < 17:
        return "afternoon"
    if 17 <= now.hour < 24:
        return "evening"
    return "night"


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
            "date": date_format(now, "l j F Y"),  # UX Wave 1: one date format on the page ("9 نوفمبر 2026")
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

    from settings_core.vocabulary import term

    for kpi, scope in visible_kpis(held):
        raw = figures.value_for(kpi.key, scope)
        cards.append(
            {
                "key": kpi.key,
                # ACT-PROFILE-001: a clinic owes nothing to "customers"; its patients do.
                "label": term("customer_dues", lang) if kpi.key == "customer_dues" else kpi.label(lang, scope),
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


# ACT-PROFILE-002: the action an activity does all day leads its quick actions.
ACTIVITY_ACTIONS = {
    "restaurants": {"key": "open_tables", "section": "restaurant", "url_name": "restaurant:board", "ar": "الطاولات والطلبات", "en": "Tables & orders"},
    "medical": {"key": "bookings", "section": "appointments", "url_name": "appointments:agenda", "ar": "الحجوزات وكشوفات النهارده", "en": "Today's bookings"},
    "education": {"key": "classes", "section": "education", "url_name": "education:today", "ar": "حصص النهارده والحضور", "en": "Today's classes"},
    "services": {"key": "bookings", "section": "appointments", "url_name": "appointments:agenda", "ar": "مواعيد النهارده", "en": "Today's appointments"},
    "manufacturing": {"key": "produce", "section": "manufacturing", "url_name": "manufacturing:home", "ar": "تشغيلة إنتاج", "en": "Production run"},
    "contracting": {"key": "projects", "section": "projects", "url_name": "projects:list", "ar": "المشاريع", "en": "Projects"},
}

QUICK_ACTION_TERMS = {"record_sale": "record_sale", "new_customer": "new_customer", "new_supplier": "new_supplier", "new_item": "new_item", "collect": "collect"}


def _quick_actions(user, lang, modules, activity="", sections=frozenset()):
    from django.urls import reverse

    from settings_core.module_gate import closed_module
    from settings_core.vocabulary import active_vocabulary, term

    words = active_vocabulary()
    actions = []
    lead = ACTIVITY_ACTIONS.get(activity)
    if activity == "services" and "workorders" in sections and user_has_permission(user, "sales.create_sales_invoice"):  # SVC-001: a workshop's day starts at the counter
        lead = {"key": "workorders", "section": "workorders", "url_name": "workorders:new", "ar": "استلام جهاز / طلب", "en": "Receive a device"}
    if activity == "services" and "my_day" in sections:  # S9: a technician's day starts with their own jobs
        lead = {"key": "my_day", "section": "my_day", "url_name": "staff:my_day", "ar": "شغلي النهارده", "en": "My jobs today"}
    if lead and lead["section"] in sections:
        actions.append({"key": lead["key"], "label": lead[lang], "primary": True, "url_name": lead["url_name"], "hint": ""})
    for action in QUICK_ACTIONS:
        if action["module"] is not None and action["module"] not in modules:
            continue
        permission = action.get("permission")
        if permission and not user_has_permission(user, permission):
            continue
        if closed_module(reverse(action["url_name"])) is not None:  # AUDIT-1: never a button into a switched-off module
            continue
        label = term(QUICK_ACTION_TERMS[action["key"]], lang, words) if action["key"] in QUICK_ACTION_TERMS else action[lang]
        actions.append({"key": action["key"], "label": label, "primary": action["primary"] and not actions, "url_name": action["url_name"],
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


def _transfer_alerts(user, lang, strings, modules):
    """AUDIT-2: a stock keeper's day starts with the transfers waiting on them."""

    if "inventory" not in modules or not user_has_permission(user, "inventory.transfer_stock"):
        return []
    from entities import scope as entity_scope
    from inventory.transfer_requests import waiting_counts
    from master_data.models import Location

    counts = waiting_counts(entity_scope.locations(Location.objects))
    rows = []
    for key, severity, ar, en, detail_ar, detail_en in (
        ("to_send", "soon", "طلب تحويل مستني تبعته", "transfer request(s) waiting for you to send", "مخزن تاني طالب بضاعة من عندك.", "Another warehouse asked you for stock."),
        ("to_receive", "urgent", "تحويل في الطريق مستني تستلمه", "transfer(s) on the way for you to receive", "البضاعة اتبعتت؛ أكّد اللي وصل.", "The goods were sent; confirm what arrived."),
    ):
        if counts[key]:
            rows.append({"key": f"transfers_{key}", "severity": severity, "severity_label": strings[f"severity_{severity}"],
                         "title": f"{counts[key]} {en}" if lang == "en" else f"{counts[key]} {ar}",
                         "detail": detail_en if lang == "en" else detail_ar, "amount": "", "path": "/inventory/warehouses/requests/"})
    return rows


def _approval_alerts(user, lang, strings):
    """FB-27: the requests waiting for this person's approval."""

    from approvals.services import waiting_for

    count = waiting_for(user).count()
    if not count:
        return []
    return [{"key": "approvals", "severity": "urgent", "severity_label": strings["severity_urgent"],
             "title": f"{count} request(s) waiting for your approval" if lang == "en" else f"{count} طلب مستني موافقتك",
             "detail": "A colleague asked to cancel or reverse something; nothing changes until you decide." if lang == "en"
             else "زميل طلب إلغاء أو عكس حاجة؛ مفيش حاجة بتتغير قبل ما تقرر.", "amount": "", "path": "/approvals/"}]


def _onboarding(lang):
    """The four starting steps, with the ones already done marked off."""

    done = onboarding_progress()
    return [
        {"label": step[lang], "done": is_done}
        for step, is_done in zip(ONBOARDING_STEPS, done)
    ]


def _demo_bar(request, lang, profile):
    """DEMO-FIRST-RUN (D3): the one-line demo bar on a showcase. Everyone signed in sees it (it names the business and
    offers «غيّر النشاط» and the other login); only the owner gets «ابدأ فاضي» (``demo_bar`` decides, for «ابدأ من هنا» too)."""

    from django.conf import settings

    if not getattr(settings, "DEMO_MODE", False):
        return None
    from config.demo_first_run import demo_bar

    # TRACK-START S01: the bar names the shop's own activity; «غيّر النشاط» opens a copy of another ready shop,
    # so the words, the menu and the sample data always belong to the same business.
    return demo_bar(request, lang, profile)


def _demo_mode():
    from django.conf import settings

    return bool(getattr(settings, "DEMO_MODE", False))


def _demo_tour(request, lang, profile):
    if not _demo_mode():
        return None
    from config.demo_first_run import tour_card

    return tour_card(request, lang, profile)


def _voice_card(user, lang, today, shared, sections, fit):
    """A1 (HG-079): the optional voice summary card. It must never break the dashboard: any error drops the card only."""

    from ai.voice_summary import card_context

    try:
        return card_context(user, lang, today, shared=shared, sections=sections, capabilities=fit.capabilities)
    except Exception:
        logger.exception("A1 (HG-079): the voice summary card failed; the dashboard is shown without it.")
        return None


def _ask_link(lang, fit, user):
    """A2 (HG-080): the «اسأل حسبة» button, only while its capability is on (read from the set already loaded) and
    only for someone who may use at least one of its tools."""

    if "ai_ask" not in fit.capabilities:
        return None
    from django.urls import reverse

    from ai.ask import can_ask

    if not can_ask(user):
        return None

    return {"url": f"{reverse('ai:ask')}?lang={lang}", "label": "Ask Hesba" if lang == "en" else "اسأل حسبة",
            "hint": "Ask about your sales, stock, cash or customers" if lang == "en" else "اسأل عن مبيعاتك ومخزونك وخزنتك وعملاءك"}


#: UX Wave 1: a KPI card that repeats a figure shown higher up on the page, and the analytics metric that shows it.
REPEATED_BY_METRIC = {"cashbox_balance": "cash", "customer_dues": "receivables", "supplier_dues": "payables"}


#: MT-06: above this many percent a trend is told in words («زيادة كبيرة جدًا»), not as a number nobody can use.
HUGE_DELTA_PCT = 300


def _shown_delta(metric):
    """UX Wave 1, display only: a trend reads "▲ 15%" or "▼ 73%" (the arrow carries the sign, never "▼ -73%"), and a
    change that rounds to 0% reads "—". A copy: the metric itself (and the analytics behind it) is never changed."""

    if not metric:
        return metric
    shown = dict(metric)
    delta = metric.get("delta")
    if delta is not None:
        rounded = int(Decimal(str(abs(delta))).quantize(Decimal("1"), ROUND_HALF_UP))  # half up, as floatformat:0 did
        shown["delta_abs"] = rounded
        shown["flat"] = rounded == 0
        shown["huge"] = rounded > HUGE_DELTA_PCT
    return shown


def _layout(cards, analytics, headline):
    """UX Wave 1: each figure once. The hero keeps the period's net sales and today's sales; the analytics grid drops the
    net sales it would repeat and keeps the cash, receivables and payables it explains; «أرقام اليوم» keeps only what
    shows nowhere else. Returns new lists; ``cards`` and ``analytics`` (which tests and other code read) stay as they are."""

    metrics = (analytics.get("metrics") or []) if analytics.get("available") else []
    metric_keys = {metric["key"] for metric in metrics}
    keys = {card["key"] for card in cards}
    # The hero shows today's invoice count only under today's sales: without that card the count stays in «أرقام اليوم».
    hero_keys = {"sales_today", "invoice_count_today"} & keys if "sales_today" in keys else set()
    hero_cash = any(card["key"] == "cashbox_balance" for card in cards) and "cash" not in metric_keys
    if hero_cash:
        hero_keys.add("cashbox_balance")
    shown_metrics = [_shown_delta(metric) for metric in metrics if not (headline and metric is headline)]
    today_cards = [card for card in cards if card["key"] not in hero_keys and REPEATED_BY_METRIC.get(card["key"]) not in metric_keys]
    # hero_any: the hero already shows a figure, so an empty «أرقام اليوم» says nothing rather than "no figures".
    return {"hero_shows_cash": hero_cash, "hero_any": "sales_today" in keys or hero_cash, "shown_metrics": shown_metrics,
            "today_cards": today_cards}


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
    if profile is not None and not profile.setup_is_complete and (_demo_mode() or request.user.is_superuser):
        # DEMO-FIRST-RUN §7: no half-set-up dashboard for whoever can finish the setup (the owner, or anyone on a
        # showcase); the gate sends them to the wizard step they are at. Anyone else keeps the dashboard they may see.
        from django.shortcuts import redirect

        return redirect("after_login")
    modules = set(usable_modules())

    held = permitted_codes(request.user, ALL_KPI_PERMISSIONS)
    # One shared read set for the cards, the alerts and the score, so the three
    # do not each re-run the same stock and party queries.
    shared = SharedReads()
    figures = DashboardFigures(request.user, today, shared)
    from .dashboard_fit import Fit, fit_analytics, option_panel

    fit = Fit.current(today)  # DASH-FIT: only what this business is and chose in setup
    cards = [card for card in _build_cards(request.user, lang, held, figures) if fit.shows(f"kpi.{card['key']}")]

    from entities.current import effective_activity

    activity = effective_activity()[0] or ""
    sections = {item["key"] for item in nav_items(request.user, lang, modules)}
    period = request.GET.get("period", "month")
    analytics = fit_analytics(build_analytics(permitted_codes(request.user, ANALYTICS_PERMISSIONS), period, today, lang), fit)
    has_data = has_any_business_data()
    period_label = PERIOD_LABELS[lang].get(analytics["period"], "")
    previous_range = (analytics.get("daily") or {}).get("previous_range")
    headline = None
    if analytics.get("available") and analytics["metrics"]:
        headline = analytics["metrics"][0]
    layout = _layout(cards, analytics, headline)
    top_actions = shortcut_actions(request.user, lang, modules, sections, DASHBOARD_SHORTCUTS, dashboard=True)

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
        "activity_slug": activity,
        # The installation's own currency, not a hard-coded "EGP". Empty before
        # bootstrap, in which case the template shows no unit at all.
        "currency": currency_label(profile.default_currency, lang) if profile is not None else "",  # TRACK-START S16
        # DASH-002: explained analytics replace the old penalty-based score.
        "analytics": analytics,
        "periods": [(key, PERIOD_LABELS[lang][key]) for key in PERIODS],
        "hero_headline_text": strings["hero_headline"].format(period=period_label),
        "day_part": _day_part(now),
        "hero_line": strings["hero_lines"][_day_part(now)],
        "hero_cards": {card["key"]: card for card in cards if card["key"] in ("sales_today", "invoice_count_today", "cashbox_balance")},
        "headline": headline,
        "headline_shown": _shown_delta(headline),
        **layout,
        # UX Wave 1: the four everyday shortcuts right under the hero; the «ابدأ من هنا» list skips the ones shown there.
        "top_actions": top_actions,
        "top_action_urls": [action["url_name"] for action in top_actions],
        "previous_range_text": strings["prev_range"].format(start=day_text(previous_range[0], lang), end=day_text(previous_range[1], lang)) if previous_range else "",
        # The app shell draws the navigation from the same list; it stays in
        # the context so the section set can be asserted without parsing HTML.
        "nav_items": nav_items(request.user, lang, modules),
        "cards": cards,
        "alerts": [alert for alert in _alerts(lang, strings, held, today, shared) if fit.shows_alert(alert["key"])]
                  + _transfer_alerts(request.user, lang, strings, modules) + _approval_alerts(request.user, lang, strings),
        "option_panel": option_panel(fit, request.user, lang),
        "quick_actions": _quick_actions(request.user, lang, modules, activity, sections),
        "activity_panel": build_activity_panel(activity, sections, lang, today, request.user),
        # A1 (HG-079): the voice summary card, only while its capability is on (read from the set already loaded).
        "voice_summary": _voice_card(request.user, lang, today, shared, sections, fit),
        "ask_link": _ask_link(lang, fit, request.user),  # A2 (HG-080)
        "demo_bar": _demo_bar(request, lang, profile),
        "demo_tour": _demo_tour(request, lang, profile),
        # DEMO-FEEDBACK: the one-time hello (pages are recorded; leave a note) shows here, after the first content page.
        "demo_hello": _demo_mode(),
        "onboarding_steps": _onboarding(lang),
        # Guide someone whose installation has seen no trade yet, and anyone who
        # can see nothing at all. A working business does not need the steps.
        # AUDIT-2: the setup steps are the owner's job; a cashier or a stock
        # keeper with no figure cards sees their quick actions instead.
        "show_onboarding": user_has_permission(request.user, "settings.manage_settings") and (not has_data or not cards),
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
