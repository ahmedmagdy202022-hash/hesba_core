"""CONTRACT-002 (HG-038): bill of quantities, progress certificates, retention,
advances, subcontractors, budget, and how the general ledger shows them."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_supplier, make_user, make_user_profile
from ledger.projector import ensure_fresh
from ledger.reports import control_balance, reconciliation
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice
from purchases.services import create_purchase_draft, post_purchase_invoice
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice

from . import contract, costs, services
from .models import CostHeading, ProjectPaymentKind

TODAY = timezone.localdate()
MODULES = "customers,suppliers,items_services,sales_operations,purchases,cashboxes,reports,inventory,expenses,projects"


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def customer_balance(customer):
    entries = CustomerLedgerEntry.objects.filter(customer=customer)
    return sum((e.due_increase - e.due_decrease for e in entries), D("0")) + customer.opening_balance


def gl(control):
    ensure_fresh()
    return control_balance(control)


def ledger_ok(test):
    result = reconciliation()
    test.assertTrue(result["balanced"])
    test.assertTrue(result["ok"], [row for row in result["rows"] if row["difference"]])


class ContractSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="contracting", sub_activity="general", modules=MODULES)
        self.owner = person(RoleCode.OWNER, "c2_owner")
        self.store = make_location(location_code="STORE", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="MAIN", is_default=True)
        self.customer = make_customer(customer_code="C-OWNER", name="شركة النور للتطوير")
        self.project = services.save_project({"name": "عمارة النور", "customer": self.customer}, self.owner)
        self.concrete = contract.save_boq_line(self.project, self.owner, {"code": "1", "description": "خرسانة مسلحة", "unit": "م³", "quantity": "100", "rate": "1000"})
        self.masonry = contract.save_boq_line(self.project, self.owner, {"code": "2", "description": "مباني طوب", "unit": "م²", "quantity": "500", "rate": "200"})
        contract.save_terms(self.project, self.owner, retention_rate="5", advance_recovery_rate="10")
        self.project.refresh_from_db()

    def certify(self, **quantities):
        lines = {"concrete": self.concrete, "masonry": self.masonry}
        return contract.create_certificate(self.project, self.owner, quantities={lines[k].pk: v for k, v in quantities.items()})


class BillOfQuantitiesTests(ContractSetup):
    def test_the_bill_of_quantities_is_the_contract_value(self):
        self.assertEqual(contract.boq_total(self.project), D("200000.00"))
        self.assertEqual(services.summary(self.project)["contract"], D("200000.00"))
        self.assertEqual([line.line_number for line in self.project.boq.all()], [1, 2])

    def test_a_certified_item_keeps_its_rate_and_cannot_shrink_below_or_be_deleted(self):
        post_sales_invoice(self.certify(concrete="40").invoice_id, self.owner)
        with self.assertRaisesMessage(ValidationError, "الفئة بتاعته ثابتة"):
            contract.save_boq_line(self.project, self.owner, {"description": "خرسانة", "quantity": "100", "rate": "1100"}, self.concrete)
        with self.assertRaisesMessage(ValidationError, "تقل عن"):
            contract.save_boq_line(self.project, self.owner, {"description": "خرسانة", "quantity": "30", "rate": "1000"}, self.concrete)
        with self.assertRaisesMessage(ValidationError, "مينفعش يتمسح"):
            contract.delete_boq_line(self.project, self.concrete, self.owner)
        # More quantity (a variation) is fine; an uncertified item can go.
        contract.save_boq_line(self.project, self.owner, {"description": "خرسانة", "quantity": "120", "rate": "1000"}, self.concrete)
        contract.delete_boq_line(self.project, self.masonry, self.owner)
        self.assertEqual(contract.boq_total(self.project), D("120000.00"))


class CertificateTests(ContractSetup):
    def test_quantities_to_date_retention_and_advance_recovery(self):
        advance = contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="30000")
        self.assertEqual(advance.payment_number, "CP-00001")
        first = self.certify(concrete="40")
        self.assertEqual((first.number, first.invoice.invoice_number, first.invoice.status), (1, "PB-P-0001-01", "draft"))
        self.assertEqual((first.gross, first.retention_amount, first.recovery_amount), (D("40000.00"), D("2000.00"), D("4000.00")))
        self.assertEqual((first.invoice.total_amount, contract.net_payable(first)), (D("40000.00"), D("34000.00")))
        with self.assertRaisesMessage(ValidationError, "لسه مسودة (1)"):
            self.certify(concrete="50")
        post_sales_invoice(first.invoice_id, self.owner)

        with self.assertRaisesMessage(ValidationError, "أقل من اللي اتعمل"):
            self.certify(concrete="30")
        with self.assertRaisesMessage(ValidationError, "مفيش كميات جديدة"):
            self.certify(concrete="40", masonry="")
        second = self.certify(concrete="100", masonry="250")
        self.assertEqual([(line.boq_line.code, line.previous_quantity, line.quantity, line.amount) for line in second.lines.all()],
                         [("1", D("40.000"), D("60.000"), D("60000.00")), ("2", D("0.000"), D("250.000"), D("50000.00"))])
        self.assertEqual((second.gross, second.retention_amount, second.recovery_amount), (D("110000.00"), D("5500.00"), D("11000.00")))
        post_sales_invoice(second.invoice_id, self.owner)
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.COLLECTION, cashbox=self.cashbox, amount="20000")

        figures = contract.position(self.project)
        self.assertEqual((figures["certified"], figures["certified_progress"]), (D("150000.00"), 75))
        self.assertEqual((figures["retention_held"], figures["recovered"], figures["advance_left"]), (D("7500.00"), D("15000.00"), D("15000.00")))
        # 150000 billed − 7500 retention − 15000 recovered − 20000 collected
        self.assertEqual(figures["due_now"], D("107500.00"))
        # The owner's account is untouched: 150000 − 30000 advance − 20000 collected.
        self.assertEqual(customer_balance(self.customer), D("100000.00"))
        self.assertEqual(customer_balance(self.customer), figures["due_now"] + figures["retention_held"] - figures["advance_left"])

        self.assertEqual(gl("receivable"), D("107500.00"))
        self.assertEqual(gl("retention_receivable"), D("7500.00"))
        self.assertEqual(gl("customer_advances"), D("-15000.00"))
        self.assertEqual(gl("sales"), D("-150000.00"))
        ledger_ok(self)

    def test_the_recovery_never_exceeds_the_advance_left(self):
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="5000")
        first = self.certify(concrete="80")  # 10% of 80000 = 8000 > 5000
        self.assertEqual(first.recovery_amount, D("5000.00"))
        post_sales_invoice(first.invoice_id, self.owner)
        self.assertEqual(self.certify(concrete="90").recovery_amount, D("0.00"))
        with self.assertRaisesMessage(ValidationError, "خصمت من الدفعة المقدمة"):
            contract.unlink_payment(self.project, self.project.payments.get(), self.owner)
        self.assertEqual(gl("customer_advances"), D("0.00"))
        ledger_ok(self)

    def test_a_cancelled_certificate_frees_its_quantities_and_nets_out_of_the_ledger(self):
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="10000")
        first = self.certify(concrete="40")
        post_sales_invoice(first.invoice_id, self.owner)
        cancel_posted_sales_invoice(first.invoice_id, self.owner, reason="كميات غلط")
        self.assertEqual(contract.certified_quantity(self.concrete), D("0"))
        self.assertEqual(contract.advance_left(self.project), D("10000.00"))
        self.assertEqual(contract.position(self.project)["retention_held"], D("0.00"))
        for control in ("receivable", "retention_receivable", "sales"):
            self.assertEqual(control_balance(control), D("0.00"), control)
        self.assertEqual(gl("customer_advances"), D("-10000.00"))
        again = self.certify(concrete="35")
        self.assertEqual((again.number, again.lines.get().previous_quantity), (2, D("0.000")))
        ledger_ok(self)

    def test_a_draft_certificate_can_be_withdrawn_but_a_posted_one_cannot(self):
        draft = self.certify(concrete="10")
        number = draft.invoice.invoice_number
        contract.withdraw_certificate(self.project, draft, self.owner)
        self.assertFalse(SalesInvoice.objects.filter(invoice_number=number).exists())
        self.assertFalse(self.project.invoices.exists())
        posted = self.certify(concrete="10")
        self.assertEqual(posted.number, 1)
        post_sales_invoice(posted.invoice_id, self.owner)
        with self.assertRaisesMessage(ValidationError, "اترحّل"):
            contract.withdraw_certificate(self.project, posted, self.owner)

    def test_retention_is_released_at_handover(self):
        post_sales_invoice(self.certify(concrete="100", masonry="500").invoice_id, self.owner)  # 200000, retention 10000
        self.assertEqual(contract.retention_held(self.project), D("10000.00"))
        with self.assertRaisesMessage(ValidationError, "أكبر من ضمان الأعمال المحتجز"):
            contract.release_retention(self.project, self.owner, amount="10000.01")
        contract.release_retention(self.project, self.owner, amount="6000", notes="استلام ابتدائي")
        figures = contract.position(self.project)
        self.assertEqual((figures["retention_held"], figures["due_now"]), (D("4000.00"), D("196000.00")))
        self.assertEqual(gl("retention_receivable"), D("4000.00"))
        self.assertEqual(gl("receivable"), D("196000.00"))
        ledger_ok(self)

    def test_a_lump_sum_certificate_without_a_bill_of_quantities(self):
        other = services.save_project({"name": "صيانة فيلا", "customer": self.customer, "contract_value": "50000"}, self.owner)
        contract.save_terms(other, self.owner, retention_rate="10", advance_recovery_rate="0")
        invoice = services.bill_progress(other, self.owner, amount="20000", description="أعمال الشهر الأول")
        certificate = invoice.project_certificate
        self.assertEqual((certificate.gross, certificate.retention_amount, certificate.lines.count()), (D("20000.00"), D("2000.00"), 0))
        with self.assertRaisesMessage(ValidationError, "ليه مقايسة"):
            services.bill_progress(self.project, self.owner, amount="1000", description="x")

    def test_vat_is_on_the_gross_and_the_deductions_come_after_it(self):
        from settings_core.capabilities import _write

        _write("vat", True)
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="30000")
        first = self.certify(concrete="40")
        self.assertEqual((first.invoice.tax_amount, first.invoice.total_amount), (D("5600.00"), D("45600.00")))
        self.assertEqual(contract.net_payable(first), D("39600.00"))  # 45600 − 2000 − 4000
        post_sales_invoice(first.invoice_id, self.owner)
        self.assertEqual((gl("sales"), gl("vat_out")), (D("-40000.00"), D("-5600.00")))
        ledger_ok(self)

    def test_payments_link_once_and_only_for_this_owner(self):
        from sales.services import record_customer_payment

        payment = record_customer_payment("CP-X1", TODAY, self.customer, self.cashbox, D("1000"), self.owner)
        contract.link_payment(self.project, payment, self.owner, kind=ProjectPaymentKind.COLLECTION)
        with self.assertRaisesMessage(ValidationError, "مربوط بمشروع"):
            contract.link_payment(self.project, payment, self.owner, kind=ProjectPaymentKind.ADVANCE)
        stranger = record_customer_payment("CP-X2", TODAY, make_customer(customer_code="C-2"), self.cashbox, D("1000"), self.owner)
        with self.assertRaisesMessage(ValidationError, "لعميل تاني"):
            contract.link_payment(self.project, stranger, self.owner, kind=ProjectPaymentKind.COLLECTION)


class SubcontractTests(ContractSetup):
    def setUp(self):
        super().setUp()
        self.sub_supplier = make_supplier(supplier_code="S-SUB", name="مؤسسة الأمل للمقاولات")
        self.subcontract = costs.save_subcontract(self.project, self.owner, {"supplier": self.sub_supplier, "scope": "أعمال المباني", "value": "60000",
                                                                             "retention_rate": "10"})

    def test_a_subcontractor_bill_holds_retention_and_books_to_project_cost(self):
        bill = costs.bill_subcontract(self.subcontract, self.owner, amount="20000", description="مستخلص 1 مباني")
        self.assertEqual((bill.invoice.invoice_number, bill.invoice.status, bill.retention_amount), ("SC-P-0001-01", "draft", D("2000.00")))
        self.assertEqual(costs.bill_net_payable(bill), D("18000.00"))
        post_purchase_invoice(bill.invoice_id, self.owner)
        figures = costs.subcontract_figures(self.subcontract)
        self.assertEqual((figures["billed"], figures["remaining"], figures["retention_held"]), (D("20000.00"), D("40000.00"), D("2000.00")))
        self.assertEqual(services.summary(self.project)["purchases"], D("20000.00"))
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.SUBCONTRACT], D("20000.00"))
        self.assertEqual(gl("project_cost"), D("20000.00"))
        self.assertEqual(gl("payable"), D("-18000.00"))
        self.assertEqual(gl("retention_payable"), D("-2000.00"))
        ledger_ok(self)

        with self.assertRaisesMessage(ValidationError, "أكبر من ضمان الأعمال المحتجز"):
            costs.release_subcontract_retention(self.subcontract, self.owner, amount="2500")
        costs.release_subcontract_retention(self.subcontract, self.owner, amount="2000")
        self.assertEqual((gl("payable"), gl("retention_payable")), (D("-20000.00"), D("0.00")))
        ledger_ok(self)

    def test_a_draft_bill_can_be_withdrawn(self):
        bill = costs.bill_subcontract(self.subcontract, self.owner, amount="5000", description="x")
        costs.withdraw_bill(bill, self.owner)
        self.assertFalse(PurchaseInvoice.objects.exists())
        self.assertFalse(self.project.purchases.exists())

    def test_only_service_purchases_link_to_a_project(self):
        cement = make_item(item_code="CEM", item_name="أسمنت", is_stock_tracked=True)
        rental = make_item(item_code="RENT", item_name="إيجار ونش", is_stock_tracked=False)
        supplier = make_supplier(supplier_code="S-2", name="معدات الدلتا")

        def purchase(number, item):
            invoice = create_purchase_draft({"invoice_number": number, "invoice_date": TODAY, "supplier": supplier, "receiving_location": self.store},
                                            [{"item": item, "quantity": D("1"), "unit_purchase_price": D("3000")}], self.owner)
            post_purchase_invoice(invoice.pk, self.owner)
            return PurchaseInvoice.objects.get(pk=invoice.pk)

        with self.assertRaisesMessage(ValidationError, "أصناف مخزنية"):
            costs.link_purchase(self.project, purchase("PI-1", cement), self.owner)
        crane = purchase("PI-2", rental)
        costs.link_purchase(self.project, crane, self.owner, heading=CostHeading.EQUIPMENT)
        with self.assertRaisesMessage(ValidationError, "مربوطة بمشروع"):
            costs.link_purchase(self.project, crane, self.owner)
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.EQUIPMENT], D("3000.00"))
        self.assertEqual(gl("project_cost"), D("3000.00"))
        ledger_ok(self)

    def test_budget_against_actual(self):
        post_purchase_invoice(costs.bill_subcontract(self.subcontract, self.owner, amount="12000", description="x").invoice_id, self.owner)
        costs.save_budget(self.project, self.owner, {"subcontract": "15000", "materials": "8000"})
        table = costs.budget_rows(self.project)
        rows = {row["heading"]: row for row in table["rows"]}
        self.assertEqual((rows["subcontract"]["actual"], rows["subcontract"]["variance"], rows["subcontract"]["used"]), (D("12000.00"), D("3000.00"), 80))
        self.assertEqual((rows["materials"]["actual"], rows["materials"]["used"]), (D("0"), 0))
        self.assertEqual((table["budget"], table["actual"]), (D("23000.00"), D("12000.00")))


class NonContractingChartTests(TestCase):
    def test_another_activity_with_projects_on_books_retention_like_contracting(self):
        prepared_client(activity="services", sub_activity="maintenance", modules=MODULES)
        owner = person(RoleCode.OWNER, "c2_srv_owner")
        make_location(location_code="STORE", is_default=True)
        cashbox = make_cashbox(cashbox_code="MAIN", is_default=True)
        customer = make_customer(customer_code="C-S")
        project = services.save_project({"name": "عقد صيانة", "customer": customer, "contract_value": "10000"}, owner)
        contract.save_terms(project, owner, retention_rate="5", advance_recovery_rate="0")
        post_sales_invoice(services.bill_progress(project, owner, amount="10000", description="شهر 1").pk, owner)
        # The Projects module brings the retention account, so the ledger matches the project screen.
        self.assertEqual((gl("receivable"), gl("retention_receivable")), (D("9500.00"), D("500.00")))
        self.assertEqual(contract.position(project)["retention_held"], D("500.00"))
        ledger_ok(self)

    def test_without_the_projects_module_no_project_accounts_are_added(self):
        from ledger.models import Account
        from ledger.services import ensure_chart

        prepared_client(activity="services", sub_activity="maintenance", modules="customers,suppliers,items_services,sales_operations,cashboxes,reports")
        ensure_chart()
        self.assertFalse(Account.objects.filter(control__in=["retention_receivable", "retention_payable", "project_cost"]).exists())


class ContractScreenTests(ContractSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.supplier = make_supplier(supplier_code="S-SUB", name="مؤسسة الأمل")

    def url(self, name, *args):
        return reverse(f"projects:{name}", args=[self.project.pk, *args])

    def test_the_whole_contract_from_the_screens(self):
        boq = self.url("boq")
        self.assertContains(self.client.get(boq), 'data-boq-line="2"')
        self.client.post(boq, {"action": "add", "code": "3", "description": "لياسة", "unit": "م²", "quantity": "400", "rate": "50"})
        self.assertEqual(contract.boq_total(self.project), D("220000.00"))
        self.client.post(boq, {"action": "terms", "retention_rate": "10", "advance_recovery_rate": "20"})
        self.project.refresh_from_db()
        self.assertEqual((self.project.retention_rate, self.project.advance_recovery_rate), (D("10.00"), D("20.00")))

        payments = self.url("payments")
        self.client.post(payments, {"action": "receive", "kind": "advance", "cashbox": str(self.cashbox.pk), "amount": "20000"})
        self.assertEqual(contract.advance_received(self.project), D("20000.00"))

        certificates = self.url("certificates")
        made = self.client.post(certificates, {f"q_{self.concrete.pk}": "50", "description": "مستخلص الهيكل"})
        self.assertRedirects(made, self.url("certificate", 1) + "?lang=ar", fetch_redirect_response=False)
        page = self.client.get(self.url("certificate", 1))
        self.assertContains(page, 'data-total="net"')
        self.assertEqual(page.context["net"], D("35000.00"))  # 50000 − 5000 − 10000
        self.assertContains(self.client.get(certificates), "data-draft-open")
        printed = self.client.get(self.url("certificate_print", 1))
        self.assertContains(printed, "مستخلص أعمال")
        self.assertContains(printed, 'data-print-total')
        post_sales_invoice(page.context["certificate"].invoice_id, self.owner)

        self.client.post(payments, {"action": "release", "amount": "2000", "notes": "استلام جزئي"})
        self.assertEqual(contract.retention_held(self.project), D("3000.00"))

        subcontracts = self.url("subcontracts")
        self.client.post(subcontracts, {"action": "new", "supplier": str(self.supplier.pk), "scope": "مباني", "value": "40000", "retention_rate": "5"})
        subcontract = self.project.subcontracts.get()
        self.client.post(subcontracts, {"action": "bill", "subcontract": str(subcontract.pk), "amount": "10000", "description": "دفعة 1"})
        self.assertEqual(subcontract.bills.get().retention_amount, D("500.00"))
        self.assertContains(self.client.get(subcontracts), 'data-sub-bill="SC-P-0001-01"')

        budget = self.url("budget")
        self.client.post(budget, {"b_subcontract": "30000", "b_materials": "50000"})
        self.assertEqual(self.project.budget.count(), 5)
        self.assertContains(self.client.get(budget), 'data-budget-row="subcontract"')

        overview = self.client.get(self.url("detail"))
        self.assertContains(overview, "data-project-tabs")
        self.assertEqual(overview.context["position"]["due_now"], D("37000.00"))  # 50000 − 3000 held − 10000 recovered
        self.assertContains(self.client.get(reverse("projects:list")), "37,000.00")

    def test_errors_come_back_on_the_page_in_arabic(self):
        page = self.client.post(self.url("certificates"), {f"q_{self.concrete.pk}": "-1"})
        self.assertContains(page, "data-project-error")
        page = self.client.post(self.url("boq"), {"action": "terms", "retention_rate": "150", "advance_recovery_rate": "0"})
        self.assertContains(page, "من 0 لـ 100")

    def test_who_may_see_and_act(self):
        accountant = person(RoleCode.ACCOUNTANT, "c2_acc")
        self.client.force_login(accountant)
        for name in ("boq", "certificates", "payments", "subcontracts"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(self.url(name)).status_code, 200)
        page = self.client.get(self.url("certificates"))
        self.assertNotContains(page, "data-certificate-form")
        self.assertNotContains(page, self.url("budget"))  # the budget shows cost: profit-report holders only
        self.assertEqual(self.client.post(self.url("boq"), {"action": "add", "description": "x", "quantity": "1", "rate": "1"}).status_code, 403)
        keeper = person(RoleCode.STOCK_KEEPER, "c2_keeper")
        self.client.force_login(keeper)
        for name in ("boq", "certificates", "payments"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(self.url(name)).status_code, 403)
        cashier = person(RoleCode.CASHIER, "c2_cashier")
        self.client.force_login(cashier)
        tabs = self.client.get(self.url("detail"))
        self.assertNotContains(tabs, self.url("budget"))  # no profit report: no budget tab


class ReviewFindingsTests(ContractSetup):
    """Codex review on #179: returns, cancellations and releases keep the contract figures possible."""

    def setUp(self):
        super().setUp()
        self.advance = contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="30000")

    def test_a_full_sales_return_gives_back_quantities_retention_and_recovery(self):
        from sales.services import create_sales_return

        first = self.certify(concrete="40")
        post_sales_invoice(first.invoice_id, self.owner)
        line = first.invoice.lines.get()
        create_sales_return("SR-C1", TODAY, first.invoice_id, [{"source_line": line, "quantity": D("40")}], "خطأ في الكميات", self.owner)
        self.assertEqual(contract.certified_quantity(self.concrete), D("0"))
        self.assertEqual(contract.effective(first), (D("0.00"), D("0.00"), D("0.00")))
        figures = contract.position(self.project)
        self.assertEqual((figures["due_now"], figures["retention_held"], figures["recovered"]), (D("0.00"), D("0.00"), D("0.00")))
        for control in ("retention_receivable", "sales"):
            self.assertEqual(gl(control) + (gl("sales_returns") if control == "sales" else D("0")), D("0.00"), control)
        self.assertEqual(gl("customer_advances"), D("-30000.00"))
        ledger_ok(self)
        # The quantity is free again.
        self.assertEqual(self.certify(concrete="40").lines.get().previous_quantity, D("0.000"))

    def test_a_partial_return_takes_back_its_share(self):
        from sales.services import create_sales_return

        first = self.certify(concrete="40")  # 40000, retention 2000, recovery 4000
        post_sales_invoice(first.invoice_id, self.owner)
        create_sales_return("SR-C2", TODAY, first.invoice_id, [{"source_line": first.invoice.lines.get(), "quantity": D("10")}], "x", self.owner)
        self.assertEqual(contract.certified_quantity(self.concrete), D("30.000"))
        self.assertEqual(contract.effective(first), (D("30000.00"), D("1500.00"), D("3000.00")))
        self.assertEqual(gl("retention_receivable"), D("1500.00"))
        self.assertEqual(gl("customer_advances"), D("-27000.00"))
        ledger_ok(self)

    def test_an_advance_a_certificate_recovered_cannot_be_cancelled(self):
        from sales.services import cancel_customer_payment

        post_sales_invoice(self.certify(concrete="40").invoice_id, self.owner)  # recovers 4000
        with self.assertRaisesMessage(ValidationError, "already recovered this advance"):
            cancel_customer_payment(self.advance.pk, self.owner, reason="غلط")
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.status, "posted")
        # A second advance covering the recovery can go.
        extra = contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="5000")
        cancel_customer_payment(extra.pk, self.owner, reason="مكررة")
        ledger_ok(self)

    def test_a_released_certificate_cannot_be_cancelled_until_the_release_is_reversed(self):
        first = self.certify(concrete="40")
        post_sales_invoice(first.invoice_id, self.owner)
        release = contract.release_retention(self.project, self.owner, amount="2000")
        with self.assertRaisesMessage(ValidationError, "more retention released than is held"):
            cancel_posted_sales_invoice(first.invoice_id, self.owner, reason="x")
        self.assertEqual(SalesInvoice.objects.get(pk=first.invoice_id).status, "posted")
        contract.reverse_release(self.project, release, self.owner)
        with self.assertRaisesMessage(ValidationError, "اتلغى بالفعل"):
            contract.reverse_release(self.project, release, self.owner)
        cancel_posted_sales_invoice(first.invoice_id, self.owner, reason="x")
        self.assertEqual(contract.position(self.project)["retention_held"], D("0.00"))
        self.assertEqual(gl("retention_receivable"), D("0.00"))
        ledger_ok(self)

    def test_releases_respect_closed_months(self):
        from datetime import timedelta

        from closing.models import Period, PeriodStatus

        post_sales_invoice(self.certify(concrete="40").invoice_id, self.owner)
        start = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
        Period.objects.create(period_code="CLOSED-1", name="closed", start_date=start, end_date=TODAY.replace(day=1) - timedelta(days=1),
                              status=PeriodStatus.CLOSED, closed_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            contract.release_retention(self.project, self.owner, amount="100", release_date=start)

    def test_screens_only_use_this_entitys_cashboxes_and_payments(self):
        from entities.current import SESSION_KEY
        from entities.models import Entity
        from entities.services import main_entity

        other = Entity.objects.create(code="E2", name_ar="شركة تانية")
        foreign = make_cashbox(cashbox_code="FAR", entity=other)
        self.client.force_login(self.owner)
        session = self.client.session
        session[SESSION_KEY] = main_entity().pk  # working in the main entity
        session.save()
        url = reverse("projects:payments", args=[self.project.pk])
        self.client.post(url, {"action": "receive", "kind": "collection", "cashbox": str(foreign.pk), "amount": "100"})
        self.assertFalse(self.project.payments.filter(payment__cashbox=foreign).exists())


class SubcontractReturnTests(ContractSetup):
    def test_a_purchase_return_takes_back_its_share_of_retention(self):
        from purchases.services import create_purchase_return

        supplier = make_supplier(supplier_code="S-R", name="مقاول")
        subcontract = costs.save_subcontract(self.project, self.owner, {"supplier": supplier, "scope": "x", "value": "50000", "retention_rate": "10"})
        bill = costs.bill_subcontract(subcontract, self.owner, amount="20000", description="1")
        post_purchase_invoice(bill.invoice_id, self.owner)
        create_purchase_return("PR-S1", TODAY, bill.invoice_id, [{"source_line": bill.invoice.lines.get(), "quantity": D("0.5")}], "نص الأعمال", self.owner)
        self.assertEqual(costs.effective_bill(bill), (D("10000.00"), D("1000.00")))
        self.assertEqual(costs.subcontract_figures(subcontract)["retention_held"], D("1000.00"))
        self.assertEqual(gl("retention_payable"), D("-1000.00"))
        ledger_ok(self)
        release = costs.release_subcontract_retention(subcontract, self.owner, amount="1000")
        with self.assertRaisesMessage(ValidationError, "subcontractor retention released"):
            create_purchase_return("PR-S2", TODAY, bill.invoice_id, [{"source_line": bill.invoice.lines.get(), "quantity": D("0.5")}], "الباقي", self.owner)
        costs.reverse_subcontract_release(release, self.owner)
        create_purchase_return("PR-S2", TODAY, bill.invoice_id, [{"source_line": bill.invoice.lines.get(), "quantity": D("0.5")}], "الباقي", self.owner)
        self.assertEqual(costs.subcontract_figures(subcontract)["retention_held"], D("0.00"))
        self.assertEqual((gl("retention_payable"), gl("payable")), (D("0.00"), D("0.00")))
        ledger_ok(self)


class ReviewRoundTwoTests(ContractSetup):
    """Codex's second review on #179."""

    def test_a_return_cannot_be_cancelled_once_its_quantity_was_billed_again(self):
        from sales.models import SalesReturn
        from sales.services import cancel_sales_return, create_sales_return

        first = self.certify(concrete="40")
        post_sales_invoice(first.invoice_id, self.owner)
        create_sales_return("SR-Q1", TODAY, first.invoice_id, [{"source_line": first.invoice.lines.get(), "quantity": D("10")}], "x", self.owner)
        again = self.certify(concrete="40")  # bills the 10 the return gave back
        self.assertEqual((again.lines.get().previous_quantity, again.lines.get().quantity), (D("30.000"), D("10.000")))
        returned = SalesReturn.objects.get(return_number="SR-Q1")
        with self.assertRaisesMessage(ValidationError, "already billed the quantity"):
            cancel_sales_return(returned.pk, TODAY, "غلط", self.owner)
        self.assertEqual(SalesReturn.objects.get(pk=returned.pk).status, "posted")
        self.assertEqual(contract.certified_quantity(self.concrete), D("40.000"))

    def test_partial_returns_take_back_exactly_the_whole_retention(self):
        from sales.services import create_sales_return

        small = services.save_project({"name": "بند صغير", "customer": self.customer}, self.owner)
        line = contract.save_boq_line(small, self.owner, {"description": "x", "quantity": "3", "rate": "1"})
        contract.save_terms(small, self.owner, retention_rate="0.5", advance_recovery_rate="0")
        certificate = contract.create_certificate(small, self.owner, quantities={line.pk: "3"})
        self.assertEqual(certificate.retention_amount, D("0.02"))
        post_sales_invoice(certificate.invoice_id, self.owner)
        sales_line = certificate.invoice.lines.get()
        for n in range(3):
            create_sales_return(f"SR-R{n}", TODAY, certificate.invoice_id, [{"source_line": sales_line, "quantity": D("1")}], "x", self.owner)
        self.assertEqual(contract.effective(certificate), (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(gl("retention_receivable"), D("0.00"))
        ledger_ok(self)

    def test_a_project_is_billed_from_one_entity(self):
        from entities.current import working_in
        from entities.models import Entity

        post_sales_invoice(self.certify(concrete="10").invoice_id, self.owner)
        branch = Entity.objects.create(code="E2", name_ar="فرع تاني")
        make_location(location_code="E2-LOC", entity=branch)
        with working_in(branch), self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            self.certify(concrete="20")
        release = contract.release_retention(self.project, self.owner, amount="500")
        self.assertTrue(release.pk)
        ledger_ok(self)


class ReviewRoundThreeTests(ContractSetup):
    """Codex's third review on #179."""

    def test_the_owner_cannot_change_once_money_exists(self):
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="1000")
        other = make_customer(customer_code="C-NEW", name="مالك تاني")
        with self.assertRaisesMessage(ValidationError, "مينفعش يتغير صاحبه"):
            services.save_project({"name": self.project.name, "customer": other}, self.owner, self.project)
        fresh = services.save_project({"name": "مشروع فاضي", "customer": self.customer}, self.owner)
        services.save_project({"name": fresh.name, "customer": other}, self.owner, fresh)  # nothing on it yet: fine

    def test_an_instalment_collection_is_never_a_project_advance(self):
        from installments.models import InstalmentPayment
        from installments.services import create_plan
        from sales.services import record_customer_payment

        from settings_core.capabilities import _write

        _write("installments", True)
        from sales.services import create_sales_draft

        invoice = create_sales_draft({"invoice_number": "SI-INST", "invoice_date": TODAY, "customer": self.customer, "selling_location": self.store},
                                     [{"item": services.billing_item(), "quantity": D("1"), "unit_sale_price": D("900")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        from datetime import timedelta

        plan = create_plan(SalesInvoice.objects.get(pk=invoice.pk), 3, TODAY + timedelta(days=30), self.owner)
        payment = record_customer_payment("CP-I1", TODAY, self.customer, self.cashbox, D("300"), self.owner)
        InstalmentPayment.objects.create(plan=plan, payment=payment)
        with self.assertRaisesMessage(ValidationError, "قسط على خطة تقسيط"):
            contract.link_payment(self.project, payment, self.owner, kind=ProjectPaymentKind.ADVANCE)

    def test_the_projects_money_stays_in_its_entity_and_others_do_not_see_its_certificates(self):
        from entities.current import working_in
        from entities.models import Entity

        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="1000")
        branch = Entity.objects.create(code="E3", name_ar="فرع")
        far_box = make_cashbox(cashbox_code="E3-BOX", entity=branch)
        make_location(location_code="E3-LOC", entity=branch)
        with self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=far_box, amount="500")
        with working_in(branch), self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            self.certify(concrete="5")  # the advance already placed the project in the main entity
        certificate = self.certify(concrete="5")
        from projects.contract_views import _certificates

        with working_in(branch):
            self.assertFalse(_certificates(self.project).exists())
        self.assertEqual(list(_certificates(self.project)), [certificate])


class ReviewRoundFourTests(ContractSetup):
    """Codex's fourth review on #179: entity reach, closed months, stable return shares, dated releases."""

    def setUp(self):
        super().setUp()
        self.subcontract = costs.save_subcontract(self.project, self.owner, {"supplier": make_supplier(supplier_code="S-SUB", name="مقاول باطن"),
                                                                             "scope": "أعمال المباني", "value": "60000", "retention_rate": "10"})

    def branch(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="E4", name_ar="فرع رابع")
        make_location(location_code="E4-LOC", entity=branch)
        make_cashbox(cashbox_code="E4-BOX", entity=branch)
        return branch

    def close_last_month(self):
        from datetime import timedelta

        from closing.models import Period, PeriodStatus

        from closing.services import get_period_for_date

        end = TODAY.replace(day=1) - timedelta(days=1)
        period = get_period_for_date(end)  # posting there already opened it: close that one, never add a second
        if period is None:
            Period.objects.create(period_code="CLOSED-4", name="closed", start_date=end.replace(day=1), end_date=end,
                                  status=PeriodStatus.CLOSED, closed_at=timezone.now())
        else:
            Period.objects.filter(pk=period.pk).update(status=PeriodStatus.CLOSED, closed_at=timezone.now())
        self.assertIsNotNone(get_period_for_date(end))
        self.assertEqual(get_period_for_date(end).status, PeriodStatus.CLOSED)
        return end

    def test_another_entity_cannot_change_or_see_the_projects_money(self):
        from entities.current import working_in

        advance = contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="10000")
        post_sales_invoice(self.certify(concrete="10").invoice_id, self.owner)  # 10000: retention 500, recovery 1000
        release = contract.release_retention(self.project, self.owner, amount="100")
        bill = costs.bill_subcontract(self.subcontract, self.owner, amount="5000", description="x")
        post_purchase_invoice(bill.invoice_id, self.owner)
        draft = costs.bill_subcontract(self.subcontract, self.owner, amount="1000", description="y")
        sub_release = costs.release_subcontract_retention(self.subcontract, self.owner, amount="100")
        branch = self.branch()
        link = self.project.payments.get(payment=advance)
        with working_in(branch):
            for attempt in (lambda: contract.unlink_payment(self.project, link, self.owner),
                            lambda: contract.release_retention(self.project, self.owner, amount="50"),
                            lambda: contract.reverse_release(self.project, release, self.owner),
                            lambda: contract.save_terms(self.project, self.owner, retention_rate="0", advance_recovery_rate="0"),
                            lambda: costs.withdraw_bill(draft, self.owner),
                            lambda: costs.release_subcontract_retention(self.subcontract, self.owner, amount="50"),
                            lambda: costs.reverse_subcontract_release(sub_release, self.owner),
                            lambda: costs.unlink_purchase(self.project, self.project.purchases.get(invoice=draft.invoice), self.owner)):
                with self.assertRaises(ValidationError):
                    attempt()
            # What it sees of the project carries none of the main entity's money.
            seen = contract.position(self.project)
            self.assertEqual({key: seen[key] for key in ("certified", "retention_held", "released", "advance", "recovered", "collections", "due_now")},
                             dict.fromkeys(("certified", "retention_held", "released", "advance", "recovered", "collections", "due_now"), D("0")))
            figures = costs.subcontract_figures(self.subcontract)
            self.assertEqual((figures["billed"], figures["retention_held"], figures["drafts"]), (D("0"), D("0"), D("0")))
            self.assertEqual((services.summary(self.project)["billed"], services.summary(self.project)["purchases"]), (D("0"), D("0")))
            self.assertEqual(costs.actual_by_heading(self.project), {})
        self.assertTrue(self.project.payments.filter(pk=link.pk).exists())
        self.assertFalse(release.__class__.objects.get(pk=release.pk).reversed_on)
        here = contract.position(self.project)
        self.assertEqual((here["certified"], here["retention_held"], here["advance"]), (D("10000.00"), D("400.00"), D("10000.00")))
        self.assertEqual(costs.subcontract_figures(self.subcontract)["billed"], D("5000.00"))

    def test_the_screen_refuses_to_unlink_another_entitys_payment(self):
        from entities.current import SESSION_KEY

        advance = contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.COLLECTION, cashbox=self.cashbox, amount="700")
        link = self.project.payments.get(payment=advance)
        branch = self.branch()
        self.client.force_login(self.owner)
        session = self.client.session
        session[SESSION_KEY] = branch.pk
        session.save()
        response = self.client.post(reverse("projects:payments", args=[self.project.pk]), {"action": "unlink", "link": str(link.pk)})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(self.project.payments.filter(pk=link.pk).exists())

    def test_tagging_an_old_payment_as_an_advance_respects_closed_months(self):
        from sales.services import record_customer_payment

        old_day = TODAY.replace(day=1) - timezone.timedelta(days=3)
        old = record_customer_payment("CP-OLD", old_day, self.customer, self.cashbox, D("2000"), self.owner)
        kept = record_customer_payment("CP-OLD2", old_day, self.customer, self.cashbox, D("500"), self.owner)
        advance = contract.link_payment(self.project, kept, self.owner, kind=ProjectPaymentKind.ADVANCE)
        self.close_last_month()
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            contract.link_payment(self.project, old, self.owner, kind=ProjectPaymentKind.ADVANCE)
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            contract.unlink_payment(self.project, advance, self.owner)
        contract.link_payment(self.project, old, self.owner, kind=ProjectPaymentKind.COLLECTION)  # a collection moves nothing in the books

    def test_linking_an_old_service_purchase_respects_closed_months(self):
        rental = make_item(item_code="RENT4", item_name="إيجار معدات", is_stock_tracked=False)
        supplier = make_supplier(supplier_code="S-4", name="معدات")
        old_day = TODAY.replace(day=1) - timezone.timedelta(days=3)
        invoices = []
        for number in ("PI-OLD", "PI-OLD2"):
            invoice = create_purchase_draft({"invoice_number": number, "invoice_date": old_day, "supplier": supplier, "receiving_location": self.store},
                                            [{"item": rental, "quantity": D("1"), "unit_purchase_price": D("800")}], self.owner)
            post_purchase_invoice(invoice.pk, self.owner)
            invoices.append(PurchaseInvoice.objects.get(pk=invoice.pk))
        link = costs.link_purchase(self.project, invoices[1], self.owner)
        self.close_last_month()
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            costs.link_purchase(self.project, invoices[0], self.owner)
        with self.assertRaisesMessage(ValidationError, "Period must be open"):
            costs.unlink_purchase(self.project, link, self.owner)

    def test_cancelling_a_return_keeps_every_other_returns_share(self):
        from sales.models import SalesReturn
        from sales.services import cancel_sales_return, create_sales_return

        small = services.save_project({"name": "بند صغير", "customer": self.customer}, self.owner)
        line = contract.save_boq_line(small, self.owner, {"description": "x", "quantity": "3", "rate": "1"})
        contract.save_terms(small, self.owner, retention_rate="0.5", advance_recovery_rate="0")
        certificate = contract.create_certificate(small, self.owner, quantities={line.pk: "3"})
        post_sales_invoice(certificate.invoice_id, self.owner)
        invoice = SalesInvoice.objects.get(pk=certificate.invoice_id)
        sales_line = invoice.lines.get()
        returns = []
        for n in range(3):
            create_sales_return(f"SR-S{n}", TODAY, invoice.pk, [{"source_line": sales_line, "quantity": D("1")}], "x", self.owner)
            returns.append(SalesReturn.objects.get(return_number=f"SR-S{n}"))
        before = contract.return_shares(D("0.02"), invoice)
        self.assertEqual([before[r.pk] for r in returns], [D("0.01"), D("0.00"), D("0.01")])
        ensure_fresh()
        cancel_sales_return(returns[0].pk, TODAY, "غلط", self.owner)
        self.assertEqual(contract.return_shares(D("0.02"), invoice), before)  # nobody else's share moved
        self.assertEqual(contract.effective(certificate)[1], D("0.01"))
        self.assertEqual(gl("retention_receivable"), D("0.01"))
        create_sales_return("SR-S3", TODAY, invoice.pk, [{"source_line": sales_line, "quantity": D("1")}], "x", self.owner)
        self.assertEqual(contract.effective(certificate)[1], D("0.00"))  # the whole invoice is back: all its retention too
        self.assertEqual(gl("retention_receivable"), D("0.00"))
        ledger_ok(self)

    def test_a_release_needs_the_retention_held_on_its_own_date(self):
        from datetime import timedelta

        if TODAY.day == 1:
            self.skipTest("needs an earlier day in the same month")
        certificate = contract.create_certificate(self.project, self.owner, quantities={self.concrete.pk: "10"}, certificate_date=TODAY)
        post_sales_invoice(certificate.invoice_id, self.owner)  # retention 500 held from today
        with self.assertRaisesMessage(ValidationError, "أكبر من ضمان الأعمال المحتجز (0"):
            contract.release_retention(self.project, self.owner, amount="100", release_date=TODAY - timedelta(days=1))
        contract.release_retention(self.project, self.owner, amount="500", release_date=TODAY)
        bill = costs.bill_subcontract(self.subcontract, self.owner, amount="5000", description="x", bill_date=TODAY)
        post_purchase_invoice(bill.invoice_id, self.owner)
        with self.assertRaisesMessage(ValidationError, "أكبر من ضمان الأعمال المحتجز (0"):
            costs.release_subcontract_retention(self.subcontract, self.owner, amount="100", release_date=TODAY - timedelta(days=1))
        costs.release_subcontract_retention(self.subcontract, self.owner, amount="500", release_date=TODAY)
        ledger_ok(self)


class ReviewRoundFiveTests(ContractSetup):
    """Codex's fifth review on #179."""

    def test_a_backdated_certificate_recovers_only_advances_received_by_its_date(self):
        from datetime import timedelta

        if TODAY.day < 3:
            self.skipTest("needs two earlier days in the same month")
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="30000")  # today
        early = contract.create_certificate(self.project, self.owner, quantities={self.concrete.pk: "10"}, certificate_date=TODAY - timedelta(days=2))
        self.assertEqual(early.recovery_amount, D("0.00"))  # nothing was received by then
        post_sales_invoice(early.invoice_id, self.owner)
        later = self.certify(concrete="20")
        self.assertEqual(later.recovery_amount, D("1000.00"))
        post_sales_invoice(later.invoice_id, self.owner)
        self.assertGreaterEqual(contract.lowest_held(contract.advance_events(self.project)), 0)
        ledger_ok(self)

    def test_an_advance_cannot_be_unlinked_when_an_earlier_certificate_needs_it(self):
        from sales.services import record_customer_payment

        old = record_customer_payment("CP-A1", TODAY, self.customer, self.cashbox, D("1000"), self.owner)
        first = contract.link_payment(self.project, old, self.owner, kind=ProjectPaymentKind.ADVANCE)
        post_sales_invoice(self.certify(concrete="10").invoice_id, self.owner)  # recovers 1000
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="5000")
        contract.unlink_payment(self.project, first, self.owner)  # today's later advance still covers it on the same date
        self.assertFalse(self.project.payments.filter(pk=first.pk).exists())

    def test_the_subcontractors_tab_shows_no_sales_figures_to_a_purchases_only_user(self):
        from permissions.services import user_has_permission

        clerk = person(RoleCode.STOCK_KEEPER, "c2_keeper")  # sees purchases, not sales
        self.assertTrue(user_has_permission(clerk, "purchases.view_purchase_invoices"))
        self.assertFalse(user_has_permission(clerk, "sales.view_sales_invoices"))
        self.client.force_login(clerk)
        page = self.client.get(reverse("projects:subcontracts", args=[self.project.pk]) + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-contract-position")
        self.assertNotContains(page, self.customer.name)

    def test_a_stock_item_holding_the_reserved_code_is_never_billed(self):
        make_item(item_code="PRJ-SUB", item_name="صنف مخزني", is_stock_tracked=True)
        make_item(item_code="PRJ-BILL", item_name="صنف مخزني 2", is_stock_tracked=True)
        subcontract = costs.save_subcontract(self.project, self.owner, {"supplier": make_supplier(supplier_code="S-5", name="مقاول"), "scope": "x",
                                                                        "value": "1000", "retention_rate": "0"})
        bill = costs.bill_subcontract(subcontract, self.owner, amount="1000", description="x")
        self.assertEqual(bill.invoice.lines.get().item.item_code, "PRJ-SUB-2")
        self.assertFalse(bill.invoice.lines.get().item.is_stock_tracked)
        certificate = self.certify(concrete="1")
        self.assertEqual(certificate.invoice.lines.get().item.item_code, "PRJ-BILL-2")
        post_purchase_invoice(bill.invoice_id, self.owner)
        post_sales_invoice(certificate.invoice_id, self.owner)
        self.assertEqual(gl("project_cost"), D("1000.00"))
        ledger_ok(self)


class ReviewRoundSixTests(ContractSetup):
    """Codex's sixth review on #179."""

    def test_a_cancelled_certificate_keeps_the_project_in_its_entity(self):
        from entities.current import working_in
        from entities.models import Entity

        first = self.certify(concrete="10")
        post_sales_invoice(first.invoice_id, self.owner)
        release = contract.release_retention(self.project, self.owner, amount="500")
        contract.reverse_release(self.project, release, self.owner)
        cancel_posted_sales_invoice(first.invoice_id, self.owner, reason="x")
        branch = Entity.objects.create(code="E6", name_ar="فرع سادس")
        make_location(location_code="E6-LOC", entity=branch)
        with working_in(branch), self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            self.certify(concrete="5")  # the past release stays where its retention was held
        self.assertEqual(contract.project_entity(self.project), contract.entity_of(first.invoice.selling_location))
        ledger_ok(self)

    def test_a_returned_service_line_takes_off_its_own_tax_only(self):
        from purchases.services import create_purchase_return
        from settings_core.capabilities import _write
        from taxes.models import ItemTaxRate, TaxRate
        from taxes.services import create_purchase_draft_with_tax

        _write("vat", True)
        taxed = make_item(item_code="SV-T", item_name="نقل", is_stock_tracked=False)
        exempt = make_item(item_code="SV-E", item_name="خدمة معفاة", is_stock_tracked=False)
        ItemTaxRate.objects.create(item=exempt, tax_rate=TaxRate.objects.get(code="EXEMPT"))
        invoice = create_purchase_draft_with_tax(
            {"invoice_number": "PI-VAT", "invoice_date": TODAY, "supplier": make_supplier(supplier_code="S-6", name="مورد"), "receiving_location": self.store},
            [{"item": taxed, "quantity": D("10"), "unit_purchase_price": D("50")}, {"item": exempt, "quantity": D("4"), "unit_purchase_price": D("25")}], self.owner)
        post_purchase_invoice(invoice.pk, self.owner)
        invoice = PurchaseInvoice.objects.get(pk=invoice.pk)
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("70.00"), D("670.00")))
        costs.link_purchase(self.project, invoice, self.owner, heading=CostHeading.EQUIPMENT)
        create_purchase_return("PR-VAT", TODAY, invoice.pk, [{"source_line": invoice.lines.get(item=taxed), "quantity": D("2")}], "x", self.owner)
        self.assertEqual(costs.net_purchase(invoice), D("500.00"))  # 600 before tax − the 100 returned before its 14 tax
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.EQUIPMENT], D("500.00"))
        self.assertEqual(gl("project_cost"), D("500.00"))  # the books agree
        ledger_ok(self)


class ReviewRoundSevenTests(ContractSetup):
    """Codex's seventh review on #179."""

    def test_the_deductions_never_exceed_the_certificate(self):
        with self.assertRaisesMessage(ValidationError, "ميزيدش عن 100%"):
            contract.save_terms(self.project, self.owner, retention_rate="60", advance_recovery_rate="50")
        contract.save_terms(self.project, self.owner, retention_rate="100", advance_recovery_rate="0")
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="50000")
        self.project.refresh_from_db()
        from projects.models import Project

        Project.objects.filter(pk=self.project.pk).update(advance_recovery_rate=D("100"))  # terms saved before the check existed
        self.project.refresh_from_db()
        certificate = self.certify(concrete="10")  # 10000
        self.assertEqual((certificate.retention_amount, certificate.recovery_amount), (D("10000.00"), D("0.00")))
        post_sales_invoice(certificate.invoice_id, self.owner)
        self.assertGreaterEqual(contract.position(self.project)["due_now"], D("0"))

    def test_a_certificate_whose_retention_was_once_released_is_returned_not_cancelled(self):
        from datetime import timedelta

        if TODAY.day < 3:
            self.skipTest("needs two earlier days in the same month")
        first = contract.create_certificate(self.project, self.owner, quantities={self.concrete.pk: "10"}, certificate_date=TODAY - timedelta(days=2))
        post_sales_invoice(first.invoice_id, self.owner)
        release = contract.release_retention(self.project, self.owner, amount="500", release_date=TODAY - timedelta(days=1))
        contract.reverse_release(self.project, release, self.owner, reversal_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "make a sales return instead"):
            cancel_posted_sales_invoice(first.invoice_id, self.owner, reason="x")
        from sales.services import create_sales_return

        create_sales_return("SR-H1", TODAY, first.invoice_id, [{"source_line": first.invoice.lines.get(), "quantity": D("10")}], "x", self.owner)
        self.assertEqual(contract.retention_held(self.project), D("0.00"))
        self.assertGreaterEqual(contract.lowest_held(contract.retention_events(self.project)), 0)
        ledger_ok(self)


class ReviewRoundEightTests(ContractSetup):
    """Codex's eighth review on #179."""

    def test_a_certificate_invoice_is_never_put_on_instalments(self):
        from datetime import timedelta

        from installments.services import create_plan
        from settings_core.capabilities import _write

        _write("installments", True)
        certificate = self.certify(concrete="10")
        post_sales_invoice(certificate.invoice_id, self.owner)
        with self.assertRaisesMessage(ValidationError, "فاتورة مستخلص مشروع"):
            create_plan(SalesInvoice.objects.get(pk=certificate.invoice_id), 3, TODAY + timedelta(days=30), self.owner)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("sales:detail", args=[certificate.invoice_id]) + "?lang=ar")
        self.assertNotContains(page, "data-instalment-new")

    def test_a_certificate_invoice_stays_on_its_project_and_its_guards_still_run(self):
        from .models import ProjectInvoice

        first = self.certify(concrete="10")
        post_sales_invoice(first.invoice_id, self.owner)
        contract.release_retention(self.project, self.owner, amount="500")
        link = ProjectInvoice.objects.get(invoice_id=first.invoice_id)
        with self.assertRaisesMessage(ValidationError, "فاتورة مستخلص"):
            services.unlink(self.project, link, self.owner)
        self.assertTrue(ProjectInvoice.objects.filter(pk=link.pk).exists())
        ProjectInvoice.objects.filter(pk=link.pk).delete()  # even without its link (an old row), the certificate guards it
        with self.assertRaisesMessage(ValidationError, "more retention released than is held"):
            cancel_posted_sales_invoice(first.invoice_id, self.owner, reason="x")


class ReviewRoundNineTests(ContractSetup):
    """Codex's ninth review on #179."""

    def test_the_project_list_computes_due_now_in_bulk_and_exactly(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from sales.services import create_sales_return

        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="5000")
        post_sales_invoice(self.certify(concrete="10").invoice_id, self.owner)
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.COLLECTION, cashbox=self.cashbox, amount="2000")
        contract.release_retention(self.project, self.owner, amount="200")
        projects = [self.project]
        for n in range(4):
            other = services.save_project({"name": f"مشروع {n}", "customer": self.customer}, self.owner)
            line = contract.save_boq_line(other, self.owner, {"description": "x", "quantity": "10", "rate": "100"})
            contract.save_terms(other, self.owner, retention_rate="5", advance_recovery_rate="0")
            certificate = contract.create_certificate(other, self.owner, quantities={line.pk: "4"})
            post_sales_invoice(certificate.invoice_id, self.owner)
            if n == 0:  # one with a return: it falls back to position()
                create_sales_return(f"SR-L{n}", TODAY, certificate.invoice_id, [{"source_line": certificate.invoice.lines.get(), "quantity": D("1")}], "x", self.owner)
            projects.append(other)
        with CaptureQueriesContext(connection) as queries:
            bulk = contract.due_now_many(projects)
        self.assertEqual(bulk, {project.pk: contract.position(project)["due_now"] for project in projects})
        self.assertLess(len(queries), 40)  # grouped, not a dozen per project
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("projects:list") + "?lang=ar").status_code, 200)

    def test_a_reserved_item_made_meanwhile_is_reused_not_duplicated(self):
        from django.db import IntegrityError

        from master_data.models import Item

        # The other request committed its row between our read and our insert: the insert fails on the unique code.
        Item.objects.create(item_code="PRJ-RACE", item_name="من طلب تاني", is_stock_tracked=False, default_sale_price=0)

        def raced(**kwargs):
            raise IntegrityError("duplicate key")

        with __import__("unittest").mock.patch.object(Item.objects, "get_or_create", side_effect=raced):
            item = services.service_item("PRJ-RACE", "خدمة")
        self.assertEqual((item.item_code, item.item_name), ("PRJ-RACE", "من طلب تاني"))
        self.assertEqual(Item.objects.filter(item_code="PRJ-RACE").count(), 1)


class ReviewRoundTenTests(ContractSetup):
    """Codex's tenth review on #179."""

    def test_no_bill_of_quantities_after_a_lump_sum_certificate(self):
        lump = services.save_project({"name": "مقطوعية", "customer": self.customer}, self.owner)
        contract.create_certificate(lump, self.owner, amount="5000", description="أعمال الشهر الأول")
        with self.assertRaisesMessage(ValidationError, "مستخلص مقطوعية"):
            contract.save_boq_line(lump, self.owner, {"description": "x", "quantity": "1", "rate": "1"})

    def test_certificates_run_forward_in_time(self):
        from datetime import timedelta

        first = contract.create_certificate(self.project, self.owner, quantities={self.concrete.pk: "40"}, certificate_date=TODAY)
        post_sales_invoice(first.invoice_id, self.owner)
        with self.assertRaisesMessage(ValidationError, TODAY.isoformat()):
            contract.create_certificate(self.project, self.owner, quantities={self.concrete.pk: "50"}, certificate_date=TODAY - timedelta(days=9))
        self.assertTrue(contract.create_certificate(self.project, self.owner, quantities={self.concrete.pk: "50"}, certificate_date=TODAY).pk)


class ReviewRoundElevenTests(ContractSetup):
    """Codex's eleventh review on #179: a linked invoice anchors the project; its instalments are its collections."""

    def sale(self, number, amount, location=None):
        from sales.services import create_sales_draft

        invoice = create_sales_draft({"invoice_number": number, "invoice_date": TODAY, "customer": self.customer, "selling_location": location or self.store},
                                     [{"item": services.billing_item(), "quantity": D("1"), "unit_sale_price": D(amount)}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        return SalesInvoice.objects.get(pk=invoice.pk)

    def test_an_ordinary_linked_invoice_anchors_the_project_to_its_entity(self):
        from entities.current import working_in
        from entities.models import Entity

        branch = Entity.objects.create(code="E11", name_ar="فرع حداشر")
        branch_store = make_location(location_code="E11-LOC", entity=branch)
        services.link_invoice(self.project, self.sale("SI-A", "1000"), self.owner)
        self.assertEqual(contract.project_entity(self.project), contract.entity_of(self.store))
        with working_in(branch), self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            self.certify(concrete="5")  # its first certificate cannot move it to the branch
        with self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            services.link_invoice(self.project, self.sale("SI-B", "500", branch_store), self.owner)
        # The invoice's collection, from its own entity, still links and lowers what is due.
        due = contract.position(self.project)["due_now"]
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.COLLECTION, cashbox=self.cashbox, amount="400")
        self.assertEqual(contract.position(self.project)["due_now"], due - D("400"))

    def test_an_instalment_of_the_projects_own_invoice_is_its_collection(self):
        from datetime import timedelta

        from installments.models import InstalmentPayment
        from installments.services import create_plan
        from sales.services import record_customer_payment
        from settings_core.capabilities import _write

        _write("installments", True)
        invoice = self.sale("SI-PLAN", "900")
        services.link_invoice(self.project, invoice, self.owner)
        plan = create_plan(invoice, 3, TODAY + timedelta(days=30), self.owner)
        paid = record_customer_payment("CP-P1", TODAY, self.customer, self.cashbox, D("300"), self.owner)
        InstalmentPayment.objects.create(plan=plan, payment=paid)
        due = contract.position(self.project)["due_now"]
        with self.assertRaisesMessage(ValidationError, "كتحصيل بس"):
            contract.link_payment(self.project, paid, self.owner, kind=ProjectPaymentKind.ADVANCE)
        contract.link_payment(self.project, paid, self.owner, kind=ProjectPaymentKind.COLLECTION)
        self.assertEqual(contract.position(self.project)["due_now"], due - D("300"))
        # The invoice stays on the project while its instalments count there.
        with self.assertRaisesMessage(ValidationError, "أقساط الفاتورة دي"):
            services.unlink(self.project, self.project.invoices.get(invoice=invoice), self.owner)
        # An instalment of an invoice that is not on this project is not its collection.
        other = self.sale("SI-OTHER", "600")
        other_plan = create_plan(other, 2, TODAY + timedelta(days=30), self.owner)
        stray = record_customer_payment("CP-P2", TODAY, self.customer, self.cashbox, D("300"), self.owner)
        InstalmentPayment.objects.create(plan=other_plan, payment=stray)
        with self.assertRaisesMessage(ValidationError, "قسط على خطة تقسيط"):
            contract.link_payment(self.project, stray, self.owner, kind=ProjectPaymentKind.COLLECTION)

    def test_the_payments_screen_offers_only_this_projects_instalments(self):
        from datetime import timedelta

        from installments.models import InstalmentPayment
        from installments.services import create_plan
        from sales.services import record_customer_payment
        from settings_core.capabilities import _write

        _write("installments", True)
        mine, theirs = self.sale("SI-MINE", "900"), self.sale("SI-THEIRS", "900")
        services.link_invoice(self.project, mine, self.owner)
        for number, invoice in (("CP-M", mine), ("CP-T", theirs)):
            payment = record_customer_payment(number, TODAY, self.customer, self.cashbox, D("300"), self.owner)
            InstalmentPayment.objects.create(plan=create_plan(invoice, 3, TODAY + timedelta(days=30), self.owner), payment=payment)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("projects:payments", args=[self.project.pk]) + "?lang=ar")
        self.assertContains(page, "CP-M")
        self.assertNotContains(page, "CP-T")
