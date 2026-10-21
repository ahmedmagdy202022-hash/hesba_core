"""T1 (MARKET-PARITY): the quotation and the sales order (HG-054, additive tables only).

A quotation is offered to a customer, sent, and accepted, rejected or
cancelled. Once accepted it becomes a sales order with its own number, and is
invoiced in one go or in parts. Nothing here posts or moves stock: each part is
an ordinary sales invoice made on the ordinary invoice form, and the link
table only remembers which invoice lines took which order quantities.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from config.money import money_round

HUNDRED = Decimal("100")


class Status(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    CONFIRMED = "confirmed", "Confirmed"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"
    CLOSED = "closed", "Closed"


#: A quotation is still being offered while it is a draft or sent.
OFFERED = (Status.DRAFT, Status.SENT)


class SalesOrder(models.Model):
    number = models.CharField(max_length=20, unique=True, help_text="The quotation's number (QT-…).")
    order_number = models.CharField(max_length=20, unique=True, null=True, blank=True, help_text="Given when the customer accepts (SO-…).")
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="sales_orders")
    quote_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)
    salesperson = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    terms = models.TextField(blank=True, help_text="Delivery, payment and validity terms printed on the quotation.")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-quote_date", "-pk"]

    def __str__(self):
        return self.order_number or self.number

    @property
    def is_order(self):
        return self.status in (Status.CONFIRMED, Status.CLOSED)

    @property
    def display_number(self):
        return self.order_number or self.number

    def is_expired(self, today=None):
        today = today or timezone.localdate()
        return self.status in OFFERED and self.valid_until is not None and self.valid_until < today


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"),
                                           validators=[MinValueValidator(Decimal("0")), MaxValueValidator(HUNDRED)])
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                   help_text="The VAT % the customer accepted, kept when the quotation becomes an order (empty while it is offered).")

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["order", "item"], name="orders_one_line_per_item")]

    def discount_for(self, quantity, already=Decimal("0")):
        """The discount on ``quantity`` more units after ``already`` were invoiced.

        Worked out on the running total so the parts always add back to the line's own discount:
        the last part takes whatever rounding is left."""

        if already + quantity >= self.quantity:
            return self.discount - self.discount_for(already) if already > 0 else self.discount
        whole = self.quantity * self.unit_price * self.discount_percent / HUNDRED
        return money_round(whole * (already + quantity) / self.quantity) - money_round(whole * already / self.quantity)

    @property
    def gross(self):
        return money_round(self.quantity * self.unit_price)

    @property
    def discount(self):
        return money_round(self.quantity * self.unit_price * self.discount_percent / HUNDRED)

    @property
    def amount(self):
        return self.gross - self.discount


class OrderInvoiceLine(models.Model):
    """Which ordinary sales-invoice line took how much of an order line."""

    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name="invoiced")
    sales_line = models.ForeignKey("sales.SalesLine", on_delete=models.CASCADE, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["order_line", "sales_line"], name="orders_one_link_per_line")]
