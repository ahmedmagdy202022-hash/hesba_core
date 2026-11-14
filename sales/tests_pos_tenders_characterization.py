"""Restaurants R2b (HG-110): what a single-tender ``checkout`` writes today, pinned row by row before ``tenders`` exists.

Each scenario rings up one sale through ``sales.pos.checkout`` (the call the till, the table bill and the offline sync
make) and records every row it wrote: how many rows of each model, the invoice header and lines, the tax rows, the
customer-ledger rows, the cashbox and stock movements, the customer payments and settlements, the audit actions, and
the journal entries the projector builds from them. The expected values were taken from the code before R2b; after R2b
the same calls (no ``tenders``) must write exactly the same rows. This file is not edited after its first commit.

The table bill's own scenarios are in ``restaurant/tests_pay_characterization.py`` (R13) and, for a card machine
cashbox picked on the bill, ``restaurant/tests_pay_tenders_characterization.py``.

A shirt sells at 300.00 and costs 120.00 (stock tracked); a service sells at 100.00. VAT is 14% when on. The clock
stands still on Wednesday 11 Nov 2026 at noon in Cairo, as in ``restaurant/tests_branches``.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock

from django.apps import apps
from django.db import router
from django.test import TestCase
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from inventory.services import recalculate_item_average_cost
from ledger.models import JournalEntry
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled
from settlements.models import InvoiceSettlement
from taxes.models import SalesLineTax

from .models import CustomerLedgerEntry, CustomerPayment
from .pos import checkout, walk_in_customer
from .pos_buttons import save_card_cashbox

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)  # a Wednesday, 12:00 in Cairo
ZERO = D("0.00")


def _counts():
    return {model._meta.label: model.objects.count() for model in apps.get_models()
            if not model._meta.proxy and router.db_for_read(model) == "default"}


def _journal():
    Projector().rebuild()
    return Counter((entry.source_type, entry.entry_date,
                    tuple(sorted((line.account.code, line.debit, line.credit) for line in entry.lines.select_related("account"))))
                   for entry in JournalEntry.objects.prefetch_related("lines"))


class TillCharacterizationTests(TestCase):
    """Every row a single-tender till sale writes, so adding ``tenders`` to ``checkout`` cannot change one of them."""

    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.profile = prepared_client("commercial", "retail", "customers,items_services,sales_operations,cashboxes,reports,inventory")
        self.owner = make_user(username="tender_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cash = make_cashbox(cashbox_code="CASH", is_default=True)
        self.card = make_cashbox(cashbox_code="CARD", name_ar="ماكينة الفيزا")
        save_card_cashbox(self.card)
        self.shirt = make_item(item_code="SHIRT", item_name="قميص", default_sale_price="300.00")
        stock_in(self.shirt, self.location, 50, "120.00", movement_date=timezone.localdate() - timedelta(days=1))
        recalculate_item_average_cost(self.shirt)
        self.service = make_item(item_code="SRV", item_name="خدمة", is_stock_tracked=False)
        self.walk_in = walk_in_customer()

    def vat(self, on):
        set_capability_enabled(self.profile, "vat", on, user=self.owner)

    def line(self, item, price, quantity="1"):
        return {"item": item, "quantity": D(quantity), "unit_sale_price": D(price), "line_discount_amount": ZERO, "description": ""}

    def sell(self, lines, **kwargs):
        """Ring up ``lines`` and return (invoice, change, every row written)."""

        kwargs.setdefault("customer", self.walk_in)
        kwargs.setdefault("cashbox", self.cash)
        kwargs.setdefault("discount", ZERO)
        journal_before = _journal()
        counts_before = _counts()
        marks = {model: model.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
                 for model in (CashboxMovement, StockMovement, CustomerLedgerEntry, AuditLog, CustomerPayment, InvoiceSettlement)}
        invoice, change = checkout(lines=lines, location=self.location, user=self.owner, **kwargs)
        counts_after = _counts()
        written = {label: counts_after[label] - counts_before[label] for label in counts_after if counts_after[label] != counts_before[label]}
        new = {model: model.objects.filter(pk__gt=mark).order_by("pk") for model, mark in marks.items()}
        journal = sorted((key[0], key[2]) for key in (_journal() - journal_before).elements())
        today = timezone.localdate()
        invoice.refresh_from_db()
        self.assertEqual(invoice.invoice_number, "POS-20261111-0001")
        self.assertEqual(invoice.invoice_date, today)
        rows = {
            "written": written,
            "invoice": (invoice.status, invoice.payment_status, invoice.subtotal, invoice.discount_amount, invoice.tax_amount, invoice.total_amount,
                        invoice.paid_now, invoice.remaining_due, invoice.customer.customer_code, invoice.cashbox.cashbox_code if invoice.cashbox else None),
            "lines": [(line.item.item_code, line.quantity, line.unit_sale_price, line.line_total_amount, line.unit_cost, line.line_cost_amount,
                       line.line_profit_amount) for line in invoice.lines.select_related("item").order_by("pk")],
            "tax": [(row.line.item.item_code, row.rate, row.taxable_amount, row.tax_amount)
                    for row in SalesLineTax.objects.filter(line__invoice=invoice).select_related("line__item").order_by("line__pk")],
            "customer_ledger": [(row.customer.customer_code, row.entry_type, row.due_increase, row.due_decrease, row.entry_date == today,
                                 row.sales_invoice_id == invoice.pk, row.customer_payment_id is not None) for row in new[CustomerLedgerEntry]],
            "cash": [(row.cashbox.cashbox_code, row.movement_type, row.direction, row.amount, row.movement_date == today,
                      row.sales_invoice_id == invoice.pk, row.customer_payment_id is not None) for row in new[CashboxMovement]],
            "stock": [(row.item.item_code, row.movement_type, row.quantity, row.unit_cost) for row in new[StockMovement]],
            "payments": [(row.payment_number[:4], row.cashbox.cashbox_code, row.amount, row.status, row.payment_date == today) for row in new[CustomerPayment]],
            "settlements": [(row.invoice_id == invoice.pk, row.amount, row.source) for row in new[InvoiceSettlement]],
            "audit": [(row.module, row.action) for row in new[AuditLog]],
            "journal": journal,
        }
        return invoice, change, rows

    SALE_AUDIT = [("sales", "create_sales_draft"), ("sales", "post_sales_invoice")]

    def test_cash_with_vat_on_and_change(self):
        self.vat(True)
        invoice, change, rows = self.sell([self.line(self.shirt, "300.00")], tendered=D("400.00"))
        self.assertEqual(change, D("58.00"))
        self.assertEqual(rows["invoice"], ("posted", "paid", D("300.00"), D("0.00"), D("42.00"), D("342.00"), D("342.00"), D("0.00"), "WALK-IN", "CASH"))
        self.assertEqual(rows["lines"], [("SHIRT", D("1.000"), D("300.00"), D("300.00"), D("120.0000"), D("120.00"), D("180.00"))])
        self.assertEqual(rows["tax"], [("SHIRT", D("14.00"), D("300.00"), D("42.00"))])
        self.assertEqual(rows["cash"], [("CASH", "sales_receipt", "in", D("342.00"), True, True, False)])
        self.assertEqual(rows["customer_ledger"], [])
        self.assertEqual(rows["stock"], [("SHIRT", "sale_out", D("1.000"), D("120.0000"))])
        self.assertEqual((rows["payments"], rows["settlements"]), ([], []))
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("342.00"), D("0.00")), ("1105", D("0.00"), D("120.00")), ("2103", D("0.00"), D("42.00")),
                                                               ("4101", D("0.00"), D("300.00")), ("5101", D("120.00"), D("0.00"))))])
        self.assertEqual(rows["written"], {"audit.AuditLog": 2, "cashboxes.CashboxMovement": 1, "inventory.StockMovement": 1,
                                           "printing.DocumentCompanySnapshot": 1, "sales.SalesInvoice": 1, "sales.SalesLine": 1, "taxes.SalesLineTax": 1})

    def test_cash_with_vat_off(self):
        self.vat(False)
        invoice, change, rows = self.sell([self.line(self.shirt, "300.00"), self.line(self.service, "100.00", "2")], tendered=D("500.00"))
        self.assertEqual(change, D("0.00"))
        self.assertEqual(rows["invoice"], ("posted", "paid", D("500.00"), D("0.00"), D("0.00"), D("500.00"), D("500.00"), D("0.00"), "WALK-IN", "CASH"))
        self.assertEqual(rows["lines"], [("SHIRT", D("1.000"), D("300.00"), D("300.00"), D("120.0000"), D("120.00"), D("180.00")),
                                         ("SRV", D("2.000"), D("100.00"), D("200.00"), D("0.0000"), D("0.00"), D("200.00"))])
        self.assertEqual(rows["tax"], [])
        self.assertEqual(rows["cash"], [("CASH", "sales_receipt", "in", D("500.00"), True, True, False)])
        self.assertEqual((rows["customer_ledger"], rows["payments"], rows["settlements"]), ([], [], []))
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("500.00"), D("0.00")), ("1105", D("0.00"), D("120.00")),
                                                               ("4101", D("0.00"), D("500.00")), ("5101", D("120.00"), D("0.00"))))])
        self.assertEqual(rows["written"], {"audit.AuditLog": 2, "cashboxes.CashboxMovement": 1, "inventory.StockMovement": 1,
                                           "printing.DocumentCompanySnapshot": 1, "sales.SalesInvoice": 1, "sales.SalesLine": 2})

    def test_by_card_into_the_card_machine_with_vat_on(self):
        self.vat(True)
        invoice, change, rows = self.sell([self.line(self.service, "100.00")], cashbox=self.card, card=True, tendered=D("999.00"))
        self.assertEqual(change, D("0.00"))  # AN8: a card pays the total exactly
        self.assertEqual(rows["invoice"], ("posted", "paid", D("100.00"), D("0.00"), D("14.00"), D("114.00"), D("114.00"), D("0.00"), "WALK-IN", "CARD"))
        self.assertEqual(rows["tax"], [("SRV", D("14.00"), D("100.00"), D("14.00"))])
        self.assertEqual(rows["cash"], [("CARD", "sales_receipt", "in", D("114.00"), True, True, False)])
        self.assertEqual((rows["customer_ledger"], rows["stock"], rows["payments"], rows["settlements"]), ([], [], [], []))
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("114.00"), D("0.00")), ("2103", D("0.00"), D("14.00")), ("4101", D("0.00"), D("100.00"))))])
        self.assertEqual(rows["written"], {"audit.AuditLog": 2, "cashboxes.CashboxMovement": 1, "printing.DocumentCompanySnapshot": 1,
                                           "sales.SalesInvoice": 1, "sales.SalesLine": 1, "taxes.SalesLineTax": 1})

    def test_a_gift_card_part_with_cash_for_the_rest(self):
        from giftcards import services as giftcards

        self.vat(False)
        set_capability_enabled(self.profile, "gift_cards", True, user=self.owner)
        card = giftcards.sell(D("200.00"), self.cash, self.owner)
        invoice, change, rows = self.sell([self.line(self.shirt, "300.00")], tendered=D("150.00"), gift=(card, D("200.00")))
        self.assertEqual(change, D("50.00"))
        # 300.00: 200.00 from the card (a payment into the gift-card box, settled on the invoice), 100.00 cash now.
        self.assertEqual(rows["invoice"], ("posted", "partial", D("300.00"), D("0.00"), D("0.00"), D("300.00"), D("100.00"), D("200.00"), "WALK-IN", "CASH"))
        self.assertEqual(rows["cash"], [("CASH", "sales_receipt", "in", D("100.00"), True, True, False),
                                        ("GIFT-CARDS-1", "customer_payment", "in", D("200.00"), True, False, True)])
        self.assertEqual(rows["customer_ledger"], [("WALK-IN", "sales_due", D("200.00"), D("0.00"), True, True, False),
                                                   ("WALK-IN", "customer_payment", D("0.00"), D("200.00"), True, False, True)])
        self.assertEqual(rows["payments"], [("GCR-", "GIFT-CARDS-1", D("200.00"), "posted", True)])
        self.assertEqual(rows["settlements"], [(True, D("200.00"), "gift_card")])
        self.assertEqual(rows["stock"], [("SHIRT", "sale_out", D("1.000"), D("120.0000"))])
        self.assertEqual(rows["audit"], self.SALE_AUDIT + [("sales", "record_customer_payment"), ("giftcards", "redeem_gift_card")])
        self.assertEqual(rows["journal"], [
            ("customer_payment", (("1103", D("0.00"), D("200.00")), ("2110", D("200.00"), D("0.00")))),
            ("sales_invoice", (("1101", D("100.00"), D("0.00")), ("1103", D("200.00"), D("0.00")), ("1105", D("0.00"), D("120.00")),
                               ("4101", D("0.00"), D("300.00")), ("5101", D("120.00"), D("0.00")))),
        ])
        self.assertEqual(rows["written"], {"audit.AuditLog": 4, "cashboxes.CashboxMovement": 2, "giftcards.GiftCardEntry": 1, "inventory.StockMovement": 1,
                                           "printing.DocumentCompanySnapshot": 2, "sales.CustomerLedgerEntry": 2, "sales.CustomerPayment": 1,
                                           "sales.SalesInvoice": 1, "sales.SalesLine": 1, "settlements.InvoiceSettlement": 1})

    def test_a_named_customer_on_credit_for_the_rest_with_vat_on(self):
        self.vat(True)
        customer = make_customer(customer_code="C-CREDIT", name="شركة النور")
        invoice, change, rows = self.sell([self.line(self.shirt, "300.00")], customer=customer, tendered=D("100.00"))
        self.assertEqual(change, D("0.00"))
        self.assertEqual(rows["invoice"], ("posted", "partial", D("300.00"), D("0.00"), D("42.00"), D("342.00"), D("100.00"), D("242.00"), "C-CREDIT", "CASH"))
        self.assertEqual(rows["cash"], [("CASH", "sales_receipt", "in", D("100.00"), True, True, False)])
        self.assertEqual(rows["customer_ledger"], [("C-CREDIT", "sales_due", D("242.00"), D("0.00"), True, True, False)])
        self.assertEqual((rows["payments"], rows["settlements"]), ([], []))
        self.assertEqual(rows["audit"], self.SALE_AUDIT)
        self.assertEqual(rows["journal"], [("sales_invoice", (("1101", D("100.00"), D("0.00")), ("1103", D("242.00"), D("0.00")), ("1105", D("0.00"), D("120.00")),
                                                               ("2103", D("0.00"), D("42.00")), ("4101", D("0.00"), D("300.00")), ("5101", D("120.00"), D("0.00"))))])
        self.assertEqual(rows["written"], {"audit.AuditLog": 2, "cashboxes.CashboxMovement": 1, "inventory.StockMovement": 1, "printing.DocumentCompanySnapshot": 1,
                                           "sales.CustomerLedgerEntry": 1, "sales.SalesInvoice": 1, "sales.SalesLine": 1, "taxes.SalesLineTax": 1})

    def test_a_walk_in_short_of_the_total_is_refused_and_nothing_is_kept(self):
        from django.core.exceptions import ValidationError

        self.vat(True)
        before = _counts()
        with self.assertRaisesMessage(ValidationError, "err_walk_in_credit"):
            checkout(lines=[self.line(self.shirt, "300.00")], customer=self.walk_in, location=self.location, cashbox=self.cash, discount=ZERO,
                     tendered=D("341.99"), user=self.owner)
        self.assertEqual(_counts(), before)
