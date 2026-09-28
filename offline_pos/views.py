"""POS-003: sync the sales a till rang up while the internet was down.

The device sends each queued sale with the key it generated when the sale was
made. The server posts it through exactly the same path as a live POS sale
(``checkout``: stock check, prices, tax, cashbox and ledger), dated the day it
was rung up, and records the key in the same transaction. A key seen before
answers with the invoice it already made, so a retry can never post twice.
A sale the server cannot post (stock gone, closed period, bad data) is
refused with the reason and stays on the device for the cashier to see.
"""

import json
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from cashboxes.models import Cashbox
from master_data.models import Customer, Location
from permissions.decorators import require_permission
from sales.pos import WORDS, _decimal, _error_text, checkout, parse_cart, walk_in_customer
from serials.services import prepare_sale_serials

from .models import OfflineSale


def max_age_days():
    return int(getattr(settings, "POS_OFFLINE_MAX_DAYS", 7))


REFUSALS = {
    "ar": {"bad": "بيانات الفاتورة ناقصة أو مش صحيحة.", "future": "تاريخ الفاتورة في المستقبل.", "old": "الفاتورة أقدم من {days} أيام؛ سجّلها يدوي.",
           "setup": "المخزن أو الخزنة مش موجودين أو موقوفين."},
    "en": {"bad": "The sale data is missing or invalid.", "future": "The sale is dated in the future.", "old": "The sale is older than {days} days; enter it by hand.",
           "setup": "The location or cashbox is missing or inactive."},
}


def _answer(status, invoice=None, error="", http=200):
    body = {"status": status}
    if invoice is not None:
        body.update({"number": invoice.invoice_number, "total": str(invoice.total_amount), "invoice": invoice.pk})
    if error:
        body["error"] = error
    return JsonResponse(body, status=http)


def _existing(key):
    found = OfflineSale.objects.select_related("invoice").filter(sale_key=key).first()
    return found.invoice if found else None


def _recorded_at(text):
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


def _cart_post(payload):
    """The sale's lines in the shape the live POS form posts, for the same parser."""

    lines = payload.get("lines")
    if not isinstance(lines, list):
        raise ValidationError("err_empty")
    post = {"line_count": str(len(lines))}
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValidationError("err_line")
        post[f"item_{index}"] = str(line.get("id", ""))
        post[f"qty_{index}"] = str(line.get("qty", ""))
        post[f"price_{index}"] = str(line.get("price", ""))
        post[f"unit_{index}"] = str(line.get("unit") or "")
        post[f"serial_{index}"] = str(line.get("serial") or "")
    return post


@require_POST
@require_permission("sales.create_sales_invoice")
def sync_sale(request):
    lang = "en" if request.GET.get("lang") == "en" else "ar"
    refusals, words = REFUSALS[lang], WORDS[lang]
    try:
        payload = json.loads(request.body or b"{}")
        key = uuid.UUID(str(payload.get("key", "")))
    except (ValueError, TypeError, AttributeError):
        return _answer("refused", error=refusals["bad"], http=400)
    invoice = _existing(key)
    if invoice is not None:
        return _answer("already", invoice)
    recorded_at = _recorded_at(payload.get("recorded_at"))
    if recorded_at is None:
        return _answer("refused", error=refusals["bad"])
    now = timezone.now()
    if recorded_at > now + timedelta(minutes=10):
        return _answer("refused", error=refusals["future"])
    if recorded_at < now - timedelta(days=max_age_days()):
        return _answer("refused", error=refusals["old"].format(days=max_age_days()))
    try:
        lines = prepare_sale_serials(parse_cart(_cart_post(payload)), lang)
        location = Location.objects.filter(pk=str(payload.get("location", "")), active=True, is_selling_location=True).first() if str(payload.get("location", "")).isdigit() else None
        cashbox = Cashbox.objects.filter(pk=str(payload.get("cashbox", "")), active=True).first() if str(payload.get("cashbox", "")).isdigit() else None
        if location is None or cashbox is None:
            return _answer("refused", error=refusals["setup"])
        customer_id = str(payload.get("customer", ""))
        customer = Customer.objects.filter(pk=customer_id, active=True).first() if customer_id.isdigit() else None
        discount = _decimal(payload.get("discount") or "0", 2)
        tendered = _decimal(payload.get("tendered") or "0", 2)
        if discount is None or tendered is None or tendered < 0:
            raise ValidationError("err_line")
        with transaction.atomic():
            invoice, _change = checkout(
                lines=lines, customer=customer or walk_in_customer(), location=location, cashbox=cashbox,
                discount=discount, tendered=tendered, user=request.user,
                sale_date=timezone.localtime(recorded_at).date(), notes="POS offline",
            )
            OfflineSale.objects.create(sale_key=key, invoice=invoice, cashier=request.user, recorded_at=recorded_at)
    except IntegrityError:
        # The same sale arrived twice at the same moment; the other request posted it.
        invoice = _existing(key)
        if invoice is not None:
            return _answer("already", invoice)
        raise
    except ValidationError as exc:
        from settings_core.ui_messages import translate  # I18N-001: the same wording the live till shows

        return _answer("refused", error=translate(_error_text(exc, words), lang))
    return _answer("posted", invoice)
