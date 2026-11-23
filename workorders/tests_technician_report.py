"""S9 / roadmap item 5: the technicians' report keeps its figures and gains a commission column.

The first class pins what the report already said (count, billed before VAT, average days, warranty returns)
so the commission change cannot move them. The second checks the new column against explicit amounts, taken
the way appointments take it: the posted invoice before tax, less its posted returns, times the employee's rate.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from sales.services import create_sales_return

from . import services
from .tests import TODAY, WorkshopSetup


class ReportSetup(WorkshopSetup):
    def setUp(self):
        super().setUp()
        self.tech.commission_percent = D("10.00")
        self.tech.save(update_fields=["commission_percent"])
        self.month = (TODAY.replace(day=1), TODAY)

    def delivered(self, *, parts=1, technician="tech", days_in_shop=0):
        """One delivered order: ``parts`` motors at 1800 and one labour line at 300 (no VAT), paid in full."""

        order = self.order(technician=self.tech if technician == "tech" else technician)
        if parts:
            services.add_line(order, self.part, self.owner, quantity=str(parts))
        services.add_line(order, self.labour, self.owner)
        order = self.to_ready(order)
        if days_in_shop:
            order.received_at = timezone.now() - timedelta(days=days_in_shop)
            order.save(update_fields=["received_at"])
        services.deliver(order, self.owner, paid_now=str(services.totals(order)["total"]), cashbox=self.cashbox)
        order.refresh_from_db()
        return order

    def row(self, technician=None):
        rows = {row["technician"]: row for row in services.technician_report(*self.month)}
        return rows[technician or self.tech]


class ExistingFiguresAreUnchangedTests(ReportSetup):
    """Characterization: these assertions held before the commission column was added."""

    def test_count_billed_average_days_and_returns(self):
        first = self.delivered(days_in_shop=3)  # 1800 + 300 = 2100
        self.delivered(parts=0)  # labour only: 300
        row = self.row()
        self.assertEqual((row["count"], row["billed"], row["returns"]), (2, D("2400.00"), 0))
        self.assertEqual(row["avg_days"], 1.5)  # (3 days + 0) / 2
        services.receive({"customer": self.customer, "device": "غسالة توشيبا", "problem": "رجعت", "technician": self.tech, "warranty_of": first}, self.owner)
        self.assertEqual(self.row()["returns"], 1)

    def test_billed_ignores_posted_returns_and_is_before_vat(self):
        order = self.delivered()
        labour_line = order.invoice.lines.get(item=self.labour)
        create_sales_return("SR-WO-1", TODAY, order.invoice.pk, [{"source_line": labour_line, "quantity": 1}], "refund", self.owner)
        self.assertEqual(self.row()["billed"], D("2100.00"))  # as before: the report's "billed" never netted returns

    def test_an_order_without_a_technician_is_its_own_row_and_the_page_still_opens(self):
        self.delivered(technician=None)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("workorders:report"))
        self.assertContains(page, 'data-tech="0"')
        self.assertContains(page, "data-tech-report")
        self.assertEqual(services.technician_report(*self.month)[0]["billed"], D("2100.00"))


class CommissionColumnTests(ReportSetup):
    """The new column, with explicit amounts (employee rate 10 %, no VAT)."""

    def test_commission_is_the_rate_on_the_posted_invoice_net_of_vat(self):
        self.delivered()  # 2100.00 -> 210.00
        self.delivered(parts=0)  # 300.00 -> 30.00
        row = self.row()
        self.assertEqual((row["net_sales"], row["rate"], row["commission"]), (D("2400.00"), D("10.00"), D("240.00")))
        self.assertEqual(row["billed"], D("2400.00"))  # the old figure beside it, untouched

    def test_a_posted_return_lowers_the_commission_but_not_billed(self):
        order = self.delivered()  # 2100.00
        create_sales_return("SR-WO-2", TODAY, order.invoice.pk, [{"source_line": order.invoice.lines.get(item=self.labour), "quantity": 1}], "refund", self.owner)
        row = self.row()
        self.assertEqual((row["billed"], row["net_sales"], row["commission"]), (D("2100.00"), D("1800.00"), D("180.00")))

    def test_the_rate_is_the_employees_own_and_a_zero_rate_earns_nothing(self):
        from staff.services import save_employee

        other = save_employee({"name": "فني بدون عمولة", "commission_percent": "0"}, self.owner)
        self.delivered()
        self.delivered(technician=other)
        self.assertEqual(self.row()["commission"], D("210.00"))
        self.assertEqual((self.row(other)["net_sales"], self.row(other)["commission"]), (D("2100.00"), D("0.00")))

    def test_an_order_with_no_technician_earns_no_commission(self):
        self.delivered(technician=None)
        row = services.technician_report(*self.month)[0]
        self.assertIsNone(row["technician"])
        self.assertEqual((row["net_sales"], row["commission"], row["rate"]), (D("2100.00"), D("0"), None))

    def test_a_cancelled_delivery_earns_nothing(self):
        from sales.services import cancel_posted_sales_invoice

        order = self.delivered()
        cancel_posted_sales_invoice(order.invoice.pk, self.owner, "غلط")
        services.sync_cancelled(self.owner)
        self.assertEqual(services.technician_report(*self.month), [])

    def test_the_page_shows_the_column(self):
        self.delivered()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("workorders:report"))
        self.assertContains(page, "<td data-tech-commission>210.00")
        self.assertContains(page, "10.00%")
        english = self.client.get(reverse("workorders:report") + "?lang=en")
        self.assertContains(english, "Commission")
        self.assertContains(english, "<td data-tech-commission>210.00")
