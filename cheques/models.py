"""T4 (MARKET-PARITY): cheques received from customers and issued to suppliers (HG-056).

A cheque received is an ordinary customer payment into the entity's "cheques in hand" box (a cashbox of kind
``cheques_in``, notes receivable in the chart). Depositing it moves the money from that box into a bank account by
a transfer. A cheque that bounces comes back on its bounce date: out of the bank if it was deposited, out of the
box, and onto the customer's account again. A cheque issued is the mirror: a supplier payment from the "cheques
issued" box (``cheques_out``, notes payable), paid out of the bank when the supplier cashes it. The payment itself
is never edited; every step is a new dated row.
"""

from django.conf import settings
from django.db import models


class ChequeDirection(models.TextChoices):
    IN = "in", "Received"
    OUT = "out", "Issued"


class ChequeStatus(models.TextChoices):
    IN_HAND = "in_hand", "In hand"
    DEPOSITED = "deposited", "Deposited"
    ISSUED = "issued", "Issued"
    CLEARED = "cleared", "Cleared"
    BOUNCED = "bounced", "Bounced"
    CANCELLED = "cancelled", "Cancelled"


class Cheque(models.Model):
    direction = models.CharField(max_length=3, choices=ChequeDirection.choices, db_index=True)
    number = models.CharField(max_length=40)
    bank_name = models.CharField(max_length=120)
    due_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="cheques")
    supplier = models.ForeignKey("master_data.Supplier", on_delete=models.PROTECT, null=True, blank=True, related_name="cheques")
    customer_payment = models.OneToOneField("sales.CustomerPayment", on_delete=models.PROTECT, null=True, blank=True, related_name="cheque")
    supplier_payment = models.OneToOneField("purchases.SupplierPayment", on_delete=models.PROTECT, null=True, blank=True, related_name="cheque")
    #: The cheques box the payment went through (cheques in hand, or cheques issued).
    box = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, related_name="+")
    #: The bank account it was deposited into (received) or is drawn on (issued).
    bank = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=ChequeStatus.choices, db_index=True)
    received_on = models.DateField(help_text="The day it was received or issued.")
    deposited_on = models.DateField(null=True, blank=True)
    cleared_on = models.DateField(null=True, blank=True)
    bounced_on = models.DateField(null=True, blank=True)
    bounce_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    deposit_operation = models.ForeignKey("cashboxes.CashboxOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    payment_operation = models.ForeignKey("cashboxes.CashboxOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    return_operation = models.ForeignKey("cashboxes.CashboxOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "pk"]

    def __str__(self):
        return f"{self.number} — {self.bank_name}"

    @property
    def party(self):
        return self.customer if self.direction == ChequeDirection.IN else self.supplier
