"""HG-070 (S4): which gateway this server talks to.

* **Live Paymob** only when ``settings.PAYMOB_LIVE`` is on **and** all four keys are in the server's environment
  (``PAYMOB_API_KEY``, ``PAYMOB_HMAC_SECRET``, ``PAYMOB_INTEGRATION_ID``, ``PAYMOB_IFRAME_ID``).
* Otherwise the **mock** when ``settings.PAYMENT_GATEWAY_MOCK`` is on (development and tests): Paymob's own code with
  in-process answers and no socket.
* Otherwise **none**: the feature shows «غير موصّلة», no link is made and every callback is refused.

``PAYMOB_LIVE`` on with a key missing gives *none*, never the mock: a half-configured live server must not pretend.
"""

import hashlib
import hmac

from django.conf import settings

from .base import Callback, Checkout, GatewayError, Outcome, PaymentProvider  # noqa: F401 - the interface, re-exported


def live_keys():
    return (getattr(settings, "PAYMOB_API_KEY", ""), getattr(settings, "PAYMOB_HMAC_SECRET", ""),
            getattr(settings, "PAYMOB_INTEGRATION_ID", ""), getattr(settings, "PAYMOB_IFRAME_ID", ""))


def _mock_secret():
    """The mock signs with its own secret, derived from SECRET_KEY: never empty, never a constant in the repo."""

    return hmac.new(settings.SECRET_KEY.encode(), b"hesba.paymob.mock.v1", hashlib.sha256).hexdigest()


def active():
    if getattr(settings, "PAYMOB_LIVE", False):
        keys = live_keys()
        if all(str(key).strip() for key in keys):
            from .paymob import PaymobProvider

            return PaymobProvider(*(str(key).strip() for key in keys))
        return None
    if getattr(settings, "PAYMENT_GATEWAY_MOCK", False):
        from .mock import MockPaymob

        return MockPaymob(hmac_secret=_mock_secret())
    return None
