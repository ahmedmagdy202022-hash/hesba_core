"""PERF-001: sales-rep performance, read from posted documents only.

Per salesperson, for a period and the entity being worked in (HG-034):
net sales (before VAT, after returns), returns, gross profit, invoice count,
average invoice, what was collected (cash taken on their invoices plus
payments from the customers they look after), credit still open on their
customers, and the commission on net sales at the employee's rate.

Invoices with no salesperson form their own row, so the rows always add up
to the profit report's sales and profit for the same window.
"""

from decimal import Decimal

from django.db.models import Sum

from config.money import money_round
from entities import scope as entity_scope
from sales.models import CustomerPayment, SalesInvoice, SalesLine, SalesReturn
from staff.models import Employee
from taxes.services import returns_tax

ZERO = Decimal("0.00")
HIGH_RETURNS = Decimal("10")      # % of gross sales
LOW_COLLECTION = Decimal("50")    # % of what they sold


def _sum(queryset, field):
    return queryset.aggregate(t=Sum(field))["t"] or ZERO


def _row(rep, date_from, date_to, base_invoices, base_returns, base_payments):
    invoices = base_invoices.filter(salesperson=rep)
    returns = base_returns.filter(source_invoice__salesperson=rep)
    lines = SalesLine.objects.filter(invoice__in=invoices)
    # Revenue as the profit report counts it: what each line earned before VAT.
    lines_profit = _sum(lines, "line_profit_amount")
    gross = lines_profit + _sum(lines, "line_cost_amount")
    returned = _sum(returns, "total_amount") - returns_tax(returns)
    returned_cost = _sum(returns, "cost_amount")
    count = invoices.count()
    net = money_round(gross - returned)
    paid_on_invoices = _sum(invoices, "paid_now")
    payments = base_payments.filter(customer__sales_rep=rep) if rep is not None else base_payments.none()
    collected = money_round(paid_on_invoices + _sum(payments, "amount"))
    sold = _sum(invoices, "total_amount")
    rate = rep.commission_percent if rep is not None else ZERO
    row = {
        "rep": rep,
        "invoices": count,
        "gross_sales": money_round(gross),
        "returns": money_round(returned),
        "net_sales": net,
        "gross_profit": money_round(lines_profit - (returned - returned_cost)),
        "average_invoice": money_round(net / count) if count else ZERO,
        "collected": collected,
        "credit_sold": money_round(_sum(invoices, "remaining_due")),
        "commission_rate": rate,
        "commission": money_round(net * rate / Decimal("100")),
        "return_rate": money_round(returned * 100 / gross) if gross else ZERO,
        "collection_rate": money_round(collected * 100 / sold) if sold else ZERO,
        "customers": rep.customers.filter(active=True).count() if rep is not None else 0,
    }
    row["alerts"] = _alerts(row)
    return row


def _alerts(row):
    """Explained, not just flagged: each alert says what it compared."""

    alerts = []
    if row["gross_sales"] and row["return_rate"] >= HIGH_RETURNS:
        alerts.append(("high_returns", row["return_rate"], HIGH_RETURNS))
    if row["gross_sales"] and row["collection_rate"] < LOW_COLLECTION:
        alerts.append(("low_collection", row["collection_rate"], LOW_COLLECTION))
    if row["rep"] is not None and not row["invoices"] and row["customers"]:
        alerts.append(("no_sales", row["customers"], None))
    return alerts


def rep_performance(date_from, date_to):
    invoices = entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE).filter(
        status="posted", invoice_date__gte=date_from, invoice_date__lte=date_to
    )
    returns = entity_scope.scope(SalesReturn.objects, entity_scope.SALES_RETURN).filter(
        status="posted", return_date__gte=date_from, return_date__lte=date_to
    )
    payments = entity_scope.scope(CustomerPayment.objects, entity_scope.CUSTOMER_PAYMENT).filter(
        status="posted", payment_date__gte=date_from, payment_date__lte=date_to
    )
    involved = set(invoices.exclude(salesperson=None).values_list("salesperson", flat=True))
    involved |= set(returns.exclude(source_invoice__salesperson=None).values_list("source_invoice__salesperson", flat=True))
    reps = Employee.objects.filter(active=True) | Employee.objects.filter(pk__in=involved)
    rows = [_row(rep, date_from, date_to, invoices, returns, payments) for rep in reps.distinct().order_by("name")]
    rows = [row for row in rows if row["invoices"] or row["returns"] or row["customers"] or row["rep"].pk in involved]
    unassigned = _row(None, date_from, date_to, invoices.filter(salesperson=None), returns.filter(source_invoice__salesperson=None), payments.none())
    if unassigned["invoices"] or unassigned["returns"]:
        rows.append(unassigned)
    rows.sort(key=lambda row: (row["rep"] is None, -row["net_sales"]))
    totals = {key: money_round(sum((row[key] for row in rows), ZERO)) for key in
              ("gross_sales", "returns", "net_sales", "gross_profit", "collected", "credit_sold", "commission")}
    totals["invoices"] = sum(row["invoices"] for row in rows)
    return rows, totals

