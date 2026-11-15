"""Contracting C8 (HG-126): the tender study and its price analysis.

A tender is priced before there is a contract, and it may be lost, so it is not a project:

* ``Tender`` holds who it is for (a customer, or only a prospect's name while there is none), the due date, the status
  and the two markups (overhead and profit, percents);
* ``TenderItem`` is one item of the bill of quantities the owner sent: code, description, unit, quantity;
* ``TenderCost`` is one line of an item's price analysis: a cost heading (the project ``CostHeading`` choices) and its
  cost **per unit of the item**.

Nothing here is money: no cashbox, journal, ledger or stock row is ever written. Winning («اترسّت علينا») creates the
project, its bill of quantities and its budget through the unchanged ``projects`` services; from then on the tender is
read-only, and the project carries the contract.

A tender belongs to the entity it was made in (HG-034; NULL is the main entity); another entity's tender answers 404.
"""

from django.conf import settings
from django.db import models

from projects.models import CostHeading


class TenderStatus(models.TextChoices):
    STUDYING = "studying", "Under study"
    SUBMITTED = "submitted", "Submitted"
    WON = "won", "Won"
    LOST = "lost", "Lost"
    WITHDRAWN = "withdrawn", "Withdrawn"


#: The statuses in which a tender is still being priced; every other status is read-only.
OPEN_STATUSES = (TenderStatus.STUDYING, TenderStatus.SUBMITTED)


class Tender(models.Model):
    name = models.CharField(max_length=255)
    #: The owner who invited the tender, once they are a customer; a prospect only has a name until then.
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    prospect_name = models.CharField(max_length=255, blank=True)
    due_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=TenderStatus.choices, default=TenderStatus.STUDYING, db_index=True)
    overhead_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    profit_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    #: Set once, when the tender is won: the project made from it.
    project = models.OneToOneField("projects.Project", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    #: Why it was lost or withdrawn (required for those two), and the day its status last changed.
    close_reason = models.CharField(max_length=255, blank=True)
    status_on = models.DateField(null=True, blank=True)
    #: HG-034: the entity it was made in (NULL reads as the main entity).
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-pk"]
        constraints = [
            models.CheckConstraint(check=models.Q(overhead_percent__gte=0, overhead_percent__lte=100), name="tender_overhead_percent_range"),
            models.CheckConstraint(check=models.Q(profit_percent__gte=0, profit_percent__lte=100), name="tender_profit_percent_range"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES


class TenderItem(models.Model):
    """One item of the owner's bill of quantities. The sizes are ``projects.BoqLine``'s, so winning copies it unchanged."""

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="items")
    line_number = models.PositiveIntegerField()
    code = models.CharField(max_length=20, blank=True)
    description = models.CharField(max_length=255)
    unit = models.CharField(max_length=30, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta:
        ordering = ["line_number", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["tender", "line_number"], name="tender_item_line_unique"),
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="tender_item_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.tender_id}:{self.line_number}"


class TenderCost(models.Model):
    """One line of an item's price analysis: what one unit of the item costs under one heading."""

    item = models.ForeignKey(TenderItem, on_delete=models.CASCADE, related_name="costs")
    heading = models.CharField(max_length=20, choices=CostHeading.choices)
    description = models.CharField(max_length=255, blank=True)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["pk"]
        constraints = [models.CheckConstraint(check=models.Q(unit_cost__gte=0), name="tender_cost_unit_cost_not_negative")]

    def __str__(self):
        return f"{self.item_id}:{self.heading}"
