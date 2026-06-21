from decimal import Decimal

from django.db.models import Sum

from cashboxes.models import CashboxMovement, CashboxDirection
from inventory.services import get_item_location_stock_quantity
from master_data.models import Cashbox, Customer, Item, Location, Supplier
from purchases.models import SupplierLedgerEntry
from sales.models import CustomerLedgerEntry, SalesLine


def _sum(queryset, field_name):
    return queryset.aggregate(total=Sum(field_name)).get("total") or Decimal("0")


def get_supplier_balance(supplier):
    """Supplier due = opening balance + due increases - due decreases."""

    due_increase = _sum(supplier.ledger_entries.all(), "due_increase")
    due_decrease = _sum(supplier.ledger_entries.all(), "due_decrease")
    return (supplier.opening_balance or Decimal("0")) + due_increase - due_decrease


def get_customer_balance(customer):
    """Customer due = opening balance + due increases - due decreases."""

    due_increase = _sum(customer.ledger_entries.all(), "due_increase")
    due_decrease = _sum(customer.ledger_entries.all(), "due_decrease")
    return (customer.opening_balance or Decimal("0")) + due_increase - due_decrease


def get_cashbox_balance(cashbox):
    """Cashbox balance = opening balance + actual cash in - actual cash out."""

    movements = cashbox.movements.all()
    cash_in = _sum(movements.filter(direction=CashboxDirection.IN), "amount")
    cash_out = _sum(movements.filter(direction=CashboxDirection.OUT), "amount")
    return (cashbox.opening_balance or Decimal("0")) + cash_in - cash_out


def get_item_location_stock(item, location):
    """Read stock from stock movements only."""

    return get_item_location_stock_quantity(item, location)


def get_profit_summary():
    """Profit summary from sales lines only: sales - cost of goods sold."""

    total_sales = _sum(SalesLine.objects.all(), "line_total_amount")
    total_cost = _sum(SalesLine.objects.all(), "line_cost_amount")
    total_profit = _sum(SalesLine.objects.all(), "line_profit_amount")
    return {
        "total_sales": total_sales,
        "total_cost": total_cost,
        "total_profit": total_profit,
    }


def get_local_controlled_cycle_snapshot():
    """Small read-only snapshot used by the first local controlled test."""

    supplier = Supplier.objects.get(supplier_code="SUP-001")
    customer = Customer.objects.get(customer_code="CUST-001")
    item = Item.objects.get(item_code="ITEM-001")
    location = Location.objects.get(location_code="MAIN")
    cashbox = Cashbox.objects.get(cashbox_code="CASH-001")

    return {
        "supplier_balance": get_supplier_balance(supplier),
        "customer_balance": get_customer_balance(customer),
        "cashbox_balance": get_cashbox_balance(cashbox),
        "item_location_stock": get_item_location_stock(item, location),
        "supplier_ledger_entries": SupplierLedgerEntry.objects.count(),
        "customer_ledger_entries": CustomerLedgerEntry.objects.count(),
        "cashbox_movements": CashboxMovement.objects.count(),
        "profit": get_profit_summary(),
    }
