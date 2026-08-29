from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from closing.models import Period, PeriodStatus
from hesba_testing.factories import (
    make_cashbox,
    make_cashbox_movement,
    make_customer,
    make_seeded_role,
    make_supplier,
    make_user,
    make_user_profile,
)
from permissions.models import RoleCode
from purchases.models import SupplierLedgerEntry, SupplierLedgerEntryType
from reports.selectors import cashbox_report, customer_report, supplier_report
from sales.models import CustomerLedgerEntry, CustomerLedgerEntryType

from .models import FinancialAdjustmentStatus, OpeningBalanceTarget
from .services import (
    cancel_opening_balance_adjustment,
    create_opening_balance_adjustment,
)


class OpeningBalanceAdjustmentTests(TestCase):
    def setUp(self):
        self.action_date = date(2026, 2, 15)
        self.period = Period.objects.create(
            period_code="2026",
            name="2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.owner = make_user("opening_owner")
        make_user_profile(self.owner, make_seeded_role(RoleCode.OWNER))
        self.accountant = make_user("opening_accountant")
        make_user_profile(self.accountant, make_seeded_role(RoleCode.ACCOUNTANT))
        self.manager = make_user("opening_manager")
        make_user_profile(self.manager, make_seeded_role(RoleCode.MANAGER))

    def test_customer_adjustment_and_reversal_are_append_only(self):
        customer = make_customer(opening_balance=Decimal("100.00"))
        CustomerLedgerEntry.objects.create(
            customer=customer,
            entry_date=self.action_date,
            entry_type=CustomerLedgerEntryType.SALES_DUE,
            due_increase=Decimal("20.00"),
        )

        adjustment = create_opening_balance_adjustment(
            OpeningBalanceTarget.CUSTOMER,
            customer.pk,
            self.action_date,
            Decimal("25.00"),
            "Correct imported opening balance",
            self.accountant,
        )
        self.assertEqual(customer_report(customer)[0]["balance"], Decimal("145.00"))
        self.assertEqual(adjustment.customer_ledger_entries.count(), 1)

        cancel_opening_balance_adjustment(
            adjustment.pk,
            self.action_date,
            "Correction source was withdrawn",
            self.owner,
        )
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, FinancialAdjustmentStatus.CANCELLED)
        self.assertEqual(customer_report(customer)[0]["balance"], Decimal("120.00"))
        self.assertEqual(adjustment.customer_ledger_entries.count(), 2)
        customer.refresh_from_db()
        self.assertEqual(customer.opening_balance, Decimal("100.00"))

    def test_supplier_negative_adjustment_uses_ledger_decrease(self):
        supplier = make_supplier(opening_balance=Decimal("100.00"))
        SupplierLedgerEntry.objects.create(
            supplier=supplier,
            entry_date=self.action_date,
            entry_type=SupplierLedgerEntryType.PURCHASE_DUE,
            due_increase=Decimal("5.00"),
        )
        adjustment = create_opening_balance_adjustment(
            OpeningBalanceTarget.SUPPLIER,
            supplier.pk,
            self.action_date,
            Decimal("-10.00"),
            "Supplier confirmation",
            self.owner,
        )
        entry = adjustment.supplier_ledger_entries.get()
        self.assertEqual(entry.due_decrease, Decimal("10.00"))
        self.assertEqual(supplier_report(supplier)[0]["balance"], Decimal("95.00"))

    def test_cashbox_adjustment_and_reversal_use_real_movements(self):
        cashbox = make_cashbox(opening_balance=Decimal("100.00"))
        make_cashbox_movement(cashbox, "in", "20.00")
        adjustment = create_opening_balance_adjustment(
            OpeningBalanceTarget.CASHBOX,
            cashbox.pk,
            self.action_date,
            Decimal("-15.00"),
            "Counted opening cash",
            self.accountant,
        )
        self.assertEqual(cashbox_report(cashbox)[0]["balance"], Decimal("105.00"))
        cancel_opening_balance_adjustment(
            adjustment.pk,
            self.action_date,
            "Recount confirmed original amount",
            self.accountant,
        )
        self.assertEqual(cashbox_report(cashbox)[0]["balance"], Decimal("120.00"))
        self.assertEqual(adjustment.cashbox_movements.count(), 2)

    def test_manager_cannot_adjust_opening_balances(self):
        customer = make_customer()
        CustomerLedgerEntry.objects.create(
            customer=customer,
            entry_date=self.action_date,
            entry_type=CustomerLedgerEntryType.SALES_DUE,
            due_increase=Decimal("1.00"),
        )
        with self.assertRaises(PermissionDenied):
            create_opening_balance_adjustment(
                OpeningBalanceTarget.CUSTOMER,
                customer.pk,
                self.action_date,
                Decimal("1.00"),
                "Not allowed",
                self.manager,
            )

    def test_unused_target_must_be_edited_directly(self):
        customer = make_customer()
        with self.assertRaisesMessage(ValidationError, "no operational use"):
            create_opening_balance_adjustment(
                OpeningBalanceTarget.CUSTOMER,
                customer.pk,
                self.action_date,
                Decimal("1.00"),
                "Premature adjustment",
                self.owner,
            )

    def test_closed_period_rejects_adjustment(self):
        self.period.status = PeriodStatus.CLOSED
        self.period.closed_at = timezone.now()
        self.period.save(update_fields=["status", "closed_at"])
        cashbox = make_cashbox()
        make_cashbox_movement(cashbox, "in", "10.00")
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            create_opening_balance_adjustment(
                OpeningBalanceTarget.CASHBOX,
                cashbox.pk,
                self.action_date,
                Decimal("1.00"),
                "Closed period",
                self.owner,
            )
