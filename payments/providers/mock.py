"""HG-070 (S4): Paymob with its network swapped for in-process answers (tests and development).

``MockPaymob`` *is* ``PaymobProvider``: only ``_http`` and the checkout address differ. The HMAC check and the
callback reading are Paymob's own, so a test that passes here passes the real verification. Nothing here opens a
socket. ``callback()`` builds a transaction callback shaped exactly like Paymob's, signed with the mock's secret.
"""

import secrets
from datetime import datetime, timezone as dt_timezone

from django.urls import reverse

from .base import GatewayError
from .paymob import PaymobProvider, sign


class MockPaymob(PaymobProvider):
    code = "paymob_mock"
    payment_prefix = "MOCKPAY"
    live = False

    def __init__(self, hmac_secret, integration_id="100001", iframe_id="200001", fail_with=None):
        super().__init__(api_key="mock-api-key", hmac_secret=hmac_secret, integration_id=integration_id, iframe_id=iframe_id,
                         base_url="https://accept.paymob.invalid")
        self.fail_with = fail_with  # a test can make the "gateway" refuse
        self.calls = []

    def _http(self, method, path, body):
        """Paymob's answers, shaped as its API returns them (trimmed to the keys Hesba reads plus a few for realism)."""

        self.calls.append((method, path, body))
        if self.fail_with:
            raise GatewayError(self.fail_with)
        if path == "/api/auth/tokens":
            return {"token": "ZXlKaGJHY2lPaUpJVXpVeE1pSXNJblI1Y0NJNklrcFhWQ0o5.mock", "profile": {"id": 1001, "user": {"id": 2002}}}
        if path == "/api/ecommerce/orders":
            return {"id": 10_000_000 + secrets.randbelow(89_999_999), "created_at": datetime.now(dt_timezone.utc).isoformat(), "delivery_needed": False,
                    "merchant": {"id": 1001}, "amount_cents": int(body["amount_cents"]), "currency": body["currency"],
                    "merchant_order_id": body["merchant_order_id"], "is_payment_locked": False, "items": [], "payment_status": "UNPAID"}
        if path == "/api/acceptance/payment_keys":
            return {"token": "ZXlKaGJHY2lPaUpJVXpVeE1pSXNJblI1Y0NJNklrcFhWQ0o5.paykey." + secrets.token_urlsafe(12)}
        raise GatewayError(f"Unknown Paymob path {path}.")

    def checkout_url(self, payment_token, merchant_reference):
        return reverse("pay_mock_checkout", args=[merchant_reference])

    # ---- building callbacks the way Paymob sends them ----

    def callback(self, order_id, amount_cents, *, transaction_id=None, success=True, pending=False, currency="EGP",
                 merchant_reference="", extra=None):
        """(payload, hmac) for a Paymob "transaction processed callback" (``POST`` JSON, ``?hmac=`` in the query)."""

        obj = {
            "id": int(transaction_id) if transaction_id is not None else 300_000_000 + secrets.randbelow(99_999_999),
            "pending": pending, "amount_cents": int(amount_cents), "success": success, "is_auth": False, "is_capture": False,
            "is_standalone_payment": True, "is_voided": False, "is_refunded": False, "is_3d_secure": True,
            "integration_id": int(self.integration_id), "profile_id": 1001, "has_parent_transaction": False,
            "order": {"id": int(order_id), "merchant_order_id": merchant_reference, "amount_cents": int(amount_cents), "currency": currency},
            "created_at": "2026-11-08T12:00:00.000000", "currency": currency, "error_occured": False, "owner": 2002,
            "source_data": {"type": "card", "pan": "2346", "sub_type": "MasterCard"},
            "data": {"message": "Approved" if success else ("Pending" if pending else "Declined")},
        }
        obj.update(extra or {})
        return {"type": "TRANSACTION", "obj": obj}, sign(obj, self.hmac_secret)
