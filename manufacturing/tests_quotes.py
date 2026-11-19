"""Manufacturing F7 (HG-138): a quotation line with specifications and a cost sheet, made into a custom production order.

The plan's printing example, to the piastre: 10 packs of 100 catalogues, A4, 16 pages, 150 g coated paper, 4 colours,
saddle-stitched. The cost sheet: paper 5 reams at 1,400.00 = 7,000.00; ink 3 kg at 450.00 = 1,350.00; labour 1,200.00;
overhead 600.00; outside binding 800.00. Cost 10,950.00; a 25% margin makes 13,687.50, so 1,368.75 a pack.

Made into a custom production order: the item ``JOB-SO-00001-1`` (the real order number has five digits; the plan wrote
SO-0007), a recipe per pack of 0.5 ream and 0.3 kg, and a production order of 10 packs with planned labour 1,200.00 and
overhead 600.00. The outside binding is a note on the order, not a cost: it joins the product's cost only when the stage
is sent out with F4 and its bill is posted. So the 10 packs enter stock at 7,000.00 + 1,350.00 + 1,200.00 + 600.00 =
10,150.00, 1,015.0000 a pack.
"""

import re
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxMovement, CashboxOperation
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, open_test_shift, stock_in
from inventory.models import StockMovement, StockOperation
from inventory.services import get_item_authoritative_average_cost, get_item_location_stock_quantity
from ledger import reports as gl
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from orders import services as sales
from orders.models import SalesOrder, Status
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from taxes.services import create_sales_draft_with_tax

from . import make_to_order, orders, quote_views, quotes, services
from .models import CostSheet, CostSheetLine, OrderStatus, ProductionOrder, Recipe
from .tests import person
from .tests_quality import BANNED, LATIN_WORD, edges, limited, sentences

TODAY = timezone.localdate()
NOT_SAID = BANNED + ("شغلانة",)
SPEC = {"size": "A4", "pages": "16", "paper": "كوشيه 150 جرام", "colours": "4 ألوان", "finishing": "تدبيس من النص", "copies": "100"}
MONEY = ("1,400.00", "7,000.00", "1,350.00", "1,200.00", "10,950.00", "13,687.50", "1,400.0000")


class PrintingPlant(TestCase):
    MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,manufacturing"

    def setUp(self):
        self.profile = prepared_client(activity="manufacturing", sub_activity="printing", modules=self.MODULES)
        set_capability_enabled(self.profile, "quotations", True)
        self.owner = person(RoleCode.OWNER, "quote_owner")
        self.plant = make_location(location_code="PRESS", name_ar="المطبعة", name_en="Press", is_default=True)
        self.paper = make_item(item_code="RM-PAPER", item_name="ورق كوشيه (رزمة)", is_stock_tracked=True)
        self.ink = make_item(item_code="RM-INK", item_name="حبر (كيلو)", is_stock_tracked=True)
        self.catalogue = make_item(item_code="PR-CATALOGUE", item_name="كتالوج مطبوع", is_stock_tracked=True, default_sale_price="0")
        stock_in(self.paper, self.plant, 20, "1400.00", movement_date=TODAY)
        stock_in(self.ink, self.plant, 10, "450.00", movement_date=TODAY)
        self.customer = make_customer(customer_code="CUS-EXPO", name="شركة المعارض", phone="01001234567")

    # ---- helpers ----

    def quote(self, quantity="10", item=None, description="كتالوج المعرض"):
        order = sales.create_quote({"customer": self.customer}, self.owner)
        sales.add_line(order, item or self.catalogue, self.owner, quantity=quantity, unit_price="0", description=description)
        return order

    def line(self, order):
        return order.lines.get()

    def sheet(self, order, *, margin="25", outside=True):
        line = self.line(order)
        quotes.save_spec(line, self.owner, "printing", SPEC)
        quotes.add_cost_line(line, self.owner, kind="material", item=self.paper, quantity="5")          # today's average: 1,400.0000
        quotes.add_cost_line(line, self.owner, kind="material", item=self.ink, quantity="3", unit_cost="450")
        quotes.add_cost_line(line, self.owner, kind="labour", description="تجهيز وتشغيل المكنة", quantity="1", unit_cost="1200")
        quotes.add_cost_line(line, self.owner, kind="overhead", description="كهربا وإهلاك", quantity="1", unit_cost="600")
        if outside:
            quotes.add_cost_line(line, self.owner, kind="outside", description="تجليد بالدبوس", quantity="1", unit_cost="800")
        quotes.set_price(line, self.owner, margin=margin)
        return CostSheet.objects.get(sales_order_line=line)

    def confirm(self, order):
        return sales.move(order, Status.CONFIRMED, self.owner)

    def job(self, quantity="10", description="كتالوج المعرض"):
        order = self.quote(quantity, description=description)
        self.sheet(order)
        order = self.confirm(order)
        return order, quotes.convert(self.line(order), self.owner)

    def walk(self, production):
        for _ in production.stages:
            orders.advance(production, self.owner)
        orders.finish(production, self.owner)
        production.refresh_from_db()
        return production

    def invoice(self, order, number):
        line = self.line(order)
        invoice = create_sales_draft_with_tax(
            {"invoice_number": number, "invoice_date": TODAY, "customer": order.customer, "selling_location": self.plant, "paid_now": D("0")},
            [{"item": line.item, "quantity": line.quantity, "unit_sale_price": line.unit_price, "line_discount_amount": D("0")}], self.owner)
        sales.link_invoice(order, invoice, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        return invoice

    def counts(self):
        return {model.__name__: model.objects.count() for model in (
            StockOperation, StockMovement, JournalEntry, JournalLine, CashboxMovement, CashboxOperation, SalesInvoice)}

    def url(self, line):
        return reverse("manufacturing:quote_sheet", args=[line.pk])


# ---------------------------------------------------------------------------------------------------------------
# The printing example, to the piastre
# ---------------------------------------------------------------------------------------------------------------


class ExampleTests(PrintingPlant):
    def test_the_sheet_costs_10950_and_25_percent_makes_1368_75_a_pack_on_the_line(self):
        order = self.quote()
        sheet = self.sheet(order)
        self.assertEqual(sheet.template, "printing")                         # the printing sub-activity picks the printing template
        self.assertEqual(list(sheet.lines.values_list("unit_cost", flat=True)), [D("1400.0000"), D("450.0000"), D("1200.0000"), D("600.0000"), D("800.0000")])
        numbers = quotes.figures(sheet, self.line(order).quantity)
        self.assertEqual((numbers["material"], numbers["labour"], numbers["overhead"], numbers["outside"]),
                         (D("8350.00"), D("1200.00"), D("600.00"), D("800.00")))
        self.assertEqual((numbers["cost"], numbers["margin"], numbers["price_total"], numbers["computed"], numbers["unit_price"]),
                         (D("10950.00"), D("2737.50"), D("13687.50"), D("1368.75"), D("1368.75")))
        line = self.line(order)
        self.assertEqual((line.unit_price, line.amount), (D("1368.75"), D("13687.50")))
        self.assertEqual(sales.totals(order)["total"], D("13687.50"))
        self.assertEqual(quotes.spec_rows(sheet), [("المقاس", "A4"), ("عدد الصفحات", "16"), ("نوع الورق ووزنه", "كوشيه 150 جرام"),
                                                   ("الألوان", "4 ألوان"), ("التشطيب والتجليد", "تدبيس من النص"), ("عدد النسخ في الوحدة", "100")])

    def test_the_price_can_be_overridden_and_is_fixed_once_the_customer_accepts(self):
        order = self.quote()
        self.sheet(order)
        line = self.line(order)
        quotes.set_price(line, self.owner, margin="25", override="1,350")
        line.refresh_from_db()
        self.assertEqual(line.unit_price, D("1350.00"))
        numbers = quotes.figures(CostSheet.objects.get(), line.quantity)
        self.assertEqual((numbers["computed"], numbers["unit_price"], numbers["overridden"]), (D("1368.75"), D("1350.00"), True))
        quotes.set_price(line, self.owner, margin="25", override="")
        line.refresh_from_db()
        self.assertEqual(line.unit_price, D("1368.75"))
        for margin, text in (("-1", "نسبة الربح"), ("1001", "نسبة الربح"), ("abc", "نسبة الربح")):
            with self.subTest(margin=margin), self.assertRaisesMessage(ValidationError, text):
                quotes.set_price(line, self.owner, margin=margin)
        self.confirm(order)
        with self.assertRaisesMessage(ValidationError, "بعد موافقة العميل السعر بيتثبت"):
            quotes.set_price(line, self.owner, margin="40")
        line.refresh_from_db()
        self.assertEqual(line.unit_price, D("1368.75"))

    def test_making_it_a_custom_production_order_makes_the_job_item_its_recipe_per_pack_and_the_order(self):
        order, production = self.job()
        self.assertEqual(order.order_number, "SO-00001")
        line = self.line(order)
        sheet = CostSheet.objects.get()
        item = line.item
        self.assertEqual((item.item_code, item.item_name, item.is_stock_tracked, item.default_sale_price, item.active),
                         ("JOB-SO-00001-1", "كتالوج المعرض", True, D("1368.75"), True))
        self.assertEqual((sheet.job_item, sheet.job_recipe.product), (item, item))
        recipe = sheet.job_recipe
        self.assertEqual(recipe.output_quantity, D("1.000"))                  # per pack
        self.assertEqual([(l.component, l.quantity) for l in recipe.lines.all()], [(self.paper, D("0.500")), (self.ink, D("0.300"))])
        self.assertEqual((production.quantity, production.status, production.recipe, production.location), (D("10.000"), OrderStatus.PLANNED, recipe, self.plant))
        self.assertEqual((production.labor_cost, production.overhead_cost), (D("1200.00"), D("600.00")))
        self.assertEqual((production.sales_order_line, production.customer), (line, self.customer))
        self.assertEqual(production.stages[0][0], "prepress")                 # the printing stage list
        self.assertIn("تجليد بالدبوس", production.notes)                      # the outside work, as a note
        self.assertNotIn("800", production.notes)                             # never an amount: the order page shows notes to everyone
        self.assertEqual(orders._batches(production), D("10.000"))
        self.assertEqual(make_to_order.uncovered(line), D("0"))

    def test_the_job_enters_stock_at_its_own_cost_and_the_invoice_takes_exactly_that(self):
        order, production = self.job()
        production = self.walk(production)
        job_item = self.line(order).item
        card = orders.cost_card(production)
        self.assertEqual((card["material"], card["total"], card["per_unit"]), (D("8350.00"), D("10150.00"), D("1015.0000")))
        self.assertEqual(get_item_location_stock_quantity(job_item, self.plant), D("10"))
        invoice = self.invoice(order, "SI-JOB-1")
        sold = invoice.lines.get()
        self.assertEqual((invoice.total_amount, sold.line_cost_amount, sold.line_profit_amount), (D("13687.50"), D("10150.00"), D("3537.50")))
        self.assertEqual(get_item_location_stock_quantity(job_item, self.plant), D("0"))
        self.assertEqual(sales.state(order), "done")


class TwoJobsTests(PrintingPlant):
    def test_each_job_sells_at_its_own_cost_beside_another_job_of_the_same_description(self):
        first_order, first = self.job()
        first = self.walk(first)                                              # yesterday's job: paper at 1,400.00
        stock_in(self.paper, self.plant, 15, "1600.00", movement_date=TODAY)  # 15 left at 1,400 + 15 at 1,600: average 1,500
        self.assertEqual(get_item_authoritative_average_cost(self.paper), D("1500.0000"))
        second_order, second = self.job()
        self.assertEqual(self.line(second_order).item.item_code, "JOB-SO-00002-1")
        self.assertEqual(self.line(second_order).item.item_name, self.line(first_order).item.item_name)   # the same description
        second = self.walk(second)
        self.assertEqual(orders.cost_card(first)["total"], D("10150.00"))
        self.assertEqual(orders.cost_card(second)["total"], D("10650.00"))     # 7,500 + 1,350 + 1,200 + 600
        late = self.invoice(second_order, "SI-JOB-2").lines.get()              # the newer job is invoiced first
        early = self.invoice(first_order, "SI-JOB-1").lines.get()
        self.assertEqual((early.line_cost_amount, late.line_cost_amount), (D("10150.00"), D("10650.00")))
        self.assertEqual(early.line_cost_amount, first.run.total_cost + first.run.conversion_cost)
        self.assertEqual(late.line_cost_amount, second.run.total_cost + second.run.conversion_cost)
        Projector().rebuild()
        self.assertEqual(gl.control_balance("cogs"), D("20800.00"))
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def test_a_quantity_that_does_not_divide_makes_one_batch_of_the_whole_job(self):
        order = self.quote("7")
        self.sheet(order)
        order = self.confirm(order)
        production = quotes.convert(self.line(order), self.owner)
        recipe = CostSheet.objects.get().job_recipe
        # 5 reams over 7 packs is 0.714 a pack, and 0.714 x 7 = 4.998: one batch of 7 issues exactly 5 reams and 3 kg.
        self.assertEqual(recipe.output_quantity, D("7.000"))
        self.assertEqual([(l.component, l.quantity) for l in recipe.lines.all()], [(self.paper, D("5.000")), (self.ink, D("3.000"))])
        orders.advance(production, self.owner)
        self.assertEqual(get_item_location_stock_quantity(self.paper, self.plant), D("15"))
        self.assertEqual(get_item_location_stock_quantity(self.ink, self.plant), D("7"))

    def test_a_few_units_made_again_after_a_defect_in_a_whole_job_recipe_can_start_and_finish(self):
        order = self.quote("3000")
        self.sheet(order)
        order = self.confirm(order)
        production = quotes.convert(self.line(order), self.owner)
        recipe = CostSheet.objects.get().job_recipe
        self.assertEqual(recipe.output_quantity, D("3000.000"))            # 5 reams over 3,000 does not divide: one batch of the whole job
        orders.advance(production, self.owner, defect="1")
        for _ in production.stages[1:]:
            orders.advance(production, self.owner)
        orders.finish(production, self.owner)
        line = self.line(order)
        self.assertEqual(make_to_order.uncovered(line), D("1.000"))
        again = make_to_order.make(line, self.owner, {})
        # 1 over 3,000 rounds to no batch at all; the order takes the smallest batch count and so can start.
        self.assertEqual(again.quantity, D("1.000"))
        self.assertEqual(orders._batches(again), D("0.001"))
        paper_before = get_item_location_stock_quantity(self.paper, self.plant)
        self.walk(again)
        self.assertEqual(paper_before - get_item_location_stock_quantity(self.paper, self.plant), D("0.005"))
        self.assertEqual(again.run.output_quantity, D("1.000"))
        self.assertEqual(make_to_order.uncovered(line), D("0.000"))
        self.assertEqual(get_item_location_stock_quantity(line.item, self.plant), D("3000"))

    def test_the_same_material_on_two_lines_is_one_recipe_line(self):
        order = self.quote()
        self.sheet(order)
        quotes.add_cost_line(self.line(order), self.owner, kind="material", item=self.paper, quantity="1", unit_cost="1400")
        order = self.confirm(order)
        quotes.convert(self.line(order), self.owner)
        recipe = CostSheet.objects.get().job_recipe
        self.assertEqual([(l.component, l.quantity) for l in recipe.lines.all()], [(self.paper, D("0.600")), (self.ink, D("0.300"))])


# ---------------------------------------------------------------------------------------------------------------
# Nothing posts, and the rules
# ---------------------------------------------------------------------------------------------------------------


class NothingPostsTests(PrintingPlant):
    def test_the_sheet_the_confirm_and_the_conversion_write_no_stock_cashbox_or_journal_row(self):
        Projector().rebuild()
        before = self.counts()
        order = self.quote()
        self.sheet(order)
        self.assertEqual(self.counts(), before)
        order = self.confirm(order)
        self.assertEqual(self.counts(), before)
        quotes.convert(self.line(order), self.owner)
        Projector().rebuild()
        self.assertEqual(self.counts(), before)
        self.assertTrue(gl.trial_balance()["balanced"])


class RuleTests(PrintingPlant):
    def test_a_quotation_not_accepted_yet_cannot_be_converted_and_a_line_is_converted_once(self):
        order = self.quote()
        self.sheet(order)
        with self.assertRaisesMessage(ValidationError, "لسه مش مؤكد"):
            quotes.convert(self.line(order), self.owner)
        order = self.confirm(order)
        quotes.convert(self.line(order), self.owner)
        with self.assertRaisesMessage(ValidationError, "اتحوّل لأمر إنتاج خاص قبل كده"):
            quotes.convert(self.line(order), self.owner)
        self.assertEqual((ProductionOrder.objects.count(), Recipe.objects.filter(product__item_code__startswith="JOB-").count()), (1, 1))

    def test_a_line_with_no_sheet_or_no_material_is_refused_and_nothing_is_made(self):
        bare = self.confirm(self.quote())
        with self.assertRaisesMessage(ValidationError, "ملوش بيان تكلفة"):
            quotes.convert(self.line(bare), self.owner)
        order = self.quote(item=make_item(item_code="PR-FLYER", item_name="فلاير", is_stock_tracked=True))
        quotes.add_cost_line(self.line(order), self.owner, kind="labour", description="تصميم", quantity="1", unit_cost="300")
        order = self.confirm(order)
        with self.assertRaisesMessage(ValidationError, "خامة واحدة على الأقل"):
            quotes.convert(self.line(order), self.owner)
        self.assertFalse(ProductionOrder.objects.exists())
        self.assertFalse(type(self.paper).objects.filter(item_code__startswith="JOB-").exists())

    def test_the_sheet_is_locked_once_converted(self):
        order, _production = self.job()
        line = self.line(order)
        for call in (lambda: quotes.save_spec(line, self.owner, "printing", {"size": "A3"}),
                     lambda: quotes.add_cost_line(line, self.owner, kind="labour", description="زيادة", quantity="1", unit_cost="5"),
                     lambda: quotes.remove_cost_line(CostSheetLine.objects.first(), self.owner)):
            with self.assertRaisesMessage(ValidationError, "اتحوّل لأمر إنتاج خاص"):
                call()
        self.assertEqual(CostSheet.objects.get().spec["size"], "A4")

    def test_a_cost_line_is_checked(self):
        line = self.line(self.quote())
        service = make_item(item_code="SRV-DESIGN", item_name="تصميم", is_stock_tracked=False)
        cases = ((dict(kind="material", item=None, quantity="1"), "اختار الخامة"), (dict(kind="material", item=service, quantity="1"), "اختار الخامة"),
                 (dict(kind="labour", description="", quantity="1", unit_cost="1"), "اكتب بيان السطر"),
                 (dict(kind="labour", description="قص", quantity="0", unit_cost="1"), "الكمية"), (dict(kind="labour", description="قص", quantity="1", unit_cost="-2"), "تكلفة الوحدة"),
                 (dict(kind="labour", description="قص", quantity="1", unit_cost=""), "تكلفة الوحدة"), (dict(kind="tea", quantity="1"), "اختار نوع السطر"))
        for data, text in cases:
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, text):
                quotes.add_cost_line(line, self.owner, **data)
        self.assertFalse(CostSheetLine.objects.exists())
        with self.assertRaisesMessage(ValidationError, "نوع المواصفات"):
            quotes.save_spec(line, self.owner, "plastics", {})

    def test_a_removed_quotation_line_takes_its_sheet_with_it(self):
        order = self.quote()
        self.sheet(order)
        sales.remove_line(self.line(order), self.owner)
        self.assertEqual((CostSheet.objects.count(), CostSheetLine.objects.count()), (0, 0))

    def test_the_plain_make_to_order_action_is_not_offered_on_a_line_with_a_sheet(self):
        # The placeholder item happens to have a recipe of its own: F1 would make the wrong item.
        services.save_recipe({"product": self.catalogue, "output_quantity": "1"}, [(self.paper, "1")], self.owner)
        order = self.quote()
        self.sheet(order)
        order = self.confirm(order)
        line = self.line(order)
        state = make_to_order.line_states(order, sales.quantities(order), "ar", can_make=True)[line.pk]
        self.assertEqual((state["can_make"], state["job_pending"]), (False, True))
        with self.assertRaisesMessage(ValidationError, "حوّله لأمر إنتاج خاص"):
            make_to_order.make(line, self.owner, {})
        self.assertFalse(ProductionOrder.objects.exists())
        production = quotes.convert(line, self.owner)
        self.assertEqual(production.recipe, CostSheet.objects.get().job_recipe)

    def test_a_line_already_in_production_or_invoiced_is_refused(self):
        services.save_recipe({"product": self.catalogue, "output_quantity": "1"}, [(self.paper, "1")], self.owner)
        order = self.confirm(self.quote())
        make_to_order.make(self.line(order), self.owner, {})                 # made the plain way before any sheet
        quotes.save_spec(self.line(order), self.owner, "printing", SPEC)
        quotes.add_cost_line(self.line(order), self.owner, kind="material", item=self.paper, quantity="5")
        with self.assertRaisesMessage(ValidationError, "ليه أمر إنتاج شغال بالفعل"):
            quotes.convert(self.line(order), self.owner)
        other = self.quote(item=make_item(item_code="PR-POSTER", item_name="بوستر", is_stock_tracked=True))
        sales.add_line(other, self.ink, self.owner, quantity="1", unit_price="500")
        quotes.add_cost_line(other.lines.get(item__item_code="PR-POSTER"), self.owner, kind="material", item=self.paper, quantity="1")
        other = self.confirm(other)
        poster = other.lines.get(item__item_code="PR-POSTER")
        draft = create_sales_draft_with_tax({"invoice_number": "SI-POSTER", "invoice_date": TODAY, "customer": self.customer, "selling_location": self.plant,
                                             "paid_now": D("0")}, [{"item": poster.item, "quantity": D("1"), "unit_sale_price": D("0")}], self.owner)
        sales.link_invoice(other, draft, self.owner)
        with self.assertRaisesMessage(ValidationError, "عليه فاتورة"):
            quotes.convert(poster, self.owner)
        poster.refresh_from_db()
        self.assertEqual(poster.item.item_code, "PR-POSTER")


    def test_specifications_written_on_a_line_already_made_the_plain_way_leave_its_remaining_units_makeable(self):
        services.save_recipe({"product": self.catalogue, "output_quantity": "1"}, [(self.paper, "1")], self.owner)
        order = self.confirm(self.quote())
        line = self.line(order)
        first = make_to_order.make(line, self.owner, {})                     # the plain way, before any sheet
        orders.advance(first, self.owner, defect="1")
        for _ in first.stages[1:]:
            orders.advance(first, self.owner)
        orders.finish(first, self.owner)
        quotes.save_spec(line, self.owner, "printing", SPEC)                  # an unconverted sheet on a line that cannot be converted any more
        self.assertEqual(quotes.pending([line.pk]), set())
        state = make_to_order.line_states(order, sales.quantities(order), "ar", can_make=True)[line.pk]
        self.assertEqual((state["can_make"], state["job_pending"], state["free"]), (True, False, D("1.000")))
        with self.assertRaisesMessage(ValidationError, "ليه أمر إنتاج شغال بالفعل"):
            quotes.convert(line, self.owner)
        again = make_to_order.make(line, self.owner, {})
        self.assertEqual((again.quantity, again.recipe.product), (D("1.000"), self.catalogue))
        self.walk(again)
        self.assertEqual(make_to_order.uncovered(line), D("0.000"))


    def test_specifications_written_on_a_line_partly_invoiced_from_stock_leave_its_remaining_units_makeable(self):
        services.save_recipe({"product": self.catalogue, "output_quantity": "1"}, [(self.paper, "1")], self.owner)
        stock_in(self.catalogue, self.plant, 9, "1500.00", movement_date=TODAY)
        order = self.confirm(self.quote())
        line = self.line(order)
        invoice = create_sales_draft_with_tax(
            {"invoice_number": "SI-PART", "invoice_date": TODAY, "customer": order.customer, "selling_location": self.plant, "paid_now": D("0")},
            [{"item": line.item, "quantity": D("9"), "unit_sale_price": D("2000"), "line_discount_amount": D("0")}], self.owner)
        sales.link_invoice(order, invoice, self.owner)
        post_sales_invoice(invoice.pk, self.owner)                           # 9 of 10 sold from stock, before any production order
        quotes.save_spec(line, self.owner, "printing", SPEC)
        quotes.add_cost_line(line, self.owner, kind="material", item=self.paper, quantity="1")
        self.assertEqual((quotes.went_plain([line.pk]), quotes.pending([line.pk])), ({line.pk}, set()))
        with self.assertRaisesMessage(ValidationError, "عليه فاتورة"):
            quotes.convert(line, self.owner)
        again = make_to_order.make(line, self.owner, {})
        self.assertEqual((again.quantity, again.recipe.product), (D("1.000"), self.catalogue))


class VatTests(PrintingPlant):
    def test_the_job_is_invoiced_at_the_vat_rate_the_customer_accepted(self):
        from taxes.models import ItemTaxRate, TaxRate

        set_capability_enabled(self.profile, "vat", True)
        reduced = TaxRate.objects.create(code="VAT5", name_ar="ضريبة مخفضة", rate=D("5.00"), active=True)
        ItemTaxRate.objects.create(item=self.catalogue, tax_rate=reduced)
        order = self.quote()
        self.sheet(order)
        order = self.confirm(order)
        accepted = sales.totals(order)
        self.assertEqual((self.line(order).vat_rate, accepted["tax"]), (D("5.00"), D("684.38")))
        quotes.convert(self.line(order), self.owner)
        line = self.line(order)
        self.assertEqual(ItemTaxRate.objects.get(item=line.item).tax_rate, reduced)
        draft = create_sales_draft_with_tax({"invoice_number": "SI-VAT", "invoice_date": TODAY, "customer": self.customer, "selling_location": self.plant,
                                             "paid_now": D("0")}, [{"item": line.item, "quantity": line.quantity, "unit_sale_price": line.unit_price}], self.owner)
        self.assertEqual((draft.tax_amount, draft.total_amount), (accepted["tax"], accepted["total"]))


class TillTests(PrintingPlant):
    def test_the_till_never_offers_a_job_item_even_when_it_is_pinned(self):
        from sales.pos_buttons import save_pinned

        order, _production = self.job()
        job_item = self.line(order).item
        save_pinned([job_item.pk, self.paper.pk])
        set_capability_enabled(self.profile, "pos", True)                  # a factory that also sells at a till
        open_test_shift(self.owner, make_cashbox(cashbox_code="TILL", is_default=True))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("sales:pos"), {"lang": "ar"})
        self.assertEqual(page.status_code, 200)
        codes = [row["code"] for row in page.context["catalog"]]
        self.assertNotIn(job_item.item_code, codes)
        self.assertFalse([code for code in codes if code.startswith("JOB-")])
        self.assertNotIn(job_item.pk, [pick["id"] for pick in page.context["quick_picks"]])
        self.assertIn(self.paper.item_code, codes)                            # every other item still lists
        self.assertIn(self.paper.pk, [pick["id"] for pick in page.context["quick_picks"]])
        # The ordinary invoice form still finds it: the job is invoiced from its sales order there.
        form = self.client.get(reverse("sales:create"), {"lang": "ar", "order": order.pk})
        self.assertIn(job_item.item_code, [row["code"] for row in form.context["item_catalog"]])


# ---------------------------------------------------------------------------------------------------------------
# Screens, permissions and entities
# ---------------------------------------------------------------------------------------------------------------


class ScreenTests(PrintingPlant):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_page_writes_the_specs_and_the_sheet_and_puts_the_price_on_the_line(self):
        order = self.quote()
        line = self.line(order)
        url = self.url(line)
        page = self.client.get(url)
        self.assertContains(page, "مواصفات وتكلفة البند")
        self.assertContains(page, 'name="spec_paper"')
        self.client.post(url, {"action": "spec", "template": "printing", **{f"spec_{key}": value for key, value in SPEC.items()}})
        self.client.post(url, {"action": "add_line", "kind": "material", "item": self.paper.pk, "quantity": "5", "unit_cost": ""})
        self.client.post(url, {"action": "add_line", "kind": "material", "item": self.ink.pk, "quantity": "3", "unit_cost": "450"})
        self.client.post(url, {"action": "add_line", "kind": "labour", "description": "تجهيز وتشغيل المكنة", "quantity": "1", "unit_cost": "1,200"})
        self.client.post(url, {"action": "add_line", "kind": "overhead", "description": "كهربا وإهلاك", "quantity": "1", "unit_cost": "600"})
        self.client.post(url, {"action": "add_line", "kind": "outside", "description": "تجليد بالدبوس", "quantity": "1", "unit_cost": "800"})
        done = self.client.post(url, {"action": "price", "margin": "25", "override": ""}, follow=True)
        self.assertContains(done, "السعر اتحط على البند.")
        self.assertContains(done, 'data-fig-cost><bdi dir="ltr">10,950.00</bdi>')
        self.assertContains(done, 'data-fig-price-total><bdi dir="ltr">13,687.50</bdi>')
        self.assertContains(done, 'data-fig-computed><bdi dir="ltr">1,368.75</bdi>')
        line.refresh_from_db()
        self.assertEqual(line.unit_price, D("1368.75"))
        detail = self.client.get(reverse("orders:detail", args=[order.pk]))
        self.assertContains(detail, "data-line-specs")
        self.assertContains(detail, "نوع الورق ووزنه: <bdi>كوشيه 150 جرام</bdi>")
        self.assertContains(detail, f'href="{url}?lang=ar" data-quote-link>المواصفات والتكلفة</a>')

    def test_converting_from_the_page(self):
        order = self.quote()
        self.sheet(order)
        order = self.confirm(order)
        url = self.url(self.line(order))
        page = self.client.get(url)
        self.assertContains(page, "data-convert-form")
        self.assertContains(page, "حوّله لأمر إنتاج خاص")
        self.assertContains(page, "data-outside-hint")
        self.assertContains(page, "data-price-locked")
        done = self.client.post(url, {"action": "convert", "location": self.plant.pk}, follow=True)
        production = ProductionOrder.objects.get()
        self.assertContains(done, f"اتفتح أمر الإنتاج {production.number} للشغل ده.")
        self.assertContains(done, 'data-converted')
        self.assertContains(done, '<bdi dir="ltr">JOB-SO-00001-1</bdi>')
        self.assertNotContains(done, "data-convert-form")
        self.assertNotContains(done, "data-spec-form")
        self.assertEqual(self.client.post(url, {"action": "convert", "location": "999999"}, follow=True).status_code, 200)
        self.assertEqual(ProductionOrder.objects.count(), 1)

    def test_the_quotation_print_shows_the_specs_under_the_line_and_never_a_cost(self):
        order = self.quote()
        self.sheet(order)
        printed = self.client.get(reverse("orders:print", args=[order.pk]))
        self.assertContains(printed, "data-line-specs")
        self.assertContains(printed, "المقاس: <bdi>A4</bdi>")
        self.assertContains(printed, "التشطيب والتجليد: <bdi>تدبيس من النص</bdi>")
        for amount in ("1,400.00", "7,000.00", "10,950.00", "1,200.00"):
            self.assertNotContains(printed, amount)
        self.assertContains(printed, "13,687.50")                             # the price the customer sees
        english = self.client.get(reverse("orders:print", args=[order.pk]), {"lang": "en"})
        self.assertContains(english, "Paper and weight: <bdi>كوشيه 150 جرام</bdi>")

    def test_the_english_page(self):
        order = self.quote()
        self.sheet(order)
        page = self.client.get(self.url(self.line(order)), {"lang": "en"})
        self.assertContains(page, "Line specifications and cost")
        self.assertContains(page, "Cost sheet")
        self.assertContains(page, "Save and put the price on the line")
        self.assertContains(page, "Paper and weight")

    def test_the_pages_use_no_banned_word_and_no_arabic_line_starts_or_ends_with_a_latin_word(self):
        import pathlib

        from django.conf import settings

        root = pathlib.Path(settings.BASE_DIR)
        for name in ("manufacturing/quotes.py", "manufacturing/quote_views.py", "templates/manufacturing/quote_sheet.html", "templates/manufacturing/_quote_line.html"):
            text = (root / name).read_text(encoding="utf-8")
            for word in NOT_SAID:
                self.assertNotIn(word, text, name)
        texts = [("screens", key, text) for key, text in quote_views.WORDS["ar"].items()] + [("rules", key, text) for key, text in quotes.MESSAGES["ar"].items()]
        texts += [("template", slug, spec["ar"]) for slug, spec in quotes.TEMPLATES.items()]
        texts += [("field", key, label) for spec in quotes.TEMPLATES.values() for key, label, _en in spec["fields"]]
        texts += [("pending", "job_pending", make_to_order.WORDS["ar"]["job_pending"])]
        for label, key, text in texts:
            first, last = edges(text)
            with self.subTest(words=label, key=key):
                self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)
                self.assertFalse(first.startswith("{") or last.endswith("}"), text)
        quote = self.quote()
        self.sheet(quote)
        order, _production = self.job(description="كتالوج المؤتمر")
        pages = [self.url(self.line(quote)), self.url(self.line(order)), reverse("orders:detail", args=[quote.pk]), reverse("orders:detail", args=[order.pk])]
        for url in pages:
            html = self.client.get(url).content.decode()
            for word in NOT_SAID:
                self.assertNotIn(word, html, url)
            if "/orders/" in url:
                continue                                                      # the sales order page's own lines are older than this change
            for text in sentences(html):
                first, last = edges(text)
                with self.subTest(url=url, text=text[:50]):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)


class PermissionTests(PrintingPlant):
    def test_a_manager_sees_the_specs_and_the_price_never_the_sheet_and_can_convert(self):
        order = self.quote()
        self.sheet(order)
        manager = person(RoleCode.MANAGER, "quote_manager")
        self.client.force_login(manager)
        url = self.url(self.line(order))
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-spec-form")                         # a manager may make quotations
        self.assertContains(page, 'value="كوشيه 150 جرام"')
        self.assertContains(page, "1,368.75")                               # the price on the line
        self.assertNotContains(page, "data-sheet")
        for amount in MONEY:
            self.assertNotContains(page, amount)
        detail = self.client.get(reverse("orders:detail", args=[order.pk]))
        self.assertContains(detail, "data-quote-link>المواصفات</a>")
        before = list(CostSheetLine.objects.values_list("pk", "unit_cost"))
        for data in ({"action": "add_line", "kind": "labour", "description": "زيادة", "quantity": "1", "unit_cost": "1"},
                     {"action": "remove_line", "row": CostSheetLine.objects.first().pk}, {"action": "price", "margin": "90"}):
            with self.subTest(action=data["action"]):
                self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual(list(CostSheetLine.objects.values_list("pk", "unit_cost")), before)
        with self.assertRaises(PermissionDenied):
            quotes.set_price(self.line(order), manager, margin="90")
        self.assertEqual(self.line(order).unit_price, D("1368.75"))
        self.confirm(order)
        page = self.client.get(url)
        self.assertContains(page, "data-convert-form")
        self.client.post(url, {"action": "convert", "location": self.plant.pk})
        production = ProductionOrder.objects.get()
        self.assertEqual((production.labor_cost, production.overhead_cost), (D("1200.00"), D("600.00")))

    def test_a_cashier_writes_the_specs_but_cannot_convert_and_a_stock_keeper_cannot_open_the_page(self):
        order = self.confirm(self.quote())
        quotes.add_cost_line(self.line(order), self.owner, kind="material", item=self.paper, quantity="5")
        cashier = person(RoleCode.CASHIER, "quote_cashier")
        self.client.force_login(cashier)
        url = self.url(self.line(order))
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-convert-form")
        self.client.post(url, {"action": "spec", "template": "printing", "spec_size": "A5"})
        self.assertEqual(CostSheet.objects.get().spec, {"size": "A5"})
        self.assertEqual(self.client.post(url, {"action": "convert"}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            quotes.convert(self.line(order), cashier)
        self.assertFalse(ProductionOrder.objects.exists())
        keeper = person(RoleCode.STOCK_KEEPER, "quote_keeper")
        self.client.force_login(keeper)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"action": "convert"}).status_code, 403)
        # Seeing the sheet takes the cost permission; writing it takes the quotation permission too.
        viewer = limited("quote_cost_viewer", "sales.view_sales_invoices", "inventory.view_cost")
        self.client.force_login(viewer)
        page = self.client.get(url)
        self.assertContains(page, "data-sheet")
        self.assertContains(page, '<bdi dir="ltr">7,000.00</bdi>')
        self.assertNotContains(page, "data-add-cost")
        self.assertEqual(self.client.post(url, {"action": "add_line", "kind": "labour", "description": "زيادة", "quantity": "1", "unit_cost": "1"}).status_code, 403)

    def test_switched_off_quotations_close_the_page(self):
        line = self.line(self.quote())
        set_capability_enabled(self.profile, "quotations", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url(line)).status_code, 404)
        with self.assertRaisesMessage(ValidationError, "عروض الأسعار مقفولة"):
            quotes.save_spec(line, self.owner, "printing", SPEC)


class EntityTests(PrintingPlant):
    def test_another_entitys_line_returns_404_for_every_action(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BRQ", "name_ar": "فرع المعادي", "activity_slug": "manufacturing", "sub_activity_slug": "printing"}, self.owner)
        make_location(location_code="BR-PRESS", name_ar="مطبعة الفرع", name_en="Branch press", entity=branch)
        with working_in(branch):
            far = self.quote()
            self.sheet(far)
            far = self.confirm(far)
        self.assertEqual(far.entity, branch)
        near = self.quote()
        main = Entity.objects.get(is_main=True)
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": main.pk, "next": "/dashboard/"})
        self.assertEqual(self.client.get(self.url(self.line(near))).status_code, 200)
        url = self.url(self.line(far))
        self.assertEqual(self.client.get(url).status_code, 404)
        for data in ({"action": "spec", "template": "printing", "spec_size": "A3"}, {"action": "add_line", "kind": "labour", "description": "x", "quantity": "1", "unit_cost": "1"},
                     {"action": "remove_line", "row": CostSheetLine.objects.first().pk}, {"action": "price", "margin": "50"}, {"action": "convert"}):
            with self.subTest(action=data["action"]):
                self.assertEqual(self.client.post(url, data).status_code, 404)
        sheet = CostSheet.objects.get(sales_order_line__order=far)
        self.assertEqual((sheet.spec["size"], sheet.job_item, ProductionOrder.objects.count()), ("A4", None, 0))
        with working_in(main):
            self.assertNotIn(self.line(far), list(quotes.scoped_lines()))
        # In the branch, the branch's line converts into a production order at the branch's own press.
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        self.client.post(url, {"action": "convert"})
        production = ProductionOrder.objects.get()
        self.assertEqual(production.location.entity, branch)
