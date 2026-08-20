from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import UserProfile
from cashboxes.models import Cashbox
from imports.apply_services import (
    FALSE_VALUES,
    MODEL_LABELS,
    SUPPORTED_TARGET_TYPES,
    TRUE_VALUES,
    _bool,
    _date,
    _decimal,
    _required_text,
    _text,
    _value,
    apply_cashbox_row,
    apply_category_row,
    apply_customer_row,
    apply_import_batch,
    apply_item_row,
    apply_location_row,
    apply_opening_balance_row,
    apply_stock_row,
    apply_supplier_row,
    apply_user_row,
    get_effective_row_data,
)
from imports.models import ImportBatch, ImportBatchStatus, ImportRaw, ImportReviewStatus, ImportRowStatus
from imports.services import (
    add_raw_rows,
    approve_import_batch,
    create_import_batch,
    mark_raw_row_validation,
    review_raw_row,
)
from imports.tests_services import batch_with_rows, make_batch, valid_batch
from inventory.models import StockMovement, StockMovementType
from master_data.models import Category, Customer, Item, Location, Supplier
from permissions.models import Role
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_role, make_supplier


class FieldHelperTests(TestCase):
    def test_value_returns_the_first_populated_key(self):
        self.assertEqual(_value({"a": "", "b": "second"}, "a", "b"), "second")

    def test_value_falls_back_to_the_default(self):
        self.assertEqual(_value({}, "a", default="fallback"), "fallback")

    def test_value_skips_none(self):
        self.assertEqual(_value({"a": None, "b": 3}, "a", "b"), 3)

    def test_text_strips_whitespace(self):
        self.assertEqual(_text({"a": "  padded  "}, "a"), "padded")

    def test_text_stringifies_numbers(self):
        self.assertEqual(_text({"a": 42}, "a"), "42")

    def test_text_returns_the_default_when_absent(self):
        self.assertEqual(_text({}, "a", default="none"), "none")

    def test_required_text_rejects_a_missing_value(self):
        with self.assertRaises(ValidationError):
            _required_text({}, "code")

    def test_required_text_rejects_blank_whitespace(self):
        with self.assertRaises(ValidationError):
            _required_text({"code": "   "}, "code")

    def test_decimal_parses_a_numeric_string(self):
        self.assertEqual(_decimal({"a": "12.34"}, "a"), Decimal("12.34"))

    def test_decimal_defaults_to_zero(self):
        self.assertEqual(_decimal({}, "a"), Decimal("0"))

    def test_decimal_uses_the_given_default_when_blank(self):
        self.assertEqual(_decimal({"a": ""}, "a", default="5"), Decimal("5"))

    def test_decimal_rejects_a_non_numeric_value(self):
        with self.assertRaises(ValidationError):
            _decimal({"a": "twelve"}, "a")

    def test_bool_accepts_every_true_word(self):
        for word in TRUE_VALUES:
            with self.subTest(word=word):
                self.assertTrue(_bool({"a": word}, "a"))

    def test_bool_accepts_every_false_word(self):
        for word in FALSE_VALUES:
            with self.subTest(word=word):
                self.assertFalse(_bool({"a": word}, "a"))

    def test_bool_is_case_insensitive(self):
        self.assertTrue(_bool({"a": "TRUE"}, "a"))
        self.assertFalse(_bool({"a": "No"}, "a"))

    def test_bool_passes_real_booleans_through(self):
        self.assertTrue(_bool({"a": True}, "a"))
        self.assertFalse(_bool({"a": False}, "a"))

    def test_bool_returns_the_default_when_blank(self):
        self.assertTrue(_bool({"a": ""}, "a", default=True))
        self.assertFalse(_bool({}, "a", default=False))

    def test_bool_rejects_an_unrecognised_word(self):
        with self.assertRaises(ValidationError):
            _bool({"a": "maybe"}, "a")

    def test_date_parses_an_iso_string(self):
        self.assertEqual(_date({"a": "2026-03-04"}, "a"), date(2026, 3, 4))

    def test_date_passes_a_real_date_through(self):
        self.assertEqual(_date({"a": date(2026, 3, 4)}, "a"), date(2026, 3, 4))

    def test_date_rejects_a_malformed_string(self):
        with self.assertRaises(ValidationError):
            _date({"a": "04/03/2026"}, "a")

    def test_date_requires_a_value_when_there_is_no_default(self):
        with self.assertRaises(ValidationError):
            _date({}, "movement_date")

    def test_date_uses_the_default_when_absent(self):
        self.assertEqual(
            _date({}, "a", default=date(2026, 1, 1)), date(2026, 1, 1)
        )


class GetEffectiveRowDataTests(TestCase):
    def setUp(self):
        super().setUp()
        _, self.rows = batch_with_rows()
        self.row = self.rows[0]

    def test_raw_data_is_used_without_a_review(self):
        self.assertEqual(get_effective_row_data(self.row), self.row.raw_data)

    def test_a_correction_overrides_the_raw_data(self):
        corrected = {"category_code": "CAT-FIXED", "name_ar": "مصحح"}
        review_raw_row(
            self.row.pk, ImportReviewStatus.CORRECTED, corrected_data=corrected
        )

        self.assertEqual(get_effective_row_data(self.row), corrected)

    def test_a_non_correcting_review_does_not_override(self):
        review_raw_row(
            self.row.pk,
            ImportReviewStatus.APPROVED,
            corrected_data={"category_code": "IGNORED"},
        )

        self.assertEqual(get_effective_row_data(self.row), self.row.raw_data)

    def test_a_correction_with_no_data_does_not_override(self):
        review_raw_row(self.row.pk, ImportReviewStatus.CORRECTED, corrected_data={})

        self.assertEqual(get_effective_row_data(self.row), self.row.raw_data)

    def test_the_latest_correction_wins(self):
        review_raw_row(
            self.row.pk,
            ImportReviewStatus.CORRECTED,
            corrected_data={"category_code": "FIRST"},
        )
        review_raw_row(
            self.row.pk,
            ImportReviewStatus.CORRECTED,
            corrected_data={"category_code": "SECOND"},
        )

        self.assertEqual(
            get_effective_row_data(self.row), {"category_code": "SECOND"}
        )

    def test_an_empty_raw_row_yields_an_empty_dict(self):
        # raw_data is NOT NULL, so the empty case is {} rather than None.
        ImportRaw.objects.filter(pk=self.row.pk).update(raw_data={})
        self.row.refresh_from_db()

        self.assertEqual(get_effective_row_data(self.row), {})


class ApplyRowHandlerTests(TestCase):
    """Each handler creates or updates one controlled row."""

    def setUp(self):
        super().setUp()
        self.batch = make_batch(batch_code="BATCH-APPLY")

    def test_category_row_is_created(self):
        obj, label = apply_category_row(
            {"category_code": "CAT-001", "name_ar": "تصنيف", "name_en": "Category"},
            self.batch,
        )

        self.assertEqual(label, MODEL_LABELS["categories"])
        self.assertEqual(obj.category_code, "CAT-001")
        self.assertEqual(obj.name_en, "Category")
        self.assertTrue(obj.active)

    def test_category_row_updates_an_existing_row(self):
        Category.objects.create(category_code="CAT-001", name_ar="قديم")

        obj, _ = apply_category_row(
            {"category_code": "CAT-001", "name_ar": "جديد"}, self.batch
        )

        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(obj.name_ar, "جديد")

    def test_category_row_links_its_parent(self):
        parent = Category.objects.create(category_code="CAT-PARENT", name_ar="أب")

        obj, _ = apply_category_row(
            {"category_code": "CAT-CHILD", "name_ar": "ابن", "parent_code": "CAT-PARENT"},
            self.batch,
        )

        self.assertEqual(obj.parent_id, parent.pk)

    def test_category_row_rejects_an_unknown_parent(self):
        with self.assertRaises(Category.DoesNotExist):
            apply_category_row(
                {"category_code": "CAT-1", "name_ar": "ابن", "parent_code": "MISSING"},
                self.batch,
            )

    def test_category_row_requires_a_code(self):
        with self.assertRaises(ValidationError):
            apply_category_row({"name_ar": "تصنيف"}, self.batch)

    def test_location_row_is_created_with_its_flags(self):
        obj, label = apply_location_row(
            {
                "location_code": "LOC-001",
                "name_ar": "مخزن",
                "is_default": "yes",
                "is_selling_location": "no",
            },
            self.batch,
        )

        self.assertEqual(label, MODEL_LABELS["locations"])
        self.assertTrue(obj.is_default)
        self.assertFalse(obj.is_selling_location)
        self.assertTrue(obj.is_receiving_location)

    def test_location_row_updates_an_existing_row(self):
        make_location(location_code="LOC-001", name_ar="قديم")

        obj, _ = apply_location_row(
            {"location_code": "LOC-001", "name_ar": "جديد"}, self.batch
        )

        self.assertEqual(Location.objects.filter(location_code="LOC-001").count(), 1)
        self.assertEqual(obj.name_ar, "جديد")

    def test_item_row_is_created_with_its_money_fields(self):
        obj, label = apply_item_row(
            {
                "item_code": "ITEM-100",
                "item_name": "Widget",
                "default_sale_price": "19.99",
                "purchase_price": "10.50",
                "average_cost": "11.00",
                "min_stock": "3",
                "unit": "box",
            },
            self.batch,
        )

        self.assertEqual(label, MODEL_LABELS["items"])
        self.assertEqual(obj.default_sale_price, Decimal("19.99"))
        self.assertEqual(obj.default_purchase_price, Decimal("10.50"))
        self.assertEqual(obj.average_cost, Decimal("11.00"))
        self.assertEqual(obj.min_stock, Decimal("3"))
        self.assertEqual(obj.unit, "box")

    def test_item_row_defaults_the_unit(self):
        obj, _ = apply_item_row(
            {"item_code": "ITEM-100", "item_name": "Widget"}, self.batch
        )

        self.assertEqual(obj.unit, "unit")

    def test_item_row_stamps_the_batch_code(self):
        obj, _ = apply_item_row(
            {"item_code": "ITEM-100", "item_name": "Widget"}, self.batch
        )

        self.assertEqual(obj.import_batch_id, self.batch.batch_code)

    def test_item_row_links_its_category(self):
        category = Category.objects.create(category_code="CAT-001", name_ar="تصنيف")

        obj, _ = apply_item_row(
            {"item_code": "ITEM-100", "item_name": "Widget", "category_code": "CAT-001"},
            self.batch,
        )

        self.assertEqual(obj.category_id, category.pk)

    def test_item_row_leaves_the_category_empty_when_absent(self):
        obj, _ = apply_item_row(
            {"item_code": "ITEM-100", "item_name": "Widget"}, self.batch
        )

        self.assertIsNone(obj.category_id)

    def test_item_row_rejects_a_bad_price(self):
        with self.assertRaises(ValidationError):
            apply_item_row(
                {"item_code": "ITEM-100", "item_name": "W", "sale_price": "abc"},
                self.batch,
            )

    def test_item_row_updates_an_existing_item(self):
        make_item(item_code="ITEM-100", item_name="Old")

        obj, _ = apply_item_row(
            {"item_code": "ITEM-100", "item_name": "New"}, self.batch
        )

        self.assertEqual(Item.objects.filter(item_code="ITEM-100").count(), 1)
        self.assertEqual(obj.item_name, "New")

    def test_customer_row_is_created(self):
        obj, label = apply_customer_row(
            {
                "customer_code": "CUST-100",
                "name": "Acme",
                "phone": "0100",
                "opening_balance": "250.00",
                "credit_limit": "1000",
            },
            self.batch,
        )

        self.assertEqual(label, MODEL_LABELS["customers"])
        self.assertEqual(obj.opening_balance, Decimal("250.00"))
        self.assertEqual(obj.credit_limit, Decimal("1000"))
        self.assertEqual(obj.import_batch_id, self.batch.batch_code)

    def test_customer_row_accepts_the_alternate_name_key(self):
        obj, _ = apply_customer_row(
            {"customer_code": "CUST-100", "customer_name": "Acme"}, self.batch
        )

        self.assertEqual(obj.name, "Acme")

    def test_customer_row_requires_a_name(self):
        with self.assertRaises(ValidationError):
            apply_customer_row({"customer_code": "CUST-100"}, self.batch)

    def test_supplier_row_is_created(self):
        obj, label = apply_supplier_row(
            {"supplier_code": "SUP-100", "name": "Vendor", "opening_balance": "75.50"},
            self.batch,
        )

        self.assertEqual(label, MODEL_LABELS["suppliers"])
        self.assertEqual(obj.opening_balance, Decimal("75.50"))

    def test_supplier_row_updates_an_existing_supplier(self):
        make_supplier(supplier_code="SUP-100", name="Old")

        obj, _ = apply_supplier_row(
            {"supplier_code": "SUP-100", "name": "New"}, self.batch
        )

        self.assertEqual(Supplier.objects.filter(supplier_code="SUP-100").count(), 1)
        self.assertEqual(obj.name, "New")

    def test_cashbox_row_is_created_with_its_currency(self):
        obj, label = apply_cashbox_row(
            {"cashbox_code": "CASH-100", "name_ar": "خزنة", "currency": "USD"},
            self.batch,
        )

        self.assertEqual(label, MODEL_LABELS["cashboxes"])
        self.assertEqual(obj.currency, "USD")

    def test_cashbox_row_defaults_the_currency_to_egp(self):
        obj, _ = apply_cashbox_row(
            {"cashbox_code": "CASH-100", "name_ar": "خزنة"}, self.batch
        )

        self.assertEqual(obj.currency, "EGP")

    def test_stock_row_creates_an_opening_movement(self):
        item = make_item(item_code="ITEM-100")
        location = make_location(location_code="LOC-100")

        obj, label = apply_stock_row(
            {
                "item_code": "ITEM-100",
                "location_code": "LOC-100",
                "quantity": "12",
                "unit_cost": "4.25",
                "movement_date": "2026-02-02",
            },
            self.batch,
        )

        self.assertEqual(label, MODEL_LABELS["stock"])
        self.assertEqual(obj.movement_type, StockMovementType.OPENING_STOCK)
        self.assertEqual(obj.item_id, item.pk)
        self.assertEqual(obj.location_id, location.pk)
        self.assertEqual(obj.quantity, Decimal("12"))
        self.assertEqual(obj.unit_cost, Decimal("4.25"))
        self.assertEqual(obj.movement_date, date(2026, 2, 2))

    def test_stock_row_falls_back_to_the_batch_go_live_date(self):
        make_item(item_code="ITEM-100")
        make_location(location_code="LOC-100")
        batch = make_batch(batch_code="BATCH-GOLIVE", go_live_date=date(2026, 5, 5))

        obj, _ = apply_stock_row(
            {"item_code": "ITEM-100", "location_code": "LOC-100", "quantity": "1"},
            batch,
        )

        self.assertEqual(obj.movement_date, date(2026, 5, 5))

    def test_stock_row_without_a_date_or_go_live_date_is_rejected(self):
        make_item(item_code="ITEM-100")
        make_location(location_code="LOC-100")

        with self.assertRaises(ValidationError):
            apply_stock_row(
                {"item_code": "ITEM-100", "location_code": "LOC-100", "quantity": "1"},
                self.batch,
            )

    def test_stock_row_rejects_an_unknown_item(self):
        make_location(location_code="LOC-100")

        with self.assertRaises(Item.DoesNotExist):
            apply_stock_row(
                {"item_code": "MISSING", "location_code": "LOC-100", "quantity": "1"},
                self.batch,
            )

    def test_stock_row_rejects_an_unknown_location(self):
        make_item(item_code="ITEM-100")

        with self.assertRaises(Location.DoesNotExist):
            apply_stock_row(
                {"item_code": "ITEM-100", "location_code": "MISSING", "quantity": "1"},
                self.batch,
            )

    def test_stock_row_records_the_acting_user(self):
        from hesba_testing.factories import make_user

        make_item(item_code="ITEM-100")
        make_location(location_code="LOC-100")
        user = make_user()

        obj, _ = apply_stock_row(
            {
                "item_code": "ITEM-100",
                "location_code": "LOC-100",
                "quantity": "1",
                "movement_date": "2026-02-02",
            },
            self.batch,
            user=user,
        )

        self.assertEqual(obj.created_by_id, user.pk)

    def test_opening_balance_updates_a_customer(self):
        customer = make_customer(customer_code="CUST-100")

        obj, label = apply_opening_balance_row(
            {"entity_type": "customer", "entity_code": "CUST-100", "balance": "300.00"},
            self.batch,
        )

        self.assertEqual(label, "opening_balance.customer")
        self.assertEqual(obj.pk, customer.pk)
        self.assertEqual(obj.opening_balance, Decimal("300.00"))

    def test_opening_balance_updates_a_supplier(self):
        make_supplier(supplier_code="SUP-100")

        obj, label = apply_opening_balance_row(
            {"entity_type": "supplier", "entity_code": "SUP-100", "balance": "80.00"},
            self.batch,
        )

        self.assertEqual(label, "opening_balance.supplier")
        self.assertEqual(obj.opening_balance, Decimal("80.00"))

    def test_opening_balance_updates_a_cashbox(self):
        make_cashbox(cashbox_code="CASH-100")

        obj, label = apply_opening_balance_row(
            {"entity_type": "cashbox", "entity_code": "CASH-100", "balance": "500.00"},
            self.batch,
        )

        self.assertEqual(label, "opening_balance.cashbox")
        self.assertEqual(obj.opening_balance, Decimal("500.00"))

    def test_opening_balance_entity_type_is_case_insensitive(self):
        make_customer(customer_code="CUST-100")

        _, label = apply_opening_balance_row(
            {"entity_type": "CUSTOMER", "entity_code": "CUST-100", "balance": "1"},
            self.batch,
        )

        self.assertEqual(label, "opening_balance.customer")

    def test_opening_balance_rejects_an_unknown_entity_type(self):
        with self.assertRaises(ValidationError):
            apply_opening_balance_row(
                {"entity_type": "employee", "entity_code": "E-1", "balance": "1"},
                self.batch,
            )

    def test_opening_balance_rejects_an_unknown_entity_code(self):
        with self.assertRaises(Customer.DoesNotExist):
            apply_opening_balance_row(
                {"entity_type": "customer", "entity_code": "MISSING", "balance": "1"},
                self.batch,
            )

    def test_user_row_creates_a_user_and_profile(self):
        obj, label = apply_user_row(
            {
                "username": "ahmed",
                "email": "ahmed@example.com",
                "first_name": "Ahmed",
                "display_name": "Ahmed H",
                "phone": "0100",
            },
            self.batch,
        )

        self.assertEqual(label, MODEL_LABELS["users"])
        self.assertIsInstance(obj, UserProfile)
        self.assertEqual(obj.display_name, "Ahmed H")
        self.assertEqual(obj.phone, "0100")
        user = get_user_model().objects.get(username="ahmed")
        self.assertEqual(user.email, "ahmed@example.com")
        self.assertEqual(user.first_name, "Ahmed")

    def test_an_imported_user_cannot_log_in_with_a_password(self):
        apply_user_row({"username": "ahmed"}, self.batch)

        user = get_user_model().objects.get(username="ahmed")
        self.assertFalse(user.has_usable_password())

    def test_user_row_defaults_the_display_name_to_the_username(self):
        obj, _ = apply_user_row({"username": "ahmed"}, self.batch)

        self.assertEqual(obj.display_name, "ahmed")

    def test_user_row_links_a_role(self):
        role = make_role(code="ROLE-ADMIN")

        obj, _ = apply_user_row(
            {"username": "ahmed", "role_code": "ROLE-ADMIN"}, self.batch
        )

        self.assertEqual(obj.role_id, role.pk)

    def test_user_row_rejects_an_unknown_role(self):
        with self.assertRaises(Role.DoesNotExist):
            apply_user_row({"username": "ahmed", "role": "MISSING"}, self.batch)

    def test_user_row_requires_a_password_change_by_default(self):
        obj, _ = apply_user_row({"username": "ahmed"}, self.batch)

        self.assertTrue(obj.must_change_password)

    def test_user_row_updates_an_existing_user_and_profile(self):
        obj, _ = apply_user_row({"username": "ahmed", "first_name": "Old"}, self.batch)
        first_profile_pk = obj.pk

        obj, _ = apply_user_row({"username": "ahmed", "first_name": "New"}, self.batch)

        self.assertEqual(get_user_model().objects.filter(username="ahmed").count(), 1)
        self.assertEqual(UserProfile.objects.count(), 1)
        self.assertEqual(obj.pk, first_profile_pk)
        self.assertEqual(
            get_user_model().objects.get(username="ahmed").first_name, "New"
        )

    def test_an_inactive_user_gets_an_inactive_profile(self):
        obj, _ = apply_user_row({"username": "ahmed", "active": "no"}, self.batch)

        self.assertFalse(obj.active)
        self.assertFalse(get_user_model().objects.get(username="ahmed").is_active)


class ApplyImportBatchTests(TestCase):
    def approved_batch(self, target_type, rows, batch_code="BATCH-RUN", **kwargs):
        batch, created = valid_batch(
            batch_code=batch_code, target_type=target_type, rows=rows, **kwargs
        )
        approve_import_batch(batch.pk)
        batch.refresh_from_db()
        return batch, created

    def test_every_supported_target_type_has_a_handler(self):
        from imports.apply_services import APPLY_HANDLERS

        self.assertEqual(set(APPLY_HANDLERS), SUPPORTED_TARGET_TYPES)

    def test_applying_creates_the_target_rows(self):
        batch, _ = self.approved_batch(
            "categories",
            [
                {"category_code": "CAT-A", "name_ar": "أ"},
                {"category_code": "CAT-B", "name_ar": "ب"},
            ],
        )

        applied = apply_import_batch(batch.pk)

        self.assertEqual(len(applied), 2)
        self.assertEqual(Category.objects.count(), 2)

    def test_applying_marks_the_rows_imported_and_completes_the_batch(self):
        batch, created = self.approved_batch(
            "categories", [{"category_code": "CAT-A", "name_ar": "أ"}]
        )

        apply_import_batch(batch.pk)
        batch.refresh_from_db()
        created[0].refresh_from_db()

        self.assertEqual(created[0].row_status, ImportRowStatus.IMPORTED)
        self.assertEqual(created[0].target_model, MODEL_LABELS["categories"])
        self.assertEqual(batch.status, ImportBatchStatus.IMPORTED)
        self.assertEqual(batch.imported_rows, 1)

    def test_rows_are_applied_in_row_number_order(self):
        batch, _ = self.approved_batch(
            "categories",
            [
                {"category_code": "CAT-PARENT", "name_ar": "أب"},
                {"category_code": "CAT-CHILD", "name_ar": "ابن", "parent_code": "CAT-PARENT"},
            ],
        )

        applied = apply_import_batch(batch.pk)

        self.assertEqual(applied[1].parent_id, applied[0].pk)

    def test_a_correction_is_applied_instead_of_the_raw_data(self):
        batch, created = valid_batch(
            batch_code="BATCH-CORRECT",
            target_type="categories",
            rows=[{"category_code": "CAT-RAW", "name_ar": "خام"}],
        )
        review_raw_row(
            created[0].pk,
            ImportReviewStatus.CORRECTED,
            corrected_data={"category_code": "CAT-FIXED", "name_ar": "مصحح"},
        )
        approve_import_batch(batch.pk)

        apply_import_batch(batch.pk)

        self.assertTrue(Category.objects.filter(category_code="CAT-FIXED").exists())
        self.assertFalse(Category.objects.filter(category_code="CAT-RAW").exists())

    def test_stock_batches_receive_the_acting_user(self):
        from hesba_testing.factories import make_user

        make_item(item_code="ITEM-100")
        make_location(location_code="LOC-100")
        batch, _ = self.approved_batch(
            "stock",
            [
                {
                    "item_code": "ITEM-100",
                    "location_code": "LOC-100",
                    "quantity": "5",
                    "movement_date": "2026-02-02",
                }
            ],
        )
        user = make_user()

        applied = apply_import_batch(batch.pk, user=user)

        self.assertEqual(applied[0].created_by_id, user.pk)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_an_unapproved_batch_cannot_be_applied(self):
        batch, _ = valid_batch(
            batch_code="BATCH-UNAPPROVED",
            target_type="categories",
            rows=[{"category_code": "CAT-A", "name_ar": "أ"}],
        )

        with self.assertRaises(ValidationError):
            apply_import_batch(batch.pk)

    def test_an_unsupported_target_type_is_rejected(self):
        batch, _ = valid_batch(
            batch_code="BATCH-BAD",
            target_type="categories",
            rows=[{"category_code": "CAT-A", "name_ar": "أ"}],
        )
        approve_import_batch(batch.pk)
        ImportBatch.objects.filter(pk=batch.pk).update(target_type="planets")

        with self.assertRaises(ValidationError):
            apply_import_batch(batch.pk)

    def test_a_batch_with_no_valid_rows_is_rejected(self):
        batch, created = self.approved_batch(
            "categories", [{"category_code": "CAT-A", "name_ar": "أ"}]
        )
        ImportRaw.objects.filter(pk=created[0].pk).update(
            row_status=ImportRowStatus.SKIPPED
        )

        with self.assertRaises(ValidationError):
            apply_import_batch(batch.pk)

    def test_a_failing_row_rolls_the_whole_batch_back(self):
        batch, _ = self.approved_batch(
            "categories",
            [
                {"category_code": "CAT-A", "name_ar": "أ"},
                {"category_code": "CAT-B"},  # missing name_ar
            ],
        )

        with self.assertRaises(ValidationError):
            apply_import_batch(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(Category.objects.count(), 0)
        self.assertEqual(batch.status, ImportBatchStatus.APPROVED)

    def test_applying_twice_is_rejected(self):
        batch, _ = self.approved_batch(
            "categories", [{"category_code": "CAT-A", "name_ar": "أ"}]
        )
        apply_import_batch(batch.pk)

        with self.assertRaises(ValidationError):
            apply_import_batch(batch.pk)
