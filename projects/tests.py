"""CONTRACT-001: projects link progress bills, materials and expenses; the engines keep the money and stock."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxDirection
from expenses.models import ExpenseCategory
from expenses.services import cancel_expense, record_expense
from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from inventory.services import get_item_location_stock_quantity
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import create_sales_return, post_sales_invoice

from . import services
from .models import Project, ProjectStatus

TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class ProjectSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="contracting", sub_activity="finishing",
                                       modules="customers,items_services,sales_operations,cashboxes,reports,inventory,expenses,projects")
        self.owner = person(RoleCode.OWNER, "prj_owner")
        self.store = make_location(location_code="STORE", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="MAIN", is_default=True)
        make_cashbox_movement(self.cashbox, CashboxDirection.IN, "5000.00", movement_date=TODAY)
        self.customer = make_customer(customer_code="C-VILLA", name="م. حسام")
        self.cement = make_item(item_code="CEM", item_name="أسمنت (شكارة)", is_stock_tracked=True)
        stock_in(self.cement, self.store, 100, "80.00", movement_date=TODAY)
        self.project = services.save_project({"name": "تشطيب فيلا التجمع", "customer": self.customer, "contract_value": "100000", "site": "التجمع الخامس"}, self.owner)


class ProjectTests(ProjectSetup):
    def test_create_numbers_and_refusals(self):
        self.assertEqual((self.project.code, self.project.status, self.project.contract_value), ("P-0001", ProjectStatus.ACTIVE, D("100000.00")))
        for data, text in (({"name": "", "customer": self.customer}, "اسم المشروع"), ({"name": "x"}, "اختار العميل"),
                           ({"name": "x", "customer": self.customer, "contract_value": "-5"}, "قيمة العقد"),
                           ({"name": "x", "customer": self.customer, "start_date": TODAY, "end_date": TODAY.replace(year=TODAY.year - 1)}, "قبل البداية")):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, text):
                services.save_project(data, self.owner)

    def test_progress_bill_is_a_draft_until_posted_and_figures_follow_posted_documents(self):
        first = services.bill_progress(self.project, self.owner, amount="30000", description="مستخلص 1 - محارة")
        self.assertEqual((first.invoice_number, first.status, first.total_amount, first.customer), ("PB-P-0001-01", "draft", D("30000.00"), self.customer))
        figures = services.summary(self.project)
        self.assertEqual((figures["billed"], figures["drafts"]), (D("0.00"), D("30000.00")))  # a draft earns nothing yet
        post_sales_invoice(first.pk, self.owner)
        second = services.bill_progress(self.project, self.owner, amount="20000", description="مستخلص 2 - دهانات")
        post_sales_invoice(second.pk, self.owner)
        create_sales_return("SR-P1", TODAY, second.pk, [{"source_line": second.lines.get(), "quantity": D("0.25")}], "credit", self.owner)
        figures = services.summary(self.project)
        self.assertEqual((figures["billed"], figures["remaining"], figures["progress"]), (D("45000.00"), D("55000.00"), 45))
        self.assertEqual((figures["invoiced"], figures["collected"], figures["due"]), (D("50000.00"), D("0.00"), D("50000.00")))

    def test_materials_leave_stock_through_the_inventory_engine_at_average_cost(self):
        operation = services.issue_materials(self.project, self.owner, item=self.cement, location=self.store, quantity="30")
        self.assertEqual((operation.reference_number, operation.unit_cost), ("PI-P-0001-001", D("80.0000")))
        self.assertEqual(get_item_location_stock_quantity(self.cement, self.store), D("70.000"))
        self.assertTrue(StockMovement.objects.filter(item=self.cement, movement_type="adjustment_out", quantity=D("30")).exists())
        with self.assertRaises(ValidationError):  # not enough stock: the engine refuses
            services.issue_materials(self.project, self.owner, item=self.cement, location=self.store, quantity="71")
        service = make_item(item_code="SRV", item_name="خدمة", is_stock_tracked=False)
        with self.assertRaisesMessage(ValidationError, "مش متتبع"):
            services.issue_materials(self.project, self.owner, item=service, location=self.store, quantity="1")
        self.assertEqual(services.summary(self.project)["materials"], D("2400.00"))

    def test_expenses_link_once_and_cancelled_ones_stop_counting(self):
        category = ExpenseCategory.objects.get(code="rent")
        expense = record_expense(category=category, cashbox=self.cashbox, amount=D("1500"), expense_date=TODAY, description="يومية نقاشين", user=self.owner)
        services.link_expense(self.project, expense, self.owner)
        other = services.save_project({"name": "شقة المعادي", "customer": self.customer}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مربوط بمشروع"):
            services.link_expense(other, expense, self.owner)
        self.assertEqual(services.summary(self.project)["expenses"], D("1500.00"))
        cancel_expense(expense.pk, reversal_date=TODAY, reason="اتسجل غلط", user=self.owner)
        self.assertEqual(services.summary(self.project)["expenses"], D("0.00"))

    def test_profit_is_billed_minus_materials_and_expenses(self):
        invoice = services.bill_progress(self.project, self.owner, amount="10000", description="مستخلص 1")
        post_sales_invoice(invoice.pk, self.owner)
        services.issue_materials(self.project, self.owner, item=self.cement, location=self.store, quantity="25")  # 2000
        expense = record_expense(category=ExpenseCategory.objects.get(code="rent"), cashbox=self.cashbox, amount=D("3000"), expense_date=TODAY,
                                 description="مقاول باطن", user=self.owner)
        services.link_expense(self.project, expense, self.owner)
        figures = services.summary(self.project)
        self.assertEqual((figures["cost"], figures["profit"], figures["margin"]), (D("5000.00"), D("5000.00"), 50))

    def test_linking_invoices_checks_the_customer(self):
        stranger = make_customer(customer_code="C-2", name="عميل تاني")
        other = services.save_project({"name": "مخزن", "customer": stranger}, self.owner)
        invoice = services.bill_progress(other, self.owner, amount="500", description="مستخلص")
        with self.assertRaisesMessage(ValidationError, "لعميل تاني"):
            services.link_invoice(self.project, invoice, self.owner)
        stranger_project = Project.objects.get(pk=other.pk)
        with self.assertRaisesMessage(ValidationError, "مربوط بمشروع"):
            services.link_invoice(stranger_project, invoice, self.owner)

    def test_a_closed_project_takes_no_new_bills_or_materials(self):
        services.save_project({"name": self.project.name, "customer": self.customer, "status": ProjectStatus.DONE}, self.owner, self.project)
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            services.bill_progress(self.project, self.owner, amount="10", description="x")
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            services.issue_materials(self.project, self.owner, item=self.cement, location=self.store, quantity="1")


class ScreenTests(ProjectSetup):
    def test_create_bill_issue_and_read_from_the_screens(self):
        self.client.force_login(self.owner)
        created = self.client.post(reverse("projects:list"), {"name": "عمارة فيصل", "customer": str(self.customer.pk), "contract_value": "250000", "status": "active"})
        project = Project.objects.get(name="عمارة فيصل")
        url = reverse("projects:detail", args=[project.pk])
        self.assertRedirects(created, f"{url}?lang=ar", fetch_redirect_response=False)
        self.client.post(url, {"action": "bill", "amount": "50000", "description": "مستخلص 1 - هيكل"})
        invoice = SalesInvoice.objects.get(invoice_number="PB-P-0002-01")
        post_sales_invoice(invoice.pk, self.owner)
        self.client.post(url, {"action": "issue", "item": str(self.cement.pk), "location": str(self.store.pk), "quantity": "10"})
        page = self.client.get(url)
        self.assertContains(page, 'data-project-invoice="PB-P-0002-01"')
        self.assertContains(page, 'data-project-issue="PI-P-0002-001"')
        self.assertContains(page, 'data-figure="profit"')
        self.assertEqual(page.context["figures"]["profit"], D("49200.00"))
        self.assertContains(self.client.get(reverse("projects:list")), 'data-project-row="P-0002"')

    def test_permissions_hide_profit_and_block_changes(self):
        cashier = person(RoleCode.CASHIER, "prj_cashier")
        self.client.force_login(cashier)
        url = reverse("projects:detail", args=[self.project.pk])
        page = self.client.get(url)
        self.assertNotContains(page, 'data-figure="profit"')  # no profit permission
        self.assertNotContains(page, "data-issue-form")  # no stock adjustment permission
        self.assertEqual(self.client.post(url, {"action": "issue", "item": str(self.cement.pk), "location": str(self.store.pk), "quantity": "1"}).status_code, 403)
        keeper = person(RoleCode.STOCK_KEEPER, "prj_keeper")
        self.client.force_login(keeper)
        self.assertIn(self.client.post(url, {"action": "bill", "amount": "1", "description": "x"}).status_code, (302, 403))
        self.assertFalse(SalesInvoice.objects.exists())

    def test_the_module_gate_and_navigation(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), reverse("projects:list"))
        from settings_core.setup_services import ModuleChangeRefused, set_module_enabled

        with self.assertRaises(ModuleChangeRefused):
            set_module_enabled(self.profile, "projects", False, self.owner)
