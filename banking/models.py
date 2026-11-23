"""T3 (MARKET-PARITY): a bank account's statement, matched against its movements (HG-055, additive tables only).

A bank account is a cashbox of kind ``bank`` (cashboxes/models.py). Its
statement is uploaded as a file; each line is matched to the movement it is in
the books, or left open: money the bank took or gave that is not recorded yet
(a fee, interest), or a movement the bank has not cleared yet (a deposit in
transit, a cheque not cashed). Matching never changes an amount or a balance.
"""

from django.conf import settings
from django.db import models


class BankStatement(models.Model):
    cashbox = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, related_name="statements")
    name = models.CharField(max_length=160)
    #: The days the bank's file covers, as uploaded (lines it repeats from an earlier upload are not kept twice, but the
    #: statement still ends where the file ends: its closing balance is that day's).
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    #: The balance the bank printed at the end of the statement, when the file or the person gives it.
    closing_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at", "-pk"]

    def __str__(self):
        return self.name


class StatementLine(models.Model):
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    line_date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    #: Money into the account is positive, money out negative, as the bank shows it.
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    movement = models.OneToOneField("cashboxes.CashboxMovement", on_delete=models.PROTECT, null=True, blank=True, related_name="statement_line")
    matched_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    matched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["line_date", "pk"]
