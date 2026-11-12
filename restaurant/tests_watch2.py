"""Watch round 2 fixes for the restaurant: RS-01 and RS-07 (a long option text must not widen a phone page).

The real check is a browser at 390 px (`document.documentElement.scrollWidth === 390`); these tests pin the rules that make it
true so a later stylesheet edit cannot undo them without a test failing.
"""

import re
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from .tests import RestaurantSetup

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
