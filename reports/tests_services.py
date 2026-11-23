from decimal import Decimal

from django.test import TestCase

from cashboxes.models import CashboxDirection
from hesba_testing.factories import (
    DEFAULT_DATE,
    make_cashbox,
    make_cashbox_movement,
    make_customer,
    make_item,
    make_location,
    make_supplier,
    posted_invoice_ready,
    stock_in,
)
from purchases.models import SupplierLedgerEntry, SupplierLedgerEntryType
from reports.services import (
    get_cashbox_balance,
    get_customer_balance,
    get_item_location_stock,
    get_local_controlled_cycle_snapshot,
    get_profit_summary,
    get_supplier_balance,
)
from sales.models import CustomerLedgerEntry, CustomerLedgerEntryType
from sales.services import post_sales_invoice


def supplier_entry(supplier, increase="0.00", decrease="0.00"):
    return SupplierLedgerEntry.objects.create(
        supplier=supplier,
        entry_date=DEFAULT_DATE,
        entry_type=SupplierLedgerEntryType.PURCHASE_DUE,
        due_increase=Decimal(increase),
        due_decrease=Decimal(decrease),
    )


def customer_entry(customer, increase="0.00", decrease="0.00"):
    return CustomerLedgerEntry.objects.create(
        customer=customer,
        entry_date=DEFAULT_DATE,
        entry_type=CustomerLedgerEntryType.SALES_DUE,
        due_increase=Decimal(increase),
        due_decrease=Decimal(decrease),
    )


class SupplierBalanceTests(TestCase):
    def test_balance_of_a_new_supplier_is_zero(self):
        self.assertEqual(get_supplier_balance(make_supplier()), Decimal("0"))

    def test_balance_starts_from_the_opening_balance(self):
        supplier = make_supplier(opening_balance=Decimal("250.00"))

        self.assertEqual(get_supplier_balance(supplier), Decimal("250.00"))

    def test_due_increase_raises_the_balance(self):
        supplier = make_supplier()
        supplier_entry(supplier, increase="100.00")

        self.assertEqual(get_supplier_balance(supplier), Decimal("100.00"))

    def test_due_decrease_lowers_the_balance(self):
        supplier = make_supplier()
        supplier_entry(supplier, increase="100.00")
        supplier_entry(supplier, decrease="40.00")

        self.assertEqual(get_supplier_balance(supplier), Decimal("60.00"))

    def test_balance_combines_opening_balance_and_entries(self):
        supplier = make_supplier(opening_balance=Decimal("500.00"))
        supplier_entry(supplier, increase="200.00")
        supplier_entry(supplier, decrease="300.00")

        self.assertEqual(get_supplier_balance(supplier), Decimal("400.00"))

    def test_overpayment_produces_a_negative_balance(self):
        supplier = make_supplier()
        supplier_entry(supplier, decrease="75.00")

        self.assertEqual(get_supplier_balance(supplier), Decimal("-75.00"))

    def test_another_suppliers_entries_are_ignored(self):
        supplier = make_supplier()
        supplier_entry(make_supplier(supplier_code="SUP-002"), increase="999.00")

        self.assertEqual(get_supplier_balance(supplier), Decimal("0"))


class CustomerBalanceTests(TestCase):
    def test_balance_of_a_new_customer_is_zero(self):
        self.assertEqual(get_customer_balance(make_customer()), Decimal("0"))

    def test_balance_starts_from_the_opening_balance(self):
        customer = make_customer(opening_balance=Decimal("120.00"))

        self.assertEqual(get_customer_balance(customer), Decimal("120.00"))

    def test_due_increase_raises_and_decrease_lowers_the_balance(self):
        customer = make_customer()
        customer_entry(customer, increase="300.00")
        customer_entry(customer, decrease="100.00")

        self.assertEqual(get_customer_balance(customer), Decimal("200.00"))

    def test_overpayment_produces_a_negative_balance(self):
        customer = make_customer()
        customer_entry(customer, decrease="50.00")

        self.assertEqual(get_customer_balance(customer), Decimal("-50.00"))

    def test_another_customers_entries_are_ignored(self):
        customer = make_customer()
        customer_entry(make_customer(customer_code="CUST-002"), increase="999.00")

        self.assertEqual(get_customer_balance(customer), Decimal("0"))


class CashboxBalanceTests(TestCase):
    def test_balance_of_a_new_cashbox_is_zero(self):
        self.assertEqual(get_cashbox_balance(make_cashbox()), Decimal("0"))

    def test_balance_starts_from_the_opening_balance(self):
        cashbox = make_cashbox(opening_balance=Decimal("1000.00"))

        self.assertEqual(get_cashbox_balance(cashbox), Decimal("1000.00"))

    def test_cash_in_raises_and_cash_out_lowers_the_balance(self):
        cashbox = make_cashbox()
        make_cashbox_movement(cashbox, CashboxDirection.IN, "500.00")
        make_cashbox_movement(cashbox, CashboxDirection.OUT, "200.00")

        self.assertEqual(get_cashbox_balance(cashbox), Decimal("300.00"))

    def test_balance_can_go_negative(self):
        cashbox = make_cashbox()
        make_cashbox_movement(cashbox, CashboxDirection.OUT, "40.00")

        self.assertEqual(get_cashbox_balance(cashbox), Decimal("-40.00"))

    def test_another_cashboxs_movements_are_ignored(self):
        cashbox = make_cashbox()
        other = make_cashbox(cashbox_code="CASH-002")
        make_cashbox_movement(other, CashboxDirection.IN, "999.00")

        self.assertEqual(get_cashbox_balance(cashbox), Decimal("0"))


class ItemLocationStockTests(TestCase):
    def test_delegates_to_the_inventory_calculation(self):
        item = make_item()
        location = make_location()
        stock_in(item, location, "12")

        self.assertEqual(get_item_location_stock(item, location), Decimal("12"))

    def test_stock_in_another_location_is_not_counted(self):
        item = make_item()
        location = make_location()
        other = make_location(location_code="BRANCH", name_ar="فرع")
        stock_in(item, other, "12")

        self.assertEqual(get_item_location_stock(item, location), Decimal("0"))


class ProfitSummaryTests(TestCase):
    def test_summary_is_zero_without_invoices(self):
        self.assertEqual(
            get_profit_summary(),
            {
                "total_sales": Decimal("0"),
                "total_cost": Decimal("0"),
                "total_profit": Decimal("0"),
            },
        )

    def test_draft_invoices_are_excluded(self):
        posted_invoice_ready()

        self.assertEqual(get_profit_summary()["total_sales"], Decimal("0"))

    def test_posted_invoice_lines_are_summed(self):
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)

        summary = get_profit_summary()

        # One line: 2 units at 30.00, costed at the 5.00 average from stock.
        self.assertEqual(summary["total_sales"], Decimal("60.00"))
        self.assertEqual(summary["total_cost"], Decimal("10.00"))
        self.assertEqual(summary["total_profit"], Decimal("50.00"))

    def test_profit_equals_sales_minus_cost(self):
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)

        summary = get_profit_summary()

        self.assertEqual(
            summary["total_profit"], summary["total_sales"] - summary["total_cost"]
        )


class LocalControlledCycleSnapshotTests(TestCase):
    """The snapshot is bound to fixed demo codes and raises without them."""

    def test_missing_seed_data_raises(self):
        with self.assertRaises(Exception):
            get_local_controlled_cycle_snapshot()

    def test_snapshot_reports_every_section(self):
        make_supplier(supplier_code="SUP-001")
        make_customer(customer_code="CUST-001")
        item = make_item(item_code="ITEM-001")
        location = make_location(location_code="MAIN")
        make_cashbox(cashbox_code="CASH-001")
        stock_in(item, location, "6")

        snapshot = get_local_controlled_cycle_snapshot()

        self.assertEqual(
            set(snapshot),
            {
                "supplier_balance",
                "customer_balance",
                "cashbox_balance",
                "item_location_stock",
                "supplier_ledger_entries",
                "customer_ledger_entries",
                "cashbox_movements",
                "profit",
            },
        )
        self.assertEqual(snapshot["item_location_stock"], Decimal("6"))
        self.assertEqual(snapshot["supplier_ledger_entries"], 0)
        self.assertEqual(snapshot["profit"]["total_sales"], Decimal("0"))
