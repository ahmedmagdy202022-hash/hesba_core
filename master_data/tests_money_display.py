"""Money cells in the master-data list tables.

These rows are built in Python rather than rendered by a template filter,
because ``master_data/list.html`` prints one generic ``{{ value }}`` per cell
and cannot know which of them is an amount. That made ``_rows`` the last
place in the codebase formatting money with its own f-string, and an
f-string rounds a Decimal HALF_EVEN while the rest of the system rounds
HALF_UP. The tests below pin the rows to the shared filter's behaviour.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from .models import Customer, Item
from .views import STRINGS, _rows


WORDS = STRINGS["ar"]


def _customer_row(credit_limit):
    customer = Customer(
        customer_code="C-1",
        name="عميل",
        phone="0100",
        credit_limit=credit_limit,
    )
    return _rows("customers", [customer], "ar", WORDS, can_view_cost=False)[0]["values"]


def _item_row(sale_price, purchase_price=Decimal("0"), can_view_cost=False):
    item = Item(
        item_code="I-1",
        item_name="صنف",
        unit="unit",
        default_sale_price=sale_price,
        default_purchase_price=purchase_price,
    )
    return _rows("items", [item], "ar", WORDS, can_view_cost=can_view_cost)[0]["values"]


class CustomerCreditLimitCellTests(SimpleTestCase):
    """The credit limit column on the customers list."""

    def test_it_shows_two_decimals_with_comma_thousands(self):
        self.assertEqual(_customer_row(Decimal("8000"))[3], "8,000.00")

    def test_a_fractional_limit_keeps_its_piastres(self):
        self.assertEqual(_customer_row(Decimal("1234.56"))[3], "1,234.56")

    def test_an_exact_half_rounds_up_not_to_even(self):
        """The f-string this replaced gave "0.12" — HALF_EVEN, against policy."""

        self.assertEqual(_customer_row(Decimal("0.125"))[3], "0.13")

    def test_a_second_exact_half_also_rounds_up(self):
        self.assertEqual(_customer_row(Decimal("10.045"))[3], "10.05")

    def test_a_value_rounding_away_from_below_carries_no_minus(self):
        self.assertEqual(_customer_row(Decimal("-0.001"))[3], "0.00")

    def test_a_genuine_negative_keeps_its_sign(self):
        self.assertEqual(_customer_row(Decimal("-25.50"))[3], "-25.50")

    def test_the_stored_exponent_does_not_reach_the_cell(self):
        limits = [Decimal("5260"), Decimal("5260.00"), Decimal("5260.0000")]
        rendered = {_customer_row(limit)[3] for limit in limits}
        self.assertEqual(rendered, {"5,260.00"})

    def test_a_missing_limit_does_not_raise(self):
        """The old f-string raised TypeError on None and took the page with it."""

        self.assertEqual(_customer_row(None)[3], "—")

    def test_the_other_cells_are_left_alone(self):
        values = _customer_row(Decimal("500"))
        self.assertEqual(values[0], "C-1")
        self.assertEqual(values[1], "عميل")
        self.assertEqual(values[2], "0100")
        self.assertEqual(values[4], WORDS["active"])


class ItemPriceCellTests(SimpleTestCase):
    """The sale-price column, and the cost column behind can_view_cost."""

    def test_the_sale_price_shows_two_decimals(self):
        self.assertEqual(_item_row(Decimal("70"))[5], "70.00")

    def test_an_exact_half_sale_price_rounds_up(self):
        self.assertEqual(_item_row(Decimal("1.005"))[5], "1.01")

    def test_a_missing_sale_price_does_not_raise(self):
        self.assertEqual(_item_row(None)[5], "—")

    def test_without_the_cost_permission_there_is_no_purchase_cell(self):
        values = _item_row(Decimal("70"), Decimal("55"), can_view_cost=False)
        self.assertEqual(len(values), 7)
        self.assertEqual(values[6], WORDS["active"])
        self.assertNotIn("55.00", values)

    def test_with_the_cost_permission_the_purchase_price_is_formatted_too(self):
        values = _item_row(Decimal("55.005"), Decimal("55.005"), can_view_cost=True)
        self.assertEqual(len(values), 8)
        self.assertEqual(values[6], "55.01")
        self.assertEqual(values[7], WORDS["active"])

    def test_the_non_money_cells_are_left_alone(self):
        values = _item_row(Decimal("70"))
        self.assertEqual(values[0], "I-1")
        self.assertEqual(values[1], "صنف")
        self.assertEqual(values[2], "—")
        self.assertEqual(values[3], "unit")
        self.assertEqual(values[4], WORDS["stock_item"])


class SharedFilterAgreementTests(SimpleTestCase):
    """A master-data cell and a template cell must never disagree."""

    def test_a_row_cell_matches_the_template_filter_exactly(self):
        from settings_core.templatetags.hesba_format import money

        for raw in ["0.125", "1.005", "8000", "1234.56", "-0.001", "10.045"]:
            value = Decimal(raw)
            with self.subTest(value=raw):
                self.assertEqual(_customer_row(value)[3], money(value))
