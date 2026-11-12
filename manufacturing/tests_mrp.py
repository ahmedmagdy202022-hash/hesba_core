"""F2: material requirements (MRP) from open orders, into draft purchase orders. No schema, nothing posted.

Bakery from ``MfgSetup``: one batch = 2 kg flour + 1 kg sugar + 10 boxes -> 10 cakes. The scenario of the plan:

* production order A, 20 cakes, not started: flour 4, sugar 2, boxes 20;
* a sales order of 30 cakes, confirmed, 10 cakes in stock: 20 to make, flour 4, sugar 2, boxes 20;
* flour: 5 on hand, 2 on a confirmed purchase order, minimum 3. Need 4 + 4 = 8, so 8 - 5 - 2 + 3 = 4 kg to buy;
* sugar: 10 on hand, need 4, nothing to buy; boxes: 25 on hand, need 40, 40 - 25 = 15 to buy.
"""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxMovement, CashboxOperation
from entities.current import SESSION_KEY, working_in
from hesba_testing.factories import make_customer, make_item, make_location, make_supplier, make_user, make_user_profile, stock_in
from inventory.models import StockMovement, StockOperation
from ledger.models import JournalEntry, JournalLine
from orders import purchasing as po
from orders import services as sales
from orders.models import OrderReceiptLine, PurchaseOrder, PurchaseStatus, Status
from permissions.models import RoleCode
from permissions.role_services import create_role
from purchases.models import PurchaseInvoice
from purchases.services import post_purchase_invoice
from reports.tests_dashboard import prepared_client
from settings_core import capabilities as caps
from taxes.services import create_purchase_draft_with_tax, vat_enabled

from . import make_to_order, mrp, orders, services
from . import tests_make_to_order as f1
from .models import OrderMaterialIssue, OrderStatus, ProductionOrder, ProductionRun, Recipe, RecipeLine
from .tests import person

TODAY = timezone.localdate()
MODULES = "items_services,sales_operations,purchases,inventory,cashboxes,reports,manufacturing"


class Money:
    """Everything a purchase order and a production order must never touch: counted before and after."""

    def counts(self):
        return {model.__name__: model.objects.count() for model in (
            StockOperation, StockMovement, JournalEntry, JournalLine, CashboxMovement, CashboxOperation, PurchaseInvoice, ProductionRun, OrderMaterialIssue)}


class MrpSetup(Money, TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="manufacturing", sub_activity="food", modules=MODULES)
        caps.set_capability_enabled(self.profile, "quotations", True)
        self.assertFalse(vat_enabled())
        self.owner = person(RoleCode.OWNER, "mrp_owner")
        self.plant = make_location(location_code="PLANT", is_default=True)
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق (كيلو)", is_stock_tracked=True, min_stock="3", unit="كيلو")
        self.sugar = make_item(item_code="RM-SUGAR", item_name="سكر (كيلو)", is_stock_tracked=True, unit="كيلو")
        self.box = make_item(item_code="PK-BOX", item_name="علبة", is_stock_tracked=True)
        self.cake = make_item(item_code="FG-CAKE", item_name="كيكة", is_stock_tracked=True, default_sale_price="25")
        self.mill = make_supplier(supplier_code="SUP-MILL", name="مطاحن الدلتا")
        self.packer = make_supplier(supplier_code="SUP-PACK", name="عبوات النيل")
        self.recipe = services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(self.flour, "2"), (self.sugar, "1"), (self.box, "10")], self.owner)
        # the stock comes in on posted purchase invoices, so each material has a last supplier and price
        self.buy(self.flour, "5", "20.00", self.mill)
        self.buy(self.box, "25", "2.50", self.packer)
        stock_in(self.sugar, self.plant, 10, "30.00", movement_date=TODAY)
        stock_in(self.cake, self.plant, 10, "9.50", movement_date=TODAY)
        self.customer = make_customer(customer_code="CUS-MRP", name="مخبز الأمل", phone="01001234567")
        self.planned = orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "20"}, self.owner)
        self.sale_order = self.sale("30")
        self.confirmed = self.purchase_order(self.flour, "2", "20.00", confirm=True)

    def buy(self, item, quantity, price, supplier):
        invoice = create_purchase_draft_with_tax(
            {"invoice_number": f"PI-MRP-{PurchaseInvoice.objects.count() + 1}", "invoice_date": TODAY, "supplier": supplier, "receiving_location": self.plant, "paid_now": D("0")},
            [{"item": item, "quantity": D(quantity), "unit_purchase_price": D(price), "line_discount_amount": D("0")}], self.owner)
        post_purchase_invoice(invoice.pk, self.owner)
        return invoice

    def sale(self, quantity, item=None, deliver_by=TODAY + timedelta(days=9)):
        quote = sales.create_quote({"customer": self.customer, "deliver_by": deliver_by}, self.owner)
        sales.add_line(quote, item or self.cake, self.owner, quantity=quantity, unit_price="25")
        return sales.move(quote, Status.CONFIRMED, self.owner)

    def salt(self):
        """A material at a minimum of its own: a recipe uses it (so it is a material), no order asks for it."""

        salt = make_item(item_code="RM-SALT", item_name="ملح", is_stock_tracked=True, min_stock="5")
        loaf = make_item(item_code="FG-LOAF", item_name="رغيف", is_stock_tracked=True)
        services.save_recipe({"product": loaf, "output_quantity": "10"}, [(salt, "1")], self.owner)
        return salt

    def purchase_order(self, item, quantity, price, supplier=None, confirm=False):
        order = po.create_order({"supplier": supplier or self.mill}, self.owner)
        po.add_line(order, item, self.owner, quantity=quantity, unit_price=price)
        return po.move(order, PurchaseStatus.CONFIRMED, self.owner) if confirm else order

    def row(self, item, key="materials", result=None):
        result = result or mrp.calculate()
        return next(row for row in result[key] if row["item"].pk == item.pk)

    def figures(self, row):
        return {name: row[name] for name in ("need", "on_hand", "incoming", "minimum", "to_buy")}


class ThePlansFiguresTests(MrpSetup):
    def test_flour_sugar_and_boxes_with_the_arithmetic(self):
        result = mrp.calculate()
        flour, sugar, box = (self.row(item, result=result) for item in (self.flour, self.sugar, self.box))
        # flour: planned order A 4 + sales order 4 = 8 needed; 5 on hand; 2 coming; minimum 3
        self.assertEqual(self.figures(flour), {"need": D("8"), "on_hand": D("5"), "incoming": D("2"), "minimum": D("3"), "to_buy": D("4")})
        self.assertEqual(flour["to_buy"], D("8") - D("5") - D("2") + D("3"))
        self.assertEqual(flour["to_buy"], D("4.000"))
        # sugar: 2 + 2 = 4 needed, 10 on hand: nothing to buy
        self.assertEqual(self.figures(sugar), {"need": D("4"), "on_hand": D("10"), "incoming": D("0"), "minimum": D("0"), "to_buy": D("0")})
        # boxes: 20 + 20 = 40 needed, 25 on hand
        self.assertEqual(self.figures(box), {"need": D("40"), "on_hand": D("25"), "incoming": D("0"), "minimum": D("0"), "to_buy": D("40") - D("25")})
        self.assertEqual(box["to_buy"], D("15.000"))
        self.assertEqual(result["semis"], [])

    def test_the_last_supplier_and_price_come_from_the_last_posted_purchase(self):
        flour, box = self.row(self.flour), self.row(self.box)
        self.assertEqual((flour["supplier"], flour["last_price"]), (self.mill, D("20.00")))
        self.assertEqual((box["supplier"], box["last_price"]), (self.packer, D("2.50")))
        self.assertIsNone(self.row(self.sugar)["supplier"])  # its stock came in on an opening movement, not a purchase

    def test_the_demand_is_the_planned_order_and_the_part_of_the_sale_stock_does_not_cover(self):
        result = mrp.calculate()
        planned = next(s for s in result["sources"] if s["kind"] == "order")
        sale = next(s for s in result["sources"] if s["kind"] == "sale")
        self.assertEqual((planned["number"], planned["quantity"]), (self.planned.number, D("20.000")))
        self.assertEqual((sale["uncovered"], sale["from_stock"], sale["quantity"]), (D("30"), D("10"), D("20")))
        self.assertEqual(result["counts"], {"planned": 1, "sales": 1, "to_buy": 2, "to_make": 0})

    def test_a_draft_purchase_order_is_not_counted_as_incoming(self):
        draft = self.purchase_order(self.flour, "10", "20.00")
        flour = self.row(self.flour)
        self.assertEqual((flour["incoming"], flour["to_buy"]), (D("2"), D("4")))  # not 12 and 0
        self.assertEqual([(d["number"], d["status"]) for d in flour["drafts"]], [(draft.number, "draft")])
        # once the supplier confirms it, it counts
        po.move(draft, PurchaseStatus.CONFIRMED, self.owner)
        flour = self.row(self.flour)
        self.assertEqual((flour["incoming"], flour["to_buy"], flour["drafts"]), (D("12"), D("0"), []))
        # an order that was only sent is not confirmed either
        sent = self.purchase_order(self.box, "5", "2.50")
        po.move(sent, PurchaseStatus.SENT, self.owner)
        box = self.row(self.box)
        self.assertEqual((box["incoming"], [d["status"] for d in box["drafts"]]), (D("0"), ["sent"]))

    def test_what_a_posted_invoice_received_is_stock_and_what_a_draft_invoice_holds_is_still_coming(self):
        order = self.confirmed
        # a draft receipt of 1 kg is not stock yet, and the order still owes it
        line = order.lines.get()
        draft = create_purchase_draft_with_tax(
            {"invoice_number": "PI-MRP-DRAFT", "invoice_date": TODAY, "supplier": self.mill, "receiving_location": self.plant, "paid_now": D("0")},
            [{"item": self.flour, "quantity": D("1"), "unit_purchase_price": D("20"), "line_discount_amount": D("0")}], self.owner)
        po.link_receipt(order, draft, self.owner)
        self.assertEqual(po.quantities(order)[0]["left"], D("1"))  # the purchasing screen's own "left" has taken it off
        flour = self.row(self.flour)
        self.assertEqual((flour["on_hand"], flour["incoming"], flour["to_buy"]), (D("5"), D("2"), D("4")))
        # posted: it is stock (6) and 1 is still coming: the shortage does not move
        post_purchase_invoice(draft.pk, self.owner)
        flour = self.row(self.flour)
        self.assertEqual((flour["on_hand"], flour["incoming"], flour["to_buy"]), (D("6"), D("1"), D("4")))
        self.assertEqual(OrderReceiptLine.objects.filter(order_line=line).count(), 1)

    def test_the_minimum_alone_asks_for_a_purchase_when_nothing_is_needed(self):
        ProductionOrder.objects.all().delete()
        self.sale_order.status = Status.CANCELLED
        self.sale_order.save(update_fields=["status"])
        self.assertEqual(mrp.calculate()["materials"], [])  # 5 on hand + 2 coming against a minimum of 3: fine, no row at all
        self.flour.min_stock = D("12")
        self.flour.save(update_fields=["min_stock"])
        flour = self.row(self.flour)
        self.assertEqual((flour["need"], flour["minimum"], flour["to_buy"]), (D("0"), D("12"), D("12") - D("5") - D("2")))
        self.assertEqual(mrp.calculate()["sources"], [])

    def test_sales_demand_is_handed_stock_by_delivery_date_and_units_made_for_a_line_are_not_free(self):
        earlier = self.sale("5", deliver_by=TODAY + timedelta(days=2))  # the nearer date takes the 10 cakes in stock first
        result = mrp.calculate()
        sales_rows = {s["number"]: s for s in result["sources"] if s["kind"] == "sale"}
        far = sales_rows[self.sale_order.display_number]
        self.assertNotIn(earlier.display_number, sales_rows)  # fully covered by stock: nothing to make
        self.assertEqual((far["uncovered"], far["from_stock"], far["quantity"]), (D("30"), D("5"), D("25")))
        self.assertEqual(self.row(self.flour)["need"], D("4") + D("5"))  # order A 4 + 25 cakes (2.5 batches) = 5

    def test_a_sale_of_a_kitchen_dish_is_not_planned(self):
        dish = make_item(item_code="DISH-X", item_name="طبق", is_stock_tracked=True, default_sale_price="50")
        recipe = services.save_recipe({"product": dish, "output_quantity": "1"}, [(self.flour, "1")], self.owner)
        Recipe.objects.filter(pk=recipe.pk).update(code="DISH-9")
        before = self.row(self.flour)["need"]
        self.sale("5", item=dish)
        self.assertEqual(self.row(self.flour)["need"], before)


class TwoLevelsTests(Money, TestCase):
    """Dough recipe: 2 kg flour + 1 kg sugar -> 3 kg dough. Cake recipe: 3 kg dough + 10 boxes -> 10 cakes."""

    def setUp(self):
        self.profile = prepared_client(activity="manufacturing", sub_activity="food", modules=MODULES)
        self.owner = person(RoleCode.OWNER, "mrp2_owner")
        self.plant = make_location(location_code="PLANT", is_default=True)
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق", is_stock_tracked=True)
        self.sugar = make_item(item_code="RM-SUGAR", item_name="سكر", is_stock_tracked=True)
        self.box = make_item(item_code="PK-BOX", item_name="علبة", is_stock_tracked=True)
        self.dough = make_item(item_code="SF-DOUGH", item_name="عجينة (كيلو)", is_stock_tracked=True)
        self.cake = make_item(item_code="FG-CAKE", item_name="كيكة", is_stock_tracked=True)
        self.dough_recipe = services.save_recipe({"product": self.dough, "output_quantity": "3"}, [(self.flour, "2"), (self.sugar, "1")], self.owner)
        self.cake_recipe = services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(self.dough, "3"), (self.box, "10")], self.owner)
        stock_in(self.dough, self.plant, 1, "10.00", movement_date=TODAY)  # 1 kg of dough in the fridge
        self.order = orders.create_order({"recipe": self.cake_recipe, "location": self.plant, "quantity": "20"}, self.owner)

    def row(self, item, key="materials"):
        return next(row for row in mrp.calculate()[key] if row["item"].pk == item.pk)

    def test_dough_is_netted_rounded_up_to_whole_batches_then_exploded(self):
        dough = self.row(self.dough, "semis")
        # 20 cakes = 2 batches = 6 kg of dough; 1 kg in stock: 5 kg to make; one batch is 3 kg, so 2 batches = 6 kg
        self.assertEqual((dough["need"], dough["on_hand"], dough["net"], dough["batches"], dough["make"]), (D("6"), D("1"), D("5"), D("2"), D("6")))
        self.assertEqual(dough["make"], D("3") * ((D("6") - D("1")) / D("3")).to_integral_value(rounding="ROUND_CEILING"))
        # 2 batches of dough: flour 2 x 2 = 4, sugar 1 x 2 = 2; the cakes' own boxes 10 x 2 = 20
        self.assertEqual(self.row(self.flour)["need"], D("4"))
        self.assertEqual(self.row(self.flour)["to_buy"], D("4"))
        self.assertEqual(self.row(self.sugar)["to_buy"], D("2"))
        self.assertEqual(self.row(self.box)["to_buy"], D("20"))
        with self.assertRaises(StopIteration):
            self.row(self.dough)  # dough is made, never bought

    def test_enough_dough_asks_for_no_flour_and_an_exact_multiple_is_not_rounded_further(self):
        stock_in(self.dough, self.plant, 5, "10.00", movement_date=TODAY)  # 6 kg now
        dough = self.row(self.dough, "semis")
        self.assertEqual((dough["net"], dough["batches"], dough["make"]), (D("0"), D("0"), D("0")))
        with self.assertRaises(StopIteration):
            self.row(self.flour)
        StockMovement.objects.filter(item=self.dough).delete()  # no dough at all: 6 kg is exactly 2 batches, not 3
        dough = self.row(self.dough, "semis")
        self.assertEqual((dough["net"], dough["batches"], dough["make"]), (D("6"), D("2"), D("6")))

    def test_an_item_used_at_two_levels_is_netted_once_against_all_its_demand(self):
        # the cake also takes 1 kg of flour per batch straight from the sack: 2 direct + 4 through the dough = 6
        services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(self.dough, "3"), (self.box, "10"), (self.flour, "1")], self.owner, self.cake_recipe)
        stock_in(self.flour, self.plant, 5, "20.00", movement_date=TODAY)
        flour = self.row(self.flour)
        self.assertEqual((flour["need"], flour["on_hand"], flour["to_buy"]), (D("6"), D("5"), D("1")))

    def test_making_the_dough_removes_its_row_and_the_flour_is_not_counted_twice(self):
        before = self.counts()
        made = mrp.make_production_order(self.dough.pk, self.owner, {})
        self.assertEqual((made.recipe, made.quantity, made.status, made.location), (self.dough_recipe, D("6.000"), OrderStatus.PLANNED, self.plant))
        self.assertEqual(self.counts(), before)  # a planned order moves no stock and posts nothing
        dough = self.row(self.dough, "semis")
        self.assertEqual((dough["incoming"], dough["making"], dough["net"], dough["make"]), (D("6"), D("6"), D("0"), D("0")))
        # the flour of the dough order (4) is demand once; the cakes' dough no longer asks for flour a second time
        self.assertEqual((self.row(self.flour)["need"], self.row(self.sugar)["need"], self.row(self.box)["need"]), (D("4"), D("2"), D("20")))
        with self.assertRaisesMessage(ValidationError, "مش محتاج تصنيع"):
            mrp.make_production_order(self.dough.pk, self.owner, {})

    def test_a_typed_quantity_place_and_conversion_cost_go_into_the_order(self):
        for data, text in (({"quantity": "0"}, "أكبر من صفر"), ({"quantity": "abc"}, "أكبر من صفر"), ({"location": 99999}, "اختار مكان الإنتاج")):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, text):
                mrp.make_production_order(self.dough.pk, self.owner, data)
        self.assertEqual(ProductionOrder.objects.count(), 1)  # only the cakes' order
        other = make_location(location_code="PLANT2")
        made = mrp.make_production_order(self.dough.pk, self.owner, {"quantity": "9", "location": other.pk, "labor_cost": "30", "overhead_cost": "12.50"})
        self.assertEqual((made.quantity, made.location, made.labor_cost, made.overhead_cost), (D("9.000"), other, D("30.00"), D("12.50")))

    def test_a_minimum_on_the_semi_finished_item_itself_asks_for_whole_batches_too(self):
        self.dough.min_stock = D("4")
        self.dough.save(update_fields=["min_stock"])
        dough = self.row(self.dough, "semis")  # 6 needed + 4 minimum - 1 on hand = 9 = exactly 3 batches
        self.assertEqual((dough["net"], dough["batches"], dough["make"]), (D("9"), D("3"), D("9")))
        self.assertEqual(self.row(self.flour)["need"], D("6"))

    def test_a_recipe_loop_saved_before_the_check_is_reported_and_never_walked(self):
        RecipeLine.objects.create(recipe=self.dough_recipe, component=self.cake, quantity=D("1"))  # dough uses cake, bypassing save_recipe
        result = mrp.calculate()
        self.assertEqual({w["kind"] for w in result["warnings"]}, {"loop"})
        self.assertEqual({w["item"].pk for w in result["warnings"]}, {self.dough.pk, self.cake.pk})  # both are in the circle
        self.assertEqual([r["item"].pk for r in result["semis"]], [])

    def test_a_chain_deeper_than_ten_levels_stops_with_a_warning(self):
        chain = [make_item(item_code=f"SF-{n:02d}", item_name=f"مرحلة {n}", is_stock_tracked=True) for n in range(14)]
        for upper, lower in zip(chain, chain[1:]):
            services.save_recipe({"product": upper, "output_quantity": "1"}, [(lower, "1")], self.owner)
        orders.create_order({"recipe": Recipe.objects.get(product=chain[0]), "location": self.plant, "quantity": "1"}, self.owner)
        result = mrp.calculate()
        depth = [w for w in result["warnings"] if w["kind"] == "depth"]
        self.assertEqual([w["item"].pk for w in depth], [chain[11].pk])  # level 10 is not opened any further
        self.assertNotIn(chain[13].pk, [r["item"].pk for r in result["materials"]])


class CycleTests(Money, TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="manufacturing", sub_activity="food", modules=MODULES)
        self.owner = person(RoleCode.OWNER, "cyc_owner")
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق", is_stock_tracked=True)
        self.dough = make_item(item_code="SF-DOUGH", item_name="عجينة", is_stock_tracked=True)
        self.cake = make_item(item_code="FG-CAKE", item_name="كيكة", is_stock_tracked=True)
        self.gift = make_item(item_code="FG-GIFT", item_name="علبة هدية", is_stock_tracked=True)
        services.save_recipe({"product": self.dough, "output_quantity": "3"}, [(self.flour, "2")], self.owner)
        self.cake_recipe = services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(self.dough, "3")], self.owner)

    def test_dough_that_uses_the_cake_is_refused_in_both_languages_and_nothing_is_saved(self):
        for lang, text in (("ar", "«كيكة» نفسه بيتصنع من «عجينة»"), ("en", "“كيكة” is itself made from “عجينة”")):
            with self.subTest(lang=lang), self.assertRaisesMessage(ValidationError, text):
                services.save_recipe({"product": self.dough, "output_quantity": "3"}, [(self.flour, "2"), (self.cake, "1")], self.owner, lang=lang)
        self.assertEqual(RecipeLine.objects.filter(recipe__product=self.dough).count(), 1)

    def test_a_longer_loop_is_refused_at_any_depth(self):
        services.save_recipe({"product": self.gift, "output_quantity": "1"}, [(self.cake, "1")], self.owner)  # gift -> cake -> dough -> flour
        with self.assertRaisesMessage(ValidationError, "«علبة هدية» نفسه بيتصنع من «عجينة»"):
            services.save_recipe({"product": self.dough, "output_quantity": "3"}, [(self.flour, "2"), (self.gift, "1")], self.owner)
        # replacing the cake's own recipe keeps working, and so does a recipe that only shares a component
        services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(self.dough, "4")], self.owner, self.cake_recipe)
        services.save_recipe({"product": self.gift, "output_quantity": "1"}, [(self.cake, "1"), (self.flour, "1")], self.owner)

    def test_an_inactive_recipe_is_out_of_the_circle_until_it_is_switched_on(self):
        old = services.save_recipe({"product": self.cake, "output_quantity": "10", "active": False}, [(self.dough, "3")], self.owner, self.cake_recipe)
        services.save_recipe({"product": self.dough, "output_quantity": "3"}, [(self.flour, "2"), (self.cake, "1")], self.owner,
                             Recipe.objects.get(product=self.dough))  # nothing active makes the cake from dough now
        with self.assertRaisesMessage(ValidationError, "نفسه بيتصنع من"):
            services.save_recipe({"product": self.cake, "output_quantity": "10", "active": True}, [(self.dough, "3")], self.owner, old)


class ScreenAndActionTests(MrpSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def screen(self, lang="ar", **extra):
        return self.client.get(reverse("manufacturing:mrp"), {"lang": lang, **extra})

    def buy_all(self, **extra):
        return self.client.post(reverse("manufacturing:mrp"), {"lang": "ar", "action": "purchase", "pick": [self.flour.pk, self.box.pk], **extra})

    def test_two_draft_purchase_orders_one_per_supplier_and_nothing_is_posted(self):
        before = self.counts()
        invoices = PurchaseInvoice.objects.count()
        response = self.buy_all()
        self.assertEqual(response.status_code, 302)
        made = list(PurchaseOrder.objects.filter(status=PurchaseStatus.DRAFT).order_by("pk"))
        self.assertEqual([(o.supplier, o.status) for o in made[-2:]], [(self.mill, "draft"), (self.packer, "draft")])
        flour_po, box_po = made[-2], made[-1]
        flour_line, box_line = flour_po.lines.get(), box_po.lines.get()
        self.assertEqual((flour_line.item, flour_line.quantity, flour_line.unit_price), (self.flour, D("4.000"), D("20.00")))
        self.assertEqual((box_line.item, box_line.quantity, box_line.unit_price), (self.box, D("15.000"), D("2.50")))
        self.assertEqual(po.totals(flour_po)["total"], D("80.00"))  # 4 kg x 20.00
        self.assertEqual(po.totals(box_po)["total"], D("37.50"))  # 15 x 2.50
        self.assertEqual(self.counts(), before)  # no stock, journal, cashbox, purchase invoice or run
        self.assertEqual(PurchaseInvoice.objects.count(), invoices)
        self.assertTrue(response["Location"].endswith(f"bought={flour_po.pk},{box_po.pk}"), response["Location"])

    def test_the_result_lists_the_new_drafts_and_the_rows_note_them_without_counting_them(self):
        response = self.buy_all()
        page = self.client.get(response["Location"])
        self.assertContains(page, "data-mrp-bought")
        for order in PurchaseOrder.objects.filter(status=PurchaseStatus.DRAFT):
            self.assertContains(page, f'data-bought-order="{order.number}"')
            self.assertContains(page, reverse("purchase_orders:detail", args=[order.pk]))
        html = page.content.decode()
        flour_row = re.search(r'<tr data-material="RM-FLOUR".*?</tr>', html, re.S).group(0)
        self.assertIn("data-draft-note", flour_row)
        self.assertRegex(flour_row, r'<td data-to-buy><span><b class="mfg-short">4</b>')  # still 4 to buy: drafts are not counted
        self.assertNotRegex(flour_row, r'name="pick"[^>]* checked')  # and not ticked again by default, so nobody orders it twice
        self.assertEqual(self.row(self.flour)["to_buy"], D("4.000"))

    def test_two_materials_of_one_supplier_make_one_order_and_a_chosen_supplier_wins(self):
        sugar_short = self.salt()
        self.client.post(reverse("manufacturing:mrp"), {"lang": "ar", "action": "purchase", "pick": [self.flour.pk, self.box.pk, sugar_short.pk],
                                                         f"supplier_{self.box.pk}": self.mill.pk, f"supplier_{sugar_short.pk}": self.mill.pk,
                                                         f"qty_{self.flour.pk}": "6.5"})
        mill_order = PurchaseOrder.objects.filter(supplier=self.mill, status=PurchaseStatus.DRAFT).get()
        lines = {line.item_id: (line.quantity, line.unit_price) for line in mill_order.lines.all()}
        self.assertEqual(lines[self.flour.pk], (D("6.500"), D("20.00")))
        self.assertEqual(lines[self.box.pk], (D("15.000"), D("2.50")))
        self.assertEqual(lines[sugar_short.pk][0], D("5.000"))  # no last price: the item's own default applies
        self.assertEqual(PurchaseOrder.objects.filter(status=PurchaseStatus.DRAFT).count(), 1)

    def test_bad_picks_are_refused_and_nothing_is_made(self):
        salt = self.salt()
        drafts = PurchaseOrder.objects.filter(status=PurchaseStatus.DRAFT)
        for post, text in (({"pick": []}, "اختار خامة واحدة"), ({"pick": [self.sugar.pk]}, "مش ناقص دلوقتي"), ({"pick": [salt.pk]}, "اختار مورد"),
                           ({"pick": [self.flour.pk], f"qty_{self.flour.pk}": "0"}, "أكبر من صفر"), ({"pick": [self.flour.pk], f"qty_{self.flour.pk}": "x"}, "أكبر من صفر")):
            with self.subTest(post=post):
                response = self.client.post(reverse("manufacturing:mrp"), {"lang": "ar", "action": "purchase", **post})
                self.assertEqual(response.status_code, 302)
                shown = self.client.get(response["Location"]).content.decode()
                self.assertIn(text, shown)
        self.assertFalse(drafts.exists())

    def test_a_production_order_from_the_screen_for_a_semi_finished_row(self):
        dough = make_item(item_code="SF-DOUGH", item_name="عجينة", is_stock_tracked=True)
        services.save_recipe({"product": dough, "output_quantity": "3"}, [(self.flour, "2"), (self.sugar, "1")], self.owner)
        services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(dough, "3"), (self.box, "10")], self.owner, self.recipe)
        page = self.screen()
        self.assertContains(page, 'data-semi="SF-DOUGH"')
        before = self.counts()
        response = self.client.post(reverse("manufacturing:mrp"), {"lang": "ar", "action": "produce", "item": dough.pk, "labor_cost": "20"})
        made = ProductionOrder.objects.get(recipe__product=dough)
        self.assertEqual((made.status, made.labor_cost), (OrderStatus.PLANNED, D("20.00")))
        self.assertEqual(self.counts(), before)
        self.assertContains(self.client.get(response["Location"]), f'data-made-order="{made.number}"')

    def test_the_screen_reads_in_both_languages_and_at_every_state(self):
        for lang, words in (("ar", ("احتياج الخامات", "اعمل أمر شراء")), ("en", ("Material requirements", "Make a purchase order"))):
            with self.subTest(lang=lang):
                page = self.screen(lang)
                self.assertEqual(page.status_code, 200)
                for word in words:
                    self.assertContains(page, word)
                self.assertContains(page, 'data-mrp-materials')
                self.assertContains(page, 'name="pick"')
        self.assertContains(self.screen("ar"), "20.00")  # the last price

    def test_nothing_to_plan_says_so(self):
        ProductionOrder.objects.all().delete()
        self.sale_order.status = Status.CANCELLED
        self.sale_order.save(update_fields=["status"])
        self.assertContains(self.screen(), "data-mrp-empty")

    def test_the_boards_shortage_table_links_here_not_to_a_blank_purchase_invoice(self):
        orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "30"}, self.owner)  # the board's own shortage: 10 kg of flour against 5
        page = self.client.get(reverse("manufacturing:home"), {"lang": "ar"})
        html = page.content.decode()
        self.assertContains(page, "data-shortages")
        link = re.search(r'<a [^>]*href="([^"]+)"[^>]*data-mrp-link', html)
        self.assertIsNotNone(link)
        self.assertEqual(link.group(1), reverse("manufacturing:mrp") + "?lang=ar")
        shortage_paragraph = html[html.index("data-shortages"):html.index("data-products")]
        self.assertNotIn(f'href="{reverse("purchases:create")}', shortage_paragraph)
        nav = re.search(r'<a [^>]*href="([^"]+)"[^>]*data-mrp-nav', html)
        self.assertEqual(nav.group(1), reverse("manufacturing:mrp") + "?lang=ar")


class PermissionAndEntityTests(MrpSetup):
    def role(self, name, *extra):
        role = create_role(name_ar=name, name_en=name, codes=["inventory.view_stock", *extra], actor=self.owner)
        user = make_user(username=f"mrp_{name}")
        make_user_profile(user=user, role=role)
        return user

    def post(self, user, action, **data):
        self.client.force_login(user)
        return self.client.post(reverse("manufacturing:mrp"), {"lang": "ar", "action": action, **data})

    def test_the_screen_needs_the_manufacturing_boards_right_and_each_action_its_own(self):
        viewer = self.role("viewer")
        buyer = self.role("buyer", "purchases.create_purchase_invoice")
        keeper = self.role("keeper", "inventory.adjust_stock")
        nobody = person(RoleCode.CASHIER, "mrp_cashier")  # a cashier has no stock view
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse("manufacturing:mrp")).status_code, 403)
        before, drafts = self.counts(), PurchaseOrder.objects.count()
        for user in (viewer, keeper):  # the screen opens for them, the purchase action does not
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("manufacturing:mrp")).status_code, 200)
            self.assertEqual(self.post(user, "purchase", pick=[self.flour.pk]).status_code, 403)
        for user in (viewer, buyer):  # and the production action needs adjust_stock
            self.assertEqual(self.post(user, "produce", item=self.flour.pk).status_code, 403)
        self.assertEqual((PurchaseOrder.objects.count(), self.counts()), (drafts, before))
        self.assertEqual(ProductionOrder.objects.count(), 1)  # only order A
        # a holder of the right does it
        self.assertEqual(self.post(buyer, "purchase", pick=[self.flour.pk]).status_code, 302)
        self.assertEqual(PurchaseOrder.objects.count(), drafts + 1)

    def test_the_forms_and_prices_follow_the_rights(self):
        viewer = self.role("viewer")
        self.client.force_login(viewer)
        html = self.client.get(reverse("manufacturing:mrp"), {"lang": "ar"}).content.decode()
        self.assertNotIn('name="pick"', html)
        self.assertNotIn("data-buy-button", html)
        self.assertIn("data-no-buy", html)
        self.assertNotIn("data-last-price", html)  # a purchase price is for those who may see cost or buy
        buyer = self.role("buyer", "purchases.create_purchase_invoice")
        self.client.force_login(buyer)
        html = self.client.get(reverse("manufacturing:mrp"), {"lang": "ar"}).content.decode()
        self.assertIn('name="pick"', html)
        self.assertIn("data-last-price", html)

    def test_a_switched_off_purchases_module_closes_the_purchase_action(self):
        from unittest import mock

        self.client.force_login(self.owner)
        with mock.patch("orders.purchasing.enabled", return_value=False):
            response = self.client.post(reverse("manufacturing:mrp"), {"lang": "ar", "action": "purchase", "pick": [self.flour.pk]})
            self.assertContains(self.client.get(reverse("manufacturing:mrp"), {"lang": "ar"}), "data-no-buy")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PurchaseOrder.objects.filter(status=PurchaseStatus.DRAFT).exists())

    def test_another_entitys_orders_stock_and_purchase_orders_are_never_counted(self):
        from entities.models import Entity
        from entities.services import main_entity

        branch = Entity.objects.create(code="MRP-BR", name_ar="فرع", active=True)
        store = make_location(location_code="BRANCH-STORE", entity=branch, is_default=True)
        stock_in(self.flour, store, 500, "20.00", movement_date=TODAY)  # the branch has flour the main plant does not
        stock_in(self.cake, store, 100, "9.50", movement_date=TODAY)
        with working_in(branch):
            far_sale = self.sale("30")
            far_planned = orders.create_order({"recipe": self.recipe, "location": store, "quantity": "50"}, self.owner)
            far_po = self.purchase_order(self.flour, "40", "20.00", confirm=True)
            far_draft = self.purchase_order(self.box, "7", "2.50")
        self.assertEqual((far_sale.entity_id, far_po.entity_id), (branch.pk, branch.pk))
        with working_in(main_entity()):
            flour, box, sugar = (self.row(item) for item in (self.flour, self.box, self.sugar))
            self.assertEqual(self.figures(flour), {"need": D("8"), "on_hand": D("5"), "incoming": D("2"), "minimum": D("3"), "to_buy": D("4")})
            self.assertEqual((box["to_buy"], sugar["to_buy"], box["drafts"]), (D("15"), D("0"), []))
            numbers = {s["number"] for s in mrp.calculate()["sources"]}
            self.assertEqual(numbers, {self.planned.number, self.sale_order.display_number})
            self.assertNotIn(far_planned.number, numbers)
            # the screen itself, as the main entity: 404 for the branch's order, and the purchase action never touches the branch's
            self.client.force_login(self.owner)
            session = self.client.session
            session[SESSION_KEY] = main_entity().pk
            session.save()
            made = self.client.post(reverse("manufacturing:mrp"), {"lang": "ar", "action": "purchase", "pick": [self.flour.pk]})
            order = PurchaseOrder.objects.exclude(pk__in=[far_po.pk, far_draft.pk, self.confirmed.pk]).get()
            self.assertEqual((order.entity_id, order.lines.get().quantity), (main_entity().pk, D("4.000")))
            self.assertEqual(self.client.get(reverse("manufacturing:mrp"), {"lang": "ar", "bought": far_po.pk}).content.decode().count("data-bought-order"), 0)
            self.assertEqual(made.status_code, 302)
        with working_in(branch):
            flour = self.row(self.flour)
            # 50 + 30 cakes' flour: planned order 5 batches = 10, sale 30 with 100 cakes in stock = nothing to make
            self.assertEqual(self.figures(flour), {"need": D("10"), "on_hand": D("500"), "incoming": D("40"), "minimum": D("3"), "to_buy": D("0")})
            self.assertEqual({s["number"] for s in mrp.calculate()["sources"]}, {far_planned.number})
            self.assertEqual([d["number"] for d in self.row(self.box)["drafts"]], [far_draft.number])

    def test_the_last_price_and_supplier_are_the_entitys_own(self):
        from entities.models import Entity
        from entities.services import main_entity

        branch = Entity.objects.create(code="MRP-BR2", name_ar="فرع", active=True)
        store = make_location(location_code="BRANCH-STORE", entity=branch, is_default=True)
        branch_supplier = make_supplier(supplier_code="SUP-BR", name="مورد الفرع")
        invoice = create_purchase_draft_with_tax(
            {"invoice_number": "PI-BR-1", "invoice_date": TODAY + timedelta(days=1), "supplier": branch_supplier, "receiving_location": store, "paid_now": D("0")},
            [{"item": self.flour, "quantity": D("1"), "unit_purchase_price": D("99.00"), "line_discount_amount": D("0")}], self.owner)
        post_purchase_invoice(invoice.pk, self.owner)
        with working_in(main_entity()):
            flour = self.row(self.flour)
            self.assertEqual((flour["supplier"], flour["last_price"]), (self.mill, D("20.00")))
        with working_in(branch):
            self.flour.min_stock = D("50")
            self.flour.save(update_fields=["min_stock"])
            flour = self.row(self.flour)
            self.assertEqual((flour["supplier"], flour["last_price"]), (branch_supplier, D("99.00")))


class MadeForALineIsNotFreeTests(f1.MakeToOrderSetup):
    def test_units_made_for_one_order_and_not_invoiced_are_not_handed_to_another(self):
        made = self.make()  # 30 cakes for the first order
        self.walk(made)
        orders.finish(made, self.owner)
        self.assertEqual(get_stock(self.cake, self.plant), D("30"))
        second = self.sale("5", deliver_by=None)
        sources = [s for s in mrp.calculate()["sources"] if s["kind"] == "sale"]
        self.assertEqual([(s["number"], s["uncovered"], s["from_stock"], s["quantity"]) for s in sources], [(second.display_number, D("5"), D("0"), D("5"))])
        self.assertEqual(make_to_order.uncovered(self.line()), D("0"))


def get_stock(item, location):
    from inventory.services import get_item_location_stock_quantity

    return get_item_location_stock_quantity(item, location)
