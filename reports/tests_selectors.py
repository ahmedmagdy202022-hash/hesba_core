from datetime import date
from decimal import Decimal

from django.test import TestCase

from cashboxes.models import CashboxDirection
from hesba_testing.factories import (
    DEFAULT_DATE,
    add_sales_line,
    make_cashbox,
    make_cashbox_movement,
    make_customer,
    make_draft_sales_invoice,
    make_item,
    make_location,
    make_supplier,
    posted_invoice_ready,
    stock_in,
)
from inventory.models import StockMovementType
from master_data.models import Item
from reports.selectors import (
    cashbox_report,
    customer_report,
    profit_report,
    purchase_report,
    sales_report,
    stock_report,
    supplier_report,
)
from reports.tests_services import customer_entry, supplier_entry
from sales.services import post_sales_invoice


class StockReportTests(TestCase):
    def setUp(self):
        super().setUp()
        self.item = make_item()
        self.main = make_location()

    def test_report_is_empty_without_movements(self):
        self.assertEqual(stock_report(), [])

    def test_zero_quantity_rows_are_omitted(self):
        stock_in(self.item, self.main, "5")
        from hesba_testing.factories import make_stock_movement

        make_stock_movement(self.item, self.main, StockMovementType.SALE_OUT, "5")

        self.assertEqual(stock_report(), [])

    def test_row_reports_quantity_and_value(self):
        Item.objects.filter(pk=self.item.pk).update(average_cost=Decimal("3.00"))
        stock_in(self.item, self.main, "4")

        row = stock_report()[0]

        self.assertEqual(row["item_id"], self.item.pk)
        self.assertEqual(row["location_id"], self.main.pk)
        self.assertEqual(row["quantity"], Decimal("4"))
        self.assertEqual(row["stock_value"], Decimal("12.00"))

    def test_inactive_items_are_excluded(self):
        stock_in(self.item, self.main, "4")
        Item.objects.filter(pk=self.item.pk).update(active=False)

        self.assertEqual(stock_report(), [])

    def test_one_row_per_item_and_location(self):
        branch = make_location(location_code="BRANCH", name_ar="فرع")
        stock_in(self.item, self.main, "4")
        stock_in(self.item, branch, "6")

        self.assertEqual(len(stock_report()), 2)

    def test_location_argument_narrows_the_report(self):
        branch = make_location(location_code="BRANCH", name_ar="فرع")
        stock_in(self.item, self.main, "4")
        stock_in(self.item, branch, "6")

        rows = stock_report(location=branch)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["location_id"], branch.pk)


class CustomerReportTests(TestCase):
    def test_a_customer_with_no_entries_still_appears(self):
        customer = make_customer()

        rows = customer_report()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["customer_id"], customer.pk)
        self.assertEqual(rows[0]["balance"], Decimal("0.00"))

    def test_balance_combines_opening_balance_and_entries(self):
        customer = make_customer(opening_balance=Decimal("100.00"))
        customer_entry(customer, increase="50.00")
        customer_entry(customer, decrease="20.00")

        row = customer_report()[0]

        self.assertEqual(row["due_increase"], Decimal("50.00"))
        self.assertEqual(row["due_decrease"], Decimal("20.00"))
        self.assertEqual(row["balance"], Decimal("130.00"))

    def test_inactive_customers_are_excluded(self):
        make_customer(active=False)

        self.assertEqual(customer_report(), [])

    def test_customer_argument_narrows_the_report(self):
        wanted = make_customer()
        make_customer(customer_code="CUST-002")

        rows = customer_report(customer=wanted)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["customer_id"], wanted.pk)


class SupplierReportTests(TestCase):
    def test_a_supplier_with_no_entries_still_appears(self):
        supplier = make_supplier()

        rows = supplier_report()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["supplier_id"], supplier.pk)

    def test_balance_combines_opening_balance_and_entries(self):
        supplier = make_supplier(opening_balance=Decimal("200.00"))
        supplier_entry(supplier, increase="100.00")
        supplier_entry(supplier, decrease="40.00")

        row = supplier_report()[0]

        self.assertEqual(row["balance"], Decimal("260.00"))

    def test_inactive_suppliers_are_excluded(self):
        make_supplier(active=False)

        self.assertEqual(supplier_report(), [])

    def test_supplier_argument_narrows_the_report(self):
        wanted = make_supplier()
        make_supplier(supplier_code="SUP-002")

        rows = supplier_report(supplier=wanted)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["supplier_id"], wanted.pk)


class CashboxReportTests(TestCase):
    def setUp(self):
        super().setUp()
        self.cashbox = make_cashbox(opening_balance=Decimal("50.00"))

    def test_balance_combines_opening_balance_and_movements(self):
        make_cashbox_movement(self.cashbox, CashboxDirection.IN, "100.00")
        make_cashbox_movement(self.cashbox, CashboxDirection.OUT, "30.00")

        row = cashbox_report()[0]

        self.assertEqual(row["cash_in"], Decimal("100.00"))
        self.assertEqual(row["cash_out"], Decimal("30.00"))
        self.assertEqual(row["balance"], Decimal("120.00"))

    def test_inactive_cashboxes_are_excluded(self):
        make_cashbox(cashbox_code="CASH-OFF", active=False)

        rows = cashbox_report()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cashbox_id"], self.cashbox.pk)

    def test_date_from_excludes_earlier_movements(self):
        make_cashbox_movement(
            self.cashbox, CashboxDirection.IN, "100.00", movement_date=date(2026, 1, 1)
        )
        make_cashbox_movement(
            self.cashbox, CashboxDirection.IN, "20.00", movement_date=date(2026, 2, 1)
        )

        row = cashbox_report(date_from=date(2026, 2, 1))[0]

        self.assertEqual(row["cash_in"], Decimal("20.00"))

    def test_date_to_excludes_later_movements(self):
        make_cashbox_movement(
            self.cashbox, CashboxDirection.IN, "100.00", movement_date=date(2026, 1, 1)
        )
        make_cashbox_movement(
            self.cashbox, CashboxDirection.IN, "20.00", movement_date=date(2026, 2, 1)
        )

        row = cashbox_report(date_to=date(2026, 1, 31))[0]

        self.assertEqual(row["cash_in"], Decimal("100.00"))

    def test_date_bounds_are_inclusive(self):
        make_cashbox_movement(
            self.cashbox, CashboxDirection.IN, "100.00", movement_date=date(2026, 1, 15)
        )

        row = cashbox_report(date_from=date(2026, 1, 15), date_to=date(2026, 1, 15))[0]

        self.assertEqual(row["cash_in"], Decimal("100.00"))

    def test_opening_balance_is_not_affected_by_date_filters(self):
        row = cashbox_report(date_from=date(2030, 1, 1))[0]

        self.assertEqual(row["balance"], Decimal("50.00"))


class SalesAndPurchaseReportTests(TestCase):
    def test_sales_report_is_empty_without_invoices(self):
        self.assertEqual(list(sales_report()), [])

    def test_sales_report_includes_drafts(self):
        invoice = make_draft_sales_invoice()

        self.assertEqual([row.pk for row in sales_report()], [invoice.pk])

    def test_sales_report_orders_newest_first(self):
        older = make_draft_sales_invoice(
            invoice_number="SI-OLD", invoice_date=date(2026, 1, 1)
        )
        newer = make_draft_sales_invoice(
            invoice_number="SI-NEW",
            invoice_date=date(2026, 3, 1),
            customer=make_customer(customer_code="CUST-002"),
        )

        self.assertEqual([row.pk for row in sales_report()], [newer.pk, older.pk])

    def test_sales_report_date_filters_apply(self):
        make_draft_sales_invoice(invoice_number="SI-OLD", invoice_date=date(2026, 1, 1))
        wanted = make_draft_sales_invoice(
            invoice_number="SI-NEW",
            invoice_date=date(2026, 3, 1),
            customer=make_customer(customer_code="CUST-002"),
        )

        rows = sales_report(date_from=date(2026, 2, 1))

        self.assertEqual([row.pk for row in rows], [wanted.pk])

    def test_purchase_report_is_empty_without_invoices(self):
        self.assertEqual(list(purchase_report()), [])


class ProfitReportTests(TestCase):
    def test_report_is_empty_without_posted_invoices(self):
        self.assertEqual(profit_report(), [])

    def test_draft_invoice_lines_are_excluded(self):
        posted_invoice_ready()

        self.assertEqual(profit_report(), [])

    def test_row_reports_sales_cost_and_profit(self):
        invoice, item, _, _ = posted_invoice_ready(unit_cost="5.00")
        post_sales_invoice(invoice.pk)

        row = profit_report()[0]

        self.assertEqual(row["invoice_id"], invoice.pk)
        self.assertEqual(row["item_id"], item.pk)
        self.assertEqual(row["quantity"], Decimal("2"))
        self.assertEqual(row["sales_amount"], Decimal("60.00"))
        self.assertEqual(row["cost_amount"], Decimal("10.00"))
        self.assertEqual(row["profit_amount"], Decimal("50.00"))

    def test_date_filters_apply_to_the_invoice_date(self):
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)

        self.assertEqual(profit_report(date_from=date(2030, 1, 1)), [])
        self.assertEqual(len(profit_report(date_to=DEFAULT_DATE)), 1)

    def test_one_row_per_invoice_line(self):
        location = make_location()
        cashbox = make_cashbox()
        first = make_item(item_code="ITEM-A")
        second = make_item(item_code="ITEM-B")
        stock_in(first, location, "10", "1.00")
        stock_in(second, location, "10", "1.00")

        invoice = make_draft_sales_invoice(location=location, cashbox=cashbox)
        add_sales_line(invoice, first, quantity=1, unit_sale_price="10.00")
        add_sales_line(invoice, second, quantity=1, unit_sale_price="10.00")
        post_sales_invoice(invoice.pk)

        self.assertEqual(len(profit_report()), 2)


class CashboxReportFilterTests(TestCase):
    def test_cashbox_argument_narrows_the_report(self):
        wanted = make_cashbox()
        make_cashbox(cashbox_code="CASH-002")

        rows = cashbox_report(cashbox=wanted)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cashbox_id"], wanted.pk)
