"""T14 (MARKET-PARITY): shipping companies and cash on delivery (HG-061, offline part).

A courier collects the money at the customer's door and hands it over later, less its fee. Until then the money
is a receivable from the courier, not cash: each courier has a box of kind ``courier`` (courier collections
receivable in the chart). Delivery records the customer's payment into that box; the courier's settlement
transfers the net to a bank or cashbox and books its fee as an expense from the box. A parcel that comes back uses
the ordinary sales return; a lost one is written off as an expense with its reason.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models


class Courier(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=50, blank=True)
    #: What it charges for one parcel, offered on each new shipment.
    fee_per_parcel = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cashbox = models.OneToOneField("cashboxes.Cashbox", on_delete=models.PROTECT, related_name="courier")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ShipmentStatus(models.TextChoices):
    WITH_COURIER = "with_courier", "With the courier"
    DELIVERED = "delivered", "Delivered and collected"
    SETTLED = "settled", "Settled by the courier"
    RETURNED = "returned", "Came back"
    LOST = "lost", "Lost"


class Settlement(models.Model):
    courier = models.ForeignKey(Courier, on_delete=models.PROTECT, related_name="settlements")
    settled_on = models.DateField()
    destination = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, related_name="+")
    collected = models.DecimalField(max_digits=14, decimal_places=2)
    fees = models.DecimalField(max_digits=14, decimal_places=2)
    net = models.DecimalField(max_digits=14, decimal_places=2)
    transfer = models.OneToOneField("cashboxes.CashboxOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="courier_settlement")
    fee_expense = models.OneToOneField("expenses.Expense", on_delete=models.PROTECT, null=True, blank=True, related_name="courier_settlement")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-settled_on", "-pk"]


class Shipment(models.Model):
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, related_name="shipments")
    courier = models.ForeignKey(Courier, on_delete=models.PROTECT, related_name="shipments")
    tracking_number = models.CharField(max_length=80, blank=True)
    #: What the courier collects at the door (0: paid before shipping).
    cod_amount = models.DecimalField(max_digits=14, decimal_places=2)
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=14, choices=ShipmentStatus.choices, default=ShipmentStatus.WITH_COURIER)
    shipped_on = models.DateField()
    closed_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    payment = models.OneToOneField("sales.CustomerPayment", on_delete=models.PROTECT, null=True, blank=True, related_name="shipment")
    loss = models.OneToOneField("expenses.Expense", on_delete=models.PROTECT, null=True, blank=True, related_name="lost_shipment")
    settlement = models.ForeignKey(Settlement, on_delete=models.PROTECT, null=True, blank=True, related_name="shipments")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-shipped_on", "-pk"]
