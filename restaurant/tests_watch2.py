"""Watch round 2 fixes for the restaurant: RS-01 and RS-07 (a long option text must not widen a phone page) and RS-03.

The real check is a browser at 390 px (`document.documentElement.scrollWidth === 390`); these tests pin the rules that make it
true so a later stylesheet edit cannot undo them without a test failing.
"""

import re
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from hesba_testing.factories import make_item

from . import kitchen
from .insights import save_ingredients
from .tests import RestaurantSetup
from .tests_kitchen import KitchenSetup

CSS = Path(settings.BASE_DIR) / "static" / "hesba" / "css" / "restaurant.css"


def rule(css, selector):
    match = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    return match.group(1) if match else ""


class PhoneWidthRules(RestaurantSetup):
    def test_the_order_grid_and_the_pay_row_may_shrink_below_their_content(self):
        css = CSS.read_text()
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", rule(css, ".rs-row"))
        self.assertIn("minmax(0,1fr)", css.split("@media(max-width:1000px){")[1].split("}")[0])
        self.assertIn("grid-template-columns:minmax(0,1fr)", rule(css, ".rs-actions form"))
        self.assertIn("min-width:0", rule(css, ".rs-actions label"))
        self.assertIn("min-width:0", rule(css, ".rs-order>*,.rs-bill"))

    def test_the_dish_ingredients_selects_shrink_to_the_card(self):
        css = CSS.read_text()
        self.assertIn("max-width:100%", rule(css, "[data-dish-ingredients] select,[data-dish-ingredients] input"))
        self.assertIn("width:100%", rule(css, "[data-dish-ingredients] select"))
        self.assertIn("min-width:0", rule(css, "[data-dish-ingredients] td"))

    def test_the_dish_page_loads_the_restaurant_stylesheet(self):
        dish = self.koshary
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            page = self.client.get(reverse("restaurant:dish", args=[dish.pk]), {"lang": lang})
            self.assertContains(page, "hesba/css/restaurant.css")
            self.assertContains(page, "data-dish-ingredients")


class OutOfStockCountsAgree(KitchenSetup):
    """RS-03: the dashboard said 1 out of stock and the inventory screen 4: the other three were dishes cooked to order, which
    carry stock 0 and are not 'out'. Both now use the dashboard's rule (``reports.selectors.made_to_order_ids``)."""

    def test_the_dashboard_and_the_inventory_screen_count_the_same_items_out_of_stock(self):
        from reports.selectors import stock_alert_counts

        kitchen.make_in_kitchen(self.koshary, self.owner)
        kebab = make_item(item_code="D-KEBAB", item_name="كباب", default_sale_price="90.00", is_stock_tracked=False)
        save_ingredients(kebab, [(self.rice, "0.100")], self.owner)
        kitchen.make_in_kitchen(kebab, self.owner)
        make_item(item_code="R-OIL", item_name="زيت", unit="لتر", is_stock_tracked=True)  # a real raw material, none on hand
        self.assertLessEqual({self.koshary.pk, kebab.pk}, kitchen.kitchen_dish_ids())
        self.assertEqual((self.stock(self.koshary), self.stock(kebab)), (D("0.000"), D("0.000")))

        self.assertEqual(stock_alert_counts()["out_of_stock"], 1)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("inventory:stock"))
        self.assertEqual(page.context["counts"]["out"], 1)
        rows = {row["item"].item_code: row["state"] for row in page.context["rows"]}
        self.assertEqual((rows["R-OIL"], rows["D-KOSH"], rows["D-KEBAB"]), ("out", "healthy", "healthy"))


class IngredientQuantityFormat(KitchenSetup):
    """RS-07 / S2-08: a plate's quantity reads 0.2 and 0.1, as everywhere else in the app, never «0,200»."""

    def test_the_ingredient_boxes_show_period_decimals_without_trailing_zeros(self):
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            page = self.client.get(reverse("restaurant:dish", args=[self.koshary.pk]), {"lang": lang}).content.decode()
            self.assertIn('name="quantity" inputmode="decimal" value="0.2"', page)
            self.assertIn('name="quantity" inputmode="decimal" value="0.1"', page)
            self.assertNotIn('value="0,2', page)
            self.assertNotIn('value="0,1', page)
