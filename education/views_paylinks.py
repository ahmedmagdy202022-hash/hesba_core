"""E4a: the fee screens' payment-link actions (one invoice, and the month in bulk) and the bulk result screen.

New views only: ``education/views.py`` is not touched. Every refusal comes from ``create_link`` itself, so a crafted request
gets the same message the button would; what is checked here is the right (403), the education install (404) and that the
invoice is one the fee screens list (404, as ``payments:create`` does for another entity's invoice).
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import billing, paylinks, services
from .views import _base, _lang


def _guard(request):
    if not services.is_education_install():
        raise Http404("Fees are for education activities.")
    if not paylinks.can_make(request.user):
        raise PermissionDenied("Payment links need sales.receive_customer_payment and the fees or dues screen.")


def _next(request, fallback):
    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return fallback


@require_POST
def make(request, invoice_pk):
    """«لينك دفع» on one fee invoice: shows its live link, or makes one."""

    _guard(request)
    lang = _lang(request)
    invoice = get_object_or_404(paylinks.fee_invoices().select_related("customer", "selling_location"), pk=invoice_pk)
    words = paylinks.WORDS[lang]
    try:
        _, made = paylinks.make_or_show(invoice, request.user, lang)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(request, words["made"] if made else words["reused"])
    return redirect(_next(request, f"{reverse('education:fees')}?lang={lang}#pay-{invoice.pk}"))


@require_POST
def bulk(request):
    """«اعمل لينكات دفع للشهر»: the result is kept in the session and shown on its own page, so a refresh never makes more links."""

    _guard(request)
    lang = _lang(request)
    try:
        first = billing.month_of(request.POST.get("month") or "")
        if not (request.POST.get("month") or "").strip():
            raise ValidationError(billing.MESSAGES[lang]["month"])
        result = paylinks.bulk(first, request.user, lang)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect(f"{reverse('education:fees')}?lang={lang}")
    request.session["edu_paylink_bulk"] = result
    return redirect(f"{reverse('education:paylinks_result')}?lang={lang}")


def result(request):
    """What the last bulk click did: made, skipped (and why), failed by student, and what is left."""

    if not services.is_education_install():
        raise Http404("Fees are for education activities.")
    if not paylinks.can_make(request.user):
        raise PermissionDenied("Payment links need sales.receive_customer_payment and the fees or dues screen.")
    lang = _lang(request)
    words = paylinks.WORDS[lang]
    data = request.session.get("edu_paylink_bulk")
    context = _base(request, lang, "fees", pl=words, data=None, cap=paylinks.BULK_CAP)
    if data:
        from sales.models import SalesInvoice

        from .models import FeeCharge

        month = billing.month_of(data["month"])
        pks = data["made"] + [pk for pk, _ in data["skipped"] + data["failed"]]
        invoices = {invoice.pk: invoice for invoice in paylinks.fee_invoices(SalesInvoice.objects.filter(pk__in=pks)).select_related("customer")}
        students = {}
        for charge in FeeCharge.objects.filter(invoice_id__in=invoices).select_related("enrollment__student").order_by("pk"):
            names = students.setdefault(charge.invoice_id, [])
            if charge.enrollment.student.name not in names:
                names.append(charge.enrollment.student.name)
        joiner = "، " if lang == "ar" else ", "

        def line(pk, *extra):
            invoice = invoices.get(pk)
            return {"invoice": invoice, "students": joiner.join(students.get(pk, [])), "extra": extra[0] if extra else ""} if invoice else None

        board = paylinks.board_for(request, lang)
        made = []
        for pk in data["made"]:
            row = line(pk)
            if row:
                state = board.state(invoices[pk])
                row["state"] = state if state.kind == "live" else None
                made.append(row)
        reasons = {"draft": words["sk_draft"], "nothing_due": words["sk_nothing_due"], "has_link": words["sk_has_link"]}
        groups = {}
        for pk, reason in data["skipped"]:
            row = line(pk)
            if row:
                groups.setdefault(reason, []).append(row)
        context.update(
            data=data, label=billing.month_label(month, lang), made=made, first=month,
            skipped=[{"label": reasons[reason], "rows": rows, "count": len(rows)} for reason, rows in groups.items()],
            skipped_count=sum(len(rows) for rows in groups.values()), failed=[row for pk, message in data["failed"] if (row := line(pk, message))],
            left=data["left"], left_text=words["left_more"].format(n=data["left"], button=words["bulk"]),
        )
    return render(request, "education/paylinks_result.html", context)
