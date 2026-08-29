from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from config.money import money_round


class SalesInvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


class SalesPaymentStatus(models.TextChoices):
    CREDIT = "credit", "Credit"
    PARTIAL = "partial", "Partial"
    PAID = "paid", "Paid"


class SalesInvoice(models.Model):
    """Sales invoice header.

    Sales invoices affect customers only, never suppliers. Posting logic must
    create customer ledger, cashbox, and stock movement rows instead of changing
    balances directly on the invoice.
    """

    invoice_number = models.CharField(max_length=80, unique=True)
    invoice_date = models.DateField()
    customer = models.ForeignKey(
        "master_data.Customer",
        on_delete=models.PROTECT,
        related_name="sales_invoices",
    )
    selling_location = models.ForeignKey(
        "master_data.Location",
        on_delete=models.PROTECT,
        related_name="sales_dispatches",
    )
    cashbox = models.ForeignKey(
        "cashboxes.Cashbox",
        on_delete=models.PROTECT,
        related_name="sales_receipts",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=SalesInvoiceStatus.choices,
        default=SalesInvoiceStatus.DRAFT,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=SalesPaymentStatus.choices,
        default=SalesPaymentStatus.CREDIT,
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
        related_name="created_sales_invoices",
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
            models.Index(fields=["customer"]),
            models.Index(fields=["status", "payment_status"]),
        ]
        verbose_name = "Sales Invoice"
        verbose_name_plural = "Sales Invoices"

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
            return SalesPaymentStatus.PAID
        if self.paid_now and self.paid_now > 0:
            return SalesPaymentStatus.PARTIAL
        return SalesPaymentStatus.CREDIT

    def __str__(self):
        return f"{self.invoice_number} - {self.customer}"


class SalesLine(models.Model):
    """Sales invoice line.

    Cost and profit fields are filled only by controlled posting logic and must
    remain hidden from users without cost/profit permissions.
    """

    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_number = models.PositiveIntegerField()
    item = models.ForeignKey(
        "master_data.Item",
        on_delete=models.PROTECT,
        related_name="sales_lines",
    )
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_sale_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    line_total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    line_cost_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    line_profit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["invoice", "line_number"]
        constraints = [
            models.UniqueConstraint(fields=["invoice", "line_number"], name="unique_sales_line_number"),
        ]
        indexes = [
            models.Index(fields=["invoice", "line_number"]),
            models.Index(fields=["item"]),
        ]
        verbose_name = "Sales Line"
        verbose_name_plural = "Sales Lines"

    @property
    def calculated_line_total(self):
        if self.quantity is None or self.unit_sale_price is None:
            return None
        return money_round((self.quantity * self.unit_sale_price) - (self.line_discount_amount or Decimal("0")))

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be greater than zero."})
        if self.unit_sale_price is not None and self.unit_sale_price < 0:
            raise ValidationError({"unit_sale_price": "Unit sale price cannot be negative."})
        if self.line_discount_amount is not None and self.line_discount_amount < 0:
            raise ValidationError({"line_discount_amount": "Line discount cannot be negative."})
        if self.line_total_amount is not None and self.line_total_amount < 0:
            raise ValidationError({"line_total_amount": "Line total cannot be negative."})
        if self.unit_cost is not None and self.unit_cost < 0:
            raise ValidationError({"unit_cost": "Unit cost cannot be negative."})
        if self.line_cost_amount is not None and self.line_cost_amount < 0:
            raise ValidationError({"line_cost_amount": "Line cost cannot be negative."})

        calculated_total = self.calculated_line_total
        if calculated_total is not None and self.line_total_amount != calculated_total:
            raise ValidationError({"line_total_amount": "Line total must equal quantity × unit price minus discount."})

    def __str__(self):
        return f"{self.invoice.invoice_number} / {self.line_number} / {self.item}"


class CustomerPaymentStatus(models.TextChoices):
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


class CustomerPayment(models.Model):
    """Standalone customer payment.

    Customer payments affect customers and cashboxes only. They must never affect
    suppliers, purchases, inventory, or item cost.
    """

    payment_number = models.CharField(max_length=80, unique=True)
    payment_date = models.DateField()
    customer = models.ForeignKey(
        "master_data.Customer",
        on_delete=models.PROTECT,
        related_name="customer_payments",
    )
    cashbox = models.ForeignKey(
        "cashboxes.Cashbox",
        on_delete=models.PROTECT,
        related_name="customer_payments",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=CustomerPaymentStatus.choices,
        default=CustomerPaymentStatus.POSTED,
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_customer_payments",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        indexes = [
            models.Index(fields=["payment_number"]),
            models.Index(fields=["payment_date"]),
            models.Index(fields=["customer"]),
            models.Index(fields=["cashbox"]),
            models.Index(fields=["status"]),
        ]
        verbose_name = "Customer Payment"
        verbose_name_plural = "Customer Payments"

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Customer payment amount must be greater than zero."})

    def __str__(self):
        return f"{self.payment_number} - {self.customer}"


class CustomerLedgerEntryType(models.TextChoices):
    SALES_DUE = "sales_due", "Sales due"
    CUSTOMER_PAYMENT = "customer_payment", "Customer payment"
    SALES_RETURN = "sales_return", "Sales return"
    OPENING_BALANCE = "opening_balance", "Opening balance"
    ADJUSTMENT = "adjustment", "Adjustment"


class CustomerLedgerEntry(models.Model):
    """Customer balance movement.

    Sales invoices create customer due only by remaining_due. Customer payments
    decrease customer due and move cashbox by the actual received amount.
    """

    customer = models.ForeignKey(
        "master_data.Customer",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    entry_date = models.DateField()
    entry_type = models.CharField(max_length=40, choices=CustomerLedgerEntryType.choices)
    sales_invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.PROTECT,
        related_name="customer_ledger_entries",
        null=True,
        blank=True,
    )
    customer_payment = models.ForeignKey(
        CustomerPayment,
        on_delete=models.PROTECT,
        related_name="customer_ledger_entries",
        null=True,
        blank=True,
    )
    opening_balance_adjustment = models.ForeignKey(
        "cashboxes.OpeningBalanceAdjustment",
        on_delete=models.PROTECT,
        related_name="customer_ledger_entries",
        null=True,
        blank=True,
    )
    due_increase = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    due_decrease = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    description = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_customer_ledger_entries",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        indexes = [
            models.Index(fields=["customer", "entry_date"]),
            models.Index(fields=["entry_type"]),
            models.Index(fields=["sales_invoice"]),
            models.Index(fields=["customer_payment"]),
            models.Index(fields=["opening_balance_adjustment"]),
        ]
        verbose_name = "Customer Ledger Entry"
        verbose_name_plural = "Customer Ledger Entries"

    def clean(self):
        if self.due_increase is not None and self.due_increase < 0:
            raise ValidationError({"due_increase": "Due increase cannot be negative."})
        if self.due_decrease is not None and self.due_decrease < 0:
            raise ValidationError({"due_decrease": "Due decrease cannot be negative."})
        if self.due_increase and self.due_decrease:
            raise ValidationError("Customer ledger entry cannot increase and decrease due at the same time.")

    def __str__(self):
        return f"{self.customer} / {self.entry_date} / {self.entry_type}"
