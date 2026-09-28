from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from config.money import money_round


class DiningTable(models.Model):
    """RESTO-001: a table in the hall (or a seat at the bar)."""

    name = models.CharField(max_length=40, unique=True)
    area = models.CharField(max_length=60, blank=True, help_text="Hall, terrace, family section…")
    seats = models.PositiveSmallIntegerField(default=4)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class OrderKind(models.TextChoices):
    DINE_IN = "dine_in", "Dine in"
    TAKEAWAY = "takeaway", "Takeaway"
    DELIVERY = "delivery", "Delivery"


class OrderStatus(models.TextChoices):
    OPEN = "open", "Open"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class Order(models.Model):
    """An order being taken; paying it makes one posted sales invoice through the POS engine."""

    number = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=20, choices=OrderKind.choices, default=OrderKind.DINE_IN)
    table = models.ForeignKey(DiningTable, on_delete=models.PROTECT, null=True, blank=True, related_name="orders")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.OPEN)
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="restaurant_orders")
    waiter = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="restaurant_orders")
    guests = models.PositiveSmallIntegerField(default=1)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="restaurant_order")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="restaurant_orders")
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            # One open order per table: the second waiter joins the first's order.
            models.UniqueConstraint(fields=["table"], condition=Q(status="open"), name="restaurant_one_open_order_per_table"),
        ]

    def __str__(self):
        return self.number

    def live_lines(self):
        return [line for line in self.lines.all() if not line.voided]

    @property
    def total(self):
        return money_round(sum((line.amount for line in self.live_lines()), Decimal("0.00")))


class KitchenTicket(models.Model):
    """What was sent to the kitchen in one go; printed once on the kitchen printer."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="tickets")
    sequence = models.PositiveSmallIntegerField()
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="kitchen_tickets")
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "sequence"]
        constraints = [models.UniqueConstraint(fields=["order", "sequence"], name="restaurant_ticket_sequence")]


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="restaurant_lines")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=120, blank=True, help_text="For the kitchen: no onions, extra spicy…")
    ticket = models.ForeignKey(KitchenTicket, on_delete=models.SET_NULL, null=True, blank=True, related_name="lines")
    voided = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]

    @property
    def amount(self):
        return money_round(self.quantity * self.unit_price)
