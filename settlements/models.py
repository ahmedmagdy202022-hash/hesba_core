"""HG-069: the one shared table that says which posted customer payment settles which posted invoice.

An invoice's own figures are frozen at posting (``SalesInvoice.clean()`` keeps ``remaining_due == total − paid_now``).
A deposit taken at booking (S5), a paid online link (S4, HG-070), a gift-card redemption (S6, HG-071) or a bill's further
tender (R2b, HG-110) is a
``CustomerPayment`` on account that the customer's ledger already nets; this row is what lets the invoice page and
the "due" totals show that the invoice is paid, without rewriting the posted document.

Every row is backed by a posted payment that the payment-based collection figures already count, so a settlement is
subtracted from what is *due* and never added to what was *collected* (``settlements.services.applied``).
"""

from django.db import models


class SettlementSource(models.TextChoices):
    DEPOSIT = "deposit", "Deposit"
    PAYMENT_LINK = "payment_link", "Payment link"
    GIFT_CARD = "gift_card", "Gift card"
    #: R2b (HG-110): a further tender of a bill paid several ways (cash and a card), recorded by ``checkout`` itself.
    SPLIT = "split", "Split payment"


class InvoiceSettlement(models.Model):
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, related_name="settlements")
    #: One settlement per payment: the OneToOne stops the same money settling two invoices.
    customer_payment = models.OneToOneField("sales.CustomerPayment", on_delete=models.PROTECT, related_name="settlement")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    source = models.CharField(max_length=20, choices=SettlementSource.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name="settlement_amount_positive")]

    def __str__(self):
        return f"{self.invoice_id} ← {self.customer_payment_id} ({self.amount})"
