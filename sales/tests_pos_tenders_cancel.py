"""Restaurants R2b (HG-110): cancelling, returning and undoing part of a bill paid several ways.

The bill and the figures of ``tests_pos_tenders``: 307.80 (VAT on) paid as cash 200.00 into «CASH» and 107.80 into «CARD».
Cancelling the invoice cancels its split payment in the same transaction (``sales.tenders._on_audit``, the gift-card
precedent HG-071 D3); a walk-in's split payment is undone only with its invoice and a walk-in invoice paid several ways is
cancelled whole, not returned (HG-071 D7's rule); a named customer's behaves as any partly paid invoice.
"""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.utils import timezone

from approvals.models import ApprovalRequest, ApprovalStatus
from approvals.services import approve, hold, set_held_actions
from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from cashboxes.services import get_cashbox_balance
from closing.models import PeriodStatus
from closing.services import get_period_for_date
from hesba_testing.factories import grant, make_customer, make_role, make_user, make_user_profile
from permissions.models import Permission
from settlements.models import InvoiceSettlement
from settlements.services import applied_to

from .models import CustomerPayment
from .services import cancel_customer_payment, cancel_posted_sales_invoice, create_sales_return
from .tenders import WALK_IN_PAYMENT_MESSAGE, WALK_IN_RETURN_MESSAGE
from .tests_pos_tenders import ZERO, TenderSetup, balance, counts


class CancelTests(TenderSetup):
    def test_cancelling_the_307_80_invoice_puts_both_cashboxes_back_and_leaves_the_walk_in_at_zero(self):
        invoice, _ = self.split_307()
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط في الطلب")
        invoice.refresh_from_db()
        payment = CustomerPayment.objects.get()
        self.assertEqual((invoice.status, payment.status), ("cancelled", "cancelled"))
        self.assertEqual((get_cashbox_balance(self.cash), get_cashbox_balance(self.card)), (ZERO, ZERO))
        # The drawer's 200.00 goes back through the invoice's own cancellation; the card's 107.80 through the payment's.
        self.assertEqual(list(CashboxMovement.objects.filter(direction="out").order_by("pk").values_list("cashbox__cashbox_code", "amount", "sales_invoice", "customer_payment")),
                         [("CASH", D("200.00"), invoice.pk, None), ("CARD", D("107.80"), None, payment.pk)])
        self.assertEqual(balance(self.walk_in), ZERO)  # not −107.80
        self.assertFalse(InvoiceSettlement.objects.exists())
        self.assertEqual(self.assert_books(), {"cash": ZERO, "bank": ZERO, "receivable": ZERO, "sales": ZERO, "vat_out": ZERO})
        cancel = AuditLog.objects.get(action="cancel_customer_payment")
        self.assertEqual((cancel.object_id, cancel.actor), (str(payment.pk), self.owner))
        self.assertIn(invoice.invoice_number, cancel.reason)

    def test_a_closed_month_refuses_the_whole_cancellation(self):
        invoice, _ = self.split_307()
        period = get_period_for_date(timezone.localdate())
        period.status, period.closed_at = PeriodStatus.CLOSED, timezone.now()
        period.save(update_fields=["status", "closed_at"])
        before = counts()
        with self.assertRaises(ValidationError):
            cancel_posted_sales_invoice(invoice.pk, self.owner, "متأخر")
        self.assertEqual(counts(), before)
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, CustomerPayment.objects.get().status), ("posted", "posted"))
        self.assertEqual((get_cashbox_balance(self.cash), get_cashbox_balance(self.card)), (D("200.00"), D("107.80")))

    def test_a_held_cancellation_moves_nothing_until_a_second_person_approves(self):
        invoice, _ = self.split_307()
        set_held_actions(["sales.cancel_invoice"], self.owner)
        role = make_role(code="CLERK-R2B", name_ar="مسؤول", name_en="Clerk")
        grant(role, Permission.objects.get(code="sales.return_sale"))
        clerk = make_user(username="r2b_clerk")
        make_user_profile(user=clerk, role=role)
        before = counts()
        request = hold(clerk, "sales.cancel_invoice", invoice.pk, {"reason": "غلط"}, invoice.invoice_number)
        self.assertIsNotNone(request)
        moved = {label: n - before[label] for label, n in counts().items() if n != before[label]}
        self.assertEqual(moved, {"approvals.ApprovalRequest": 1, "audit.AuditLog": 1})  # the request only
        self.assertEqual((get_cashbox_balance(self.cash), get_cashbox_balance(self.card)), (D("200.00"), D("107.80")))
        approve(request.pk, self.owner)
        request.refresh_from_db()
        self.assertEqual(request.status, ApprovalStatus.DONE)
        self.assertEqual((get_cashbox_balance(self.cash), get_cashbox_balance(self.card), balance(self.walk_in)), (ZERO, ZERO, ZERO))
        self.assertEqual(CustomerPayment.objects.get().status, "cancelled")
        self.assertFalse(ApprovalRequest.objects.filter(status=ApprovalStatus.PENDING).exists())

    def test_a_walk_ins_split_payment_is_not_cancelled_alone(self):
        self.split_307()
        payment = CustomerPayment.objects.get()
        before = counts()
        with self.assertRaisesMessage(ValidationError, WALK_IN_PAYMENT_MESSAGE):
            cancel_customer_payment(payment.pk, self.owner, "غلط")
        self.assertEqual(counts(), before)
        self.assertEqual(balance(self.walk_in), ZERO)

    def test_a_named_customers_split_payment_may_be_cancelled_alone_and_the_invoice_is_due_again(self):
        customer = make_customer(customer_code="C-SAMEH", name="أستاذ سامح")
        invoice, _ = self.split_307(customer)
        payment = CustomerPayment.objects.get()
        cancel_customer_payment(payment.pk, self.owner, "رجعت الفيزا")
        self.assertEqual((balance(customer), applied_to(invoice), get_cashbox_balance(self.card)), (D("107.80"), ZERO, ZERO))


class ReturnTests(TenderSetup):
    def ret(self, invoice, quantity="1"):
        line = invoice.lines.get(item=self.service)
        return create_sales_return(f"SR-{invoice.pk}", timezone.localdate(), invoice.pk,
                                   [{"source_line": line.pk, "quantity": D(quantity)}], "رجّع الخدمة", self.owner)

    def test_a_walk_in_invoice_paid_several_ways_is_not_returned(self):
        invoice, _ = self.split_307()
        before = counts()
        with self.assertRaisesMessage(ValidationError, WALK_IN_RETURN_MESSAGE):
            self.ret(invoice)
        self.assertEqual(counts(), before)
        self.assertEqual(balance(self.walk_in), ZERO)

    def test_a_named_customers_split_invoice_returns_as_any_partly_paid_invoice(self):
        """The cash part comes back from the invoice's cashbox in proportion; the rest lowers what the customer owes (the
        card part becomes the customer's credit), exactly as for an invoice paid 200.00 now and 107.80 by a later collection."""

        customer = make_customer(customer_code="C-RET", name="مدام سعاد")
        invoice, _ = self.split_307(customer)
        sales_return = self.ret(invoice)  # the service, 120.00 + 16.80 VAT = 136.80
        self.assertEqual((sales_return.total_amount, sales_return.cash_amount), (D("136.80"), D("88.89")))
        self.assertEqual((get_cashbox_balance(self.cash), get_cashbox_balance(self.card)), (D("111.11"), D("107.80")))
        self.assertEqual(balance(customer), D("-47.91"))  # 136.80 − 88.89 now owed back to the customer

    def test_a_single_paid_walk_in_invoice_still_returns_in_cash(self):
        invoice, _ = self.sell(None, tendered=D("307.80"))
        sales_return = self.ret(invoice)
        self.assertEqual((sales_return.total_amount, sales_return.cash_amount), (D("136.80"), D("136.80")))
        self.assertEqual(balance(self.walk_in), ZERO)

