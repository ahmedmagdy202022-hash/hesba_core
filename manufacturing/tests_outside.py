"""Manufacturing F4 (HG-137): a stage of an order done at an outside supplier («تشغيل لدى الغير»).

The garments plant (the plan's example, to the piastre): an order of 100 shirts takes 120 m of fabric at 65.00
(7,800.00) and 20 spools of thread at 15.00 (300.00), so the start puts 8,100.00 into work in progress. Own labour
is 1,000.00. The stage list is the garments one (cutting, sewing, finishing, quality check, packing); the finishing
stage goes to an outside supplier at 3.00 a piece and comes back with 98 good and 2 defective.

The bill is 98 x 3.00 = 294.00; with VAT at 14%, 41.16 of VAT and 335.16 owed to the supplier. The finish absorbs
1,000.00 + 294.00 = 1,294.00, so the 98 shirts enter stock at 9,394.00 / 98 = 95.8571 each.
"""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_item, make_location, make_supplier, stock_in
from inventory.models import StockMovement, StockOperation
from inventory.services import get_item_authoritative_average_cost, get_item_location_stock_quantity, get_item_stock_value
from ledger import reports as gl
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from permissions.services import user_has_permission
from purchases.models import PurchaseInvoice, PurchaseLine, SupplierLedgerEntry
from purchases.services import cancel_posted_purchase_invoice, create_purchase_return, post_purchase_invoice
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled

from . import actuals, orders, outside, services
from .models import CostLineKind, OrderCostLine, OrderStatus, OutsideJob, OutsideStatus, RunStatus
from .tests import person
from .tests_f11 import BakeryChart
from .tests_quality import BANNED, LATIN_WORD, edges, limited, sentences

TODAY = timezone.localdate()


class GarmentsPlant(TestCase):
    ACTIVITY, SUB = "manufacturing", "garments"
    MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,manufacturing"
    VAT = True
    OUT_STAGE = "finishing"

    def setUp(self):
        self.profile = prepared_client(activity=self.ACTIVITY, sub_activity=self.SUB, modules=self.MODULES)
        set_capability_enabled(self.profile, "vat", self.VAT)
        self.owner = person(RoleCode.OWNER, "out_owner")
        self.plant = make_location(location_code="PLANT", name_ar="المصنع", name_en="Plant", is_default=True)
        self.cashbox = make_cashbox()
        self.fabric = make_item(item_code="RM-FABRIC", item_name="قماش (متر)", is_stock_tracked=True)
        self.thread = make_item(item_code="RM-THREAD", item_name="خيط (بكرة)", is_stock_tracked=True)
        self.shirt = make_item(item_code="FG-SHIRT", item_name="قميص", is_stock_tracked=True, default_sale_price="150")
        stock_in(self.fabric, self.plant, 300, "65.00", movement_date=TODAY)
        stock_in(self.thread, self.plant, 60, "15.00", movement_date=TODAY)
        self.recipe = services.save_recipe({"product": self.shirt, "output_quantity": "10"}, [(self.fabric, "12"), (self.thread, "2")], self.owner)
        self.supplier = make_supplier("SUP-EMB", name="مصنع النور للتطريز")

    # ---- the floor ----

    def open(self, quantity="100", labor="1000", overhead="0"):
        return orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": quantity, "labor_cost": labor,
                                    "overhead_cost": overhead}, self.owner)

    def at_finishing(self, **order):
        """An order on the floor whose first two stages (cutting and sewing) are done in the factory: its finishing stage is next."""

        order = self.open(**order)
        for _ in range(2):
            orders.advance(order, self.owner)
            order.refresh_from_db()
        self.assertEqual(order.stages[order.stage_index][0], self.OUT_STAGE)
        return order

    def sent(self, order=None, price="3.00", **extra):
        return outside.send(order or self.at_finishing(), self.owner, supplier=self.supplier, unit_price=price, **extra)

    def back(self, job=None, good="98", defect="2", **extra):
        job = job or self.sent()
        outside.receive(job, self.owner, good=good, defect=defect, **extra)
        job.refresh_from_db()
        return job

    def billed(self, job=None, post=True):
        job = job or self.back()
        invoice = outside.make_bill(job, self.owner)
        if post:
            post_purchase_invoice(invoice.pk, self.owner)
        job.refresh_from_db()
        return job

    def walk(self, order):
        """The stages left in the factory, then the finish."""

        order.refresh_from_db()
        while order.stage_index < len(order.stages):
            orders.advance(order, self.owner)
            order.refresh_from_db()
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        return order

    def finished(self):
        job = self.billed()
        return self.walk(job.order), OutsideJob.objects.get(pk=job.pk)

    def more_materials(self):
        """Enough fabric and thread for five more orders, at the same costs (so no average moves)."""

        stock_in(self.fabric, self.plant, 600, "65.00", movement_date=TODAY)
        stock_in(self.thread, self.plant, 100, "15.00", movement_date=TODAY)

    # ---- the books ----

    def books(self, *keys):
        Projector().rebuild()
        return {key: gl.control_balance(key) for key in keys}

    def journal(self, source_type, source_id):
        """{control or account code: debit minus credit} of the entries one document wrote (after a rebuild)."""

        Projector().rebuild()
        totals = {}
        for line in JournalLine.objects.filter(entry__source_type=source_type, entry__source_id=source_id).select_related("account"):
            key = line.account.control or line.account.code
            totals[key] = totals.get(key, D("0")) + line.debit - line.credit
        return {key: value for key, value in totals.items() if value}

    def rows(self):
        Projector().rebuild()
        return (StockMovement.objects.count(), StockOperation.objects.count(), JournalEntry.objects.count(), JournalLine.objects.count())

    def balanced(self):
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def owed(self):
        rows = SupplierLedgerEntry.objects.filter(supplier=self.supplier)
        return sum((row.due_increase - row.due_decrease for row in rows), D("0"))


# ---------------------------------------------------------------------------------------------------------------
# The plan's example, to the piastre
# ---------------------------------------------------------------------------------------------------------------


class GarmentsExampleTests(GarmentsPlant):
    def test_the_whole_example(self):
        order = self.at_finishing()
        self.assertEqual(self.books("wip", "raw_materials"), {"wip": D("8100.00"), "raw_materials": D("12300.00")})   # 7,800.00 + 300.00 issued out of 20,400.00
        job = self.sent(order)
        self.assertEqual((job.status, job.stage_key, job.quantity_sent, job.unit_price, job.sent_on), (OutsideStatus.SENT, "finishing", D("100.000"), D("3.00"), TODAY))
        job = self.back(job)
        self.assertEqual((job.status, job.returned_good, job.returned_defective, job.returned_on), (OutsideStatus.BACK, D("98.000"), D("2.000"), TODAY))
        invoice = outside.make_bill(job, self.owner)
        line = invoice.lines.get()
        self.assertEqual((invoice.status, invoice.supplier, line.item.item_code, line.item.is_stock_tracked, line.quantity, line.unit_purchase_price),
                         ("draft", self.supplier, "MFG-OUT", False, D("98.000"), D("3.00")))
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.remaining_due), (D("294.00"), D("41.16"), D("335.16"), D("335.16")))
        post_purchase_invoice(invoice.pk, self.owner)
        self.assertEqual(self.journal("purchase_invoice", invoice.pk), {"general_expense": D("294.00"), "vat_in": D("41.16"), "payable": D("-335.16")})
        self.assertEqual(self.owed(), D("335.16"))
        order = self.walk(order)
        run = order.run
        self.assertEqual((run.output_quantity, run.scrap_quantity, run.total_cost, run.conversion_cost), (D("98.000"), D("2.000"), D("8100.00"), D("1294.00")))
        self.assertEqual(run.output_operation.unit_cost, D("95.8571"))
        self.assertEqual(self.journal("stock_operation", run.output_operation_id), {"finished_goods": D("9394.00"), "wip": D("-8100.00"), "production_cost": D("-1294.00")})
        self.assertEqual((get_item_authoritative_average_cost(self.shirt), get_item_stock_value(self.shirt).quantize(D("0.01"))), (D("95.8571"), D("9394.00")))
        self.assertEqual(self.books("finished_goods", "wip", "production_cost", "general_expense", "vat_in", "payable"),
                         {"finished_goods": D("9394.00"), "wip": D("0.00"), "production_cost": D("-1294.00"), "general_expense": D("294.00"),
                          "vat_in": D("41.16"), "payable": D("-335.16")})
        self.balanced()

    def test_the_supplier_ledger_and_the_purchase_report_show_the_bill_as_an_ordinary_purchase(self):
        from reports.selectors import purchase_report, supplier_report

        job = self.billed()
        invoice = job.purchase_invoice
        entry = SupplierLedgerEntry.objects.get(supplier=self.supplier)
        self.assertEqual((entry.entry_type, entry.purchase_invoice, entry.due_increase, entry.due_decrease), ("purchase_due", invoice, D("335.16"), D("0.00")))
        self.assertEqual(supplier_report(self.supplier)[0]["balance"], D("335.16"))
        self.assertEqual([(row.invoice_number, row.status, row.total_amount) for row in purchase_report(status="posted")], [(invoice.invoice_number, "posted", D("335.16"))])
        self.assertEqual(invoice.invoice_number, f"OUT-{job.order.number.removeprefix('PO-')}-01")
        self.assertIn(job.order.number, invoice.notes)
        self.assertEqual(invoice.receiving_location, self.plant)                    # the order's entity; a service line moves no stock
        self.assertEqual(get_item_location_stock_quantity(invoice.lines.get().item, self.plant), D("0"))

    def test_sending_receiving_and_the_draft_bill_move_no_stock_and_write_no_journal_row(self):
        order = self.at_finishing()
        figures = lambda: (self.rows(), get_item_stock_value(self.fabric), get_item_authoritative_average_cost(self.fabric),
                           get_item_stock_value(self.thread), get_item_stock_value(self.shirt), self.books("wip", "raw_materials", "general_expense", "payable"))
        before = figures()
        self.assertEqual(before[0][:2], (4, 2))                                     # two stock-ins, two issues: nothing else has moved
        job = self.sent(order)
        self.assertEqual(figures(), before)                                         # sending: a record only
        job = self.back(job)
        self.assertEqual(figures(), before)                                         # the return closes the stage and moves nothing
        order.refresh_from_db()
        self.assertEqual((order.stage_index, order.stages[order.stage_index][0]), (3, "quality"))
        log = order.stage_logs.order_by("-pk").first()
        self.assertEqual((log.stage_key, log.good_quantity, log.defect_quantity), ("finishing", D("98.000"), D("2.000")))
        invoice = outside.make_bill(job, self.owner)
        self.assertEqual(figures(), before)                                         # a draft bill is not in the books
        self.assertEqual(self.owed(), D("0"))
        outside.withdraw_bill(OutsideJob.objects.get(pk=job.pk), self.owner)
        self.assertEqual(figures(), before)
        self.assertFalse(PurchaseInvoice.objects.filter(pk=invoice.pk).exists())
        # Posting the bill is an ordinary purchase of a service: one journal entry, still no stock movement or operation.
        job = self.billed(OutsideJob.objects.get(pk=job.pk))
        after = self.rows()
        self.assertEqual((after[0], after[1], after[2]), (before[0][0], before[0][1], before[0][2] + 1))

    def test_a_finish_with_the_bill_still_draft_is_refused_and_nothing_changes(self):
        job = self.billed(post=False)
        order = job.order
        while order.stage_index < len(order.stages):
            orders.advance(order, self.owner)
            order.refresh_from_db()
        before = self.rows()
        number = job.purchase_invoice.invoice_number
        message = f"فاتورة التشغيل {number} من «مصنع النور للتطريز» لسه مترحّلتش؛ رحّلها من شاشة المشتريات وبعدين أنهِ الأمر."
        self.assertEqual(outside.blockers(order), [message])
        with self.assertRaisesMessage(ValidationError, message):
            orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, order.run_id, OrderCostLine.objects.count(), self.rows()), (OrderStatus.IN_PROGRESS, None, 0, before))
        with self.assertRaisesMessage(ValidationError, "is not posted yet"):
            orders.finish(order, self.owner, "en")
        post_purchase_invoice(job.purchase_invoice_id, self.owner)
        self.assertEqual(outside.blockers(order), [])
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, order.run.output_operation.unit_cost), (OrderStatus.DONE, D("95.8571")))

    def test_a_finish_with_no_bill_at_all_is_refused(self):
        job = self.back()
        order = job.order
        while order.stage_index < len(order.stages):
            orders.advance(order, self.owner)
            order.refresh_from_db()
        message = "مرحلة «تشطيب وكي» رجعت من «مصنع النور للتطريز» ولسه ملهاش فاتورة تشغيل؛ اعمل الفاتورة ورحّلها وبعدين أنهِ الأمر."
        with self.assertRaisesMessage(ValidationError, message):
            orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, order.run_id), (OrderStatus.IN_PROGRESS, None))

    def test_a_cancelled_bill_puts_the_job_back_and_a_new_bill_can_be_made(self):
        job = self.billed()
        first = job.purchase_invoice
        cancel_posted_purchase_invoice(first.pk, self.owner, "رقم الفاتورة غلط")
        job = OutsideJob.objects.get(pk=job.pk)
        self.assertEqual((job.status, outside.state(job), outside.bill_state(job), self.owed()), (OutsideStatus.BILLED, OutsideStatus.BACK, "", D("0.00")))
        order = job.order
        while order.stage_index < len(order.stages):
            orders.advance(order, self.owner)
            order.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "ولسه ملهاش فاتورة تشغيل"):
            orders.finish(order, self.owner)
        with self.assertRaisesMessage(ValidationError, "مفيش فاتورة مسودة على المرحلة دي."):
            outside.withdraw_bill(job, self.owner)                                  # a cancelled bill is not a draft to withdraw
        second = outside.make_bill(job, self.owner)
        self.assertEqual((second.invoice_number, second.total_amount), (f"OUT-{order.number.removeprefix('PO-')}-02", D("335.16")))
        job.refresh_from_db()
        self.assertEqual((job.purchase_invoice, outside.state(job), outside.bill_state(job)), (second, OutsideStatus.BILLED, "draft"))
        post_purchase_invoice(second.pk, self.owner)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.run.conversion_cost, order.run.output_operation.unit_cost), (D("1294.00"), D("95.8571")))
        self.assertEqual(self.owed(), D("335.16"))                                  # the first bill's debt came and went
        self.assertEqual(self.books("general_expense", "payable", "vat_in"), {"general_expense": D("294.00"), "payable": D("-335.16"), "vat_in": D("41.16")})
        self.assertEqual(PurchaseInvoice.objects.get(pk=first.pk).status, "cancelled")  # it stays in purchases as a cancelled document
        self.balanced()

    def test_the_processing_bill_is_not_linked_to_a_project_as_well(self):
        """One bill counts in one place: the fee joins the product's cost at the finish, so a project does not take the
        same invoice (found on the maintenance bills, F10, and true of this bill since F4)."""

        from hesba_testing.factories import make_customer
        from projects import costs as project_costs, services as project_services

        invoice = self.billed().purchase_invoice
        self.assertEqual(invoice.status, "posted")
        project = project_services.save_project({"name": "توسعة المصنع", "customer": make_customer(customer_code="CUS-PRJ", name="مصنع النور")}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مربوطة بأمر صيانة أو بأمر إنتاج"):
            project_costs.link_purchase(project, invoice, self.owner)
        self.assertFalse(project.purchases.exists())

    def test_a_draft_bill_is_withdrawn_and_a_posted_one_is_not(self):
        job = self.billed(post=False)
        draft = job.purchase_invoice
        outside.withdraw_bill(job, self.owner)
        job.refresh_from_db()
        self.assertEqual((job.status, job.purchase_invoice, PurchaseInvoice.objects.count(), PurchaseLine.objects.count()), (OutsideStatus.BACK, None, 0, 0))
        again = outside.make_bill(job, self.owner)
        self.assertEqual(again.invoice_number, draft.invoice_number)                # the number is free again
        post_purchase_invoice(again.pk, self.owner)
        job.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "الفاتورة اترحّلت؛ لو محتاج تلغيها، الإلغاء من شاشة المشتريات."):
            outside.withdraw_bill(job, self.owner)
        with self.assertRaisesMessage(ValidationError, "المرحلة دي ليها فاتورة تشغيل قبل كده."):
            outside.make_bill(job, self.owner)
        self.assertEqual((PurchaseInvoice.objects.count(), self.owed()), (1, D("335.16")))

    def test_a_purchase_return_before_the_finish_lowers_the_fee_that_joins_the_cost(self):
        job = self.billed()
        invoice = job.purchase_invoice
        # The supplier credits 8 of the 98 pieces: 8 x 3.00 = 24.00 and 3.36 of VAT, 27.36 off what he is owed.
        returned = create_purchase_return("PR-OUT-1", TODAY, invoice.pk, [{"source_line": invoice.lines.get(), "quantity": D("8")}], "خصم على 8 قطع", self.owner)
        self.assertEqual((returned.total_amount, outside.bill_cost(invoice), self.owed()), (D("27.36"), D("270.00"), D("307.80")))
        order = self.walk(job.order)
        run = order.run
        self.assertEqual((run.conversion_cost, run.output_operation.unit_cost), (D("1270.00"), D("95.6122")))   # 9,370.00 / 98
        self.assertEqual(self.journal("stock_operation", run.output_operation_id), {"finished_goods": D("9370.00"), "wip": D("-8100.00"), "production_cost": D("-1270.00")})
        self.assertEqual(self.books("general_expense", "production_cost", "vat_in", "payable"),
                         {"general_expense": D("270.00"), "production_cost": D("-1270.00"), "vat_in": D("37.80"), "payable": D("-307.80")})
        line = OrderCostLine.objects.get()
        self.assertEqual((line.kind, line.amount, line.order, OutsideJob.objects.get(pk=job.pk).cost_line), (CostLineKind.OUTSIDE, D("270.00"), order, line))
        self.balanced()

    def test_the_fee_is_absorbed_once_as_an_outside_cost_line(self):
        order, job = self.finished()
        line = OrderCostLine.objects.get()
        self.assertEqual((line.kind, line.amount, line.created_by, job.cost_line), (CostLineKind.OUTSIDE, D("294.00"), self.owner, line))
        self.assertIn("مصنع النور للتطريز", line.description)
        self.assertIn(job.purchase_invoice.invoice_number, line.description)
        self.assertEqual(actuals.conversion(order), {"labour": D("1000.00"), "overhead": D("0.00"), "outside": D("294.00"), "total": D("1294.00"),
                                                     "labour_actual": False, "overhead_actual": False})
        self.assertEqual(outside.absorb(order, self.owner), [])                    # asked again: nothing more to write
        self.assertEqual((OrderCostLine.objects.count(), outside.drift(job)), (1, D("0.00")))
        with self.assertRaisesMessage(ValidationError, "التكاليف الفعلية بتتسجل وتتلغي لحد ما الأمر يخلص بس."):
            actuals.cancel_cost_line(line, self.owner)

    def test_the_cost_card_shows_the_expected_fee_until_the_finish_and_the_absorbed_one_after(self):
        order = self.at_finishing()
        self.assertEqual((orders.cost_card(order)["outside"], orders.cost_card(order)["total"]), (D("0.00"), D("9100.00")))
        job = self.sent(order)
        card = orders.cost_card(order)
        self.assertEqual((card["outside"], card["total"], card["per_unit"]), (D("300.00"), D("9400.00"), D("94.0000")))   # 100 sent x 3.00
        job = self.back(job)
        order.refresh_from_db()
        card = orders.cost_card(order)
        self.assertEqual((card["outside"], card["total"], card["output"], card["per_unit"]), (D("294.00"), D("9394.00"), D("98.000"), D("95.8571")))
        job = self.billed(job)
        self.assertEqual(orders.cost_card(order)["outside"], D("294.00"))
        order = self.walk(order)
        card = orders.cost_card(order)
        self.assertEqual((card["outside"], card["labor"], card["total"], card["per_unit"], card["stage"]), (D("294.00"), D("1000.00"), D("9394.00"), D("95.8571"), "posted"))

    def test_a_bill_cancelled_or_returned_after_the_finish_does_not_change_the_product_cost(self):
        order, job = self.finished()
        invoice = job.purchase_invoice
        create_purchase_return("PR-OUT-2", TODAY, invoice.pk, [{"source_line": invoice.lines.get(), "quantity": D("8")}], "خصم بعد الإنهاء", self.owner)
        job = OutsideJob.objects.get(pk=job.pk)
        self.assertEqual((outside.drift(job), outside.blockers(order)), (D("-24.00"), []))
        self.assertEqual((get_item_authoritative_average_cost(self.shirt), OrderCostLine.objects.get().amount), (D("95.8571"), D("294.00")))
        self.assertEqual(self.books("finished_goods", "general_expense", "production_cost"),
                         {"finished_goods": D("9394.00"), "general_expense": D("270.00"), "production_cost": D("-1294.00")})
        self.balanced()

    def test_cancelling_the_run_takes_the_goods_out_and_leaves_the_bill_an_ordinary_expense(self):
        order, job = self.finished()
        services.cancel_run(order.run, self.owner, reason="تشغيلة غلط")
        order.refresh_from_db()
        self.assertEqual(order.run.status, RunStatus.CANCELLED)
        self.assertEqual(self.books("finished_goods", "wip", "raw_materials", "production_cost", "general_expense", "payable"),
                         {"finished_goods": D("0.00"), "wip": D("0.00"), "raw_materials": D("20400.00"), "production_cost": D("0.00"),
                          "general_expense": D("294.00"), "payable": D("-335.16")})
        self.balanced()


class AuditTests(GarmentsPlant):
    def test_every_step_leaves_an_audit_row_on_the_job(self):
        from audit.models import AuditLog

        order = self.at_finishing()
        job = outside.send(order, self.owner, supplier=self.supplier)
        outside.cancel_sending(job, self.owner)
        job = outside.send(order, self.owner, supplier=self.supplier)
        outside.set_price(job, self.owner, "3.00")
        job = self.back(job)
        outside.make_bill(job, self.owner)
        outside.withdraw_bill(job, self.owner)
        job = self.billed(job)
        self.walk(order)
        rows = AuditLog.objects.filter(module="manufacturing", object_type="manufacturing.OutsideJob").order_by("pk")
        self.assertEqual([row.action for row in rows], ["send_stage_out", "cancel_stage_out", "send_stage_out", "price_stage_out", "receive_stage_back",
                                                        "bill_stage_out", "withdraw_stage_bill", "bill_stage_out", "absorb_stage_bill"])
        self.assertTrue(all(row.actor == self.owner for row in rows))
        priced, absorbed = rows[3], rows[8]
        self.assertEqual((priced.before_data, priced.after_data), ({"unit_price": "0.00"}, {"unit_price": "3.00"}))
        self.assertEqual((absorbed.object_id, absorbed.after_data["amount"], absorbed.after_data["order"]), (str(job.pk), "294.00", order.number))


class NoVatTests(GarmentsPlant):
    VAT = False

    def test_without_vat_the_bill_is_294_and_the_cost_is_the_same(self):
        order, job = self.finished()
        invoice = job.purchase_invoice
        self.assertEqual((invoice.tax_amount, invoice.total_amount, self.owed()), (D("0.00"), D("294.00"), D("294.00")))
        self.assertEqual(self.journal("purchase_invoice", invoice.pk), {"general_expense": D("294.00"), "payable": D("-294.00")})
        self.assertEqual((order.run.conversion_cost, order.run.output_operation.unit_cost), (D("1294.00"), D("95.8571")))
        self.balanced()


class BakeryOutsideTests(BakeryChart, GarmentsPlant):
    """Plan trap 18: on a restaurant bakery's chart work in progress and absorbed production cost fall back to the cost of sales."""

    MODULES = BakeryChart.MODULES + ",purchases,suppliers"
    OUT_STAGE = "quality"                                                           # the default list: preparation, making, quality check, packing

    def test_the_same_example_on_the_restaurant_chart(self):
        order, job = self.finished()
        run = order.run
        self.assertEqual((run.conversion_cost, run.output_operation.unit_cost), (D("1294.00"), D("95.8571")))
        self.assertEqual(self.journal("purchase_invoice", job.purchase_invoice_id), {"general_expense": D("294.00"), "vat_in": D("41.16"), "payable": D("-335.16")})
        self.assertEqual(self.journal("stock_operation", run.output_operation_id), {"inventory": D("9394.00"), "cogs": D("-9394.00")})
        Projector().rebuild()
        self.controls(finished=D("9394.00"), raw=D("12300.00"), wip=D("0.00"), absorbed=D("1294.00"), cogs=D("0.00"))
        self.assertTrue(gl.trial_balance()["balanced"])


# ---------------------------------------------------------------------------------------------------------------
# The rules: what is refused, and what a stage that is out stops
# ---------------------------------------------------------------------------------------------------------------


class SendingRulesTests(GarmentsPlant):
    def refused(self, message, order, **data):
        fields = {"supplier": self.supplier, "unit_price": "3.00", **data}
        with self.assertRaisesMessage(ValidationError, message):
            outside.send(order, self.owner, **fields)
        self.assertFalse(OutsideJob.objects.filter(order=order).exclude(status=OutsideStatus.CANCELLED).exists())

    def test_the_first_stage_is_never_sent_and_a_closed_order_neither(self):
        order = self.open()
        self.refused("أول مرحلة بتتسجل في المصنع عشان الخامات تتصرف على الأمر؛ المراحل اللي بعدها هي اللي ممكن تتبعت لجهة تشغيل.", order)
        self.assertFalse(outside.can_send(order))
        orders.cancel(order, self.owner, "اتلغى")
        self.refused("الأمر ده مش في مرحلة ينفع تتبعت لجهة تشغيل.", order)
        done = self.at_finishing()
        done = self.walk(done)
        self.refused("الأمر ده مش في مرحلة ينفع تتبعت لجهة تشغيل.", done)

    def test_the_supplier_the_price_and_the_date_are_checked(self):
        order = self.at_finishing()
        self.assertTrue(outside.can_send(order))
        self.refused("اختار المورّد اللي هيشغّل المرحلة.", order, supplier=None)
        self.refused("اختار المورّد اللي هيشغّل المرحلة.", order, supplier=make_supplier("SUP-OFF", name="مورد متوقف", active=False))
        for price in ("0", "-3", "abc", "0.001"):
            with self.subTest(price=price):
                self.refused("سعر الوحدة المتفق عليه لازم يكون رقم أكبر من صفر.", order, unit_price=price)
        self.refused("التاريخ مينفعش يكون بعد النهارده.", order, sent_on=TODAY + timedelta(days=1))
        self.refused("التاريخ مش صحيح.", order, sent_on="18/11/2026")
        with self.assertRaisesMessage(ValidationError, "The agreed price per unit must be a number above zero."):
            outside.send(order, self.owner, supplier=self.supplier, unit_price="0", lang="en")
        job = outside.send(order, self.owner, supplier=self.supplier, unit_price="3.005", sent_on=(TODAY - timedelta(days=2)).isoformat(), note="  تطريز الصدر  ")
        self.assertEqual((job.unit_price, job.sent_on, job.note, job.created_by), (D("3.01"), TODAY - timedelta(days=2), "تطريز الصدر", self.owner))

    def test_the_whole_stage_goes_out_with_the_units_that_passed_the_stages_before(self):
        order = self.open()
        orders.advance(order, self.owner)
        orders.advance(order, self.owner, defect="4")                               # sewing spoils 4
        order.refresh_from_db()
        job = self.sent(order)
        self.assertEqual(job.quantity_sent, D("96.000"))
        with self.assertRaisesMessage(ValidationError, "الكمية المرسلة 96 بس؛ السليم والمعيب مع بعض مينفعش يزيدوا عنها."):
            outside.receive(job, self.owner, good="96", defect="1")
        outside.receive(job, self.owner, defect="1")                                # good left blank: the rest of what was sent
        job.refresh_from_db()
        self.assertEqual((job.returned_good, job.returned_defective, orders.good_units(order)), (D("95.000"), D("1.000"), D("95.000")))

    def test_a_stage_with_no_good_unit_left_has_nothing_to_send(self):
        order = self.open()
        orders.advance(order, self.owner)
        orders.advance(order, self.owner, good="0", defect="100")
        order.refresh_from_db()
        self.refused("مفيش وحدات سليمة داخلة المرحلة دي عشان تتبعت.", order)

    def test_a_stage_is_out_once_at_a_time_and_a_cancelled_sending_frees_it(self):
        job = self.sent()
        order = job.order
        with self.assertRaisesMessage(ValidationError, "المرحلة دي متسجلة عند جهة تشغيل قبل كده."):
            outside.send(order, self.owner, supplier=self.supplier, unit_price="3.00")
        self.assertFalse(outside.can_send(order))
        outside.cancel_sending(job, self.owner)
        job.refresh_from_db()
        self.assertEqual((job.status, outside.can_send(order), outside.current_job(order), outside.blockers(order)), (OutsideStatus.CANCELLED, True, None, []))
        with self.assertRaisesMessage(ValidationError, "إلغاء الإرسال ممكن قبل ما المرحلة ترجع بس."):
            outside.cancel_sending(job, self.owner)
        again = self.sent(order, price="3.50")                                      # the same stage can go out again
        self.assertEqual((again.stage_key, again.unit_price, OutsideJob.objects.filter(order=order).count()), ("finishing", D("3.50"), 2))
        self.back(again)
        with self.assertRaisesMessage(ValidationError, "إلغاء الإرسال ممكن قبل ما المرحلة ترجع بس."):
            outside.cancel_sending(again, self.owner)

    def test_a_cancelled_sending_lets_the_factory_close_the_stage_itself(self):
        job = self.sent()
        order = job.order
        outside.cancel_sending(job, self.owner)
        orders.advance(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.stage_index, 3)
        order = self.walk(order)                                                    # no outside fee: only the own labour is absorbed
        self.assertEqual((order.run.conversion_cost, OrderCostLine.objects.count()), (D("1000.00"), 0))

    def test_the_quality_stage_with_criteria_stays_in_the_factory(self):
        from . import quality

        quality.save_recipe_quality(self.recipe, self.owner, needs_release=False, rows=[{"name_ar": "المقاس", "name_en": "Size", "kind": "pass_fail"}])
        order = self.at_finishing()
        orders.advance(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.stages[order.stage_index][0], "quality")
        self.refused("مرحلة فحص الجودة ليها معايير بتتسجل في المصنع، فمينفعش تتبعت لجهة تشغيل.", order)
        self.assertFalse(outside.can_send(order))


class OutRulesTests(GarmentsPlant):
    def test_a_stage_that_is_out_is_not_closed_in_the_factory(self):
        from . import quality

        job = self.sent()
        order = job.order
        message = "المرحلة دي عند «مصنع النور للتطريز»؛ سجّل رجوعها من قسم «تشغيل لدى الغير» في صفحة الأمر."
        for close in (orders.advance, quality.advance):
            with self.subTest(close=close.__module__):
                with self.assertRaisesMessage(ValidationError, message):
                    close(order, self.owner)
        with self.assertRaisesMessage(ValidationError, "This stage is at “مصنع النور للتطريز”"):
            orders.advance(order, self.owner, lang="en")
        order.refresh_from_db()
        self.assertEqual((order.stage_index, order.stage_logs.count()), (2, 2))

    def test_an_order_is_not_cancelled_while_a_stage_is_out(self):
        job = self.sent()
        order = job.order
        with self.assertRaisesMessage(ValidationError, "الأمر ليه مرحلة عند «مصنع النور للتطريز»؛ سجّل رجوعها أو ألغِ الإرسال قبل ما تلغي الأمر."):
            orders.cancel(order, self.owner, "العميل لغى")
        order.refresh_from_db()
        self.assertEqual((order.status, get_item_location_stock_quantity(self.fabric, self.plant)), (OrderStatus.IN_PROGRESS, D("180")))
        self.back(job)
        orders.cancel(order, self.owner, "العميل لغى")                              # back in the factory: the materials come back as ever
        order.refresh_from_db()
        self.assertEqual((order.status, get_item_location_stock_quantity(self.fabric, self.plant)), (OrderStatus.CANCELLED, D("300")))
        # The supplier still did the work: his bill is an ordinary purchase, never absorbed.
        job = self.billed(OutsideJob.objects.get(pk=job.pk))
        self.assertEqual((self.owed(), OrderCostLine.objects.count()), (D("335.16"), 0))
        self.assertEqual(self.books("general_expense", "wip", "raw_materials"), {"general_expense": D("294.00"), "wip": D("0.00"), "raw_materials": D("20400.00")})
        self.balanced()

    def test_the_return_is_checked_like_a_stage(self):
        job = self.sent(sent_on=TODAY - timedelta(days=3))
        refusals = (({"good": "99", "defect": "2"}, "الكمية المرسلة 100 بس؛ السليم والمعيب مع بعض مينفعش يزيدوا عنها."),
                    ({"good": "-1"}, "الكمية السليمة والكمية المعيبة لازم يكونوا أرقام مش سالبة."),
                    ({"good": "98", "defect": "x"}, "الكمية السليمة والكمية المعيبة لازم يكونوا أرقام مش سالبة."),
                    ({"good": "98", "returned_on": TODAY - timedelta(days=4)}, "تاريخ الرجوع مينفعش يكون قبل تاريخ الإرسال."),
                    ({"good": "98", "returned_on": TODAY + timedelta(days=1)}, "التاريخ مينفعش يكون بعد النهارده."))
        for data, message in refusals:
            with self.subTest(data=data):
                with self.assertRaisesMessage(ValidationError, message):
                    outside.receive(job, self.owner, **data)
        job.refresh_from_db()
        self.assertEqual((job.status, job.order.stage_logs.count()), (OutsideStatus.SENT, 2))
        outside.receive(job, self.owner, good="90", defect="2", returned_on=TODAY - timedelta(days=1), note="8 لسه عند المورّد")   # 8 neither good nor defective
        job.refresh_from_db()
        log = job.order.stage_logs.order_by("-pk").first()
        self.assertEqual((job.returned_good, job.returned_defective, job.returned_on, log.good_quantity, log.note, orders.good_units(job.order)),
                         (D("90.000"), D("2.000"), TODAY - timedelta(days=1), D("90.000"), "8 لسه عند المورّد", D("90.000")))
        with self.assertRaisesMessage(ValidationError, "المرحلة دي مش عند جهة التشغيل دلوقتي."):
            outside.receive(job, self.owner, good="1")

    def test_the_bill_needs_the_stage_back_good_units_and_a_price(self):
        job = self.sent()
        with self.assertRaisesMessage(ValidationError, "فاتورة التشغيل بتتعمل بعد ما المرحلة ترجع من جهة التشغيل."):
            outside.make_bill(job, self.owner)
        job = self.back(job, good="0", defect="100")
        with self.assertRaisesMessage(ValidationError, "مرجعش من المرحلة دي وحدات سليمة، فمفيش فاتورة تشغيل عليها."):
            outside.make_bill(job, self.owner)
        self.assertEqual((outside.blockers(job.order), outside.expected(job.order), PurchaseInvoice.objects.count()), ([], D("0.00"), 0))

    def test_the_price_is_set_later_and_changes_only_until_the_bill(self):
        order = self.at_finishing()
        job = outside.send(order, self.owner, supplier=self.supplier)               # the price is not agreed yet
        self.assertEqual((job.unit_price, outside.expected(order)), (D("0.00"), D("0.00")))
        job = self.back(job)
        with self.assertRaisesMessage(ValidationError, "سعر الوحدة المتفق عليه لسه متحددش على المرحلة دي؛ حدّده من صفحة الأمر وبعدين اعمل فاتورة التشغيل."):
            outside.make_bill(job, self.owner)
        with self.assertRaisesMessage(ValidationError, "سعر الوحدة المتفق عليه لازم يكون رقم أكبر من صفر."):
            outside.set_price(job, self.owner, "0")
        outside.set_price(job, self.owner, "2.50")
        outside.set_price(job, self.owner, "3")
        job.refresh_from_db()
        self.assertEqual((job.unit_price, outside.expected(order)), (D("3.00"), D("294.00")))
        invoice = outside.make_bill(job, self.owner)
        self.assertEqual(invoice.total_amount, D("335.16"))
        with self.assertRaisesMessage(ValidationError, "سعر التشغيل بيتغيّر لحد ما فاتورة التشغيل تتعمل بس."):
            outside.set_price(job, self.owner, "4")
        job.refresh_from_db()
        self.assertEqual(job.unit_price, D("3.00"))

    def test_the_bill_sits_in_the_orders_entity_even_when_its_place_does_not_receive(self):
        self.plant.is_receiving_location = False
        self.plant.save(update_fields=["is_receiving_location"])
        store = make_location(location_code="STORE", name_ar="المخزن", is_default=False)
        job = self.billed()
        self.assertEqual((job.purchase_invoice.receiving_location, store.entity_id), (store, self.plant.entity_id))

    def test_a_routed_recipe_sends_its_own_step_out(self):
        from . import workcentres

        table = workcentres.save_work_centre({"code": "CUT", "name_ar": "ترابيزة القص", "name_en": "Cutting table", "kind": "manual", "hours_per_day": "8",
                                              "rate_per_hour": "60.00"}, self.owner)
        workcentres.save_routing(self.recipe, [
            {"name_ar": "قص", "name_en": "Cutting", "work_centre": table.pk, "minutes_per_batch": "30", "setup_minutes": "0"},
            {"name_ar": "تطريز", "name_en": "Embroidery", "work_centre": table.pk, "minutes_per_batch": "30", "setup_minutes": "0"},
            {"name_ar": "تقفيل", "name_en": "Assembly", "work_centre": table.pk, "minutes_per_batch": "30", "setup_minutes": "0"}], self.owner)
        order = self.open()
        orders.advance(order, self.owner, minutes="300")
        order.refresh_from_db()
        job = self.sent(order)
        self.assertEqual((job.stage_key, outside.stage_name(order, job.stage_key), outside.stage_name(order, job.stage_key, "en")), ("embroidery", "تطريز", "Embroidery"))
        job = self.billed(self.back(job))
        self.assertEqual([row["log"].stage_key for row in workcentres.suggestions(order)], ["cutting"])   # no minutes at the supplier: no suggested labour for it
        order = self.walk(order)
        self.assertEqual((order.run.conversion_cost, order.run.output_operation.unit_cost), (D("1294.00"), D("95.8571")))


# ---------------------------------------------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------------------------------------------


class ScreenTests(GarmentsPlant):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def page(self, order, lang="ar"):
        return self.client.get(reverse("manufacturing:order", args=[order.pk]), {"lang": lang})

    def act(self, order, action, lang="ar", **data):
        return self.client.post(reverse("manufacturing:outside_action", args=[order.pk]), {"action": action, "lang": lang, **data}, follow=True)

    def test_the_order_page_walks_the_stage_out_and_back_to_its_bill(self):
        order = self.at_finishing()
        page = self.page(order)
        self.assertContains(page, "data-send-out")
        self.assertContains(page, "ابعت المرحلة دي لجهة تشغيل")
        self.assertContains(page, "data-advance")                                   # the factory can still close the stage itself
        self.assertContains(page, f'<option value="{self.supplier.pk}">مصنع النور للتطريز</option>', html=True)
        self.assertContains(page, 'data-will-send>هتتبعت كل الوحدات السليمة الداخلة المرحلة، وعددها <bdi dir="ltr">100</bdi> وحدة</p>')
        self.assertNotContains(page, "data-outside-jobs")
        # Sent: the stage form gives way to the return form.
        sent = self.act(order, "send", supplier=self.supplier.pk, unit_price="3.00", sent_on=TODAY.isoformat(), note="تطريز الصدر")
        self.assertContains(sent, "المرحلة اتسجلت عند جهة التشغيل.")
        job = OutsideJob.objects.get()
        self.assertEqual((job.order, job.supplier, job.quantity_sent, job.unit_price, job.note), (order, self.supplier, D("100.000"), D("3.00"), "تطريز الصدر"))
        self.assertContains(sent, "data-receive-back")
        self.assertContains(sent, "المرحلة دي عند <strong>مصنع النور للتطريز</strong>")
        self.assertNotContains(sent, "data-advance")
        self.assertNotContains(sent, "data-send-out")
        self.assertContains(sent, 'data-outside-state="sent"')
        self.assertContains(sent, "data-cancel-sending")
        self.assertContains(sent, 'data-outside-expected><article><small>تكلفة التشغيل لدى الغير المتوقعة</small><strong>300.00</strong>')
        # The factory's own stage post is refused while the stage is out.
        blocked = self.client.post(reverse("manufacturing:order", args=[order.pk]), {"action": "advance"})
        self.assertContains(blocked, "المرحلة دي عند «مصنع النور للتطريز»؛ سجّل رجوعها من قسم «تشغيل لدى الغير» في صفحة الأمر.")
        # Back: 98 good, 2 defective.
        back = self.act(order, "receive", job=job.pk, good="98", defect="2", returned_on=TODAY.isoformat())
        self.assertContains(back, "رجوع المرحلة اتسجل.")
        self.assertContains(back, 'data-outside-state="back"')
        self.assertContains(back, "data-make-bill")
        self.assertContains(back, "data-advance")                                   # the next stage is the factory's
        self.assertContains(back, 'data-stage="finishing"')
        order.refresh_from_db()
        self.assertEqual(order.stage_index, 3)
        # The bill: a draft, to be posted from purchases.
        billed = self.act(order, "bill", job=job.pk, bill_date=TODAY.isoformat())
        invoice = PurchaseInvoice.objects.get()
        self.assertContains(billed, f"فاتورة التشغيل {invoice.invoice_number} اتعملت مسودة؛ رحّلها من شاشة المشتريات.")
        self.assertContains(billed, 'data-outside-state="draft"')
        self.assertContains(billed, "data-withdraw-bill")
        self.assertContains(billed, "data-outside-post-hint")
        self.assertContains(billed, f'href="{reverse("purchases:detail", args=[invoice.pk])}?lang=ar" data-outside-bill')
        self.assertEqual((invoice.status, invoice.total_amount), ("draft", D("335.16")))
        # The purchases screen posts it as any purchase.
        posted = self.client.post(reverse("purchases:post", args=[invoice.pk]), follow=True)
        self.assertEqual(posted.status_code, 200)
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, self.owed()), ("posted", D("335.16")))
        page = self.page(order)
        self.assertContains(page, 'data-outside-state="posted"')
        self.assertNotContains(page, "data-withdraw-bill")
        self.assertNotContains(page, "data-outside-post-hint")

    def test_the_finish_says_what_it_waits_for_and_then_shows_the_fee_in_the_cost(self):
        job = self.billed(post=False)
        order = job.order
        while order.stage_index < len(order.stages):
            orders.advance(order, self.owner)
            order.refresh_from_db()
        page = self.page(order)
        self.assertContains(page, "data-outside-blockers")
        self.assertContains(page, "قبل إنهاء الأمر")
        self.assertContains(page, f"فاتورة التشغيل {job.purchase_invoice.invoice_number} من «مصنع النور للتطريز» لسه مترحّلتش")
        refused = self.client.post(reverse("manufacturing:order", args=[order.pk]), {"action": "finish"})
        self.assertContains(refused, "data-order-error")
        self.assertContains(refused, "لسه مترحّلتش؛ رحّلها من شاشة المشتريات وبعدين أنهِ الأمر.")
        post_purchase_invoice(job.purchase_invoice_id, self.owner)
        self.assertNotContains(self.page(order), "data-outside-blockers")
        self.client.post(reverse("manufacturing:order", args=[order.pk]), {"action": "finish"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.run.output_operation.unit_cost), (OrderStatus.DONE, D("95.8571")))
        page = self.page(order)
        self.assertContains(page, "data-outside-absorbed")
        self.assertContains(page, "دخل في تكلفة المنتج")
        self.assertContains(page, 'data-cost-outside><small>تشغيل لدى الغير</small><strong>294.00</strong>')
        self.assertContains(page, 'data-cost-per-unit>95.8571<')
        self.assertNotContains(page, "data-outside-expected")
        self.assertNotContains(page, "data-outside-drift")
        cancel_posted_purchase_invoice(job.purchase_invoice_id, self.owner, "اتلغت")
        page = self.page(order)
        self.assertContains(page, "data-outside-drift")
        self.assertContains(page, "فاتورة التشغيل اتلغت أو اترجّع منها بعد إنهاء الأمر؛ تكلفة المنتج في المخزون فضلت زي ما هي.")

    def test_a_refusal_is_said_on_the_order_page_and_nothing_is_written(self):
        order = self.at_finishing()
        for data, message in (({"unit_price": "3.00"}, "اختار المورّد اللي هيشغّل المرحلة."),
                              ({"supplier": self.supplier.pk, "unit_price": "0"}, "سعر الوحدة المتفق عليه لازم يكون رقم أكبر من صفر."),
                              ({"supplier": self.supplier.pk, "unit_price": "3", "sent_on": "2999-01-01"}, "التاريخ مينفعش يكون بعد النهارده.")):
            with self.subTest(data=data):
                self.assertContains(self.act(order, "send", **data), message)
        self.assertEqual(OutsideJob.objects.count(), 0)
        self.assertContains(self.act(order, "send", lang="en", unit_price="3.00"), "Choose the supplier who will process the stage.")
        job = self.sent(order)
        self.assertContains(self.act(order, "receive", job=job.pk, good="99", defect="2"), "الكمية المرسلة 100 بس؛ السليم والمعيب مع بعض مينفعش يزيدوا عنها.")
        self.assertContains(self.act(order, "bill", job=job.pk), "فاتورة التشغيل بتتعمل بعد ما المرحلة ترجع من جهة التشغيل.")
        self.assertEqual(self.client.post(reverse("manufacturing:outside_action", args=[order.pk]), {"action": "nothing"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("manufacturing:outside_action", args=[order.pk]), {"action": "receive", "job": "999999"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("manufacturing:outside_action", args=[order.pk])).status_code, 405)
        cancelled = self.act(order, "cancel_sending", job=job.pk)
        self.assertContains(cancelled, "الإرسال اتلغى، والمرحلة رجعت تتسجل في المصنع.")
        self.assertContains(cancelled, 'data-outside-state="cancelled"')
        self.assertContains(cancelled, "data-send-out")

    def test_the_price_is_set_on_the_order_page_and_the_bill_is_withdrawn_there(self):
        order = self.at_finishing()
        self.act(order, "send", supplier=self.supplier.pk)                          # no price yet
        job = self.back(OutsideJob.objects.get())
        page = self.page(order)
        self.assertContains(page, "data-needs-price")
        self.assertContains(page, "لسه متحددش")
        self.assertNotContains(page, "data-make-bill")
        self.assertContains(page, f'data-set-price="{job.pk}" open')
        priced = self.act(order, "price", job=job.pk, unit_price="3.00")
        self.assertContains(priced, "سعر التشغيل اتحفظ.")
        self.assertContains(priced, "data-make-bill")
        self.assertNotContains(priced, "data-needs-price")
        self.assertContains(priced, 'data-outside-amount><bdi dir="ltr">294.00</bdi></span>')
        self.assertContains(priced, 'data-outside-price>سعر الوحدة <bdi dir="ltr">3.00</bdi></small>')
        self.assertContains(priced, 'name="unit_price" inputmode="decimal" dir="ltr" value="3" required')
        self.act(order, "bill", job=job.pk)
        withdrawn = self.act(order, "withdraw", job=job.pk)
        self.assertContains(withdrawn, "الفاتورة المسودة اتسحبت، وتقدر تعمل فاتورة جديدة.")
        self.assertContains(withdrawn, 'data-outside-state="back"')
        self.assertEqual(PurchaseInvoice.objects.count(), 0)

    def test_the_board_card_says_where_the_units_are(self):
        job = self.sent()
        board = self.client.get(reverse("manufacturing:orders"))
        self.assertContains(board, '<small data-at-supplier>عند مصنع النور للتطريز: <bdi dir="ltr">100</bdi></small>')
        self.assertContains(self.client.get(reverse("manufacturing:orders"), {"lang": "en"}), '<small data-at-supplier>At مصنع النور للتطريز: <bdi dir="ltr">100</bdi></small>')
        self.back(job)
        self.assertNotContains(self.client.get(reverse("manufacturing:orders")), "data-at-supplier")

    def test_the_screen_lists_what_is_open_and_what_closed_in_both_languages(self):
        empty = self.client.get(reverse("manufacturing:outside"))
        self.assertContains(empty, "مفيش مراحل مفتوحة عند جهات تشغيل دلوقتي.")
        self.assertContains(empty, "لسه مفيش تشغيل لدى الغير اتقفل.")
        self.more_materials()
        out = self.sent()                                                           # at the supplier
        waiting = self.back()                                                       # back, waiting for a bill
        draft = self.billed(post=False)                                             # billed, not posted
        closed = self.billed()                                                      # billed and posted
        gone = self.sent()
        outside.cancel_sending(gone, self.owner)
        for lang, title, state in (("ar", "تشغيل لدى الغير", "عند جهة التشغيل"), ("en", "Outside processing", "At the supplier")):
            with self.subTest(lang=lang):
                screen = self.client.get(reverse("manufacturing:outside"), {"lang": lang})
                self.assertContains(screen, f"<h1>{title}</h1>")
                self.assertContains(screen, state)
                for key, count in (("sent", 1), ("back", 1), ("draft", 1)):
                    self.assertContains(screen, f"data-tile-{key}>{count}<")
                html = screen.content.decode()
                open_table, closed_table = html.split('data-outside-table="closed"')
                for job, key in ((out, "sent"), (waiting, "back"), (draft, "draft")):
                    self.assertIn(f'data-outside-job="{job.pk}" data-outside-state="{key}"', open_table)
                for job, key in ((closed, "posted"), (gone, "cancelled")):
                    self.assertIn(f'data-outside-job="{job.pk}" data-outside-state="{key}"', closed_table)
                self.assertContains(screen, f'href="{reverse("manufacturing:order", args=[out.order_id])}?lang={lang}"')
                self.assertContains(screen, f'href="{reverse("purchases:detail", args=[closed.purchase_invoice_id])}?lang={lang}" data-outside-bill')
                self.assertContains(screen, 'data-outside-amount><bdi dir="ltr">294.00</bdi></td>')
        # A bill cancelled from purchases puts its job back among the open ones.
        cancel_posted_purchase_invoice(closed.purchase_invoice_id, self.owner, "اتلغت")
        screen = self.client.get(reverse("manufacturing:outside"))
        self.assertContains(screen, f'data-outside-job="{closed.pk}" data-outside-state="back"')
        self.assertContains(screen, "data-tile-back>2<")
        self.assertContains(self.client.get(reverse("manufacturing:home")), "data-outside-nav")

    def test_the_english_order_page(self):
        job = self.sent()
        page = self.page(job.order, "en")
        self.assertContains(page, "This stage is at <strong>مصنع النور للتطريز</strong>")
        self.assertContains(page, "The stage is back from the supplier")
        self.assertContains(page, "Record the return")
        self.back(job)
        page = self.page(job.order, "en")
        self.assertContains(page, "Make the processing bill")
        self.assertContains(page, "Back, waiting for a bill")

    def test_the_pages_use_no_banned_word_and_no_arabic_line_starts_or_ends_with_a_latin_word(self):
        import pathlib

        from django.conf import settings

        from . import outside_views

        root = pathlib.Path(settings.BASE_DIR)
        files = ["manufacturing/outside.py", "manufacturing/outside_views.py"] + [f"templates/manufacturing/{name}" for name in (
            "outside.html", "_outside_table.html", "_outside_order.html", "_outside_send.html", "_outside_receive.html")]
        for name in files:
            text = (root / name).read_text(encoding="utf-8")
            for word in BANNED:
                self.assertNotIn(word, text, name)
        for label, words in (("screens", outside_views.WORDS["ar"]), ("rules", outside.MESSAGES["ar"])):
            for key, text in words.items():
                first, last = edges(text)
                with self.subTest(words=label, key=key):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)
                    self.assertFalse(first.startswith("{") or last.endswith("}"), text)   # a code or a number never opens or closes the line
        self.more_materials()
        out = self.sent()
        draft = self.billed(post=False)
        done, _job = self.finished()
        pages = [reverse("manufacturing:outside"), reverse("manufacturing:order", args=[self.at_finishing().pk]), reverse("manufacturing:order", args=[out.order_id]),
                 reverse("manufacturing:order", args=[draft.order_id]), reverse("manufacturing:order", args=[done.pk]), reverse("manufacturing:orders")]
        for url in pages:
            html = self.client.get(url).content.decode()
            for word in BANNED:
                self.assertNotIn(word, html, url)
            if url == reverse("manufacturing:orders"):
                continue                                                             # the board's own lines are older than this change
            for text in sentences(html):
                first, last = edges(text)
                with self.subTest(url=url, text=text[:50]):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)


# ---------------------------------------------------------------------------------------------------------------
# Permissions and entities
# ---------------------------------------------------------------------------------------------------------------


class PermissionTests(GarmentsPlant):
    MONEY = ("3.00", "294.00", "300.00", "335.16", "95.8571")

    def url(self, order):
        return reverse("manufacturing:outside_action", args=[order.pk])

    def test_the_rules_ask_for_the_right_permission_themselves(self):
        order = self.at_finishing()
        manager = person(RoleCode.MANAGER, "out_manager")                            # may adjust stock and make purchase invoices; may not see cost
        viewer = person(RoleCode.SITE_ENGINEER, "out_viewer")                        # sees stock, nothing else
        buyer = limited("out_buyer", "inventory.view_stock", "purchases.create_purchase_invoice")
        self.assertEqual([user_has_permission(manager, code) for code in (outside.PRODUCE, outside.COST, outside.BILL)], [True, False, True])
        with self.assertRaises(PermissionDenied):
            outside.send(order, viewer, supplier=self.supplier)
        with self.assertRaises(PermissionDenied):
            outside.send(order, manager, supplier=self.supplier, unit_price="3.00")   # a price is typed only by someone who may see cost
        job = outside.send(order, manager, supplier=self.supplier)                  # without a price the floor can send
        self.assertEqual(job.unit_price, D("0.00"))
        for user in (manager, viewer, buyer):
            with self.assertRaises(PermissionDenied):
                outside.set_price(job, user, "3.00")
        for call in (outside.receive, outside.cancel_sending):
            for user in (viewer, buyer):
                with self.assertRaises(PermissionDenied):
                    call(job, user)
        outside.receive(job, manager, good="98", defect="2")
        outside.set_price(job, self.owner, "3.00")
        with self.assertRaises(PermissionDenied):
            outside.make_bill(job, viewer)
        invoice = outside.make_bill(job, buyer)                                     # the purchase right alone makes the bill
        with self.assertRaises(PermissionDenied):
            outside.withdraw_bill(job, viewer)
        self.assertEqual((invoice.total_amount, invoice.created_by), (D("335.16"), buyer))

    def test_a_cashier_is_refused_and_a_viewer_reads_without_any_form(self):
        order = self.at_finishing()
        job = self.sent(order)
        self.client.force_login(person(RoleCode.CASHIER, "out_cashier"))
        self.assertEqual(self.client.get(reverse("manufacturing:outside")).status_code, 403)
        self.assertEqual(self.client.post(self.url(order), {"action": "receive", "job": job.pk, "good": "98"}).status_code, 403)
        self.client.force_login(person(RoleCode.SITE_ENGINEER, "out_reader"))
        screen = self.client.get(reverse("manufacturing:outside"))
        self.assertEqual(screen.status_code, 200)
        self.assertContains(screen, f'data-outside-job="{job.pk}"')
        page = self.client.get(reverse("manufacturing:order", args=[order.pk]))
        self.assertContains(page, 'data-outside-state="sent"')
        for marker in ("data-receive-back", "data-cancel-sending", "data-send-out", "data-set-price", "data-make-bill", "data-outside-price", "data-outside-amount"):
            self.assertNotContains(page, marker)
        for data in ({"action": "send", "supplier": self.supplier.pk}, {"action": "receive", "job": job.pk, "good": "98"},
                     {"action": "cancel_sending", "job": job.pk}, {"action": "price", "job": job.pk, "unit_price": "9"},
                     {"action": "bill", "job": job.pk}, {"action": "withdraw", "job": job.pk}):
            with self.subTest(action=data["action"]):
                self.assertEqual(self.client.post(self.url(order), data).status_code, 403)
        job.refresh_from_db()
        self.assertEqual((job.status, job.unit_price), (OutsideStatus.SENT, D("3.00")))

    def test_the_floor_sends_and_receives_without_seeing_or_typing_any_money(self):
        order = self.at_finishing()
        manager = person(RoleCode.MANAGER, "out_floor")
        self.client.force_login(manager)
        page = self.client.get(reverse("manufacturing:order", args=[order.pk]))
        self.assertContains(page, "data-send-out")
        self.assertNotContains(page, 'name="unit_price"')
        self.assertEqual(self.client.post(self.url(order), {"action": "send", "supplier": self.supplier.pk, "unit_price": "3.00"}).status_code, 403)
        self.client.post(self.url(order), {"action": "send", "supplier": self.supplier.pk})
        job = OutsideJob.objects.get()
        self.client.post(self.url(order), {"action": "receive", "job": job.pk, "good": "98", "defect": "2"})
        job.refresh_from_db()
        self.assertEqual((job.status, job.returned_good, job.unit_price), (OutsideStatus.BACK, D("98.000"), D("0.00")))
        self.assertEqual(self.client.post(self.url(order), {"action": "price", "job": job.pk, "unit_price": "3.00"}).status_code, 403)
        page = self.client.get(reverse("manufacturing:order", args=[order.pk]))
        self.assertContains(page, "data-needs-price")
        self.assertNotContains(page, "data-set-price")
        self.assertNotContains(page, "data-make-bill")
        refused = self.client.post(self.url(order), {"action": "bill", "job": job.pk}, follow=True)
        self.assertContains(refused, "سعر الوحدة المتفق عليه لسه متحددش على المرحلة دي")
        outside.set_price(job, self.owner, "3.00")                                  # the owner agrees the price
        billed = self.client.post(self.url(order), {"action": "bill", "job": job.pk}, follow=True)
        self.assertContains(billed, 'data-outside-state="draft"')
        self.assertEqual(PurchaseInvoice.objects.get().total_amount, D("335.16"))
        # No price, amount, expected fee or unit cost on the manufacturing screens for someone who may not see cost.
        for url in (reverse("manufacturing:order", args=[order.pk]), reverse("manufacturing:outside"), reverse("manufacturing:orders")):
            for lang in ("ar", "en"):
                text = self.client.get(url, {"lang": lang}).content.decode()
                for figure in self.MONEY:
                    self.assertNotIn(figure, text, url)
                for marker in ("data-outside-price", "data-outside-amount", "data-outside-expected", "data-cost-card"):
                    self.assertNotIn(marker, text, url)

    def test_the_bill_link_shows_only_to_someone_who_may_open_purchases(self):
        job = self.billed()
        keeper = limited("out_keeper", "inventory.view_stock", "inventory.adjust_stock")
        self.client.force_login(keeper)
        for url in (reverse("manufacturing:order", args=[job.order_id]), reverse("manufacturing:outside")):
            page = self.client.get(url)
            self.assertContains(page, job.purchase_invoice.invoice_number)          # the number is said
            self.assertNotContains(page, "data-outside-bill")                       # the way into purchases is not offered
            self.assertNotContains(page, "/purchases/")


class EntityTests(GarmentsPlant):
    def test_another_entitys_jobs_are_not_listed_and_return_404(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BRO", "name_ar": "فرع العبور", "activity_slug": "manufacturing", "sub_activity_slug": "garments"}, self.owner)
        branch_plant = make_location(location_code="BR-PLANT-O", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        stock_in(self.fabric, branch_plant, 300, "65.00", movement_date=TODAY)
        stock_in(self.thread, branch_plant, 60, "15.00", movement_date=TODAY)
        near = self.sent()
        far_order = orders.create_order({"recipe": self.recipe, "location": branch_plant, "quantity": "100", "labor_cost": "1000"}, self.owner)
        for _ in range(2):
            orders.advance(far_order, self.owner)
            far_order.refresh_from_db()
        far = self.back(self.sent(far_order))
        main = Entity.objects.get(is_main=True)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": main.pk, "next": "/dashboard/"})
        screen = self.client.get(reverse("manufacturing:outside"))
        self.assertContains(screen, f'data-outside-job="{near.pk}"')
        self.assertNotContains(screen, f'data-outside-job="{far.pk}"')
        self.assertNotContains(screen, far_order.number)
        with working_in(main):
            self.assertEqual(list(outside.scoped_jobs()), [near])
        far_url = reverse("manufacturing:outside_action", args=[far_order.pk])
        for data in ({"action": "bill", "job": far.pk}, {"action": "price", "job": far.pk, "unit_price": "9"}, {"action": "send", "supplier": self.supplier.pk}):
            with self.subTest(action=data["action"]):
                self.assertEqual(self.client.post(far_url, data).status_code, 404)
        # A job is reached only through its own order: another order's address does not open it.
        self.assertEqual(self.client.post(reverse("manufacturing:outside_action", args=[near.order_id]), {"action": "bill", "job": far.pk}).status_code, 404)
        far.refresh_from_db()
        self.assertEqual((far.status, far.unit_price, PurchaseInvoice.objects.count()), (OutsideStatus.BACK, D("3.00"), 0))
        # In the branch the two swap places, and the bill made there is the branch's own purchase.
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        screen = self.client.get(reverse("manufacturing:outside"))
        self.assertContains(screen, f'data-outside-job="{far.pk}"')
        self.assertNotContains(screen, f'data-outside-job="{near.pk}"')
        self.assertEqual(self.client.post(reverse("manufacturing:outside_action", args=[near.order_id]), {"action": "receive", "job": near.pk}).status_code, 404)
        self.client.post(far_url, {"action": "bill", "job": far.pk})
        invoice = PurchaseInvoice.objects.get()
        self.assertEqual((invoice.receiving_location, invoice.receiving_location.entity, invoice.total_amount), (branch_plant, branch, D("335.16")))
        self.assertEqual(self.client.get(reverse("purchases:detail", args=[invoice.pk])).status_code, 200)
        self.client.post(reverse("entities:switch"), {"entity": main.pk, "next": "/dashboard/"})
        self.assertEqual(self.client.get(reverse("purchases:detail", args=[invoice.pk])).status_code, 404)
