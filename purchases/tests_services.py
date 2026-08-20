from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement, CashboxMovementType
from hesba_testing.factories import (
    DEFAULT_DATE,
    add_purchase_line,
    make_cashbox,
    make_draft_purchase_invoice,
    make_item,
    make_location,
    make_supplier,
    make_user,
    purchase_ready,
    recalculate_purchase_totals,
)
from inventory.models import StockMovement, StockMovementType
from inventory.services import get_item_location_stock_quantity
from purchases.models import (
    PurchaseInvoice,
    PurchaseInvoiceStatus,
    SupplierLedgerEntry,
    SupplierLedgerEntryType,
    SupplierPayment,
    SupplierPaymentStatus,
)
from purchases.services import (
    cancel_posted_purchase_invoice,
    cancel_supplier_payment,
    post_purchase_invoice,
    record_supplier_payment,
)
from reports.services import get_cashbox_balance, get_supplier_balance


class PostPurchaseInvoiceGuardTests(TestCase):
    def test_posting_a_posted_invoice_is_rejected(self):
        invoice, _, _, _ = purchase_ready()
        post_purchase_invoice(invoice.pk)

        with self.assertRaises(ValidationError):
            post_purchase_invoice(invoice.pk)

    def test_posting_a_cancelled_invoice_is_rejected(self):
        invoice, _, _, _ = purchase_ready()
        post_purchase_invoice(invoice.pk)
        cancel_posted_purchase_invoice(invoice.pk)

        with self.assertRaises(ValidationError):
            post_purchase_invoice(invoice.pk)

    def test_posting_an_invoice_without_lines_is_rejected(self):
        invoice = make_draft_purchase_invoice()

        with self.assertRaises(ValidationError):
            post_purchase_invoice(invoice.pk)

    def test_a_purchase_needs_no_existing_stock(self):
        """Unlike a sale, a purchase brings stock in, so nothing is on hand."""
        invoice, item, location, _ = purchase_ready()

        posted = post_purchase_invoice(invoice.pk)

        self.assertEqual(posted.status, PurchaseInvoiceStatus.POSTED)
        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("4"))


class PostPurchaseInvoiceEffectTests(TestCase):
    def test_status_becomes_posted(self):
        invoice, _, _, _ = purchase_ready()

        post_purchase_invoice(invoice.pk)

        self.assertEqual(
            PurchaseInvoice.objects.get(pk=invoice.pk).status,
            PurchaseInvoiceStatus.POSTED,
        )

    def test_credit_purchase_creates_a_supplier_due_entry(self):
        invoice, _, _, _ = purchase_ready(paid_now="0.00")

        post_purchase_invoice(invoice.pk)

        entry = SupplierLedgerEntry.objects.get()
        self.assertEqual(entry.entry_type, SupplierLedgerEntryType.PURCHASE_DUE)
        self.assertEqual(entry.due_increase, Decimal("100.00"))
        self.assertEqual(entry.due_decrease, Decimal("0"))
        self.assertEqual(entry.purchase_invoice_id, invoice.pk)

    def test_fully_paid_purchase_creates_no_ledger_entry(self):
        invoice, _, _, _ = purchase_ready(paid_now="100.00")

        post_purchase_invoice(invoice.pk)

        self.assertEqual(SupplierLedgerEntry.objects.count(), 0)

    def test_payment_creates_a_cash_out_movement(self):
        invoice, _, _, cashbox = purchase_ready(paid_now="100.00")

        post_purchase_invoice(invoice.pk)

        movement = CashboxMovement.objects.get()
        self.assertEqual(movement.direction, CashboxDirection.OUT)
        self.assertEqual(movement.movement_type, CashboxMovementType.PURCHASE_PAYMENT)
        self.assertEqual(movement.amount, Decimal("100.00"))
        self.assertEqual(movement.cashbox_id, cashbox.pk)

    def test_credit_purchase_creates_no_cash_movement(self):
        invoice, _, _, _ = purchase_ready(paid_now="0.00")

        post_purchase_invoice(invoice.pk)

        self.assertEqual(CashboxMovement.objects.count(), 0)

    def test_partial_payment_creates_both_a_due_entry_and_a_cash_movement(self):
        invoice, _, _, _ = purchase_ready(paid_now="40.00")

        post_purchase_invoice(invoice.pk)

        self.assertEqual(SupplierLedgerEntry.objects.get().due_increase, Decimal("60.00"))
        self.assertEqual(CashboxMovement.objects.get().amount, Decimal("40.00"))

    def test_stock_arrives_at_the_receiving_location(self):
        invoice, item, location, _ = purchase_ready(quantity=4)

        post_purchase_invoice(invoice.pk)

        movement = StockMovement.objects.get(movement_type=StockMovementType.PURCHASE_IN)
        self.assertEqual(movement.item_id, item.pk)
        self.assertEqual(movement.location_id, location.pk)
        self.assertEqual(movement.quantity, Decimal("4"))
        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("4"))

    def test_unit_cost_is_the_line_total_divided_by_quantity(self):
        invoice, _, _, _ = purchase_ready(quantity=4, unit_purchase_price="25.00")

        post_purchase_invoice(invoice.pk)

        movement = StockMovement.objects.get(movement_type=StockMovementType.PURCHASE_IN)
        self.assertEqual(movement.unit_cost, Decimal("25.00"))

    def test_a_line_discount_lowers_the_unit_cost(self):
        location = make_location()
        item = make_item()
        invoice = make_draft_purchase_invoice(location=location)
        add_purchase_line(
            invoice, item, quantity=4, unit_purchase_price="25.00", discount="20.00"
        )
        recalculate_purchase_totals(invoice)

        post_purchase_invoice(invoice.pk)

        movement = StockMovement.objects.get(movement_type=StockMovementType.PURCHASE_IN)
        self.assertEqual(movement.unit_cost, Decimal("20.00"))

    def test_posting_refreshes_the_item_average_cost(self):
        invoice, item, _, _ = purchase_ready(quantity=4, unit_purchase_price="25.00")

        post_purchase_invoice(invoice.pk)
        item.refresh_from_db()

        self.assertEqual(item.average_cost, Decimal("25.00"))

    def test_no_stock_movement_for_an_untracked_item(self):
        location = make_location()
        item = make_item(is_stock_tracked=False)
        invoice = make_draft_purchase_invoice(location=location)
        add_purchase_line(invoice, item, quantity=2, unit_purchase_price="10.00")
        recalculate_purchase_totals(invoice)

        post_purchase_invoice(invoice.pk)

        self.assertEqual(StockMovement.objects.count(), 0)

    def test_supplier_balance_rises_by_the_amount_owed(self):
        invoice, _, _, _ = purchase_ready(paid_now="0.00")

        post_purchase_invoice(invoice.pk)
        invoice.refresh_from_db()

        self.assertEqual(get_supplier_balance(invoice.supplier), Decimal("100.00"))

    def test_posting_writes_an_audit_log(self):
        invoice, _, _, _ = purchase_ready()
        user = make_user()

        post_purchase_invoice(invoice.pk, user=user)

        log = AuditLog.objects.get(action="post_purchase_invoice")
        self.assertEqual(log.event_type, AuditEventType.UPDATE)
        self.assertEqual(log.module, "purchases")
        self.assertEqual(log.object_id, str(invoice.pk))
        self.assertEqual(log.actor_id, user.pk)

    def test_average_cost_blends_two_purchases_at_different_prices(self):
        location = make_location()
        item = make_item()

        first = make_draft_purchase_invoice(location=location, invoice_number="PI-001")
        add_purchase_line(first, item, quantity=10, unit_purchase_price="2.00")
        recalculate_purchase_totals(first)
        post_purchase_invoice(first.pk)

        second = make_draft_purchase_invoice(
            location=location, invoice_number="PI-002", supplier=make_supplier(supplier_code="SUP-002")
        )
        add_purchase_line(second, item, quantity=10, unit_purchase_price="4.00")
        recalculate_purchase_totals(second)
        post_purchase_invoice(second.pk)

        item.refresh_from_db()
        self.assertEqual(item.average_cost, Decimal("3.00"))


class CancelPostedPurchaseInvoiceTests(TestCase):
    def test_cancelling_a_draft_invoice_is_rejected(self):
        invoice, _, _, _ = purchase_ready()

        with self.assertRaises(ValidationError):
            cancel_posted_purchase_invoice(invoice.pk)

    def test_cancelling_twice_is_rejected(self):
        invoice, _, _, _ = purchase_ready()
        post_purchase_invoice(invoice.pk)
        cancel_posted_purchase_invoice(invoice.pk)

        with self.assertRaises(ValidationError):
            cancel_posted_purchase_invoice(invoice.pk)

    def test_status_becomes_cancelled(self):
        invoice, _, _, _ = purchase_ready()
        post_purchase_invoice(invoice.pk)

        cancelled = cancel_posted_purchase_invoice(invoice.pk)

        self.assertEqual(cancelled.status, PurchaseInvoiceStatus.CANCELLED)

    def test_supplier_due_is_reversed(self):
        invoice, _, _, _ = purchase_ready(paid_now="0.00")
        post_purchase_invoice(invoice.pk)

        cancel_posted_purchase_invoice(invoice.pk)

        reversal = SupplierLedgerEntry.objects.get(
            entry_type=SupplierLedgerEntryType.PURCHASE_RETURN
        )
        self.assertEqual(reversal.due_decrease, Decimal("100.00"))

    def test_supplier_balance_returns_to_zero(self):
        invoice, _, _, _ = purchase_ready(paid_now="0.00")
        post_purchase_invoice(invoice.pk)
        cancel_posted_purchase_invoice(invoice.pk)

        invoice.refresh_from_db()
        self.assertEqual(get_supplier_balance(invoice.supplier), Decimal("0"))

    def test_payment_is_returned_to_the_cashbox(self):
        invoice, _, _, cashbox = purchase_ready(paid_now="100.00")
        post_purchase_invoice(invoice.pk)

        cancel_posted_purchase_invoice(invoice.pk)

        reversal = CashboxMovement.objects.get(direction=CashboxDirection.IN)
        self.assertEqual(reversal.amount, Decimal("100.00"))
        self.assertEqual(reversal.movement_type, CashboxMovementType.ADJUSTMENT)
        self.assertEqual(get_cashbox_balance(cashbox), Decimal("0"))

    def test_stock_leaves_again_on_cancellation(self):
        invoice, item, location, _ = purchase_ready(quantity=4)
        post_purchase_invoice(invoice.pk)
        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("4"))

        cancel_posted_purchase_invoice(invoice.pk)

        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("0"))
        self.assertTrue(
            StockMovement.objects.filter(
                movement_type=StockMovementType.PURCHASE_RETURN_OUT
            ).exists()
        )

    def test_average_cost_is_reset_when_all_stock_is_reversed(self):
        invoice, item, _, _ = purchase_ready(quantity=4, unit_purchase_price="25.00")
        post_purchase_invoice(invoice.pk)

        cancel_posted_purchase_invoice(invoice.pk)
        item.refresh_from_db()

        self.assertEqual(item.average_cost, Decimal("0"))

    def test_cancelling_writes_an_audit_log_with_the_reason(self):
        invoice, _, _, _ = purchase_ready()
        post_purchase_invoice(invoice.pk)

        cancel_posted_purchase_invoice(invoice.pk, reason="wrong supplier")

        log = AuditLog.objects.get(action="cancel_posted_purchase_invoice")
        self.assertEqual(log.reason, "wrong supplier")


class RecordSupplierPaymentTests(TestCase):
    def setUp(self):
        super().setUp()
        self.supplier = make_supplier()
        self.cashbox = make_cashbox()

    def record(self, amount="150.00", **kwargs):
        return record_supplier_payment(
            payment_number=kwargs.pop("payment_number", "SP-001"),
            payment_date=kwargs.pop("payment_date", DEFAULT_DATE),
            supplier=kwargs.pop("supplier", self.supplier),
            cashbox=kwargs.pop("cashbox", self.cashbox),
            amount=Decimal(amount),
            **kwargs,
        )

    def test_payment_is_created_and_posted(self):
        payment = self.record()

        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(payment.status, SupplierPaymentStatus.POSTED)

    def test_payment_lowers_the_supplier_due(self):
        self.record()

        entry = SupplierLedgerEntry.objects.get()
        self.assertEqual(entry.entry_type, SupplierLedgerEntryType.SUPPLIER_PAYMENT)
        self.assertEqual(entry.due_decrease, Decimal("150.00"))

    def test_payment_moves_cash_out(self):
        self.record()

        movement = CashboxMovement.objects.get()
        self.assertEqual(movement.direction, CashboxDirection.OUT)
        self.assertEqual(movement.movement_type, CashboxMovementType.SUPPLIER_PAYMENT)
        self.assertEqual(get_cashbox_balance(self.cashbox), Decimal("-150.00"))

    def test_payment_touches_no_stock(self):
        self.record()

        self.assertEqual(StockMovement.objects.count(), 0)

    def test_payment_writes_an_audit_log(self):
        payment = self.record()

        log = AuditLog.objects.get(action="record_supplier_payment")
        self.assertEqual(log.event_type, AuditEventType.CREATE)
        self.assertEqual(log.object_id, str(payment.pk))


class CancelSupplierPaymentTests(TestCase):
    def setUp(self):
        super().setUp()
        self.supplier = make_supplier()
        self.cashbox = make_cashbox()
        self.payment = record_supplier_payment(
            payment_number="SP-001",
            payment_date=DEFAULT_DATE,
            supplier=self.supplier,
            cashbox=self.cashbox,
            amount=Decimal("150.00"),
        )

    def test_status_becomes_cancelled(self):
        cancelled = cancel_supplier_payment(self.payment.pk)

        self.assertEqual(cancelled.status, SupplierPaymentStatus.CANCELLED)

    def test_cancelling_twice_is_rejected(self):
        cancel_supplier_payment(self.payment.pk)

        with self.assertRaises(ValidationError):
            cancel_supplier_payment(self.payment.pk)

    def test_cancelling_restores_the_supplier_due(self):
        cancel_supplier_payment(self.payment.pk)

        reversal = SupplierLedgerEntry.objects.get(
            entry_type=SupplierLedgerEntryType.ADJUSTMENT
        )
        self.assertEqual(reversal.due_increase, Decimal("150.00"))

    def test_balances_net_to_zero_after_cancelling(self):
        cancel_supplier_payment(self.payment.pk)

        self.assertEqual(get_supplier_balance(self.supplier), Decimal("0"))
        self.assertEqual(get_cashbox_balance(self.cashbox), Decimal("0"))

    def test_cancelling_writes_an_audit_log_with_the_reason(self):
        cancel_supplier_payment(self.payment.pk, reason="duplicate")

        log = AuditLog.objects.get(action="cancel_supplier_payment")
        self.assertEqual(log.reason, "duplicate")


class CancelPurchaseInvoiceEdgeTests(TestCase):
    def test_cancelling_an_invoice_whose_lines_were_removed_is_rejected(self):
        # Tracked lines are protected by their stock movements, so use an
        # untracked item: posting it leaves no movement holding the line.
        location = make_location()
        item = make_item(is_stock_tracked=False)
        invoice = make_draft_purchase_invoice(location=location)
        add_purchase_line(invoice, item, quantity=1, unit_purchase_price="10.00")
        recalculate_purchase_totals(invoice)
        post_purchase_invoice(invoice.pk)
        invoice.lines.all().delete()

        with self.assertRaises(ValidationError):
            cancel_posted_purchase_invoice(invoice.pk)

    def test_cancelling_an_untracked_item_moves_no_stock(self):
        location = make_location()
        item = make_item(is_stock_tracked=False)
        invoice = make_draft_purchase_invoice(location=location)
        add_purchase_line(invoice, item, quantity=2, unit_purchase_price="10.00")
        recalculate_purchase_totals(invoice)
        post_purchase_invoice(invoice.pk)

        cancelled = cancel_posted_purchase_invoice(invoice.pk)

        self.assertEqual(cancelled.status, PurchaseInvoiceStatus.CANCELLED)
        self.assertEqual(StockMovement.objects.count(), 0)
