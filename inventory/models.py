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


class StockOperationType(models.TextChoices):
    TRANSFER = "transfer", "Transfer"
    ADJUSTMENT = "adjustment", "Adjustment"


class StockOperationStatus(models.TextChoices):
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


class StockAdjustmentDirection(models.TextChoices):
    IN = "in", "Increase"
    OUT = "out", "Decrease"


class StockOperation(models.Model):
    """Header linking atomic stock movements and their later reversals."""

    reference_number = models.CharField(max_length=100, unique=True)
    operation_date = models.DateField()
    operation_type = models.CharField(max_length=20, choices=StockOperationType.choices)
    item = models.ForeignKey(
        "master_data.Item", on_delete=models.PROTECT, related_name="stock_operations"
    )
    source_location = models.ForeignKey(
        "master_data.Location",
        on_delete=models.PROTECT,
        related_name="stock_operations_out",
        null=True,
        blank=True,
    )
    destination_location = models.ForeignKey(
        "master_data.Location",
        on_delete=models.PROTECT,
        related_name="stock_operations_in",
        null=True,
        blank=True,
    )
    adjustment_direction = models.CharField(
        max_length=10,
        choices=StockAdjustmentDirection.choices,
        blank=True,
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=StockOperationStatus.choices,
        default=StockOperationStatus.POSTED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_stock_operations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cancelled_stock_operations",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    reversal_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-operation_date", "-id"]
        indexes = [
            models.Index(fields=["operation_type", "status"]),
            models.Index(fields=["operation_date"]),
            models.Index(fields=["item"]),
        ]

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be greater than zero."})
        if self.unit_cost is not None and self.unit_cost < 0:
            raise ValidationError({"unit_cost": "Unit cost cannot be negative."})
        if self.operation_type == StockOperationType.TRANSFER:
            if not (self.reason or "").strip():
                raise ValidationError({"reason": "Transfer reason is required."})
            if not self.source_location_id or not self.destination_location_id:
                raise ValidationError("A transfer requires source and destination locations.")
            if self.source_location_id == self.destination_location_id:
                raise ValidationError("Transfer locations must be different.")
            if self.adjustment_direction:
                raise ValidationError("A transfer cannot have an adjustment direction.")
        elif self.operation_type == StockOperationType.ADJUSTMENT:
            if self.adjustment_direction not in StockAdjustmentDirection.values:
                raise ValidationError("An adjustment direction is required.")
            if not (self.reason or "").strip():
                raise ValidationError({"reason": "Adjustment reason is required."})
            location_ids = [self.source_location_id, self.destination_location_id]
            if sum(value is not None for value in location_ids) != 1:
                raise ValidationError("An adjustment requires exactly one location.")

    def __str__(self):
        return self.reference_number


class StockMovement(models.Model):
    """Traceable inventory movement.

    Stock reports must calculate inventory by Item + Location from this table.
    Purchases, sales, returns, transfers, adjustments, and opening stock create
    controlled movement rows instead of changing balances directly.
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
    sales_invoice = models.ForeignKey(
        "sales.SalesInvoice",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    sales_line = models.ForeignKey(
        "sales.SalesLine",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    purchase_return = models.ForeignKey(
        "purchases.PurchaseReturn",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    purchase_return_line = models.ForeignKey(
        "purchases.PurchaseReturnLine",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    sales_return = models.ForeignKey(
        "sales.SalesReturn",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    sales_return_line = models.ForeignKey(
        "sales.SalesReturnLine",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
    )
    stock_operation = models.ForeignKey(
        StockOperation,
        on_delete=models.PROTECT,
        related_name="movements",
        null=True,
        blank=True,
    )
    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="reversal_movements",
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
            models.Index(fields=["sales_invoice"]),
            models.Index(fields=["sales_line"]),
            models.Index(fields=["stock_operation"]),
            models.Index(fields=["reversal_of"]),
            models.Index(fields=["purchase_return"]),
            models.Index(fields=["purchase_return_line"]),
            models.Index(fields=["sales_return"]),
            models.Index(fields=["sales_return_line"]),
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
