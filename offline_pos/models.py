from django.conf import settings
from django.db import models


class OfflineSale(models.Model):
    """POS-003: one sale rung up while the till was offline, posted once when it synced.

    ``sale_key`` is generated on the device when the sale is made. It is unique,
    so the same sale sent twice (a retry after a lost answer, two tabs) always
    lands on the same invoice instead of posting again.
    """

    sale_key = models.UUIDField(unique=True)
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="offline_sale")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="offline_sales")
    recorded_at = models.DateTimeField(help_text="When the cashier rang it up on the device.")
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-synced_at"]

    def __str__(self):
        return f"{self.sale_key} → {self.invoice_id}"
