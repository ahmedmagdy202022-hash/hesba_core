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
        self.assertEqual(invoice.invoice_number, f"OUT-{job.order.number}-01")
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
        self.assertEqual((second.invoice_number, second.total_amount), (f"OUT-{order.number}-02", D("335.16")))
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
