"""R2-9: the manufacturing home is a control board, and recipes have their own page."""

from decimal import Decimal as D

from django.urls import reverse

from permissions.models import RoleCode

from . import orders
from .models import Recipe
from .tests import MfgSetup, person


class ControlBoardTests(MfgSetup):
    def test_the_board_shows_the_floor_the_shortages_and_the_cost(self):
        small = orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "20"}, self.owner)
        orders.advance(small, self.owner)
        # 300 cakes = 30 batches: 60 kg flour against 50 on hand, 30 kg sugar against 20, 300 boxes against 100.
        orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "300"}, self.owner)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("manufacturing:home") + "?lang=ar")
        self.assertEqual((page.context["running_count"], page.context["planned_count"]), (1, 1))
        short = {row["item"].item_code: row for row in page.context["shortages"]}  # both orders together
        self.assertEqual(set(short), {"RM-FLOUR", "RM-SUGAR", "PK-BOX"})
        # HG-036: the small order took its 4 kg when it started (work in progress),
        # so only the big one still needs flour: 60 against the 46 left.
        self.assertEqual(short["RM-FLOUR"]["need"], D("60.000"))
        self.assertEqual(short["RM-FLOUR"]["missing"], D("14.000"))
        self.assertEqual(len(short["RM-FLOUR"]["orders"]), 1)
        self.assertEqual(page.context["wip_value"], D("190.00"))   # 2 batches x 95 on the floor
        self.assertContains(page, "data-wip-value")
        product = page.context["products"][0]
        self.assertEqual(product["unit_cost"], D("9.5000"))        # 95 a batch of 10
        self.assertEqual(product["margin"], 92)                    # (120 - 9.5) / 120
        for marker in ("data-mfg-tiles", "data-open-orders", "data-shortages", "data-products", "data-new-recipe"):
            self.assertContains(page, marker)
        self.assertContains(self.client.get(reverse("manufacturing:home") + "?lang=en"), "On the floor")

    def test_no_cost_without_the_cost_permission(self):
        keeper = person(RoleCode.STOCK_KEEPER, "mfg_board_keeper")
        self.client.force_login(keeper)
        page = self.client.get(reverse("manufacturing:home") + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("unit_cost", page.context["products"][0])

    def test_a_recipe_has_its_own_page(self):
        juice = self.cake.__class__.objects.create(item_code="FG-JUICE", item_name="عصير", is_stock_tracked=True)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("manufacturing:recipe_new") + "?lang=ar")
        self.assertContains(page, "data-add-component")
        self.assertContains(page, "data-recipe-line hidden")      # three lines to start, more on demand
        made = self.client.post(reverse("manufacturing:recipe_new"), {"lang": "ar", "product": str(juice.pk), "output_quantity": "4",
                                                                       "component_0": str(self.sugar.pk), "qty_0": "1", "active": "on"})
        recipe = Recipe.objects.get(product=juice)
        self.assertRedirects(made, f"{reverse('manufacturing:recipe', args=[recipe.pk])}?lang=ar", fetch_redirect_response=False)
        bad = self.client.post(reverse("manufacturing:recipe_new"), {"lang": "ar", "product": str(juice.pk), "output_quantity": "1"})
        self.assertContains(bad, "data-recipe-error")

    def test_a_cashier_cannot_add_recipes(self):
        cashier = person(RoleCode.CASHIER, "mfg_board_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("manufacturing:recipe_new")).status_code, 403)


class ControlBoardScaleTests(MfgSetup):
    """Codex on #173: bulk reads, entity-scoped stock, inactive recipes, warehouses on shortages."""

    def test_queries_do_not_grow_with_orders(self):
        for n in range(3):
            orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "300"}, self.owner)
        self.client.force_login(self.owner)
        self.client.get(reverse("manufacturing:home"))  # warm caches (profile, flags)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("manufacturing:home"))
        for n in range(10):
            orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "30"}, self.owner)
        with CaptureQueriesContext(connection) as many:
            page = self.client.get(reverse("manufacturing:home"))
        self.assertEqual(len(few), len(many))
        self.assertEqual(len(page.context["open_orders"]), 13)

    def test_inactive_recipes_stay_listed_and_shortages_name_the_warehouse(self):
        Recipe.objects.filter(pk=self.recipe.pk).update(active=False)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("manufacturing:home") + "?lang=ar")
        self.assertContains(page, "data-inactive")
        self.assertContains(page, reverse("manufacturing:recipe", args=[self.recipe.pk]))

    def test_product_stock_stays_inside_the_entity(self):
        from entities.current import working_in
        from entities.models import Entity

        from .views import _overview

        branch = Entity.objects.create(code="MFG-BR", name_ar="فرع", active=True)
        request = self.client.request().wsgi_request
        request.user = self.owner
        from manufacturing.tests import stock_in

        stock_in(self.cake, self.plant, 7, "10.00")
        with working_in(branch):
            data = _overview(request, "ar")
        self.assertEqual(data["products"][0]["stock"], 0)  # the plant belongs to the main entity
        with working_in(None):
            self.assertEqual(_overview(request, "ar")["products"][0]["stock"], 7)
