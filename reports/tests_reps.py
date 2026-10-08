"""PERF-001: salesperson on the invoice, the customer's rep, and the rep report."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from sales.forms import SalesDraftForm
from sales.models import SalesInvoice
from sales.services import create_sales_draft, create_sales_return, post_sales_invoice, record_customer_payment
from staff.services import save_employee

from .reps import rep_performance
from .selectors import profit_totals
from .tests_dashboard import prepared_client


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class RepsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,pdf_printing,employees_technicians")
        cls.today = timezone.localdate()
        cls.owner = person(RoleCode.OWNER, "rep_owner")
        cls.cashier = person(RoleCode.CASHIER, "rep_cashier")
        cls.samir = save_employee({"name": "سمير المندوب", "commission_percent": "10"}, cls.owner)
        cls.mona = save_employee({"name": "منى البائعة", "commission_percent": "5", "user": str(cls.cashier.pk)}, cls.owner)
        cls.distributor = make_customer(customer_code="C-DIST", name="موزع الجيزة", sales_rep=cls.samir)
        cls.walk_in = make_customer(customer_code="C-WALK", name="عميل نقدي")
        cls.location = make_location(location_code="L-1", is_selling_location=True)
        cls.cashbox = make_cashbox(cashbox_code="C-1")
        cls.item = make_item(item_code="IT-R", item_name="صنف")
        stock_in(cls.item, cls.location, 50, "10.00")
        recalculate_item_average_cost(cls.item)

    def sell(self, customer, user, qty, price, paid, number, **header):
        invoice = create_sales_draft(
            {"invoice_number": number, "invoice_date": self.today, "customer": customer, "selling_location": self.location,
             "cashbox": self.cashbox, "paid_now": D(paid), **header},
            [{"item": self.item, "quantity": D(qty), "unit_sale_price": D(price)}],
            user,
        )
        post_sales_invoice(invoice.pk, user)
        invoice.refresh_from_db()
        return invoice

    def test_who_gets_the_sale(self):
        # The customer's rep, else the seller's own employee, else nobody; a choice made on the form wins.
        self.assertEqual(self.sell(self.distributor, self.owner, 1, "20", "0", "R-1").salesperson, self.samir)
        self.assertEqual(self.sell(self.walk_in, self.cashier, 1, "20", "20", "R-2").salesperson, self.mona)
        self.assertIsNone(self.sell(self.walk_in, self.owner, 1, "20", "20", "R-3").salesperson)
        self.assertEqual(self.sell(self.walk_in, self.owner, 1, "20", "20", "R-4", salesperson=self.samir).salesperson, self.samir)
        self.samir.active = False
        self.samir.save()
        self.assertIsNone(self.sell(self.distributor, self.owner, 1, "20", "0", "R-5").salesperson)

    def test_figures_commission_alerts_and_they_add_up_to_the_profit_report(self):
        big = self.sell(self.distributor, self.owner, 10, "30", "100", "R-10")       # 300, 100 paid
        self.sell(self.walk_in, self.cashier, 2, "25", "50", "R-11")                 # 50, paid
        self.sell(self.walk_in, self.owner, 1, "40", "40", "R-12")                   # nobody's
        create_sales_return("RR-1", self.today, big.pk, [{"source_line": big.lines.get(), "quantity": D("2")}], "تالف", self.owner)
        record_customer_payment("CP-R1", self.today, self.distributor, self.cashbox, D("20.00"), user=self.owner)

        rows, totals = rep_performance(self.today, self.today)
        by = {row["rep"].code if row["rep"] else None: row for row in rows}
        samir, mona, nobody = by[self.samir.code], by[self.mona.code], by[None]
        self.assertEqual((samir["invoices"], samir["gross_sales"], samir["returns"], samir["net_sales"]), (1, D("300.00"), D("60.00"), D("240.00")))
        self.assertEqual(samir["gross_profit"], D("240.00") - D("80.00"))             # 8 units kept at cost 10
        self.assertEqual(samir["commission"], D("24.00"))
        self.assertEqual(samir["collected"], D("120.00"))                             # 100 at the till + 20 later
        self.assertEqual(samir["average_invoice"], D("240.00"))
        self.assertEqual([code for code, *_ in samir["alerts"]], ["high_returns", "low_collection"])
        self.assertEqual((mona["net_sales"], mona["commission"], mona["alerts"]), (D("50.00"), D("2.50"), []))
        self.assertEqual((nobody["net_sales"], nobody["commission"]), (D("40.00"), D("0.00")))

        profit = profit_totals(self.today, self.today)
        self.assertEqual(totals["net_sales"], profit["sales"])
        self.assertEqual(totals["gross_profit"], profit["profit"])
        self.assertEqual(totals["commission"], D("26.50"))

    def test_the_screen_permissions_and_profit_column(self):
        self.sell(self.distributor, self.owner, 1, "20", "0", "R-20")
        self.client.force_login(self.owner)
        page = self.client.get(reverse("reports:reps"))
        self.assertContains(page, "سمير المندوب")
        self.assertContains(page, "data-rep-profit")
        self.assertContains(self.client.get(reverse("report_hub")), reverse("reports:reps"))
        self.client.force_login(person(RoleCode.MANAGER, "rep_manager"))
        page = self.client.get(reverse("reports:reps") + "?lang=en")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Sales rep performance")
        self.assertNotContains(page, "data-rep-profit")
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("reports:reps")).status_code, 403)

    def test_the_form_offers_reps_and_the_invoice_and_print_show_them(self):
        form = SalesDraftForm(lang="ar")
        self.assertIn(self.samir, form.fields["salesperson"].queryset)
        invoice = self.sell(self.distributor, self.owner, 1, "20", "0", "R-30")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("sales:detail", args=[invoice.pk])), "سمير المندوب")
        self.assertContains(self.client.get(reverse("printing:sales_invoice", args=[invoice.pk])), "سمير المندوب")

    def test_no_employees_means_no_rep_field(self):
        from staff.models import Employee

        SalesInvoice.objects.all().delete()
        Employee.objects.update(active=False)
        self.assertNotIn("salesperson", SalesDraftForm(lang="ar").fields)


class RepsNeedTheEmployeesModuleTests(TestCase):
    """FEEDBACK-R1: a pharmacy that never switched employees on is never asked for a rep."""

    @classmethod
    def setUpTestData(cls):
        prepared_client("commercial", "pharmacy", modules="customers,items_services,sales_operations,inventory,cashboxes,reports")
        cls.owner = person(RoleCode.OWNER, "norep_owner")
        save_employee({"name": "موظف من البيانات التجريبية"}, cls.owner)

    def test_no_rep_fields_report_card_or_report(self):
        from master_data.forms import CustomerForm

        self.assertNotIn("sales_rep", CustomerForm().fields)
        self.assertNotIn("salesperson", SalesDraftForm(lang="ar").fields)
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("master_data:customer_create")), 'name="sales_rep"')
        self.assertNotContains(self.client.get(reverse("report_hub")), reverse("reports:reps"))
        self.assertEqual(self.client.get(reverse("reports:reps")).status_code, 403)

    def test_invoices_name_no_rep(self):
        from sales.services import default_salesperson
        from staff.models import Employee

        customer = make_customer(customer_code="C-NOREP", sales_rep=Employee.objects.get())
        self.assertIsNone(default_salesperson(customer, self.owner))

    def test_switching_employees_on_brings_them_back(self):
        from master_data.forms import CustomerForm
        from settings_core.models import FeatureFlag
        from settings_core.setup_services import module_flag_code

        FeatureFlag.objects.update_or_create(code=module_flag_code("employees_technicians"), defaults={"enabled": True})
        self.assertIn("sales_rep", CustomerForm().fields)
        self.assertIn("salesperson", SalesDraftForm(lang="ar").fields)
