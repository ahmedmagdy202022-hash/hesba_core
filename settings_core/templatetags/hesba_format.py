"""Display-only formatting for money, quantity and currency.

Two things outside this module currently decide how a bare ``{{ value }}``
looks, and neither of them is a display decision. The first is the active
locale: LANGUAGE_CODE is "ar", and Django's Arabic locale renders a decimal
comma. The second is the exponent the database happened to return: the SQLite
backend quantizes plain columns to their field scale but leaves aggregate
expressions alone, so one report row can carry Decimal("8000.00") next to
Decimal("5260") and render "8000,00" beside "5260".

These filters remove both variables. Formatting is written out in explicit
Python rather than delegated to number_format, floatformat or humanize, so the
output cannot follow LANGUAGE_CODE; and every value is quantized on the way in,
so the incoming exponent cannot reach the output. The same input renders the
same way on SQLite and PostgreSQL, under any locale.

Display only. Rounding is ROUND_HALF_UP, matching config.money, so a rendered
figure can never disagree with the system's own rounding policy — but the
result is a string and must never re-enter a calculation path.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template
from django.db import Error as DatabaseError

from config.money import COST_QUANT, MONEY_QUANT
from settings_core.models import ClientProfile


register = template.Library()


# A value we cannot show is shown as absent. Rendering it as "0.00" would be a
# claim about an amount that nobody made.
MISSING = "—"  # em dash

# Quantity carries at most three decimals anywhere in the schema
# (inventory.StockMovement.quantity), so three is also the display cap.
QTY_QUANT = Decimal("0.001")


def _to_decimal(value):
    """Coerce to a finite Decimal, or None when the value cannot be shown.

    Returning None rather than raising is deliberate: a filter that raises
    takes down a whole page over one unexpected row.
    """

    # bool is a subclass of int, and "True" is not an amount.
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        # Via str(): Decimal(0.1) carries the binary artefact, Decimal("0.1")
        # does not.
        number = Decimal(str(value))
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None

    # "nan" and "inf" parse as Decimals but cannot be quantized or grouped.
    return number if number.is_finite() else None


def _quantized(value, quantum):
    """Coerce and round to a fixed scale, or None when that is not possible."""

    number = _to_decimal(value)
    if number is None:
        return None
    try:
        number = number.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # A magnitude beyond the decimal context's precision. Nothing in the
        # schema reaches this (max_digits tops out at 16), but a filter must
        # not raise.
        return None

    # A tiny negative that rounds away leaves Decimal("-0.00"), which formats
    # as "-0.00" and reads as a real negative amount. Only the sign is dropped,
    # and only once the value is genuinely zero — Decimal("-0.00") == 0 is
    # True, so anything that survives this check is a number worth a minus.
    return abs(number) if number == 0 else number


def _split(text):
    """Split a formatted fixed-point string into whole and fractional halves.

    Stripping trailing zeros from the whole string would turn "12,500.000" into
    "12,5", so the fractional part is always isolated before it is trimmed.
    """

    whole, _, fraction = text.partition(".")
    return whole, fraction


@register.filter
def money(value):
    """Money with comma thousands, period decimal, always two decimals.

    Decimal("5260"), Decimal("5260.00") and Decimal("5260.0000") all render as
    "5,260.00": the incoming exponent has no effect on the output.
    """

    number = _quantized(value, MONEY_QUANT)
    if number is None:
        return MISSING
    return f"{number:,.2f}"


@register.filter
def qty(value):
    """Quantity with comma thousands, period decimal, no trailing zeros.

    A stored Decimal("8.000") is eight units, and under the Arabic locale a
    bare render shows it as "8,000" — indistinguishable from eight thousand.
    This renders it as "8".
    """

    number = _quantized(value, QTY_QUANT)
    if number is None:
        return MISSING
    whole, fraction = _split(f"{number:,.3f}")
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


@register.filter
def unit_cost(value):
    """Unit cost at up to four decimals, never fewer than two.

    Costs are stored at four decimals, so Decimal("70.0000") renders as "70.00"
    rather than "70,0000", while genuine precision survives: "70.125".
    """

    number = _quantized(value, COST_QUANT)
    if number is None:
        return MISSING
    whole, fraction = _split(f"{number:,.4f}")
    fraction = fraction.rstrip("0").ljust(2, "0")
    return f"{whole}.{fraction}"


@register.simple_tag
def currency_code():
    """This installation's currency code, or "" before bootstrap."""

    try:
        profile = ClientProfile.get_active()
    except DatabaseError:
        return ""
    return profile.default_currency if profile is not None else ""
