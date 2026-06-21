from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class PurchaseInvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


class PurchasePaymentStatus(models.TextChoices):
    CREDIT = "credit", "Credit"
    PARTIAL = "partial", "Partial"
    PAID = "paid", "Paid"


class PurchaseInvoice(models.Model):
    """Purchase invoice header.

    Business rule foundation:
    - Purchase invoices affect suppliers only, never customers.
    - Supplier due is based on remaining due only.
    - Cashbox will later be affected by paid_now only.
    - Inventory will later increase in receiving_location through stock movements.
    """

    invoice_number = models.CharField(max_length=80, unique=True)
    invoice_date = models.DateField()
    supplier = models.ForeignKey(
        "master_data.Supplier",
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
    )
    receiving_location = models.ForeignKey(
        "master_data.Location",
        on_delete=models.PROTECT,
        related_name="purchase_receipts",
    )
    cashbox = models.ForeignKey(
        "cashboxes.Cashbox",
        on_delete=models.PROTECT,
        related_name="purchase_payments",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=PurchaseInvoiceStatus.choices,
        default=PurchaseInvoiceStatus.DRAFT,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PurchasePaymentStatus.choices,
        default=PurchasePaymentStatus.CREDIT,
    )
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_now = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    remaining_due = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_purchase_invoices",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-invoice_date", "-id"]
        indexes = [
            models.Index(fields=["invoice_number"]),
            models.Index(fields=["invoice_date"]),
            models.Index(fields=["supplier"]),
            models.Index(fields=["status", "payment_status"]),
        ]
        verbose_name = "Purchase Invoice"
        verbose_name_plural = "Purchase Invoices"

    def clean(self):
        money_fields = {
            "subtotal": self.subtotal,
            "discount_amount": self.discount_amount,
            "tax_amount": self.tax_amount,
            "total_amount": self.total_amount,
            "paid_now": self.paid_now,
            "remaining_due": self.remaining_due,
        }
        for field_name, value in money_fields.items():
            if value is not None and value < 0:
                raise ValidationError({field_name: "Value cannot be negative."})

        if self.paid_now is not None and self.total_amount is not None:
            if self.paid_now > self.total_amount:
                raise ValidationError({"paid_now": "Paid now cannot exceed invoice total."})

        expected_remaining = (self.total_amount or Decimal("0")) - (self.paid_now or Decimal("0"))
        if self.remaining_due != expected_remaining:
            raise ValidationError({"remaining_due": "Remaining due must equal total amount minus paid now."})

        expected_status = self.calculate_payment_status()
        if self.payment_status != expected_status:
            raise ValidationError({"payment_status": f"Payment status must be {expected_status}."})

        if self.paid_now and self.paid_now > 0 and self.cashbox is None:
            raise ValidationError({"cashbox": "Cashbox is required when paid now is greater than zero."})

    def calculate_payment_status(self):
        if self.total_amount and self.paid_now == self.total_amount:
            return PurchasePaymentStatus.PAID
        if self.paid_now and self.paid_now > 0:
            return PurchasePaymentStatus.PARTIAL
        return PurchasePaymentStatus.CREDIT

    def __str__(self):
        return f"{self.invoice_number} - {self.supplier}"


class PurchaseLine(models.Model):
    """Purchase invoice line.

    Multi-line invoices start here. Lines do not directly change stock; future
    posting logic must create traceable stock movements into receiving_location.
    """

    invoice = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_number = models.PositiveIntegerField()
    item = models.ForeignKey(
        "master_data.Item",
        on_delete=models.PROTECT,
        related_name="purchase_lines",
    )
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_purchase_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    line_total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["invoice", "line_number"]
        constraints = [
            models.UniqueConstraint(fields=["invoice", "line_number"], name="unique_purchase_line_number"),
        ]
        indexes = [
            models.Index(fields=["invoice", "line_number"]),
            models.Index(fields=["item"]),
        ]
        verbose_name = "Purchase Line"
        verbose_name_plural = "Purchase Lines"

    @property
    def calculated_line_total(self):
        return (self.quantity * self.unit_purchase_price) - self.line_discount_amount

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be greater than zero."})
        if self.unit_purchase_price is not None and self.unit_purchase_price < 0:
            raise ValidationError({"unit_purchase_price": "Unit purchase price cannot be negative."})
        if self.line_discount_amount is not None and self.line_discount_amount < 0:
            raise ValidationError({"line_discount_amount": "Line discount cannot be negative."})
        if self.line_total_amount is not None and self.line_total_amount < 0:
            raise ValidationError({"line_total_amount": "Line total cannot be negative."})
        if self.line_total_amount != self.calculated_line_total:
            raise ValidationError({"line_total_amount": "Line total must equal quantity × unit price minus discount."})

    def __str__(self):
        return f"{self.invoice.invoice_number} / {self.line_number} / {self.item}"
