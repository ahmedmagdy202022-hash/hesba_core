from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from closing.models import Period, PeriodStatus
from hesba_testing.factories import (
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    stock_in,
)
from permissions.models import RoleCode

from .models import (
    StockAdjustmentDirection,
    StockMovementType,
    StockOperationStatus,
)
from .services import (
    adjust_stock,
    cancel_stock_operation,
    get_item_authoritative_average_cost,
    get_item_location_stock_quantity,
    transfer_stock,
)


class StockOperationTests(TestCase):
    def setUp(self):
        self.operation_date = date(2026, 3, 10)
        self.period = Period.objects.create(
            period_code="2026-STOCK",
            name="2026 stock",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.owner = make_user("stock_operation_owner")
        make_user_profile(self.owner, make_seeded_role(RoleCode.OWNER))
        self.keeper = make_user("stock_operation_keeper")
        make_user_profile(self.keeper, make_seeded_role(RoleCode.STOCK_KEEPER))
        self.cashier = make_user("stock_operation_cashier")
        make_user_profile(self.cashier, make_seeded_role(RoleCode.CASHIER))
        self.item = make_item(average_cost=Decimal("999.0000"))
        self.source = make_location()
        self.destination = make_location(location_code="DEST", name_ar="الفرع")
        stock_in(self.item, self.source, "10", "5.00")

    def test_transfer_creates_atomic_linked_out_and_in(self):
        operation = transfer_stock(
            "TR-001",
            self.operation_date,
            self.item,
            self.source,
            self.destination,
            Decimal("3"),
            self.keeper,
        )
        movements = list(operation.movements.order_by("id"))
        self.assertEqual(
            [movement.movement_type for movement in movements],
            [StockMovementType.TRANSFER_OUT, StockMovementType.TRANSFER_IN],
        )
        self.assertEqual(movements[0].unit_cost, Decimal("5.0000"))
        self.assertEqual(get_item_location_stock_quantity(self.item, self.source), Decimal("7"))
        self.assertEqual(
            get_item_location_stock_quantity(self.item, self.destination), Decimal("3")
        )

    def test_transfer_reversal_appends_paired_inverse_movements(self):
        operation = transfer_stock(
            "TR-REV",
            self.operation_date,
            self.item,
            self.source,
            self.destination,
            Decimal("4"),
            self.owner,
        )
        cancel_stock_operation(
            operation.pk, self.operation_date, "Transfer entered twice", self.owner
        )
        operation.refresh_from_db()
        self.assertEqual(operation.status, StockOperationStatus.CANCELLED)
        self.assertEqual(operation.movements.count(), 4)
        self.assertEqual(get_item_location_stock_quantity(self.item, self.source), Decimal("10"))
        self.assertEqual(
            get_item_location_stock_quantity(self.item, self.destination), Decimal("0")
        )

    def test_adjustment_requires_reason_and_permission(self):
        with self.assertRaisesMessage(ValidationError, "reason"):
            adjust_stock(
                "ADJ-NO-REASON",
                self.operation_date,
                self.item,
                self.source,
                StockAdjustmentDirection.OUT,
                Decimal("1"),
                "",
                self.keeper,
            )
        with self.assertRaises(PermissionDenied):
            adjust_stock(
                "ADJ-DENIED",
                self.operation_date,
                self.item,
                self.source,
                StockAdjustmentDirection.OUT,
                Decimal("1"),
                "Count correction",
                self.cashier,
            )

    def test_adjustment_and_reversal_preserve_history(self):
        operation = adjust_stock(
            "ADJ-001",
            self.operation_date,
            self.item,
            self.source,
            StockAdjustmentDirection.OUT,
            Decimal("2"),
            "Physical count",
            self.keeper,
        )
        self.assertEqual(get_item_location_stock_quantity(self.item, self.source), Decimal("8"))
        cancel_stock_operation(
            operation.pk, self.operation_date, "Count checked again", self.keeper
        )
        self.assertEqual(get_item_location_stock_quantity(self.item, self.source), Decimal("10"))
        self.assertEqual(operation.movements.count(), 2)

    def test_authoritative_cost_ignores_stale_item_cache(self):
        self.assertEqual(get_item_authoritative_average_cost(self.item), Decimal("5.0000"))

    def test_closed_period_rejects_without_partial_movements(self):
        self.period.status = PeriodStatus.CLOSED
        self.period.closed_at = timezone.now()
        self.period.save(update_fields=["status", "closed_at"])
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            transfer_stock(
                "TR-CLOSED",
                self.operation_date,
                self.item,
                self.source,
                self.destination,
                Decimal("1"),
                self.owner,
            )
        self.assertFalse(self.item.stock_operations.exists())
