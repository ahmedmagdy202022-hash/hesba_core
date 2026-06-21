from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.models import StockMovement, StockMovementType
from master_data.models import Category, Customer, Item, Location, Supplier
from cashboxes.models import Cashbox

from .apply_services import apply_import_batch
from .models import ImportRowStatus, ImportBatchStatus, ImportTargetType
from .services import add_raw_rows, approve_import_batch, create_import_batch
from .validators import validate_import_batch


class ImportWorkflowTests(TestCase):
    def test_category_batch_validate_approve_apply(self):
        batch = create_import_batch(
            batch_code="BATCH_CAT_001",
            target_type=ImportTargetType.CATEGORIES,
            source_file_name="categories.csv",
        )
        add_raw_rows(
            batch.id,
            [
                {
                    "category_code": "CAT_TEST",
                    "name_ar": "Test Category",
                    "name_en": "Test Category",
                    "active": True,
                }
            ],
        )

        result = validate_import_batch(batch.id)
        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["invalid"], 0)

        raw_row = batch.raw_rows.get()
        raw_row.refresh_from_db()
        self.assertEqual(raw_row.row_status, ImportRowStatus.VALID)

        approve_import_batch(batch.id)
        applied = apply_import_batch(batch.id)

        batch.refresh_from_db()
        raw_row.refresh_from_db()
        self.assertEqual(batch.status, ImportBatchStatus.IMPORTED)
        self.assertEqual(len(applied), 1)
        self.assertTrue(Category.objects.filter(category_code="CAT_TEST").exists())
        self.assertEqual(raw_row.target_model, "master_data.Category")
        self.assertEqual(raw_row.target_object_id, str(Category.objects.get(category_code="CAT_TEST").id))

    def test_invalid_item_batch_cannot_be_approved(self):
        batch = create_import_batch(
            batch_code="BATCH_ITEM_INVALID_001",
            target_type=ImportTargetType.ITEMS,
            source_file_name="items.csv",
        )
        add_raw_rows(
            batch.id,
            [
                {
                    "item_code": "ITEM_BAD",
                    "item_name": "Bad Item",
                    "default_sale_price": "10.00",
                    "default_purchase_price": "5.00",
                    "average_cost": "5.0000",
                    "min_stock": "-1.000",
                    "is_stock_tracked": True,
                    "active": True,
                }
            ],
        )

        result = validate_import_batch(batch.id)
        self.assertEqual(result["valid"], 0)
        self.assertEqual(result["invalid"], 1)

        raw_row = batch.raw_rows.get()
        raw_row.refresh_from_db()
        self.assertEqual(raw_row.row_status, ImportRowStatus.INVALID)
        self.assertTrue(raw_row.validation_errors)

        with self.assertRaises(ValidationError):
            approve_import_batch(batch.id)

    def test_opening_stock_batch_creates_stock_movement(self):
        category = Category.objects.create(category_code="CAT_STOCK", name_ar="Stock")
        location = Location.objects.create(location_code="LOC_MAIN", name_ar="Main")
        item = Item.objects.create(
            item_code="ITEM_STOCK",
            item_name="Stock Item",
            category=category,
            default_sale_price=Decimal("100.00"),
            default_purchase_price=Decimal("70.00"),
            average_cost=Decimal("70.0000"),
            is_stock_tracked=True,
        )
        batch = create_import_batch(
            batch_code="BATCH_STOCK_001",
            target_type=ImportTargetType.STOCK,
            source_file_name="opening_stock.csv",
            go_live_date=date(2026, 1, 1),
        )
        add_raw_rows(
            batch.id,
            [
                {
                    "item_code": item.item_code,
                    "location_code": location.location_code,
                    "quantity": "12.000",
                    "unit_cost": "70.0000",
                }
            ],
        )

        result = validate_import_batch(batch.id)
        self.assertEqual(result["valid"], 1)
        approve_import_batch(batch.id)
        apply_import_batch(batch.id)

        movement = StockMovement.objects.get(item=item, location=location)
        self.assertEqual(movement.movement_type, StockMovementType.OPENING_STOCK)
        self.assertEqual(movement.movement_date, date(2026, 1, 1))
        self.assertEqual(movement.quantity, Decimal("12.000"))
        self.assertEqual(movement.unit_cost, Decimal("70.0000"))

    def test_opening_balances_batch_updates_existing_records(self):
        customer = Customer.objects.create(customer_code="CUST_001", name="Customer One")
        supplier = Supplier.objects.create(supplier_code="SUP_001", name="Supplier One")
        cashbox = Cashbox.objects.create(cashbox_code="BOX_001", name_ar="Box One")
        batch = create_import_batch(
            batch_code="BATCH_BAL_001",
            target_type=ImportTargetType.OPENING_BALANCES,
            source_file_name="opening_balances.csv",
        )
        add_raw_rows(
            batch.id,
            [
                {"entity_type": "customer", "entity_code": customer.customer_code, "opening_balance": "150.00"},
                {"entity_type": "supplier", "entity_code": supplier.supplier_code, "opening_balance": "250.00"},
                {"entity_type": "cashbox", "entity_code": cashbox.cashbox_code, "opening_balance": "1000.00"},
            ],
        )

        result = validate_import_batch(batch.id)
        self.assertEqual(result["valid"], 3)
        self.assertEqual(result["invalid"], 0)
        approve_import_batch(batch.id)
        apply_import_batch(batch.id)

        customer.refresh_from_db()
        supplier.refresh_from_db()
        cashbox.refresh_from_db()
        self.assertEqual(customer.opening_balance, Decimal("150.00"))
        self.assertEqual(supplier.opening_balance, Decimal("250.00"))
        self.assertEqual(cashbox.opening_balance, Decimal("1000.00"))
        self.assertEqual(customer.import_batch_id, batch.batch_code)
        self.assertEqual(supplier.import_batch_id, batch.batch_code)
        self.assertEqual(cashbox.import_batch_id, batch.batch_code)
