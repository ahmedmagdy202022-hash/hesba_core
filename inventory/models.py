from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class StockMovementType(models.TextChoices):
    PURCHASE_IN = "purchase_in", "Purchase in"
    SALE_OUT = "sale_out", "Sale out"
    PURCHASE_RETURN_OUT = "purchase_return_out", "Purchase return out"
    SALE_RETURN_IN = "sale_return_in", "Sale return in"
    TRANSFER_IN = "transfer_in", "Transfer in"
    TRANSFER_OUT = "transfer_out", "Transfer out"
    ADJUSTMENT_IN = "adjustment_in", "Adjustment in"
    ADJUSTMENT_OUT = "adjustment_out", "Adjustment out"
    OPENING_STOCK = "opening_stock", "Opening stock"


class StockMovement(models.Model):
    """Traceable inventory movement.

    Stock reports must calculate inventory by Item + Location from this table.
    Purchases, sales, returns, transfers, adjustments, and opening stock should
    create controlled movement rows instead of changing item/location balances
    directly.
    """

    movement_date = models.DateField()
    movement_type = models.CharField(max_length=40, choices=StockMovementType.choices)
    item = models.ForeignKey(
        "master_data.Item",
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )
    location = models.ForeignKey(
        "master_data.Location",
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    purchase_invoice = models.ForeignKey(
        "purchases.PurchaseInvoice",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    purchase_line = models.ForeignKey(
        "purchases.PurchaseLine",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_stock_movements",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-movement_date", "-id"]
        indexes = [
            models.Index(fields=["item", "location"]),
            models.Index(fields=["movement_date"]),
            models.Index(fields=["movement_type"]),
            models.Index(fields=["purchase_invoice"]),
            models.Index(fields=["purchase_line"]),
        ]
        verbose_name = "Stock Movement"
        verbose_name_plural = "Stock Movements"

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "Stock movement quantity must be greater than zero."})
        if self.unit_cost is not None and self.unit_cost < 0:
            raise ValidationError({"unit_cost": "Unit cost cannot be negative."})

    def __str__(self):
        return f"{self.movement_date} / {self.movement_type} / {self.item} / {self.location}"
