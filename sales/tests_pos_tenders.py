"""Restaurants R2b (HG-110): one bill, several tenders, through ``sales.pos.checkout(..., tenders=...)``.

The bill of R1a's example: a shirt at 150.00 (stock tracked, cost 60.00) and a service at 120.00, so 270.00 before VAT;
VAT 14% per line, 21.00 + 16.80 = 37.80, total **307.80**. Paid as cash 200.00 into «CASH» (the drawer, inside the owner's
shift) and 107.80 into «CARD» (the card machine's cashbox). With VAT off the same bill is 270.00, paid 150.00 and 120.00.

Every money figure is a Decimal; the clock stands still on Wednesday 11 Nov 2026 at noon in Cairo.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import router
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxKind
from cashboxes.services import get_cashbox_balance
from closing.models import PeriodStatus
from closing.services import get_period_for_date
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, open_test_shift, stock_in
from inventory.services import recalculate_item_average_cost
from ledger import reports as gl
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled
from settlements.models import InvoiceSettlement
from settlements.services import applied_to
from shifts.services import open_shift_for, summary

from .models import CustomerPayment
from .pos import checkout, walk_in_customer
from .pos_buttons import save_card_cashbox

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)  # a Wednesday, 12:00 in Cairo
ZERO = D("0.00")


def counts():
    return {model._meta.label: model.objects.count() for model in apps.get_models()
            if not model._meta.proxy and router.db_for_read(model) == "default"}


def balance(customer):
    agg = customer.ledger_entries.aggregate(i=Sum("due_increase"), d=Sum("due_decrease"))
    return customer.opening_balance + (agg["i"] or ZERO) - (agg["d"] or ZERO)


class TenderSetup(TestCase):
    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.profile = prepared_client("commercial", "retail", "customers,items_services,sales_operations,cashboxes,reports,inventory")
        self.owner = make_user(username="split_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cash = make_cashbox(cashbox_code="CASH", is_default=True)
        self.card = make_cashbox(cashbox_code="CARD", name_ar="ماكينة الفيزا")
        self.bank = make_cashbox(cashbox_code="BANK", name_ar="حساب البنك", kind=CashboxKind.BANK)
        save_card_cashbox(self.card)
        self.shirt = make_item(item_code="SHIRT", item_name="قميص", default_sale_price="150.00")
        stock_in(self.shirt, self.location, 10, "60.00", movement_date=timezone.localdate() - timedelta(days=1))
        recalculate_item_average_cost(self.shirt)
        self.service = make_item(item_code="SRV", item_name="خدمة", is_stock_tracked=False)
        self.walk_in = walk_in_customer()
        self.shift = open_test_shift(self.owner, self.cash)
        self.vat(True)

    def vat(self, on):
        set_capability_enabled(self.profile, "vat", on, user=self.owner)

    def lines(self):
        return [{"item": self.shirt, "quantity": D("1"), "unit_sale_price": D("150.00"), "line_discount_amount": ZERO, "description": ""},
                {"item": self.service, "quantity": D("1"), "unit_sale_price": D("120.00"), "line_discount_amount": ZERO, "description": ""}]

    def sell(self, tenders, customer=None, **kwargs):
        kwargs.setdefault("cashbox", self.cash)
        kwargs.setdefault("tendered", ZERO)
        return checkout(lines=self.lines(), customer=customer or self.walk_in, location=self.location, discount=ZERO, user=self.owner,
                        tenders=tenders, **kwargs)

    def split_307(self, customer=None):
        return self.sell([(self.cash, D("200.00")), (self.card, D("107.80"))], customer=customer)

    def assert_refused(self, message, tenders, customer=None, **kwargs):
        before = counts()
        with self.assertRaisesMessage(ValidationError, message):
            self.sell(tenders, customer=customer, **kwargs)
        self.assertEqual(counts(), before)

    def assert_books(self):
        """The trial balance is balanced; cash on hand, receivables, sales and VAT agree with what moved."""

        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        return {key: gl.control_balance(key) for key in ("cash", "bank", "receivable", "sales", "vat_out")}


class SplitCheckoutTests(TenderSetup):
    def test_307_80_with_vat_on_as_cash_200_and_card_107_80(self):
        invoice, change = self.split_307()
        self.assertEqual(change, ZERO)
        self.assertEqual((invoice.status, invoice.total_amount, invoice.tax_amount, invoice.paid_now, invoice.remaining_due, invoice.cashbox),
                         ("posted", D("307.80"), D("37.80"), D("200.00"), D("107.80"), self.cash))
        self.assertEqual((get_cashbox_balance(self.cash), get_cashbox_balance(self.card)), (D("200.00"), D("107.80")))
        payment = CustomerPayment.objects.get()
        self.assertEqual((payment.payment_number, payment.payment_date, payment.cashbox, payment.amount, payment.customer, payment.status),
                         (f"{invoice.invoice_number}-T2", timezone.localdate(), self.card, D("107.80"), self.walk_in, "posted"))
        self.assertEqual(list(InvoiceSettlement.objects.values_list("invoice_id", "customer_payment_id", "amount", "source")),
                         [(invoice.pk, payment.pk, D("107.80"), "split")])
        self.assertEqual(balance(self.walk_in), ZERO)
        self.assertEqual(applied_to(invoice), D("107.80"))  # nothing is due on the invoice
        # The drawer's shift counts the cash sale; the card machine has no drawer and is in no shift (AN8), as for a card sale.
        figures = summary(open_shift_for(self.owner))
        self.assertEqual((figures["groups"], D(figures["expected"])), ({"sales": "200.00"}, self.shift.opening_float + D("200.00")))
        books = self.assert_books()
        self.assertEqual(books, {"cash": D("307.80"), "bank": ZERO, "receivable": ZERO, "sales": D("-270.00"), "vat_out": D("-37.80")})
        actions = list(AuditLog.objects.filter(module="sales").order_by("pk").values_list("action", flat=True))
        self.assertEqual(actions, ["create_sales_draft", "record_customer_payment", "post_sales_invoice"])

    def test_270_with_vat_off_as_150_and_120(self):
        self.vat(False)
        invoice, change = self.sell([(self.cash, D("150.00")), (self.card, D("120.00"))])
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due, change), (D("270.00"), D("150.00"), D("120.00"), ZERO))
        self.assertEqual((get_cashbox_balance(self.cash), get_cashbox_balance(self.card)), (D("150.00"), D("120.00")))
        self.assertEqual(list(InvoiceSettlement.objects.values_list("amount", "source")), [(D("120.00"), "split")])
        self.assertEqual(balance(self.walk_in), ZERO)
        self.assertEqual(self.assert_books(), {"cash": D("270.00"), "bank": ZERO, "receivable": ZERO, "sales": D("-270.00"), "vat_out": ZERO})

    def test_three_tenders_cash_card_and_bank(self):
        invoice, change = self.sell([(self.cash, D("100.00")), (self.card, D("100.00")), (self.bank, D("107.80"))])
        self.assertEqual((invoice.paid_now, change), (D("100.00"), ZERO))
        self.assertEqual([get_cashbox_balance(box) for box in (self.cash, self.card, self.bank)], [D("100.00"), D("100.00"), D("107.80")])
        self.assertEqual(list(CustomerPayment.objects.order_by("pk").values_list("payment_number", "cashbox__cashbox_code", "amount")),
                         [(f"{invoice.invoice_number}-T2", "CARD", D("100.00")), (f"{invoice.invoice_number}-T3", "BANK", D("107.80"))])
        self.assertEqual(InvoiceSettlement.objects.filter(source="split").aggregate(total=Sum("amount"))["total"], D("207.80"))
        self.assertEqual(balance(self.walk_in), ZERO)
        self.assertEqual(self.assert_books(), {"cash": D("200.00"), "bank": D("107.80"), "receivable": ZERO, "sales": D("-270.00"), "vat_out": D("-37.80")})

    def test_one_tender_is_the_plain_call(self):
        invoice, change = self.sell([(self.cash, D("400.00"))])
        self.assertEqual((invoice.cashbox, invoice.paid_now, invoice.remaining_due, change), (self.cash, D("307.80"), ZERO, D("92.20")))
        self.assertFalse(CustomerPayment.objects.exists())
        self.assertFalse(InvoiceSettlement.objects.exists())

    def test_a_gift_card_part_and_two_tenders(self):
        from giftcards import services as giftcards

        set_capability_enabled(self.profile, "gift_cards", True, user=self.owner)
        card = giftcards.sell(D("100.00"), self.cash, self.owner)
        invoice, change = self.sell([(self.cash, D("100.00")), (self.card, D("107.80"))], gift=(card, D("100.00")))
        self.assertEqual((invoice.paid_now, invoice.remaining_due, change), (D("100.00"), D("207.80"), ZERO))
        self.assertEqual(sorted(InvoiceSettlement.objects.values_list("source", "amount")), [("gift_card", D("100.00")), ("split", D("107.80"))])
        self.assertEqual(balance(self.walk_in), ZERO)
        self.assertEqual(giftcards.balance(card), ZERO)

    def test_a_named_customer_may_leave_the_rest_on_account(self):
        customer = make_customer(customer_code="C-NOUR", name="شركة النور")
        invoice, _ = self.sell([(self.cash, D("100.00")), (self.card, D("100.00"))], customer=customer)
        self.assertEqual((invoice.paid_now, invoice.remaining_due, applied_to(invoice)), (D("100.00"), D("207.80"), D("100.00")))
        self.assertEqual(balance(customer), D("107.80"))
        self.assertEqual(self.assert_books()["receivable"], D("107.80"))

    def test_the_e_receipt_takes_the_largest_tenders_method(self):
        from ereceipt.services import _payment_method

        invoice, _ = self.split_307()
        self.assertEqual(_payment_method(invoice), "C")  # cash 200.00 is the larger
        self.vat(False)
        invoice, _ = self.sell([(self.cash, D("70.00")), (self.card, D("200.00"))])
        self.assertEqual(_payment_method(invoice), "V")  # the card's 200.00 is the larger
        invoice, _ = self.sell([(self.card, D("135.00")), (self.cash, D("135.00"))])
        self.assertEqual(_payment_method(invoice), "V")  # a tie goes to the first tender
        invoice, _ = self.sell(None, tendered=D("270.00"))
        self.assertEqual(_payment_method(invoice), "C")  # a sale paid one way: its own cashbox, as before


class RefusedTests(TenderSetup):
    def test_tenders_short_of_the_total_for_a_walk_in(self):
        self.assert_refused("err_walk_in_credit", [(self.cash, D("200.00")), (self.card, D("100.00"))])  # 300.00 on 307.80

    def test_tenders_above_the_total(self):
        self.assert_refused("err_tenders_over", [(self.cash, D("200.00")), (self.card, D("107.81"))])
        customer = make_customer(customer_code="C-OVER")
        self.assert_refused("err_tenders_over", [(self.cash, D("300.00")), (self.card, D("100.00"))], customer=customer)

    def test_a_zero_or_negative_tender(self):
        self.assert_refused("err_tender_amount", [(self.cash, D("307.80")), (self.card, D("0.00"))])
        self.assert_refused("err_tender_amount", [(self.cash, D("407.80")), (self.card, D("-100.00"))])
        self.assert_refused("err_tender_amount", [(self.cash, D("307.80")), (self.card, D("0.004"))])
        self.assert_refused("err_tender_amount", [])

    def test_a_repeated_cashbox(self):
        self.assert_refused("err_tender_repeated", [(self.cash, D("200.00")), (self.cash, D("107.80"))])

    def test_an_inactive_cashbox(self):
        self.card.active = False
        self.card.save(update_fields=["active"])
        self.assert_refused("err_tender_cashbox", [(self.cash, D("200.00")), (self.card, D("107.80"))])

    def test_a_held_kind_cheques_courier_or_gift_cards(self):
        for kind in (CashboxKind.CHEQUES_IN, CashboxKind.COURIER, CashboxKind.GIFT_CARD):
            box = make_cashbox(cashbox_code=f"HELD-{kind}", kind=kind)
            self.assert_refused("err_tender_cashbox", [(self.cash, D("200.00")), (box, D("107.80"))])
            self.assert_refused("err_tender_cashbox", [(box, D("200.00")), (self.cash, D("107.80"))])

    def test_the_card_button_with_tenders(self):
        self.assert_refused("err_tender_card", [(self.cash, D("200.00")), (self.card, D("107.80"))], card=True)

    def test_a_closed_month_keeps_nothing(self):
        period = get_period_for_date(timezone.localdate())
        period.status, period.closed_at = PeriodStatus.CLOSED, timezone.now()
        period.save(update_fields=["status", "closed_at"])
        before = counts()
        with self.assertRaises(ValidationError):
            self.split_307()
        self.assertEqual(counts(), before)

