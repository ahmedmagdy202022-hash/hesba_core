from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Cashbox(models.Model):
    """Money holder.

    Cashbox balances must be calculated from opening balance plus real cashbox
    movements only. Invoices later affect cashboxes only by the actual paid
    amount, never by invoice total or remaining due.
    """

    cashbox_code = models.CharField(max_length=80, unique=True)
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default="EGP")
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    import_batch_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cashbox_code"]
        verbose_name = "Cashbox"
        verbose_name_plural = "Cashboxes"

    def __str__(self):
        return f"{self.cashbox_code} - {self.name_ar}"


class CashboxMovementType(models.TextChoices):
    PURCHASE_PAYMENT = "purchase_payment", "Purchase payment"
    SUPPLIER_PAYMENT = "supplier_payment", "Supplier payment"
    CUSTOMER_PAYMENT = "customer_payment", "Customer payment"
    DIRECT_IN = "direct_in", "Direct in"
    DIRECT_OUT = "direct_out", "Direct out"
    TRANSFER_IN = "transfer_in", "Transfer in"
    TRANSFER_OUT = "transfer_out", "Transfer out"
    ADJUSTMENT = "adjustment", "Adjustment"


class CashboxDirection(models.TextChoices):
    IN = "in", "In"
    OUT = "out", "Out"


class CashboxMovement(models.Model):
    """Actual cash movement.

    Cashbox reports must read from this table. Invoice totals must never move
    cashboxes directly; only paid amounts or direct cash movements appear here.
    """

    cashbox = models.ForeignKey(
        Cashbox,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    movement_date = models.DateField()
    movement_type = models.CharField(max_length=40, choices=CashboxMovementType.choices)
    direction = models.CharField(max_length=10, choices=CashboxDirection.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    purchase_invoice = models.ForeignKey(
        "purchases.PurchaseInvoice",
        on_delete=models.PROTECT,
        related_name="cashbox_movements",
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_cashbox_movements",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-movement_date", "-id"]
        indexes = [
            models.Index(fields=["cashbox", "movement_date"]),
            models.Index(fields=["movement_type"]),
            models.Index(fields=["purchase_invoice"]),
        ]
        verbose_name = "Cashbox Movement"
        verbose_name_plural = "Cashbox Movements"

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Movement amount must be greater than zero."})

    def __str__(self):
        return f"{self.cashbox} / {self.movement_date} / {self.direction} / {self.amount}"
