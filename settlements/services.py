"""HG-069: the applied amount per invoice, for the screens and the four "due" totals.

``applied`` is a report-layer subtraction: ``due = remaining_due − applied``. It is never added to ``paid_now`` or to a
collections figure, because the payment behind each row is already counted there on its own payment date.
"""

from decimal import Decimal

from django.db.models import QuerySet, Sum

from .models import InvoiceSettlement, SettlementSource

ZERO = Decimal("0.00")


def _ids(invoices):
    if isinstance(invoices, QuerySet):
        return invoices.values("pk")
    return [getattr(invoice, "pk", invoice) for invoice in invoices]


def applied(invoices):
    """{invoice_id: amount settled by posted payments} for the given invoices (a queryset, ids or instances)."""

    rows = (InvoiceSettlement.objects.filter(invoice__in=_ids(invoices), customer_payment__status="posted")
            .values("invoice_id").annotate(total=Sum("amount")))
    return {row["invoice_id"]: row["total"] or ZERO for row in rows}


def applied_total(invoices):
    """The sum of ``applied`` over the invoices, as one figure for a totals row."""

    return sum(applied(invoices).values(), ZERO)


def applied_to(invoice):
    """The amount settled on one invoice (0 when none)."""

    return applied([invoice]).get(getattr(invoice, "pk", invoice), ZERO)


def settle(invoice, payment, amount, source):
    """Record that ``payment`` settles ``amount`` of ``invoice``. One row per payment; the caller checks the amount."""

    return InvoiceSettlement.objects.create(invoice=invoice, customer_payment=payment, amount=amount, source=source)


def release(invoice):
    """Remove the settlement rows of an invoice that was cancelled, returning the payments they held."""

    rows = list(InvoiceSettlement.objects.filter(invoice=invoice).select_related("customer_payment"))
    payments = [row.customer_payment for row in rows]
    InvoiceSettlement.objects.filter(pk__in=[row.pk for row in rows]).delete()
    return payments


__all__ = ["applied", "applied_total", "applied_to", "settle", "release", "SettlementSource"]
