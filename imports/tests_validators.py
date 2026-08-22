from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from hesba_testing.factories import (
    make_cashbox,
    make_customer,
    make_item,
    make_location,
    make_role,
    make_supplier,
)
from imports.models import ImportBatch, ImportBatchStatus, ImportRowStatus
from imports.services import add_raw_rows, approve_import_batch
from imports.tests_services import batch_with_rows, make_batch, valid_batch
from imports.validators import (
    VALIDATORS,
    _as_error_list,
    _nonnegative_decimal,
    _positive_decimal,
    validate_cashbox_data,
    validate_category_data,
    validate_customer_data,
    validate_import_batch,
    validate_item_data,
    validate_location_data,
    validate_opening_balance_data,
    validate_raw_import_row,
    validate_stock_data,
    validate_supplier_data,
    validate_user_data,
)
from master_data.models import Category


class ErrorListTests(TestCase):
    def test_a_field_error_dict_is_flattened_with_field_names(self):
        error = ValidationError({"name": ["This field is required."]})

        self.assertEqual(_as_error_list(error), ["name: This field is required."])

    def test_multiple_fields_each_produce_an_entry(self):
        error = ValidationError({"a": ["first"], "b": ["second"]})

        self.assertEqual(sorted(_as_error_list(error)), ["a: first", "b: second"])

    def test_a_plain_message_is_returned_as_a_single_entry(self):
        error = ValidationError("something went wrong")

        self.assertEqual(_as_error_list(error), ["something went wrong"])

    def test_a_non_validation_error_falls_back_to_its_text(self):
        self.assertEqual(_as_error_list(ValueError("boom")), ["boom"])


class NumericGuardTests(TestCase):
    def test_nonnegative_accepts_zero(self):
        self.assertEqual(_nonnegative_decimal({"a": "0"}, "a"), Decimal("0"))

    def test_nonnegative_accepts_a_positive_value(self):
        self.assertEqual(_nonnegative_decimal({"a": "5"}, "a"), Decimal("5"))

    def test_nonnegative_rejects_a_negative_value(self):
        with self.assertRaises(ValidationError):
            _nonnegative_decimal({"a": "-1"}, "a")

    def test_positive_rejects_zero(self):
        with self.assertRaises(ValidationError):
            _positive_decimal({"a": "0"}, "a")

    def test_positive_rejects_a_negative_value(self):
        with self.assertRaises(ValidationError):
            _positive_decimal({"a": "-2"}, "a")

    def test_positive_accepts_a_positive_value(self):
        self.assertEqual(_positive_decimal({"a": "2.5"}, "a"), Decimal("2.5"))


class RowValidatorTests(TestCase):
    def setUp(self):
        super().setUp()
        self.batch = make_batch(batch_code="BATCH-VALIDATE")

    def test_every_supported_target_type_has_a_validator(self):
        from imports.apply_services import SUPPORTED_TARGET_TYPES

        self.assertEqual(set(VALIDATORS), SUPPORTED_TARGET_TYPES)

    def test_a_complete_category_row_passes(self):
        self.assertIsNone(
            validate_category_data(
                {"category_code": "CAT-1", "name_ar": "تصنيف"}, self.batch
            )
        )

    def test_a_category_cannot_be_its_own_parent(self):
        with self.assertRaises(ValidationError):
            validate_category_data(
                {"category_code": "CAT-1", "name_ar": "تصنيف", "parent_code": "CAT-1"},
                self.batch,
            )

    def test_a_category_parent_must_exist(self):
        with self.assertRaises(Category.DoesNotExist):
            validate_category_data(
                {"category_code": "CAT-1", "name_ar": "تصنيف", "parent_code": "NOPE"},
                self.batch,
            )

    def test_an_existing_category_parent_passes(self):
        Category.objects.create(category_code="CAT-PARENT", name_ar="أب")

        self.assertIsNone(
            validate_category_data(
                {
                    "category_code": "CAT-1",
                    "name_ar": "تصنيف",
                    "parent_code": "CAT-PARENT",
                },
                self.batch,
            )
        )

    def test_a_category_needs_a_name(self):
        with self.assertRaises(ValidationError):
            validate_category_data({"category_code": "CAT-1"}, self.batch)

    def test_a_category_rejects_a_bad_active_flag(self):
        with self.assertRaises(ValidationError):
            validate_category_data(
                {"category_code": "CAT-1", "name_ar": "تصنيف", "active": "maybe"},
                self.batch,
            )

    def test_a_complete_location_row_passes(self):
        self.assertIsNone(
            validate_location_data(
                {"location_code": "LOC-1", "name_ar": "مخزن"}, self.batch
            )
        )

    def test_a_location_needs_a_code(self):
        with self.assertRaises(ValidationError):
            validate_location_data({"name_ar": "مخزن"}, self.batch)

    def test_a_location_rejects_a_bad_flag(self):
        with self.assertRaises(ValidationError):
            validate_location_data(
                {"location_code": "LOC-1", "name_ar": "مخزن", "is_default": "sometimes"},
                self.batch,
            )

    def test_a_complete_item_row_passes(self):
        self.assertIsNone(
            validate_item_data(
                {"item_code": "ITEM-1", "item_name": "Widget"}, self.batch
            )
        )

    def test_an_item_category_must_exist(self):
        with self.assertRaises(Category.DoesNotExist):
            validate_item_data(
                {"item_code": "ITEM-1", "item_name": "W", "category_code": "NOPE"},
                self.batch,
            )

    def test_an_item_rejects_a_negative_sale_price(self):
        with self.assertRaises(ValidationError):
            validate_item_data(
                {"item_code": "ITEM-1", "item_name": "W", "sale_price": "-1"},
                self.batch,
            )

    def test_an_item_rejects_a_negative_average_cost(self):
        with self.assertRaises(ValidationError):
            validate_item_data(
                {"item_code": "ITEM-1", "item_name": "W", "average_cost": "-0.01"},
                self.batch,
            )

    def test_an_item_rejects_a_negative_min_stock(self):
        with self.assertRaises(ValidationError):
            validate_item_data(
                {"item_code": "ITEM-1", "item_name": "W", "min_stock": "-5"},
                self.batch,
            )

    def test_an_item_rejects_a_non_numeric_price(self):
        with self.assertRaises(ValidationError):
            validate_item_data(
                {"item_code": "ITEM-1", "item_name": "W", "sale_price": "free"},
                self.batch,
            )

    def test_a_complete_customer_row_passes(self):
        self.assertIsNone(
            validate_customer_data(
                {"customer_code": "CUST-1", "name": "Acme"}, self.batch
            )
        )

    def test_a_customer_rejects_a_negative_credit_limit(self):
        with self.assertRaises(ValidationError):
            validate_customer_data(
                {"customer_code": "CUST-1", "name": "Acme", "credit_limit": "-10"},
                self.batch,
            )

    def test_a_customer_accepts_a_negative_opening_balance(self):
        """Opening balance may be negative; only the credit limit may not."""
        self.assertIsNone(
            validate_customer_data(
                {"customer_code": "CUST-1", "name": "Acme", "opening_balance": "-10"},
                self.batch,
            )
        )

    def test_a_customer_needs_a_name(self):
        with self.assertRaises(ValidationError):
            validate_customer_data({"customer_code": "CUST-1"}, self.batch)

    def test_a_complete_supplier_row_passes(self):
        self.assertIsNone(
            validate_supplier_data(
                {"supplier_code": "SUP-1", "name": "Vendor"}, self.batch
            )
        )

    def test_a_supplier_accepts_a_negative_opening_balance(self):
        self.assertIsNone(
            validate_supplier_data(
                {"supplier_code": "SUP-1", "name": "V", "opening_balance": "-5"},
                self.batch,
            )
        )

    def test_a_supplier_needs_a_code(self):
        with self.assertRaises(ValidationError):
            validate_supplier_data({"name": "Vendor"}, self.batch)

    def test_a_complete_cashbox_row_passes(self):
        self.assertIsNone(
            validate_cashbox_data(
                {"cashbox_code": "CASH-1", "name_ar": "خزنة"}, self.batch
            )
        )

    def test_a_cashbox_needs_a_name(self):
        with self.assertRaises(ValidationError):
            validate_cashbox_data({"cashbox_code": "CASH-1"}, self.batch)

    def test_a_cashbox_rejects_a_bad_default_flag(self):
        with self.assertRaises(ValidationError):
            validate_cashbox_data(
                {"cashbox_code": "CASH-1", "name_ar": "خزنة", "is_default": "kind of"},
                self.batch,
            )


class StockValidatorTests(TestCase):
    def setUp(self):
        super().setUp()
        self.batch = make_batch(
            batch_code="BATCH-STOCK", target_type="stock", go_live_date=date(2026, 1, 1)
        )
        self.item = make_item(item_code="ITEM-1")
        self.location = make_location(location_code="LOC-1")

    def row(self, **overrides):
        data = {"item_code": "ITEM-1", "location_code": "LOC-1", "quantity": "5"}
        data.update(overrides)
        return data

    def test_a_complete_stock_row_passes(self):
        self.assertIsNone(validate_stock_data(self.row(), self.batch))

    def test_an_unknown_item_is_rejected(self):
        from master_data.models import Item

        with self.assertRaises(Item.DoesNotExist):
            validate_stock_data(self.row(item_code="NOPE"), self.batch)

    def test_an_unknown_location_is_rejected(self):
        from master_data.models import Location

        with self.assertRaises(Location.DoesNotExist):
            validate_stock_data(self.row(location_code="NOPE"), self.batch)

    def test_a_zero_quantity_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_stock_data(self.row(quantity="0"), self.batch)

    def test_a_negative_quantity_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_stock_data(self.row(quantity="-3"), self.batch)

    def test_a_negative_unit_cost_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_stock_data(self.row(unit_cost="-1"), self.batch)

    def test_a_malformed_movement_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_stock_data(self.row(movement_date="01/01/2026"), self.batch)

    def test_a_missing_date_with_no_go_live_date_is_rejected(self):
        batch = make_batch(batch_code="BATCH-NO-GOLIVE", target_type="stock")

        with self.assertRaises(ValidationError):
            validate_stock_data(self.row(), batch)

    def test_an_untracked_item_cannot_receive_opening_stock(self):
        make_item(item_code="ITEM-UNTRACKED", is_stock_tracked=False)

        with self.assertRaises(ValidationError):
            validate_stock_data(self.row(item_code="ITEM-UNTRACKED"), self.batch)


class OpeningBalanceValidatorTests(TestCase):
    def setUp(self):
        super().setUp()
        self.batch = make_batch(
            batch_code="BATCH-OB", target_type="opening_balances"
        )

    def test_a_customer_balance_passes(self):
        make_customer(customer_code="CUST-1")

        self.assertIsNone(
            validate_opening_balance_data(
                {"entity_type": "customer", "entity_code": "CUST-1", "balance": "10"},
                self.batch,
            )
        )

    def test_a_supplier_balance_passes(self):
        make_supplier(supplier_code="SUP-1")

        self.assertIsNone(
            validate_opening_balance_data(
                {"entity_type": "supplier", "entity_code": "SUP-1", "balance": "10"},
                self.batch,
            )
        )

    def test_a_cashbox_balance_passes(self):
        make_cashbox(cashbox_code="CASH-1")

        self.assertIsNone(
            validate_opening_balance_data(
                {"entity_type": "cashbox", "entity_code": "CASH-1", "balance": "10"},
                self.batch,
            )
        )

    def test_an_unknown_entity_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_opening_balance_data(
                {"entity_type": "employee", "entity_code": "E-1", "balance": "10"},
                self.batch,
            )

    def test_a_missing_customer_is_rejected(self):
        from master_data.models import Customer

        with self.assertRaises(Customer.DoesNotExist):
            validate_opening_balance_data(
                {"entity_type": "customer", "entity_code": "NOPE", "balance": "10"},
                self.batch,
            )

    def test_a_non_numeric_balance_is_rejected(self):
        make_customer(customer_code="CUST-1")

        with self.assertRaises(ValidationError):
            validate_opening_balance_data(
                {"entity_type": "customer", "entity_code": "CUST-1", "balance": "lots"},
                self.batch,
            )


class UserValidatorTests(TestCase):
    def setUp(self):
        super().setUp()
        self.batch = make_batch(batch_code="BATCH-USERS", target_type="users")

    def test_a_username_alone_passes(self):
        self.assertIsNone(validate_user_data({"username": "ahmed"}, self.batch))

    def test_a_missing_username_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_user_data({"email": "a@example.com"}, self.batch)

    def test_an_existing_role_passes(self):
        make_role(code="ROLE-1")

        self.assertIsNone(
            validate_user_data({"username": "ahmed", "role_code": "ROLE-1"}, self.batch)
        )

    def test_an_unknown_role_is_rejected(self):
        from permissions.models import Role

        with self.assertRaises(Role.DoesNotExist):
            validate_user_data({"username": "ahmed", "role": "NOPE"}, self.batch)

    def test_a_bad_support_flag_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_user_data(
                {"username": "ahmed", "is_support_user": "perhaps"}, self.batch
            )


class ValidateRawImportRowTests(TestCase):
    def test_a_valid_row_returns_no_errors(self):
        _, rows = batch_with_rows()

        self.assertEqual(validate_raw_import_row(rows[0]), [])

    def test_an_invalid_row_returns_its_error_messages(self):
        _, rows = batch_with_rows(rows=[{"category_code": "CAT-1"}])

        errors = validate_raw_import_row(rows[0])

        self.assertEqual(len(errors), 1)
        self.assertIn("name_ar", errors[0])

    def test_a_missing_reference_is_reported_as_not_found(self):
        _, rows = batch_with_rows(
            rows=[{"category_code": "CAT-1", "name_ar": "أ", "parent_code": "NOPE"}]
        )

        errors = validate_raw_import_row(rows[0])

        self.assertEqual(len(errors), 1)
        self.assertIn("not found", errors[0])

    def test_an_unsupported_target_type_is_reported(self):
        batch, rows = batch_with_rows()
        ImportBatch.objects.filter(pk=batch.pk).update(target_type="planets")
        rows[0].refresh_from_db()

        errors = validate_raw_import_row(rows[0])

        self.assertEqual(len(errors), 1)
        self.assertIn("Unsupported import target type", errors[0])

    def test_a_correction_is_validated_instead_of_the_raw_data(self):
        from imports.models import ImportReviewStatus
        from imports.services import review_raw_row

        _, rows = batch_with_rows(rows=[{"category_code": "CAT-1"}])
        review_raw_row(
            rows[0].pk,
            ImportReviewStatus.CORRECTED,
            corrected_data={"category_code": "CAT-1", "name_ar": "مصحح"},
        )

        self.assertEqual(validate_raw_import_row(rows[0]), [])


class ValidateImportBatchTests(TestCase):
    def test_all_valid_rows_are_counted(self):
        batch, _ = batch_with_rows(
            rows=[
                {"category_code": "CAT-A", "name_ar": "أ"},
                {"category_code": "CAT-B", "name_ar": "ب"},
            ]
        )

        result = validate_import_batch(batch.pk)

        self.assertEqual(result["valid"], 2)
        self.assertEqual(result["invalid"], 0)
        self.assertEqual(result["errors"], {})

    def test_invalid_rows_are_reported_by_row_number(self):
        batch, _ = batch_with_rows(
            rows=[
                {"category_code": "CAT-A", "name_ar": "أ"},
                {"category_code": "CAT-B"},
            ]
        )

        result = validate_import_batch(batch.pk)

        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["invalid"], 1)
        self.assertIn(2, result["errors"])

    def test_row_statuses_are_written(self):
        batch, created = batch_with_rows(
            rows=[
                {"category_code": "CAT-A", "name_ar": "أ"},
                {"category_code": "CAT-B"},
            ]
        )

        validate_import_batch(batch.pk)
        created[0].refresh_from_db()
        created[1].refresh_from_db()

        self.assertEqual(created[0].row_status, ImportRowStatus.VALID)
        self.assertEqual(created[1].row_status, ImportRowStatus.INVALID)
        self.assertTrue(created[1].validation_errors)

    def test_batch_counters_are_refreshed(self):
        batch, _ = batch_with_rows(
            rows=[
                {"category_code": "CAT-A", "name_ar": "أ"},
                {"category_code": "CAT-B"},
            ]
        )

        validate_import_batch(batch.pk)
        batch.refresh_from_db()

        self.assertEqual(batch.total_rows, 2)
        self.assertEqual(batch.valid_rows, 1)
        self.assertEqual(batch.invalid_rows, 1)

    def test_an_empty_batch_validates_to_zero_counts(self):
        batch = make_batch()
        add_raw_rows(batch.pk, [])

        result = validate_import_batch(batch.pk)

        self.assertEqual(result, {"valid": 0, "invalid": 0, "errors": {}})

    def test_an_approved_batch_cannot_be_revalidated(self):
        batch, _ = valid_batch()
        approve_import_batch(batch.pk)

        with self.assertRaises(ValidationError):
            validate_import_batch(batch.pk)

    def test_a_cancelled_batch_cannot_be_validated(self):
        batch, _ = batch_with_rows()
        ImportBatch.objects.filter(pk=batch.pk).update(
            status=ImportBatchStatus.CANCELLED
        )

        with self.assertRaises(ValidationError):
            validate_import_batch(batch.pk)

    def test_an_unsupported_target_type_is_rejected(self):
        batch, _ = batch_with_rows()
        ImportBatch.objects.filter(pk=batch.pk).update(target_type="planets")

        with self.assertRaises(ValidationError):
            validate_import_batch(batch.pk)

    def test_revalidating_clears_a_previous_failure(self):
        from imports.models import ImportRaw

        batch, created = batch_with_rows(rows=[{"category_code": "CAT-A"}])
        validate_import_batch(batch.pk)

        ImportRaw.objects.filter(pk=created[0].pk).update(
            raw_data={"category_code": "CAT-A", "name_ar": "أ"}
        )
        result = validate_import_batch(batch.pk)
        created[0].refresh_from_db()

        self.assertEqual(result["valid"], 1)
        self.assertEqual(created[0].row_status, ImportRowStatus.VALID)
        self.assertEqual(created[0].validation_errors, [])
