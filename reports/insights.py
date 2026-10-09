"""R2-8: the business in plain words and charts, per activity.

Thirty days against the thirty before them. Every figure reuses a formula the
dashboard and the reports already use (net sales net of VAT and returns,
line profit, average cost); this page only arranges them, writes the quick
read, and adds what the chosen activity cares about: dishes for a
restaurant, people for a clinic or a salon, projects for a builder,
production cost for a factory, expiry dates for a pharmacy. All reads.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.utils import timezone

from config.money import money_round
from entities import scope as entity_scope
from sales.models import SalesInvoice, SalesInvoiceStatus, SalesLine

from . import analytics


ZERO = Decimal("0")
DAYS = 30

WORDS = {
    "ar": {
        "up": "زادت", "down": "قلّت", "flat": "زي ما هي",
        "sales": "المبيعات في آخر 30 يوم {now}، {trend} {pct}% عن الـ 30 يوم اللي قبلها ({before}).",
        "sales_first": "المبيعات في آخر 30 يوم {now}. مفيش مبيعات قبلها نقارن بيها.",
        "no_sales": "لسه مفيش مبيعات في آخر 30 يوم.",
        "driver_up": "أكتر حاجة رفعت المبيعات: «{name}» (+{delta}).",
        "driver_down": "أكتر حاجة نزّلت المبيعات: «{name}» ({delta}).",
        "margin": "هامش الربح الإجمالي {margin}%: من كل 100 جنيه مبيعات، {margin} ربح قبل المصروفات.",
        "best_day": "أقوى يوم في الأسبوع: {day}.",
        "peak": "ساعة الذروة حوالي {hour}:00.",
        "overdue": "فيه {total} متأخر على العملاء بعد ميعاد السداد.",
        "slow": "{count} صنف راكد (مطلعش من 60 يوم) قيمتهم {value} واقفة على الرفوف.",
        "top_customer": "أكبر عميل: {name} ({value}).",
    },
    "en": {
        "up": "up", "down": "down", "flat": "unchanged",
        "sales": "Sales in the last 30 days: {now}, {trend} {pct}% on the 30 days before ({before}).",
        "sales_first": "Sales in the last 30 days: {now}. Nothing earlier to compare with.",
        "no_sales": "No sales in the last 30 days yet.",
        "driver_up": "What lifted sales most: “{name}” (+{delta}).",
        "driver_down": "What pulled sales down most: “{name}” ({delta}).",
        "margin": "Gross margin {margin}%: of every 100 in sales, {margin} is profit before expenses.",
        "best_day": "Strongest day of the week: {day}.",
        "peak": "Peak hour around {hour}:00.",
        "overdue": "{total} owed by customers is past its due date.",
        "slow": "{count} slow items (nothing out in 60 days) hold {value} on the shelves.",
        "top_customer": "Biggest customer: {name} ({value}).",
    },
}


def _fmt(value):
    return f"{money_round(value):,.2f}"


def _posted_lines(start, end):
    return SalesLine.objects.filter(
        entity_scope.q(entity_scope.SALES_INVOICE, "invoice__"),
        invoice__status=SalesInvoiceStatus.POSTED, invoice__invoice_date__gte=start, invoice__invoice_date__lte=end,
    )


def _bars(rows, value_key="value", limit=6):
    """Horizontal bars, widths relative to the largest; ``rows`` already sorted."""

    rows = [row for row in rows if row[value_key] > 0][:limit]
    top = max((row[value_key] for row in rows), default=ZERO)
    for row in rows:
        row["pct"] = analytics._n(float(row[value_key] / top) * 100) if top else "0.0"
    return rows


def by_category(start, end, lang):
    rows = (_posted_lines(start, end).values("item__category__name_ar", "item__category__name_en")
            .annotate(value=Sum("line_total_amount")).order_by("-value"))
    total = sum((row["value"] or ZERO for row in rows), ZERO)
    out = []
    for row in rows:
        name = (row["item__category__name_en"] if lang == "en" else None) or row["item__category__name_ar"] or ("Uncategorised" if lang == "en" else "بدون تصنيف")
        value = money_round(row["value"] or ZERO)
        out.append({"label": name, "value": value, "share": round(float(value / total) * 100) if total else 0})
    return _bars(out)


def top_customers(start, end, limit=5):
    rows = (entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE)
            .filter(status=SalesInvoiceStatus.POSTED, invoice_date__gte=start, invoice_date__lte=end)
            .values("customer__name").annotate(value=Sum(F("total_amount") - F("tax_amount")), count=Count("id")).order_by("-value"))
    return _bars([{"label": row["customer__name"], "value": money_round(row["value"] or ZERO), "count": row["count"]} for row in rows], limit=limit)


def item_sales(start, end):
    return {row["item_id"]: (row["item__item_name"], row["value"] or ZERO)
            for row in _posted_lines(start, end).values("item_id", "item__item_name").annotate(value=Sum("line_total_amount"))}


def driver(now, before):
    """The item whose sales moved most, in the direction the total moved."""

    deltas = []
    for item_id in set(now) | set(before):
        name = (now.get(item_id) or before.get(item_id))[0]
        deltas.append((name, (now.get(item_id, ("", ZERO))[1]) - (before.get(item_id, ("", ZERO))[1])))
    if not deltas:
        return None
    total = sum((delta for _, delta in deltas), ZERO)
    pick = max(deltas, key=lambda row: row[1]) if total >= 0 else min(deltas, key=lambda row: row[1])
    return {"name": pick[0], "delta": money_round(pick[1])} if pick[1] else None


# --- per activity ------------------------------------------------------------

def _restaurant(start, end):
    dishes = (_posted_lines(start, end).values("item__item_name").annotate(value=Sum("quantity"), sales=Sum("line_total_amount")).order_by("-value"))
    return {"kind": "dishes", "rows": _bars([{"label": row["item__item_name"], "value": row["value"] or ZERO, "sales": money_round(row["sales"] or ZERO)} for row in dishes])}


def _people(start, end):
    from appointments.services import performance

    rows = sorted(performance(start, end), key=lambda row: -row["sales"])
    services = (_posted_lines(start, end).filter(item__is_stock_tracked=False).values("item__item_name")
                .annotate(value=Sum("line_total_amount"), count=Sum("quantity")).order_by("-value"))
    return {
        "kind": "people",
        "people": _bars([{"label": row["employee"].name, "value": row["sales"], "done": row["done"], "commission": row["commission"]} for row in rows]),
        "services": _bars([{"label": row["item__item_name"], "value": money_round(row["value"] or ZERO), "count": row["count"]} for row in services]),
    }


def _projects():
    from projects.models import Project
    from projects.services import summary

    rows = []
    for project in Project.objects.select_related("customer").order_by("-pk")[:8]:
        data = summary(project)
        rows.append({"project": project, **data, "bar": min(data["progress"], 100)})
    return {"kind": "projects", "rows": rows}


def _production(start, end):
    from manufacturing.models import ProductionRun

    runs = (ProductionRun.objects.filter(run_date__gte=start, run_date__lte=end, status="posted")
            .values("recipe__product__item_name", "recipe__product__default_sale_price")
            .annotate(output=Sum("output_quantity"), cost=Sum("total_cost"), runs=Count("id")).order_by("-cost"))
    rows = []
    for row in runs:
        output = row["output"] or ZERO
        unit = (row["cost"] / output).quantize(Decimal("0.01")) if output else ZERO
        price = row["recipe__product__default_sale_price"] or ZERO
        rows.append({"label": row["recipe__product__item_name"], "value": money_round(row["cost"] or ZERO), "output": output, "runs": row["runs"],
                     "unit_cost": unit, "price": price, "margin": round(float((price - unit) / price * 100)) if price else None})
    return {"kind": "production", "rows": _bars(rows)}


def _expiry(today):
    from batches.services import batches_enabled, expiry_alerts

    if not batches_enabled():
        return None
    alerts = expiry_alerts(today)
    return {"kind": "expiry", "expired": alerts["expired"][:8], "soon": alerts["soon"][:8]}


def activity_section(activity, start, end, today):
    from settings_core.setup_services import module_is_enabled

    if activity == "restaurants":
        return _restaurant(start, end)
    if activity in ("services", "medical", "education") or module_is_enabled("appointments_visits"):
        return _people(start, end)
    if activity == "contracting" or module_is_enabled("projects"):
        return _projects()
    if activity == "manufacturing" or module_is_enabled("manufacturing"):
        return _production(start, end)
    return _expiry(today)


def build_insights(user, lang="ar", today=None):
    from entities.current import effective_activity
    from permissions.services import user_has_permission

    lang = "en" if lang == "en" else "ar"
    words = WORDS[lang]
    today = today or timezone.localdate()
    start, prev_end = today - timedelta(days=DAYS - 1), today - timedelta(days=DAYS)
    prev_start = prev_end - timedelta(days=DAYS - 1)
    can = lambda code: user_has_permission(user, code)  # noqa: E731

    now, before = analytics._sales(start, today), analytics._sales(prev_start, prev_end)
    read = []
    if not now["net"]:
        read.append(words["no_sales"])
    elif not before["net"]:
        read.append(words["sales_first"].format(now=_fmt(now["net"])))
    else:
        pct = analytics.change(now["net"], before["net"])
        trend = words["up"] if pct > 0 else words["down"] if pct < 0 else words["flat"]
        read.append(words["sales"].format(now=_fmt(now["net"]), trend=trend, pct=f"{abs(pct):.0f}", before=_fmt(before["net"])))
    moved = driver(item_sales(start, today), item_sales(prev_start, prev_end)) if before["net"] else None
    if moved:
        read.append((words["driver_up"] if moved["delta"] > 0 else words["driver_down"]).format(name=moved["name"], delta=_fmt(moved["delta"])))
    profit = None
    if can("reports.view_profit_report"):
        from .selectors import profit_totals

        profit = profit_totals(start, today)
        if profit["sales"]:
            margin = round(float(profit["profit"] / profit["sales"] * 100))
            profit["margin"] = margin
            read.append(words["margin"].format(margin=margin))
    weekdays = analytics._weekday_chart(analytics.weekday_pattern(today), lang)
    if not weekdays["empty"]:
        read.append(words["best_day"].format(day=next(row["label"] for row in weekdays["rows"] if row["best"])))
    hours = analytics._hours(analytics.hourly_sales(start, today))
    if hours["peak"] is not None and hours["busiest"]:
        read.append(words["peak"].format(hour=hours["peak"]))
    customers = top_customers(start, today)
    if customers:
        read.append(words["top_customer"].format(name=customers[0]["label"], value=_fmt(customers[0]["value"])))
    if can("reports.view_customer_report"):
        from .aging import aging_rows, aging_totals

        overdue = aging_totals(aging_rows("customers", today))["overdue"]
        if overdue:
            read.append(words["overdue"].format(total=_fmt(overdue)))
    stock = analytics.stock_health(today) if can("inventory.view_cost") else None
    if stock and stock["slow_count"]:
        read.append(words["slow"].format(count=stock["slow_count"], value=_fmt(stock["slow_value"])))

    activity, sub_activity = effective_activity()
    top = analytics.top_items(start, today, limit=6, by="profit" if profit is not None else "sales")
    for row in top:
        row["label"], row["value"] = row["name"], row["profit"] if profit is not None else row["sales"]
    return {
        "start": start, "end": today, "prev_start": prev_start, "prev_end": prev_end,
        "read": read, "now": now, "before": before, "delta": analytics.change(now["net"], before["net"]) if before["net"] else None,
        "profit": profit, "count": now["count"],
        "average": money_round(now["net"] / now["count"]) if now["count"] else ZERO,
        # No quiet line along zero when the earlier period sold nothing.
        "daily": analytics._chart(analytics.daily_sales(start, today), analytics.daily_sales(prev_start, prev_end) if before["net"] else []),
        "categories": by_category(start, today, lang),
        "top": _bars(top), "top_by_profit": profit is not None,
        "customers": customers,
        "weekdays": weekdays, "hours": hours,
        "stock": stock,
        "slow_bars": _bars([{"label": row["name"], "value": row["value"]} for row in stock["slow"]]) if stock else [],
        "activity": activity, "sub_activity": sub_activity,
        "section": activity_section(activity, start, today, today),
    }
