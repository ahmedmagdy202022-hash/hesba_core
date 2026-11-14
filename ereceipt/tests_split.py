"""Restaurants R2b (HG-110): the e-receipt of a bill paid several ways names the largest tender's method (owner question 8).

The receipt is built and hashed when the invoice posts (``_on_audit`` on ``post_sales_invoice``), so the further tenders
must already be recorded then: this test goes through the real issue, not a call made afterwards.
"""

from decimal import Decimal as D

from hesba_testing.factories import make_cashbox
from sales.pos import checkout, walk_in_customer
from sales.pos_buttons import save_card_cashbox

from .models import Device, Receipt, ReceiptStatus
from .tests import ReceiptSetup, line


class SplitReceiptTests(ReceiptSetup):
    def setUp(self):
        super().setUp()
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        self.card = make_cashbox(cashbox_code="EI-CARD", name_ar="ماكينة الفيزا")
        save_card_cashbox(self.card)

    def split(self, cash, card):
        # 100.00 taxed at 14% (114.00) and 50.00 exempt: 164.00.
        invoice, _change = checkout(lines=[line(self.a, 1, "100.00"), line(self.b, 1, "50.00")], customer=walk_in_customer(), location=self.location,
                                    cashbox=self.cashbox, discount=D("0"), tendered=D("0"), user=self.owner,
                                    tenders=[(self.cashbox, D(cash)), (self.card, D(card))])
        self.assertEqual(invoice.total_amount, D("164.00"))
        receipt = Receipt.objects.get(invoice=invoice)
        self.assertEqual(receipt.status, ReceiptStatus.QUEUED)
        return receipt.document

    def test_the_card_being_the_larger_part_makes_it_a_card_receipt(self):
        doc = self.split("64.00", "100.00")
        self.assertEqual((doc["paymentMethod"], doc["totalAmount"]), ("V", 164.0))

    def test_the_cash_being_the_larger_part_keeps_it_a_cash_receipt(self):
        self.assertEqual(self.split("100.00", "64.00")["paymentMethod"], "C")
