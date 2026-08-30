from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANT = Decimal("0.01")
COST_QUANT = Decimal("0.0001")


def money_round(value):
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def cost_round(value):
    return Decimal(value).quantize(COST_QUANT, rounding=ROUND_HALF_UP)


def allocate_proportionally(total, weights):
    """Allocate money by weight and put the final cent residual last.

    This is deterministic and guarantees that allocated values add back to the
    rounded source total. Callers must provide at least one positive weight.
    """

    rounded_total = money_round(total)
    decimal_weights = [Decimal(weight) for weight in weights]
    if not decimal_weights:
        return []
    if rounded_total < 0:
        raise ValueError("Proportional allocation requires a nonnegative total.")
    if any(weight < 0 for weight in decimal_weights):
        raise ValueError("Proportional allocation weights cannot be negative.")
    weight_total = sum(decimal_weights, Decimal("0"))
    if weight_total <= 0:
        raise ValueError("Proportional allocation requires a positive total weight.")

    allocations = []
    allocated = Decimal("0")
    for index, weight in enumerate(decimal_weights):
        if index == len(decimal_weights) - 1:
            share = rounded_total - allocated
        else:
            remaining = rounded_total - allocated
            share = min(money_round(rounded_total * weight / weight_total), remaining)
            allocated += share
        allocations.append(share)
    return allocations
