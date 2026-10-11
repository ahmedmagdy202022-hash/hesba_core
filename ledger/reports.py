"""GL-002: balances, the trial balance, and the reconciliation that proves the
journal agrees with the reports Hesba already trusts."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from config.money import money_round

from .models import Account, JournalLine
from .projector import ensure_fresh

ZERO = Decimal("0")


def _lines(date_from=None, date_to=None, entity=None):
    qs = JournalLine.objects.all()
    if date_from:
        qs = qs.filter(entry__entry_date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__entry_date__lte=date_to)
    if entity is not None:
        qs = qs.filter(entity=entity)
    return qs


def balances(date_from=None, date_to=None, entity=None):
    """{account_id: (debit, credit)} for posted lines in the window."""

    rows = _lines(date_from, date_to, entity).values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))
    return {row["account_id"]: (row["d"] or ZERO, row["c"] or ZERO) for row in rows}


def control_balance(control, date_to=None, entity=None):
    """Debit-positive balance of the account(s) under a control key."""

    account = Account.objects.filter(control=control).first()
    if account is None:
        return ZERO
    agg = _lines(None, date_to, entity).filter(account=account).aggregate(d=Sum("debit"), c=Sum("credit"))
    return money_round((agg["d"] or ZERO) - (agg["c"] or ZERO))


def trial_balance(date_from=None, date_to=None, entity=None, refresh=True):
    """Every account with an opening balance, the window's movement, and the
    closing balance; group accounts roll their children up."""

    if refresh:
        ensure_fresh()
    accounts = list(Account.objects.order_by("code"))
    opening = balances(None, _day_before(date_from), entity) if date_from else {}
    movement = balances(date_from, date_to, entity)
    rows, totals = [], defaultdict(Decimal)
    by_code = {}
    for account in accounts:
        od, oc = opening.get(account.pk, (ZERO, ZERO))
        md, mc = movement.get(account.pk, (ZERO, ZERO))
        row = {"account": account, "opening": od - oc, "debit": md, "credit": mc, "closing": od - oc + md - mc}
        rows.append(row)
        by_code[account.code] = row
    # Roll leaves up into their groups (longest codes first).
    for row in sorted(rows, key=lambda r: -len(r["account"].code)):
        parent = row["account"].parent
        if parent is not None and not parent.is_postable and parent.code in by_code:
            target = by_code[parent.code]
            for key in ("opening", "debit", "credit", "closing"):
                target[key] += row[key]
    for row in rows:
        if row["account"].is_postable:
            totals["debit"] += row["debit"]
            totals["credit"] += row["credit"]
            totals["closing_debit"] += max(row["closing"], ZERO)
            totals["closing_credit"] += max(-row["closing"], ZERO)
    shown = [row for row in rows if any(row[key] for key in ("opening", "debit", "credit", "closing"))]
    return {"rows": shown, "totals": {k: money_round(v) for k, v in totals.items()},
            "balanced": money_round(totals["debit"]) == money_round(totals["credit"])}


def _day_before(day):
    from datetime import timedelta

    return day - timedelta(days=1)


def reconciliation():
    """Each control account against the report that already owns the figure.

    A difference names a document the projector could not place (it sits in
    suspense) or a report that counts something differently; both are shown.
    """

    ensure_fresh()
    from inventory.models import StockMovement
    from master_data.models import Customer, Supplier
    from reports.selectors import STOCK_IN_TYPES, STOCK_OUT_TYPES, profit_totals
    from cashboxes.models import Cashbox

    def party_total(model):
        total = ZERO
        for party in model.objects.annotate(i=Sum("ledger_entries__due_increase"), d=Sum("ledger_entries__due_decrease")):
            total += party.opening_balance + (party.i or ZERO) - (party.d or ZERO)
        return money_round(total)

    from cashboxes.models import CashboxMovement

    # The cashbox report's own formula (opening + in − out), over every cashbox.
    cash_in = CashboxMovement.objects.filter(direction="in").aggregate(t=Sum("amount"))["t"] or ZERO
    cash_out = CashboxMovement.objects.filter(direction="out").aggregate(t=Sum("amount"))["t"] or ZERO
    cash_report = money_round((Cashbox.objects.aggregate(t=Sum("opening_balance"))["t"] or ZERO) + cash_in - cash_out)
    stock_value = ZERO
    for quantity, unit_cost, movement_type in StockMovement.objects.values_list("quantity", "unit_cost", "movement_type"):
        value = money_round(quantity * unit_cost)
        stock_value += value if movement_type in STOCK_IN_TYPES else -value if movement_type in STOCK_OUT_TYPES else ZERO
    profit = profit_totals()
    inventory_controls = [c for c in ("inventory", "raw_materials", "finished_goods") if Account.objects.filter(control=c).exists()]
    revenue = -(control_balance("sales") + control_balance("sales_returns"))
    # HG-038: a customer's balance may sit partly in retention receivable and
    # customer advances, a supplier's in retention payable; together they are
    # the party ledgers.
    receivable = sum((control_balance(c) for c in ("receivable", "retention_receivable", "customer_advances")), ZERO)
    payable = sum((control_balance(c) for c in ("payable", "retention_payable")), ZERO)
    checks = [
        ("receivable", money_round(receivable), party_total(Customer)),
        ("payable", money_round(-payable), party_total(Supplier)),
        ("cash", control_balance("cash"), cash_report),
        ("inventory", money_round(sum((control_balance(c) for c in inventory_controls), ZERO)), stock_value),
        ("sales", money_round(revenue), money_round(profit["sales"])),
        ("cogs", control_balance("cogs"), money_round(profit["cost"])),
        ("suspense", control_balance("suspense"), ZERO),
    ]
    rows = [{"key": key, "ledger": ledger, "report": report, "difference": money_round(ledger - report)} for key, ledger, report in checks]
    agg = JournalLine.objects.aggregate(d=Sum("debit"), c=Sum("credit"))
    return {"rows": rows, "ok": all(not row["difference"] for row in rows),
            "balanced": money_round(agg["d"] or ZERO) == money_round(agg["c"] or ZERO)}
