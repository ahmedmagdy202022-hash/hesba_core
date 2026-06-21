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


REVIEWABLE_BATCH_STATUSES = {
    ImportBatchStatus.UPLOADED,
    ImportBatchStatus.REVIEWING,
}


def refresh_batch_counters(batch):
    """Recalculate denormalized counters from import rows."""

    batch.total_rows = batch.raw_rows.count()
    batch.valid_rows = batch.raw_rows.filter(row_status=ImportRowStatus.VALID).count()
    batch.invalid_rows = batch.raw_rows.filter(row_status=ImportRowStatus.INVALID).count()
    batch.imported_rows = batch.raw_rows.filter(row_status=ImportRowStatus.IMPORTED).count()
    batch.save(update_fields=["total_rows", "valid_rows", "invalid_rows", "imported_rows", "updated_at"])
    return batch


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
