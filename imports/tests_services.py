from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from hesba_testing.factories import make_user
from imports.models import (
    ImportBatch,
    ImportBatchStatus,
    ImportRaw,
    ImportReview,
    ImportReviewStatus,
    ImportRowStatus,
)
from imports.services import (
    add_raw_rows,
    approve_import_batch,
    create_import_batch,
    mark_raw_row_imported,
    mark_raw_row_validation,
    refresh_batch_counters,
    review_raw_row,
)


CATEGORY_ROW = {"category_code": "CAT-001", "name_ar": "تصنيف"}


def make_batch(batch_code="BATCH-001", target_type="categories", **kwargs):
    return create_import_batch(batch_code=batch_code, target_type=target_type, **kwargs)


def batch_with_rows(rows=None, **kwargs):
    batch = make_batch(**kwargs)
    created = add_raw_rows(batch.pk, rows or [CATEGORY_ROW])
    batch.refresh_from_db()
    return batch, created


def valid_batch(rows=None, **kwargs):
    """A batch whose rows are all marked valid, ready for approval."""
    batch, created = batch_with_rows(rows=rows, **kwargs)
    for row in created:
        mark_raw_row_validation(row.pk, True)
    batch.refresh_from_db()
    return batch, created


class CreateImportBatchTests(TestCase):
    def test_batch_starts_as_a_draft(self):
        batch = make_batch()

        self.assertEqual(ImportBatch.objects.count(), 1)
        self.assertEqual(batch.status, ImportBatchStatus.DRAFT)

    def test_batch_records_its_metadata(self):
        user = make_user()

        batch = make_batch(
            source_file_name="items.xlsx",
            go_live_date=date(2026, 1, 1),
            user=user,
            notes="first load",
        )

        self.assertEqual(batch.source_file_name, "items.xlsx")
        self.assertEqual(batch.go_live_date, date(2026, 1, 1))
        self.assertEqual(batch.created_by_id, user.pk)
        self.assertEqual(batch.notes, "first load")

    def test_counters_start_at_zero(self):
        batch = make_batch()

        self.assertEqual(batch.total_rows, 0)
        self.assertEqual(batch.valid_rows, 0)
        self.assertEqual(batch.invalid_rows, 0)
        self.assertEqual(batch.imported_rows, 0)


class AddRawRowsTests(TestCase):
    def test_rows_are_numbered_from_one(self):
        _, created = batch_with_rows(rows=[CATEGORY_ROW, {"category_code": "CAT-002"}])

        self.assertEqual([row.row_number for row in created], [1, 2])

    def test_a_second_call_continues_the_numbering(self):
        batch, _ = batch_with_rows()

        created = add_raw_rows(batch.pk, [{"category_code": "CAT-002"}])

        self.assertEqual([row.row_number for row in created], [2])
        self.assertEqual(ImportRaw.objects.count(), 2)

    def test_adding_rows_marks_the_batch_uploaded(self):
        batch, _ = batch_with_rows()

        self.assertEqual(batch.status, ImportBatchStatus.UPLOADED)

    def test_rows_start_pending_and_keep_their_raw_data(self):
        _, created = batch_with_rows()

        row = created[0]
        self.assertEqual(row.row_status, ImportRowStatus.PENDING)
        self.assertEqual(row.raw_data, CATEGORY_ROW)

    def test_counters_are_refreshed(self):
        batch, _ = batch_with_rows(rows=[CATEGORY_ROW, {"category_code": "CAT-002"}])

        self.assertEqual(batch.total_rows, 2)

    def test_adding_no_rows_still_marks_the_batch_uploaded(self):
        batch = make_batch()

        add_raw_rows(batch.pk, [])
        batch.refresh_from_db()

        self.assertEqual(batch.status, ImportBatchStatus.UPLOADED)
        self.assertEqual(batch.total_rows, 0)

    def test_adding_rows_to_an_approved_batch_is_rejected(self):
        batch, _ = valid_batch()
        approve_import_batch(batch.pk)

        with self.assertRaises(ValidationError):
            add_raw_rows(batch.pk, [{"category_code": "CAT-002"}])

    def test_adding_rows_to_a_cancelled_batch_is_rejected(self):
        batch, _ = batch_with_rows()
        ImportBatch.objects.filter(pk=batch.pk).update(
            status=ImportBatchStatus.CANCELLED
        )

        with self.assertRaises(ValidationError):
            add_raw_rows(batch.pk, [{"category_code": "CAT-002"}])


class MarkRawRowValidationTests(TestCase):
    def test_marking_a_row_valid_sets_its_status(self):
        _, created = batch_with_rows()

        row = mark_raw_row_validation(created[0].pk, True)

        self.assertEqual(row.row_status, ImportRowStatus.VALID)
        self.assertEqual(row.validation_errors, [])

    def test_marking_a_row_invalid_stores_the_errors(self):
        _, created = batch_with_rows()

        row = mark_raw_row_validation(created[0].pk, False, errors=["missing name"])

        self.assertEqual(row.row_status, ImportRowStatus.INVALID)
        self.assertEqual(row.validation_errors, ["missing name"])

    def test_validating_moves_the_batch_to_reviewing(self):
        batch, created = batch_with_rows()

        mark_raw_row_validation(created[0].pk, True)
        batch.refresh_from_db()

        self.assertEqual(batch.status, ImportBatchStatus.REVIEWING)

    def test_validating_refreshes_the_counters(self):
        batch, created = batch_with_rows(
            rows=[CATEGORY_ROW, {"category_code": "CAT-002"}]
        )

        mark_raw_row_validation(created[0].pk, True)
        mark_raw_row_validation(created[1].pk, False, errors=["bad"])
        batch.refresh_from_db()

        self.assertEqual(batch.valid_rows, 1)
        self.assertEqual(batch.invalid_rows, 1)
        self.assertEqual(batch.total_rows, 2)

    def test_revalidating_a_row_replaces_its_verdict(self):
        batch, created = batch_with_rows()
        mark_raw_row_validation(created[0].pk, False, errors=["bad"])

        row = mark_raw_row_validation(created[0].pk, True)
        batch.refresh_from_db()

        self.assertEqual(row.row_status, ImportRowStatus.VALID)
        self.assertEqual(batch.invalid_rows, 0)
        self.assertEqual(batch.valid_rows, 1)

    def test_validating_after_approval_is_rejected(self):
        batch, created = valid_batch()
        approve_import_batch(batch.pk)

        with self.assertRaises(ValidationError):
            mark_raw_row_validation(created[0].pk, True)

    def test_validating_after_cancellation_is_rejected(self):
        _, created = batch_with_rows()
        ImportBatch.objects.filter(pk=created[0].batch_id).update(
            status=ImportBatchStatus.CANCELLED
        )

        with self.assertRaises(ValidationError):
            mark_raw_row_validation(created[0].pk, True)


class ReviewRawRowTests(TestCase):
    def setUp(self):
        super().setUp()
        self.batch, self.rows = batch_with_rows()

    def test_a_review_is_recorded(self):
        review = review_raw_row(self.rows[0].pk, ImportReviewStatus.APPROVED)

        self.assertEqual(ImportReview.objects.count(), 1)
        self.assertEqual(review.review_status, ImportReviewStatus.APPROVED)
        self.assertEqual(review.raw_row_id, self.rows[0].pk)
        self.assertEqual(review.batch_id, self.batch.pk)
        self.assertIsNotNone(review.reviewed_at)

    def test_a_correction_stores_the_corrected_data(self):
        corrected = {"category_code": "CAT-001", "name_ar": "تصنيف مصحح"}

        review = review_raw_row(
            self.rows[0].pk,
            ImportReviewStatus.CORRECTED,
            corrected_data=corrected,
            notes="fixed the name",
        )

        self.assertEqual(review.corrected_data, corrected)
        self.assertEqual(review.notes, "fixed the name")

    def test_corrected_data_defaults_to_an_empty_dict(self):
        review = review_raw_row(self.rows[0].pk, ImportReviewStatus.REJECTED)

        self.assertEqual(review.corrected_data, {})

    def test_the_reviewer_is_recorded(self):
        user = make_user()

        review = review_raw_row(
            self.rows[0].pk, ImportReviewStatus.APPROVED, user=user
        )

        self.assertEqual(review.reviewed_by_id, user.pk)

    def test_an_unknown_review_status_is_rejected(self):
        with self.assertRaises(ValidationError):
            review_raw_row(self.rows[0].pk, "not-a-status")

    def test_every_valid_review_status_is_accepted(self):
        for status in ImportReviewStatus.values:
            with self.subTest(status=status):
                review = review_raw_row(self.rows[0].pk, status)

                self.assertEqual(review.review_status, status)

    def test_reviewing_an_approved_batch_is_still_allowed(self):
        """Approval blocks validation but not review."""
        mark_raw_row_validation(self.rows[0].pk, True)
        approve_import_batch(self.batch.pk)

        review = review_raw_row(self.rows[0].pk, ImportReviewStatus.APPROVED)

        self.assertEqual(review.review_status, ImportReviewStatus.APPROVED)

    def test_reviewing_after_cancellation_is_rejected(self):
        ImportBatch.objects.filter(pk=self.batch.pk).update(
            status=ImportBatchStatus.CANCELLED
        )

        with self.assertRaises(ValidationError):
            review_raw_row(self.rows[0].pk, ImportReviewStatus.APPROVED)

    def test_reviewing_an_imported_batch_is_rejected(self):
        ImportBatch.objects.filter(pk=self.batch.pk).update(
            status=ImportBatchStatus.IMPORTED
        )

        with self.assertRaises(ValidationError):
            review_raw_row(self.rows[0].pk, ImportReviewStatus.APPROVED)


class ApproveImportBatchTests(TestCase):
    def test_a_fully_valid_batch_is_approved(self):
        batch, _ = valid_batch()

        approved = approve_import_batch(batch.pk)

        self.assertEqual(approved.status, ImportBatchStatus.APPROVED)

    def test_an_uploaded_batch_can_be_approved(self):
        """Approval accepts UPLOADED as well as REVIEWING."""
        batch, created = batch_with_rows()
        ImportRaw.objects.filter(pk=created[0].pk).update(
            row_status=ImportRowStatus.VALID
        )
        ImportBatch.objects.filter(pk=batch.pk).update(
            status=ImportBatchStatus.UPLOADED
        )

        approved = approve_import_batch(batch.pk)

        self.assertEqual(approved.status, ImportBatchStatus.APPROVED)

    def test_a_draft_batch_cannot_be_approved(self):
        batch = make_batch()

        with self.assertRaises(ValidationError):
            approve_import_batch(batch.pk)

    def test_an_already_approved_batch_cannot_be_approved_again(self):
        batch, _ = valid_batch()
        approve_import_batch(batch.pk)

        with self.assertRaises(ValidationError):
            approve_import_batch(batch.pk)

    def test_an_empty_batch_cannot_be_approved(self):
        batch = make_batch()
        add_raw_rows(batch.pk, [])

        with self.assertRaises(ValidationError):
            approve_import_batch(batch.pk)

    def test_invalid_rows_block_approval(self):
        batch, created = batch_with_rows()
        mark_raw_row_validation(created[0].pk, False, errors=["bad"])

        with self.assertRaises(ValidationError):
            approve_import_batch(batch.pk)

    def test_pending_rows_block_approval(self):
        batch, created = batch_with_rows(
            rows=[CATEGORY_ROW, {"category_code": "CAT-002"}]
        )
        mark_raw_row_validation(created[0].pk, True)

        with self.assertRaises(ValidationError):
            approve_import_batch(batch.pk)

    def test_skipped_rows_block_approval(self):
        batch, created = valid_batch(
            rows=[CATEGORY_ROW, {"category_code": "CAT-002"}]
        )
        ImportRaw.objects.filter(pk=created[1].pk).update(
            row_status=ImportRowStatus.SKIPPED
        )

        with self.assertRaises(ValidationError):
            approve_import_batch(batch.pk)


class MarkRawRowImportedTests(TestCase):
    def setUp(self):
        super().setUp()
        self.batch, self.rows = valid_batch()
        approve_import_batch(self.batch.pk)

    def test_marking_a_row_imported_records_its_target(self):
        row = mark_raw_row_imported(self.rows[0].pk, "master_data.Category", 42)

        self.assertEqual(row.row_status, ImportRowStatus.IMPORTED)
        self.assertEqual(row.target_model, "master_data.Category")
        self.assertEqual(row.target_object_id, "42")

    def test_the_target_object_id_is_stored_as_text(self):
        row = mark_raw_row_imported(self.rows[0].pk, "master_data.Category", 7)

        self.assertIsInstance(row.target_object_id, str)

    def test_a_missing_target_model_is_rejected(self):
        with self.assertRaises(ValidationError):
            mark_raw_row_imported(self.rows[0].pk, "", 42)

    def test_a_missing_target_object_id_is_rejected(self):
        with self.assertRaises(ValidationError):
            mark_raw_row_imported(self.rows[0].pk, "master_data.Category", None)

    def test_importing_the_last_row_completes_the_batch(self):
        mark_raw_row_imported(self.rows[0].pk, "master_data.Category", 42)
        self.batch.refresh_from_db()

        self.assertEqual(self.batch.status, ImportBatchStatus.IMPORTED)
        self.assertEqual(self.batch.imported_rows, 1)

    def test_the_batch_stays_approved_until_every_row_is_imported(self):
        batch, rows = valid_batch(
            batch_code="BATCH-002",
            rows=[{"category_code": "CAT-A"}, {"category_code": "CAT-B"}],
        )
        approve_import_batch(batch.pk)

        mark_raw_row_imported(rows[0].pk, "master_data.Category", 1)
        batch.refresh_from_db()

        self.assertEqual(batch.status, ImportBatchStatus.APPROVED)
        self.assertEqual(batch.imported_rows, 1)

    def test_importing_from_an_unapproved_batch_is_rejected(self):
        batch, rows = valid_batch(batch_code="BATCH-003")

        with self.assertRaises(ValidationError):
            mark_raw_row_imported(rows[0].pk, "master_data.Category", 1)

    def test_importing_a_row_that_is_not_valid_is_rejected(self):
        ImportRaw.objects.filter(pk=self.rows[0].pk).update(
            row_status=ImportRowStatus.PENDING
        )

        with self.assertRaises(ValidationError):
            mark_raw_row_imported(self.rows[0].pk, "master_data.Category", 1)

    def test_importing_the_same_row_twice_is_rejected(self):
        mark_raw_row_imported(self.rows[0].pk, "master_data.Category", 42)

        with self.assertRaises(ValidationError):
            mark_raw_row_imported(self.rows[0].pk, "master_data.Category", 42)


class RefreshBatchCountersTests(TestCase):
    def test_counters_reflect_each_row_status(self):
        batch, created = batch_with_rows(
            rows=[{"category_code": f"CAT-{index}"} for index in range(4)]
        )
        ImportRaw.objects.filter(pk=created[0].pk).update(
            row_status=ImportRowStatus.VALID
        )
        ImportRaw.objects.filter(pk=created[1].pk).update(
            row_status=ImportRowStatus.INVALID
        )
        ImportRaw.objects.filter(pk=created[2].pk).update(
            row_status=ImportRowStatus.IMPORTED
        )

        refreshed = refresh_batch_counters(batch)

        self.assertEqual(refreshed.total_rows, 4)
        self.assertEqual(refreshed.valid_rows, 1)
        self.assertEqual(refreshed.invalid_rows, 1)
        self.assertEqual(refreshed.imported_rows, 1)

    def test_counters_are_persisted(self):
        batch, _ = batch_with_rows()

        refresh_batch_counters(batch)

        self.assertEqual(ImportBatch.objects.get(pk=batch.pk).total_rows, 1)

    def test_skipped_rows_count_only_toward_the_total(self):
        batch, created = batch_with_rows()
        ImportRaw.objects.filter(pk=created[0].pk).update(
            row_status=ImportRowStatus.SKIPPED
        )

        refreshed = refresh_batch_counters(batch)

        self.assertEqual(refreshed.total_rows, 1)
        self.assertEqual(refreshed.valid_rows, 0)
        self.assertEqual(refreshed.invalid_rows, 0)
        self.assertEqual(refreshed.imported_rows, 0)
