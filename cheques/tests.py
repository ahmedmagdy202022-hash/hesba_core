"""T4 (MARKET-PARITY): cheques received and issued, with their bounces (HG-056)."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import Cashbox, CashboxKind
from cashboxes.services import get_cashbox_balance
from closing.models import PeriodStatus
from closing.services import provision_period_for
from entities import scope as entity_scope
from hesba_testing.factories import make_customer, make_seeded_role, make_supplier, make_user, make_user_profile
from ledger import reports
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry

from . import services
from .models import Cheque, ChequeStatus

TODAY = timezone.localdate()
D0 = TODAY - timedelta(days=4)


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def owes(customer):
    agg = CustomerLedgerEntry.objects.filter(customer=customer).aggregate(i=Sum("due_increase"), d=Sum("due_decrease"))
    return customer.opening_balance + (agg["i"] or D("0")) - (agg["d"] or D("0"))


def bal(code):
    agg = JournalLine.objects.filter(account__code=code).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


def books_hold(test):
    Projector().rebuild()
    result = reports.reconciliation()
    test.assertTrue(result["ok"], result["rows"])
    test.assertTrue(result["balanced"])
    test.assertEqual(bal("1199"), D("0"))  # nothing in suspense


class ChequeSetup(TestCase):
    def setUp(self):
        prepared_client("commercial", "wholesale")
        self.owner = person(RoleCode.OWNER, "cq_owner")
        self.cashier = person(RoleCode.CASHIER, "cq_cashier")
        self.customer = make_customer(customer_code="CQ-C", name="محلات النور", opening_balance=D("5000.00"))
        self.supplier = make_supplier(supplier_code="CQ-S", name="مصنع الشروق", opening_balance=D("4000.00"))
        self.bank = Cashbox.objects.create(cashbox_code="BANK-CQ", name_ar="بنك مصر", kind=CashboxKind.BANK, bank_name="بنك مصر", opening_balance=D("10000.00"))

    def receive(self, amount="3000.00", number="100200", on=D0, due=None):
        return services.receive(self.customer, {"number": number, "bank_name": "QNB", "due_date": (due or TODAY + timedelta(days=30)).isoformat(),
                                                "amount": amount, "received_on": on.isoformat()}, self.owner)


class ReceivedChequeTests(ChequeSetup):
    def test_receive_deposit_clear(self):
        cheque = self.receive()
        self.assertEqual(owes(self.customer), D("2000.00"))  # the cheque pays them the day it is received
        self.assertEqual(get_cashbox_balance(cheque.box), D("3000.00"))
        self.assertEqual(cheque.box.kind, CashboxKind.CHEQUES_IN)
        services.deposit(cheque, self.bank, self.owner, on=D0 + timedelta(days=1))
        cheque.refresh_from_db()
        self.assertEqual((cheque.status, get_cashbox_balance(cheque.box), get_cashbox_balance(self.bank)), (ChequeStatus.DEPOSITED, D("0"), D("13000.00")))
        services.clear(cheque, self.owner, on=D0 + timedelta(days=3))
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, ChequeStatus.CLEARED)
        self.assertEqual(get_cashbox_balance(self.bank), D("13000.00"))  # clearing moves no money
        books_hold(self)
        self.assertEqual(bal("1104"), D("0"))
        self.assertEqual(bal("1102"), D("13000.00"))
        with self.assertRaisesMessage(ValidationError, "اتصرف"):
            services.bounce(cheque, "رجع", self.owner)  # a cleared cheque cannot bounce

    def test_a_cheque_in_hand_is_notes_receivable_until_deposited(self):
        self.receive()
        books_hold(self)
        self.assertEqual(bal("1104"), D("3000.00"))
        self.assertEqual(bal("1101"), D("0"))  # never cash on hand

    def test_bounce_before_deposit(self):
        cheque = self.receive()
        services.bounce(cheque, "رصيد غير كافٍ", self.owner, on=TODAY)
        cheque.refresh_from_db()
        self.assertEqual((cheque.status, cheque.bounced_on), (ChequeStatus.BOUNCED, TODAY))
        self.assertEqual(owes(self.customer), D("5000.00"))  # owes it again
        self.assertEqual(get_cashbox_balance(cheque.box), D("0"))
        self.assertTrue(CustomerLedgerEntry.objects.filter(customer=self.customer, entry_date=TODAY, due_increase=D("3000.00")).exists())
        self.assertTrue(AuditLog.objects.filter(module="cheques", action="bounce_cheque", object_id=str(cheque.pk)).exists())
        books_hold(self)
        self.assertEqual(bal("1104"), D("0"))

    def test_bounce_after_deposit_takes_it_back_out_of_the_bank(self):
        cheque = self.receive()
        services.deposit(cheque, self.bank, self.owner, on=D0 + timedelta(days=1))
        services.bounce(cheque, "توقيع مختلف", self.owner, on=TODAY)
        cheque.refresh_from_db()
        self.assertEqual(get_cashbox_balance(self.bank), D("10000.00"))  # where it started
        self.assertEqual(get_cashbox_balance(cheque.box), D("0"))
        self.assertEqual(owes(self.customer), D("5000.00"))
        self.assertEqual(cheque.return_operation.operation_date, TODAY)
        books_hold(self)
        self.assertEqual(bal("1102"), D("10000.00"))

    def test_a_bounce_after_the_payments_period_closed_is_dated_today_and_accepted(self):
        old = TODAY - timedelta(days=40)
        cheque = self.receive(on=old)
        period = provision_period_for(old)
        period.status, period.closed_at = PeriodStatus.CLOSED, timezone.now()
        period.save(update_fields=["status", "closed_at"])
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            services.bounce(cheque, "رجع", self.owner, on=old + timedelta(days=1))  # a date inside the closed month is refused
        services.bounce(cheque, "رجع", self.owner, on=TODAY)
        self.assertEqual(owes(self.customer), D("5000.00"))
        self.assertEqual(Cheque.objects.get(pk=cheque.pk).customer_payment.status, "posted")  # the payment itself is untouched

    def test_cancel_only_in_hand_and_never_from_the_payments_list(self):
        from sales.services import cancel_customer_payment

        cheque = self.receive()
        with self.assertRaisesMessage(ValidationError, "cheques screen"):
            cancel_customer_payment(cheque.customer_payment_id, user=self.owner, reason="x")
        services.cancel(cheque, "اتسجل غلط", self.owner)
        cheque.refresh_from_db()
        self.assertEqual((cheque.status, cheque.customer_payment.status), (ChequeStatus.CANCELLED, "cancelled"))
        self.assertEqual(owes(self.customer), D("5000.00"))
        second = self.receive(number="100201")
        services.deposit(second, self.bank, self.owner, on=TODAY)
        with self.assertRaisesMessage(ValidationError, "اتودع"):
            services.cancel(second, "x", self.owner)

    def test_the_same_cheque_twice_and_the_form_rules(self):
        self.receive()
        with self.assertRaisesMessage(ValidationError, "متسجل قبل كده"):
            self.receive()
        with self.assertRaisesMessage(ValidationError, "رقم الشيك"):
            services.receive(self.customer, {"bank_name": "QNB", "due_date": TODAY.isoformat(), "amount": "10"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "أكبر من صفر"):
            services.receive(self.customer, {"number": "9", "bank_name": "QNB", "due_date": TODAY.isoformat(), "amount": "0"}, self.owner)

    def test_what_falls_due(self):
        soon = self.receive(number="1", due=TODAY + timedelta(days=3))
        self.receive(number="2", due=TODAY + timedelta(days=60))
        self.assertEqual(list(services.due_soon()), [soon])

    def test_cheque_boxes_stay_out_of_pickers_and_cash_on_hand(self):
        cheque = self.receive()
        self.assertFalse(entity_scope.cashboxes(Cashbox.objects).filter(pk=cheque.box_id).exists())
        from reports.selectors import cashbox_report

        self.assertNotIn(cheque.box_id, [row["cashbox_id"] for row in cashbox_report()])


class IssuedChequeTests(ChequeSetup):
    def supplier_owed(self):
        from purchases.models import SupplierLedgerEntry

        agg = SupplierLedgerEntry.objects.filter(supplier=self.supplier).aggregate(i=Sum("due_increase"), d=Sum("due_decrease"))
        return self.supplier.opening_balance + (agg["i"] or D("0")) - (agg["d"] or D("0"))

    def issue(self, amount="2500.00", number="7001"):
        return services.issue(self.supplier, self.bank, {"number": number, "due_date": (TODAY + timedelta(days=20)).isoformat(), "amount": amount,
                                                         "received_on": D0.isoformat()}, self.owner)

    def test_issue_then_cashed(self):
        cheque = self.issue()
        self.assertEqual(self.supplier_owed(), D("1500.00"))
        self.assertEqual(get_cashbox_balance(cheque.box), D("-2500.00"))  # what our cheques still owe
        self.assertEqual(get_cashbox_balance(self.bank), D("10000.00"))
        books_hold(self)
        self.assertEqual(bal("2102"), D("-2500.00"))  # notes payable
        services.clear(cheque, self.owner, on=TODAY)
        self.assertEqual(get_cashbox_balance(cheque.box), D("0"))
        self.assertEqual(get_cashbox_balance(self.bank), D("7500.00"))
        books_hold(self)
        self.assertEqual(bal("2102"), D("0"))

    def test_an_issued_cheque_that_bounces_is_owed_again(self):
        cheque = self.issue()
        services.bounce(cheque, "مفيش رصيد", self.owner, on=TODAY)
        self.assertEqual(self.supplier_owed(), D("4000.00"))
        self.assertEqual(get_cashbox_balance(cheque.box), D("0"))
        books_hold(self)

    def test_a_cheque_bigger_than_the_bank_cannot_be_paid(self):
        cheque = self.issue(amount="12000.00")
        with self.assertRaisesMessage(ValidationError, "negative"):
            services.clear(cheque, self.owner, on=TODAY)


class ScreenTests(ChequeSetup):
    def test_the_register_receives_and_deposits(self):
        self.client.force_login(self.owner)
        url = reverse("cheques:register")
        for lang in ("ar", "en"):
            self.assertContains(self.client.get(url + f"?lang={lang}"), "data-receive-cheque")
        response = self.client.post(url, {"lang": "ar", "tab": "in", "action": "receive", "customer": self.customer.pk, "number": "55",
                                          "bank_name": "CIB", "due_date": TODAY.isoformat(), "amount": "1,000"})
        self.assertEqual(response.status_code, 302)
        cheque = Cheque.objects.get()
        page = self.client.get(url + "?lang=ar&tab=due")
        self.assertContains(page, f'data-cheque="{cheque.pk}"')
        self.client.post(url, {"lang": "ar", "action": "deposit", "cheque": cheque.pk, "bank": self.bank.pk, "on": TODAY.isoformat()})
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, ChequeStatus.DEPOSITED)
        self.assertContains(self.client.get(reverse("dashboard_snapshot") + "?lang=ar"), 'href="/cheques/?lang=ar"')
        bad = self.client.post(url, {"lang": "ar", "tab": "out", "action": "issue", "supplier": self.supplier.pk, "bank": self.bank.pk, "number": "",
                                     "due_date": TODAY.isoformat(), "amount": "5"})
        self.assertContains(bad, "رقم الشيك")

    def test_issuing_from_the_screen_and_a_cheque_transfer_cannot_be_reversed_alone(self):
        self.client.force_login(self.owner)
        url = reverse("cheques:register")
        response = self.client.post(url, {"lang": "ar", "tab": "out", "action": "issue", "supplier": self.supplier.pk, "bank": self.bank.pk, "number": "8001",
                                          "due_date": (TODAY + timedelta(days=10)).isoformat(), "amount": "700", "received_on": TODAY.isoformat()})
        self.assertEqual(response.status_code, 302)
        issued = Cheque.objects.get(number="8001")
        self.assertEqual((issued.status, issued.due_date), (ChequeStatus.ISSUED, TODAY + timedelta(days=10)))
        received = self.receive()
        services.deposit(received, self.bank, self.owner, on=TODAY)
        received.refresh_from_db()
        response = self.client.post(reverse("cashboxes:operation_cancel", args=[received.deposit_operation_id]),
                                    {"lang": "ar", "reversal_date": TODAY.isoformat(), "reason": "غلط"}, follow=True)
        self.assertContains(response, "تابعة لشيك")
        received.deposit_operation.refresh_from_db()
        self.assertEqual(received.deposit_operation.status, "posted")

    def test_the_cashier_can_cancel_a_cheque_they_received(self):
        cheque = self.receive()
        self.client.force_login(self.cashier)
        page = self.client.get(reverse("cheques:register") + "?lang=ar")
        self.assertContains(page, f'data-cancel="{cheque.pk}"')
        self.client.post(reverse("cheques:register"), {"lang": "ar", "action": "cancel", "cheque": cheque.pk, "reason": "اتسجل غلط"})
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, ChequeStatus.CANCELLED)

    def test_who_does_what(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "cq_keeper"))
        self.assertEqual(self.client.get(reverse("cheques:register")).status_code, 403)
        self.client.force_login(self.cashier)
        page = self.client.get(reverse("cheques:register") + "?lang=ar")
        self.assertEqual(page.status_code, 200)  # the cashier takes cheques at the counter
        self.assertNotContains(page, "data-deposit=")
