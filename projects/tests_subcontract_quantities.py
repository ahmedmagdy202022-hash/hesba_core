"""Contracting C11 (HG-128): subcontractor bills by quantities, the subcontractor's advance and its recovery, the value
and quantity caps, the warning ahead of the owner, the guard on cancelling an advance, and how the general ledger shows
them. Reference subcontract: plaster 5,000 m² × 60 = 300,000.00, retention 5%, recovery 10%, advance 30,000.00."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.urls import reverse

from hesba_testing.factories import make_cashbox, make_supplier
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice, SupplierPayment
from purchases.services import cancel_posted_purchase_invoice, cancel_purchase_return, cancel_supplier_payment, create_purchase_return, post_purchase_invoice
from sales.services import post_sales_invoice

from . import contract, costs, services, subcontracting
from .models import CostHeading, SubcontractPaymentKind
from .tests_contract import TODAY, ContractSetup, gl, ledger_ok, person


def supplier_balance(supplier):
    from purchases.models import SupplierLedgerEntry

    entries = SupplierLedgerEntry.objects.filter(supplier=supplier)
    return sum((e.due_increase - e.due_decrease for e in entries), D("0")) + supplier.opening_balance


class SubcontractSetup(ContractSetup):
    def setUp(self):
        super().setUp()
        # The client's plaster item (the owner pays 80 a m²); the subcontractor does it at 60.
        self.client_plaster = contract.save_boq_line(self.project, self.owner, {"code": "3", "description": "بياض محارة", "unit": "م²",
                                                                                "quantity": "5000", "rate": "80"})
        self.sub_supplier = make_supplier(supplier_code="S-PLASTER", name="مؤسسة الإتقان للتشطيبات")
        self.subcontract = costs.save_subcontract(self.project, self.owner, {"supplier": self.sub_supplier, "scope": "أعمال البياض", "value": "300000",
                                                                             "retention_rate": "5", "advance_recovery_rate": "10"})
        self.plaster = subcontracting.save_line(self.subcontract, self.owner, {"description": "بياض محارة", "unit": "م²", "quantity": "5000",
                                                                               "rate": "60", "boq_line": self.client_plaster.pk})

    def advance(self, amount="30000"):
        return subcontracting.pay(self.subcontract, self.owner, kind=SubcontractPaymentKind.ADVANCE, cashbox=self.cashbox, amount=amount)

    def bill(self, cumulative, **kwargs):
        return subcontracting.bill_by_quantities(self.subcontract, self.owner, quantities={self.plaster.pk: cumulative}, **kwargs)

    def post(self, bill):
        post_purchase_invoice(bill.invoice_id, self.owner)
        bill.refresh_from_db()
        bill.invoice.refresh_from_db()
        return bill


class BillsByQuantitiesTests(SubcontractSetup):
    def test_two_bills_by_cumulative_quantities_with_retention_and_advance_recovery(self):
        payment = self.advance()
        self.assertEqual((payment.payment_number, payment.amount, payment.supplier), ("SP-00001", D("30000.00"), self.sub_supplier))
        self.assertEqual(payment.subcontract_payment.kind, SubcontractPaymentKind.ADVANCE)
        self.assertEqual(gl("subcontractor_advances"), D("30000.00"))  # the advance is an asset, not a smaller payable
        self.assertEqual(gl("payable"), D("0.00"))

        first = self.bill("1000")
        self.assertEqual((first.number, first.invoice.invoice_number, first.invoice.status), (1, "SC-P-0001-01", "draft"))
        self.assertEqual((first.gross, first.retention_amount, first.recovery_amount), (D("60000.00"), D("3000.00"), D("6000.00")))
        self.assertEqual((first.invoice.total_amount, costs.bill_net_payable(first)), (D("60000.00"), D("51000.00")))
        self.assertEqual([(r.previous_quantity, r.quantity, r.rate, r.amount) for r in first.lines.all()],
                         [(D("0.000"), D("1000.000"), D("60.00"), D("60000.00"))])
        line = first.invoice.lines.get()
        self.assertEqual((line.quantity, line.unit_purchase_price, line.item.item_code), (D("1000.000"), D("60.00"), "PRJ-SUB"))
        with self.assertRaisesMessage(ValidationError, "لسه مسودة (1)"):
            self.bill("1500")
        self.post(first)

        with self.assertRaisesMessage(ValidationError, "أقل من اللي اتعمل بيه مستخلصات"):
            self.bill("900")
        with self.assertRaisesMessage(ValidationError, "مفيش كميات جديدة"):
            self.bill("1000")
        second = self.bill("2500")
        self.assertEqual([(r.previous_quantity, r.quantity, r.amount) for r in second.lines.all()], [(D("1000.000"), D("1500.000"), D("90000.00"))])
        self.assertEqual((second.gross, second.retention_amount, second.recovery_amount), (D("90000.00"), D("4500.00"), D("9000.00")))
        self.assertEqual(costs.bill_net_payable(second), D("76500.00"))
        self.post(second)

        # Project cost and the general ledger after both bills (VAT off), credits negative.
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.SUBCONTRACT], D("150000.00"))
        self.assertEqual(services.summary(self.project)["purchases"], D("150000.00"))
        self.assertEqual(gl("payable"), D("-127500.00"))
        self.assertEqual(gl("retention_payable"), D("-7500.00"))
        self.assertEqual(gl("subcontractor_advances"), D("15000.00"))
        self.assertEqual(gl("project_cost"), D("150000.00"))
        self.assertEqual(gl("cash"), D("-30000.00"))
        # The supplier's own ledger is untouched: 150,000 billed − 30,000 paid = 127,500 + 7,500 − 15,000.
        self.assertEqual(supplier_balance(self.sub_supplier), D("120000.00"))
        self.assertEqual(subcontracting.figures(self.subcontract)["advance_left"], D("15000.00"))
        self.assertEqual(costs.subcontract_figures(self.subcontract)["retention_held"], D("7500.00"))
        ledger_ok(self)

    def test_vat_is_on_the_gross_and_retention_and_recovery_come_after_it(self):
        from settings_core.capabilities import _write

        _write("vat", True)
        self.advance()
        first = self.bill("1000")
        self.assertEqual((first.invoice.tax_amount, first.invoice.total_amount), (D("8400.00"), D("68400.00")))
        self.assertEqual(costs.bill_net_payable(first), D("59400.00"))  # 68,400 − 3,000 − 6,000
        self.post(first)
        second = self.bill("2500")
        self.assertEqual((second.invoice.tax_amount, second.invoice.total_amount), (D("12600.00"), D("102600.00")))
        self.assertEqual((second.retention_amount, second.recovery_amount), (D("4500.00"), D("9000.00")))
        self.assertEqual(costs.bill_net_payable(second), D("89100.00"))
        self.post(second)
        self.assertEqual((gl("vat_in"), gl("project_cost")), (D("21000.00"), D("150000.00")))
        self.assertEqual((gl("payable"), gl("retention_payable"), gl("subcontractor_advances")), (D("-148500.00"), D("-7500.00"), D("15000.00")))
        self.assertEqual(supplier_balance(self.sub_supplier), D("141000.00"))  # 171,000 − 30,000
        ledger_ok(self)

    def test_the_quantity_never_passes_the_subcontract_line(self):
        with self.assertRaisesMessage(ValidationError, "أكبر من كميته في عقد مقاول الباطن"):
            self.bill("5001")
        with self.assertRaisesMessage(ValidationError, "is above its quantity in the subcontract"):
            self.bill("5001", lang="en")
        self.assertFalse(PurchaseInvoice.objects.exists())
        self.assertEqual(self.post(self.bill("5000")).gross, D("300000.00"))

    def test_the_billed_gross_never_passes_the_subcontract_value(self):
        # A second line takes the lines to the value; raising the value is the subcontract's variation.
        with self.assertRaisesMessage(ValidationError, "زوّد قيمة العقد الأول"):
            subcontracting.save_line(self.subcontract, self.owner, {"description": "بياض إضافي", "quantity": "10", "rate": "60"})
        lump = costs.save_subcontract(self.project, self.owner, {"supplier": make_supplier(supplier_code="S-LUMP", name="مقاول نجارة"), "scope": "نجارة",
                                                                 "value": "10000", "retention_rate": "0"})
        self.post(costs.bill_subcontract(lump, self.owner, amount="8000", description="1"))
        with self.assertRaisesMessage(ValidationError, "أكبر من قيمة عقد مقاول الباطن (10000.00)"):
            costs.bill_subcontract(lump, self.owner, amount="2500", description="2")
        costs.save_subcontract(self.project, self.owner, {"supplier": lump.supplier, "scope": "نجارة", "value": "12000", "retention_rate": "0"}, lump)
        self.assertEqual(costs.bill_subcontract(lump, self.owner, amount="2500", description="2").gross, D("2500.00"))
        # A value of 0 was never typed: nothing to cap, as before.
        free = costs.save_subcontract(self.project, self.owner, {"supplier": lump.supplier, "scope": "دهانات", "value": "0", "retention_rate": "0"})
        self.assertEqual(costs.bill_subcontract(free, self.owner, amount="99999", description="x").gross, D("99999.00"))

    def test_bill_two_warns_when_it_passes_what_the_owner_was_certified_for(self):
        certificate = contract.create_certificate(self.project, self.owner, quantities={self.client_plaster.pk: "2000"})
        post_sales_invoice(certificate.invoice_id, self.owner)
        first = self.bill("1000")
        self.assertEqual(first.warnings, [])
        self.post(first)
        second = self.bill("2500")  # warned, never refused
        self.assertEqual(second.warnings, ["البند 1: الكمية حتى تاريخه (2500) أكتر من اللي اتعمل بيه مستخلص للمالك (2000)."])
        self.assertEqual(subcontracting.bill_warnings(second, "en"), ["Item 1: the quantity to date (2500) is more than the owner has been certified for (2000)."])
        self.assertEqual(second.gross, D("90000.00"))

    def test_a_purchase_return_of_half_of_bill_two_takes_back_its_shares_and_the_quantity(self):
        self.advance()
        self.post(self.bill("1000"))
        second = self.post(self.bill("2500"))
        returned = create_purchase_return("PR-SC", TODAY, second.invoice_id, [{"source_line": second.invoice.lines.get(), "quantity": D("750")}],
                                          "نص الكمية مرفوضة", self.owner)
        self.assertEqual(returned.total_amount, D("45000.00"))
        self.assertEqual(costs.return_share(second.retention_amount, second.invoice, returned), D("2250.00"))
        self.assertEqual(costs.return_share(second.recovery_amount, second.invoice, returned), D("4500.00"))
        self.assertEqual(subcontracting.billed_quantity(self.plaster), D("1750.000"))  # 750 m² given back
        self.assertEqual(subcontracting.effective_recovery(second), D("4500.00"))
        self.assertEqual(subcontracting.advance_left(self.subcontract), D("19500.00"))
        self.assertEqual(costs.subcontract_figures(self.subcontract)["retention_held"], D("5250.00"))
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.SUBCONTRACT], D("105000.00"))
        self.assertEqual((gl("payable"), gl("retention_payable"), gl("subcontractor_advances"), gl("project_cost")),
                         (D("-89250.00"), D("-5250.00"), D("19500.00"), D("105000.00")))
        self.assertEqual(supplier_balance(self.sub_supplier), D("75000.00"))  # = 89,250 + 5,250 − 19,500
        ledger_ok(self)

        # The 750 m² can be billed again, and that bill recovers from what the return gave back.
        third = self.bill("2500")
        self.assertEqual((third.gross, third.retention_amount, third.recovery_amount), (D("45000.00"), D("2250.00"), D("4500.00")))
        # The return cannot be cancelled now: the third bill already billed what it gave back.
        with self.assertRaisesMessage(ValidationError, "A later subcontractor bill already billed"):
            cancel_purchase_return(returned.pk, TODAY, "x", self.owner)
        costs.withdraw_bill(third, self.owner)
        cancel_purchase_return(returned.pk, TODAY, "رجعت", self.owner)
        self.assertEqual((gl("payable"), gl("retention_payable"), gl("subcontractor_advances")), (D("-127500.00"), D("-7500.00"), D("15000.00")))
        ledger_ok(self)

    def test_a_cancelled_bill_frees_its_quantity_and_its_recovery(self):
        self.advance()
        first = self.post(self.bill("1000"))
        cancel_posted_purchase_invoice(first.invoice_id, self.owner, "غلط")
        self.assertEqual(subcontracting.billed_quantity(self.plaster), D("0"))
        self.assertEqual(subcontracting.advance_left(self.subcontract), D("30000.00"))
        self.assertEqual((gl("payable"), gl("retention_payable"), gl("subcontractor_advances"), gl("project_cost")),
                         (D("0.00"), D("0.00"), D("30000.00"), D("0.00")))
        again = self.bill("1000")
        self.assertEqual((again.number, again.recovery_amount), (2, D("6000.00")))
        ledger_ok(self)

    def test_recovery_never_passes_the_advance_left(self):
        self.advance("8000")
        first = self.post(self.bill("1000"))  # 10% of 60,000 = 6,000
        second = self.post(self.bill("2000"))  # 6,000 wanted, 2,000 left
        self.assertEqual((first.recovery_amount, second.recovery_amount), (D("6000.00"), D("2000.00")))
        third = self.bill("3000")
        self.assertEqual(third.recovery_amount, D("0"))
        costs.withdraw_bill(third, self.owner)
        # A second advance is recovered from the next bill, never above 10% of it.
        self.advance("9000")
        self.assertEqual(self.bill("3000").recovery_amount, D("6000.00"))

    def test_a_lump_sum_bill_is_unchanged_and_lines_and_lump_sums_never_mix(self):
        old = costs.save_subcontract(self.project, self.owner, {"supplier": make_supplier(supplier_code="S-OLD", name="مقاول مباني"), "scope": "مباني",
                                                                "value": "60000", "retention_rate": "10"})
        bill = costs.bill_subcontract(old, self.owner, amount="20000", description="مستخلص 1 مباني")
        self.assertEqual((bill.gross, bill.retention_amount, bill.recovery_amount, costs.bill_net_payable(bill)),
                         (D("20000.00"), D("2000.00"), D("0"), D("18000.00")))
        self.assertEqual(bill.invoice.lines.get().quantity, D("1.000"))
        self.post(bill)
        self.assertEqual((gl("payable"), gl("retention_payable"), gl("subcontractor_advances")), (D("-18000.00"), D("-2000.00"), D("0.00")))
        with self.assertRaisesMessage(ValidationError, "مينفعش تضيف بنود بعده"):
            subcontracting.save_line(old, self.owner, {"description": "x", "quantity": "1", "rate": "1"})
        with self.assertRaisesMessage(ValidationError, "اعمل المستخلص بالكميات"):
            costs.bill_subcontract(self.subcontract, self.owner, amount="1000", description="x")
        ledger_ok(self)

    def test_a_lump_sum_bill_recovers_an_advance_too(self):
        lump = costs.save_subcontract(self.project, self.owner, {"supplier": make_supplier(supplier_code="S-L2", name="مقاول"), "scope": "x",
                                                                 "value": "50000", "retention_rate": "5", "advance_recovery_rate": "20"})
        subcontracting.pay(lump, self.owner, kind=SubcontractPaymentKind.ADVANCE, cashbox=self.cashbox, amount="5000")
        bill = self.post(costs.bill_subcontract(lump, self.owner, amount="10000", description="1"))
        self.assertEqual((bill.retention_amount, bill.recovery_amount, costs.bill_net_payable(bill)), (D("500.00"), D("2000.00"), D("7500.00")))
        self.assertEqual(gl("subcontractor_advances"), D("3000.00"))
        ledger_ok(self)


class AdvanceGuardTests(SubcontractSetup):
    def test_an_advance_a_bill_recovered_cannot_be_cancelled(self):
        payment = self.advance()
        first = self.bill("1000")
        with self.assertRaisesMessage(ValidationError, "Bills of this subcontract already recovered this advance"):
            cancel_supplier_payment(payment.pk, self.owner, "غلط")  # a draft's recovery counts too
        self.post(first)
        with self.assertRaisesMessage(ValidationError, "Bills of this subcontract already recovered this advance"):
            cancel_supplier_payment(payment.pk, self.owner, "غلط")
        payment.refresh_from_db()
        self.assertEqual(payment.status, "posted")
        self.assertEqual(gl("subcontractor_advances"), D("24000.00"))
        ledger_ok(self)

    def test_an_advance_nothing_recovered_is_cancelled_and_leaves_the_account(self):
        payment = self.advance()
        cancel_supplier_payment(payment.pk, self.owner, "اتسجلت غلط")
        self.assertEqual(SupplierPayment.objects.get(pk=payment.pk).status, "cancelled")
        self.assertEqual((gl("subcontractor_advances"), gl("payable"), gl("cash")), (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(self.bill("1000").recovery_amount, D("0"))
        ledger_ok(self)

    def test_a_payment_against_bills_is_an_ordinary_payment(self):
        self.post(self.bill("1000"))
        payment = subcontracting.pay(self.subcontract, self.owner, kind=SubcontractPaymentKind.PAYMENT, cashbox=self.cashbox, amount="57000")
        self.assertEqual((gl("payable"), gl("subcontractor_advances")), (D("0.00"), D("0.00")))
        cancel_supplier_payment(payment.pk, self.owner, "x")  # not an advance: the guard lets it through
        self.assertEqual(gl("payable"), D("-57000.00"))
        ledger_ok(self)

    def test_payments_stay_in_the_subcontracts_entity(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="E-SC", name_ar="فرع التشطيبات")
        box = make_cashbox(cashbox_code="SC-BOX", entity=branch)
        from hesba_testing.factories import make_location

        make_location(location_code="SC-LOC", entity=branch, is_receiving_location=True)
        from entities.current import working_in

        with working_in(branch):
            subcontracting.pay(self.subcontract, self.owner, kind=SubcontractPaymentKind.ADVANCE, cashbox=box, amount="30000")
            bill = self.bill("1000")  # the advance fixed the branch: the bill is received there too
        self.assertEqual(bill.invoice.receiving_location.entity_id, branch.pk)
        self.assertEqual(bill.recovery_amount, D("6000.00"))
        with self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            subcontracting.pay(self.subcontract, self.owner, kind=SubcontractPaymentKind.PAYMENT, cashbox=self.cashbox, amount="100")
        post_purchase_invoice(bill.invoice_id, self.owner)
        self.assertEqual(gl("subcontractor_advances"), D("24000.00"))
        ledger_ok(self)
