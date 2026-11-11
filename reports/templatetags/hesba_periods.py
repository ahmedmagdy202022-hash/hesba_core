"""UX Wave 3: period chips for a report filtered by date_from / date_to («النهارده / الأسبوع / الشهر / مخصوص»).

Presentation only. Each chip is a plain link that fills the report's own date_from and date_to, the same two filters
its date boxes send, so the report computes exactly what it computed before. The ranges are the dashboard's
(reports.analytics.period_bounds): today, the last 7 days (labelled so, as on the dashboard), the month to date. «مخصوص» is the date boxes themselves:
it is the current chip whenever the dates match no preset, including no dates at all (the whole history, as before).
"""

from datetime import date
from urllib.parse import urlencode

from django import template
from django.utils import timezone
from django.utils.html import format_html

from reports.analytics import period_bounds

register = template.Library()

LABELS = {
    "ar": {"today": "النهارده", "week": "آخر 7 أيام", "month": "الشهر", "custom": "مخصوص"},
    "en": {"today": "Today", "week": "Last 7 days", "month": "This month", "custom": "Custom"},
}
#: chip -> the dashboard's period key
PRESETS = (("today", "today"), ("week", "7d"), ("month", "month"))


@register.simple_tag
def period_chips(date_from="", date_to="", lang="ar", today=None):
    """[{key, label, url, current, start, end}] for the chips; ``url`` is a query string ("?lang=..&date_from=..") or "#report-dates"."""

    today = today or timezone.localdate()
    words = LABELS["en" if lang == "en" else "ar"]
    lang = "en" if lang == "en" else "ar"
    chips, matched = [], False
    for key, period in PRESETS:
        start, end = period_bounds(period, today)[:2]
        # On the 1st of the month «النهارده» and «الشهر» cover the same day: only the first match is current.
        current = not matched and (date_from, date_to) == (start.isoformat(), end.isoformat())
        matched = matched or current
        chips.append({"key": key, "label": words[key], "current": current, "start": start, "end": end,
                      "url": "?" + urlencode({"lang": lang, "date_from": start.isoformat(), "date_to": end.isoformat()})})
    # «مخصوص» opens nothing new: it takes you to the report's own date boxes on the same page.
    chips.append({"key": "custom", "label": words["custom"], "current": not matched, "start": None, "end": None, "url": "#report-dates"})
    return chips


def _day(raw):
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None  # the report ignores a date it cannot read, so the line does too


@register.simple_tag
def period_text(date_from="", date_to="", lang="ar"):
    """The period a report is showing, in words: «كل الفترة», or the from-to dates in the app's date format.

    Reads the same two raw strings the date boxes and chips send, so it can never say anything the report is not doing.
    """

    from settings_core.templatetags.hesba_format import day_text

    lang = "en" if lang == "en" else "ar"
    start, end = _day(date_from), _day(date_to)
    if start is None and end is None:
        return "Showing: all time" if lang == "en" else "الفترة المعروضة: كل الفترة"
    if start and end and start == end:
        return format_html("Showing: <bdi>{}</bdi>" if lang == "en" else "الفترة المعروضة: يوم <bdi>{}</bdi>", day_text(start, lang))
    if start and end:
        return format_html("Showing: from <bdi>{}</bdi> to <bdi>{}</bdi>" if lang == "en" else "الفترة المعروضة: من <bdi>{}</bdi> لحد <bdi>{}</bdi>", day_text(start, lang), day_text(end, lang))
    if start:
        return format_html("Showing: from <bdi>{}</bdi> onwards" if lang == "en" else "الفترة المعروضة: من <bdi>{}</bdi> وبعده", day_text(start, lang))
    return format_html("Showing: up to <bdi>{}</bdi>" if lang == "en" else "الفترة المعروضة: لحد <bdi>{}</bdi>", day_text(end, lang))
