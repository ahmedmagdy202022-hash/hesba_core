from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ImportBatch,
    ImportBatchStatus,
    ImportRaw,
    ImportReview,
    ImportReviewStatus,
    ImportRowStatus,
)


@transaction.atomic
def create_import_batch(batch_code, target_type, source_file_name="", go_live_date=None, user=None, notes=""):
    batch = ImportBatch.objects.create(
        batch_code=batch_code,
        target_type=target_type,
        source_file_name=source_file_name,
        go_live_date=go_live_date,
        notes=notes,
        created_by=user,
    )
    batch.full_clean()
    return batch


@transaction.atomic
def add_raw_rows(batch_id, rows):
    """Add unchanged raw rows to an import batch."""

    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status not in {ImportBatchStatus.DRAFT, ImportBatchStatus.UPLOADED}:
        raise ValidationError("Raw rows can only be added to draft or uploaded batches.")

    created_rows = []
    next_row_number = batch.raw_rows.count() + 1
    for offset, raw_data in enumerate(rows):
        created_rows.append(
            ImportRaw.objects.create(
                batch=batch,
                row_number=next_row_number + offset,
                raw_data=raw_data,
            )
        )

    batch.total_rows = batch.raw_rows.count()
    batch.status = ImportBatchStatus.UPLOADED
    batch.save(update_fields=["total_rows", "status", "updated_at"])
    return created_rows


@transaction.atomic
def mark_raw_row_validation(raw_row_id, is_valid, errors=None):
    raw_row = ImportRaw.objects.select_for_update().select_related("batch").get(pk=raw_row_id)
    if raw_row.batch.status in {ImportBatchStatus.APPROVED, ImportBatchStatus.IMPORTED, ImportBatchStatus.CANCELLED}:
        raise ValidationError("Cannot validate rows after batch approval or completion.")

    raw_row.row_status = ImportRowStatus.VALID if is_valid else ImportRowStatus.INVALID
    raw_row.validation_errors = errors or []
    raw_row.save(update_fields=["row_status", "validation_errors"])

    batch = raw_row.batch
    batch.valid_rows = batch.raw_rows.filter(row_status=ImportRowStatus.VALID).count()
    batch.invalid_rows = batch.raw_rows.filter(row_status=ImportRowStatus.INVALID).count()
    batch.status = ImportBatchStatus.REVIEWING
    batch.save(update_fields=["valid_rows", "invalid_rows", "status", "updated_at"])
    return raw_row


@transaction.atomic
def review_raw_row(raw_row_id, review_status, user=None, corrected_data=None, notes=""):
    raw_row = ImportRaw.objects.select_for_update().select_related("batch").get(pk=raw_row_id)
    if review_status not in ImportReviewStatus.values:
        raise ValidationError("Invalid review status.")
    if raw_row.batch.status in {ImportBatchStatus.IMPORTED, ImportBatchStatus.CANCELLED}:
        raise ValidationError("Cannot review rows after import completion or cancellation.")

    return ImportReview.objects.create(
        batch=raw_row.batch,
        raw_row=raw_row,
        review_status=review_status,
        corrected_data=corrected_data or {},
        notes=notes,
        reviewed_by=user,
        reviewed_at=timezone.now(),
    )


@transaction.atomic
def approve_import_batch(batch_id):
    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.invalid_rows > 0:
        raise ValidationError("Cannot approve import batch while invalid rows exist.")
    if batch.total_rows == 0:
        raise ValidationError("Cannot approve empty import batch.")
    if batch.valid_rows != batch.total_rows:
        raise ValidationError("All rows must be valid before approval.")

    batch.status = ImportBatchStatus.APPROVED
    batch.save(update_fields=["status", "updated_at"])
    return batch


@transaction.atomic
def mark_raw_row_imported(raw_row_id, target_model, target_object_id):
    raw_row = ImportRaw.objects.select_for_update().select_related("batch").get(pk=raw_row_id)
    if raw_row.batch.status != ImportBatchStatus.APPROVED:
        raise ValidationError("Rows can only be imported from approved batches.")
    if raw_row.row_status != ImportRowStatus.VALID:
        raise ValidationError("Only valid rows can be marked as imported.")

    raw_row.row_status = ImportRowStatus.IMPORTED
    raw_row.target_model = target_model
    raw_row.target_object_id = str(target_object_id)
    raw_row.save(update_fields=["row_status", "target_model", "target_object_id"])

    batch = raw_row.batch
    batch.imported_rows = batch.raw_rows.filter(row_status=ImportRowStatus.IMPORTED).count()
    if batch.imported_rows == batch.total_rows:
        batch.status = ImportBatchStatus.IMPORTED
    batch.save(update_fields=["imported_rows", "status", "updated_at"])
    return raw_row
