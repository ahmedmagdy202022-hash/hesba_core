"""T13 (MARKET-PARITY): an online store by file, until a store account is linked (the API part needs one).

The catalogue goes out as the store's own product CSV; orders come back as the store's order export and become
draft sales invoices. ``StoreOrder`` remembers each imported order, so the same file twice never makes it twice.
"""

from django.conf import settings
from django.db import models


class StoreSource(models.TextChoices):
    SHOPIFY = "shopify", "Shopify"
    TEMPLATE = "template", "Hesba template (WooCommerce or another store)"


class StoreOrder(models.Model):
    source = models.CharField(max_length=12, choices=StoreSource.choices)
    order_number = models.CharField(max_length=60)
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="store_order")
    #: The order's total as the store had it (with its shipping and tax), to compare with the draft.
    store_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["source", "order_number"], name="onlinestore_order_once")]

    def __str__(self):
        return f"{self.source} {self.order_number}"
