"""DASH-FIT: the dashboard shows what this business actually is and chose, not every figure it has a permission for.

Permission still decides who may see a figure (``dashboard_kpis``, ``analytics``).
On top of that, every card, metric, chart and alert says what it belongs to:

- a **module** switched on in setup (no purchases module, no purchases card;
  no inventory, no stock alerts or running-out list);
- the **activity** (sales by the hour say something to a shop, a café or a
  clinic, nothing to a contractor or a factory);
- a **fact from the data** where setup alone cannot tell: the "paid or on
  credit" split only shows for a business that sells on credit at all
  (instalments switched on, or credit sales in the last 90 days).

``option_tiles`` adds one tile for each capability switched on in setup that
has something to watch (overdue instalments, expiring batches, the month's
VAT, e-invoices not sent yet, the month's depreciation, open shifts, serials
in stock), so an option chosen in setup shows on the first screen too.

Everything here only reads; one table (``NEEDS``) holds the rules, and the
tests walk it.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from permissions.services import user_has_permission

ZERO = Decimal("0")

#: Activities whose customers come in through the day: hours and weekdays mean something there.
WALK_IN = frozenset({"commercial", "restaurants", "services", "medical", "other"})
#: Activities that trade on most days (a weekday pattern is worth reading); a contractor bills by certificate.
DAILY_TRADE = WALK_IN | {"education"}


@dataclass(frozen=True)
class Need:
    modules: frozenset = frozenset()  # all of these switched on
    activities: frozenset = frozenset()  # one of these (empty: any)
    fact: str = ""  # a named fact from the data, see Fit.fact


def need(*modules, activities=(), fact=""):
    return Need(frozenset(modules), frozenset(activities), fact)


#: What each dashboard element belongs to. An element missing here always shows (subject to permission).
NEEDS = {
    # figure cards (dashboard_kpis keys)
    "kpi.purchases_today": need("purchases"),
    "kpi.supplier_dues": need("suppliers"),
    "kpi.supplier_payments_today": need("suppliers"),
    "kpi.customer_dues": need("customers"),
    "kpi.receipts_today": need("customers"),
    "kpi.low_stock_count": need("inventory"),
    "kpi.out_of_stock_count": need("inventory"),
    "kpi.cashbox_balance": need("cashboxes"),
    # analytics metrics
    "metric.expenses": need("expenses"),
    "metric.net_profit": need("expenses"),
    "metric.cash": need("cashboxes"),
    "metric.receivables": need("customers"),
    "metric.payables": need("suppliers"),
    # charts and lists
    "chart.split": need("customers", fact="sells_on_credit"),
    "chart.hours": need(activities=WALK_IN),
    "chart.weekdays": need(activities=DAILY_TRADE),
    "list.stock": need("inventory"),
    # alerts (by key prefix)
    "alert.out_of_stock": need("inventory"),
    "alert.low_stock": need("inventory"),
    "alert.batches_expired": need("inventory"),
    "alert.batches_expiring": need("inventory"),
    "alert.customer_over_limit": need("customers"),
    "alert.instalments_overdue": need("customers"),
    "alert.cashbox_negative": need("cashboxes"),
    "alert.cashbox_low": need("cashboxes"),
}


@dataclass
class Fit:
    activity: str
    modules: frozenset
    capabilities: frozenset
    today: object = None
    _facts: dict = field(default_factory=dict)

    @classmethod
    def current(cls, today=None):
        from entities.current import effective_activity
        from settings_core import setup_catalog
        from settings_core.capabilities import enabled_capabilities
        from settings_core.models import ClientProfile
        from settings_core.setup_services import usable_modules

        profile = ClientProfile.get_active()
        # Before setup is finished nothing has been chosen, so nothing is taken away (an older install shows everything).
        chosen = profile is not None and profile.setup_is_complete
        modules = frozenset(usable_modules()) if chosen else frozenset(setup_catalog.MODULE_SLUGS)
        return cls(activity=effective_activity()[0] or "", modules=modules,
                   capabilities=frozenset(enabled_capabilities()), today=today or timezone.localdate())

    def fact(self, name):
        if name not in self._facts:
            self._facts[name] = getattr(self, f"_fact_{name}")()
        return self._facts[name]

    def _fact_sells_on_credit(self):
        """Instalments switched on, or any posted sale of the last 90 days left something owed."""

        if "installments" in self.capabilities:
            return True
        from entities import scope as entity_scope
        from sales.models import SalesInvoice

        since = (self.today or timezone.localdate()) - timedelta(days=90)
        return entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE).filter(
            status="posted", invoice_date__gte=since, remaining_due__gt=0).exists()

    def shows(self, key):
        rule = NEEDS.get(key)
        if rule is None:
            return True
        if not rule.modules <= self.modules:
            return False
        if rule.activities and self.activity not in rule.activities:
            return False
        return not rule.fact or self.fact(rule.fact)

    def shows_alert(self, alert_key):
        """An alert's key may carry an id (``customer_over_limit_12``): the rule is on its prefix."""

        for prefix, rule in NEEDS.items():
            if prefix.startswith("alert.") and (alert_key == prefix[6:] or alert_key.startswith(prefix[6:] + "_")):
                return self.shows(prefix)
        return True


def fit_analytics(analytics, fit):
    """The analytics block with the metrics, charts and lists this business has no use for taken out."""

    if not analytics.get("available"):
        return analytics
    analytics["metrics"] = [metric for metric in analytics["metrics"] if fit.shows(f"metric.{metric['key']}")]
    for key, chart in (("split", "chart.split"), ("hours", "chart.hours"), ("weekdays", "chart.weekdays"), ("stock", "list.stock")):
        if not fit.shows(chart):
            analytics[key] = None
    return analytics


# ---- the options chosen in setup, each with what it has to watch ----

WORDS = {
    "ar": {"title": "متابعة اللي فعّلته في الإعدادات", "open": "افتح",
           "instalments": "أقساط متأخرة", "instalments_sub": "{amount} على {plans} عميل", "instalments_none": "مفيش أقساط متأخرة",
           "batches_expired": "تشغيلات انتهت ولسه في المخزن", "batches_soon": "تشغيلات هتنتهي قريب",
           "vat": "ضريبة مبيعات الشهر", "vat_sub": "صافي ضريبة القيمة المضافة على المبيعات المرحّلة",
           "einvoice": "فواتير الشهر لسه ماتبعتتش للضرايب", "einvoice_sub": "من {total} فاتورة مرحّلة",
           "assets": "إهلاك الشهر", "assets_sub": "{count} أصل",
           "shifts": "ورديات مفتوحة دلوقتي", "serials": "أجهزة بسيريال في المخزن"},
    "en": {"title": "What you switched on in setup", "open": "Open",
           "instalments": "Overdue instalments", "instalments_sub": "{amount} from {plans} customers", "instalments_none": "No overdue instalments",
           "batches_expired": "Expired batches still in stock", "batches_soon": "Batches expiring soon",
           "vat": "This month's sales VAT", "vat_sub": "Net output VAT on posted sales",
           "einvoice": "This month's invoices not sent to the tax authority", "einvoice_sub": "of {total} posted invoices",
           "assets": "This month's depreciation", "assets_sub": "{count} assets",
           "shifts": "Shifts open now", "serials": "Serial-numbered units in stock"},
}


def _tile(key, label, value, url, kind="count", tone="", sub=""):
    return {"key": key, "label": label, "value": value, "kind": kind, "tone": tone, "sub": sub, "url": url}


def option_tiles(fit, user, lang="ar"):
    """One tile for each capability switched on that has something to watch, for a viewer allowed to open it."""

    from settings_core.templatetags.hesba_format import money as display_money

    words = WORDS["en" if lang == "en" else "ar"]
    from batches.words import words as expiry_words

    dated = expiry_words(lang)
    if dated:  # AN8: a shop keeping expiry dates only
        words = {**words, "batches_expired": dated["summary_expired"], "batches_soon": dated["summary_soon"]}
    today = fit.today or timezone.localdate()
    month_start = today.replace(day=1)
    can = lambda code: user_has_permission(user, code)  # noqa: E731
    on = fit.capabilities
    tiles = []
    if "installments" in on and "customers" in fit.modules and can("sales.view_sales_invoices"):
        from installments.services import overdue_summary

        late = overdue_summary(today)
        tiles.append(_tile("opt_instalments", words["instalments"], late["count"], reverse("installments:list"), tone="bad" if late["count"] else "good",
                           sub=words["instalments_sub"].format(amount=display_money(late["amount"]), plans=late["plans"]) if late["count"] else words["instalments_none"]))
    if "batches_expiry" in on and "inventory" in fit.modules and can("inventory.view_stock"):
        from batches.services import expiry_alerts

        batches = expiry_alerts(today)
        tiles.append(_tile("opt_batches_expired", words["batches_expired"], len(batches["expired"]), reverse("batches:index"),
                           tone="bad" if batches["expired"] else "good"))
        tiles.append(_tile("opt_batches_soon", words["batches_soon"], len(batches["soon"]), reverse("batches:index"), tone="warn" if batches["soon"] else ""))
    if "vat" in on and can("reports.view_sales_report"):
        from taxes.services import vat_report

        tiles.append(_tile("opt_vat", words["vat"], vat_report(month_start, today)["net_tax"], reverse("taxes:report"), kind="money", sub=words["vat_sub"]))
    if "e_invoice" in on and can("sales.view_sales_invoices"):
        from einvoice.models import SubmissionStatus
        from entities import scope as entity_scope
        from sales.models import SalesInvoice

        posted = entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE).filter(status="posted", invoice_date__gte=month_start)
        sent = posted.filter(eta_submissions__status__in=(SubmissionStatus.SUBMITTED, SubmissionStatus.VALID)).distinct().count()
        total = posted.count()
        tiles.append(_tile("opt_einvoice", words["einvoice"], total - sent, reverse("sales:list"), tone="warn" if total - sent else "good",
                           sub=words["einvoice_sub"].format(total=total)))
    if "fixed_assets" in on and can("cashboxes.view_expenses"):
        from fixed_assets.services import _assets, depreciation_between

        tiles.append(_tile("opt_assets", words["assets"], depreciation_between(month_start, today), reverse("fixed_assets:list"), kind="money",
                           sub=words["assets_sub"].format(count=_assets().count())))
    if "pos" in on and can("sales.create_sales_invoice"):
        from shifts.models import Shift, ShiftStatus

        tiles.append(_tile("opt_shifts", words["shifts"], Shift.objects.filter(status=ShiftStatus.OPEN).count(), reverse("shifts:mine")))
    if "serials" in on and "inventory" in fit.modules and can("inventory.view_stock"):
        from serials.services import available_serials

        tiles.append(_tile("opt_serials", words["serials"], available_serials().count(), reverse("serials:index")))
    return tiles


def option_panel(fit, user, lang="ar"):
    tiles = option_tiles(fit, user, lang)
    if not tiles:
        return None
    return {"title": WORDS["en" if lang == "en" else "ar"]["title"], "tiles": tiles, "open": WORDS["en" if lang == "en" else "ar"]["open"]}
