from django.conf import settings
from django.db import models


class ExpenseCategory(models.Model):
    """What an expense was spent on: rent, salaries, utilities..."""

    code = models.CharField(max_length=40, unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name_ar"]
        verbose_name = "Expense category"
        verbose_name_plural = "Expense categories"

    def label(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar

    def __str__(self):
        return self.name_ar


class Expense(models.Model):
    """An operating expense paid out of a cashbox.

    The money side lives entirely in the linked direct-out CashboxOperation, so
    the cashbox ledger stays the one source of truth. The expense is in effect
    exactly while that operation is posted; cancelling the expense reverses the
    operation with its own append-only inverse movement.
    """

    expense_number = models.CharField(max_length=40, unique=True)
    expense_date = models.DateField()
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    cashbox = models.ForeignKey("cashboxes.Cashbox", on_delete=models.PROTECT, related_name="expenses")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payee = models.CharField(max_length=160, blank=True)
    description = models.CharField(max_length=255)
    cashbox_operation = models.OneToOneField(
        "cashboxes.CashboxOperation", on_delete=models.PROTECT, related_name="expense"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_expenses"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        indexes = [models.Index(fields=["expense_date"]), models.Index(fields=["category"])]

    @property
    def status(self):
        return self.cashbox_operation.status

    @property
    def is_posted(self):
        return self.cashbox_operation.status == "posted"

    def __str__(self):
        return self.expense_number
