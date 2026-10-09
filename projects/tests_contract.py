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
    def test_without_the_contracting_accounts_balances_stay_in_receivable(self):
        prepared_client(activity="services", sub_activity="maintenance", modules=MODULES)
        owner = person(RoleCode.OWNER, "c2_srv_owner")
        make_location(location_code="STORE", is_default=True)
        cashbox = make_cashbox(cashbox_code="MAIN", is_default=True)
        customer = make_customer(customer_code="C-S")
        project = services.save_project({"name": "عقد صيانة", "customer": customer, "contract_value": "10000"}, owner)
        contract.save_terms(project, owner, retention_rate="5", advance_recovery_rate="0")
        post_sales_invoice(services.bill_progress(project, owner, amount="10000", description="شهر 1").pk, owner)
        self.assertEqual(gl("receivable"), D("10000.00"))
        ledger_ok(self)


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
