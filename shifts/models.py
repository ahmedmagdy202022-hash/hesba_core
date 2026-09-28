"""SHIFT-001: a cashier's shift at one cashbox, from counted float to counted drawer.

Expected cash is worked out, not typed: the float counted at the start plus
every cash movement that cashier recorded on that cashbox during the shift
(sales, refunds, collections, cash in/out). The difference is only recorded;
a manager may then post it to the cashbox through the ordinary direct cash
operation, so the books never change behind anyone's back (HG-024).
"""

from django.conf import settings
from django.db import models


class ShiftStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class Shift(models.Model):
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shifts")
    cashbox = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, related_name="shifts")
    status = models.CharField(max_length=10, choices=ShiftStatus.choices, default=ShiftStatus.OPEN)
    opened_at = models.DateTimeField()
    opening_float = models.DecimalField(max_digits=14, decimal_places=2)
    closed_at = models.DateTimeField(null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    counted_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    difference_operation = models.ForeignKey("cashboxes.CashboxOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-opened_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["cashier"], condition=models.Q(status="open"), name="shifts_one_open_per_cashier")]

    def __str__(self):
        return f"{self.cashier} @ {self.cashbox} {self.opened_at:%Y-%m-%d %H:%M}"
