"""GL-001: the chart of accounts.

Accounts form a tree by code (1 → 11 → 1101). Only leaf accounts take entries.
A *control* key marks the account a kind of document posts to (cash,
receivables, sales, VAT…), so the journal projector (GL-002) never guesses by
name. System accounts carry a control key and cannot be deleted or retyped;
the owner may rename them and add sub-accounts freely.
"""

from django.db import models


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    INCOME = "income", "Income"
    EXPENSE = "expense", "Expense"


#: Which side increases an account of each type.
DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE}


class Account(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    control = models.CharField(max_length=40, blank=True, help_text="What posts here automatically: cash, receivable, sales…")
    is_postable = models.BooleanField(default=True)
    is_contra = models.BooleanField(default=False, help_text="Reduces its group: accumulated depreciation, sales returns…")
    is_system = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["control"], condition=~models.Q(control=""), name="one_account_per_control")]

    def __str__(self):
        return f"{self.code} - {self.name_ar}"

    @property
    def debit_normal(self):
        return (self.account_type in DEBIT_NORMAL) != self.is_contra

    @property
    def depth(self):
        return max(len(self.code) // 2 - 1, 0) if len(self.code) > 1 else 0

    def display(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar


class ExpenseAccount(models.Model):
    """Which account an expense category posts to (GL-002 reads it)."""

    category = models.OneToOneField("expenses.ExpenseCategory", on_delete=models.CASCADE, related_name="ledger_link")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expense_categories")
