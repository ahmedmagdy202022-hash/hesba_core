"""QA-001: one full trading month of a small shop, end to end, reconciled.

Every document goes through the real services (drafts, posting, payments,
returns, expenses, closing). The expected figures below are worked out by hand
from the story, not read back from the code, and every report that shows the
same number must agree with the others: cashbox, stock, customer and supplier
balances, profit, aging, the dashboard analytics and the closing snapshot.

The story (last calendar month; day numbers are dates in that month):

  day 1   owner puts 20,000 capital into the till; pays rent 800
  day 2   purchase P1 from Delta (paid 5,000 of 8,000):
            rice 100 x 20, oil 50 x 60, tea 200 x 15
  day 3..28  every day a walk-in buys 2 rice x 30 + 3 tea x 25 = 135 cash
  day 5   credit sale to Karim: 10 oil x 80 = 800, pays 300
  day 10  purchase P2 from Delta on credit: 50 rice x 22 = 1,100  (before the day's sale)
  day 15  Karim pays 400
  day 18  Karim returns 2 oil (160): 60 back in cash, 100 off his balance
  day 20  shop pays Delta 2,000
  day 22  shop returns 20 tea from P1 (300): 187.50 cash back, 112.50 off the balance
  day 25  electricity 450
  month end  the books are closed
"""

from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from cashboxes.models import CashboxOperationType
from cashboxes.services import create_cashbox_operation, get_cashbox_balance
from closing.models import Period, PeriodStatus, PeriodSummary
from closing.services import complete_period_closing
from expenses.models import ExpenseCategory
from expenses.services import expense_total, record_expense
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_supplier, make_user, make_user_profile
from inventory.models import StockMovement
from inventory.services import get_item_stock_quantity, get_item_stock_value
from permissions.models import RoleCode
from purchases.services import create_purchase_draft, create_purchase_return, post_purchase_invoice, record_supplier_payment
from sales.models import SalesLine
from sales.pos import walk_in_customer
from sales.services import create_sales_draft, create_sales_return, post_sales_invoice, record_customer_payment

from .aging import aging_rows
from .analytics import build_analytics
from .selectors import cashbox_report, customer_report, profit_totals, stock_report, supplier_report
from .tests_analytics import EVERYTHING, by_key


def last_month():
    first_this_month = timezone.localdate().replace(day=1)
    end = first_this_month - timedelta(days=1)
    return end.replace(day=1), end


class OneTradingMonthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.start, cls.end = last_month()
        owner = make_user(username="month_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        cls.owner = owner
        cls.shop = make_location(location_code="SHOP")
        cls.till = make_cashbox(cashbox_code="TILL")
        cls.rice = make_item(item_code="RICE", item_name="Rice 1kg")
        cls.oil = make_item(item_code="OIL", item_name="Oil 1L")
        cls.tea = make_item(item_code="TEA", item_name="Tea 250g")
        cls.delta = make_supplier(supplier_code="DELTA", name="Delta Foods")
        cls.karim = make_customer(customer_code="KARIM", name="Karim", phone="01001112223")
        walk_in = walk_in_customer()
        d = lambda n: cls.start.replace(day=n)  # noqa: E731

        create_cashbox_operation("CAP-1", d(1), CashboxOperationType.DIRECT_IN, D("20000"), "رأس المال", owner, destination_cashbox=cls.till)
        record_expense(category=ExpenseCategory.objects.get(code="rent"), cashbox=cls.till, amount=D("800"), expense_date=d(1), description="إيجار الشهر", user=owner)

        cls.p1 = cls._purchase("P1", d(2), D("5000"), [(cls.rice, 100, "20"), (cls.oil, 50, "60"), (cls.tea, 200, "15")])
        karim_sale = None
        for n in range(3, 29):
            if n == 5:
                karim_sale = cls._sale(f"K-{n}", d(n), cls.karim, D("300"), [(cls.oil, 10, "80")])
            if n == 10:
                cls._purchase("P2", d(n), D("0"), [(cls.rice, 50, "22")])
            cls._sale(f"W-{n:02d}", d(n), walk_in, D("135"), [(cls.rice, 2, "30"), (cls.tea, 3, "25")])
            if n == 15:
                record_customer_payment("RC-1", d(n), cls.karim, cls.till, D("400"), user=owner)
            if n == 18:
                oil_line = SalesLine.objects.get(invoice=karim_sale, item=cls.oil)
                create_sales_return("SR-1", d(n), karim_sale.pk, [{"source_line": oil_line, "quantity": D("2")}], "عبوة تالفة", owner)
            if n == 20:
                record_supplier_payment("SP-1", d(n), cls.delta, cls.till, D("2000"), user=owner)
            if n == 22:
                tea_line = cls.p1.lines.get(item=cls.tea)
                create_purchase_return("PR-1", d(n), cls.p1.pk, [{"source_line": tea_line, "quantity": D("20")}], "تاريخ قريب", owner)
            if n == 25:
                record_expense(category=ExpenseCategory.objects.get(code="utilities"), cashbox=cls.till, amount=D("450"), expense_date=d(n), description="كهرباء", user=owner)

    @classmethod
    def _purchase(cls, number, when, paid, lines):
        invoice = create_purchase_draft(
            {"invoice_number": number, "invoice_date": when, "supplier": cls.delta, "receiving_location": cls.shop, "cashbox": cls.till, "paid_now": paid},
            [{"item": item, "quantity": D(qty), "unit_purchase_price": D(price)} for item, qty, price in lines],
            cls.owner,
        )
        post_purchase_invoice(invoice.pk, cls.owner)
        invoice.refresh_from_db()
        return invoice

    @classmethod
    def _sale(cls, number, when, customer, paid, lines):
        invoice = create_sales_draft(
            {"invoice_number": number, "invoice_date": when, "customer": customer, "selling_location": cls.shop, "cashbox": cls.till, "paid_now": paid},
            [{"item": item, "quantity": D(qty), "unit_sale_price": D(price)} for item, qty, price in lines],
            cls.owner,
        )
        post_sales_invoice(invoice.pk, cls.owner)
        invoice.refresh_from_db()
        return invoice

    # --- the figures a shop owner would check -------------------------------

    def test_cash_in_the_till(self):
        # 20,000 - 800 - 5,000 + 26 x 135 + 300 + 400 - 60 - 2,000 + 187.50 - 450
        self.assertEqual(get_cashbox_balance(self.till), D("16087.50"))
        report = {row["cashbox_code"]: row for row in cashbox_report()}
        self.assertEqual(report["TILL"]["balance"], D("16087.50"))

    def test_stock_on_the_shelf(self):
        expected = {"RICE": D("98"), "OIL": D("42"), "TEA": D("102")}  # 150-52, 50-10+2, 200-78-20
        for item in (self.rice, self.oil, self.tea):
            with self.subTest(item=item.item_code):
                self.assertEqual(get_item_stock_quantity(item), expected[item.item_code])
        shelf = {row["item_id"]: row for row in stock_report()}
        self.assertEqual({item.item_code: shelf[item.pk]["quantity"] for item in (self.rice, self.oil, self.tea)}, expected)
        # Oil and tea never changed cost. Rice: the movement ledger holds
        # 2,820 - 38 x 20.7353 = 2,032.0586, while the stock report shows
        # 98 x 20.7353 (the cached average) = 2,032.0594. The sub-cent gap is
        # rounding of a moving average; both are 2,032.06 on screen (QA-001 note).
        ledger = {"RICE": D("2032.0586"), "OIL": D("2520.0000"), "TEA": D("1530.0000")}
        for item in (self.rice, self.oil, self.tea):
            with self.subTest(value=item.item_code):
                self.assertEqual(get_item_stock_value(item), ledger[item.item_code])
                self.assertEqual(shelf[item.pk]["stock_value"].quantize(D("0.01")), ledger[item.item_code].quantize(D("0.01")))
        self.assertEqual(shelf[self.rice.pk]["stock_value"], D("2032.0594"))

    def test_who_owes_whom(self):
        customers = {row["customer_code"]: row["balance"] for row in customer_report()}
        self.assertEqual(customers["KARIM"], D("0.00"))  # 500 - 400 - 100
        self.assertEqual(customers.get("WALK-IN", D("0.00")), D("0.00"))
        suppliers = {row["supplier_code"]: row["balance"] for row in supplier_report()}
        self.assertEqual(suppliers["DELTA"], D("1987.50"))  # 3,000 + 1,100 - 2,000 - 112.50
        self.assertEqual([row["code"] for row in aging_rows("customers", self.end)], [])
        delta = aging_rows("suppliers", self.end)[0]
        self.assertEqual(delta["total"], D("1987.50"))

    def test_profit_for_the_month(self):
        totals = profit_totals(self.start, self.end)
        self.assertEqual(totals["sales"], D("4150.00"))  # 3,510 + 800 - 160
        # Cost: tea 78 x 15 + oil 8 x 60 + rice 14 x 20 (before P2) + rice 38 at the new average.
        rice_after_p2 = StockMovement.objects.filter(item=self.rice, movement_type="sale_out", movement_date__gte=self.start.replace(day=10))
        self.assertEqual({m.unit_cost for m in rice_after_p2}, {D("20.7353")})  # (86 x 20 + 1,100) / 136
        self.assertEqual(totals["cost"], D("1170.00") + D("480.00") + D("280.00") + D("787.93"))  # 19 x 41.47
        self.assertEqual(totals["profit"], D("1432.07"))
        self.assertEqual(expense_total(self.start, self.end), D("1250.00"))
        metrics = by_key(build_analytics(EVERYTHING, "month", self.end))
        self.assertEqual(metrics["net_sales"]["value"], D("4150.00"))
        self.assertEqual(metrics["gross_profit"]["value"], D("1432.07"))
        self.assertEqual(metrics["expenses"]["value"], D("1250.00"))
        self.assertEqual(metrics["net_profit"]["value"], D("182.07"))
        self.assertEqual(metrics["receivables"]["value"], D("0.00"))
        self.assertEqual(metrics["avg_invoice"]["count"], 27)

    def test_month_end_close_freezes_the_same_figures(self):
        period = Period.objects.get(start_date__lte=self.start, end_date__gte=self.end)
        self.assertEqual(period.status, PeriodStatus.OPEN)  # opened by the first cash entry
        run = complete_period_closing(period.pk, user=self.owner, reason="إقفال الشهر")
        snapshot = {row.summary_code: row.amount for row in PeriodSummary.objects.filter(closing_run=run)}
        self.assertEqual(snapshot["sales_total"], D("4310.00"))  # invoices before returns
        self.assertEqual(snapshot["purchase_total"], D("9100.00"))
        self.assertEqual(snapshot["profit_total"], D("1432.07"))
        self.assertEqual(snapshot["cashbox_balance_total"], D("16087.50"))
        self.assertEqual(snapshot["customer_balance_total"], D("0.00"))
        self.assertEqual(snapshot["supplier_balance_total"], D("1987.50"))
        self.assertEqual(snapshot["stock_value_total"], D("6082.06"))  # 2,032.06 + 2,520 + 1,530
        with self.assertRaises(ValidationError):
            self._sale("LATE", self.end, walk_in_customer(), D("30"), [(self.rice, 1, "30")])
        with self.assertRaises(ValidationError):
            record_expense(category=ExpenseCategory.objects.get(code="utilities"), cashbox=self.till, amount=D("5"), expense_date=self.end, description="late", user=self.owner)
        self.assertEqual(get_cashbox_balance(self.till), D("16087.50"))
        self.assertEqual(get_item_stock_quantity(self.rice), D("98"))
