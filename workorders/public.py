"""S2: the customer's page behind a signed work-order link (backend only; the page's design waits for Ahmed).

GET shows the order's status and quote; POST approves or refuses the quote. Both are public (no account),
so everything here is gated by the signed token, and nothing is shown or written beyond what services allow.
"""

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
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


def _expired(request):
    lang = _lang(request)
    return render(request, "printing/share_expired.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl"}, status=404)


def _order_of(token):
    from printing.share import read_token

    found = read_token(token)
    if found is None or found[0] != "work_order":
        return None
    return WorkOrder.objects.filter(pk=found[1]).first()


def page(request, order):
    """GET (called by the share dispatcher): the order's state, plus the CSRF token a later page will post with."""

    state = services.public_state(order, _lang(request))
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
        order, changed = services.customer_decide(order, action, lang)
    except ValidationError as error:
        order.refresh_from_db()
        return _json({"ok": False, "message": error.messages[0], **services.public_state(order, lang)}, status=409)
    words = services.MESSAGES[lang]
    message = words["link_done_approved" if order.status == "approved" else "link_done_refused"] if changed else words["link_same"]
    return _json({"ok": True, "changed": changed, "message": message, **services.public_state(order, lang)})
