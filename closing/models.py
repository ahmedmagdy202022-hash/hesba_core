from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class PeriodStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    REOPENED = "reopened", "Reopened"


class ClosingFrequency(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    SEMI_ANNUAL = "semi_annual", "Semi annual"
    ANNUAL = "annual", "Annual"


class ClosingRunStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class PostClosingAdjustmentStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


class Period(models.Model):
    """Accounting and stock review period.

    Closed periods are read-only by default. Any correction for a closed period
    should normally be recorded in the current open period through a controlled
    post-closing adjustment.
    """

    period_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    frequency = models.CharField(
        max_length=20,
        choices=ClosingFrequency.choices,
        default=ClosingFrequency.QUARTERLY,
    )
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=PeriodStatus.choices, default=PeriodStatus.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="closed_periods",
        null=True,
        blank=True,
    )
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reopened_periods",
        null=True,
        blank=True,
    )
    reopen_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        indexes = [
            models.Index(fields=["start_date", "end_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["frequency"]),
        ]
        verbose_name = "Period"
        verbose_name_plural = "Periods"

    @property
    def is_closed(self):
        return self.status == PeriodStatus.CLOSED

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before start date."})
        if self.status == PeriodStatus.CLOSED and not self.closed_at:
            raise ValidationError({"closed_at": "Closed periods must have closed at date."})
        if self.status == PeriodStatus.REOPENED and not self.reopen_reason:
            raise ValidationError({"reopen_reason": "Reopened periods must have a reason."})

    def __str__(self):
        return f"{self.period_code} - {self.name}"


class ClosingRun(models.Model):
    """Closing execution record.

    Closing runs summarize the period and support auditability. Future closing
    logic will write summaries and mark the period as closed.
    """

    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="closing_runs")
    run_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=ClosingRunStatus.choices, default=ClosingRunStatus.DRAFT)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_closing_runs",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-started_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["period", "run_number"], name="unique_closing_run_number")]
        indexes = [models.Index(fields=["period", "status"])]
        verbose_name = "Closing Run"
        verbose_name_plural = "Closing Runs"

    def __str__(self):
        return f"{self.period} / run {self.run_number}"


class PeriodSummary(models.Model):
    """Read-only saved summary produced by a closing run."""

    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="summaries")
    closing_run = models.ForeignKey(ClosingRun, on_delete=models.PROTECT, related_name="summaries")
    summary_code = models.CharField(max_length=80)
    summary_name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    quantity = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["period", "summary_code"]
        constraints = [models.UniqueConstraint(fields=["period", "summary_code"], name="unique_period_summary_code")]
        indexes = [models.Index(fields=["period", "summary_code"])]
        verbose_name = "Period Summary"
        verbose_name_plural = "Period Summaries"

    def __str__(self):
        return f"{self.period} / {self.summary_code}"


class PostClosingAdjustment(models.Model):
    """Controlled correction related to a closed period.

    The adjustment itself is posted in the current open period, while this record
    keeps the link to the closed period and the reason for audit review.
    """

    adjustment_number = models.CharField(max_length=80, unique=True)
    related_closed_period = models.ForeignKey(
        Period,
        on_delete=models.PROTECT,
        related_name="post_closing_adjustments",
    )
    adjustment_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=PostClosingAdjustmentStatus.choices,
        default=PostClosingAdjustmentStatus.DRAFT,
    )
    reason = models.TextField()
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_post_closing_adjustments",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-adjustment_date", "-id"]
        indexes = [
            models.Index(fields=["related_closed_period"]),
            models.Index(fields=["status"]),
            models.Index(fields=["adjustment_date"]),
        ]
        verbose_name = "Post Closing Adjustment"
        verbose_name_plural = "Post Closing Adjustments"

    def clean(self):
        if self.related_closed_period and self.related_closed_period.status != PeriodStatus.CLOSED:
            raise ValidationError({"related_closed_period": "Post-closing adjustments must reference a closed period."})
        if not self.reason:
            raise ValidationError({"reason": "Reason is required."})

    def __str__(self):
        return self.adjustment_number
