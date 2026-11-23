"""HG-048: the site engineer reads the project, records the quantities done on a draft certificate and issues materials to
the site; posting, collecting, profit, the cashbox and the subcontractors stay with the office."""

from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from hesba_testing.factories import make_item, stock_in
from inventory.models import StockAdjustmentDirection
from inventory.services import adjust_stock, get_item_location_stock_quantity
from permissions.models import RoleCode
from permissions.services import user_has_permission
from sales.services import post_sales_invoice

from . import contract, services
from .models import ProjectPaymentKind
from .tests_contract import ContractSetup, person


class SiteEngineerTests(ContractSetup):
    def setUp(self):
        super().setUp()
        self.engineer = person(RoleCode.SITE_ENGINEER, "site_eng")
        self.client.force_login(self.engineer)
        self.cement = make_item(item_code="CEM", item_name="أسمنت (شكارة)", is_stock_tracked=True)
        stock_in(self.cement, self.store, 100, "80.00")

    def url(self, name, *args):
        return reverse(f"projects:{name}", args=[self.project.pk, *args])

    def test_the_role_holds_the_site_and_nothing_on_the_money_side(self):
        for code in ("projects.view_projects", "projects.record_progress", "projects.issue_to_site", "inventory.view_stock"):
            self.assertTrue(user_has_permission(self.engineer, code), code)
        for code in ("sales.view_sales_invoices", "sales.create_sales_invoice", "sales.post_sales_invoice", "sales.receive_customer_payment",
                     "cashboxes.view_cashboxes", "reports.view_profit_report", "purchases.view_purchase_invoices", "inventory.adjust_stock",
                     "inventory.view_cost"):
            self.assertFalse(user_has_permission(self.engineer, code), code)

    def test_an_engineer_reads_the_project_and_its_bill_of_quantities(self):
        self.assertContains(self.client.get(reverse("projects:list")), 'data-project-row="')
        detail = self.client.get(self.url("detail"))
        self.assertContains(detail, "data-project-figures")
        self.assertContains(detail, "data-issue-form")
        for hidden in ('data-figure="collected"', 'data-figure="profit"', "data-contract-position", "data-project-invoice", "data-bill-form"):
            self.assertNotContains(detail, hidden)
        self.assertContains(self.client.get(self.url("boq")), 'data-boq-line="2"')
        for screen in ("payments", "subcontracts", "budget"):
            self.assertEqual(self.client.get(self.url(screen)).status_code, 403, screen)
            self.assertNotContains(detail, self.url(screen))
        self.assertEqual(self.client.get(reverse("sales:list")).status_code, 403)

    def test_an_engineer_records_quantities_on_a_draft_and_can_never_post_it(self):
        made = self.client.post(self.url("certificates"), {f"q_{self.concrete.pk}": "30", "description": "أعمال الأسبوع"})
        certificate = self.project.certificates.get()
        self.assertRedirects(made, self.url("certificate", certificate.number) + "?lang=ar", fetch_redirect_response=False)
        self.assertEqual((certificate.invoice.status, certificate.gross, certificate.created_by),
                         ("draft", D("30000.00"), self.engineer))
        page = self.client.get(self.url("certificate", certificate.number))
        self.assertContains(page, 'data-certificate-status="draft"')
        self.assertNotContains(page, "data-open-invoice")  # the sales invoice behind it is the office's
        self.assertNotContains(page, "data-withdraw-form")
        # Posting is a sales screen the engineer cannot open, and withdrawing is the office's too.
        self.assertEqual(self.client.post(reverse("sales:post", args=[certificate.invoice_id])).status_code, 403)
        self.assertEqual(self.client.post(self.url("certificate", certificate.number), {"action": "withdraw"}).status_code, 403)
        certificate.invoice.refresh_from_db()
        self.assertEqual(certificate.invoice.status, "draft")
        post_sales_invoice(certificate.invoice_id, self.owner)  # the office posts it

    def test_an_engineer_cannot_change_the_contract_or_take_money(self):
        for action, data in (("boq", {"action": "terms", "retention_rate": "0", "advance_recovery_rate": "0"}),
                             ("detail", {"action": "edit", "name": "x"}),
                             ("detail", {"action": "bill", "amount": "1000", "description": "x"})):
            self.assertEqual(self.client.post(self.url(action), data).status_code, 403, data["action"])
        self.assertEqual(self.client.post(self.url("payments"), {"action": "receive", "kind": ProjectPaymentKind.ADVANCE,
                                                                 "cashbox": str(self.cashbox.pk), "amount": "1000"}).status_code, 403)
        self.assertEqual(contract.advance_received(self.project), D("0"))

    def test_an_engineer_issues_materials_to_the_site_and_nothing_else_from_stock(self):
        response = self.client.post(self.url("detail"), {"action": "issue", "item": self.cement.pk, "location": self.store.pk, "quantity": "12"})
        self.assertEqual(response.status_code, 302)
        issue = self.project.issues.select_related("operation").get()
        self.assertEqual((issue.operation.quantity, issue.operation.created_by, issue.operation.unit_cost), (D("12.000"), self.engineer, D("80.0000")))
        self.assertEqual(get_item_location_stock_quantity(self.cement, self.store), D("88"))
        self.assertEqual(services.summary(self.project)["materials"], D("960.00"))  # costed at the stock's real cost
        with self.assertRaises(PermissionDenied):  # a general stock adjustment still needs inventory.adjust_stock
            adjust_stock("ADJ-E", issue.operation.operation_date, self.cement, self.store, StockAdjustmentDirection.OUT, D("1"), "x", self.engineer)
        with self.assertRaises(PermissionDenied):  # and no flow may name a stand-in of its own
            adjust_stock("ADJ-F", issue.operation.operation_date, self.cement, self.store, StockAdjustmentDirection.OUT, D("1"), "x", self.engineer,
                         stand_in="projects.view_projects")

    def test_the_engineer_lands_on_the_projects(self):
        self.assertRedirects(self.client.get(reverse("after_login")), reverse("projects:list"), fetch_redirect_response=False)

    def test_a_manager_still_does_everything_and_lands_on_the_dashboard(self):
        manager = person(RoleCode.MANAGER, "site_mgr")
        self.client.force_login(manager)
        self.assertRedirects(self.client.get(reverse("after_login")), reverse("dashboard_snapshot"), fetch_redirect_response=False)
        detail = self.client.get(self.url("detail"))
        self.assertContains(detail, 'data-figure="collected"')
        self.assertContains(detail, "data-issue-form")
