from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class SectorCode(models.TextChoices):
    STORE = "store", "Store"
    SERVICES = "services", "Services"
    CONSTRUCTION = "construction", "Construction"
    FACTORY = "factory", "Factory"


class SectorModule(models.Model):
    code = models.CharField(max_length=40, choices=SectorCode.choices, unique=True)
    name_ar = models.CharField(max_length=120)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name_ar}"


class WorkProjectStatus(models.TextChoices):
    OPEN = "open", "Open"
    ON_HOLD = "on_hold", "On hold"
    CLOSED = "closed", "Closed"


class WorkProject(models.Model):
    project_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, blank=True)
    location = models.ForeignKey("master_data.Location", on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=20, choices=WorkProjectStatus.choices, default=WorkProjectStatus.OPEN)
    contract_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project_code"]
        indexes = [models.Index(fields=["project_code"]), models.Index(fields=["status"])]

    def clean(self):
        if self.contract_value is not None and self.contract_value < 0:
            raise ValidationError({"contract_value": "Contract value cannot be negative."})

    def __str__(self):
        return f"{self.project_code} - {self.name}"


class ProjectCostType(models.TextChoices):
    MATERIAL = "material", "Material"
    LABOR = "labor", "Labor"
    SUBCONTRACTOR = "subcontractor", "Subcontractor"
    EQUIPMENT = "equipment", "Equipment"
    OVERHEAD = "overhead", "Overhead"


class ProjectCostEntry(models.Model):
    project = models.ForeignKey(WorkProject, on_delete=models.PROTECT, related_name="cost_entries")
    entry_date = models.DateField()
    cost_type = models.CharField(max_length=30, choices=ProjectCostType.choices)
    supplier = models.ForeignKey("master_data.Supplier", on_delete=models.PROTECT, null=True, blank=True)
    cashbox = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_now = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        indexes = [models.Index(fields=["project", "entry_date"]), models.Index(fields=["cost_type"])]

    def clean(self):
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": "Amount cannot be negative."})
        if self.paid_now is not None and self.paid_now < 0:
            raise ValidationError({"paid_now": "Paid now cannot be negative."})
        if self.paid_now and self.amount is not None and self.paid_now > self.amount:
            raise ValidationError({"paid_now": "Paid now cannot exceed amount."})
        if self.paid_now and self.paid_now > 0 and self.cashbox is None:
            raise ValidationError({"cashbox": "Cashbox is required when paid now is greater than zero."})

    def __str__(self):
        return f"{self.project} / {self.entry_date} / {self.amount}"
