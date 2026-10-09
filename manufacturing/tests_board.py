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
        self.assertEqual(short["RM-FLOUR"]["need"], D("64.000"))   # 4 for the small order + 60
        self.assertEqual(short["RM-FLOUR"]["missing"], D("14.000"))
        self.assertEqual(len(short["RM-FLOUR"]["orders"]), 2)
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
