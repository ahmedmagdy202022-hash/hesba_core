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
    # CONTRACT-002 (HG-038): withheld from each progress certificate until
    # handover, and the share of each certificate that pays back the advance.
    retention_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    advance_recovery_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
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


class CostHeading(models.TextChoices):
    """CONTRACT-002: the headings a project's cost is budgeted and read by."""

    MATERIALS = "materials", "Materials"
    SUBCONTRACT = "subcontract", "Subcontractors"
    LABOUR = "labour", "Labour"
    EQUIPMENT = "equipment", "Equipment"
    OTHER = "other", "Other"


class ProjectExpense(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="expenses")
    expense = models.OneToOneField("expenses.Expense", on_delete=models.PROTECT, related_name="project_link")
    heading = models.CharField(max_length=20, choices=CostHeading.choices, default=CostHeading.OTHER)
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


# ---- CONTRACT-002 (HG-038): bill of quantities, progress certificates,
# retention, advances, subcontractors and budget. None of these rows holds
# money or stock: a certificate and a subcontractor bill are ordinary draft
# invoices made through the sales and purchases engines, an advance is an
# ordinary customer payment; the rows here keep the contract arithmetic.


class BoqLine(models.Model):
    """One item of the bill of quantities (المقايسة): the work, how much, at what rate."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="boq")
    line_number = models.PositiveIntegerField()
    code = models.CharField(max_length=20, blank=True)
    description = models.CharField(max_length=255)
    unit = models.CharField(max_length=30, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["line_number"]
        constraints = [models.UniqueConstraint(fields=["project", "line_number"], name="projects_boq_line_number_unique")]

    def __str__(self):
        return f"{self.code or self.line_number} - {self.description}"


class Certificate(models.Model):
    """A progress certificate (مستخلص): the work done since the last one, priced
    from the bill of quantities or as a lump sum. Its invoice carries the gross
    work value; retention and the advance recovered are deducted on top of it."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="certificates")
    number = models.PositiveIntegerField()
    certificate_date = models.DateField()
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="project_certificate")
    gross = models.DecimalField(max_digits=14, decimal_places=2)
    retention_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    retention_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    recovery_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="projects_certificate_number_unique")]


class CertificateLine(models.Model):
    certificate = models.ForeignKey(Certificate, on_delete=models.CASCADE, related_name="lines")
    boq_line = models.ForeignKey(BoqLine, on_delete=models.PROTECT, related_name="certificate_lines")
    previous_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["boq_line__line_number"]


class ProjectPaymentKind(models.TextChoices):
    ADVANCE = "advance", "Advance"
    COLLECTION = "collection", "Collection"


class ProjectPayment(models.Model):
    """A customer payment received for this project: the owner's advance (paid
    back from each certificate) or a collection against the certificates."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="payments")
    payment = models.OneToOneField("sales.CustomerPayment", on_delete=models.PROTECT, related_name="project_payment")
    kind = models.CharField(max_length=20, choices=ProjectPaymentKind.choices, default=ProjectPaymentKind.COLLECTION)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


class RetentionRelease(models.Model):
    """Retention the owner may now pay (at handover, or part of it). No money moves here."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="retention_releases")
    release_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["release_date", "pk"]


class Subcontract(models.Model):
    """Work given to a subcontractor (a supplier) on this project."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="subcontracts")
    supplier = models.ForeignKey("master_data.Supplier", on_delete=models.PROTECT, related_name="subcontracts")
    scope = models.CharField(max_length=255)
    value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    retention_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


class ProjectPurchase(models.Model):
    """A purchase invoice of services (no stock lines) that is a cost of this
    project: a subcontractor's bill, equipment hire, a site service."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="purchases")
    invoice = models.OneToOneField("purchases.PurchaseInvoice", on_delete=models.PROTECT, related_name="project_purchase")
    heading = models.CharField(max_length=20, choices=CostHeading.choices, default=CostHeading.SUBCONTRACT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


class SubcontractBill(models.Model):
    """A subcontractor's progress bill: a draft purchase invoice, less the retention we hold."""

    subcontract = models.ForeignKey(Subcontract, on_delete=models.PROTECT, related_name="bills")
    number = models.PositiveIntegerField()
    bill_date = models.DateField()
    invoice = models.OneToOneField("purchases.PurchaseInvoice", on_delete=models.PROTECT, related_name="subcontract_bill")
    gross = models.DecimalField(max_digits=14, decimal_places=2)
    retention_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    retention_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    description = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["subcontract", "number"], name="projects_subcontract_bill_number_unique")]


class SubcontractRelease(models.Model):
    """Retention we now owe the subcontractor back. No money moves here."""

    subcontract = models.ForeignKey(Subcontract, on_delete=models.PROTECT, related_name="releases")
    release_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["release_date", "pk"]


class BudgetLine(models.Model):
    """The planned cost of one heading on one project."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="budget")
    heading = models.CharField(max_length=20, choices=CostHeading.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["project", "heading"], name="projects_budget_heading_unique")]
