"""R2-8: the insights report page."""

from django.shortcuts import render

from permissions.decorators import require_permission

from .functional_views import _context, _lang
from .insights import build_insights


@require_permission("reports.view_all_sales_report")
def insights_report(request):
    lang = _lang(request)
    return render(request, "reports/insights.html", _context(request, data=build_insights(request.user, lang), page_title="تحليل النشاط" if lang == "ar" else "Business insights"))
