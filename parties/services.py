"""PARTY-001: one customer's or supplier's account, read straight from its ledger.

Read-only. The balance here is exactly the one the customer and supplier
reports show (opening balance + due increases - due decreases), because both
read the same ledger rows; a statement for a period starts from the balance
brought forward to its first day.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Max, Sum

from config.money import money_round
from master_data.models import Customer, Supplier
from purchases.models import PurchaseInvoice, SupplierLedgerEntry, SupplierPayment
from sales.models import CustomerLedgerEntry, CustomerPayment, SalesInvoice


ZERO = Decimal("0.00")

KINDS = {
    "customer": {
        "model": Customer, "entry": CustomerLedgerEntry, "fk": "customer",
        "refs": (("sales_invoice", "invoice_number", "sales:detail"), ("customer_payment", "payment_number", None),
                 ("sales_return", "return_number", "sales:return_detail"), ("opening_balance_adjustment", "adjustment_number", None)),
    },
    "supplier": {
        "model": Supplier, "entry": SupplierLedgerEntry, "fk": "supplier",
        "refs": (("purchase_invoice", "invoice_number", "purchases:detail"), ("supplier_payment", "payment_number", None),
                 ("purchase_return", "return_number", "purchases:return_detail"), ("opening_balance_adjustment", "adjustment_number", None)),
    },
}


def _entries(kind, party):
    spec = KINDS[kind]
    return spec["entry"].objects.filter(**{spec["fk"]: party})


def _totals(queryset):
    sums = queryset.aggregate(inc=Sum("due_increase"), dec=Sum("due_decrease"))
    return (sums["inc"] or ZERO), (sums["dec"] or ZERO)


def balance(kind, party, as_of=None):
    entries = _entries(kind, party)
    if as_of is not None:
        entries = entries.filter(entry_date__lte=as_of)
    inc, dec = _totals(entries)
    return money_round(party.opening_balance + inc - dec)


def _reference(entry, kind):
    for field, number_attr, url_name in KINDS[kind]["refs"]:
        document = getattr(entry, field, None)
        if document is not None:
            return {"number": getattr(document, number_attr, ""), "url_name": url_name, "pk": document.pk}
    return None


def statement(kind, party, date_from=None, date_to=None):
    """Brought-forward balance, dated rows with a running balance, and the closing balance."""

    spec = KINDS[kind]
    entries = _entries(kind, party).select_related(*[field for field, *_ in spec["refs"]]).order_by("entry_date", "id")
    before = entries.filter(entry_date__lt=date_from) if date_from else entries.none()
    inc_before, dec_before = _totals(before)
    opening = money_round(party.opening_balance + inc_before - dec_before)
    window = entries
    if date_from:
        window = window.filter(entry_date__gte=date_from)
    if date_to:
        window = window.filter(entry_date__lte=date_to)
    running, rows, total_inc, total_dec = opening, [], ZERO, ZERO
    for entry in window:
        running = money_round(running + entry.due_increase - entry.due_decrease)
        total_inc += entry.due_increase
        total_dec += entry.due_decrease
        rows.append({"entry": entry, "reference": _reference(entry, kind), "increase": entry.due_increase, "decrease": entry.due_decrease, "balance": running})
    return {"opening": opening, "rows": rows, "increase": money_round(total_inc), "decrease": money_round(total_dec), "closing": running}


def card(kind, party, today=None):
    """What the card shows at a glance."""

    today = today or date.today()
    if kind == "customer":
        documents = SalesInvoice.objects.filter(customer=party, status="posted")
        payments = CustomerPayment.objects.filter(customer=party, status="posted")
    else:
        documents = PurchaseInvoice.objects.filter(supplier=party, status="posted")
        payments = SupplierPayment.objects.filter(supplier=party, status="posted")
    year = documents.filter(invoice_date__year=today.year).aggregate(total=Sum("total_amount"))["total"] or ZERO
    return {
        "balance": balance(kind, party),
        "year_total": money_round(year),
        "invoice_count": documents.count(),
        "last_invoice": documents.order_by("-invoice_date", "-id").first(),
        "last_payment": payments.order_by("-payment_date", "-id").first(),
        "recent_invoices": list(documents.order_by("-invoice_date", "-id")[:8]),
        "recent_payments": list(payments.order_by("-payment_date", "-id")[:8]),
        "last_activity": _entries(kind, party).aggregate(last=Max("entry_date"))["last"],
    }
