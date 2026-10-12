"""RESTO-004 (HG-044 C1): a dish made in the kitchen takes its ingredients out of stock, through the manufacturing engine."""

from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from hesba_testing.factories import make_item, stock_in
from inventory.models import StockMovement
from inventory.services import get_item_location_stock_quantity, recalculate_item_average_cost
from manufacturing.models import ProductionRun, Recipe
from master_data.models import Item
from permissions.models import RoleCode
from sales.models import SalesLine

from . import kitchen
from . import services as floor
from .insights import save_ingredients
from .models import DishIngredient, OrderStatus
from .tests import RestaurantSetup, person


def ledger_ok(test):
    from ledger.reports import reconciliation

    result = reconciliation()
    test.assertTrue(result["balanced"])
    test.assertTrue(result["ok"], [row for row in result["rows"] if row["difference"]])


class KitchenSetup(RestaurantSetup):
    def setUp(self):
        super().setUp()
        self.rice = make_item(item_code="R-RICE", item_name="أرز", unit="كجم", is_stock_tracked=True)
        self.lentil = make_item(item_code="R-LENT", item_name="عدس", unit="كجم", is_stock_tracked=True)
        stock_in(self.rice, self.location, 20, "30.00")
        stock_in(self.lentil, self.location, 10, "50.00")
        for item in (self.rice, self.lentil):
            recalculate_item_average_cost(item)
        save_ingredients(self.koshary, [(self.rice, "0.200"), (self.lentil, "0.100")], self.owner)  # 11.00 a plate

    def stock(self, item):
        return get_item_location_stock_quantity(item, self.location)

    def order(self, item, quantity=1):
        order = floor.open_order(self.owner, table=self.t1, guests=2)
        floor.add_item(order, item, self.owner, quantity=quantity)
        return order

    def pay(self, order, user=None):
        invoice, _ = floor.pay(order, user or self.owner, cashbox=self.cashbox, tendered=D("100000"))
        order.refresh_from_db()
        return invoice


class SwitchTests(KitchenSetup):
    def test_a_dish_never_sold_becomes_a_stock_item_with_its_recipe(self):
        dish = kitchen.make_in_kitchen(self.koshary, self.owner)
        self.assertEqual(dish.pk, self.koshary.pk)
        dish.refresh_from_db()
        self.assertTrue(dish.is_stock_tracked)
        recipe = Recipe.objects.get(code=f"DISH-{dish.pk}")
        self.assertEqual((recipe.product_id, recipe.output_quantity), (dish.pk, D("1")))
        self.assertEqual({(line.component_id, line.quantity) for line in recipe.lines.all()}, {(self.rice.pk, D("0.200")), (self.lentil.pk, D("0.100"))})
        self.assertTrue(kitchen.is_kitchen_made(dish))
        with self.assertRaises(ValidationError):
            kitchen.make_in_kitchen(dish, self.owner)  # once is enough

    def test_a_dish_without_ingredients_cannot_be_switched(self):
        with self.assertRaises(ValidationError):
            kitchen.make_in_kitchen(self.tea, self.owner)
        self.tea.refresh_from_db()
        self.assertFalse(self.tea.is_stock_tracked)

    def test_a_dish_sold_before_gets_a_stock_copy_and_its_old_invoices_never_move_stock(self):
        from sales.services import create_sales_return

        old_invoice = self.pay(self.order(self.koshary, 2))  # sold as a service
        dish = kitchen.make_in_kitchen(self.koshary, self.owner)
        self.koshary.refresh_from_db()
        self.assertNotEqual(dish.pk, self.koshary.pk)
        self.assertFalse(self.koshary.is_stock_tracked)  # the old one never changes kind
        self.assertFalse(self.koshary.active)
        self.assertTrue(dish.is_stock_tracked and dish.active)
        self.assertEqual((dish.item_name, dish.default_sale_price), (self.koshary.item_name, self.koshary.default_sale_price))
        self.assertEqual(DishIngredient.objects.filter(dish=dish).count(), 2)
        self.assertFalse(DishIngredient.objects.filter(dish=self.koshary).exists())
        line = old_invoice.lines.get(item=self.koshary)
        create_sales_return("SR-OLD-1", old_invoice.invoice_date, old_invoice.pk, [{"source_line": line, "quantity": D("1")}], "رجوع", self.owner)
        self.assertFalse(StockMovement.objects.filter(item=self.koshary).exists())  # no stock that never went out comes back
        ledger_ok(self)

    def test_only_who_manages_items_switches_or_prepares(self):
        cashier = person(RoleCode.CASHIER, "kitchen_cashier")
        with self.assertRaises(PermissionDenied):
            kitchen.make_in_kitchen(self.koshary, cashier)
        dish = kitchen.make_in_kitchen(self.koshary, self.owner)
        with self.assertRaises(PermissionDenied):
            kitchen.prepare(dish, "3", cashier)


class SellingTests(KitchenSetup):
    def setUp(self):
        super().setUp()
        self.dish = kitchen.make_in_kitchen(self.koshary, self.owner)

    def test_paying_makes_the_plates_from_their_ingredients_and_costs_them(self):
        invoice = self.pay(self.order(self.dish, 3))
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(self.stock(self.rice), D("19.400"))  # 20 − 3 × 0.2
        self.assertEqual(self.stock(self.lentil), D("9.700"))  # 10 − 3 × 0.1
        self.assertEqual(self.stock(self.dish), D("0"))  # made and sold
        run = ProductionRun.objects.get(recipe__product=self.dish)
        self.assertEqual((run.output_quantity, run.total_cost), (D("3.000"), D("33.00")))
        line = SalesLine.objects.get(invoice=invoice, item=self.dish)
        self.assertEqual(line.line_cost_amount, D("33.00"))  # the plates' real cost is the cost of sales
        ledger_ok(self)

    def test_plates_prepared_ahead_are_sold_first(self):
        kitchen.prepare(self.dish, "5", self.owner)
        self.assertEqual(self.stock(self.dish), D("5.000"))
        self.pay(self.order(self.dish, 2))
        self.assertEqual(ProductionRun.objects.filter(recipe__product=self.dish).count(), 1)  # nothing more to make
        self.assertEqual(self.stock(self.dish), D("3.000"))
        self.pay(self.order(self.dish, 4))  # 3 ready, 1 made on the spot
        run = ProductionRun.objects.filter(recipe__product=self.dish).latest("pk")
        self.assertEqual(run.output_quantity, D("1.000"))
        self.assertEqual(self.stock(self.dish), D("0"))
        ledger_ok(self)

    def test_a_short_ingredient_stops_the_payment_and_says_which(self):
        order = self.order(self.dish, 200)  # 40 kg of rice needed, 20 there
        with self.assertRaises(ValidationError) as caught:
            self.pay(order)
        message = " ".join(caught.exception.messages)
        self.assertIn(self.dish.item_name, message)
        self.assertIn("أرز", message)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.OPEN)  # nothing paid, nothing moved
        self.assertFalse(ProductionRun.objects.exists())
        self.assertEqual(self.stock(self.rice), D("20"))

    def test_a_cashier_sells_what_the_kitchen_prepared_and_is_told_when_it_is_not_ready(self):
        from permissions.services import user_has_permission

        cashier = person(RoleCode.CASHIER, "kitchen_till")
        self.assertFalse(user_has_permission(cashier, "inventory.adjust_stock"))  # the stock engine's rule, unchanged
        order = self.order(self.dish, 2)
        with self.assertRaisesMessage(ValidationError, "مش جاهز"):
            self.pay(order, cashier)
        self.assertFalse(ProductionRun.objects.exists())
        kitchen.prepare(self.dish, "2", self.owner)  # the kitchen's batch
        invoice = self.pay(order, cashier)
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(self.stock(self.dish), D("0"))
        self.assertEqual(self.stock(self.rice), D("19.600"))
        ledger_ok(self)

    def test_changed_ingredients_are_used_from_the_next_plate(self):
        save_ingredients(self.dish, [(self.rice, "0.300")], self.owner)
        self.pay(self.order(self.dish, 1))
        self.assertEqual(self.stock(self.rice), D("19.700"))
        self.assertEqual(self.stock(self.lentil), D("10"))

    def test_a_service_dish_still_moves_no_stock(self):
        self.pay(self.order(self.tea, 2))
        self.assertFalse(StockMovement.objects.filter(item=self.tea).exists())


class KitchenScreenTests(KitchenSetup):
    def test_the_dish_page_switches_and_prepares(self):
        self.client.force_login(self.owner)
        url = reverse("restaurant:dish", args=[self.koshary.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-kitchen-switch")
        self.client.post(url, {"action": "kitchen"})
        self.koshary.refresh_from_db()
        self.assertTrue(self.koshary.is_stock_tracked)
        page = self.client.get(url)
        self.assertContains(page, "data-kitchen-on")
        self.client.post(url, {"action": "prepare", "quantity": "4"})
        self.assertEqual(self.stock(self.koshary), D("4.000"))
        costing = self.client.get(reverse("restaurant:costing"))
        self.assertContains(costing, "data-kitchen-badge")
        self.assertContains(costing, f'data-dish="{self.koshary.pk}"')

    def test_a_bad_quantity_is_refused_with_a_message(self):
        dish = kitchen.make_in_kitchen(self.koshary, self.owner)
        for raw in ("0", "-2", "abc", "20000"):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                kitchen.prepare(dish, raw, self.owner)
        self.assertFalse(ProductionRun.objects.exists())
        self.assertTrue(Item.objects.filter(pk=dish.pk, is_stock_tracked=True).exists())
