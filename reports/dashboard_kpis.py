"""What each person sees on the dashboard, decided by permission.

docs/dashboard_kpis.md lists a different set of cards per role. Rather than
restate that list role by role, every card declares the permission it needs and
the seeded matrix decides — so the documented sets fall out on their own and the
dashboard never asks who someone is.

Two consequences of reading the matrix rather than the KPI document:

* A manager gets no supplier-dues card. ``reports.view_supplier_report`` is
  seeded as sensitive finance and withheld from managers, and what is enforced
  wins over what the KPI list hoped for.
* A cashier's sales figures cover only their own invoices. That is what
  ``reports.view_all_sales_report`` exists to distinguish.
"""

from dataclasses import dataclass


#: Scope of a figure when the viewer may only see their own work.
SCOPE_ALL = "all"
SCOPE_OWN = "own"


@dataclass(frozen=True)
class Kpi:
    """One dashboard figure and the right to see it."""

    key: str
    label_ar: str
    label_en: str
    #: Without this permission the card is not built at all.
    permission: str
    #: Holding this widens the figure from the viewer's own work to the whole
    #: business. Lacking it is not a refusal, only a narrower scope.
    scope_permission: str = ""
    unit: str = "currency"
    #: Cards carrying cost or profit, kept together so they are easy to assert on.
    sensitive: bool = False
    label_own_ar: str = ""
    label_own_en: str = ""

    def scope_for(self, held):
        if not self.scope_permission:
            return SCOPE_ALL
        return SCOPE_ALL if self.scope_permission in held else SCOPE_OWN

    def label(self, lang, scope=SCOPE_ALL):
        if scope == SCOPE_OWN and self.label_own_ar:
            return self.label_own_en if lang == "en" else self.label_own_ar
        return self.label_en if lang == "en" else self.label_ar


CURRENCY = "currency"
COUNT = "count"
LEVEL = "level"


#: Declaration order is display order.
DASHBOARD_KPIS = (
    Kpi(
        key="sales_today",
        label_ar="مبيعات اليوم",
        label_en="Sales today",
        label_own_ar="مبيعاتي اليوم",
        label_own_en="My sales today",
        permission="reports.view_sales_report",
        scope_permission="reports.view_all_sales_report",
    ),
    Kpi(
        key="invoice_count_today",
        label_ar="عدد الفواتير اليوم",
        label_en="Invoices today",
        label_own_ar="عدد فواتيري اليوم",
        label_own_en="My invoices today",
        permission="sales.view_sales_invoices",
        scope_permission="reports.view_all_sales_report",
        unit=COUNT,
    ),
    Kpi(
        key="purchases_today",
        label_ar="مشتريات اليوم",
        label_en="Purchases today",
        permission="reports.view_purchase_report",
    ),
    Kpi(
        key="profit_today",
        label_ar="صافي الربح اليوم",
        label_en="Profit today",
        permission="reports.view_profit_report",
        sensitive=True,
    ),
    Kpi(
        key="cashbox_balance",
        label_ar="رصيد الخزن",
        label_en="Cashbox balance",
        permission="cashboxes.view_finance",
        sensitive=True,
    ),
    Kpi(
        key="customer_dues",
        label_ar="مديونيات العملاء",
        label_en="Customer dues",
        permission="reports.view_customer_report",
    ),
    Kpi(
        key="supplier_dues",
        label_ar="مستحقات الموردين",
        label_en="Supplier dues",
        permission="reports.view_supplier_report",
        sensitive=True,
    ),
    Kpi(
        key="receipts_today",
        label_ar="تحصيلات اليوم",
        label_en="Receipts today",
        permission="sales.receive_customer_payment",
    ),
    Kpi(
        key="supplier_payments_today",
        label_ar="مدفوعات الموردين اليوم",
        label_en="Supplier payments today",
        permission="purchases.pay_supplier",
        sensitive=True,
    ),
    Kpi(
        key="low_stock_count",
        label_ar="أصناف تحت الحد الأدنى",
        label_en="Items below minimum",
        permission="reports.view_inventory_report",
        unit=COUNT,
    ),
    Kpi(
        key="out_of_stock_count",
        label_ar="أصناف نفدت",
        label_en="Out of stock",
        permission="reports.view_inventory_report",
        unit=COUNT,
    ),
    Kpi(
        key="usage_status",
        label_ar="حالة الاستهلاك",
        label_en="Usage status",
        permission="settings.view_settings",
        unit=LEVEL,
    ),
)

KPI_BY_KEY = {kpi.key: kpi for kpi in DASHBOARD_KPIS}

ALL_KPI_PERMISSIONS = tuple(
    sorted(
        {kpi.permission for kpi in DASHBOARD_KPIS}
        | {kpi.scope_permission for kpi in DASHBOARD_KPIS if kpi.scope_permission}
    )
)

SENSITIVE_KPI_KEYS = frozenset(kpi.key for kpi in DASHBOARD_KPIS if kpi.sensitive)


def visible_kpis(held_permissions):
    """The cards this viewer may see, each paired with its scope."""

    return tuple(
        (kpi, kpi.scope_for(held_permissions))
        for kpi in DASHBOARD_KPIS
        if kpi.permission in held_permissions
    )
