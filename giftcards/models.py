"""HG-071 (S6): gift cards. A card is a liability to whoever holds its code until it is spent, never revenue.

The money moves only through services that exist (``giftcards.services``):

* **sold**: a cashbox transfer out of the entity's gift-card box into the cash or bank that took the money
  (cash +V, gift-card box −V: Dr 1101/1102, Cr 2110). No invoice, no revenue, no VAT;
* **redeemed**: a customer payment *into* the gift-card box for the invoice it pays, settled by an
  ``InvoiceSettlement`` (source gift card): customer due −R, box +R (Dr 2110, Cr 1103);
* **breakage**: a direct cash in to the gift-card box, booked to sundry income (4201) by the projector; manual only;
* **reversed**: history only, written when one of the three above is reversed.

A card's balance is read from its entries whose backing document is still posted, so every reversal path (the
gift-card screens, the collections screen, an approval) keeps it right by itself. The box's balance is always minus
the sum of the live cards' balances in its entity.
"""

from django.conf import settings
from django.db import models


class GiftCardStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CANCELLED = "cancelled", "Sale cancelled"


class GiftCard(models.Model):
    #: The shop's sequential number (GC-00001), for lists and the receipt.
    number = models.CharField(max_length=20, unique=True)
    #: What the holder presents to spend it: random, never sequential (the holder is whoever has the code).
    code = models.CharField(max_length=20, unique=True)
    #: HG-034: the entity it was sold in; its money and its redemptions stay there.
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, related_name="+")
    face_value = models.DecimalField(max_digits=14, decimal_places=2)
    buyer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    holder_name = models.CharField(max_length=120, blank=True)
    holder_phone = models.CharField(max_length=50, blank=True)
    #: Shown, never enforced (HG-071): an expired card still pays until a person writes its rest off.
    expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=GiftCardStatus.choices, default=GiftCardStatus.ACTIVE, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]
        constraints = [models.CheckConstraint(check=models.Q(face_value__gt=0), name="giftcard_face_value_positive")]

    def __str__(self):
        return self.number


class EntryKind(models.TextChoices):
    SOLD = "sold", "Sold"
    REDEEMED = "redeemed", "Redeemed"
    BREAKAGE = "breakage", "Written off (breakage)"
    REVERSED = "reversed", "Reversed"


class GiftCardEntry(models.Model):
    card = models.ForeignKey(GiftCard, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(max_length=10, choices=EntryKind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    entry_date = models.DateField()
    #: The sale's transfer or the breakage's cash in.
    operation = models.ForeignKey("cashboxes.CashboxOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    #: The redemption's customer payment into the gift-card box.
    payment = models.ForeignKey("sales.CustomerPayment", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    #: The invoice a redemption pays (kept here, not only in the settlement row, so cancelling it finds the payment).
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    #: For a reversed entry: the entry it undid.
    reverses = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name="giftcard_entry_amount_positive"),
            models.UniqueConstraint(fields=["payment"], condition=models.Q(kind="redeemed"), name="giftcard_one_redemption_per_payment"),
            models.UniqueConstraint(fields=["reverses"], name="giftcard_reversed_once"),
        ]

    def __str__(self):
        return f"{self.card_id} {self.kind} {self.amount}"
