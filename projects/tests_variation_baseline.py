"""Contracting C1 (HG-103): the figures of a contract that has NO variation order, pinned to the piastre.

Written before any line of C1 and green on the code as it stood (``develop`` 3f021d0); it must stay green, unedited, after
it: a project that never takes a variation order bills, holds retention, recovers its advance and books to the ledger
exactly as before, and a posted certificate is never rewritten.

The reference contract (1,000,000.00):

* item A, reinforced concrete: 400 m³ × 1,500.00 = 600,000.00;
* item B, masonry: 2,000 m² × 200.00 = 400,000.00;
* retention 5%, advance recovery 10%, advance received 100,000.00.

The clock is fixed on a working Wednesday at 10:00, so no figure depends on the day the suite runs.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock

from django.test import TestCase

from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_cashbox, make_customer, make_location
from inventory.models import StockMovement
from ledger.models import JournalEntry, JournalLine
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, CustomerPayment, SalesInvoice
from sales.services import post_sales_invoice

from . import contract, insights, services
from .models import BoqLine, Certificate, CertificateLine, Project, ProjectPaymentKind
from .tests_contract import MODULES, customer_balance, gl, ledger_ok, person

#: Wednesday 11 November 2026, 10:00 in Cairo (08:00 UTC): a working day, away from midnight.
NOW = datetime(2026, 11, 11, 8, 0, tzinfo=dt_timezone.utc)
DAY = date(2026, 11, 11)


def money_footprint():
    """Every table money, stock or a certificate is written to. Drafting, approving or refusing a variation order must leave it as it was."""

    return {"sales_invoices": SalesInvoice.objects.count(), "payments": CustomerPayment.objects.count(), "customer_ledger": CustomerLedgerEntry.objects.count(),
            "stock_movements": StockMovement.objects.count(), "cashbox_movements": CashboxMovement.objects.count(), "journal_entries": JournalEntry.objects.count(),
            "journal_lines": JournalLine.objects.count(), "certificates": Certificate.objects.count(), "certificate_lines": CertificateLine.objects.count()}


def certificate_figures(certificate):
    """A certificate as it is stored: its money, and each line's item, previous and this quantity, rate and amount."""

    certificate = Certificate.objects.select_related("invoice").get(pk=certificate.pk)
    invoice = certificate.invoice
    return {"number": certificate.number, "status": invoice.status, "gross": certificate.gross, "retention_rate": certificate.retention_rate,
            "retention": certificate.retention_amount, "recovery": certificate.recovery_amount, "tax": invoice.tax_amount, "invoice": invoice.total_amount,
            "net": contract.net_payable(certificate),
            "lines": [(line.boq_line.code, line.previous_quantity, line.quantity, line.rate, line.amount) for line in certificate.lines.all()]}


class ReferenceContract(TestCase):
    """The reference contract with its advance, on a fixed clock. ``vat`` turns the 14% tax on before anything is billed."""

    vat = False

    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.profile = prepared_client(activity="contracting", sub_activity="general", modules=MODULES)
        if self.vat:
            from settings_core.capabilities import _write

            _write("vat", True)
        self.owner = person(RoleCode.OWNER, "c1_owner")
        self.store = make_location(location_code="STORE", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="MAIN", is_default=True)
        self.customer = make_customer(customer_code="C-OWNER", name="شركة النور للتطوير العقاري")
        self.project = services.save_project({"name": "برج النور الإداري", "customer": self.customer, "start_date": DAY - timedelta(days=100),
                                              "end_date": DAY + timedelta(days=100)}, self.owner)
        self.a = contract.save_boq_line(self.project, self.owner, {"code": "A", "description": "خرسانة مسلحة", "unit": "م³", "quantity": "400", "rate": "1500"})
        self.b = contract.save_boq_line(self.project, self.owner, {"code": "B", "description": "مباني طوب", "unit": "م²", "quantity": "2000", "rate": "200"})
        contract.save_terms(self.project, self.owner, retention_rate="5", advance_recovery_rate="10")
        self.project.refresh_from_db()
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="100000")

    def certify(self, post=True, **quantities):
        """A certificate by cumulative quantities (``a=``, ``b=``, or any BOQ line id), posted unless told otherwise."""

        lines = {"a": self.a.pk, "b": self.b.pk}
        certificate = contract.create_certificate(self.project, self.owner, quantities={lines.get(key, key): value for key, value in quantities.items()})
        if post:
            post_sales_invoice(certificate.invoice_id, self.owner)
        return certificate

    def two_certificates(self):
        return self.certify(a="100", b="250"), self.certify(a="200", b="750")


class BaselineWithoutVatTests(ReferenceContract):
    def test_the_contract_value_is_the_bill_of_quantities_total(self):
        self.assertEqual(contract.boq_total(self.project), D("1000000.00"))
        self.assertEqual(contract.contract_value(self.project), D("1000000.00"))
        self.assertEqual(services.summary(self.project)["contract"], D("1000000.00"))
        self.assertEqual([(line.code, line.quantity, line.rate, contract.line_amount(line)) for line in self.project.boq.all()],
                         [("A", D("400.000"), D("1500.00"), D("600000.00")), ("B", D("2000.000"), D("200.00"), D("400000.00"))])

    def test_certificate_one_and_two_to_the_piastre(self):
        first, second = self.two_certificates()
        self.assertEqual(certificate_figures(first), {
            "number": 1, "status": "posted", "gross": D("200000.00"), "retention_rate": D("5.00"), "retention": D("10000.00"), "recovery": D("20000.00"),
            "tax": D("0.00"), "invoice": D("200000.00"), "net": D("170000.00"),
            "lines": [("A", D("0.000"), D("100.000"), D("1500.00"), D("150000.00")), ("B", D("0.000"), D("250.000"), D("200.00"), D("50000.00"))]})
        self.assertEqual(certificate_figures(second), {
            "number": 2, "status": "posted", "gross": D("250000.00"), "retention_rate": D("5.00"), "retention": D("12500.00"), "recovery": D("25000.00"),
            "tax": D("0.00"), "invoice": D("250000.00"), "net": D("212500.00"),
            "lines": [("A", D("100.000"), D("100.000"), D("1500.00"), D("150000.00")), ("B", D("250.000"), D("500.000"), D("200.00"), D("100000.00"))]})

    def test_the_project_the_owner_account_and_the_ledger_after_both(self):
        self.two_certificates()
        figures = contract.position(self.project)
        self.assertEqual((figures["contract"], figures["certified"], figures["certified_progress"]), (D("1000000.00"), D("450000.00"), 45))
        self.assertEqual((figures["retention"], figures["released"], figures["retention_held"]), (D("22500.00"), D("0.00"), D("22500.00")))
        self.assertEqual((figures["advance"], figures["recovered"], figures["advance_left"]), (D("100000.00"), D("45000.00"), D("55000.00")))
        self.assertEqual((figures["collections"], figures["due_now"]), (D("0.00"), D("382500.00")))  # 450,000 − 22,500 − 45,000
        # The owner's account: 450,000 billed − 100,000 advance = what is due now + retention held − the advance left.
        self.assertEqual(customer_balance(self.customer), D("350000.00"))
        self.assertEqual(customer_balance(self.customer), figures["due_now"] + figures["retention_held"] - figures["advance_left"])
        self.assertEqual(gl("receivable"), D("382500.00"))
        self.assertEqual(gl("retention_receivable"), D("22500.00"))
        self.assertEqual(gl("customer_advances"), D("-55000.00"))
        self.assertEqual(gl("sales"), D("-450000.00"))
        self.assertEqual(gl("vat_out"), D("0.00"))
        self.assertEqual(gl("cash"), D("100000.00"))
        ledger_ok(self)

    def test_revenue_and_what_is_left_of_the_contract(self):
        self.two_certificates()
        summary = services.summary(self.project)
        self.assertEqual((summary["contract"], summary["billed"], summary["invoiced"], summary["due"]), (D("1000000.00"), D("450000.00"), D("450000.00"), D("450000.00")))
        self.assertEqual((summary["remaining"], summary["over"], summary["progress"], summary["drafts"]), (D("550000.00"), D("0.00"), 45, D("0.00")))
        self.assertEqual((summary["cost"], summary["profit"], summary["margin"]), (D("0.00"), D("450000.00"), 100))
        self.assertEqual(contract.due_now_many([self.project]), {self.project.pk: D("382500.00")})

    def test_the_calendar_share_and_the_behind_flag(self):
        self.two_certificates()
        self.assertEqual(insights.time_share(self.project), 50)  # 100 of 200 days
        self.assertEqual(insights.time_share(self.project, DAY + timedelta(days=60)), 80)
        self.assertEqual(insights.time_share(self.project, DAY + timedelta(days=300)), 100)
        rows, totals = insights.portfolio(today=DAY + timedelta(days=60))
        row = {row["project"].pk: row for row in rows}[self.project.pk]
        self.assertEqual((row["time_pct"], row["figures"]["progress"], row["behind"]), (80, 45, True))  # 45 + 15 < 80
        self.assertEqual((totals["contract"], totals["billed"], totals["retention"], totals["behind"]), (D("1000000.00"), D("450000.00"), D("22500.00"), 1))

    def test_a_draft_holds_no_retention_and_claims_its_recovery(self):
        self.certify(a="100", b="250")
        draft = self.certify(post=False, a="200", b="750")
        self.assertEqual((draft.gross, draft.retention_amount, draft.recovery_amount, contract.net_payable(draft)),
                         (D("250000.00"), D("12500.00"), D("25000.00"), D("212500.00")))
        self.assertEqual((contract.retention_held(self.project), contract.advance_left(self.project)), (D("10000.00"), D("55000.00")))
        figures = contract.position(self.project)  # posted documents only
        self.assertEqual((figures["certified"], figures["retention_held"], figures["recovered"], figures["due_now"]),
                         (D("200000.00"), D("10000.00"), D("20000.00"), D("170000.00")))

    def test_the_recovery_stops_at_the_advance_received(self):
        """Billed to the full 1,000,000.00: the 100,000.00 advance is recovered exactly, never more."""

        self.two_certificates()
        third = self.certify(a="400", b="2000")  # the remaining 550,000.00
        self.assertEqual((third.gross, third.retention_amount, third.recovery_amount, contract.net_payable(third)),
                         (D("550000.00"), D("27500.00"), D("55000.00"), D("467500.00")))
        figures = contract.position(self.project)
        self.assertEqual((figures["certified"], figures["certified_progress"]), (D("1000000.00"), 100))
        self.assertEqual((figures["retention_held"], figures["recovered"], figures["advance_left"], figures["due_now"]),
                         (D("50000.00"), D("100000.00"), D("0.00"), D("850000.00")))
        self.assertEqual(customer_balance(self.customer), D("900000.00"))
        self.assertEqual((gl("receivable"), gl("retention_receivable"), gl("customer_advances"), gl("sales")),
                         (D("850000.00"), D("50000.00"), D("0.00"), D("-1000000.00")))
        ledger_ok(self)

    def test_what_is_written_by_two_certificates(self):
        before = money_footprint()
        self.two_certificates()
        after = money_footprint()
        self.assertEqual({key: after[key] - before[key] for key in before},
                         {"sales_invoices": 2, "payments": 0, "customer_ledger": 2, "stock_movements": 0, "cashbox_movements": 0,
                          "journal_entries": after["journal_entries"] - before["journal_entries"], "journal_lines": after["journal_lines"] - before["journal_lines"],
                          "certificates": 2, "certificate_lines": 4})
        self.assertEqual((BoqLine.objects.count(), Project.objects.count()), (2, 1))


class BaselineWithVatTests(ReferenceContract):
    vat = True

    def test_certificate_one_and_two_to_the_piastre(self):
        first, second = self.two_certificates()
        one, two = certificate_figures(first), certificate_figures(second)
        self.assertEqual((one["gross"], one["retention"], one["recovery"], one["tax"], one["invoice"], one["net"]),
                         (D("200000.00"), D("10000.00"), D("20000.00"), D("28000.00"), D("228000.00"), D("198000.00")))
        self.assertEqual((two["gross"], two["retention"], two["recovery"], two["tax"], two["invoice"], two["net"]),
                         (D("250000.00"), D("12500.00"), D("25000.00"), D("35000.00"), D("285000.00"), D("247500.00")))

    def test_the_project_the_owner_account_and_the_ledger_after_both(self):
        self.two_certificates()
        figures = contract.position(self.project)
        self.assertEqual((figures["certified"], figures["certified_progress"]), (D("450000.00"), 45))
        self.assertEqual((figures["retention_held"], figures["recovered"], figures["advance_left"], figures["due_now"]),
                         (D("22500.00"), D("45000.00"), D("55000.00"), D("445500.00")))  # 513,000 − 22,500 − 45,000
        self.assertEqual(customer_balance(self.customer), D("413000.00"))  # 513,000 − 100,000
        self.assertEqual(customer_balance(self.customer), figures["due_now"] + figures["retention_held"] - figures["advance_left"])
        self.assertEqual(gl("receivable"), D("445500.00"))
        self.assertEqual(gl("retention_receivable"), D("22500.00"))
        self.assertEqual(gl("customer_advances"), D("-55000.00"))
        self.assertEqual(gl("sales"), D("-450000.00"))
        self.assertEqual(gl("vat_out"), D("-63000.00"))
        self.assertEqual(gl("cash"), D("100000.00"))
        ledger_ok(self)
        summary = services.summary(self.project)
        self.assertEqual((summary["billed"], summary["invoiced"], summary["progress"]), (D("450000.00"), D("513000.00"), 45))
