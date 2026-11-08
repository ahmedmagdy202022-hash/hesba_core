"""S2: the customer's page behind a signed work-order link (backend only; the page's design waits for Ahmed).

GET shows the order's status and quote; POST approves or refuses the quote. Both are public (no account),
so everything here is gated by the signed token, and nothing is shown or written beyond what services allow.
"""

from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from . import services
from .models import WorkOrder


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _json(payload, status=200):
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    return response


def _wants_html(request):
    """A browser (page load or form post) asks for HTML explicitly; API callers and the tests send no Accept and keep JSON."""

    return "text/html" in request.headers.get("Accept", "")


def _private(response):
    """No caching, no indexing and no referrer: the address itself is the customer's key."""

    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    patch_vary_headers(response, ("Accept",))
    return response


def _expired(request):
    from printing.share import expired_page

    return expired_page(request, _lang(request))


def _order_of(token):
    from printing.share import read_token

    found = read_token(token)
    if found is None or found[0] != "work_order":
        return None
    return WorkOrder.objects.filter(pk=found[1]).first()


CHAIN = ("received", "diagnosed", "quoted", "approved", "in_repair", "ready", "delivered")
CURRENCY_WORDS = {"EGP": {"ar": "ج.م", "en": "EGP"}}


def _money(value):
    return f"{Decimal(value):,.2f}"


def _quantity(value):
    quantity = Decimal(value).normalize()
    return f"{quantity:f}"


def _stamp(iso):
    return timezone.localtime(datetime.fromisoformat(iso)).strftime("%Y-%m-%d %H:%M")


def _steps(state):
    """The timeline as the customer reads it: what has happened, in order; the order's own status is the one marked now."""

    steps = []
    for entry in state["timeline"]:
        status = entry["status"]
        steps.append({"status": status, "label": entry["label"], "when": _stamp(entry["at"]), "now": status == state["status"],
                      "branch": status in ("refused", "cancelled"), "by_customer": entry["by_customer"]})
    return steps


def _view_model(request, order, lang, message="", message_ok=False):
    from printing.company import company_details
    from reports.aging import whatsapp_number

    state = services.public_state(order, lang)
    company = company_details()
    token = request.resolver_match.kwargs["token"] if request.resolver_match else ""
    steps = _steps(state)
    status = state["status"]
    decided = next((step for step in reversed(steps) if step["status"] == status and step["by_customer"]), None)
    shows_quote = bool(state["lines"]) and status in ("quoted", "approved", "in_repair", "ready", "delivered")
    tax, total = Decimal(state["tax"]), Decimal(state["total"])
    other = "ar" if lang == "en" else "en"
    here = reverse("share_document", args=[token])  # also right when this is the answer to a post
    return {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "state": state, "company": company, "steps": steps,
        "received_at": steps[0]["when"] if steps else "", "decided_at": decided["when"] if decided else "",
        "status_at": steps[-1]["when"] if steps else "",
        "lines": [{**line, "quantity": _quantity(line["quantity"]), "unit_price": _money(line["unit_price"]), "amount": _money(line["amount"])}
                  for line in state["lines"]] if shows_quote else [],
        "shows_quote": shows_quote, "subtotal": _money(total - tax), "tax": _money(tax), "has_tax": tax > 0, "total": _money(total),
        "currency": CURRENCY_WORDS.get(company["currency"], {}).get(lang, company["currency"]),
        "phone": company["phone"], "whatsapp": whatsapp_number(company["phone"]),
        "approve_url": reverse("share_work_order_approve", args=[token]), "refuse_url": reverse("share_work_order_refuse", args=[token]),
        "switch_url": f"{here}?lang={other}", "other_lang": other, "message": message, "message_ok": message_ok,
        "pay_url": _pay_url(order, lang),
    }


def _pay_url(order, lang):
    """S4 (HG-070): the live payment link of the order's invoice, when the shop sent one (the page never makes one)."""

    from payments.templatetags.hesba_payments import public_pay_url

    return public_pay_url(order.invoice, lang) if order.invoice_id else ""


def _html(request, order, lang, message="", status=200):
    return _private(render(request, "workorders/public.html", _view_model(request, order, lang, message), status=status))


def page(request, order):
    """GET (called by the share dispatcher): the customer's page in a browser; the order's state as JSON for anything else.

    Both carry what a later post needs: the CSRF cookie anonymous visitors post with, and the quote's fingerprint."""

    lang = _lang(request)
    if _wants_html(request):
        return _html(request, order, lang)
    state = services.public_state(order, lang)
    state["csrf_token"] = get_token(request)  # also sets the CSRF cookie anonymous visitors need to post
    return _json(state)


@require_POST
@csrf_protect
def decide(request, token, action):
    order = _order_of(token)
    if order is None:
        return _expired(request)
    lang = _lang(request)
    try:
        order, changed = services.customer_decide(order, action, lang, fingerprint=request.POST.get("fingerprint"))
    except ValidationError as error:
        order.refresh_from_db()
        if _wants_html(request):
            return _html(request, order, lang, error.messages[0], status=409)
        return _json({"ok": False, "message": error.messages[0], **services.public_state(order, lang)}, status=409)
    if _wants_html(request):  # a plain form post: show the page again (post, then get), now in its new state
        return _private(HttpResponseRedirect(f"{reverse('share_document', args=[token])}?lang={lang}", status=303))
    words = services.MESSAGES[lang]
    message = words["link_done_approved" if order.status == "approved" else "link_done_refused"] if changed else words["link_same"]
    return _json({"ok": True, "changed": changed, "message": message, **services.public_state(order, lang)})
