"""T13 (live part): the store's new orders pulled from its own API, instead of uploading its export.

* **Shopify** (Admin REST API): ``SHOPIFY_STORE`` (``my-shop.myshopify.com``), ``SHOPIFY_ACCESS_TOKEN`` (a custom
  app's Admin API token with ``read_orders``) and ``SHOPIFY_API_VERSION`` (default ``2024-10``).
  ``GET https://{store}/admin/api/{version}/orders.json?status=any&created_at_min=…&limit=250&since_id=…`` with the
  token in ``X-Shopify-Access-Token``, page by page on ``since_id``.
* **WooCommerce** (REST API v3): ``WOOCOMMERCE_URL`` (the site), ``WOOCOMMERCE_KEY`` and ``WOOCOMMERCE_SECRET`` (a
  read key). ``GET {url}/wp-json/wc/v3/orders?after=…&per_page=100&page=…`` with the key and secret as HTTP Basic.

The secrets live in the deployment's environment, never the database. Each order becomes the same shape a file
upload makes, and goes through the same import: a draft sales invoice, once (a WooCommerce order shares its numbers
with the Hesba template, which is how a WooCommerce shop uploaded them before). Nothing is posted.

Network calls go through ``_http`` only, which is what the tests replace.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from decimal import Decimal, InvalidOperation

from decouple import config
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import StoreSource

TIMEOUT = 30
MAX_PAGES = 20


def settings():
    return {
        "shopify_store": config("SHOPIFY_STORE", default="").strip().removeprefix("https://").strip("/"),
        "shopify_token": config("SHOPIFY_ACCESS_TOKEN", default="").strip(),
        "shopify_version": config("SHOPIFY_API_VERSION", default="2024-10").strip() or "2024-10",
        "woo_url": config("WOOCOMMERCE_URL", default="").strip().rstrip("/"),
        "woo_key": config("WOOCOMMERCE_KEY", default="").strip(),
        "woo_secret": config("WOOCOMMERCE_SECRET", default="").strip(),
    }


def connected():
    """The stores whose API is set up: a list of "shopify" and/or "woocommerce"."""

    values = settings()
    stores = []
    if values["shopify_store"] and values["shopify_token"]:
        stores.append("shopify")
    if values["woo_url"].startswith("https://") and values["woo_key"] and values["woo_secret"]:
        stores.append("woocommerce")
    return stores


def _http(url, headers):
    """(status, parsed JSON or None) for one GET. A network failure is status 0."""

    request = urllib.request.Request(url, method="GET", headers={**headers, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - the shop's own https API
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        return status, None


def _text(value):
    return "" if value is None else str(value).strip()


def _number(value):
    try:
        return Decimal(_text(value) or "0")
    except InvalidOperation:
        return Decimal("0")


def _blank():
    return {"lines": [], "date": "", "customer": "", "phone": "", "email": "", "address": "", "discount": "", "shipping": "",
            "total": "", "status": "", "currency": "", "cancelled": ""}


def shopify_order(row):
    billing, shipping_address, customer = row.get("billing_address") or {}, row.get("shipping_address") or {}, row.get("customer") or {}
    order = _blank()
    order.update({
        "number": _text(row.get("name") or row.get("order_number")).lstrip("#"),
        "date": _text(row.get("created_at"))[:10],
        "customer": _text(billing.get("name")) or " ".join(part for part in (_text(customer.get("first_name")), _text(customer.get("last_name"))) if part),
        "phone": _text(row.get("phone")) or _text(billing.get("phone")) or _text(shipping_address.get("phone")) or _text(customer.get("phone")),
        "email": _text(row.get("email")),
        "address": "، ".join(part for part in (_text(shipping_address.get("address1")), _text(shipping_address.get("city"))) if part),
        "discount": _text(row.get("total_discounts")),
        "shipping": str(sum((float(line.get("price") or 0) for line in row.get("shipping_lines") or []), 0.0)) if row.get("shipping_lines") else "",
        "total": _text(row.get("total_price")),
        "status": _text(row.get("financial_status")),
        "currency": _text(row.get("currency")),
        "cancelled": _text(row.get("cancelled_at")),
        "prices_include_tax": bool(row.get("taxes_included")),
    })
    order["lines"] = [{"sku": _text(line.get("sku")) or _text(line.get("title")), "quantity": _text(line.get("quantity")), "price": _text(line.get("price"))}
                      for line in row.get("line_items") or []]
    return order


def woocommerce_order(row):
    billing, shipping_address = row.get("billing") or {}, row.get("shipping") or {}
    status = _text(row.get("status"))
    order = _blank()
    order.update({
        "number": _text(row.get("number") or row.get("id")),
        "date": _text(row.get("date_created"))[:10],
        "customer": " ".join(part for part in (_text(billing.get("first_name")), _text(billing.get("last_name"))) if part),
        "phone": _text(billing.get("phone")) or _text(shipping_address.get("phone")),
        "email": _text(billing.get("email")),
        "address": "، ".join(part for part in (_text(shipping_address.get("address_1") or billing.get("address_1")),
                                                _text(shipping_address.get("city") or billing.get("city"))) if part),
        "discount": _text(row.get("discount_total")),
        "shipping": _text(row.get("shipping_total")),
        "total": _text(row.get("total")),
        "status": "refunded" if status == "refunded" else status,
        "currency": _text(row.get("currency")),
        "cancelled": status if status in ("cancelled", "failed", "trash") else "",
        # Codex #283: WooCommerce's REST amounts (line subtotals, shipping, discount) are always before tax, whatever the
        # shop's "prices include tax" setting, so they come in as tax-exclusive and Hesba adds the VAT on top.
        "prices_include_tax": False,
    })
    lines = []
    for line in row.get("line_items") or []:
        quantity, subtotal, total = (_number(line.get(key)) for key in ("quantity", "subtotal", "total"))
        # Codex #283: the unit price keeps the line's exact subtotal (before coupons), so the import's rounding can
        # bring the line back to it; each line's own coupon is what WooCommerce took off it (subtotal - total), never
        # the order's coupon spread again over lines it did not touch.
        lines.append({"sku": _text(line.get("sku")) or _text(line.get("name")), "quantity": _text(line.get("quantity")),
                      "price": str((subtotal / quantity).quantize(Decimal("0.000001"))) if quantity else _text(line.get("price")),
                      "discount": str(max(subtotal - total, Decimal("0"))) if line.get("total") not in (None, "") else "0"})
    order["lines"] = lines
    return order


def _shopify(since, since_id=0, max_pages=MAX_PAGES):
    """(orders, the since_id to carry on from, or None when every order was read)."""

    values = settings()
    base = f"https://{values['shopify_store']}/admin/api/{values['shopify_version']}/orders.json"
    headers = {"X-Shopify-Access-Token": values["shopify_token"]}
    rows, pages = [], 0
    while max_pages is None or pages < max_pages:
        query = urllib.parse.urlencode({"status": "any", "created_at_min": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 250, "since_id": since_id})
        status, payload = _http(f"{base}?{query}", headers)
        if status != 200 or not isinstance(payload, dict):
            raise ValidationError(f"shopify:{status}")
        page = payload.get("orders") or []
        rows += page
        pages += 1
        if len(page) < 250:
            return [shopify_order(row) for row in rows], None
        since_id = max(int(row.get("id") or 0) for row in page)
    return [shopify_order(row) for row in rows], since_id


def _woocommerce(since, page=1, max_pages=MAX_PAGES):
    """(orders, the page to carry on from, or None when every order was read)."""

    values = settings()
    basic = base64.b64encode(f"{values['woo_key']}:{values['woo_secret']}".encode()).decode()
    headers = {"Authorization": f"Basic {basic}"}
    rows, pages = [], 0
    while max_pages is None or pages < max_pages:
        query = urllib.parse.urlencode({"after": since.strftime("%Y-%m-%dT%H:%M:%S"), "per_page": 100, "page": page, "orderby": "date", "order": "asc"})
        status, payload = _http(f"{values['woo_url']}/wp-json/wc/v3/orders?{query}", headers)
        if status != 200 or not isinstance(payload, list):
            raise ValidationError(f"woocommerce:{status}")
        rows += payload
        pages += 1
        if len(payload) < 100:
            return [woocommerce_order(row) for row in rows], None
        page += 1
    return [woocommerce_order(row) for row in rows], page


def _since(more, days):
    """The window's start and where to carry on, from a ``more`` marker ("<start>|<position>") or afresh."""

    start, _sep, position = (more or "").partition("|")
    try:
        since = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt_timezone.utc)
        return since, max(int(position), 0)
    except ValueError:
        return timezone.now() - timedelta(days=days), None


def fetch(store, days=7, more="", max_pages=MAX_PAGES):
    """(source, {number: order}, more) for the store's orders of the last ``days`` days.

    Codex #283: a pull reads at most ``max_pages`` pages (None: all of them). When the store has more, ``more`` says
    where to carry on (the same window, from the next page), and "" once every order was read."""

    since, position = _since(more, days)
    if store == "shopify":
        orders, next_position = _shopify(since, position or 0, max_pages)
    else:
        orders, next_position = _woocommerce(since, position or 1, max_pages)
    source = StoreSource.SHOPIFY if store == "shopify" else StoreSource.TEMPLATE
    more = f"{since.strftime('%Y-%m-%dT%H:%M:%SZ')}|{next_position}" if next_position is not None else ""
    return source, OrderedDict((order["number"], order) for order in orders if order["number"]), more


def pull(store, location, user, lang="ar", shipping_item=None, replace=False, days=7, more="", max_pages=MAX_PAGES):
    """Pull the store's recent orders into draft invoices, as an upload would. Same result shape as ``import_orders``,
    plus ``more``: where a next pull carries on when the store had more orders than one pull reads ("" when none)."""

    from . import services
    from taxes.services import vat_enabled

    if store not in connected():
        raise ValidationError(services.MESSAGES[lang]["not_connected"])
    if not services.can_import(user):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied(f"Importing store orders needs {services.IMPORT}.")
    if location is None:
        raise ValidationError(services.MESSAGES[lang]["location"])
    source, orders, more = fetch(store, days, more, max_pages)
    # Codex #283: each order says whether its own prices include VAT (``prices_include_tax``); Hesba takes the VAT back
    # out of that order only, and only when the shop charges VAT.
    result = services.import_parsed(source, orders, location, user, lang, services.import_options(shipping_item, vat_enabled()), replace)
    result["more"] = more
    return result
