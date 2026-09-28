"""DIGEST-001 screen: one day's summary, with a WhatsApp button that sends it as text."""

from datetime import date, timedelta
from urllib.parse import quote

from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from permissions.services import user_has_permission
from printing.company import company_details

from .digest import daily_digest, digest_text


def daily_summary(request):
    if not (user_has_permission(request.user, "reports.view_sales_report") or user_has_permission(request.user, "reports.view_cashbox_report")):
        raise PermissionDenied("The daily summary needs a sales or cashbox report permission.")
    lang = "en" if request.GET.get("lang") == "en" else "ar"
    today = timezone.localdate()
    try:
        day = date.fromisoformat(request.GET.get("date") or "")
    except ValueError:
        day = today - timedelta(days=1)
    day = min(day, today)
    data = daily_digest(request.user, day)
    company = company_details()
    text = digest_text(data, company, lang)
    return render(request, "reports/digest.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "page_title": "Daily summary" if lang == "en" else "ملخص اليوم", "section": "reports",
        "data": data, "day": day, "prev": day - timedelta(days=1), "next": day + timedelta(days=1) if day < today else None, "today": today,
        "whatsapp": f"https://wa.me/?text={quote(text)}", "text": text, "currency": company["currency"],
    })
