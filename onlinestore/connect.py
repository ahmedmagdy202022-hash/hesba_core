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
from datetime import timedelta

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
        "prices_include_tax": bool(row.get("prices_include_tax")),
    })
    lines = []
    for line in row.get("line_items") or []:
        quantity = float(line.get("quantity") or 0)
        subtotal = float(line.get("subtotal") or 0)  # before coupons; the coupons are the order's discount_total
        lines.append({"sku": _text(line.get("sku")) or _text(line.get("name")), "quantity": _text(line.get("quantity")),
                      "price": f"{subtotal / quantity:.2f}" if quantity else _text(line.get("price"))})
    order["lines"] = lines
    return order


def _shopify(since):
    values = settings()
    base = f"https://{values['shopify_store']}/admin/api/{values['shopify_version']}/orders.json"
    headers = {"X-Shopify-Access-Token": values["shopify_token"]}
    rows, since_id = [], 0
    for _page in range(MAX_PAGES):
        query = urllib.parse.urlencode({"status": "any", "created_at_min": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 250, "since_id": since_id})
        status, payload = _http(f"{base}?{query}", headers)
        if status != 200 or not isinstance(payload, dict):
            raise ValidationError(f"shopify:{status}")
        page = payload.get("orders") or []
        rows += page
        if len(page) < 250:
            break
        since_id = max(int(row.get("id") or 0) for row in page)
    return [shopify_order(row) for row in rows]


def _woocommerce(since):
    values = settings()
    basic = base64.b64encode(f"{values['woo_key']}:{values['woo_secret']}".encode()).decode()
    headers = {"Authorization": f"Basic {basic}"}
    rows = []
    for page in range(1, MAX_PAGES + 1):
        query = urllib.parse.urlencode({"after": since.strftime("%Y-%m-%dT%H:%M:%S"), "per_page": 100, "page": page, "orderby": "date", "order": "asc"})
        status, payload = _http(f"{values['woo_url']}/wp-json/wc/v3/orders?{query}", headers)
        if status != 200 or not isinstance(payload, list):
            raise ValidationError(f"woocommerce:{status}")
        rows += payload
        if len(payload) < 100:
            break
    return [woocommerce_order(row) for row in rows]


def fetch(store, days=7):
    """(source, {number: order}) for the store's orders of the last ``days`` days."""

    since = timezone.now() - timedelta(days=days)
    orders = _shopify(since) if store == "shopify" else _woocommerce(since)
    source = StoreSource.SHOPIFY if store == "shopify" else StoreSource.TEMPLATE
    return source, OrderedDict((order["number"], order) for order in orders if order["number"])


def pull(store, location, user, lang="ar", shipping_item=None, replace=False, days=7):
    """Pull the store's recent orders into draft invoices, as an upload would. Same result shape as ``import_orders``."""

    from . import services
    from taxes.services import vat_enabled

    if store not in connected():
        raise ValidationError(services.MESSAGES[lang]["not_connected"])
    if not services.can_import(user):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied(f"Importing store orders needs {services.IMPORT}.")
    if location is None:
        raise ValidationError(services.MESSAGES[lang]["location"])
    source, orders = fetch(store, days)
    # The store says whether its prices include VAT; Hesba takes it back only when the shop charges VAT.
    with_vat = vat_enabled() and any(order.get("prices_include_tax") for order in orders.values())
    return services.import_parsed(source, orders, location, user, lang, services.import_options(shipping_item, with_vat), replace)
