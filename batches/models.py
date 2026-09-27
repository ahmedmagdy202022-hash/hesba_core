"""BATCH-001: batch numbers and expiry dates received into stock.

A ``Batch`` records that a quantity of an item arrived at a location with a
batch number and/or an expiry date: from a purchase line, or registered by
hand for stock already on the shelf. It is a tracking record only. Stock,
cost and every posting stay in ``inventory`` (HG-019); what is left of each
batch is worked out from the real on-hand quantity, earliest expiry first
(see ``batches.services.batch_positions``).
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Batch(models.Model):
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="batches")
    location = models.ForeignKey("master_data.Location", on_delete=models.PROTECT, related_name="batches")
    batch_no = models.CharField(max_length=60, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    received_on = models.DateField()
    # Set for a batch that came in on a purchase: it counts once that invoice is posted.
    purchase_line = models.OneToOneField("purchases.PurchaseLine", on_delete=models.CASCADE, null=True, blank=True, related_name="batch")
    note = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["expiry_date", "received_on", "pk"]
        indexes = [models.Index(fields=["item", "location"]), models.Index(fields=["expiry_date"])]

    def __str__(self):
        return f"{self.item.item_code} {self.batch_no or '-'} {self.expiry_date or ''}".strip()
