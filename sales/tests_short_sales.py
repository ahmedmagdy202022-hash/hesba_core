"""HG-065: selling an item that is not in stock, and settling its cost when the stock arrives."""

from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import (
    add_purchase_line,
    add_sales_line,
    make_cashbox,
    make_customer,
    make_draft_purchase_invoice,
    make_draft_sales_invoice,
    make_item,
    make_location,
    make_supplier,
    make_user,
    make_user_profile,
    make_seeded_role,
    recalculate_invoice_totals,
)
from inventory.services import (
    adjust_stock,
    get_item_authoritative_average_cost,
    get_item_location_stock_quantity,
    get_item_stock_quantity,
    get_item_stock_value,
    transfer_stock,
)
from permissions.models import RoleCode
from purchases.services import cancel_posted_purchase_invoice, post_purchase_invoice

from . import short_sales
from .models import SalesInvoice, ShortSale, ShortSaleEntry
from .services import (
    cancel_posted_sales_invoice,
    cancel_sales_return,
    create_sales_return,
    post_sales_invoice,
)

DAY = date(2026, 3, 2)


class ShortSaleCase(TestCase):
    def setUp(self):
        self.owner = make_user("owner_hg065", is_superuser=True)
        self.location = make_location()
        self.cashbox = make_cashbox()
        self.item = make_item(default_purchase_price=D("7.00"))
        self.supplier = make_supplier()
        self.customer = make_customer()
        self.numbers = 0

    def buy(self, quantity, price, day=DAY, location=None):
        self.numbers += 1
        invoice = make_draft_purchase_invoice(supplier=self.supplier, location=location or self.location, cashbox=self.cashbox,
                                              invoice_number=f"PI-{self.numbers}", invoice_date=day)
        add_purchase_line(invoice, self.item, quantity, price)
        post_purchase_invoice(invoice.pk, self.owner)
        return invoice

    def draft(self, quantity, price="30.00", day=DAY, location=None):
        self.numbers += 1
        invoice = make_draft_sales_invoice(customer=self.customer, location=location or self.location, cashbox=self.cashbox,
                                           invoice_number=f"SI-{self.numbers}", invoice_date=day)
        add_sales_line(invoice, self.item, quantity, price)
        recalculate_invoice_totals(invoice)
        return invoice

    def sell(self, quantity, price="30.00", day=DAY, allow_short=True, location=None):
        invoice = self.draft(quantity, price, day, location)
        post_sales_invoice(invoice.pk, self.owner, allow_short=allow_short)
        return SalesInvoice.objects.get(pk=invoice.pk)

    def switch_on(self):
        short_sales.set_enabled(True, self.owner)

    def assert_books(self):
        from ledger import reports
        from ledger.projector import Projector

        Projector().rebuild()
        result = reports.reconciliation()
        for row in result["rows"]:
            self.assertEqual(row["difference"], 0, row)
        self.assertTrue(result["balanced"])
        return reports

    def value(self):
        return get_item_stock_value(self.item)


class SwitchTests(ShortSaleCase):
    def test_off_by_default_and_a_short_sale_is_refused_as_before(self):
        self.buy(2, "10.00")
        invoice = self.draft(5)
        self.assertFalse(short_sales.enabled())
        # The same message as before (the quantities print as the database returns them: 2 or 2.000).
        with self.assertRaisesRegex(ValidationError, r"Not enough stock for item Test item\. Available: 2(\.000)?, required: 5\.000\."):
            post_sales_invoice(invoice.pk, self.owner)
        # The seller's agreement counts for nothing while the owner has not allowed it.
        with self.assertRaises(short_sales.StockShortage):
            post_sales_invoice(invoice.pk, self.owner, allow_short=True)
        self.assertEqual(SalesInvoice.objects.get(pk=invoice.pk).status, "draft")
        self.assertFalse(ShortSale.objects.exists())
        self.assertEqual(get_item_stock_quantity(self.item), D("2"))

    def test_only_the_owner_switches_it(self):
        manager = make_user("manager_hg065")
        make_user_profile(manager, make_seeded_role(RoleCode.MANAGER))
        with self.assertRaises(PermissionDenied):
            short_sales.set_enabled(True, manager)
        self.assertFalse(short_sales.enabled())
        owner_by_role = make_user("owner_role_hg065")
        make_user_profile(owner_by_role, make_seeded_role(RoleCode.OWNER))
        self.assertTrue(short_sales.set_enabled(True, owner_by_role))
        self.assertTrue(short_sales.enabled())
        self.assertTrue(AuditLog.objects.filter(action="sell_short_setting", actor=owner_by_role).exists())
        self.assertTrue(short_sales.set_enabled(False, self.owner))
        self.assertFalse(short_sales.enabled())

    def test_lines_of_one_item_are_checked_together(self):
        # Two lines of 3 each against 4 on hand: each fits alone, together they do not.
        self.buy(4, "10.00")
        invoice = self.draft(3)
        add_sales_line(invoice, self.item, 3, "30.00")
        recalculate_invoice_totals(invoice)
        with self.assertRaises(short_sales.StockShortage) as caught:
            post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual([(row["available"], row["required"]) for row in caught.exception.shortages], [(D("4"), D("6"))])

    def test_with_it_on_the_seller_must_still_agree(self):
        self.switch_on()
        invoice = self.draft(5)
        with self.assertRaises(short_sales.StockShortage) as caught:
            post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual([(row["item"], row["available"], row["required"]) for row in caught.exception.shortages],
                         [(self.item, D("0"), D("5"))])


class SellingShortTests(ShortSaleCase):
    def setUp(self):
        super().setUp()
        self.switch_on()

    def test_the_agreed_sale_posts_below_zero_with_the_estimate_and_is_logged(self):
        self.buy(2, "10.00")
        self.sell(2)  # the shelf is empty now; the last purchase cost 10
        invoice = self.sell(5)
        line = invoice.lines.get()
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(get_item_location_stock_quantity(self.item, self.location), D("-5"))
        short = line.short_sale
        self.assertEqual((short.location_short, short.estimated_quantity, short.estimated_unit_cost, short.estimate_source),
                         (D("5"), D("5"), D("10.0000"), "last_purchase"))
        self.assertEqual(short.agreed_by, self.owner)
        self.assertEqual((line.unit_cost, line.line_cost_amount, line.line_profit_amount), (D("10.0000"), D("50.00"), D("100.00")))
        log = AuditLog.objects.get(action="sell_short", object_id=str(invoice.pk))
        self.assertEqual(log.actor, self.owner)
        self.assertEqual(log.after_data["items"][0]["required"], "5.000")
        self.assertEqual(log.after_data["source"], "invoice")
        self.assertEqual(self.value(), D("-50.0000"))
        self.assert_books()

    def test_with_no_purchase_the_estimate_is_the_purchase_price(self):
        invoice = self.sell(3)
        short = invoice.lines.get().short_sale
        self.assertEqual((short.estimated_unit_cost, short.estimate_source), (D("7.0000"), "purchase_price"))

    def test_the_purchase_settles_the_cost_as_if_it_had_come_first(self):
        self.buy(2, "10.00")
        self.sell(2)
        invoice = self.sell(5)
        self.buy(8, "12.00", day=DAY + timedelta(days=3))
        short = invoice.lines.get().short_sale
        entry = short.entries.get()
        self.assertEqual((entry.kind, entry.quantity, entry.unit_cost, entry.amount, entry.entry_date),
                         ("settlement", D("5"), D("12.0000"), D("10.00"), DAY + timedelta(days=3)))
        self.assertEqual(short_sales.open_quantity(short), 0)
        # 3 units left at 12 each, as if the 8 had been bought before the sale.
        self.assertEqual(get_item_stock_quantity(self.item), D("3"))
        self.assertEqual(self.value(), D("36.0000"))
        self.assertEqual(get_item_authoritative_average_cost(self.item), D("12.0000"))
        # The posted line is not rewritten; the profit report carries the settlement on the purchase's day.
        line = invoice.lines.get()
        self.assertEqual(line.line_cost_amount, D("50.00"))
        from reports.selectors import profit_report, profit_totals

        self.assertEqual(profit_totals()["cost"], D("20.00") + D("60.00"))
        settled = [row for row in profit_report() if row.get("is_settlement")]
        self.assertEqual([(row["invoice_number"], row["cost_amount"], row["invoice_date"]) for row in settled],
                         [(invoice.invoice_number, D("10.00"), DAY + timedelta(days=3))])
        reports = self.assert_books()
        self.assertEqual(reports.control_balance("cogs"), D("80.00"))
        self.assertTrue(reports.trial_balance()["balanced"])

    def test_stock_that_covers_part_of_the_line_is_costed_at_its_average(self):
        self.buy(2, "10.00")
        invoice = self.sell(5)  # 2 at the average 10, 3 at the estimate 10 (the same purchase)
        short = invoice.lines.get().short_sale
        self.assertEqual((short.location_short, short.estimated_quantity), (D("3"), D("3")))
        self.buy(10, "13.00", day=DAY + timedelta(days=1))
        self.assertEqual(short.entries.get().amount, D("9.00"))
        self.assertEqual(self.value(), D("91.0000"))  # 7 at 13
        self.assert_books()

    def test_a_blend_of_average_and_estimate(self):
        self.buy(1, "10.00")
        self.buy(1, "14.00")  # average 12, last purchase 14
        invoice = self.sell(4)
        line = invoice.lines.get()
        self.assertEqual(line.unit_cost, D("13.0000"))  # (2 x 12 + 2 x 14) / 4
        self.assertEqual(line.short_sale.estimated_unit_cost, D("14.0000"))

    def test_a_purchase_smaller_than_the_hole_settles_what_it_covers(self):
        self.buy(1, "10.00")
        self.sell(1)
        invoice = self.sell(5)
        short = invoice.lines.get().short_sale
        self.buy(3, "11.00", day=DAY + timedelta(days=1))
        self.assertEqual(short_sales.open_quantity(short), D("2"))
        self.assertEqual(get_item_stock_quantity(self.item), D("-2"))
        self.assertEqual(self.value(), D("-20.0000"))  # the 2 still short, at the estimate
        self.buy(4, "14.00", day=DAY + timedelta(days=2))
        self.assertEqual(short_sales.open_quantity(short), 0)
        self.assertEqual(sorted(entry.amount for entry in short.entries.all()), [D("3.00"), D("8.00")])
        self.assertEqual(self.value(), D("28.0000"))  # 2 at 14
        self.assert_books()

    def test_oldest_short_sale_is_settled_first(self):
        first = self.sell(2)
        second = self.sell(2)
        self.buy(3, "9.00", day=DAY + timedelta(days=1))
        self.assertEqual(short_sales.open_quantity(first.lines.get().short_sale), 0)
        self.assertEqual(short_sales.open_quantity(second.lines.get().short_sale), D("1"))

    def test_short_at_the_warehouse_but_not_overall_needs_no_estimate(self):
        other = make_location("SECOND", name_ar="فرع", name_en="Branch")
        self.buy(5, "10.00", location=other)
        invoice = self.sell(3)
        short = invoice.lines.get().short_sale
        self.assertEqual((short.location_short, short.estimated_quantity), (D("3"), D("0")))
        self.assertEqual(invoice.lines.get().unit_cost, D("10.0000"))
        transfer_stock("TR-1", DAY, self.item, other, self.location, D("3"), self.owner, reason="refill")
        self.assertEqual(get_item_location_stock_quantity(self.item, self.location), 0)
        self.assertFalse(ShortSaleEntry.objects.exists())
        self.assert_books()

    def test_a_sale_from_stock_while_the_item_is_short_elsewhere_takes_the_estimate(self):
        other = make_location("SECOND", name_ar="فرع", name_en="Branch")
        self.buy(1, "10.00")
        self.sell(4)  # MAIN at -3
        self.buy(2, "12.00", location=other, day=DAY + timedelta(days=1))  # settles 2 of the 3
        invoice = self.sell(2, location=other, allow_short=False, day=DAY + timedelta(days=1))  # SECOND has 2, the item has -1
        short = invoice.lines.get().short_sale
        self.assertEqual((short.location_short, short.estimated_quantity, short.agreed_by), (D("0"), D("2"), None))
        self.assertGreater(invoice.lines.get().unit_cost, 0)
        self.assert_books()

    def test_opening_stock_entered_after_the_sale_settles_it(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from imports.screen_services import import_batch, upload_batch

        invoice = self.sell(3)  # nothing entered yet: the item's purchase price, 7
        text = f"item_code,location_code,movement_date,quantity,unit_cost\n{self.item.item_code},{self.location.location_code},{DAY},10,5\n"
        batch = upload_batch(target_type="stock", uploaded=SimpleUploadedFile("stock.csv", text.encode(), content_type="text/csv"),
                             user=self.owner)
        import_batch(batch.pk, self.owner)
        self.assertEqual(invoice.lines.get().short_sale.entries.get().amount, D("-6.00"))  # 3 x (5 - 7)
        self.assertEqual(self.value(), D("35.0000"))  # 7 left at 5
        self.assert_books()

    def test_a_stock_count_that_finds_stock_settles_too(self):
        self.buy(1, "10.00")
        self.sell(1)
        invoice = self.sell(2)
        adjust_stock("ADJ-1", DAY + timedelta(days=1), self.item, self.location, "in", D("4"), "found in the back", self.owner,
                     unit_cost=D("11.00"))
        self.assertEqual(invoice.lines.get().short_sale.entries.get().amount, D("2.00"))
        self.assertEqual(self.value(), D("22.0000"))
        self.assert_books()


class ReversalTests(ShortSaleCase):
    def setUp(self):
        super().setUp()
        self.switch_on()
        self.buy(1, "10.00")
        self.sell(1)

    def test_cancelling_a_settled_short_sale_leaves_the_books_as_if_it_never_happened(self):
        invoice = self.sell(5)
        self.buy(8, "12.00", day=DAY + timedelta(days=1))
        cancel_posted_sales_invoice(invoice.pk, self.owner, reason="wrong customer")
        self.assertEqual(get_item_stock_quantity(self.item), D("8"))
        self.assertEqual(self.value(), D("96.0000"))
        short = invoice.lines.get().short_sale
        self.assertEqual(short_sales.settled_amount(short), 0)
        from reports.selectors import profit_totals

        self.assertEqual(profit_totals()["cost"], D("10.00"))  # only the first, ordinary sale
        self.assert_books()

    def test_cancelling_before_the_stock_arrives(self):
        invoice = self.sell(5)
        cancel_posted_sales_invoice(invoice.pk, self.owner, reason="wrong customer")
        self.assertEqual(get_item_stock_quantity(self.item), 0)
        self.assertEqual(self.value(), 0)
        self.buy(3, "12.00", day=DAY + timedelta(days=1))
        self.assertFalse(ShortSaleEntry.objects.exists())
        self.assertEqual(self.value(), D("36.0000"))
        self.assert_books()

    def test_a_partial_return_before_the_stock_arrives_closes_its_share(self):
        invoice = self.sell(4)
        line = invoice.lines.get()
        create_sales_return("SR-1", DAY, invoice.pk, [{"source_line": line, "quantity": D("1")}], "too many", self.owner)
        short = line.short_sale
        self.assertEqual(short_sales.open_quantity(short), D("3"))
        self.assertEqual(get_item_stock_quantity(self.item), D("-3"))
        self.buy(5, "12.00", day=DAY + timedelta(days=1))
        self.assertEqual(short_sales.settled_amount(short), D("6.00"))  # 3 x (12 - 10)
        self.assertEqual(self.value(), D("24.0000"))  # 2 at 12
        self.assert_books()

    def test_a_return_after_the_settlement_comes_back_at_the_settled_cost(self):
        invoice = self.sell(4)
        self.buy(6, "12.00", day=DAY + timedelta(days=1))
        line = invoice.lines.get()
        sales_return = create_sales_return("SR-1", DAY + timedelta(days=2), invoice.pk, [{"source_line": line, "quantity": D("2")}],
                                           "changed mind", self.owner)
        returned = sales_return.lines.get()
        self.assertEqual((returned.unit_cost, returned.cost_amount), (D("12.0000"), D("24.00")))
        self.assertEqual(self.value(), D("48.0000"))  # 2 + 2 back, all at 12
        self.assert_books()
        cancel_sales_return(sales_return.pk, DAY + timedelta(days=3), "entered by mistake", self.owner)
        self.assertEqual(self.value(), D("24.0000"))
        self.assert_books()

    def test_reversing_a_return_reopens_what_it_closed(self):
        invoice = self.sell(4)
        line = invoice.lines.get()
        self.buy(1, "12.00", day=DAY + timedelta(days=1))  # 1 settled, 3 open, the item at -3
        sales_return = create_sales_return("SR-1", DAY + timedelta(days=1), invoice.pk, [{"source_line": line, "quantity": D("2")}],
                                           "changed mind", self.owner)
        release = sales_return.short_sale_entries.get(kind="release")
        self.assertEqual(release.quantity, D("1.5"))  # half the open units went back with half the line
        self.assertEqual(get_item_stock_quantity(self.item), D("-1"))
        self.assertEqual(short_sales.open_quantity(line.short_sale), D("1"))  # and 0.5 settled by the stock back
        self.buy(3, "12.00", day=DAY + timedelta(days=2))
        cancel_sales_return(sales_return.pk, DAY + timedelta(days=3), "entered by mistake", self.owner)
        self.assertTrue(ShortSaleEntry.objects.filter(reversal_of=release, quantity=D("-1.5")).exists())
        self.assertEqual(short_sales.open_quantity(line.short_sale), 0)  # reopened, then settled by the stock there
        self.assertEqual(get_item_stock_quantity(self.item), 0)
        self.assertEqual(self.value(), 0)
        self.assert_books()

    def test_a_full_return_of_an_unsettled_line_closes_it(self):
        invoice = self.sell(3)
        line = invoice.lines.get()
        create_sales_return("SR-1", DAY, invoice.pk, [{"source_line": line, "quantity": D("3")}], "not wanted", self.owner)
        self.assertEqual(short_sales.open_quantity(line.short_sale), 0)
        self.assertEqual(get_item_stock_quantity(self.item), 0)
        self.buy(2, "12.00", day=DAY + timedelta(days=1))
        self.assertFalse(ShortSaleEntry.objects.filter(kind="settlement").exists())
        self.assertEqual(self.value(), D("24.0000"))
        self.assert_books()

    def test_cancelling_the_purchase_that_settled_reopens_the_sale(self):
        invoice = self.sell(3)
        purchase = self.buy(5, "12.00", day=DAY + timedelta(days=1))
        short = invoice.lines.get().short_sale
        self.assertEqual(short_sales.open_quantity(short), 0)
        cancel_posted_purchase_invoice(purchase.pk, self.owner, reason="wrong supplier")
        self.assertEqual(short_sales.open_quantity(short), D("3"))
        self.assertEqual(short_sales.settled_amount(short), 0)
        self.assertEqual(self.value(), D("-30.0000"))  # back to the 3 short at the estimate
        self.buy(3, "15.00", day=DAY + timedelta(days=2))
        self.assertEqual(short_sales.settled_amount(short), D("15.00"))
        self.assertEqual(self.value(), 0)
        self.assert_books()


class ScreenTests(TestCase):
    """The till, the invoice, the offline sync and the owner's page, with the switch off and on."""

    def setUp(self):
        import uuid  # noqa: F401  (used by offline())

        from django.utils import timezone

        from hesba_testing.factories import open_test_shift, stock_in
        from inventory.services import recalculate_item_average_cost
        from reports.tests_dashboard import prepared_client

        prepared_client()
        self.owner = make_user("owner_screens", is_superuser=True)
        self.cashier = make_user("cashier_screens")
        make_user_profile(self.cashier, make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(self.cashier)
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        open_test_shift(self.cashier, self.cashbox)
        self.shirt = make_item(item_code="SHIRT", item_name="Shirt", default_sale_price="240.00")
        stock_in(self.shirt, self.location, 2, "100.00", movement_date=timezone.localdate())
        recalculate_item_average_cost(self.shirt)

    def till(self, qty, **extra):
        from .pos import walk_in_customer

        data = {"line_count": "1", "customer": str(walk_in_customer().pk), "location": str(self.location.pk), "cashbox": str(self.cashbox.pk),
                "discount": "0", "tendered": "2400", "print": "0", "item_0": str(self.shirt.pk), "qty_0": qty, "price_0": "240.00"}
        data.update(extra)
        return self.client.post("/sales/pos/", data)

    def test_the_till_refuses_as_before_while_it_is_off(self):
        page = self.till("5")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "op-message-error")
        self.assertNotContains(page, "data-short-box")
        self.till("5", allow_short="1")  # a forged agreement does nothing while the owner has not allowed it
        self.assertFalse(SalesInvoice.objects.exists())

    def test_the_till_asks_then_sells_with_the_cashiers_agreement(self):
        short_sales.set_enabled(True, self.owner)
        page = self.till("5")
        self.assertContains(page, "data-short-box")
        self.assertContains(page, "المطلوب 5")
        self.assertContains(page, 'name="qty_0" value="5"')  # the same sale, ready to send again
        self.assertFalse(SalesInvoice.objects.exists())
        done = self.till("5", allow_short="1")
        invoice = SalesInvoice.objects.get()
        self.assertRedirects(done, f"/sales/pos/?lang=ar&last={invoice.pk}", fetch_redirect_response=False)
        self.assertEqual(get_item_location_stock_quantity(self.shirt, self.location), D("-3"))
        log = AuditLog.objects.get(action="sell_short")
        self.assertEqual((log.actor, log.after_data["source"]), (self.cashier, "pos"))

    def test_the_invoice_shows_the_box_and_posts_with_agreement(self):
        from .models import SalesLine

        invoice = make_draft_sales_invoice(customer=make_customer(), location=self.location, cashbox=self.cashbox, invoice_number="SI-BOX",
                                           invoice_date=DAY)
        add_sales_line(invoice, self.shirt, 4, "240.00")
        recalculate_invoice_totals(invoice)
        detail = f"/sales/{invoice.pk}/"
        self.assertNotContains(self.client.get(detail), "data-short-box")
        short_sales.set_enabled(True, self.owner)
        page = self.client.get(detail)
        self.assertContains(page, "data-short-box")
        self.assertContains(page, 'name="allow_short" value="1"')
        self.client.post(f"/sales/{invoice.pk}/post/", {"lang": "ar"})
        self.assertEqual(SalesInvoice.objects.get(pk=invoice.pk).status, "draft")
        self.client.post(f"/sales/{invoice.pk}/post/", {"lang": "ar", "allow_short": "1"})
        self.assertEqual(SalesInvoice.objects.get(pk=invoice.pk).status, "posted")
        self.assertEqual(SalesLine.objects.get(invoice=invoice).short_sale.estimated_quantity, D("2"))
        self.assertEqual(AuditLog.objects.get(action="sell_short").after_data["source"], "invoice")

    def test_an_offline_sale_after_the_stock_ran_out(self):
        import json
        import uuid

        from django.utils import timezone

        def offline():
            body = {"key": str(uuid.uuid4()), "recorded_at": timezone.now().isoformat(), "customer": "", "location": str(self.location.pk),
                    "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "1200",
                    "lines": [{"id": self.shirt.pk, "qty": "5", "price": "240.00", "unit": None, "serial": None}]}
            return self.client.post("/sales/pos/sync/", json.dumps(body), content_type="application/json").json()

        self.assertEqual(offline()["status"], "refused")
        short_sales.set_enabled(True, self.owner)
        self.assertEqual(offline()["status"], "posted")
        self.assertEqual(get_item_location_stock_quantity(self.shirt, self.location), D("-3"))
        self.assertEqual(AuditLog.objects.get(action="sell_short").after_data["source"], "offline")

    def test_only_the_owner_switches_it_on_the_page_and_the_stock_shows_below_zero(self):
        manager = make_user("manager_screens")
        make_user_profile(manager, make_seeded_role(RoleCode.MANAGER))
        self.client.force_login(manager)
        page = self.client.get("/sales/short-sales/")
        self.assertContains(page, "صاحب الحساب بس")
        self.assertNotContains(page, "data-sell-short-toggle")
        self.assertEqual(self.client.post("/sales/short-sales/", {"enabled": "1"}).status_code, 403)
        self.assertFalse(short_sales.enabled())
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/sales/short-sales/?lang=en"), "data-sell-short-toggle")
        self.client.post("/sales/short-sales/", {"enabled": "1", "lang": "ar"})
        self.assertTrue(short_sales.enabled())
        invoice = make_draft_sales_invoice(customer=make_customer(), location=self.location, cashbox=self.cashbox, invoice_number="SI-RED",
                                           invoice_date=DAY)
        add_sales_line(invoice, self.shirt, 5, "240.00")
        recalculate_invoice_totals(invoice)
        post_sales_invoice(invoice.pk, self.owner, allow_short=True)
        listing = self.client.get("/sales/short-sales/")
        self.assertContains(listing, "SI-RED")
        self.assertContains(listing, "مستني البضاعة")
        stock = self.client.get(reverse("inventory:stock"))
        self.assertContains(stock, "data-below-zero")
        self.assertContains(stock, "data-short-sales-link")
        self.assertContains(self.client.get("/settings/capabilities/"), "data-sell-short-link")
