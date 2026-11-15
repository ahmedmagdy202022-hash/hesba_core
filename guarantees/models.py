"""Contracting C4 (HG-123): letters of guarantee and their renewal dates.

A letter of guarantee is a bank's promise to pay the beneficiary (the project owner) up to an amount until a date. No
money moves while it lives, so nothing here touches a cashbox, the journal or a customer's account:

* the cash margin (``margin_percent``) and the bank's ``commission`` are memo figures only; the commission is an
  ordinary expense the user records, and booking the margin is a T3 follow-up (owner question 11);
* the history of a guarantee is its append-only ``GuaranteeEvent`` rows (extend, reduce, release, liquidate, cancel).
  The guarantee row itself holds the current amount and expiry, which the alerts read;
* a liquidated guarantee only records the event: the money the owner takes is an ordinary bank cash-out the user
  records on the bank's own screen.

A guarantee belongs to the entity of its bank account (HG-034); another entity's guarantee answers 404.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class GuaranteeKind(models.TextChoices):
    BID = "bid", "Bid bond"
    PERFORMANCE = "performance", "Performance bond"
    ADVANCE = "advance", "Advance payment guarantee"
    RETENTION = "retention", "Retention guarantee"
    OTHER = "other", "Other"


class GuaranteeStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    RELEASED = "released", "Released"
    LIQUIDATED = "liquidated", "Liquidated"
    CANCELLED = "cancelled", "Cancelled"


class Guarantee(models.Model):
    kind = models.CharField(max_length=12, choices=GuaranteeKind.choices, db_index=True)
    #: The bank's own reference for the letter.
    number = models.CharField(max_length=60)
    #: The bank account (a cashbox of kind ``bank``) that issued it. Never moved by anything here.
    bank = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, related_name="+")
    #: Who may call it: the project owner, a customer.
    beneficiary = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="+")
    #: A bid bond exists before its project does, and one guarantee may outlive several projects, so it is optional.
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    #: The current amount (after the latest reduction); the issued amount is the first event's ``previous_amount`` or this.
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=10, default="EGP")
    issued_on = models.DateField()
    #: The current expiry (after the latest extension).
    expires_on = models.DateField(db_index=True)
    #: Memo only (HG-123): the cash margin the bank holds, as a percent of the amount. Not booked.
    margin_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    #: Memo only: the bank's commission. Recorded as an ordinary expense by the user, never from here.
    commission = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, choices=GuaranteeStatus.choices, default=GuaranteeStatus.ACTIVE, db_index=True)
    notes = models.TextField(blank=True)
    #: HG-034: the entity of its bank account (NULL reads as the main entity).
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["expires_on", "pk"]
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name="guarantee_amount_positive"),
            models.CheckConstraint(check=models.Q(expires_on__gte=models.F("issued_on")), name="guarantee_expires_after_issue"),
        ]

    def __str__(self):
        return self.number


class EventKind(models.TextChoices):
    EXTEND = "extend", "Extended"
    REDUCE = "reduce", "Reduced"
    RELEASE = "release", "Released"
    LIQUIDATE = "liquidate", "Liquidated"
    CANCEL = "cancel", "Cancelled"


class GuaranteeEvent(models.Model):
    """One change to a guarantee. Append-only: a row is never edited or deleted, so the history of extensions and
    reductions stays as it happened."""

    guarantee = models.ForeignKey(Guarantee, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=10, choices=EventKind.choices)
    event_date = models.DateField()
    previous_expiry = models.DateField()
    new_expiry = models.DateField(null=True, blank=True)
    previous_amount = models.DecimalField(max_digits=14, decimal_places=2)
    new_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [models.CheckConstraint(check=models.Q(new_amount__isnull=True) | models.Q(new_amount__gt=0), name="guarantee_event_amount_positive")]

    def __str__(self):
        return f"{self.guarantee_id}:{self.kind}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("A guarantee event is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A guarantee event is append-only.")
