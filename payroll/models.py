"""T12 (MARKET-PARITY): payroll — the month's run, its accrual and its payments (HG-060).

A run is prepared from each employee's salary, with the absences the attendance sheet counted (T11), and can be
changed line by line while it is a draft. Posting it books the cost (gross + the company's insurance share) and
what is owed: the net pay to the employees, the insurance (both shares) and the income tax to the authorities. No
money moves then. Each payment is a cashbox operation of its own, dated when paid, against one of those debts.
A posted run is reversed by a dated reversal (append-only). The insurance and tax rules are data, not code.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

#: Egypt's income tax brackets on the yearly taxable salary, as (up to, rate %); the last has no top. Data to review.
DEFAULT_BRACKETS = "40000:0\n55000:10\n70000:15\n200000:20\n400000:22.5\n1200000:25\n:27.5"


class PayrollSettings(models.Model):
    """The one set of rules (row 1). The defaults are the published Egyptian figures; an accountant should check
    them every January, since they change by decree."""

    employee_insurance_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("11.00"))
    company_insurance_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("18.75"))
    insurance_min_wage = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("2300.00"))
    insurance_max_wage = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("14500.00"))
    yearly_exemption = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("20000.00"))
    #: One bracket per line, "up to:rate"; the last line has nothing before the colon.
    tax_brackets = models.TextField(default=DEFAULT_BRACKETS)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]


class SalaryProfile(models.Model):
    employee = models.OneToOneField("staff.Employee", on_delete=models.CASCADE, related_name="salary")
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    #: The wage social insurance is counted on; empty takes the basic salary (within the legal floor and ceiling).
    insured_wage = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    insured = models.BooleanField(default=True)
    taxed = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class RunStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    REVERSED = "reversed", "Reversed"


class PayrollRun(models.Model):
    month = models.DateField(help_text="The first day of the month paid for.")
    status = models.CharField(max_length=10, choices=RunStatus.choices, default=RunStatus.DRAFT)
    posted_on = models.DateField(null=True, blank=True)
    reversed_on = models.DateField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month", "-pk"]
        # Codex #269: one run a month that stands; a reversed one stays for the record and a corrected run replaces it.
        constraints = [models.UniqueConstraint(fields=["month"], condition=~models.Q(status="reversed"), name="payroll_one_standing_run_per_month")]


class PayrollLine(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, related_name="+")
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    absent_days = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal("0"))
    absence_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    other_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    employee_insurance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    company_insurance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    income_tax = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    net = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["employee__name", "pk"]
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="payroll_one_line_per_employee")]


class PayTo(models.TextChoices):
    NET = "net", "Net pay to the employees"
    INSURANCE = "insurance", "Social insurance"
    TAX = "tax", "Income tax"


class PayrollPayment(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="payments")
    pay_to = models.CharField(max_length=10, choices=PayTo.choices)
    operation = models.OneToOneField("cashboxes.CashboxOperation", on_delete=models.PROTECT, related_name="payroll_payment")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_on = models.DateField()
    cancelled_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["paid_on", "pk"]
