"""SERIAL-001: every unit of a tracked item followed by its serial / IMEI.

Tracking records only (HG-021): stock, cost and posting stay in inventory and
sales/purchases. A serial is *received* by a posted purchase (or registered by
hand for stock already on the shelf), *sold* by a posted sale, and *back in
stock* after a posted sales return. Cancelled documents stop counting, so the
state is always worked out from the documents, never stored.
"""

from django.conf import settings
from django.db import models


class SerialSetting(models.Model):
    """Which items are sold by serial number, and their warranty."""

    item = models.OneToOneField("master_data.Item", on_delete=models.CASCADE, related_name="serial_setting")
    tracked = models.BooleanField(default=True)
    warranty_months = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"{self.item.item_code}: {'tracked' if self.tracked else 'not tracked'}, {self.warranty_months} months"


class SerialNumber(models.Model):
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="serials")
    serial = models.CharField(max_length=80, db_index=True)
    purchase_line = models.ForeignKey("purchases.PurchaseLine", on_delete=models.CASCADE, null=True, blank=True, related_name="serials")
    received_on = models.DateField()
    active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["serial"]

    def __str__(self):
        return self.serial


class SerialSale(models.Model):
    serial = models.ForeignKey(SerialNumber, on_delete=models.PROTECT, related_name="sales")
    sales_line = models.ForeignKey("sales.SalesLine", on_delete=models.CASCADE, related_name="serials")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["serial", "sales_line"], name="serials_once_per_sales_line")]


class SerialReturn(models.Model):
    serial = models.ForeignKey(SerialNumber, on_delete=models.PROTECT, related_name="returns")
    return_line = models.ForeignKey("sales.SalesReturnLine", on_delete=models.CASCADE, related_name="serials")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["serial", "return_line"], name="serials_once_per_return_line")]
