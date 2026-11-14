"""Restaurants R2b (HG-110): the table bill into a card machine cashbox, pinned before ``tenders`` exists.

``tests_pay_characterization`` (R13) pins the cash bills and says it does not cover a card machine cashbox picked in the
bill's list. This file adds that case with the same row-by-row helper, so the bill's one-cashbox path (no ``split``) is
shown to write exactly the same rows after R2b. Not edited after its first commit.
"""

from decimal import Decimal as D

from hesba_testing.factories import make_cashbox, make_customer, make_item
from sales.pos_buttons import save_card_cashbox

from . import services, tests_pay_characterization as r13
from .tests import RestaurantSetup


class CardCashboxBillTests(RestaurantSetup):
    vat = r13.PayCharacterizationTests.vat
    pay = r13.PayCharacterizationTests.pay
    dine_in = r13.PayCharacterizationTests.dine_in
    SALE_AUDIT = r13.PayCharacterizationTests.SALE_AUDIT

    def setUp(self):
        super().setUp()
        self.grill = make_item(item_code="D-GRILL", item_name="مشويات", default_sale_price="120.00", is_stock_tracked=False)
        self.salad = make_item(item_code="D-SALAD", item_name="سلطة", default_sale_price="85.00", is_stock_tracked=False)

    def card_machine(self):
        self.cashbox = make_cashbox(cashbox_code="CARD", name_ar="ماكينة الفيزا")
        save_card_cashbox(self.cashbox)

    def test_a_walk_in_bill_with_vat_on_into_the_card_machine(self):
        self.vat(True)
        self.card_machine()
        order = self.dine_in((self.grill, 1), (self.salad, 1))
        invoice, change, rows = self.pay(order, tendered=D("233.70"))
        self.assertEqual(change, D("0.00"))
        self.assertEqual(rows["invoice"], ("posted", "paid", D("205.00"), D("0.00"), D("28.70"), D("233.70"), D("233.70"), D("0.00"),
                                           "WALK-IN", "HALL", "CARD", f"طلب مطعم {order.number} (1)", "resto_owner"))
        self.assertEqual(rows["tax"], [("D-GRILL", D("14.00"), D("120.00"), D("16.80")), ("D-SALAD", D("14.00"), D("85.00"), D("11.90"))])
        self.assertEqual(rows["cash"], [("CARD", "sales_receipt", "in", D("233.70"), True, "resto_owner", True)])
        self.assertEqual((rows["customer_ledger"], rows["stock"]), ([], []))
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("233.70"), D("0.00")), ("2103", D("0.00"), D("28.70")), ("4101", D("0.00"), D("205.00"))))])
        self.assertEqual(rows["written"], {"audit.AuditLog": 3, "cashboxes.CashboxMovement": 1, "master_data.Customer": 1, "printing.DocumentCompanySnapshot": 1,
                                           "sales.SalesInvoice": 1, "sales.SalesLine": 2, "taxes.SalesLineTax": 2})

    def test_a_named_customer_paying_part_by_card_with_vat_off(self):
        self.vat(False)
        self.card_machine()
        customer = make_customer(customer_code="C-CARD", name="أستاذ سامح")
        order = services.open_order(self.owner, table=self.t1, customer=customer)
        services.add_item(order, self.grill, self.owner, 2)
        invoice, change, rows = self.pay(order, tendered=D("100.00"))
        self.assertEqual(change, D("0.00"))
        self.assertEqual(rows["invoice"], ("posted", "partial", D("240.00"), D("0.00"), D("0.00"), D("240.00"), D("100.00"), D("140.00"),
                                           "C-CARD", "HALL", "CARD", f"طلب مطعم {order.number} (1)", "resto_owner"))
        self.assertEqual(rows["cash"], [("CARD", "sales_receipt", "in", D("100.00"), True, "resto_owner", True)])
        self.assertEqual(rows["customer_ledger"], [("C-CARD", "sales_due", D("140.00"), D("0.00"), True, True)])
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("100.00"), D("0.00")), ("1103", D("140.00"), D("0.00")), ("4101", D("0.00"), D("240.00"))))])
