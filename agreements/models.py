"""SVC-002: service contracts billed every period (maintenance contracts, monthly retainers).

A contract never moves money by itself. Each period it is due, an ordinary
draft sales invoice is made through the sales engine; posting that invoice is
what touches the customer's account and the cashbox.
"""

from django.conf import settings
from django.db import models


class Every(models.TextChoices):
    MONTH = "month", "Every month"
    QUARTER = "quarter", "Every quarter"
    YEAR = "year", "Every year"


class ContractStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ENDED = "ended", "Ended"


class ServiceContract(models.Model):
    number = models.CharField(max_length=20, unique=True)
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="service_contracts")
    service = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    every = models.CharField(max_length=10, choices=Every.choices, default=Every.MONTH)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    visits_per_period = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=10, choices=ContractStatus.choices, default=ContractStatus.ACTIVE, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_on", "-pk"]

    def __str__(self):
        return self.number


class ContractBill(models.Model):
    """One period of a contract and the invoice that bills it. A cancelled invoice frees the period."""

    contract = models.ForeignKey(ServiceContract, on_delete=models.PROTECT, related_name="bills")
    period_start = models.DateField()
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="contract_bill")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_start", "-pk"]

    def __str__(self):
        return f"{self.contract_id}:{self.period_start}"

