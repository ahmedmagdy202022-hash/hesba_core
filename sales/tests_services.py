from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement, CashboxMovementType
from hesba_testing.factories import (
    DEFAULT_DATE,
    add_sales_line,
    make_cashbox,
    make_customer,
    make_draft_sales_invoice,
    make_item,
    make_location,
    make_user,
    posted_invoice_ready,
    recalculate_invoice_totals,
    stock_in,
)
from inventory.models import StockMovement, StockMovementType
from inventory.services import get_item_location_stock_quantity
from sales.models import (
    CustomerLedgerEntry,
    CustomerLedgerEntryType,
    CustomerPayment,
    CustomerPaymentStatus,
    SalesInvoice,
    SalesInvoiceStatus,
)
from sales.services import (
    cancel_customer_payment,
    cancel_posted_sales_invoice,
    post_sales_invoice,
    record_customer_payment,
)


class PostSalesInvoiceGuardTests(TestCase):
    def test_posting_a_posted_invoice_is_rejected(self):
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)

        with self.assertRaises(ValidationError):
            post_sales_invoice(invoice.pk)

    def test_posting_a_cancelled_invoice_is_rejected(self):
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)
        cancel_posted_sales_invoice(invoice.pk)

        with self.assertRaises(ValidationError):
            post_sales_invoice(invoice.pk)

    def test_posting_an_invoice_without_lines_is_rejected(self):
        invoice = make_draft_sales_invoice()

        with self.assertRaises(ValidationError):
            post_sales_invoice(invoice.pk)

    def test_posting_without_enough_stock_is_rejected(self):
        location = make_location()
        item = make_item()
        stock_in(item, location, "1", "5.00")
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, quantity=5, unit_sale_price="10.00")

        with self.assertRaises(ValidationError):
            post_sales_invoice(invoice.pk)

    def test_a_rejected_posting_leaves_no_side_effects(self):
        location = make_location()
        item = make_item()
        stock_in(item, location, "1", "5.00")
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, quantity=5, unit_sale_price="10.00")

        with self.assertRaises(ValidationError):
            post_sales_invoice(invoice.pk)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SalesInvoiceStatus.DRAFT)
        self.assertEqual(CustomerLedgerEntry.objects.count(), 0)
        self.assertEqual(CashboxMovement.objects.count(), 0)
        self.assertEqual(
            StockMovement.objects.filter(movement_type=StockMovementType.SALE_OUT).count(),
            0,
        )

    def test_stock_is_not_required_for_an_untracked_item(self):
        location = make_location()
        item = make_item(is_stock_tracked=False)
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, quantity=99, unit_sale_price="10.00")

        posted = post_sales_invoice(invoice.pk)

        self.assertEqual(posted.status, SalesInvoiceStatus.POSTED)

    def test_stock_is_checked_at_the_selling_location_only(self):
        selling = make_location()
        elsewhere = make_location(location_code="BRANCH", name_ar="فرع")
        item = make_item()
        stock_in(item, elsewhere, "50", "5.00")
        invoice = make_draft_sales_invoice(location=selling)
        add_sales_line(invoice, item, quantity=2, unit_sale_price="10.00")

        with self.assertRaises(ValidationError):
            post_sales_invoice(invoice.pk)


class PostSalesInvoiceEffectTests(TestCase):
    def test_status_becomes_posted(self):
        invoice, _, _, _ = posted_invoice_ready()

        posted = post_sales_invoice(invoice.pk)

        self.assertEqual(posted.status, SalesInvoiceStatus.POSTED)
        self.assertEqual(
            SalesInvoice.objects.get(pk=invoice.pk).status, SalesInvoiceStatus.POSTED
        )

    def test_credit_sale_creates_a_customer_due_entry(self):
        invoice, _, _, _ = posted_invoice_ready(paid_now="0.00")

        post_sales_invoice(invoice.pk)

        entry = CustomerLedgerEntry.objects.get()
        self.assertEqual(entry.entry_type, CustomerLedgerEntryType.SALES_DUE)
        self.assertEqual(entry.due_increase, Decimal("60.00"))
        self.assertEqual(entry.due_decrease, Decimal("0"))
        self.assertEqual(entry.sales_invoice_id, invoice.pk)
        self.assertEqual(entry.entry_date, DEFAULT_DATE)

    def test_fully_paid_sale_creates_no_ledger_entry(self):
        invoice, _, _, _ = posted_invoice_ready(paid_now="60.00")

        post_sales_invoice(invoice.pk)

        self.assertEqual(CustomerLedgerEntry.objects.count(), 0)

    def test_payment_creates_a_cash_in_movement(self):
        invoice, _, _, cashbox = posted_invoice_ready(paid_now="60.00")

        post_sales_invoice(invoice.pk)

        movement = CashboxMovement.objects.get()
        self.assertEqual(movement.cashbox_id, cashbox.pk)
        self.assertEqual(movement.direction, CashboxDirection.IN)
        self.assertEqual(movement.movement_type, CashboxMovementType.SALES_RECEIPT)
        self.assertEqual(movement.amount, Decimal("60.00"))

    def test_credit_sale_creates_no_cash_movement(self):
        invoice, _, _, _ = posted_invoice_ready(paid_now="0.00")

        post_sales_invoice(invoice.pk)

        self.assertEqual(CashboxMovement.objects.count(), 0)

    def test_partial_payment_creates_both_a_due_entry_and_a_cash_movement(self):
        invoice, _, _, _ = posted_invoice_ready(paid_now="20.00")

        post_sales_invoice(invoice.pk)

        self.assertEqual(CustomerLedgerEntry.objects.get().due_increase, Decimal("40.00"))
        self.assertEqual(CashboxMovement.objects.get().amount, Decimal("20.00"))

    def test_line_cost_and_profit_are_stored_from_the_average_cost(self):
        invoice, _, _, _ = posted_invoice_ready(unit_cost="5.00")

        post_sales_invoice(invoice.pk)

        line = invoice.lines.get()
        self.assertEqual(line.unit_cost, Decimal("5.00"))
        self.assertEqual(line.line_cost_amount, Decimal("10.00"))
        self.assertEqual(line.line_profit_amount, Decimal("50.00"))

    def test_profit_is_line_total_minus_line_cost(self):
        invoice, _, _, _ = posted_invoice_ready(unit_cost="5.00")

        post_sales_invoice(invoice.pk)

        line = invoice.lines.get()
        self.assertEqual(
            line.line_profit_amount, line.line_total_amount - line.line_cost_amount
        )

    def test_an_untracked_item_is_costed_at_zero(self):
        location = make_location()
        item = make_item(is_stock_tracked=False, average_cost=Decimal("9.00"))
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, quantity=2, unit_sale_price="10.00")

        post_sales_invoice(invoice.pk)

        line = invoice.lines.get()
        self.assertEqual(line.unit_cost, Decimal("0"))
        self.assertEqual(line.line_cost_amount, Decimal("0"))

    def test_a_stale_average_cost_is_ignored_in_favour_of_movements(self):
        location = make_location()
        item = make_item(average_cost=Decimal("0.00"))
        stock_in(item, location, "10", "5.00")  # deliberately not recalculated
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, quantity=2, unit_sale_price="30.00")

        post_sales_invoice(invoice.pk)

        line = invoice.lines.get()
        self.assertEqual(line.unit_cost, Decimal("5.0000"))
        self.assertEqual(line.line_cost_amount, Decimal("10.00"))
        self.assertEqual(line.line_profit_amount, Decimal("50.00"))

    def test_stock_leaves_the_selling_location(self):
        invoice, item, location, _ = posted_invoice_ready(stock_quantity=10)

        post_sales_invoice(invoice.pk)

        movement = StockMovement.objects.get(movement_type=StockMovementType.SALE_OUT)
        self.assertEqual(movement.item_id, item.pk)
        self.assertEqual(movement.location_id, location.pk)
        self.assertEqual(movement.quantity, Decimal("2"))
        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("8"))

    def test_no_stock_movement_for_an_untracked_item(self):
        location = make_location()
        item = make_item(is_stock_tracked=False)
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, quantity=2, unit_sale_price="10.00")

        post_sales_invoice(invoice.pk)

        self.assertEqual(StockMovement.objects.count(), 0)

    def test_posting_writes_an_audit_log(self):
        invoice, _, _, _ = posted_invoice_ready()
        user = make_user()

        post_sales_invoice(invoice.pk, user=user)

        log = AuditLog.objects.get(action="post_sales_invoice")
        self.assertEqual(log.event_type, AuditEventType.UPDATE)
        self.assertEqual(log.module, "sales")
        self.assertEqual(log.object_type, "SalesInvoice")
        self.assertEqual(log.object_id, str(invoice.pk))
        self.assertEqual(log.actor_id, user.pk)
        self.assertEqual(log.after_data["status"], SalesInvoiceStatus.POSTED)

    def test_the_acting_user_is_recorded_on_every_row(self):
        invoice, _, _, _ = posted_invoice_ready(paid_now="20.00")
        user = make_user()

        post_sales_invoice(invoice.pk, user=user)

        self.assertEqual(CustomerLedgerEntry.objects.get().created_by_id, user.pk)
        self.assertEqual(CashboxMovement.objects.get().created_by_id, user.pk)
        self.assertEqual(StockMovement.objects.get(
            movement_type=StockMovementType.SALE_OUT
        ).created_by_id, user.pk)

    def test_posting_without_a_user_is_allowed(self):
        invoice, _, _, _ = posted_invoice_ready(paid_now="20.00")

        post_sales_invoice(invoice.pk)

        self.assertIsNone(CustomerLedgerEntry.objects.get().created_by_id)
        self.assertIsNone(AuditLog.objects.get(action="post_sales_invoice").actor_id)

    def test_multiple_lines_are_each_costed_and_moved(self):
        location = make_location()
        cashbox = make_cashbox()
        first = make_item(item_code="ITEM-A")
        second = make_item(item_code="ITEM-B")
        stock_in(first, location, "10", "2.00")
        stock_in(second, location, "10", "4.00")
        from inventory.services import recalculate_item_average_cost

        recalculate_item_average_cost(first)
        recalculate_item_average_cost(second)

        invoice = make_draft_sales_invoice(location=location, cashbox=cashbox)
        add_sales_line(invoice, first, quantity=1, unit_sale_price="10.00")
        add_sales_line(invoice, second, quantity=2, unit_sale_price="10.00")
        recalculate_invoice_totals(invoice)

        post_sales_invoice(invoice.pk)

        self.assertEqual(
            StockMovement.objects.filter(
                movement_type=StockMovementType.SALE_OUT
            ).count(),
            2,
        )
        self.assertEqual(invoice.lines.get(item=first).line_cost_amount, Decimal("2.00"))
        self.assertEqual(invoice.lines.get(item=second).line_cost_amount, Decimal("8.00"))


class CancelPostedSalesInvoiceTests(TestCase):
    def test_cancelling_a_draft_invoice_is_rejected(self):
        invoice, _, _, _ = posted_invoice_ready()

        with self.assertRaises(ValidationError):
            cancel_posted_sales_invoice(invoice.pk)

    def test_cancelling_twice_is_rejected(self):
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)
        cancel_posted_sales_invoice(invoice.pk)

        with self.assertRaises(ValidationError):
            cancel_posted_sales_invoice(invoice.pk)

    def test_status_becomes_cancelled(self):
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)

        cancelled = cancel_posted_sales_invoice(invoice.pk)

        self.assertEqual(cancelled.status, SalesInvoiceStatus.CANCELLED)

    def test_credit_sale_due_is_reversed(self):
        invoice, _, _, _ = posted_invoice_ready(paid_now="0.00")
        post_sales_invoice(invoice.pk)

        cancel_posted_sales_invoice(invoice.pk)

        reversal = CustomerLedgerEntry.objects.get(
            entry_type=CustomerLedgerEntryType.SALES_RETURN
        )
        self.assertEqual(reversal.due_decrease, Decimal("60.00"))
        self.assertEqual(reversal.due_increase, Decimal("0"))

    def test_customer_balance_returns_to_zero_after_cancelling(self):
        from reports.services import get_customer_balance

        invoice, _, _, _ = posted_invoice_ready(paid_now="0.00")
        post_sales_invoice(invoice.pk)
        cancel_posted_sales_invoice(invoice.pk)

        invoice.refresh_from_db()
        self.assertEqual(get_customer_balance(invoice.customer), Decimal("0"))

    def test_payment_is_reversed_out_of_the_cashbox(self):
        invoice, _, _, cashbox = posted_invoice_ready(paid_now="60.00")
        post_sales_invoice(invoice.pk)

        cancel_posted_sales_invoice(invoice.pk)

        reversal = CashboxMovement.objects.get(direction=CashboxDirection.OUT)
        self.assertEqual(reversal.amount, Decimal("60.00"))
        self.assertEqual(reversal.movement_type, CashboxMovementType.ADJUSTMENT)
        self.assertEqual(reversal.cashbox_id, cashbox.pk)

    def test_cashbox_balance_returns_to_zero_after_cancelling(self):
        from reports.services import get_cashbox_balance

        invoice, _, _, cashbox = posted_invoice_ready(paid_now="60.00")
        post_sales_invoice(invoice.pk)
        cancel_posted_sales_invoice(invoice.pk)

        self.assertEqual(get_cashbox_balance(cashbox), Decimal("0"))

    def test_stock_is_returned_to_the_selling_location(self):
        invoice, item, location, _ = posted_invoice_ready(stock_quantity=10)
        post_sales_invoice(invoice.pk)
        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("8"))

        cancel_posted_sales_invoice(invoice.pk)

        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("10"))
        self.assertTrue(
            StockMovement.objects.filter(
                movement_type=StockMovementType.SALE_RETURN_IN
            ).exists()
        )

    def test_cancelling_writes_an_audit_log_with_the_reason(self):
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)

        cancel_posted_sales_invoice(invoice.pk, reason="customer returned goods")

        log = AuditLog.objects.get(action="cancel_posted_sales_invoice")
        self.assertEqual(log.reason, "customer returned goods")
        self.assertEqual(log.after_data["status"], SalesInvoiceStatus.CANCELLED)


class RecordCustomerPaymentTests(TestCase):
    def setUp(self):
        super().setUp()
        self.customer = make_customer()
        self.cashbox = make_cashbox()

    def record(self, amount="100.00", **kwargs):
        return record_customer_payment(
            payment_number=kwargs.pop("payment_number", "CP-001"),
            payment_date=kwargs.pop("payment_date", DEFAULT_DATE),
            customer=kwargs.pop("customer", self.customer),
            cashbox=kwargs.pop("cashbox", self.cashbox),
            amount=Decimal(amount),
            **kwargs,
        )

    def test_payment_is_created_and_posted(self):
        payment = self.record()

        self.assertEqual(CustomerPayment.objects.count(), 1)
        self.assertEqual(payment.status, CustomerPaymentStatus.POSTED)
        self.assertEqual(payment.amount, Decimal("100.00"))

    def test_payment_lowers_the_customer_due(self):
        self.record()

        entry = CustomerLedgerEntry.objects.get()
        self.assertEqual(entry.entry_type, CustomerLedgerEntryType.CUSTOMER_PAYMENT)
        self.assertEqual(entry.due_decrease, Decimal("100.00"))
        self.assertEqual(entry.due_increase, Decimal("0"))

    def test_payment_moves_cash_in(self):
        self.record()

        movement = CashboxMovement.objects.get()
        self.assertEqual(movement.direction, CashboxDirection.IN)
        self.assertEqual(movement.movement_type, CashboxMovementType.CUSTOMER_PAYMENT)
        self.assertEqual(movement.amount, Decimal("100.00"))

    def test_payment_touches_no_stock(self):
        self.record()

        self.assertEqual(StockMovement.objects.count(), 0)

    def test_payment_writes_an_audit_log(self):
        payment = self.record()

        log = AuditLog.objects.get(action="record_customer_payment")
        self.assertEqual(log.event_type, AuditEventType.CREATE)
        self.assertEqual(log.object_id, str(payment.pk))

    def test_payment_records_the_acting_user(self):
        user = make_user()

        payment = self.record(user=user)

        self.assertEqual(payment.created_by_id, user.pk)
        self.assertEqual(CashboxMovement.objects.get().created_by_id, user.pk)


class CancelCustomerPaymentTests(TestCase):
    def setUp(self):
        super().setUp()
        self.customer = make_customer()
        self.cashbox = make_cashbox()
        self.payment = record_customer_payment(
            payment_number="CP-001",
            payment_date=DEFAULT_DATE,
            customer=self.customer,
            cashbox=self.cashbox,
            amount=Decimal("100.00"),
        )

    def test_status_becomes_cancelled(self):
        cancelled = cancel_customer_payment(self.payment.pk)

        self.assertEqual(cancelled.status, CustomerPaymentStatus.CANCELLED)

    def test_cancelling_twice_is_rejected(self):
        cancel_customer_payment(self.payment.pk)

        with self.assertRaises(ValidationError):
            cancel_customer_payment(self.payment.pk)

    def test_cancelling_restores_the_customer_due(self):
        cancel_customer_payment(self.payment.pk)

        reversal = CustomerLedgerEntry.objects.get(
            entry_type=CustomerLedgerEntryType.ADJUSTMENT
        )
        self.assertEqual(reversal.due_increase, Decimal("100.00"))

    def test_cancelling_moves_the_cash_back_out(self):
        cancel_customer_payment(self.payment.pk)

        reversal = CashboxMovement.objects.get(direction=CashboxDirection.OUT)
        self.assertEqual(reversal.amount, Decimal("100.00"))
        self.assertEqual(reversal.movement_type, CashboxMovementType.ADJUSTMENT)

    def test_balances_net_to_zero_after_cancelling(self):
        from reports.services import get_cashbox_balance, get_customer_balance

        cancel_customer_payment(self.payment.pk)

        self.assertEqual(get_customer_balance(self.customer), Decimal("0"))
        self.assertEqual(get_cashbox_balance(self.cashbox), Decimal("0"))

    def test_cancelling_writes_an_audit_log_with_the_reason(self):
        cancel_customer_payment(self.payment.pk, reason="entered twice")

        log = AuditLog.objects.get(action="cancel_customer_payment")
        self.assertEqual(log.reason, "entered twice")
        self.assertEqual(log.after_data["status"], CustomerPaymentStatus.CANCELLED)


class CancelSalesInvoiceEdgeTests(TestCase):
    def test_cancelling_an_invoice_whose_lines_were_removed_is_rejected(self):
        # Tracked lines are protected by their stock movements, so use an
        # untracked item: posting it leaves no movement holding the line.
        location = make_location()
        item = make_item(is_stock_tracked=False)
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, quantity=1, unit_sale_price="10.00")
        post_sales_invoice(invoice.pk)
        invoice.lines.all().delete()

        with self.assertRaises(ValidationError):
            cancel_posted_sales_invoice(invoice.pk)

    def test_cancelling_an_untracked_item_moves_no_stock_back(self):
        location = make_location()
        item = make_item(is_stock_tracked=False)
        invoice = make_draft_sales_invoice(location=location)
        add_sales_line(invoice, item, quantity=2, unit_sale_price="10.00")
        post_sales_invoice(invoice.pk)

        cancelled = cancel_posted_sales_invoice(invoice.pk)

        self.assertEqual(cancelled.status, SalesInvoiceStatus.CANCELLED)
        self.assertEqual(StockMovement.objects.count(), 0)
