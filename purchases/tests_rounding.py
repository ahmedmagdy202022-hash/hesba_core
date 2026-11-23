from decimal import Decimal

from django.test import SimpleTestCase

from config.money import allocate_proportionally


class ProportionalAllocationTests(SimpleTestCase):
    def test_rounding_overshoot_is_capped_without_a_negative_final_share(self):
        allocations = allocate_proportionally(Decimal("0.02"), [1, 1, 1, 1])

        self.assertTrue(all(amount >= 0 for amount in allocations))
        self.assertEqual(sum(allocations, Decimal("0")), Decimal("0.02"))

    def test_negative_total_or_weight_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "nonnegative total"):
            allocate_proportionally(Decimal("-0.01"), [1])
        with self.assertRaisesMessage(ValueError, "cannot be negative"):
            allocate_proportionally(Decimal("1.00"), [1, -1])
