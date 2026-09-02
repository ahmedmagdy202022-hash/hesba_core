"""Tests for the shared money/quantity display filters.

The two properties that matter most here are negative ones: the output must not
depend on the exponent the database returned, and it must not depend on the
active locale. Both are asserted explicitly rather than left implied, because
both are what the filters exist to guarantee.
"""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils.translation import override

from settings_core.models import ClientProfile
from settings_core.templatetags.hesba_format import (
    MISSING,
    currency_code,
    money,
    qty,
    unit_cost,
)


class MoneyFilterTests(SimpleTestCase):
    def test_exponent_does_not_reach_the_output(self):
        """The SQLite aggregate/column scale difference must not be visible."""

        from_aggregate = money(Decimal("5260"))
        from_column = money(Decimal("5260.00"))
        from_wider_scale = money(Decimal("5260.0000"))

        self.assertEqual(from_aggregate, "5,260.00")
        self.assertEqual(from_column, "5,260.00")
        self.assertEqual(from_wider_scale, "5,260.00")
        self.assertEqual(from_aggregate, from_column)
        self.assertEqual(from_column, from_wider_scale)

    def test_audited_cashbox_report_row_renders_consistently(self):
        """The mixed row from the HESBA-FOUNDATION-001 audit, made uniform."""

        self.assertEqual(money(Decimal("8000.00")), "8,000.00")
        self.assertEqual(money(Decimal("5260")), "5,260.00")
        self.assertEqual(money(Decimal("8606")), "8,606.00")
        self.assertEqual(money(Decimal("4654.00")), "4,654.00")

    def test_thousands_are_grouped_with_commas(self):
        self.assertEqual(money(Decimal("1234567.891")), "1,234,567.89")
        self.assertEqual(money(Decimal("1000")), "1,000.00")

    def test_negative_keeps_its_sign(self):
        self.assertEqual(money(Decimal("-1234.5")), "-1,234.50")
        self.assertEqual(money(Decimal("-0.01")), "-0.01")

    def test_zero_renders_as_an_amount_not_as_missing(self):
        self.assertEqual(money(0), "0.00")
        self.assertEqual(money(Decimal("0")), "0.00")

    def test_rounding_is_half_up(self):
        self.assertEqual(money(Decimal("0.005")), "0.01")
        self.assertEqual(money(Decimal("2.345")), "2.35")
        self.assertEqual(money(Decimal("-0.005")), "-0.01")

    def test_accepts_int_float_and_str_alike(self):
        self.assertEqual(money(1234), "1,234.00")
        self.assertEqual(money(1234.5), "1,234.50")
        self.assertEqual(money("1234.5"), "1,234.50")
        self.assertEqual(money(Decimal("1234.5")), "1,234.50")
        self.assertEqual(money(1234.5), money(Decimal("1234.5")))
        self.assertEqual(money("1234.5"), money(Decimal("1234.5")))

    def test_float_artefacts_do_not_leak(self):
        self.assertEqual(money(0.1), "0.10")
        self.assertEqual(money(1.005), "1.01")

    def test_missing_and_invalid_render_as_an_em_dash(self):
        for value in (None, "", "   ", "abc", [], {}, object(), True, False):
            with self.subTest(value=value):
                self.assertEqual(money(value), MISSING)

    def test_non_finite_values_render_as_missing(self):
        for value in ("nan", "inf", "-inf", Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value=value):
                self.assertEqual(money(value), MISSING)

    def test_em_dash_is_the_expected_character(self):
        self.assertEqual(MISSING, "—")


class QuantityFilterTests(SimpleTestCase):
    def test_stored_scale_is_not_shown_as_a_thousands_group(self):
        """The defect this filter exists to prevent: 8 units is not 8,000."""

        self.assertEqual(qty(Decimal("8.000")), "8")
        self.assertEqual(qty(Decimal("3.000")), "3")
        self.assertEqual(qty(Decimal("12.000")), "12")

    def test_real_fractions_survive(self):
        self.assertEqual(qty(Decimal("8.500")), "8.5")
        self.assertEqual(qty(Decimal("0.125")), "0.125")
        self.assertEqual(qty(Decimal("2.250")), "2.25")

    def test_whole_values_show_no_decimal_point(self):
        self.assertEqual(qty(Decimal("8")), "8")
        self.assertEqual(qty(Decimal("12500")), "12,500")
        self.assertEqual(qty(0), "0")

    def test_thousands_grouping_does_not_eat_trailing_zeros(self):
        """"12,500.000" must not be trimmed to "12,5"."""

        self.assertEqual(qty(Decimal("12500.000")), "12,500")
        self.assertEqual(qty(Decimal("1000000")), "1,000,000")

    def test_decimals_are_capped_at_three_with_half_up(self):
        self.assertEqual(qty(Decimal("0.1255")), "0.126")
        self.assertEqual(qty(Decimal("0.1234")), "0.123")

    def test_negative_keeps_its_sign(self):
        self.assertEqual(qty(Decimal("-8.500")), "-8.5")
        self.assertEqual(qty(Decimal("-12000")), "-12,000")

    def test_accepts_int_float_and_str_alike(self):
        self.assertEqual(qty(8), "8")
        self.assertEqual(qty(8.5), "8.5")
        self.assertEqual(qty("8.500"), "8.5")
        self.assertEqual(qty(8.5), qty(Decimal("8.5")))
        self.assertEqual(qty("8.5"), qty(Decimal("8.5")))

    def test_missing_and_invalid_render_as_an_em_dash(self):
        for value in (None, "", "   ", "abc", [], {}, object(), True, False):
            with self.subTest(value=value):
                self.assertEqual(qty(value), MISSING)


class UnitCostFilterTests(SimpleTestCase):
    def test_audited_four_decimal_costs_render_as_two(self):
        self.assertEqual(unit_cost(Decimal("70.0000")), "70.00")
        self.assertEqual(unit_cost(Decimal("150.0000")), "150.00")

    def test_genuine_precision_survives(self):
        self.assertEqual(unit_cost(Decimal("70.1250")), "70.125")
        self.assertEqual(unit_cost(Decimal("70.1234")), "70.1234")

    def test_never_shows_fewer_than_two_decimals(self):
        self.assertEqual(unit_cost(Decimal("1500")), "1,500.00")
        self.assertEqual(unit_cost(Decimal("70.1")), "70.10")
        self.assertEqual(unit_cost(0), "0.00")

    def test_exponent_does_not_reach_the_output(self):
        self.assertEqual(unit_cost(Decimal("70")), unit_cost(Decimal("70.0000")))

    def test_rounding_is_half_up_at_four_decimals(self):
        self.assertEqual(unit_cost(Decimal("70.00005")), "70.0001")

    def test_negative_keeps_its_sign(self):
        self.assertEqual(unit_cost(Decimal("-70.0000")), "-70.00")

    def test_accepts_int_float_and_str_alike(self):
        self.assertEqual(unit_cost(70), "70.00")
        self.assertEqual(unit_cost(70.0), "70.00")
        self.assertEqual(unit_cost("70.0000"), "70.00")
        self.assertEqual(unit_cost(70.125), unit_cost(Decimal("70.125")))

    def test_missing_and_invalid_render_as_an_em_dash(self):
        for value in (None, "", "   ", "abc", [], {}, object(), True, False):
            with self.subTest(value=value):
                self.assertEqual(unit_cost(value), MISSING)


class LocaleIndependenceTests(SimpleTestCase):
    """LANGUAGE_CODE is "ar", whose Django locale uses a decimal comma.

    The filters must ignore that entirely, in either direction.
    """

    def test_money_uses_period_and_comma_under_the_arabic_locale(self):
        with override("ar"):
            rendered = money(Decimal("1234567.891"))
        self.assertEqual(rendered, "1,234,567.89")
        self.assertIn(".", rendered)
        self.assertIn(",", rendered)
        self.assertEqual(rendered.split(".")[1], "89")

    def test_qty_uses_period_and_comma_under_the_arabic_locale(self):
        with override("ar"):
            whole = qty(Decimal("12500.000"))
            fractional = qty(Decimal("8.500"))
        self.assertEqual(whole, "12,500")
        self.assertEqual(fractional, "8.5")

    def test_unit_cost_uses_period_and_comma_under_the_arabic_locale(self):
        with override("ar"):
            rendered = unit_cost(Decimal("1500.0000"))
        self.assertEqual(rendered, "1,500.00")

    def test_output_is_identical_across_locales(self):
        with override("ar"):
            arabic = (money(Decimal("1234.5")), qty(Decimal("8.000")), unit_cost(Decimal("70.0000")))
        with override("en"):
            english = (money(Decimal("1234.5")), qty(Decimal("8.000")), unit_cost(Decimal("70.0000")))
        self.assertEqual(arabic, english)
        self.assertEqual(arabic, ("1,234.50", "8", "70.00"))


class ReturnTypeTests(SimpleTestCase):
    def test_filters_return_plain_strings(self):
        for rendered in (money(Decimal("1")), qty(Decimal("1")), unit_cost(Decimal("1")), money(None)):
            with self.subTest(rendered=rendered):
                self.assertIs(type(rendered), str)


class CurrencyCodeTagTests(TestCase):
    def test_returns_the_active_profiles_currency(self):
        ClientProfile.objects.create(
            client_code="FMT-1",
            legal_name="Hesba Legal",
            display_name="Hesba Store",
        )
        self.assertEqual(currency_code(), "EGP")

    def test_reflects_a_changed_currency(self):
        ClientProfile.objects.create(
            client_code="FMT-2",
            legal_name="Hesba Legal",
            display_name="Hesba Store",
            default_currency="USD",
        )
        self.assertEqual(currency_code(), "USD")

    def test_returns_empty_string_without_raising_when_no_profile_exists(self):
        self.assertFalse(ClientProfile.objects.exists())
        self.assertEqual(currency_code(), "")

    def test_ignores_an_inactive_profile(self):
        ClientProfile.objects.create(
            client_code="FMT-3",
            legal_name="Hesba Legal",
            display_name="Hesba Store",
            default_currency="USD",
            is_active=False,
        )
        self.assertEqual(currency_code(), "")
