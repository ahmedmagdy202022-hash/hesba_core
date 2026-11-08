"""HG-070 (S4): what a payment gateway must do for Hesba, whatever company runs it.

A provider makes a checkout for an amount (``create_checkout``), checks that a callback really came from the gateway
(``verify_callback``) and reads it (``parse_callback``). It never records money: that is ``payments.services``, on a
verified, confirmed callback only. Paymob is the first provider; Fawry and others slot in behind the same three calls.
"""

from dataclasses import dataclass, field


class GatewayError(Exception):
    """The gateway refused, could not be reached, or answered something we cannot read."""


class Outcome:
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass
class Checkout:
    order_id: str  # the gateway's own order id
    checkout_url: str  # where the customer pays (the gateway's page)
    raw: dict = field(default_factory=dict)


@dataclass
class Callback:
    transaction_id: str
    order_id: str
    merchant_reference: str
    amount_cents: int
    currency: str
    outcome: str
    raw: dict = field(default_factory=dict)


class PaymentProvider:
    #: Stored on the link and the transaction row; transaction ids are unique per provider code.
    code = ""
    #: The ``CustomerPayment.payment_number`` prefix, e.g. ``PAYMOB-<transaction id>``.
    payment_prefix = ""
    #: True only for a provider that talks to the real gateway with real keys.
    live = False

    def create_checkout(self, *, amount_cents, currency, merchant_reference, customer, expires_seconds):
        raise NotImplementedError

    def verify_callback(self, payload, received_hmac):
        raise NotImplementedError

    def parse_callback(self, payload):
        raise NotImplementedError
