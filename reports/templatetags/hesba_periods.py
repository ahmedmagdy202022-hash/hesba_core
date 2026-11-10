"""UX Wave 3: period chips for a report filtered by date_from / date_to («النهارده / الأسبوع / الشهر / مخصوص»).

Presentation only. Each chip is a plain link that fills the report's own date_from and date_to, the same two filters
its date boxes send, so the report computes exactly what it computed before. The ranges are the dashboard's
(reports.analytics.period_bounds): today, the last 7 days, the month to date. «مخصوص» is the date boxes themselves:
it is the current chip whenever the dates match no preset, including no dates at all (the whole history, as before).
"""

from urllib.parse import urlencode

from django import template
from django.utils import timezone

from reports.analytics import period_bounds

register = template.Library()

LABELS = {
    "ar": {"today": "النهارده", "week": "الأسبوع", "month": "الشهر", "custom": "مخصوص"},
    "en": {"today": "Today", "week": "This week", "month": "This month", "custom": "Custom"},
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
        current = (date_from, date_to) == (start.isoformat(), end.isoformat())
        matched = matched or current
        chips.append({"key": key, "label": words[key], "current": current, "start": start, "end": end,
                      "url": "?" + urlencode({"lang": lang, "date_from": start.isoformat(), "date_to": end.isoformat()})})
    # «مخصوص» opens nothing new: it takes you to the report's own date boxes on the same page.
    chips.append({"key": "custom", "label": words["custom"], "current": not matched, "start": None, "end": None, "url": "#report-dates"})
    return chips
