"""CONTRACT-003: the projects report, overdue certificates and the dashboard tile."""

from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from permissions.models import RoleCode
from sales.services import post_sales_invoice

from . import contract, insights, services
from .models import Certificate, ProjectPaymentKind
from .tests_contract import TODAY, ContractSetup, person


class ProjectInsightTests(ContractSetup):
    def certified(self, days_ago, **quantities):
        certificate = self.certify(**quantities)
        Certificate.objects.filter(pk=certificate.pk).update(certificate_date=TODAY - timedelta(days=days_ago))
        post_sales_invoice(certificate.invoice_id, user=self.owner)
        certificate.refresh_from_db()
        return certificate

    def test_an_unpaid_certificate_past_the_credit_days_is_overdue_but_retention_is_not(self):
        first = self.certified(50, concrete="20")  # 20,000 gross, 5% retention
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.COLLECTION, cashbox=self.cashbox,
                                 amount=contract.net_payable(first), payment_date=TODAY - timedelta(days=45))
        second = self.certified(40, concrete="40")  # another 20,000 gross, unpaid
        self.certified(5, concrete="50")  # recent: not overdue yet
        rows, total, terms = insights.overdue_certificates()
        self.assertEqual(terms, 30)
        self.assertEqual([(row["certificate"].pk, row["late"]) for row in rows], [(second.pk, 10)])
        self.assertEqual(total, contract.net_payable(second))  # its net payable; the first's retention is not "owed now"

    def test_a_partly_paid_project_owes_on_its_newest_certificates_first(self):
        first = self.certified(70, concrete="20")
        second = self.certified(60, concrete="40")
        paid = contract.net_payable(first) + D("1000")
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.COLLECTION, cashbox=self.cashbox, amount=paid)
        rows, total, _ = insights.overdue_certificates()
        self.assertEqual([row["certificate"].pk for row in rows], [second.pk])
        self.assertEqual(total, contract.net_payable(second) - D("1000"))

    def test_another_unpaid_invoice_is_never_put_on_a_paid_certificate(self):
        from sales.services import post_sales_invoice

        first = self.certified(50, concrete="20")
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.COLLECTION, cashbox=self.cashbox, amount=contract.net_payable(first))
        from hesba_testing.factories import add_sales_line, make_draft_sales_invoice, make_item

        extra = make_draft_sales_invoice(customer=self.customer, location=self.store, invoice_number="SI-EXTRA", invoice_date=TODAY)
        add_sales_line(extra, make_item(item_code="SRV-X", item_name="أعمال إضافية", is_stock_tracked=False), 1, "10000")
        post_sales_invoice(extra.pk, user=self.owner)
        services.link_invoice(self.project, extra, self.owner)
        self.assertEqual(insights.certificate_owed(self.project)[first.pk], D("0.00"))
        rows, total, _ = insights.overdue_certificates()
        self.assertEqual((rows, total), ([], D("0.00")))  # the 10,000 is owed on the other invoice, not the paid certificate

    def test_the_portfolio_reads_each_project_and_flags_one_behind_its_calendar(self):
        self.project.start_date, self.project.end_date = TODAY - timedelta(days=90), TODAY + timedelta(days=10)
        self.project.save()
        self.certified(10, concrete="10")  # 10,000 of 200,000 = 5% billed with 90% of the time gone
        other = services.save_project({"name": "فيلا", "customer": self.customer, "contract_value": "100000"}, self.owner)
        rows, totals = insights.portfolio()
        by_code = {row["project"].code: row for row in rows}
        self.assertEqual(by_code[self.project.code]["time_pct"], 90)
        self.assertTrue(by_code[self.project.code]["behind"])
        self.assertIsNone(by_code[other.code]["time_pct"])
        self.assertEqual(totals["contract"], D("300000.00"))
        self.assertEqual(totals["behind"], 1)
        self.assertEqual(insights.time_share(self.project, TODAY + timedelta(days=50)), 100)

    def test_the_report_page_the_tile_and_who_sees_them(self):
        from permissions.services import user_has_permission
        from reports.activity_panel import build_activity_panel

        self.certified(40, concrete="20")
        self.client.force_login(self.owner)
        page = self.client.get(reverse("projects:report"))
        self.assertContains(page, f'data-portfolio-row="{self.project.code}"')
        self.assertContains(page, "data-overdue=")
        self.assertEqual(self.client.get(reverse("projects:report") + "?lang=en&status=all").status_code, 200)
        self.assertContains(self.client.get(reverse("projects:list")), "data-projects-report")
        panel = build_activity_panel("contracting", {"projects"}, "ar", TODAY)
        tile = next(tile for tile in panel["tiles"] if tile["key"] == "overdue_certs")
        self.assertEqual((tile["value"], tile["tone"]), (1, "bad"))
        cashier = person(RoleCode.CASHIER, "c3_cashier")
        if user_has_permission(cashier, "reports.view_profit_report"):
            self.skipTest("the seeded cashier's rights changed")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("projects:report")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("projects:list")), "data-projects-report")
