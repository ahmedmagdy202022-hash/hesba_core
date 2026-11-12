"""Watch round 2 fixes for loyalty: BE-10 (the rule boxes show 10.00 and 0.10, never 10,00 and 0,10)."""

from decimal import Decimal as D

from django.urls import reverse

from .models import LoyaltySettings
from .tests import LoyaltySetup


class RuleBoxesTests(LoyaltySetup):
    def test_the_rule_boxes_show_period_decimals_in_both_languages(self):
        LoyaltySettings.objects.update_or_create(pk=1, defaults={"amount_per_point": D("10.00"), "point_value": D("0.10"), "min_points": 20, "expiry_months": 0})
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            page = self.client.get(reverse("loyalty:home"), {"lang": lang}).content.decode()
            self.assertIn('name="amount_per_point" inputmode="decimal" dir="ltr" required value="10.00"', page)
            self.assertIn('name="point_value" inputmode="decimal" dir="ltr" required value="0.10"', page)
            self.assertNotIn('value="10,00"', page)
            self.assertNotIn('value="0,10"', page)

    def test_a_saved_rule_comes_back_the_way_it_was_typed(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("loyalty:home"), {"lang": "ar", "amount_per_point": "12.5", "point_value": "0.25", "min_points": "30", "expiry_months": "6"})
        page = self.client.get(reverse("loyalty:home"), {"lang": "ar"}).content.decode()
        self.assertIn('value="12.50"', page)
        self.assertIn('value="0.25"', page)
