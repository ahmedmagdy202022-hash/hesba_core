"""INSTAL-001: an instalment schedule for what a customer still owes on a sale.

The sale is an ordinary posted credit invoice: the customer ledger already
holds the debt. A plan only splits that amount into dated instalments, and
each collection is an ordinary customer payment (``record_customer_payment``)
linked to the plan. Which instalments are paid is worked out from the linked
payments that are still posted, oldest instalment first (HG-022).
"""

from django.conf import settings
from django.db import models


class PlanStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CANCELLED = "cancelled", "Cancelled"


class InstalmentPlan(models.Model):
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="instalment_plan")
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="instalment_plans")
    financed_amount = models.DecimalField(max_digits=14, decimal_places=2)
    count = models.PositiveSmallIntegerField()
    first_due_date = models.DateField()
    status = models.CharField(max_length=20, choices=PlanStatus.choices, default=PlanStatus.ACTIVE)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.invoice.invoice_number} / {self.count}"


class Instalment(models.Model):
    plan = models.ForeignKey(InstalmentPlan, on_delete=models.CASCADE, related_name="instalments")
    number = models.PositiveSmallIntegerField()
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["plan_id", "number"]
        constraints = [models.UniqueConstraint(fields=["plan", "number"], name="installments_one_number_per_plan")]


class InstalmentPayment(models.Model):
    plan = models.ForeignKey(InstalmentPlan, on_delete=models.PROTECT, related_name="payments")
    payment = models.OneToOneField("sales.CustomerPayment", on_delete=models.PROTECT, related_name="instalment_link")
    created_at = models.DateTimeField(auto_now_add=True)
