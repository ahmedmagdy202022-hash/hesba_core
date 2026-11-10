"""A1 (HG-079): the voice summary's text, built from the dashboard's own figures. No language model is involved.

Every figure is one the dashboard already shows, read through the same code (``reports.dashboard_data``), under the same
permission (``reports.dashboard_kpis``), the same fit to the business (``reports.dashboard_fit``) and the entity being
worked in (HG-034: ``reports.selectors`` and ``entities.scope``). So the summary and the dashboard can never disagree,
and a figure the listener may not see is left out of the text, never read as zero:

* yesterday's posted sales, and their count (the "Sales today" / "Invoices today" cards, for yesterday's date; only the
  listener's own invoices when they may not see everyone's);
* yesterday's collections (the "Receipts today" card, for yesterday);
* the items running low and, when there are any, the items out of stock (the stock cards, today; never added together);
* today's appointments, when the appointments section is open to the listener (the dashboard's bookings panel count).

The day is the Cairo day (``timezone.localdate()``). The text holds no clock time and no time-of-day greeting, so it stays
the same all day unless a figure changes: the cache (``ai.voice_summary``) voices each distinct text once.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from hashlib import sha256
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import date_format

from entities.current import current_entity
from permissions.decorators import permitted_codes
from reports.dashboard_data import DashboardFigures, SharedReads
from reports.dashboard_kpis import ALL_KPI_PERMISSIONS, SCOPE_OWN, visible_kpis
from settings_core.models import ClientProfile
from settings_core.templatetags.hesba_format import money

#: The dashboard figures the summary reads (``reports.dashboard_kpis`` keys).
SALES, INVOICES, RECEIPTS, LOW, OUT = "sales_today", "invoice_count_today", "receipts_today", "low_stock_count", "out_of_stock_count"
SUMMARY_KPIS = (SALES, INVOICES, RECEIPTS, LOW, OUT)

WORDS = {
    "ar": {
        "title": "ملخص الصبح", "play": "شغّل الملخص", "pause": "وقّف الملخص", "loading": "بنجهّز الصوت…",
        "note": "الأرقام من تقارير حسبة نفسها. الصوت بس اللي متولّد.",
        "sales": "مبيعات امبارح", "my_sales": "مبيعاتي امبارح", "invoices": "فواتير امبارح", "my_invoices": "فواتيري امبارح",
        "receipts": "التحصيل", "low": "قربت تخلص", "appointments": "مواعيد النهارده",
        "press_again": "الصوت جاهز. دوس تشغيل تاني.", "failed": "الصوت مااشتغلش. جرّب تاني.",
        "all_collections": "افتح قايمة التحصيلات كلها",
    },
    "en": {
        "title": "Morning summary", "play": "Play the summary", "pause": "Pause the summary", "loading": "Preparing the audio…",
        "note": "The figures come from Hesba's own reports. Only the voice is generated.",
        "sales": "Yesterday's sales", "my_sales": "My sales yesterday", "invoices": "Invoices yesterday", "my_invoices": "My invoices yesterday",
        "receipts": "Collections", "low": "Running low", "appointments": "Today's appointments",
        "press_again": "The audio is ready. Press play again.", "failed": "The audio did not play. Try again.",
        "all_collections": "Open the full collections list",
    },
}


@dataclass
class Summary:
    lang: str
    day: date
    entity: object
    text: str = ""
    tiles: list = field(default_factory=list)
    day_label: str = ""

    @property
    def has_figures(self):
        return bool(self.tiles)

    @property
    def yesterday(self):
        return self.day - timedelta(days=1)

    @property
    def scope_key(self):
        return scope_key(self.entity)

    @property
    def text_sha256(self):
        return sha256(self.text.encode("utf-8")).hexdigest()

    def as_dict(self):
        return {"lang": self.lang, "day": self.day.isoformat(), "day_label": self.day_label, "text": self.text,
                "figures": [{key: tile[key] for key in ("key", "label", "value", "sub", "url")} for tile in self.tiles]}


def scope_key(entity):
    return f"entity:{entity.pk}" if entity is not None else "group"


# ---- words ----

def _ar_count(n, singular, plural):
    """«3 أصناف» but «11 صنف»: Arabic takes the plural from three to ten."""

    return f"{n} {plural if 3 <= n <= 10 else singular}"


def _en_count(n, singular, plural=None):
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _spoken_currency(lang):
    """The currency as a word to be read aloud («جنيه مصري», "Egyptian pounds"), not the screen's symbol («ج.م»)."""

    from settings_core.currency import CURRENCIES

    profile = ClientProfile.get_active()
    code = profile.default_currency if profile is not None else ""
    names = dict(CURRENCIES).get(code)
    if not names:
        return code
    return names["ar"] if lang == "ar" else f"{names['en']}s"


def _screen_currency(lang):
    from settings_core.currency import currency_label

    profile = ClientProfile.get_active()
    return currency_label(profile.default_currency, lang) if profile is not None else ""


def _link(user, name, lang, **params):
    """The report behind a figure, or "" when the listener could not open it."""

    from settings_core.operational_views import _opens_for

    url = reverse(name)
    if not _opens_for(user, url):
        return ""
    return f"{url}?{urlencode({**params, 'lang': lang})}"


def _tile(key, label, value, url="", sub="", tone=""):
    return {"key": key, "label": label, "value": value, "url": url, "sub": sub, "tone": tone}


# ---- the figures ----

def _appointments_today(user, lang, today, sections):
    """Today's appointments, not counting cancelled ones (the dashboard's bookings panel), or None when the listener does
    not have the appointments section open (module off, or no permission)."""

    if sections is None:
        from reports.navigation import nav_items
        from settings_core.setup_services import usable_modules

        sections = {item["key"] for item in nav_items(user, lang, set(usable_modules()))}
    if "appointments" not in sections:
        return None
    from appointments.models import AppointmentStatus
    from appointments.services import day_agenda

    return day_agenda(today).exclude(status=AppointmentStatus.CANCELLED).count()


def build_summary(user, lang="ar", today=None, shared=None, sections=None):
    """The summary ``user`` may hear, in ``lang``, for the Cairo day ``today`` (default: today), in the current entity."""

    from reports.dashboard_fit import Fit

    lang = "en" if lang == "en" else "ar"
    today = today or timezone.localdate()
    yesterday = today - timedelta(days=1)
    words = WORDS[lang]
    fit = Fit.current(today)
    held = permitted_codes(user, ALL_KPI_PERMISSIONS)
    visible = {kpi.key: scope for kpi, scope in visible_kpis(held) if kpi.key in SUMMARY_KPIS and fit.shows(f"kpi.{kpi.key}")}
    shared = shared or SharedReads()
    past = DashboardFigures(user, yesterday, shared)
    present = DashboardFigures(user, today, shared)

    with translation.override(lang):
        day_label = date_format(yesterday, "j F Y")  # UX Wave 1: the figures are yesterday's; one date format
    summary = Summary(lang=lang, day=today, entity=current_entity(), day_label=day_label)
    spoken_currency = _spoken_currency(lang)
    screen_currency = _screen_currency(lang)

    def said(amount):
        return f"{money(amount)} {spoken_currency}".strip()

    def shown(amount):
        return f"{money(amount)} {screen_currency}".strip()

    figures = {key: (past if key in (SALES, INVOICES, RECEIPTS) else present).value_for(key, scope) for key, scope in visible.items()}
    own = {key: scope == SCOPE_OWN for key, scope in visible.items()}
    appointments = _appointments_today(user, lang, today, sections)

    sentences = ["Hello, here is today's summary." if lang == "en" else "أهلاً، ده ملخص النهارده."]
    tiles = []
    sales_link = _link(user, "reports:sales", lang, date_from=yesterday.isoformat(), date_to=yesterday.isoformat(), status="posted")

    # Yesterday's sales and their count.
    if SALES in figures:
        count = figures.get(INVOICES)
        if lang == "en":
            sentence = f"{'Your sales' if own[SALES] else 'Sales'} yesterday came to {said(figures[SALES])}"
            sentence += f" from {_en_count(count, 'invoice')}." if count is not None else "."
            sub = _en_count(count, "invoice") if count is not None else ""
        else:
            sentence = f"امبارح {'مبيعاتك' if own[SALES] else 'المبيعات'} كانت {said(figures[SALES])}"
            sentence += f" من {_ar_count(count, 'فاتورة', 'فواتير')}." if count is not None else "."
            sub = _ar_count(count, "فاتورة", "فواتير") if count is not None else ""
        sentences.append(sentence)
        tiles.append(_tile("sales", words["my_sales" if own[SALES] else "sales"], shown(figures[SALES]), sales_link, sub))
    elif INVOICES in figures:
        count = figures[INVOICES]
        if lang == "en":
            sentences.append(f"{'You made' if own[INVOICES] else 'There were'} {_en_count(count, 'sales invoice')} yesterday.")
        else:
            sentences.append(f"امبارح {'عملت' if own[INVOICES] else 'اتعمل'} {_ar_count(count, 'فاتورة بيع', 'فواتير بيع')}.")
        # The sales report is the screen that takes yesterday's date range (the invoice list has no date filter).
        tiles.append(_tile("invoices", words["my_invoices" if own[INVOICES] else "invoices"], str(count), sales_link))

    # Yesterday's collections. The dashboard's receipts card has no own scope (no scope permission): whoever may see it
    # sees the whole shop's, so the summary says the same.
    if RECEIPTS in figures:
        if lang == "en":
            sentences.append(f"Collections came to {said(figures[RECEIPTS])}.")
        else:
            sentences.append(f"والتحصيل كان {said(figures[RECEIPTS])}.")
        # No screen lists collections by date, so the link says it opens the whole list, not yesterday's.
        receipts_link = _link(user, "sales:payments", lang, status="posted")
        tiles.append(_tile("receipts", words["receipts"], shown(figures[RECEIPTS]),
                           receipts_link, words["all_collections"] if receipts_link else ""))

    # Stock running low (and out), today. Two figures, never added together.
    if LOW in figures:
        low, out = figures[LOW], figures.get(OUT)
        if lang == "en":
            if low:
                sentence = f"{_en_count(low, 'item')} {'is' if low == 1 else 'are'} running low"
            else:
                sentence = "No item is running low"
            sentence += f", and {_en_count(out, 'item')} {'is' if out == 1 else 'are'} out of stock." if out else "."
            value = _en_count(low, "item")
        else:
            sentence = f"فيه {_ar_count(low, 'صنف', 'أصناف')} قربت تخلص" if low else "مفيش أصناف قربت تخلص"
            sentence += f"، و{_ar_count(out, 'صنف', 'أصناف')} خلصت من المخزن." if out else "."
            value = _ar_count(low, "صنف", "أصناف")
        sentences.append(sentence)
        tiles.append(_tile("low_stock", words["low"], value, _link(user, "reports:inventory", lang), tone="warn" if low else ""))

    # Today's appointments.
    if appointments is not None:
        if lang == "en":
            sentences.append(f"You have {_en_count(appointments, 'appointment')} today." if appointments else "You have no appointments today.")
        else:
            sentences.append(f"والنهارده عندك {_ar_count(appointments, 'ميعاد', 'مواعيد')}." if appointments else "ومفيش مواعيد النهارده.")
        tiles.append(_tile("appointments", words["appointments"], str(appointments), _link(user, "appointments:agenda", lang, day=today.isoformat())))

    summary.tiles = tiles
    summary.text = " ".join(sentences) if tiles else ""
    return summary
