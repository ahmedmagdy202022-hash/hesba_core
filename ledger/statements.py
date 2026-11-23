"""FS-001: the income statement, balance sheet and cash-flow statement, read
from the projected journal (GL-002) for one entity or the whole group.

Consolidation needs no separate elimination step: intercompany lines from
`Projector.balance_entities` sit on one account (1109), so across all
entities they net to zero by construction.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from config.money import money_round

from .models import Account
from .projector import ensure_fresh
from .reports import balances

ZERO = Decimal("0")

# Sections by the account tree's second level; user accounts always sit under these groups.
INCOME_SECTIONS = (
    ("revenue", ("41",), -1),
    ("cost_of_sales", ("51",), 1),
    ("operating_expenses", ("52",), 1),
    ("other_income", ("42",), -1),
)
BALANCE_SECTIONS = (
    ("current_assets", ("11",), 1),
    ("fixed_assets", ("12",), 1),
    ("current_liabilities", ("21",), -1),
    ("long_term_liabilities", ("22",), -1),
    ("equity", ("3",), -1),
)
CASH_CONTROLS = ("cash", "bank")


def _accounts():
    return list(Account.objects.order_by("code"))


def _under(account, prefixes):
    return any(account.code.startswith(prefix) for prefix in prefixes)


def _net(bal, account):
    debit, credit = bal.get(account.pk, (ZERO, ZERO))
    return debit - credit


def _cash_codes(accounts):
    roots = [a.code for a in accounts if a.control in CASH_CONTROLS]
    return {a.pk for a in accounts if any(a.code.startswith(code) for code in roots)}


def _section(accounts, bal, prefixes, sign, exclude=()):
    lines = []
    for account in accounts:
        if not account.is_postable or not _under(account, prefixes) or account.pk in exclude:
            continue
        amount = money_round(_net(bal, account) * sign)
        if amount:
            lines.append({"account": account, "amount": amount})
    return {"lines": lines, "total": money_round(sum((line["amount"] for line in lines), ZERO))}


def _profit(sections):
    s = {key: sections[key]["total"] for key in sections}
    gross = s["revenue"] - s["cost_of_sales"]
    operating = gross - s["operating_expenses"]
    return {"gross_profit": money_round(gross), "operating_profit": money_round(operating),
            "net_profit": money_round(operating + s["other_income"])}


def comparison_windows(date_from, date_to):
    """The previous period of the same length, and the same period last year."""

    if not (date_from and date_to):
        return {}
    length = (date_to - date_from).days
    previous_to = date_from - timedelta(days=1)

    def year_back(day):
        try:
            return day.replace(year=day.year - 1)
        except ValueError:  # 29 February
            return day.replace(year=day.year - 1, day=28)

    return {"previous": (previous_to - timedelta(days=length), previous_to),
            "last_year": (year_back(date_from), year_back(date_to))}


def income_statement(date_from=None, date_to=None, entity=None, compare=True, refresh=True):
    if refresh:
        ensure_fresh()
    accounts = _accounts()

    def build(start, end):
        bal = balances(start, end, entity)
        sections = {key: _section(accounts, bal, prefixes, sign) for key, prefixes, sign in INCOME_SECTIONS}
        return {"sections": sections, **_profit(sections)}

    current = build(date_from, date_to)
    current["window"] = (date_from, date_to)
    if compare:
        current["comparisons"] = {name: dict(build(*window), window=window)
                                  for name, window in comparison_windows(date_from, date_to).items()}
    return current


def net_profit(date_from=None, date_to=None, entity=None):
    return income_statement(date_from, date_to, entity, compare=False, refresh=False)["net_profit"]


def balance_sheet(as_of=None, entity=None, refresh=True):
    """Position at the end of `as_of`. Profit not yet closed into retained
    earnings (Hesba posts no closing entries) shows as its own equity line."""

    if refresh:
        ensure_fresh()
    accounts = _accounts()
    bal = balances(None, as_of, entity)
    sections = {key: _section(accounts, bal, prefixes, sign) for key, prefixes, sign in BALANCE_SECTIONS}
    earnings = net_profit(None, as_of, entity)
    sections["equity"]["total"] = money_round(sections["equity"]["total"] + earnings)
    assets = money_round(sections["current_assets"]["total"] + sections["fixed_assets"]["total"])
    liabilities = money_round(sections["current_liabilities"]["total"] + sections["long_term_liabilities"]["total"])
    equity = sections["equity"]["total"]
    return {"sections": sections, "unclosed_earnings": earnings, "total_assets": assets, "total_liabilities": liabilities,
            "total_equity": equity, "total_liabilities_and_equity": money_round(liabilities + equity),
            "balanced": assets == money_round(liabilities + equity), "as_of": as_of}


def cash_flow(date_from=None, date_to=None, entity=None, refresh=True):
    """Indirect method. Every balance-sheet movement in the window is placed
    in one activity, so the three activities add up to the change in cash
    and bank by double entry; `reconciles` proves it."""

    if refresh:
        ensure_fresh()
    accounts = _accounts()
    by_control = {a.control: a for a in accounts if a.control}
    cash_ids = _cash_codes(accounts)
    movement = balances(date_from, date_to, entity)
    opening_bal = balances(None, date_from - timedelta(days=1), entity) if date_from else {}
    closing_bal = balances(None, date_to, entity)

    profit = net_profit(date_from, date_to, entity)

    def moved(account):
        return _net(movement, account)

    depreciation = money_round(moved(by_control["depreciation"])) if "depreciation" in by_control else ZERO
    disposal = money_round(-moved(by_control["asset_disposal"])) if "asset_disposal" in by_control else ZERO
    accumulated = by_control.get("accumulated_depreciation")

    operating, investing, financing = [], [], []
    for account in accounts:
        if not account.is_postable or account.pk in cash_ids:
            continue
        change = moved(account)
        if not change or account.code[0] not in "123":
            continue
        amount = -change  # more assets uses cash; more liabilities or equity brings it
        if accumulated is not None and account.pk == accumulated.pk:
            amount -= depreciation  # the depreciation itself is added back above
            if amount:
                investing.append({"account": account, "amount": money_round(amount)})
            continue
        line = {"account": account, "amount": money_round(amount)}
        if _under(account, ("11", "21")):
            operating.append(line)
        elif _under(account, ("12",)):
            investing.append(line)
        else:
            financing.append(line)

    total = lambda lines: money_round(sum((line["amount"] for line in lines), ZERO))  # noqa: E731
    operating_total = money_round(profit + depreciation - disposal + total(operating))
    investing_total = money_round(disposal + total(investing))
    financing_total = total(financing)
    cash_open = money_round(sum((_net(opening_bal, a) for a in accounts if a.pk in cash_ids), ZERO))
    cash_close = money_round(sum((_net(closing_bal, a) for a in accounts if a.pk in cash_ids), ZERO))
    net_change = money_round(operating_total + investing_total + financing_total)
    return {"net_profit": profit, "depreciation": depreciation, "disposal_gain": disposal,
            "operating": operating, "operating_total": operating_total,
            "investing": investing, "investing_total": investing_total,
            "financing": financing, "financing_total": financing_total,
            "net_change": net_change, "cash_opening": cash_open, "cash_closing": cash_close,
            "reconciles": money_round(cash_open + net_change) == cash_close}


def default_window(today=None):
    """This month so far: the window an owner opens the statements on."""

    today = today or timezone.localdate()
    return today.replace(day=1), today
