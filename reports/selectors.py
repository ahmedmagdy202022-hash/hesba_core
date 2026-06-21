from decimal import Decimal

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


def stock_report(location=None):
    """Read-only stock by item and location from StockMovement rows."""

    rows = []
    items = Item.objects.filter(active=True)
    locations = Location.objects.filter(active=True)
    if location is not None:
        locations = locations.filter(pk=location.pk)

    for item in items:
        for current_location in locations:
            movements = StockMovement.objects.filter(item=item, location=current_location)
            in_qty = _sum(movements.filter(movement_type__in=STOCK_IN_TYPES).values_list("quantity", flat=True))
            out_qty = _sum(movements.filter(movement_type__in=STOCK_OUT_TYPES).values_list("quantity", flat=True))
            quantity = in_qty - out_qty
            if quantity == 0:
                continue
            rows.append(
                {
                    "item_id": item.id,
                    "item_label": item.search_label,
                    "location_id": current_location.id,
                    "location_name": current_location.name_ar,
                    "quantity": quantity,
                    "stock_value": quantity * item.average_cost,
                    "min_stock": item.min_stock,
                }
            )
    return rows


def customer_report(customer=None):
    """Read-only customer balance from CustomerLedgerEntry rows."""

    rows = []
    customers = Customer.objects.filter(active=True)
    if customer is not None:
        customers = customers.filter(pk=customer.pk)

    for current_customer in customers:
        entries = CustomerLedgerEntry.objects.filter(customer=current_customer)
        due_increase = _sum(entries.values_list("due_increase", flat=True))
        due_decrease = _sum(entries.values_list("due_decrease", flat=True))
        rows.append(
            {
                "customer_id": current_customer.id,
                "customer_code": current_customer.customer_code,
                "customer_name": current_customer.name,
                "opening_balance": current_customer.opening_balance,
                "due_increase": due_increase,
                "due_decrease": due_decrease,
                "balance": current_customer.opening_balance + due_increase - due_decrease,
            }
        )
    return rows


def supplier_report(supplier=None):
    """Read-only supplier balance from SupplierLedgerEntry rows."""

    rows = []
    suppliers = Supplier.objects.filter(active=True)
    if supplier is not None:
        suppliers = suppliers.filter(pk=supplier.pk)

    for current_supplier in suppliers:
        entries = SupplierLedgerEntry.objects.filter(supplier=current_supplier)
        due_increase = _sum(entries.values_list("due_increase", flat=True))
        due_decrease = _sum(entries.values_list("due_decrease", flat=True))
        rows.append(
            {
                "supplier_id": current_supplier.id,
                "supplier_code": current_supplier.supplier_code,
                "supplier_name": current_supplier.name,
                "opening_balance": current_supplier.opening_balance,
                "due_increase": due_increase,
                "due_decrease": due_decrease,
                "balance": current_supplier.opening_balance + due_increase - due_decrease,
            }
        )
    return rows


def cashbox_report(cashbox=None, date_from=None, date_to=None):
    """Read-only cashbox balance from actual CashboxMovement rows."""

    rows = []
    cashboxes = Cashbox.objects.filter(active=True)
    if cashbox is not None:
        cashboxes = cashboxes.filter(pk=cashbox.pk)

    for current_cashbox in cashboxes:
        movements = CashboxMovement.objects.filter(cashbox=current_cashbox)
        movements = _date_filter(movements, "movement_date", date_from, date_to)
        cash_in = _sum(movements.filter(direction=CashboxDirection.IN).values_list("amount", flat=True))
        cash_out = _sum(movements.filter(direction=CashboxDirection.OUT).values_list("amount", flat=True))
        rows.append(
            {
                "cashbox_id": current_cashbox.id,
                "cashbox_code": current_cashbox.cashbox_code,
                "cashbox_name": current_cashbox.name_ar,
                "opening_balance": current_cashbox.opening_balance,
                "cash_in": cash_in,
                "cash_out": cash_out,
                "balance": current_cashbox.opening_balance + cash_in - cash_out,
            }
        )
    return rows


def sales_report(date_from=None, date_to=None):
    qs = SalesInvoice.objects.select_related("customer", "selling_location", "cashbox")
    qs = _date_filter(qs, "invoice_date", date_from, date_to)
    return qs.order_by("-invoice_date", "-id")


def purchase_report(date_from=None, date_to=None):
    qs = PurchaseInvoice.objects.select_related("supplier", "receiving_location", "cashbox")
    qs = _date_filter(qs, "invoice_date", date_from, date_to)
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
