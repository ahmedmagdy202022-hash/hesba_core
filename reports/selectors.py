"""Read-only report queries.

Every figure here is derived from movement and ledger rows rather than stored
on a balance field, so a report can never disagree with the transactions behind
it.

These run on page load now that the dashboard reads them, so the aggregation
happens in the database. The earlier versions looped in Python and issued a
query per item, per location, or per party — correct, but one query per active
item times each active location is not something a screen can afford.
"""

from decimal import Decimal

from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from cashboxes.models import Cashbox, CashboxDirection, CashboxMovement
from inventory.models import StockMovement, StockMovementType
from master_data.models import Customer, Item, Location, Supplier
from purchases.models import PurchaseInvoice, SupplierLedgerEntry
from sales.models import CustomerLedgerEntry, SalesInvoice, SalesLine


STOCK_IN_TYPES = {
    StockMovementType.PURCHASE_IN,
    StockMovementType.SALE_RETURN_IN,
    StockMovementType.TRANSFER_IN,
    StockMovementType.ADJUSTMENT_IN,
    StockMovementType.OPENING_STOCK,
}

STOCK_OUT_TYPES = {
    StockMovementType.SALE_OUT,
    StockMovementType.PURCHASE_RETURN_OUT,
    StockMovementType.TRANSFER_OUT,
    StockMovementType.ADJUSTMENT_OUT,
}

ZERO = Decimal("0")

_QUANTITY = DecimalField(max_digits=14, decimal_places=3)
_MONEY = DecimalField(max_digits=14, decimal_places=2)


def _sum(values):
    total = Decimal("0")
    for value in values:
        total += value or Decimal("0")
    return total


def _date_filter(queryset, field_name, date_from=None, date_to=None):
    if date_from:
        queryset = queryset.filter(**{f"{field_name}__gte": date_from})
    if date_to:
        queryset = queryset.filter(**{f"{field_name}__lte": date_to})
    return queryset


def _zero_sum(expression, condition, output_field):
    """Sum that yields 0 rather than None when nothing matches."""

    return Coalesce(
        Sum(expression, filter=condition), Value(ZERO), output_field=output_field
    )


def stock_report(location=None):
    """Read-only stock by item and location from StockMovement rows.

    Item/location pairs holding nothing are left out: this answers "what is on
    the shelves", so a net of zero is not a row. Use stock_alert_counts() when
    the question is which items have run out.
    """

    locations = Location.objects.filter(active=True)
    if location is not None:
        locations = locations.filter(pk=location.pk)

    location_names = dict(locations.values_list("id", "name_ar"))
    if not location_names:
        return []

    items = {item.id: item for item in Item.objects.filter(active=True)}
    if not items:
        return []

    totals = (
        StockMovement.objects.filter(item_id__in=items, location_id__in=location_names)
        .values("item_id", "location_id")
        .annotate(
            in_qty=_zero_sum("quantity", Q(movement_type__in=STOCK_IN_TYPES), _QUANTITY),
            out_qty=_zero_sum("quantity", Q(movement_type__in=STOCK_OUT_TYPES), _QUANTITY),
        )
        .order_by("item_id", "location_id")
    )

    rows = []
    for total in totals:
        quantity = total["in_qty"] - total["out_qty"]
        if quantity == 0:
            continue
        item = items[total["item_id"]]
        rows.append(
            {
                "item_id": item.id,
                "item_label": item.search_label,
                "location_id": total["location_id"],
                "location_name": location_names[total["location_id"]],
                "quantity": quantity,
                "stock_value": quantity * item.average_cost,
                "min_stock": item.min_stock,
            }
        )
    return rows


def stock_levels_by_item():
    """Net quantity per active item across every active location.

    Unlike stock_report this keeps items sitting at zero, and includes items
    that have never moved at all — both are out of stock as far as the business
    is concerned, and dropping them would understate the shortage.
    """

    location_ids = list(Location.objects.filter(active=True).values_list("id", flat=True))
    items = Item.objects.filter(active=True, is_stock_tracked=True)

    on_hand = {}
    if location_ids:
        totals = (
            StockMovement.objects.filter(location_id__in=location_ids)
            .values("item_id")
            .annotate(
                in_qty=_zero_sum("quantity", Q(movement_type__in=STOCK_IN_TYPES), _QUANTITY),
                out_qty=_zero_sum("quantity", Q(movement_type__in=STOCK_OUT_TYPES), _QUANTITY),
            )
        )
        on_hand = {t["item_id"]: t["in_qty"] - t["out_qty"] for t in totals}

    return [
        {
            "item_id": item.id,
            "item_label": item.search_label,
            "quantity": on_hand.get(item.id, ZERO),
            "min_stock": item.min_stock,
        }
        for item in items
    ]


def stock_alert_counts():
    """How many tracked items have run out, and how many are running low.

    An item is low when it still has stock but has fallen to or below its
    minimum. Out of stock is counted separately so the two never double up.
    """

    out_of_stock = 0
    low_stock = 0

    for row in stock_levels_by_item():
        if row["quantity"] <= 0:
            out_of_stock += 1
        elif row["min_stock"] > 0 and row["quantity"] <= row["min_stock"]:
            low_stock += 1

    return {"low_stock": low_stock, "out_of_stock": out_of_stock}


def customer_report(customer=None):
    """Read-only customer balance from CustomerLedgerEntry rows.

    Every active customer appears, including one with no entries yet, so the
    report doubles as the customer list.
    """

    customers = Customer.objects.filter(active=True)
    if customer is not None:
        customers = customers.filter(pk=customer.pk)

    customers = customers.annotate(
        total_due_increase=_zero_sum("ledger_entries__due_increase", None, _MONEY),
        total_due_decrease=_zero_sum("ledger_entries__due_decrease", None, _MONEY),
    ).order_by("id")

    return [
        {
            "customer_id": row.id,
            "customer_code": row.customer_code,
            "customer_name": row.name,
            "opening_balance": row.opening_balance,
            "due_increase": row.total_due_increase,
            "due_decrease": row.total_due_decrease,
            "balance": row.opening_balance + row.total_due_increase - row.total_due_decrease,
        }
        for row in customers
    ]


def supplier_report(supplier=None):
    """Read-only supplier balance from SupplierLedgerEntry rows."""

    suppliers = Supplier.objects.filter(active=True)
    if supplier is not None:
        suppliers = suppliers.filter(pk=supplier.pk)

    suppliers = suppliers.annotate(
        total_due_increase=_zero_sum("ledger_entries__due_increase", None, _MONEY),
        total_due_decrease=_zero_sum("ledger_entries__due_decrease", None, _MONEY),
    ).order_by("id")

    return [
        {
            "supplier_id": row.id,
            "supplier_code": row.supplier_code,
            "supplier_name": row.name,
            "opening_balance": row.opening_balance,
            "due_increase": row.total_due_increase,
            "due_decrease": row.total_due_decrease,
            "balance": row.opening_balance + row.total_due_increase - row.total_due_decrease,
        }
        for row in suppliers
    ]


def cashbox_report(cashbox=None, date_from=None, date_to=None):
    """Read-only cashbox balance from actual CashboxMovement rows."""

    cashboxes = Cashbox.objects.filter(active=True)
    if cashbox is not None:
        cashboxes = cashboxes.filter(pk=cashbox.pk)

    movement_window = Q()
    if date_from:
        movement_window &= Q(movements__movement_date__gte=date_from)
    if date_to:
        movement_window &= Q(movements__movement_date__lte=date_to)

    cashboxes = cashboxes.annotate(
        total_in=_zero_sum(
            "movements__amount", Q(movements__direction=CashboxDirection.IN) & movement_window, _MONEY
        ),
        total_out=_zero_sum(
            "movements__amount", Q(movements__direction=CashboxDirection.OUT) & movement_window, _MONEY
        ),
    ).order_by("id")

    return [
        {
            "cashbox_id": row.id,
            "cashbox_code": row.cashbox_code,
            "cashbox_name": row.name_ar,
            "opening_balance": row.opening_balance,
            "cash_in": row.total_in,
            "cash_out": row.total_out,
            "balance": row.opening_balance + row.total_in - row.total_out,
        }
        for row in cashboxes
    ]


def sales_report(date_from=None, date_to=None, status=None):
    """Sales invoices, newest first.

    Drafts are included by default: this is the invoice list, not a money
    figure. Pass ``status`` when the caller needs posted invoices only — a total
    built from drafts would contradict profit_report, which posts-only already.
    """

    qs = SalesInvoice.objects.select_related("customer", "selling_location", "cashbox")
    qs = _date_filter(qs, "invoice_date", date_from, date_to)
    if status is not None:
        qs = qs.filter(status=status)
    return qs.order_by("-invoice_date", "-id")


def purchase_report(date_from=None, date_to=None, status=None):
    """Purchase invoices, newest first. See sales_report on ``status``."""

    qs = PurchaseInvoice.objects.select_related("supplier", "receiving_location", "cashbox")
    qs = _date_filter(qs, "invoice_date", date_from, date_to)
    if status is not None:
        qs = qs.filter(status=status)
    return qs.order_by("-invoice_date", "-id")


def profit_report(date_from=None, date_to=None):
    """Read-only profit rows. Profit equals sales minus cost."""

    lines = SalesLine.objects.filter(invoice__status="posted").select_related("invoice", "item")
    if date_from:
        lines = lines.filter(invoice__invoice_date__gte=date_from)
    if date_to:
        lines = lines.filter(invoice__invoice_date__lte=date_to)

    rows = []
    for line in lines.order_by("-invoice__invoice_date", "-invoice_id", "line_number"):
        rows.append(
            {
                "invoice_id": line.invoice_id,
                "invoice_number": line.invoice.invoice_number,
                "invoice_date": line.invoice.invoice_date,
                "item_id": line.item_id,
                "item_label": line.item.search_label,
                "quantity": line.quantity,
                "sales_amount": line.line_total_amount,
                "cost_amount": line.line_cost_amount,
                "profit_amount": line.line_profit_amount,
            }
        )
    return rows


def profit_totals(date_from=None, date_to=None):
    """Sales, cost and profit for a window, aggregated in the database."""

    totals = SalesLine.objects.filter(invoice__status="posted")
    totals = _date_filter(totals, "invoice__invoice_date", date_from, date_to).aggregate(
        sales=_zero_sum("line_total_amount", None, _MONEY),
        cost=_zero_sum("line_cost_amount", None, _MONEY),
    )
    totals["profit"] = totals["sales"] - totals["cost"]
    return totals
