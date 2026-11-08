"""HG-070 (S4): the Paymob adapter (Accept API: auth token → order → payment key → hosted iframe).

Every HTTP call goes through ``PaymobProvider._http``, the one place that opens a socket. The mock
(``payments.providers.mock.MockPaymob``) is this same class with ``_http`` answering Paymob's real JSON shapes
in-process, so the HMAC check and the callback reading that tests exercise are the real ones.

The keys come from the server's environment only (``settings.PAYMOB_*``, read with ``config()``), never from a model.

**Callback HMAC (Paymob's "transaction processed callback"):** the values of these keys of ``obj``, in this order,
concatenated as text (booleans as ``true`` / ``false``, nested keys reached through the dot), HMAC-SHA512 with the
HMAC secret, hex digest, compared with the ``hmac`` query parameter in constant time. An empty secret verifies nothing.
"""

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from decimal import Decimal

from .base import Callback, Checkout, GatewayError, Outcome, PaymentProvider

BASE_URL = "https://accept.paymob.com"
TIMEOUT = 20

HMAC_FIELDS = (
    "amount_cents", "created_at", "currency", "error_occured", "has_parent_transaction", "id", "integration_id",
    "is_3d_secure", "is_auth", "is_capture", "is_refunded", "is_standalone_payment", "is_voided", "order.id", "owner",
    "pending", "source_data.pan", "source_data.sub_type", "source_data.type", "success",
)


def _text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _dig(obj, dotted):
    value = obj
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def hmac_message(obj):
    return "".join(_text(_dig(obj, key)) for key in HMAC_FIELDS)


def sign(obj, secret):
    return hmac.new(secret.encode(), hmac_message(obj).encode(), hashlib.sha512).hexdigest()


def _truthy(value):
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


class PaymobProvider(PaymentProvider):
    code = "paymob"
    payment_prefix = "PAYMOB"
    live = True

    def __init__(self, api_key, hmac_secret, integration_id, iframe_id, base_url=BASE_URL):
        self.api_key = api_key or ""
        self.hmac_secret = hmac_secret or ""
        self.integration_id = str(integration_id or "")
        self.iframe_id = str(iframe_id or "")
        self.base_url = base_url.rstrip("/")

    # ---- transport (the only network access) ----

    def _http(self, method, path, body):
        request = urllib.request.Request(f"{self.base_url}{path}", data=json.dumps(body).encode(), method=method,
                                         headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https endpoint
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise GatewayError(f"Paymob answered {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise GatewayError("Paymob could not be reached.") from exc

    # ---- the three Accept calls ----

    def authenticate(self):
        token = self._http("POST", "/api/auth/tokens", {"api_key": self.api_key}).get("token")
        if not token:
            raise GatewayError("Paymob gave no auth token.")
        return token

    def create_order(self, token, amount_cents, currency, merchant_reference):
        answer = self._http("POST", "/api/ecommerce/orders", {
            "auth_token": token, "delivery_needed": "false", "amount_cents": str(amount_cents), "currency": currency,
            "merchant_order_id": merchant_reference, "items": [],
        })
        if not answer.get("id"):
            raise GatewayError("Paymob gave no order id.")
        return answer

    def payment_key(self, token, order_id, amount_cents, currency, customer, expires_seconds):
        name = (getattr(customer, "name", "") or "Customer").split()
        billing = {
            "first_name": name[0][:50], "last_name": (" ".join(name[1:]) or "NA")[:50], "email": getattr(customer, "email", "") or "NA",
            "phone_number": getattr(customer, "phone", "") or "NA", "apartment": "NA", "floor": "NA", "street": "NA", "building": "NA",
            "shipping_method": "NA", "postal_code": "NA", "city": "NA", "country": "EG", "state": "NA",
        }
        answer = self._http("POST", "/api/acceptance/payment_keys", {
            "auth_token": token, "amount_cents": str(amount_cents), "expiration": int(expires_seconds), "order_id": order_id,
            "billing_data": billing, "currency": currency, "integration_id": int(self.integration_id) if self.integration_id.isdigit() else self.integration_id,
        })
        if not answer.get("token"):
            raise GatewayError("Paymob gave no payment key.")
        return answer["token"]

    def checkout_url(self, payment_token, merchant_reference):
        return f"{self.base_url}/api/acceptance/iframes/{self.iframe_id}?payment_token={payment_token}"

    def create_checkout(self, *, amount_cents, currency, merchant_reference, customer, expires_seconds):
        token = self.authenticate()
        order = self.create_order(token, amount_cents, currency, merchant_reference)
        payment_token = self.payment_key(token, order["id"], amount_cents, currency, customer, expires_seconds)
        return Checkout(order_id=str(order["id"]), checkout_url=self.checkout_url(payment_token, merchant_reference), raw={"order": order.get("id")})

    # ---- the callback ----

    def verify_callback(self, payload, received_hmac):
        if not self.hmac_secret or not received_hmac or not isinstance(payload, dict):
            return False
        obj = payload.get("obj")
        if not isinstance(obj, dict):
            return False
        expected = sign(obj, self.hmac_secret)
        return hmac.compare_digest(expected.encode(), str(received_hmac).strip().lower().encode())

    def parse_callback(self, payload):
        if payload.get("type", "TRANSACTION") != "TRANSACTION":
            raise GatewayError("Not a transaction callback.")
        obj = payload["obj"]
        try:
            amount_cents = int(Decimal(str(obj.get("amount_cents"))))
        except Exception as exc:  # noqa: BLE001 - any unreadable amount is a refusal
            raise GatewayError("Unreadable amount.") from exc
        transaction_id, order_id = _text(obj.get("id")), _text(_dig(obj, "order.id"))
        if not transaction_id or not order_id:
            raise GatewayError("A transaction callback needs its id and its order id.")
        success, pending = _truthy(obj.get("success")), _truthy(obj.get("pending"))
        if any(_truthy(obj.get(key)) for key in ("is_voided", "is_refunded", "has_parent_transaction")):
            outcome = Outcome.FAILED  # a void, refund or child transaction never posts money; after a confirmation it is flagged
        elif success and not pending:
            outcome = Outcome.CONFIRMED
        elif pending:
            outcome = Outcome.PENDING
        else:
            outcome = Outcome.FAILED
        return Callback(transaction_id=transaction_id, order_id=order_id, merchant_reference=_text(_dig(obj, "order.merchant_order_id")),
                        amount_cents=amount_cents, currency=_text(obj.get("currency")) or "EGP", outcome=outcome, raw=obj)
