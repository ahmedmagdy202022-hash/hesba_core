"""MFG-002: production orders follow the factory's own stages, then post one run."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.urls import reverse

from inventory.services import get_item_location_stock_quantity
from permissions.models import RoleCode

from . import orders
from .models import OrderStatus, ProductionOrder
from .stages import stages_for
from .tests import MfgSetup, person


class ProductionOrderTests(MfgSetup):
    def open(self, **extra):
        data = {"recipe": self.recipe, "location": self.plant, "quantity": "20", "labor_cost": "60", "overhead_cost": "40", **extra}
        return orders.create_order(data, self.owner)

    def test_an_order_walks_the_food_stages_then_posts_one_run(self):
        order = self.open()
        self.assertEqual([s[0] for s in order.stages], ["prep", "cooking", "cooling", "packing"])
        self.assertEqual(order.status, OrderStatus.PLANNED)
        # Nothing moves in stock while the order is on the floor.
        orders.advance(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, order.stage_index), (OrderStatus.IN_PROGRESS, 1))
        self.assertEqual(get_item_location_stock_quantity(self.flour, self.plant), D("50"))
        with self.assertRaises(ValidationError):
            orders.finish(order, self.owner)
        orders.advance(order, self.owner, good="19", defect="1", note="كيكة اتحرقت")
        orders.advance(order, self.owner)
        orders.advance(order, self.owner)
        with self.assertRaises(ValidationError):
            orders.advance(order, self.owner)
        card = orders.cost_card(order)
        self.assertTrue(card["estimate"])
        self.assertEqual(card["material"], D("190.00"))   # 2 batches x 95
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        self.assertEqual(order.run.output_quantity, D("20.000"))
        self.assertEqual(get_item_location_stock_quantity(self.cake, self.plant), D("20"))
        self.assertEqual(get_item_location_stock_quantity(self.flour, self.plant), D("46"))
        card = orders.cost_card(order)
        self.assertFalse(card["estimate"])
        self.assertEqual((card["material"], card["total"], card["per_unit"]), (D("190.00"), D("290.00"), D("14.5000")))
        self.assertEqual(orders.quality(order), {"defects": D("1"), "rate": D("5.0")})
        with self.assertRaises(ValidationError):
            orders.cancel(order, self.owner, "x")

    def test_rules(self):
        for data, text in (({"quantity": "0"}, "الكمية"), ({"labor_cost": "-5"}, "العمالة"), ({"recipe": None}, "الوصفة"), ({"location": None}, "مكان")):
            with self.subTest(data=data), self.assertRaises(ValidationError) as caught:
                self.open(**data)
            self.assertIn(text, " ".join(caught.exception.messages))
        order = self.open()
        for good, defect in (("25", "0"), ("-1", "0"), ("5", "-2")):
            with self.subTest(good=good, defect=defect), self.assertRaises(ValidationError):
                orders.advance(order, self.owner, good=good, defect=defect)
        with self.assertRaises(ValidationError):
            orders.cancel(order, self.owner, "")
        orders.cancel(order, self.owner, "العميل لغى")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        with self.assertRaises(ValidationError):
            orders.advance(order, self.owner)

    def test_short_materials_stop_the_finish_not_the_floor(self):
        order = self.open(quantity="1000")   # needs 200 kg flour; only 50 on hand
        for _ in order.stages:
            orders.advance(order, self.owner)
        with self.assertRaises(ValidationError):
            orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.IN_PROGRESS)
        self.assertIsNone(order.run)

    def test_garment_factories_get_garment_stages(self):
        self.profile.sub_activity_slug = "garments"
        self.profile.save()
        order = self.open()
        self.assertEqual([s[1] for s in order.stages], ["قص", "خياطة", "تشطيب وكي", "فحص جودة", "تغليف"])
        self.assertEqual(stages_for("unknown")[0][0], "prep")

    def test_board_and_screens(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("manufacturing:orders"), {"recipe": self.recipe.pk, "location": self.plant.pk, "quantity": "10",
                                                                     "due_date": "2020-01-01", "labor_cost": "30"})
        order = ProductionOrder.objects.get()
        self.assertRedirects(response, reverse("manufacturing:order", args=[order.pk]) + "?lang=ar", fetch_redirect_response=False)
        board = self.client.get(reverse("manufacturing:orders"))
        self.assertContains(board, "data-late-orders")
        self.assertContains(board, f'data-order="{order.number}"')
        self.assertContains(self.client.get(reverse("manufacturing:home")), "data-orders-link")
        for _ in order.stages:
            self.client.post(reverse("manufacturing:order", args=[order.pk]), {"action": "advance"})
        page = self.client.get(reverse("manufacturing:order", args=[order.pk]))
        self.assertContains(page, "data-finish")
        self.assertContains(page, "data-cost-card")
        self.client.post(reverse("manufacturing:order", args=[order.pk]), {"action": "finish"})
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        self.assertContains(self.client.get(reverse("manufacturing:order", args=[order.pk]) + "?lang=en"), "data-order-run")

    def test_permissions(self):
        order = self.open()
        cashier = person(RoleCode.CASHIER, "mfg_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.post(reverse("manufacturing:order", args=[order.pk]), {"action": "advance"}).status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.stage_index, 0)
        keeper = person(RoleCode.STOCK_KEEPER, "mfg_keeper")
        self.client.force_login(keeper)
        board = self.client.get(reverse("manufacturing:orders"))
        self.assertEqual(board.status_code, 200)
        self.assertNotContains(self.client.get(reverse("manufacturing:order", args=[order.pk])), "data-cost-card")


class FactoryKindPresetsTests(MfgSetup):
    def test_each_kind_of_factory_starts_with_its_own_capabilities(self):
        from settings_core import capabilities

        garments = capabilities.default_selection("manufacturing", "garments")
        self.assertIn("variants", garments)
        self.assertNotIn("batches_expiry", garments)
        food = capabilities.default_selection("manufacturing", "food")
        self.assertIn("batches_expiry", food)
        self.assertNotIn("variants", food)
        self.assertIn("batches_expiry", capabilities.default_selection("manufacturing", "chemicals"))
        # Commercial presets are unchanged, and other activities still suggest none.
        self.assertIn("variants", capabilities.default_selection("commercial", "fashion"))
        self.assertEqual(capabilities.default_selection("medical", "clinic"), ())
