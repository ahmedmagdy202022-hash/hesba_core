"""Restaurants R13: what paying a table writes, pinned row by row before ``pay()`` is split into steps.

Each scenario pays one order through ``services.pay`` and records every row the
payment wrote: how many rows of each model, the invoice header and lines, the
tax rows, the customer-ledger rows, the cashbox and stock movements, the audit
actions, and the journal entries the projector builds from them. The expected
values were taken from the code before the refactor; the refactored code must
write exactly the same rows.

Not covered because the table bill does not do it: paying by card (``pay()``
has no card argument; a card machine cashbox picked in the list is just a
cashbox), promotions, points and gift cards.
"""

from collections import Counter
from decimal import Decimal as D

from django.apps import apps
from django.db import router
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_customer, make_item
from inventory.models import StockMovement
from ledger.models import JournalEntry
from ledger.projector import Projector
from sales.models import CustomerLedgerEntry, SalesInvoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from taxes.models import SalesLineTax

from . import services
from .insights import save_ingredients
from .models import OrderKind, OrderLine
from .tests import RestaurantSetup


def _counts():
    return {model._meta.label: model.objects.count() for model in apps.get_models()
            if not model._meta.proxy and router.db_for_read(model) == "default"}


def _journal():
    Projector().rebuild()
    return Counter((entry.source_type, entry.entry_date,
                    tuple(sorted((line.account.code, line.debit, line.credit) for line in entry.lines.select_related("account"))))
                   for entry in JournalEntry.objects.prefetch_related("lines"))


class PayCharacterizationTests(RestaurantSetup):
    """Every row a table payment writes, so splitting ``pay()`` cannot change one of them."""

    def setUp(self):
        super().setUp()
        self.grill = make_item(item_code="D-GRILL", item_name="مشويات", default_sale_price="120.00", is_stock_tracked=False)
        self.salad = make_item(item_code="D-SALAD", item_name="سلطة", default_sale_price="85.00", is_stock_tracked=False)

    def vat(self, on):
        set_capability_enabled(ClientProfile.get_active(), "vat", on, user=self.owner)

    def pay(self, order, **kwargs):
        """Pay ``order`` and return (invoice, change, every row written)."""

        journal_before = _journal()
        counts_before = _counts()
        marks = {model: model.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
                 for model in (CashboxMovement, StockMovement, CustomerLedgerEntry, AuditLog)}
        invoice, change = services.pay(order, self.owner, cashbox=self.cashbox, **kwargs)
        counts_after = _counts()
        written = {label: counts_after[label] - counts_before[label] for label in counts_after if counts_after[label] != counts_before[label]}
        new = {model: model.objects.filter(pk__gt=mark).order_by("pk") for model, mark in marks.items()}
        journal = sorted((key[0], key[2]) for key in (_journal() - journal_before).elements())
        today = timezone.localdate()
        invoice.refresh_from_db()
        self.assertTrue(invoice.invoice_number.startswith(f"POS-{today:%Y%m%d}-"))
        self.assertEqual(invoice.invoice_date, today)
        rows = {
            "written": written,
            "invoice": (invoice.status, invoice.payment_status, invoice.subtotal, invoice.discount_amount, invoice.tax_amount, invoice.total_amount,
                        invoice.paid_now, invoice.remaining_due, invoice.customer.customer_code, invoice.selling_location.location_code,
                        invoice.cashbox.cashbox_code, invoice.notes, invoice.created_by.username),
            "lines": [(line.item.item_code, line.description, line.quantity, line.unit_sale_price, line.line_discount_amount, line.line_total_amount,
                       line.unit_cost, line.line_cost_amount, line.line_profit_amount) for line in invoice.lines.select_related("item").order_by("pk")],
            "tax": [(row.line.item.item_code, row.rate, row.taxable_amount, row.tax_amount)
                    for row in SalesLineTax.objects.filter(line__invoice=invoice).select_related("line__item").order_by("line__pk")],
            "customer_ledger": [(row.customer.customer_code, row.entry_type, row.due_increase, row.due_decrease, row.entry_date == today,
                                 row.sales_invoice_id == invoice.pk) for row in new[CustomerLedgerEntry]],
            "cash": [(row.cashbox.cashbox_code, row.movement_type, row.direction, row.amount, row.movement_date == today, row.created_by.username,
                      row.sales_invoice_id == invoice.pk) for row in new[CashboxMovement]],
            "stock": [(row.item.item_code, row.location.location_code, row.movement_type, row.quantity, row.unit_cost, row.sales_invoice_id == invoice.pk)
                      for row in new[StockMovement]],
            "audit": [(row.module, row.action) for row in new[AuditLog]],
            "journal": journal,
        }
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice_id, order.closed_at is not None), ("paid", invoice.pk, True))
        return invoice, change, rows

    def dine_in(self, *items):
        order = services.open_order(self.owner, table=self.t1, guests=2)
        for item, quantity in items:
            services.add_item(order, item, self.owner, quantity)
        return order

    #: The audit rows every table payment writes: the draft, its posting, and the order's own row.
    SALE_AUDIT = [("sales", "create_sales_draft"), ("sales", "post_sales_invoice"), ("restaurant", "pay_order")]

    def test_two_dishes_with_vat_on_in_cash(self):
        self.vat(True)
        order = self.dine_in((self.grill, 1), (self.salad, 1))
        services.send_to_kitchen(order, self.owner)
        invoice, change, rows = self.pay(order, tendered=D("250.00"))
        self.assertEqual(change, D("16.30"))
        # 120.00 + 85.00 = 205.00; VAT 14% per line: 16.80 + 11.90 = 28.70; total 233.70.
        self.assertEqual(rows["invoice"], ("posted", "paid", D("205.00"), D("0.00"), D("28.70"), D("233.70"), D("233.70"), D("0.00"),
                                           "WALK-IN", "HALL", "TILL", f"طلب مطعم {order.number} (1)", "resto_owner"))
        self.assertEqual(rows["lines"], [
            ("D-GRILL", "", D("1.000"), D("120.00"), D("0.00"), D("120.00"), D("0.0000"), D("0.00"), D("120.00")),
            ("D-SALAD", "", D("1.000"), D("85.00"), D("0.00"), D("85.00"), D("0.0000"), D("0.00"), D("85.00")),
        ])
        self.assertEqual(rows["tax"], [("D-GRILL", D("14.00"), D("120.00"), D("16.80")), ("D-SALAD", D("14.00"), D("85.00"), D("11.90"))])
        self.assertEqual(rows["cash"], [("TILL", "sales_receipt", "in", D("233.70"), True, "resto_owner", True)])
        self.assertEqual(rows["customer_ledger"], [])
        self.assertEqual(rows["stock"], [])
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("233.70"), D("0.00")), ("2103", D("0.00"), D("28.70")), ("4101", D("0.00"), D("205.00"))))])
        self.assertEqual(rows["written"], {"audit.AuditLog": 3, "cashboxes.CashboxMovement": 1, "master_data.Customer": 1, "printing.DocumentCompanySnapshot": 1,
                                           "sales.SalesInvoice": 1, "sales.SalesLine": 2, "taxes.SalesLineTax": 2})

    def test_the_same_dishes_with_vat_off(self):
        self.vat(False)
        order = self.dine_in((self.grill, 1), (self.salad, 1))
        invoice, change, rows = self.pay(order, tendered=D("205.00"))
        self.assertEqual(change, D("0.00"))
        self.assertEqual(rows["invoice"], ("posted", "paid", D("205.00"), D("0.00"), D("0.00"), D("205.00"), D("205.00"), D("0.00"),
                                           "WALK-IN", "HALL", "TILL", f"طلب مطعم {order.number} (1)", "resto_owner"))
        self.assertEqual(rows["lines"], [
            ("D-GRILL", "", D("1.000"), D("120.00"), D("0.00"), D("120.00"), D("0.0000"), D("0.00"), D("120.00")),
            ("D-SALAD", "", D("1.000"), D("85.00"), D("0.00"), D("85.00"), D("0.0000"), D("0.00"), D("85.00")),
        ])
        self.assertEqual(rows["tax"], [])
        self.assertEqual(rows["cash"], [("TILL", "sales_receipt", "in", D("205.00"), True, "resto_owner", True)])
        self.assertEqual((rows["customer_ledger"], rows["stock"]), ([], []))
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("205.00"), D("0.00")), ("4101", D("0.00"), D("205.00"))))])
        self.assertEqual(rows["written"], {"audit.AuditLog": 3, "cashboxes.CashboxMovement": 1, "master_data.Customer": 1, "printing.DocumentCompanySnapshot": 1,
                                           "sales.SalesInvoice": 1, "sales.SalesLine": 2})

    def test_a_bill_discount_after_tax_a_stocked_drink_and_a_voided_sent_line(self):
        self.vat(True)
        order = self.dine_in((self.grill, 1), (self.cola, 2), (self.tea, 1))
        services.send_to_kitchen(order, self.owner)
        OrderLine.objects.filter(order=order, item=self.tea).update(voided=True)  # a line voided after the kitchen fired never reaches the bill
        invoice, change, rows = self.pay(order, discount=D("10.00"), tendered=D("200.00"))
        self.assertEqual(change, D("27.60"))
        # 120.00 + 2 × 20.00 = 160.00; VAT 16.80 + 5.60 = 22.40; the bill discount comes off after tax: 182.40 − 10.00 = 172.40.
        self.assertEqual(rows["invoice"], ("posted", "paid", D("160.00"), D("10.00"), D("22.40"), D("172.40"), D("172.40"), D("0.00"),
                                           "WALK-IN", "HALL", "TILL", f"طلب مطعم {order.number} (1)", "resto_owner"))
        self.assertEqual(rows["lines"], [
            ("D-GRILL", "", D("1.000"), D("120.00"), D("0.00"), D("120.00"), D("0.0000"), D("0.00"), D("112.50")),
            ("B-COLA", "", D("2.000"), D("20.00"), D("0.00"), D("40.00"), D("9.0000"), D("18.00"), D("19.50")),
        ])
        self.assertEqual(rows["tax"], [("D-GRILL", D("14.00"), D("120.00"), D("16.80")), ("B-COLA", D("14.00"), D("40.00"), D("5.60"))])
        self.assertEqual(rows["cash"], [("TILL", "sales_receipt", "in", D("172.40"), True, "resto_owner", True)])
        self.assertEqual(rows["customer_ledger"], [])
        self.assertEqual(rows["stock"], [("B-COLA", "HALL", "sale_out", D("2.000"), D("9.0000"), True)])
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("172.40"), D("0.00")), ("1105", D("0.00"), D("18.00")), ("2103", D("0.00"), D("22.40")),
                                                               ("4101", D("0.00"), D("150.00")), ("5101", D("18.00"), D("0.00"))))])
        self.assertEqual(rows["written"], {"audit.AuditLog": 3, "cashboxes.CashboxMovement": 1, "inventory.StockMovement": 1, "master_data.Customer": 1,
                                           "printing.DocumentCompanySnapshot": 1, "sales.SalesInvoice": 1, "sales.SalesLine": 2, "taxes.SalesLineTax": 2})

    def test_a_delivery_with_its_fee_for_a_named_customer_who_pays_part(self):
        self.vat(True)
        customer = make_customer(customer_code="C-DLV", name="شركة النور")
        order = services.open_order(self.owner, kind=OrderKind.DELIVERY, customer=customer, address="المعادي", delivery_fee="20")
        services.add_item(order, self.salad, self.owner, 2)
        invoice, change, rows = self.pay(order, tendered=D("100.00"))
        self.assertEqual(change, D("0.00"))
        # 2 × 85.00 + a 20.00 fee = 190.00; VAT 23.80 + 2.80 = 26.60; total 216.60, 100.00 now, 116.60 on account.
        self.assertEqual(rows["invoice"], ("posted", "partial", D("190.00"), D("0.00"), D("26.60"), D("216.60"), D("100.00"), D("116.60"),
                                           "C-DLV", "HALL", "TILL", f"طلب مطعم {order.number} (دليفري)", "resto_owner"))
        self.assertEqual(rows["lines"], [
            ("D-SALAD", "", D("2.000"), D("85.00"), D("0.00"), D("170.00"), D("0.0000"), D("0.00"), D("170.00")),
            ("DELIVERY-FEE", "رسوم توصيل", D("1.000"), D("20.00"), D("0.00"), D("20.00"), D("0.0000"), D("0.00"), D("20.00")),
        ])
        self.assertEqual(rows["tax"], [("D-SALAD", D("14.00"), D("170.00"), D("23.80")), ("DELIVERY-FEE", D("14.00"), D("20.00"), D("2.80"))])
        self.assertEqual(rows["customer_ledger"], [("C-DLV", "sales_due", D("116.60"), D("0.00"), True, True)])
        self.assertEqual(rows["cash"], [("TILL", "sales_receipt", "in", D("100.00"), True, "resto_owner", True)])
        self.assertEqual(rows["stock"], [])
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("100.00"), D("0.00")), ("1103", D("116.60"), D("0.00")), ("2103", D("0.00"), D("26.60")),
                                                               ("4101", D("0.00"), D("190.00"))))])
        # The fee item is made on the first delivery; a named customer earns points (the café has loyalty on).
        self.assertEqual(rows["written"], {"audit.AuditLog": 3, "cashboxes.CashboxMovement": 1, "loyalty.LoyaltySettings": 1, "loyalty.PointsEntry": 1,
                                           "master_data.Item": 1, "printing.DocumentCompanySnapshot": 1, "sales.CustomerLedgerEntry": 1,
                                           "sales.SalesInvoice": 1, "sales.SalesLine": 2, "taxes.SalesLineTax": 2})

    def test_a_takeaway_kitchen_dish_made_from_its_ingredients_at_payment(self):
        from inventory.services import recalculate_item_average_cost

        from hesba_testing.factories import stock_in

        from . import kitchen

        self.vat(False)
        rice = make_item(item_code="R-RICE", item_name="أرز", unit="كجم", is_stock_tracked=True)
        stock_in(rice, self.location, 20, "30.00")
        recalculate_item_average_cost(rice)
        save_ingredients(self.koshary, [(rice, "0.200")], self.owner)  # 6.00 a plate
        dish = kitchen.make_in_kitchen(self.koshary, self.owner)
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY)
        services.add_item(order, dish, self.owner, 2)
        invoice, change, rows = self.pay(order, tendered=D("120.00"))
        self.assertEqual(change, D("0.00"))
        self.assertEqual(rows["invoice"], ("posted", "paid", D("120.00"), D("0.00"), D("0.00"), D("120.00"), D("120.00"), D("0.00"),
                                           "WALK-IN", "HALL", "TILL", f"طلب مطعم {order.number} (تيك أواي)", "resto_owner"))
        self.assertEqual(rows["lines"], [("D-KOSH", "", D("2.000"), D("60.00"), D("0.00"), D("120.00"), D("6.0000"), D("12.00"), D("108.00"))])
        self.assertEqual(rows["tax"], [])
        self.assertEqual(rows["cash"], [("TILL", "sales_receipt", "in", D("120.00"), True, "resto_owner", True)])
        self.assertEqual(rows["customer_ledger"], [])
        # Two plates are made first (0.4 kg of rice out at 30.00, two plates in at 6.00), then sold at that cost.
        self.assertEqual(rows["stock"], [("R-RICE", "HALL", "adjustment_out", D("0.400"), D("30.0000"), False),
                                         ("D-KOSH", "HALL", "adjustment_in", D("2.000"), D("6.0000"), False),
                                         ("D-KOSH", "HALL", "sale_out", D("2.000"), D("6.0000"), True)])
        self.assertEqual(rows["audit"], [("inventory", "adjust_stock"), ("inventory", "adjust_stock"), ("manufacturing", "produce")] + self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [
            ("sales_invoice", (("1101", D("120.00"), D("0.00")), ("1105", D("0.00"), D("12.00")), ("4101", D("0.00"), D("120.00")), ("5101", D("12.00"), D("0.00")))),
            ("stock_operation", (("1105", D("0.00"), D("12.00")), ("5101", D("12.00"), D("0.00")))),
            ("stock_operation", (("1105", D("12.00"), D("0.00")), ("5101", D("0.00"), D("12.00")))),
        ])
        self.assertEqual(rows["written"], {"audit.AuditLog": 6, "cashboxes.CashboxMovement": 1, "inventory.StockMovement": 3, "inventory.StockOperation": 2,
                                           "manufacturing.ProductionConsumption": 1, "manufacturing.ProductionRun": 1, "master_data.Customer": 1,
                                           "printing.DocumentCompanySnapshot": 1, "sales.SalesInvoice": 1, "sales.SalesLine": 1})
        self.assertEqual(SalesInvoice.objects.count(), 1)
