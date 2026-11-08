"""HG-069 (S5): a deposit taken at booking.

The deposit *is* a customer payment on account (``sales.CustomerPayment``, made through
``record_customer_payment``): the customer's due goes down and the cashbox or bank goes up the day it is taken.
This row only says what the payment was for and where it stands:

* ``held``: the customer's money, waiting (booked in «2105 دفعات مقدمة من العملاء» by the ledger projector);
* ``applied``: the service invoice posted and the deposit settles it (``settlements.InvoiceSettlement``);
* ``forfeited``: the booking was cancelled late and the deposit settled a posted «رسوم إلغاء» invoice;
* ``refunded``: the payment was cancelled (``cancel_customer_payment``, approvable under HG-068).

The booked thing is a generic link, so an appointment today, a patient's online booking (M4) or a bakery pre-order
(R10) can all hold one. The appointment itself still never moves money (``appointments.models``).
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class DepositStatus(models.TextChoices):
    HELD = "held", "Held"
    APPLIED = "applied", "Applied"
    FORFEITED = "forfeited", "Forfeited"
    REFUNDED = "refunded", "Refunded"


class Deposit(models.Model):
    payment = models.OneToOneField("sales.CustomerPayment", on_delete=models.PROTECT, related_name="deposit")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    status = models.CharField(max_length=12, choices=DepositStatus.choices, default=DepositStatus.HELD, db_index=True)
    #: The invoice this deposit settles (set while it is still a draft, so posting knows which deposit to apply),
    #: or the cancellation-fee invoice it was forfeited into. Cleared when that invoice is cancelled.
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="deposits")
    #: HG-069 rounding: a cancellation fee is built back from the deposit with VAT inside it, and one tax line reaches
    #: only some totals, so up to one piastre may stay as the customer's credit on account. Shown as «فرق تقريب».
    rounding_credit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]
        indexes = [models.Index(fields=["content_type", "object_id"])]
        constraints = [
            # One held deposit per booked thing (the service also locks the booking row before taking one).
            models.UniqueConstraint(fields=["content_type", "object_id"], condition=models.Q(status="held"), name="deposit_one_held_per_target"),
        ]

    @property
    def amount(self):
        return self.payment.amount

    def __str__(self):
        return f"{self.payment.payment_number} ({self.status})"
