"""Real figures for the dashboard, built from the report layer.

Everything here goes through reports.selectors rather than querying models
directly, which is what docs/dashboard_kpis.md requires: "Dashboard cards must
read from report logic only." That also keeps one definition of a balance, so a
card and its report can never disagree.

Figures are computed lazily and cached per request. A cashier sees three cards,
so a cashier's page should not pay for the other nine.
"""

from decimal import Decimal

from django.db.models import Sum

from cashboxes.models import Cashbox
from inventory.models import StockMovement
from master_data.models import Customer, Item, Supplier
from purchases.models import PurchaseInvoice, PurchaseInvoiceStatus, SupplierPayment, SupplierPaymentStatus
from sales.models import CustomerPayment, CustomerPaymentStatus, SalesInvoice, SalesInvoiceStatus
from settings_core.services import collect_usage_metrics, evaluate_usage_status

from . import selectors
from .dashboard_kpis import SCOPE_OWN


ZERO = Decimal("0")


def _money(value):
    return value if value is not None else ZERO


def _positive_total(rows, key="balance"):
    """Sum only what is owed.

    A party in credit reduces nothing that is outstanding, so netting it against
    another party's debt would understate what is actually out there.
    """

    total = ZERO
    for row in rows:
        if row[key] > 0:
            total += row[key]
    return total


class SharedReads:
    """Selector results a single dashboard needs more than once.

    The cards, the alerts and the health score all ask about stock, customers
    and cashboxes. Without this each of the three would re-run the same query,
    which is most of a dashboard's query count for no gain.
    """

    def __init__(self):
        self._cache = {}

    def _get(self, key, produce):
        if key not in self._cache:
            self._cache[key] = produce()
        return self._cache[key]

    def stock_alerts(self):
        return self._get("stock_alerts", selectors.stock_alert_counts)

    def customers(self):
        return self._get("customers", selectors.customer_report)

    def cashboxes(self):
        return self._get("cashboxes", selectors.cashbox_report)

    def credit_limits(self):
        return self._get(
            "credit_limits",
            lambda: dict(
                Customer.objects.filter(active=True, credit_limit__gt=0).values_list(
                    "id", "credit_limit"
                )
            ),
        )

    def customers_over_limit(self):
        def produce():
            limits = self.credit_limits()
            if not limits:
                return []
            return [
                row
                for row in self.customers()
                if row["customer_id"] in limits and row["balance"] > limits[row["customer_id"]]
            ]

        return self._get("over_limit", produce)


class DashboardFigures:
    """Computes a dashboard's numbers on demand, once each."""

    def __init__(self, user, today, shared=None):
        self.user = user
        self.today = today
        self.shared = shared or SharedReads()
        self._cache = {}

    def value_for(self, key, scope):
        cache_key = (key, scope)
        if cache_key not in self._cache:
            resolver = getattr(self, f"_{key}", None)
            self._cache[cache_key] = ZERO if resolver is None else resolver(scope)
        return self._cache[cache_key]

    # ---- sales ----

    def _sales_invoices_today(self, scope):
        invoices = selectors.sales_report(
            date_from=self.today, date_to=self.today, status=SalesInvoiceStatus.POSTED
        )
        if scope == SCOPE_OWN:
            invoices = invoices.filter(created_by=self.user)
        return invoices

    def _sales_today(self, scope):
        return _money(
            self._sales_invoices_today(scope).aggregate(total=Sum("total_amount"))["total"]
        )

    def _invoice_count_today(self, scope):
        return self._sales_invoices_today(scope).count()

    # ---- purchases ----

    def _purchases_today(self, scope):
        invoices = selectors.purchase_report(
            date_from=self.today, date_to=self.today, status=PurchaseInvoiceStatus.POSTED
        )
        return _money(invoices.aggregate(total=Sum("total_amount"))["total"])

    # ---- profit ----

    def _profit_today(self, scope):
        return selectors.profit_totals(date_from=self.today, date_to=self.today)["profit"]

    # ---- cash and parties ----

    def _cashbox_balance(self, scope):
        return sum((row["balance"] for row in self.shared.cashboxes()), ZERO)

    def _customer_dues(self, scope):
        return _positive_total(self.shared.customers())

    def _supplier_dues(self, scope):
        return _positive_total(selectors.supplier_report())

    def _receipts_today(self, scope):
        payments = CustomerPayment.objects.filter(
            payment_date=self.today, status=CustomerPaymentStatus.POSTED
        )
        if scope == SCOPE_OWN:
            payments = payments.filter(created_by=self.user)
        return _money(payments.aggregate(total=Sum("amount"))["total"])

    def _supplier_payments_today(self, scope):
        payments = SupplierPayment.objects.filter(
            payment_date=self.today, status=SupplierPaymentStatus.POSTED
        )
        return _money(payments.aggregate(total=Sum("amount"))["total"])

    # ---- stock ----

    def _low_stock_count(self, scope):
        return self.shared.stock_alerts()["low_stock"]

    def _out_of_stock_count(self, scope):
        return self.shared.stock_alerts()["out_of_stock"]

    # ---- usage ----

    def _usage_status(self, scope):
        return evaluate_usage_status(sum(collect_usage_metrics().values()))


def has_any_business_data():
    """Whether this installation has been used yet.

    Master data alone does not count. Someone who added a cashbox but has never
    recorded anything still needs the four-step guide, not an empty dashboard.
    """

    return (
        SalesInvoice.objects.exists()
        or PurchaseInvoice.objects.exists()
        or StockMovement.objects.exists()
    )


def onboarding_progress():
    """Which of the four starting steps are already done."""

    return [
        Cashbox.objects.filter(active=True).exists(),
        Customer.objects.filter(active=True).exists() or Supplier.objects.filter(active=True).exists(),
        Item.objects.filter(active=True).exists(),
        SalesInvoice.objects.exists() or PurchaseInvoice.objects.exists(),
    ]


# Thresholds for the alert list. Deliberately conservative: an alert the owner
# learns to ignore is worse than no alert.
CASHBOX_LOW_THRESHOLD = Decimal("500")


def build_alerts(held_permissions, today, shared=None):
    """Real alerts from real thresholds, newest risk first.

    Only alerts the viewer is allowed to see are built at all, so this never
    leaks a figure the KPI row would have withheld. Cheques are absent because
    no cheque model exists yet; docs/120_DASHBOARD_CORE_PLAN is explicit that
    missing data should show nothing rather than empty noise.
    """

    shared = shared or SharedReads()
    alerts = []

    if "reports.view_inventory_report" in held_permissions:
        counts = shared.stock_alerts()
        if counts["out_of_stock"]:
            alerts.append(
                {
                    "key": "out_of_stock",
                    "severity": "urgent",
                    "ar": f"{counts['out_of_stock']} صنف نفد من المخزون",
                    "en": f"{counts['out_of_stock']} item(s) out of stock",
                    "detail_ar": "لا يمكن البيع من هذه الأصناف الآن.",
                    "detail_en": "These cannot be sold right now.",
                    "amount": "",
                }
            )
        if counts["low_stock"]:
            alerts.append(
                {
                    "key": "low_stock",
                    "severity": "soon",
                    "ar": f"{counts['low_stock']} صنف تحت الحد الأدنى",
                    "en": f"{counts['low_stock']} item(s) below minimum",
                    "detail_ar": "راجع الأصناف قبل نفاذها.",
                    "detail_en": "Review before they run out.",
                    "amount": "",
                }
            )

    if "reports.view_customer_report" in held_permissions:
        limits = shared.credit_limits()
        for row in shared.customers_over_limit():
            limit = limits[row["customer_id"]]
            alerts.append(
                {
                    "key": f"customer_over_limit_{row['customer_id']}",
                    "severity": "urgent",
                    "ar": f"{row['customer_name']} تجاوز حد المديونية",
                    "en": f"{row['customer_name']} is over the credit limit",
                    "detail_ar": f"الحد المسموح {limit:,.0f}.",
                    "detail_en": f"Limit is {limit:,.0f}.",
                    "amount": f"{row['balance']:,.0f}",
                }
            )

    if "cashboxes.view_finance" in held_permissions:
        for row in shared.cashboxes():
            if row["balance"] < 0:
                alerts.append(
                    {
                        "key": f"cashbox_negative_{row['cashbox_id']}",
                        "severity": "urgent",
                        "ar": f"{row['cashbox_name']} رصيدها سالب",
                        "en": f"{row['cashbox_name']} has a negative balance",
                        "detail_ar": "راجع الحركات المسجلة على الخزنة.",
                        "detail_en": "Review the movements recorded on it.",
                        "amount": f"{row['balance']:,.0f}",
                    }
                )
            elif row["balance"] < CASHBOX_LOW_THRESHOLD:
                alerts.append(
                    {
                        "key": f"cashbox_low_{row['cashbox_id']}",
                        "severity": "watch",
                        "ar": f"{row['cashbox_name']} رصيدها منخفض",
                        "en": f"{row['cashbox_name']} balance is low",
                        "detail_ar": "أقل من الحد المعتاد للتشغيل.",
                        "detail_en": "Below the usual working level.",
                        "amount": f"{row['balance']:,.0f}",
                    }
                )

    severity_order = {"urgent": 0, "soon": 1, "watch": 2}
    alerts.sort(key=lambda alert: severity_order[alert["severity"]])
    return alerts


# How much each risk can take off the health score.
HEALTH_PENALTIES = {
    "out_of_stock": 15,
    "low_stock": 8,
    "negative_cashbox": 20,
    "overdue_customers": 12,
    "no_sales_today": 10,
    "loss_today": 15,
}


#: How many of the five inputs a viewer must be able to see before the score is
#: shown at all. A cashier can see only whether anything sold today; scoring the
#: whole business on that one fact would read 100% while stock is out and a
#: customer is over their limit. One fact is not a summary.
MINIMUM_HEALTH_INPUTS = 3

#: Which permission each penalty's evidence belongs to. A viewer who may not see
#: the underlying figure must not have it priced into their score either.
HEALTH_INPUT_PERMISSIONS = {
    "out_of_stock": "reports.view_inventory_report",
    "low_stock": "reports.view_inventory_report",
    "negative_cashbox": "cashboxes.view_finance",
    "overdue_customers": "reports.view_customer_report",
    "no_sales_today": "reports.view_sales_report",
    "loss_today": "reports.view_profit_report",
}


def health_score(today, held_permissions, shared=None):
    """A single number for "how is the business doing".

    Starts at 100 and takes off what is actually wrong, so the score stays
    explainable by the alerts shown beside it.

    Only risks the viewer is allowed to see are priced in. A cashier scoring the
    business on overdue customers and cashbox balances would be reading facts
    they cannot open — and a score with no visible cause reads as arbitrary.
    ``available`` is False when the viewer can see too few of the inputs for the
    number to mean anything, and the ring is then left off the page entirely.
    """

    shared = shared or SharedReads()

    def may_see(reason):
        return HEALTH_INPUT_PERMISSIONS[reason] in held_permissions

    score = 100
    reasons = []
    considered = 0

    if may_see("out_of_stock"):
        considered += 1
        stock = shared.stock_alerts()
        if stock["out_of_stock"]:
            score -= HEALTH_PENALTIES["out_of_stock"]
            reasons.append("out_of_stock")
        if stock["low_stock"]:
            score -= HEALTH_PENALTIES["low_stock"]
            reasons.append("low_stock")

    if may_see("negative_cashbox"):
        considered += 1
        if any(row["balance"] < 0 for row in shared.cashboxes()):
            score -= HEALTH_PENALTIES["negative_cashbox"]
            reasons.append("negative_cashbox")

    if may_see("overdue_customers"):
        considered += 1
        if shared.customers_over_limit():
            score -= HEALTH_PENALTIES["overdue_customers"]
            reasons.append("overdue_customers")

    if may_see("no_sales_today"):
        considered += 1
        posted_today = SalesInvoice.objects.filter(
            invoice_date=today, status=SalesInvoiceStatus.POSTED
        )
        if not posted_today.exists():
            score -= HEALTH_PENALTIES["no_sales_today"]
            reasons.append("no_sales_today")

    if may_see("loss_today"):
        considered += 1
        if selectors.profit_totals(date_from=today, date_to=today)["profit"] < 0:
            score -= HEALTH_PENALTIES["loss_today"]
            reasons.append("loss_today")

    return {
        "score": max(0, min(100, score)),
        "reasons": reasons,
        "available": considered >= MINIMUM_HEALTH_INPUTS,
        "inputs_seen": considered,
    }


HEALTH_BANDS = (
    (80, "steady", {"ar": "نشاطك مستقر", "en": "Your business is steady"}),
    (55, "watch", {"ar": "نشاطك يحتاج متابعة", "en": "Your business needs attention"}),
    (0, "risk", {"ar": "نشاطك فيه مخاطر تحتاج تصرف", "en": "Your business has risks to address"}),
)


def health_band(score):
    for floor, key, words in HEALTH_BANDS:
        if score >= floor:
            return key, words
    return HEALTH_BANDS[-1][1], HEALTH_BANDS[-1][2]
