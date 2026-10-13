"""FB-REPORTS: every report and every list can be printed (or saved as PDF from the print window) and downloaded for Excel.

One switch for the whole app instead of a button per screen: the screens that
read figures (every report, every list, the ledgers, stock and movements, in
every activity) get the same small toolbar. It works on what the page shows,
so it exports exactly what this viewer may see, in the language and with the
filters on the screen. The download is a CSV that Excel opens in Arabic (UTF-8
with a byte-order mark), built in the browser from the page's tables and its
summary figures.
"""

#: Screens that read rather than write. Any url name containing "report" also counts.
READING = frozenset({
    "list", "index", "items", "customers", "suppliers", "categories", "movements", "stock", "warehouses", "warehouse_levels",
    "warehouse_detail", "payments", "operations", "cashboxes", "trial_balance", "summary", "statements", "statement",
    "claims", "aging", "group", "packages", "hours", "visits", "students", "patients", "history", "agenda", "dues", "where",
    "insights", "daily", "reps", "profit", "sales", "purchases", "inventory", "commissions", "portfolio", "board_report",
})
#: Screens that look like readers by name but are forms or tills.
NEVER = frozenset({"create", "new", "edit", "pos", "post", "cancel"})

WORDS = {
    "ar": {"print": "اطبع / PDF", "excel": "تنزيل Excel", "printed": "اتطبع", "hint": "من نافذة الطباعة تقدر تختار «حفظ كـ PDF»."},
    "en": {"print": "Print / PDF", "excel": "Download for Excel", "printed": "Printed", "hint": "In the print window you can choose “Save as PDF”."},
}


def shows_tools(match):
    if match is None or not match.url_name or match.url_name in NEVER:
        return False
    if match.namespace == "reports":
        return True
    return "report" in match.url_name or match.url_name in READING


def report_tools(request):
    """Context processor: the toolbar's switch and words, on reading screens only."""

    if not shows_tools(getattr(request, "resolver_match", None)):
        return {}
    from settings_core.models import ClientProfile

    lang = "en" if request.GET.get("lang") == "en" else "ar"
    profile = ClientProfile.get_active()
    return {"report_tools": dict(WORDS[lang], company=profile.display_name if profile else "")}
