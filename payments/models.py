"""HG-070 (S4): an online payment link for a posted sales invoice, and every gateway transaction behind it.

* ``PaymentLink``: what the customer is asked to pay (at most the invoice's outstanding, ``remaining_due − applied``),
  through which gateway, into which «بوابة الدفع» bank account (the invoice's entity's), until when. One live
  (``new``) link per invoice. An unpaid link is expired, never deleted.
* ``GatewayTransaction``: the state of one gateway transaction id (unique per provider): pending, confirmed or
  failed, the last payload that set it, and the ``CustomerPayment`` it made. Money is recorded on exactly one
  transition, new or pending → confirmed, under a row lock; each callback received is also an audit row (the log).

No key or secret is ever stored here: they live in the server's environment (``settings.PAYMOB_*``).
"""

from django.conf import settings
from django.db import models


class LinkStatus(models.TextChoices):
    NEW = "new", "Waiting for payment"
    PAID = "paid", "Paid"
    #: The gateway confirmed a different amount from the link's: what was taken is recorded, the invoice is never adjusted.
    MISMATCH = "mismatch", "Paid, different amount"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"


class PaymentLink(models.Model):
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, related_name="payment_links")
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="payment_links")
    #: The «بوابة الدفع» bank account of the invoice's entity, fixed when the link is made (HG-034).
    cashbox = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, related_name="payment_links")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="EGP")
    provider = models.CharField(max_length=20)
    #: Our order id at the gateway (``merchant_order_id``): random, so the gateway's return can find the link.
    reference = models.CharField(max_length=40, unique=True)
    #: The random key in the customer's address (the address carries it signed).
    token = models.CharField(max_length=64, unique=True)
    gateway_order_id = models.CharField(max_length=64, blank=True, db_index=True)
    checkout_url = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=LinkStatus.choices, default=LinkStatus.NEW, db_index=True)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-pk"]
        constraints = [
            models.UniqueConstraint(fields=["invoice"], condition=models.Q(status="new"), name="payment_link_one_live_per_invoice"),
            models.CheckConstraint(check=models.Q(amount__gt=0), name="payment_link_amount_positive"),
        ]

    def __str__(self):
        return f"{self.reference} ({self.status})"


class TransactionOutcome(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    FAILED = "failed", "Failed"


class GatewayTransaction(models.Model):
    provider = models.CharField(max_length=20)
    transaction_id = models.CharField(max_length=64)
    #: None for a callback about an order no link of ours made (recorded and ignored).
    link = models.ForeignKey(PaymentLink, on_delete=models.PROTECT, null=True, blank=True, related_name="transactions")
    outcome = models.CharField(max_length=10, choices=TransactionOutcome.choices)
    amount_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="EGP")
    hmac_ok = models.BooleanField(default=False)
    payload = models.JSONField(default=dict, blank=True)
    #: The payment this transaction recorded; set once, on the single confirmed transition.
    customer_payment = models.OneToOneField("sales.CustomerPayment", on_delete=models.PROTECT, null=True, blank=True, related_name="gateway_transaction")
    #: A person must look: a failure or refund after a confirmation, an unknown order, a currency we do not take,
    #: a second payment on a paid link, or a posting that could not be made (e.g. a closed month).
    needs_review = models.BooleanField(default=False)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-pk"]
        constraints = [models.UniqueConstraint(fields=["provider", "transaction_id"], name="gateway_transaction_unique_id")]

    def __str__(self):
        return f"{self.provider}:{self.transaction_id} ({self.outcome})"
