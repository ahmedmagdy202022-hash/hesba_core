from cashboxes.models import CashboxMovement
from inventory.models import StockMovement
from master_data.models import Customer, Item, Supplier
from purchases.models import PurchaseInvoice
from sales.models import SalesInvoice
from .models import UsageStatusLevel, UsageStatusSnapshot


GREEN_LIMIT = 10000
YELLOW_LIMIT = 25000
ORANGE_LIMIT = 45000


DEFAULT_RECOMMENDATIONS = [
    "Close periods regularly.",
    "Archive old periods.",
    "Clean trial data.",
    "Limit report date ranges.",
    "Save file links instead of file blobs.",
    "Use summaries for heavy reports.",
]


def collect_usage_metrics():
    return {
        "active_items_count": Item.objects.filter(active=True).count(),
        "active_customers_count": Customer.objects.filter(active=True).count(),
        "active_suppliers_count": Supplier.objects.filter(active=True).count(),
        "stock_movements_count": StockMovement.objects.count(),
        "cashbox_movements_count": CashboxMovement.objects.count(),
        "sales_invoices_count": SalesInvoice.objects.count(),
        "purchase_invoices_count": PurchaseInvoice.objects.count(),
    }


def calculate_total_rows(metrics):
    return sum(metrics.values())


def evaluate_usage_status(total_rows):
    if total_rows >= ORANGE_LIMIT:
        return UsageStatusLevel.RED
    if total_rows >= YELLOW_LIMIT:
        return UsageStatusLevel.ORANGE
    if total_rows >= GREEN_LIMIT:
        return UsageStatusLevel.YELLOW
    return UsageStatusLevel.GREEN


def build_usage_warnings(status_level):
    if status_level == UsageStatusLevel.GREEN:
        return ["Usage is normal."]
    if status_level == UsageStatusLevel.YELLOW:
        return ["Usage is increasing."]
    if status_level == UsageStatusLevel.ORANGE:
        return ["Usage is close to safe limits."]
    return ["Usage needs action."]


def create_usage_status_snapshot():
    metrics = collect_usage_metrics()
    total_rows = calculate_total_rows(metrics)
    status_level = evaluate_usage_status(total_rows)
    return UsageStatusSnapshot.objects.create(
        status_level=status_level,
        total_rows=total_rows,
        warnings=build_usage_warnings(status_level),
        recommendations=DEFAULT_RECOMMENDATIONS,
        **metrics,
    )
