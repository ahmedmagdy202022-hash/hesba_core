from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from permissions.decorators import require_any_permission, require_permission
from permissions.services import user_has_permission

from .models import Period
from .services import complete_period_closing, reopen_period


STRINGS = {
    "ar": {"page_title": "إقفال الفترات", "periods": "الفترات المحاسبية", "dashboard": "لوحة القيادة", "language": "English", "back": "العودة للفترات", "empty": "لا توجد فترات.", "close": "إقفال الفترة", "reopen": "إعادة فتح الفترة", "closed": "تم إقفال الفترة وحفظ الملخصات.", "reopened": "تمت إعادة فتح الفترة.", "history": "سجل مرات الإقفال", "summaries": "ملخصات التشغيل"},
    "en": {"page_title": "Period closing", "periods": "Accounting periods", "dashboard": "Dashboard", "language": "العربية", "back": "Back to periods", "empty": "No periods found.", "close": "Close period", "reopen": "Reopen period", "closed": "Period closed and summaries saved.", "reopened": "Period reopened.", "history": "Closing run history", "summaries": "Run summaries"},
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": STRINGS[lang], "page_title": STRINGS[lang]["page_title"]}
    context.update(extra)
    return context


@require_any_permission("closing.run_closing", "closing.reopen_period")
def period_list(request):
    page = Paginator(Period.objects.all(), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "closing/list.html",
        _context(
            request,
            page=page,
            can_close=user_has_permission(request.user, "closing.run_closing"),
            can_reopen=user_has_permission(request.user, "closing.reopen_period"),
        ),
    )


@require_any_permission("closing.run_closing", "closing.reopen_period")
def period_detail(request, pk):
    period = get_object_or_404(
        Period.objects.prefetch_related("closing_runs__summaries", "closing_runs__created_by"),
        pk=pk,
    )
    return render(
        request,
        "closing/detail.html",
        _context(
            request,
            period=period,
            can_close=user_has_permission(request.user, "closing.run_closing"),
            can_reopen=user_has_permission(request.user, "closing.reopen_period"),
        ),
    )


@require_permission("closing.run_closing")
def period_close(request, pk):
    if request.method != "POST":
        return redirect("closing:detail", pk=pk)
    lang = _lang(request)
    try:
        complete_period_closing(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["closed"])
    return redirect(f"/closing/{pk}/?lang={lang}")


@require_permission("closing.reopen_period")
def period_reopen(request, pk):
    if request.method != "POST":
        return redirect("closing:detail", pk=pk)
    lang = _lang(request)
    try:
        reopen_period(pk, request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, STRINGS[lang]["reopened"])
    return redirect(f"/closing/{pk}/?lang={lang}")

