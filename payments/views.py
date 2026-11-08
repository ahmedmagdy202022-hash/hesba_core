"""HG-070 (S4): the customer's pay page, the return page, the gateway's webhook, and the staff actions.

Public pages are gated by the signed token only and never record money. The webhook is the one CSRF-exempt endpoint:
it is authenticated by the gateway's HMAC, verified in constant time in ``payments.services.handle_callback``.
"""

import json
from decimal import Decimal

from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_POST

from permissions.services import user_has_permission

from . import services
from .models import GatewayTransaction, LinkStatus, PaymentLink

RATE_WINDOW = 60  # seconds
PAGE_LIMIT = 60  # pay-page views per address per minute
WEBHOOK_LIMIT = 600  # generous: a pending and a confirmed callback for one payment must always pass


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _throttled(request, bucket, limit):
    """A per-process counter per address and minute; enough to blunt a flood (HG-070 "rate-limit the public endpoints")."""

    key = f"payments:{bucket}:{(request.META.get('REMOTE_ADDR') or '')[:64]}:{int(timezone.now().timestamp()) // RATE_WINDOW}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, RATE_WINDOW * 2)
        count = 1
    return count > limit


def _private(response):
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    patch_vary_headers(response, ("Accept-Language",))
    return response


def _too_many():
    return _private(HttpResponse("Too many requests.", status=429, content_type="text/plain; charset=utf-8"))


def _page(request, template, link, lang, signed, status=200):
    from printing.company import company_details
    from reports.aging import whatsapp_number
    from workorders.public import CURRENCY_WORDS

    company = company_details()
    payable = services.enabled() and services.is_payable(link)
    other = "en" if lang == "ar" else "ar"
    context = {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "company": company, "phone": company["phone"], "whatsapp": whatsapp_number(company["phone"]),
        "link": link, "status_label": services.status_label(link, lang), "amount": f"{Decimal(link.amount):,.2f}",
        "paid_amount": f"{Decimal(link.paid_amount):,.2f}" if link.paid_amount is not None else "",
        "currency": CURRENCY_WORDS.get(company["currency"], {}).get(lang, company["currency"]),
        "invoice_number": link.invoice.invoice_number, "expires_at": timezone.localtime(link.expires_at).strftime("%Y-%m-%d %H:%M"),
        "payable": payable, "feature_on": services.enabled(), "expired": link.status == LinkStatus.EXPIRED or link.expires_at <= timezone.now(),
        "pay_url": reverse("pay_go", args=[signed]), "done_url": f"{reverse('pay_done', args=[signed])}?lang={lang}",
        "switch_url": f"{request.path}?lang={other}",
    }
    return _private(render(request, template, context, status=status))


def _expired(request):
    from printing.share import expired_page

    return expired_page(request, _lang(request))


@never_cache
def pay_page(request, token):
    if _throttled(request, "page", PAGE_LIMIT):
        return _too_many()
    link = services.read_token(token)
    if link is None:
        return _expired(request)
    services.expire_stale(link.invoice)
    link.refresh_from_db()
    return _page(request, "payments/pay.html", link, _lang(request), token)


@never_cache
def pay_go(request, token):
    """The button: re-checks the link now, then sends the customer to the gateway's own page."""

    if _throttled(request, "page", PAGE_LIMIT):
        return _too_many()
    link = services.read_token(token)
    if link is None:
        return _expired(request)
    services.expire_stale(link.invoice)
    link.refresh_from_db()
    if not (services.enabled() and services.is_payable(link) and link.checkout_url):
        return _private(HttpResponseRedirect(f"{reverse('pay_page', args=[token])}?lang={_lang(request)}", status=303))
    return _private(HttpResponseRedirect(link.checkout_url, status=303))


@never_cache
def pay_done(request, token):
    """Status only. A query string from the browser is never read for money: only the verified webhook records it."""

    if _throttled(request, "page", PAGE_LIMIT):
        return _too_many()
    link = services.read_token(token)
    if link is None:
        return _expired(request)
    return _page(request, "payments/done.html", link, _lang(request), token)


@never_cache
def pay_return(request):
    """Paymob's fixed "transaction response callback" (the customer's browser): find the link by our random reference
    and show its status page. Nothing in the query is trusted for money."""

    if _throttled(request, "page", PAGE_LIMIT):
        return _too_many()
    reference = (request.GET.get("merchant_order_id") or "")[:40]
    link = PaymentLink.objects.filter(reference=reference).first() if reference else None
    if link is None:  # the gateway's return did not name our order: a neutral page, never "expired" after a payment
        from printing.company import company_details
        from reports.aging import whatsapp_number

        lang = _lang(request)
        company = company_details()
        return _private(render(request, "payments/checking.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "company": company,
                                                                   "phone": company["phone"], "whatsapp": whatsapp_number(company["phone"])}))
    return _private(HttpResponseRedirect(f"{reverse('pay_done', args=[services.sign_token(link)])}?lang={_lang(request)}", status=303))


@csrf_exempt
@require_POST
@never_cache
def webhook(request):
    """``POST /pay/webhook/paymob/?hmac=…`` with Paymob's JSON body. 400 unless the HMAC verifies; 200 for every
    verified callback, whether it recorded money or was a recorded no-op (so the gateway stops retrying)."""

    if _throttled(request, "webhook", WEBHOOK_LIMIT):
        return _too_many()
    try:
        payload = json.loads(request.body.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        services._audit("callback_rejected", "payments.GatewayTransaction", "", {"why": "not json"})
        return JsonResponse({"ok": False}, status=400)
    status, result = services.handle_callback(payload, request.GET.get("hmac", ""))
    return JsonResponse({"ok": status == 200, "result": result if status == 200 else "rejected"}, status=status)


# ---- development only: the mock gateway's checkout page ----------------------------------------


def _mock_provider():
    from .providers.mock import MockPaymob

    provider = services.connected()
    if not isinstance(provider, MockPaymob):
        raise Http404()
    return provider


@csrf_protect
@never_cache
def mock_checkout(request, reference):
    """Stands in for Paymob's hosted page while the mock is the gateway: the buttons send a signed callback through the
    same webhook code (same HMAC check, same posting). 404 whenever a real gateway (or none) is active."""

    provider = _mock_provider()
    link = get_object_or_404(PaymentLink.objects.select_related("invoice"), reference=reference, provider=provider.code)
    services.expire_stale(link.invoice)
    link.refresh_from_db()
    if not services.is_payable(link):  # paid, failed, expired, or the invoice changed: nothing to pay here
        raise Http404()
    lang = _lang(request)
    signed = services.sign_token(link)
    if request.method == "POST":
        outcome = request.POST.get("outcome")
        flags = {"success": {"success": True, "pending": False}, "pending": {"success": False, "pending": True},
                 "failed": {"success": False, "pending": False}}.get(outcome)
        if flags is None:
            raise Http404()
        # One transaction id per link, as one card payment is at Paymob: a second press is a repeat, never a second payment.
        payload, signature = provider.callback(link.gateway_order_id, services.to_cents(link.amount), transaction_id=900_000_000 + link.pk,
                                               merchant_reference=link.reference, **flags)
        services.handle_callback(payload, signature, provider)
        return HttpResponseRedirect(f"{reverse('pay_done', args=[signed])}?lang={lang}", status=303)
    return _page(request, "payments/mock_checkout.html", link, lang, signed)


# ---- staff -----------------------------------------------------------------------------------


def _next(request, fallback):
    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return fallback


@require_POST
def create(request, invoice_pk):
    from entities import scope as entity_scope
    from sales.models import SalesInvoice

    lang = _lang(request)
    if not user_has_permission(request.user, services.RECEIVE):
        raise PermissionDenied
    invoice = get_object_or_404(entity_scope.scope(SalesInvoice.objects.select_related("customer", "selling_location"), entity_scope.SALES_INVOICE), pk=invoice_pk)
    fallback = f"{reverse('sales:detail', args=[invoice.pk])}?lang={lang}"
    try:
        services.create_link(invoice, request.user, request.POST.get("amount") or None, lang)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(request, "اتعمل لينك الدفع؛ ابعته للعميل." if lang == "ar" else "The payment link was made; send it to the customer.")
    return redirect(_next(request, fallback))


@require_POST
def cancel(request, pk):
    from entities import scope as entity_scope

    lang = _lang(request)
    if not user_has_permission(request.user, services.RECEIVE):
        raise PermissionDenied
    link = get_object_or_404(entity_scope.scope(PaymentLink.objects.all(), "invoice__selling_location__entity"), pk=pk)
    try:
        services.cancel_link(link, request.user, lang)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    return redirect(_next(request, f"{reverse('sales:detail', args=[link.invoice_id])}?lang={lang}"))


def gateway(request):
    """The gateway accounts, the links, the callbacks that need a person, and the payout form."""

    from cashboxes.models import Cashbox
    from cashboxes.services import get_cashbox_balance
    from entities import scope as entity_scope

    lang = _lang(request)
    can_settle = user_has_permission(request.user, services.MOVE_CASH)
    if not (can_settle or user_has_permission(request.user, services.RECEIVE)):
        raise PermissionDenied
    boxes = entity_scope.scope(services.gateway_boxes(), entity_scope.CASHBOX).order_by("pk")
    if request.method == "POST":
        if not can_settle:
            raise PermissionDenied
        gateway_box = boxes.filter(pk=request.POST.get("gateway") or 0).first()
        bank = entity_scope.cashboxes(Cashbox.objects.filter(active=True)).filter(pk=request.POST.get("bank") or 0).first()
        try:
            services.book_payout(gateway_box, bank, request.POST.get("net"), request.POST.get("fee"), request.user,
                                 reference=request.POST.get("reference", ""), lang=lang)
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            messages.success(request, "اتسجّل تحويل البوابة وعمولتها." if lang == "ar" else "The payout and its fee were recorded.")
        return redirect(f"{reverse('payments:gateway')}?lang={lang}")
    links = entity_scope.scope(PaymentLink.objects.select_related("invoice", "customer"), "invoice__selling_location__entity")[:50]
    review = entity_scope.scope(GatewayTransaction.objects.filter(needs_review=True).select_related("link__invoice"), "link__invoice__selling_location__entity")[:50]
    banks = entity_scope.cashboxes(Cashbox.objects.filter(active=True)).exclude(pk__in=boxes.values("pk")).order_by("pk")
    return render(request, "payments/gateway.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "boxes": [(box, get_cashbox_balance(box)) for box in boxes],
        "links": [(link, services.status_label(link, lang)) for link in links], "review": review, "banks": banks,
        "can_settle": can_settle, "connected": services.connected() is not None, "feature_on": services.enabled(),
    })
