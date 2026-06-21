from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ImportTargetType(models.TextChoices):
    ITEMS = "items", "Items"
    CATEGORIES = "categories", "Categories"
    LOCATIONS = "locations", "Locations"
    STOCK = "stock", "Stock"
    CUSTOMERS = "customers", "Customers"
    SUPPLIERS = "suppliers", "Suppliers"
    CASHBOXES = "cashboxes", "Cashboxes"
    USERS = "users", "Users"
    OPENING_BALANCES = "opening_balances", "Opening balances"


class ImportBatchStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    UPLOADED = "uploaded", "Uploaded"
    REVIEWING = "reviewing", "Reviewing"
    APPROVED = "approved", "Approved"
    IMPORTED = "imported", "Imported"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class ImportRowStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VALID = "valid", "Valid"
    INVALID = "invalid", "Invalid"
    IMPORTED = "imported", "Imported"
    SKIPPED = "skipped", "Skipped"


class ImportReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CORRECTED = "corrected", "Corrected"


class ImportBatch(models.Model):
    """Import batch header."""

    batch_code = models.CharField(max_length=80, unique=True)
    target_type = models.CharField(max_length=40, choices=ImportTargetType.choices)
    status = models.CharField(max_length=20, choices=ImportBatchStatus.choices, default=ImportBatchStatus.DRAFT)
    source_file_name = models.CharField(max_length=255, blank=True)
    go_live_date = models.DateField(null=True, blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    invalid_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_import_batches",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["batch_code"]),
            models.Index(fields=["target_type", "status"]),
            models.Index(fields=["go_live_date"]),
        ]
        verbose_name = "Import Batch"
        verbose_name_plural = "Import Batches"

    def clean(self):
        if self.status == ImportBatchStatus.IMPORTED and self.imported_rows != self.total_rows:
            raise ValidationError({"imported_rows": "Imported rows must equal total rows when batch is fully imported."})

    def __str__(self):
        return f"{self.batch_code} - {self.target_type}"


class ImportRaw(models.Model):
    """Source import row before approval."""

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="raw_rows")
    row_number = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict)
    row_status = models.CharField(max_length=20, choices=ImportRowStatus.choices, default=ImportRowStatus.PENDING)
    validation_errors = models.JSONField(default=list, blank=True)
    target_model = models.CharField(max_length=120, blank=True)
    target_object_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["batch", "row_number"]
        constraints = [models.UniqueConstraint(fields=["batch", "row_number"], name="unique_import_raw_row")]
        indexes = [
            models.Index(fields=["batch", "row_status"]),
            models.Index(fields=["target_model", "target_object_id"]),
        ]
        verbose_name = "Import Raw Row"
        verbose_name_plural = "Import Raw Rows"

    def __str__(self):
        return f"{self.batch.batch_code} / row {self.row_number}"


class ImportReview(models.Model):
    """Review/correction row for imported source data."""

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="review_rows")
    raw_row = models.ForeignKey(ImportRaw, on_delete=models.CASCADE, related_name="reviews", null=True, blank=True)
    review_status = models.CharField(max_length=20, choices=ImportReviewStatus.choices, default=ImportReviewStatus.PENDING)
    corrected_data = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_import_rows",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["batch", "raw_row_id", "id"]
        indexes = [
            models.Index(fields=["batch", "review_status"]),
            models.Index(fields=["raw_row"]),
        ]
        verbose_name = "Import Review Row"
        verbose_name_plural = "Import Review Rows"

    def clean(self):
        if self.raw_row_id and self.batch_id and self.raw_row.batch_id != self.batch_id:
            raise ValidationError({"raw_row": "Review row must belong to the same batch as the raw row."})

    def __str__(self):
        return f"{self.batch.batch_code} / {self.review_status}"
