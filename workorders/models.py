"""SVC-001: the work order of a repair or service business (HG-041, design A).

A device or a request is received, diagnosed, quoted, approved, repaired and
delivered. Parts and labour are planned on the order; nothing leaves stock
until delivery, when the order becomes one ordinary sales invoice through the
unchanged sales engine. Parts under warranty go on that invoice at price 0.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Status(models.TextChoices):
    RECEIVED = "received", "Received"
    DIAGNOSED = "diagnosed", "Diagnosed"
    QUOTED = "quoted", "Quoted"
    APPROVED = "approved", "Approved"
    IN_REPAIR = "in_repair", "In repair"
    READY = "ready", "Ready"
    REFUSED = "refused", "Quote refused"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"


OPEN = (Status.RECEIVED, Status.DIAGNOSED, Status.QUOTED, Status.APPROVED, Status.IN_REPAIR, Status.READY, Status.REFUSED)


class WorkOrder(models.Model):
    number = models.CharField(max_length=20, unique=True)
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="work_orders")
    device = models.CharField(max_length=160, help_text="What was received, or the service asked for.")
    serial = models.CharField(max_length=80, blank=True)
    accessories = models.CharField(max_length=255, blank=True, help_text="Charger, case, bag… handed in with it.")
    problem = models.TextField()
    diagnosis = models.TextField(blank=True)
    technician = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="work_orders")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    received_at = models.DateTimeField(default=timezone.now)
    promised_on = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    warranty_days = models.PositiveIntegerField(default=0)
    warranty_until = models.DateField(null=True, blank=True)
    warranty_of = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="warranty_returns",
                                    help_text="The delivered order this one came back under warranty of.")
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="work_orders")
    notes = models.CharField(max_length=255, blank=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-pk"]

    def __str__(self):
        return f"{self.number} — {self.device}"

    @property
    def is_open(self):
        return self.status in OPEN


class WorkOrderLine(models.Model):
    order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1"), validators=[MinValueValidator(Decimal("0.001"))])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))])
    is_warranty = models.BooleanField(default=False, help_text="Covered by warranty: billed at 0, its cost still counted.")

    class Meta:
        ordering = ["pk"]

    @property
    def amount(self):
        return Decimal("0") if self.is_warranty else (self.quantity * self.unit_price).quantize(Decimal("0.01"))


class WorkOrderEvent(models.Model):
    """The order's timeline: each status it went through, by whom and when."""

    order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=12, choices=Status.choices)
    note = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "pk"]
