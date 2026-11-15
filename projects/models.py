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
    # Contracting C1 (HG-103): the contract value as it stood before the first approved variation order, written once at that
    # approval and never again. NULL: no variation order was approved, so the contract value is still the original one.
    original_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
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
    # Contracting C1 (HG-103): the approved variation order that added this item. NULL: an item of the original bill of quantities.
    variation = models.ForeignKey("projects.VariationOrder", on_delete=models.PROTECT, null=True, blank=True, related_name="boq_lines")
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
    # The invoice line it was billed on, so a sales return on that line gives the quantity back.
    sales_line = models.OneToOneField("sales.SalesLine", on_delete=models.PROTECT, related_name="certificate_line")
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
    # Append-only: a release is never edited or deleted, only reversed on a date.
    reversed_on = models.DateField(null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
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
    # Contracting C11 (HG-128): the share of each bill's gross that pays back the advance paid to the subcontractor.
    advance_recovery_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
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
    # Contracting C11 (HG-128): the advance this bill pays back (deducted on top of its invoice, like retention).
    recovery_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    description = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["subcontract", "number"], name="projects_subcontract_bill_number_unique")]


# ---- Contracting C11 (HG-128): the subcontract's own bill of quantities, bills by quantities, and the advance paid to
# the subcontractor. None of these rows holds money: a bill is still an ordinary draft purchase invoice (one line per
# subcontract line) and a payment is an ordinary supplier payment; the rows keep the subcontract's arithmetic.


class SubcontractLine(models.Model):
    """One item of the work given to the subcontractor: how much, at what rate, and the client's item it covers (if any)."""

    subcontract = models.ForeignKey(Subcontract, on_delete=models.PROTECT, related_name="lines")
    line_number = models.PositiveIntegerField()
    description = models.CharField(max_length=255)
    unit = models.CharField(max_length=30, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    boq_line = models.ForeignKey(BoqLine, on_delete=models.PROTECT, null=True, blank=True, related_name="subcontract_lines")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["line_number"]
        constraints = [models.UniqueConstraint(fields=["subcontract", "line_number"], name="projects_subcontract_line_number_unique")]

    def __str__(self):
        return f"{self.line_number} - {self.description}"


class SubcontractBillLine(models.Model):
    bill = models.ForeignKey(SubcontractBill, on_delete=models.CASCADE, related_name="lines")
    subcontract_line = models.ForeignKey(SubcontractLine, on_delete=models.PROTECT, related_name="bill_lines")
    # The purchase line it was billed on, so a purchase return on that line gives the quantity back.
    purchase_line = models.OneToOneField("purchases.PurchaseLine", on_delete=models.PROTECT, related_name="subcontract_bill_line")
    previous_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["subcontract_line__line_number"]


class SubcontractPaymentKind(models.TextChoices):
    ADVANCE = "advance", "Advance"
    PAYMENT = "payment", "Payment"


class SubcontractPayment(models.Model):
    """A supplier payment made for this subcontract: the subcontractor's advance (paid back from each bill) or a payment
    against the bills."""

    subcontract = models.ForeignKey(Subcontract, on_delete=models.PROTECT, related_name="payments")
    payment = models.OneToOneField("purchases.SupplierPayment", on_delete=models.PROTECT, related_name="subcontract_payment")
    kind = models.CharField(max_length=20, choices=SubcontractPaymentKind.choices, default=SubcontractPaymentKind.PAYMENT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


class SubcontractRelease(models.Model):
    """Retention we now owe the subcontractor back. No money moves here."""

    subcontract = models.ForeignKey(Subcontract, on_delete=models.PROTECT, related_name="releases")
    release_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    # Append-only: a release is never edited or deleted, only reversed on a date.
    reversed_on = models.DateField(null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
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


class Weather(models.TextChoices):
    CLEAR = "clear", "Clear"
    HOT = "hot", "Hot"
    CLOUDY = "cloudy", "Cloudy"
    RAIN = "rain", "Rain"
    WIND = "wind", "Windy"
    DUST = "dust", "Dust"
    COLD = "cold", "Cold"


class SiteReport(models.Model):
    """Contracting C2b (HG-095): what happened on one site on one day. A record only: it changes no quantity of the bill
    of quantities, no cost and no certificate (the quantities executed are never taken from here). Photos are attachments
    on the report (``attachments.services.OWNERS["projects.sitereport"]``); the head count is the sum of the crew rows."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="site_reports")
    report_date = models.DateField()
    weather = models.CharField(max_length=10, choices=Weather.choices, blank=True)
    work_done = models.TextField(blank=True)
    delayed = models.TextField(blank=True)
    materials = models.TextField(blank=True)
    visitors = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-report_date", "-pk"]
        constraints = [models.UniqueConstraint(fields=["project", "report_date"], name="projects_site_report_day_unique")]

    def __str__(self):
        return f"{self.project.code} {self.report_date}"


class SiteReportRowKind(models.TextChoices):
    CREW = "crew", "Workers by trade"
    EQUIPMENT = "equipment", "Equipment"


class SiteReportRow(models.Model):
    """One free row of a day's report: a trade and its head count, or a piece of equipment and how many were on site."""

    report = models.ForeignKey(SiteReport, on_delete=models.CASCADE, related_name="rows")
    kind = models.CharField(max_length=10, choices=SiteReportRowKind.choices)
    label = models.CharField(max_length=80)
    count = models.PositiveSmallIntegerField(default=1)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["kind", "position", "pk"]


# ---- Contracting C1 (HG-103): variation orders and extra works. A variation order is a document: it holds no money and no
# stock. A draft changes nothing. Approving it writes into the bill of quantities (or, on a project without one, raises the
# contract value by an amount), and from then on certificates bill the new work through the unchanged certificate engine.


class VariationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class VariationOrder(models.Model):
    """A change to the contract the owner is asked to approve (أمر تغيير): more or less of an item of the bill of quantities,
    new items (extra works), and an optional extension of time. Once approved it is never edited or deleted: a mistake is
    corrected by another order."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="variation_orders")
    number = models.PositiveIntegerField()
    order_date = models.DateField()
    title = models.CharField(max_length=160)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=VariationStatus.choices, default=VariationStatus.DRAFT)
    extension_days = models.PositiveSmallIntegerField(default=0)
    # A project without a bill of quantities (billed as a lump sum): the order is one amount, added or taken off (−).
    lump_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    # What the order added to the contract value, fixed when it was approved (0 until then).
    value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # The approval, as staff record it: the date the owner approved and the owner's letter or reference. The signed copy is an
    # attachment on the order (``attachments.services.OWNERS["projects.variationorder"]``).
    approved_on = models.DateField(null=True, blank=True)
    owner_reference = models.CharField(max_length=80, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="projects_variation_number_unique")]

    def __str__(self):
        return f"{self.project.code} VO-{self.number:02d}"


class VariationLine(models.Model):
    """One line of a variation order: a change (+ or −) to the quantity of an existing item, or a new item with its own rate.
    The item's code, description and unit are copied onto the line, so the order reads the same whatever happens to the item."""

    order = models.ForeignKey(VariationOrder, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    # The existing item this line changes. NULL: a new item (the approval creates it, marked with ``BoqLine.variation``).
    boq_line = models.ForeignKey(BoqLine, on_delete=models.PROTECT, null=True, blank=True, related_name="variation_lines")
    code = models.CharField(max_length=20, blank=True)
    description = models.CharField(max_length=255)
    unit = models.CharField(max_length=30, blank=True)
    quantity_change = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    # What the line adds to the contract value; for a draft it is refreshed when the order is saved, and fixed at approval.
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["position", "pk"]


# ---- Contracting C3 (HG-124): the schedule. A task is a plan: dates, a predecessor and how much of the work it is. It holds no
# money and moves nothing; its progress is read from the posted certificates of the bill-of-quantities items linked to it, or
# typed on site when none is linked.


class ProjectTask(models.Model):
    """One piece of the work on the project's schedule (الجدول الزمني), with its planned start and end."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="tasks")
    sort = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=160)
    start_date = models.DateField()
    end_date = models.DateField()
    # Finish-to-start only: this task should start once its predecessor is done. A task that is someone's predecessor is not deleted.
    predecessor = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="successors")
    # How much of the project this task is, when it is linked to no bill-of-quantities item. NULL: its duration in days.
    weight = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    boq_lines = models.ManyToManyField(BoqLine, blank=True, related_name="tasks")
    # Percent done as typed on site; read only when no bill-of-quantities item is linked (otherwise the certificates say it).
    progress = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="projects_task_end_after_start"),
            models.CheckConstraint(condition=models.Q(progress__lte=100), name="projects_task_progress_max_100"),
            models.CheckConstraint(condition=models.Q(weight__isnull=True) | models.Q(weight__gt=0), name="projects_task_weight_positive"),
        ]

    def __str__(self):
        return f"{self.project.code} {self.name}"


# ---- Contracting C7 (HG-125): purchase requests from the site. A request is a document: it holds no money and moves no stock.
# The site engineer drafts and submits it; someone who buys (never the requester) approves it, and the approval makes an ordinary
# draft purchase order through ``orders.purchasing`` (unchanged). The link back is ``PurchaseRequest.purchase_order``;
# ``PurchaseOrder`` gains no column.


class PurchaseRequestStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Waiting for approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    ORDERED = "ordered", "Ordered"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseRequest(models.Model):
    """What the site needs bought (طلب شراء), before any supplier is chosen: shown as ``PR-<project code>-NN``.

    «Ordered» means a draft purchase order was made from it. When that order is cancelled the request reads as approved again
    (``projects.purchase_requests.state``): the order's own status is read, nothing is written back."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="purchase_requests")
    number = models.PositiveIntegerField()
    needed_by = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=PurchaseRequestStatus.choices, default=PurchaseRequestStatus.DRAFT, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    # The reason of a rejection, or what the approver changed (quantities, items) and their own note.
    decision_note = models.TextField(blank=True)
    # The latest purchase order made from this request. A cancelled order stays linked, so the request shows it, until a new one replaces it.
    purchase_order = models.ForeignKey("orders.PurchaseOrder", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="projects_purchase_request_number_unique")]

    def __str__(self):
        return f"PR-{self.project.code}-{self.number:02d}"


class PurchaseRequestLine(models.Model):
    """One thing the site asks for: an item of the catalogue (its unit is the item's) or a free description with its own unit."""

    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    # What the engineer asked for, kept when the approver changed the quantity (NULL: approved as asked, or not decided yet).
    requested_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    boq_line = models.ForeignKey(BoqLine, on_delete=models.PROTECT, null=True, blank=True, related_name="purchase_request_lines")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="projects_purchase_request_line_quantity_positive"),
            models.CheckConstraint(condition=models.Q(item__isnull=False) | ~models.Q(description=""), name="projects_purchase_request_line_item_or_description"),
        ]
