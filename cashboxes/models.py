from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Cashbox(models.Model):
    """Money holder.

    Cashbox balances must be calculated from opening balance plus real cashbox
    movements only. Invoices affect cashboxes only by actual paid amounts.
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
    SALES_RECEIPT = "sales_receipt", "Sales receipt"
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


class FinancialAdjustmentStatus(models.TextChoices):
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


class OpeningBalanceTarget(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    SUPPLIER = "supplier", "Supplier"
    CASHBOX = "cashbox", "Cashbox"


class OpeningBalanceAdjustment(models.Model):
    """Auditable correction to an opening balance after operational use.

    The original master-data value is never rewritten after use. The signed
    amount is represented by append-only ledger/cashbox rows and cancellation
    appends the exact inverse rows.
    """

    adjustment_number = models.CharField(max_length=100, unique=True)
    target_type = models.CharField(max_length=20, choices=OpeningBalanceTarget.choices)
    customer = models.ForeignKey(
        "master_data.Customer",
        on_delete=models.PROTECT,
        related_name="opening_balance_adjustments",
        null=True,
        blank=True,
    )
    supplier = models.ForeignKey(
        "master_data.Supplier",
        on_delete=models.PROTECT,
        related_name="opening_balance_adjustments",
        null=True,
        blank=True,
    )
    cashbox = models.ForeignKey(
        Cashbox,
        on_delete=models.PROTECT,
        related_name="opening_balance_adjustments",
        null=True,
        blank=True,
    )
    adjustment_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=FinancialAdjustmentStatus.choices,
        default=FinancialAdjustmentStatus.POSTED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_opening_balance_adjustments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cancelled_opening_balance_adjustments",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    reversal_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-adjustment_date", "-id"]
        indexes = [
            models.Index(fields=["target_type", "status"]),
            models.Index(fields=["adjustment_date"]),
            models.Index(fields=["customer"]),
            models.Index(fields=["supplier"]),
            models.Index(fields=["cashbox"]),
        ]

    @property
    def target(self):
        return {
            OpeningBalanceTarget.CUSTOMER: self.customer,
            OpeningBalanceTarget.SUPPLIER: self.supplier,
            OpeningBalanceTarget.CASHBOX: self.cashbox,
        }.get(self.target_type)

    def clean(self):
        selected = {
            OpeningBalanceTarget.CUSTOMER: self.customer_id,
            OpeningBalanceTarget.SUPPLIER: self.supplier_id,
            OpeningBalanceTarget.CASHBOX: self.cashbox_id,
        }
        if self.target_type not in selected or selected[self.target_type] is None:
            raise ValidationError("The selected opening-balance target is required.")
        if sum(value is not None for value in selected.values()) != 1:
            raise ValidationError("An opening-balance adjustment must reference exactly one target.")
        if self.amount is not None and self.amount == 0:
            raise ValidationError({"amount": "Adjustment amount cannot be zero."})
        if not (self.reason or "").strip():
            raise ValidationError({"reason": "Adjustment reason is required."})

    def __str__(self):
        return self.adjustment_number


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
    sales_invoice = models.ForeignKey(
        "sales.SalesInvoice",
        on_delete=models.PROTECT,
        related_name="cashbox_movements",
        null=True,
        blank=True,
    )
    supplier_payment = models.ForeignKey(
        "purchases.SupplierPayment",
        on_delete=models.PROTECT,
        related_name="cashbox_movements",
        null=True,
        blank=True,
    )
    customer_payment = models.ForeignKey(
        "sales.CustomerPayment",
        on_delete=models.PROTECT,
        related_name="cashbox_movements",
        null=True,
        blank=True,
    )
    opening_balance_adjustment = models.ForeignKey(
        OpeningBalanceAdjustment,
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
            models.Index(fields=["sales_invoice"]),
            models.Index(fields=["supplier_payment"]),
            models.Index(fields=["customer_payment"]),
            models.Index(fields=["opening_balance_adjustment"]),
        ]
        verbose_name = "Cashbox Movement"
        verbose_name_plural = "Cashbox Movements"

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Movement amount must be greater than zero."})

    def __str__(self):
        return f"{self.cashbox} / {self.movement_date} / {self.direction} / {self.amount}"
