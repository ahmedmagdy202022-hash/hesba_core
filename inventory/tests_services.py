from decimal import Decimal

from django.test import TestCase

from hesba_testing.factories import make_item, make_location, make_stock_movement
from inventory.models import StockMovementType
from master_data.models import Item
from inventory.services import (
    get_item_location_stock_quantity,
    get_item_stock_quantity,
    get_item_stock_value,
    recalculate_item_average_cost,
)


class StockQuantityTests(TestCase):
    def setUp(self):
        super().setUp()
        self.item = make_item()
        self.main = make_location()
        self.branch = make_location(location_code="BRANCH", name_ar="فرع")

    def test_quantity_is_zero_without_movements(self):
        self.assertEqual(get_item_stock_quantity(self.item), Decimal("0"))

    def test_in_movements_increase_quantity(self):
        for movement_type in (
            StockMovementType.PURCHASE_IN,
            StockMovementType.SALE_RETURN_IN,
            StockMovementType.TRANSFER_IN,
            StockMovementType.ADJUSTMENT_IN,
            StockMovementType.OPENING_STOCK,
        ):
            with self.subTest(movement_type=movement_type):
                item = make_item(item_code=f"ITEM-{movement_type}")
                make_stock_movement(item, self.main, movement_type, "7")

                self.assertEqual(get_item_stock_quantity(item), Decimal("7"))

    def test_out_movements_decrease_quantity(self):
        for movement_type in (
            StockMovementType.SALE_OUT,
            StockMovementType.PURCHASE_RETURN_OUT,
            StockMovementType.TRANSFER_OUT,
            StockMovementType.ADJUSTMENT_OUT,
        ):
            with self.subTest(movement_type=movement_type):
                item = make_item(item_code=f"ITEM-{movement_type}")
                make_stock_movement(item, self.main, StockMovementType.PURCHASE_IN, "10")
                make_stock_movement(item, self.main, movement_type, "4")

                self.assertEqual(get_item_stock_quantity(item), Decimal("6"))

    def test_quantity_can_go_negative(self):
        make_stock_movement(self.item, self.main, StockMovementType.SALE_OUT, "3")

        self.assertEqual(get_item_stock_quantity(self.item), Decimal("-3"))

    def test_quantity_sums_across_locations(self):
        make_stock_movement(self.item, self.main, StockMovementType.PURCHASE_IN, "5")
        make_stock_movement(self.item, self.branch, StockMovementType.PURCHASE_IN, "8")

        self.assertEqual(get_item_stock_quantity(self.item), Decimal("13"))

    def test_quantity_ignores_other_items(self):
        other = make_item(item_code="ITEM-OTHER")
        make_stock_movement(other, self.main, StockMovementType.PURCHASE_IN, "99")

        self.assertEqual(get_item_stock_quantity(self.item), Decimal("0"))

    def test_location_quantity_only_counts_that_location(self):
        make_stock_movement(self.item, self.main, StockMovementType.PURCHASE_IN, "5")
        make_stock_movement(self.item, self.branch, StockMovementType.PURCHASE_IN, "8")
        make_stock_movement(self.item, self.main, StockMovementType.SALE_OUT, "2")

        self.assertEqual(get_item_location_stock_quantity(self.item, self.main), Decimal("3"))
        self.assertEqual(get_item_location_stock_quantity(self.item, self.branch), Decimal("8"))

    def test_location_quantity_is_zero_for_an_unused_location(self):
        self.assertEqual(
            get_item_location_stock_quantity(self.item, self.branch), Decimal("0")
        )


class StockValueTests(TestCase):
    def setUp(self):
        super().setUp()
        self.item = make_item()
        self.main = make_location()

    def test_value_is_zero_without_movements(self):
        self.assertEqual(get_item_stock_value(self.item), Decimal("0"))

    def test_value_multiplies_quantity_by_unit_cost(self):
        make_stock_movement(
            self.item, self.main, StockMovementType.PURCHASE_IN, "4", unit_cost="2.50"
        )

        self.assertEqual(get_item_stock_value(self.item), Decimal("10.00"))

    def test_out_movements_reduce_value_at_their_own_unit_cost(self):
        make_stock_movement(
            self.item, self.main, StockMovementType.PURCHASE_IN, "10", unit_cost="3.00"
        )
        make_stock_movement(
            self.item, self.main, StockMovementType.SALE_OUT, "2", unit_cost="3.00"
        )

        self.assertEqual(get_item_stock_value(self.item), Decimal("24.00"))

    def test_value_combines_batches_at_different_costs(self):
        make_stock_movement(
            self.item, self.main, StockMovementType.PURCHASE_IN, "10", unit_cost="2.00"
        )
        make_stock_movement(
            self.item, self.main, StockMovementType.PURCHASE_IN, "10", unit_cost="4.00"
        )

        self.assertEqual(get_item_stock_value(self.item), Decimal("60.00"))


class RecalculateItemAverageCostTests(TestCase):
    def setUp(self):
        super().setUp()
        self.main = make_location()

    def test_untracked_item_keeps_its_average_cost_untouched(self):
        item = make_item(is_stock_tracked=False, average_cost=Decimal("7.77"))
        make_stock_movement(
            item, self.main, StockMovementType.PURCHASE_IN, "10", unit_cost="1.00"
        )

        returned = recalculate_item_average_cost(item)
        item.refresh_from_db()

        self.assertEqual(returned, Decimal("7.77"))
        self.assertEqual(item.average_cost, Decimal("7.77"))

    def test_average_cost_is_value_over_quantity(self):
        item = make_item()
        make_stock_movement(
            item, self.main, StockMovementType.PURCHASE_IN, "10", unit_cost="2.00"
        )
        make_stock_movement(
            item, self.main, StockMovementType.PURCHASE_IN, "10", unit_cost="4.00"
        )

        recalculate_item_average_cost(item)
        item.refresh_from_db()

        self.assertEqual(item.average_cost, Decimal("3.00"))

    def test_average_cost_is_persisted(self):
        item = make_item()
        make_stock_movement(
            item, self.main, StockMovementType.PURCHASE_IN, "4", unit_cost="2.50"
        )

        recalculate_item_average_cost(item)

        self.assertEqual(
            Item.objects.get(pk=item.pk).average_cost,
            Decimal("2.50"),
        )

    def test_zero_quantity_resets_average_cost_to_zero(self):
        item = make_item(average_cost=Decimal("9.99"))
        make_stock_movement(
            item, self.main, StockMovementType.PURCHASE_IN, "5", unit_cost="3.00"
        )
        make_stock_movement(
            item, self.main, StockMovementType.SALE_OUT, "5", unit_cost="3.00"
        )

        recalculate_item_average_cost(item)
        item.refresh_from_db()

        self.assertEqual(item.average_cost, Decimal("0"))

    def test_negative_quantity_resets_average_cost_to_zero(self):
        item = make_item(average_cost=Decimal("9.99"))
        make_stock_movement(
            item, self.main, StockMovementType.SALE_OUT, "2", unit_cost="3.00"
        )

        recalculate_item_average_cost(item)
        item.refresh_from_db()

        self.assertEqual(item.average_cost, Decimal("0"))

    def test_item_with_no_movements_resets_average_cost_to_zero(self):
        item = make_item(average_cost=Decimal("9.99"))

        recalculate_item_average_cost(item)
        item.refresh_from_db()

        self.assertEqual(item.average_cost, Decimal("0"))
