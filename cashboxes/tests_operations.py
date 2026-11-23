from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from closing.models import Period, PeriodStatus
from hesba_testing.factories import make_cashbox, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .models import (
    CashboxDirection,
    CashboxMovementType,
    CashboxOperation,
    CashboxOperationStatus,
    CashboxOperationType,
)
from .services import cancel_cashbox_operation, create_cashbox_operation, get_cashbox_balance


class CashboxOperationTests(TestCase):
    def setUp(self):
        self.operation_date = date(2026, 4, 10)
        self.period = Period.objects.create(
            period_code="2026-CASH-OPS",
            name="2026 cash operations",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.owner = make_user("cash_operation_owner")
        make_user_profile(self.owner, make_seeded_role(RoleCode.OWNER))
        self.accountant = make_user("cash_operation_accountant")
        make_user_profile(self.accountant, make_seeded_role(RoleCode.ACCOUNTANT))
        self.manager = make_user("cash_operation_manager")
        make_user_profile(self.manager, make_seeded_role(RoleCode.MANAGER))
        self.source = make_cashbox(opening_balance=Decimal("100.00"))
        self.destination = make_cashbox(
            cashbox_code="CASH-DEST", opening_balance=Decimal("20.00")
        )

    def create(self, reference, operation_type, **kwargs):
        return create_cashbox_operation(
            reference_number=reference,
            operation_date=self.operation_date,
            operation_type=operation_type,
            amount=kwargs.pop("amount", Decimal("25.00")),
            reason=kwargs.pop("reason", "Approved cash operation"),
            user=kwargs.pop("user", self.accountant),
            **kwargs,
        )

    def test_direct_in_and_out_create_real_linked_movements(self):
        cash_in = self.create(
            "CASH-IN-1",
            CashboxOperationType.DIRECT_IN,
            destination_cashbox=self.destination,
        )
        cash_out = self.create(
            "CASH-OUT-1",
            CashboxOperationType.DIRECT_OUT,
            source_cashbox=self.source,
        )
        self.assertEqual(cash_in.movements.get().direction, CashboxDirection.IN)
        self.assertEqual(cash_out.movements.get().direction, CashboxDirection.OUT)
        self.assertEqual(get_cashbox_balance(self.destination), Decimal("45.00"))
        self.assertEqual(get_cashbox_balance(self.source), Decimal("75.00"))

    def test_transfer_creates_atomic_linked_out_and_in(self):
        operation = self.create(
            "CASH-TR-1",
            CashboxOperationType.TRANSFER,
            source_cashbox=self.source,
            destination_cashbox=self.destination,
        )
        movements = list(operation.movements.order_by("id"))
        self.assertEqual(
            [movement.movement_type for movement in movements],
            [CashboxMovementType.TRANSFER_OUT, CashboxMovementType.TRANSFER_IN],
        )
        self.assertEqual(get_cashbox_balance(self.source), Decimal("75.00"))
        self.assertEqual(get_cashbox_balance(self.destination), Decimal("45.00"))

    def test_transfer_rejects_currency_mismatch_without_partial_rows(self):
        self.destination.currency = "USD"
        self.destination.save(update_fields=["currency"])
        with self.assertRaisesMessage(ValidationError, "same currency"):
            self.create(
                "CASH-TR-FX",
                CashboxOperationType.TRANSFER,
                source_cashbox=self.source,
                destination_cashbox=self.destination,
            )
        self.assertFalse(CashboxOperation.objects.exists())
        self.assertFalse(self.source.movements.exists())
        self.assertFalse(self.destination.movements.exists())

    def test_out_rejects_negative_source_and_manager_permission(self):
        with self.assertRaisesMessage(ValidationError, "cannot become negative"):
            self.create(
                "CASH-OUT-NEG",
                CashboxOperationType.DIRECT_OUT,
                source_cashbox=self.source,
                amount=Decimal("100.01"),
            )
        with self.assertRaises(PermissionDenied):
            self.create(
                "CASH-OUT-MANAGER",
                CashboxOperationType.DIRECT_OUT,
                source_cashbox=self.source,
                user=self.manager,
            )

    def test_transfer_reversal_appends_inverse_rows(self):
        operation = self.create(
            "CASH-TR-REV",
            CashboxOperationType.TRANSFER,
            source_cashbox=self.source,
            destination_cashbox=self.destination,
        )
        cancel_cashbox_operation(
            operation.pk, self.operation_date, "Transfer duplicated", self.owner
        )
        operation.refresh_from_db()
        self.assertEqual(operation.status, CashboxOperationStatus.CANCELLED)
        self.assertEqual(operation.movements.count(), 4)
        self.assertEqual(operation.movements.filter(reversal_of__isnull=False).count(), 2)
        self.assertEqual(get_cashbox_balance(self.source), Decimal("100.00"))
        self.assertEqual(get_cashbox_balance(self.destination), Decimal("20.00"))

    def test_reversal_requires_reason_and_available_destination_funds(self):
        operation = self.create(
            "CASH-IN-REV",
            CashboxOperationType.DIRECT_IN,
            destination_cashbox=self.destination,
            amount=Decimal("25.00"),
        )
        self.create(
            "CASH-SPEND",
            CashboxOperationType.DIRECT_OUT,
            source_cashbox=self.destination,
            amount=Decimal("45.00"),
        )
        with self.assertRaisesMessage(ValidationError, "cannot become negative"):
            cancel_cashbox_operation(
                operation.pk, self.operation_date, "Reverse cash in", self.accountant
            )
        with self.assertRaisesMessage(ValidationError, "reason"):
            cancel_cashbox_operation(operation.pk, self.operation_date, "", self.accountant)

    def test_closed_period_rejects_operation(self):
        self.period.status = PeriodStatus.CLOSED
        self.period.closed_at = timezone.now()
        self.period.save(update_fields=["status", "closed_at"])
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            self.create(
                "CASH-CLOSED",
                CashboxOperationType.DIRECT_OUT,
                source_cashbox=self.source,
            )
        self.assertFalse(CashboxOperation.objects.exists())
