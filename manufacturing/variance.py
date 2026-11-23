"""Manufacturing F12 (HG-132): the variance report, ``/manufacturing/costs/``. Reading only.

For each finished order (its run posted) in a period, by the run's date:

* per material, the **standard** (``OrderStandardLine``, written when the order started: the recipe's quantity × the
  order's batches at the cost it was issued at) against the **actual** (issues + extras − returns, each at the cost
  it moved at). An order started before F12 has no standard rows: its start's issue rows are the same figures (and
  for one started before HG-036, its run's consumption). Never the recipe as it is today (trap 17);
* labour and overhead **planned** (typed when the order was opened) against **actual** (each kind's live lines, or the
  planned figure when it has none, which is what the finish absorbed);
* **yield**: good units against the order's quantity;
* **unit cost** against the standard: (standard materials + planned labour + planned overhead) ÷ the order's quantity.

And one line per month: what the orders **absorbed** into stock (absorbed production cost, 5102) against what was
**paid** (wages 5201, plus rent 5202, utilities 5203 and maintenance 5206 as the factory's overhead), so absorbing
more than was paid is visible (trap 11). It reads the journal of the entity being worked in; on a chart with no
absorbed-cost account (a restaurant with the module on) the line is not shown.
"""

from datetime import date, timedelta
from decimal import Decimal

from config.money import cost_round, money_round

from .actuals import conversion, sign
from .models import IssueKind, OrderStatus, ProductionOrder

ZERO = Decimal("0")
#: The accounts the month line counts as paid: wages, then the factory's overhead (rent, utilities, maintenance).
PAID_WAGES = ("5201",)
PAID_OVERHEAD = ("5202", "5203", "5206")


def standard(order):
    """[(item, standard quantity, standard unit cost)] of ``order``."""

    rows = list(order.standard_lines.select_related("component"))
    if rows:
        return [(row.component, row.quantity, row.unit_cost) for row in rows]
    operations = [issue.operation for issue in order.issues.filter(kind=IssueKind.ISSUE).select_related("operation__item")]
    if not operations and order.run_id:  # started before HG-036: the run consumed the recipe at the finish
        operations = [row.operation for row in order.run.consumptions.select_related("operation__item")]
    return [(operation.item, operation.quantity, operation.unit_cost) for operation in operations]


def _actual(order):
    operations = [issue.operation for issue in order.issues.select_related("operation__item")]
    if not operations and order.run_id:
        operations = [row.operation for row in order.run.consumptions.select_related("operation__item")]
    return operations


def order_variance(order):
    materials = {}

    def row(item):
        return materials.setdefault(item.pk, {"item": item, "std_qty": ZERO, "std_value": ZERO, "qty": ZERO, "value": ZERO})

    for item, quantity, unit_cost in standard(order):
        line = row(item)
        line["std_qty"] += quantity
        line["std_value"] += money_round(quantity * unit_cost)
    for operation in _actual(order):
        line = row(operation.item)
        line["qty"] += sign(operation) * operation.quantity
        line["value"] += sign(operation) * money_round(operation.quantity * operation.unit_cost)
    for line in materials.values():
        line["diff_qty"], line["diff_value"] = line["qty"] - line["std_qty"], line["value"] - line["std_value"]
    spent = conversion(order)
    std_material = sum((line["std_value"] for line in materials.values()), ZERO)
    # The sum of the lines (each at its own journal value), so the header's variance is the sum of the lines' variances.
    material = sum((line["value"] for line in materials.values()), ZERO)
    good = order.run.output_quantity if order.run_id else ZERO
    # Manufacturing F9 (HG-139): the product's own cost is what the by-products and co-products left of the order's cost,
    # so the standard leaves them the same amount (no standard is kept for them); 0 for an order without them.
    extras = sum((row.cost for row in order.run.outputs.all()), ZERO) if order.run_id else ZERO
    standard_unit = cost_round((std_material + order.labor_cost + order.overhead_cost - extras) / order.quantity) if order.quantity else ZERO
    unit = order.run.output_operation.unit_cost if order.run_id else ZERO
    return {
        "order": order, "materials": list(materials.values()),
        "std_material": std_material, "material": material, "diff_material": material - std_material,
        "planned_labour": order.labor_cost, "labour": spent["labour"], "diff_labour": spent["labour"] - order.labor_cost, "labour_actual": spent["labour_actual"],
        "planned_overhead": order.overhead_cost, "overhead": spent["overhead"], "diff_overhead": spent["overhead"] - order.overhead_cost,
        "overhead_actual": spent["overhead_actual"], "outside": spent["outside"],
        "planned_units": order.quantity, "good": good, "diff_units": good - order.quantity,
        "standard_unit": standard_unit, "unit": unit, "diff_unit": unit - standard_unit,
    }


def finished_orders(date_from, date_to):
    from entities import scope as entity_scope

    return (entity_scope.scope(ProductionOrder.objects, "location__entity")
            .filter(status=OrderStatus.DONE, run__status="posted", run__run_date__gte=date_from, run__run_date__lte=date_to)
            .select_related("recipe__product", "location", "run__output_operation").order_by("run__run_date", "pk"))


def report(date_from, date_to):
    rows = [order_variance(order) for order in finished_orders(date_from, date_to)]
    keys = ("std_material", "material", "planned_labour", "labour", "planned_overhead", "overhead", "outside", "planned_units", "good")
    totals = {key: sum((row[key] for row in rows), ZERO) for key in keys}
    for left, right, name in (("std_material", "material", "diff_material"), ("planned_labour", "labour", "diff_labour"),
                              ("planned_overhead", "overhead", "diff_overhead"), ("planned_units", "good", "diff_units")):
        totals[name] = totals[right] - totals[left]
    return {"rows": rows, "totals": totals, "months": month_lines(date_from, date_to)}


def _months(date_from, date_to):
    first = date_from.replace(day=1)
    while first <= date_to:
        following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield first, following - timedelta(days=1)
        first = following


def month_lines(date_from, date_to):
    """[{month, absorbed, wages, overhead, paid, difference}] or None on a chart without absorbed production cost."""

    from django.db.models import Sum

    from entities.current import current_entity
    from ledger.models import Account, JournalLine
    from ledger.projector import ensure_fresh

    ensure_fresh()  # also lays out the chart on a first visit
    absorbed_account = Account.objects.filter(control="production_cost").first()
    if absorbed_account is None:
        return None
    entity = current_entity()

    def total(accounts, start, end):
        lines = JournalLine.objects.filter(account__in=accounts, entry__entry_date__gte=start, entry__entry_date__lte=end)
        if entity is not None:
            lines = lines.filter(entity=entity)
        agg = lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        return money_round((agg["d"] or ZERO) - (agg["c"] or ZERO))

    wages = list(Account.objects.filter(code__in=PAID_WAGES))
    overhead = list(Account.objects.filter(code__in=PAID_OVERHEAD))
    months = []
    for start, end in _months(date_from, date_to):
        absorbed = -total([absorbed_account], max(start, date_from), min(end, date_to))
        paid_wages = total(wages, max(start, date_from), min(end, date_to))
        paid_overhead = total(overhead, max(start, date_from), min(end, date_to))
        months.append({"month": start, "absorbed": absorbed, "wages": paid_wages, "overhead": paid_overhead,
                       "paid": paid_wages + paid_overhead, "difference": absorbed - paid_wages - paid_overhead})
    return months


def period(raw_from, raw_to, today=None):
    """The period asked for (this month by default); a date that cannot be read falls back, and from ≤ to."""

    today = today or date.today()

    def read(raw):
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    date_from = read(raw_from) or today.replace(day=1)
    date_to = read(raw_to) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to
