from django.conf import settings
from django.db import models


class ProjectStatus(models.TextChoices):
    PLANNED = "planned", "Planned"
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On hold"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "Cancelled"


class Project(models.Model):
    """CONTRACT-001: one job for one customer, with its contract value.

    A project owns no money or stock itself. Progress bills are ordinary sales
    invoices, materials leave stock through an ordinary stock adjustment and
    costs are ordinary expenses; the project only links them so its revenue,
    cost and margin can be read in one place.
    """

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="projects")
    site = models.CharField(max_length=255, blank=True)
    contract_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="projects")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ProjectInvoice(models.Model):
    """A sales invoice (usually a progress bill) that belongs to a project."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="invoices")
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="project_link")
    label = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


class ProjectExpense(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="expenses")
    expense = models.OneToOneField("expenses.Expense", on_delete=models.PROTECT, related_name="project_link")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


class ProjectIssue(models.Model):
    """Materials taken out of stock for a project (a stock adjustment out)."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="issues")
    operation = models.OneToOneField("inventory.StockOperation", on_delete=models.PROTECT, related_name="project_issue")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
