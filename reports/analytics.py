"""DASH-002: the dashboard's analytics. Every figure has a stated formula.

Replaces the old "activity score" (100 minus fixed penalties), which could not
be explained or acted on. Each metric here is a plain number from the books,
compared with the previous period of the same length, and ships the sentence
that says how it was computed. All reads, no writes.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Max, Q, Sum
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone

from config.money import money_round
from inventory.models import StockMovement
from master_data.models import Item
from sales.models import SalesInvoice, SalesInvoiceStatus, SalesLine, SalesReturn, SalesReturnStatus

from taxes.models import SalesReturnLineTax
from taxes.services import returns_tax

from .selectors import STOCK_IN_TYPES, STOCK_OUT_TYPES, cashbox_report, profit_totals, supplier_report


ZERO = Decimal("0")
PERIODS = ("today", "7d", "month", "30d")
SLOW_DAYS = 60
COVER_ALERT_DAYS = 7


def period_bounds(key, today):
    """(start, end, previous_start, previous_end) for a period key."""

    if key == "today":
        start = end = today
    elif key == "7d":
        start, end = today - timedelta(days=6), today
    elif key == "30d":
        start, end = today - timedelta(days=29), today
    else:  # month to date, compared with the same days of last month
        start, end = today.replace(day=1), today
    length = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length - 1)
    return start, end, previous_start, previous_end


def change(current, previous):
    """Percent change, or None when there is nothing to compare with."""

    if not previous:
        return None
    return float((Decimal(current) - Decimal(previous)) / abs(Decimal(previous)) * 100)


def _sales(start, end):
    # HG-015: sales are counted net of VAT; refunds net of the tax they gave back.
    posted = SalesInvoice.objects.filter(status=SalesInvoiceStatus.POSTED, invoice_date__gte=start, invoice_date__lte=end)
    totals = posted.aggregate(total=Sum(F("total_amount") - F("tax_amount")), count=Count("id"))
    return_qs = SalesReturn.objects.filter(status=SalesReturnStatus.POSTED, return_date__gte=start, return_date__lte=end)
    returns = (return_qs.aggregate(total=Sum("total_amount"))["total"] or ZERO) - returns_tax(return_qs)
    gross = totals["total"] or ZERO
    return {"net": money_round(gross - returns), "count": totals["count"] or 0, "returns": money_round(returns), "gross": money_round(gross)}


def daily_sales(start, end):
    """[(date, net sales)] for every day in the window, zero-filled."""

    by_day = defaultdict(Decimal)
    rows = SalesInvoice.objects.filter(status=SalesInvoiceStatus.POSTED, invoice_date__gte=start, invoice_date__lte=end).values("invoice_date").annotate(total=Sum(F("total_amount") - F("tax_amount")))
    for row in rows:
        by_day[row["invoice_date"]] += row["total"] or ZERO
    returns = SalesReturn.objects.filter(status=SalesReturnStatus.POSTED, return_date__gte=start, return_date__lte=end).values("return_date").annotate(total=Sum("total_amount"))
    for row in returns:
        by_day[row["return_date"]] -= row["total"] or ZERO
    returned_tax = SalesReturnLineTax.objects.filter(
        return_line__sales_return__status=SalesReturnStatus.POSTED,
        return_line__sales_return__return_date__gte=start, return_line__sales_return__return_date__lte=end,
    ).values("return_line__sales_return__return_date").annotate(total=Sum("tax_amount"))
    for row in returned_tax:
        by_day[row["return_line__sales_return__return_date"]] += row["total"] or ZERO
    days = []
    day = start
    while day <= end:
        days.append((day, money_round(by_day.get(day, ZERO))))
        day += timedelta(days=1)
    return days


def daily_profit(start, end):
    """[(date, gross profit)] for every day, by the same formula as profit_totals.

    Per day: line profit on posted invoices, less what posted returns took back
    (refund net of its tax, minus the stock cost that came back).
    """

    by_day = defaultdict(Decimal)
    lines = SalesLine.objects.filter(invoice__status=SalesInvoiceStatus.POSTED, invoice__invoice_date__gte=start, invoice__invoice_date__lte=end)
    for row in lines.values("invoice__invoice_date").annotate(total=Sum("line_profit_amount")):
        by_day[row["invoice__invoice_date"]] += row["total"] or ZERO
    returns = SalesReturn.objects.filter(status=SalesReturnStatus.POSTED, return_date__gte=start, return_date__lte=end)
    for row in returns.values("return_date").annotate(total=Sum("total_amount"), cost=Sum("cost_amount")):
        by_day[row["return_date"]] -= (row["total"] or ZERO) - (row["cost"] or ZERO)
    returned_tax = SalesReturnLineTax.objects.filter(
        return_line__sales_return__status=SalesReturnStatus.POSTED,
        return_line__sales_return__return_date__gte=start, return_line__sales_return__return_date__lte=end,
    ).values("return_line__sales_return__return_date").annotate(total=Sum("tax_amount"))
    for row in returned_tax:
        by_day[row["return_line__sales_return__return_date"]] += row["total"] or ZERO
    days, day = [], start
    while day <= end:
        days.append((day, money_round(by_day.get(day, ZERO))))
        day += timedelta(days=1)
    return days


def payment_split(start, end):
    """How posted sales in the window were settled at the till: paid now vs on account."""

    totals = SalesInvoice.objects.filter(status=SalesInvoiceStatus.POSTED, invoice_date__gte=start, invoice_date__lte=end).aggregate(
        paid=Sum("paid_now"), credit=Sum("remaining_due"))
    return {"paid": money_round(totals["paid"] or ZERO), "credit": money_round(totals["credit"] or ZERO)}


def weekday_pattern(today, weeks=4):
    """Average net sales per weekday over the last ``weeks`` whole weeks, Saturday first."""

    start = today - timedelta(days=7 * weeks - 1)
    sums = defaultdict(Decimal)
    for day, value in daily_sales(start, today):
        sums[day.weekday()] += value
    order = (5, 6, 0, 1, 2, 3, 4)  # Saturday .. Friday, the Egyptian working week
    return [(weekday, money_round(sums[weekday] / weeks)) for weekday in order]


def hourly_sales(start, end):
    """Invoice count per hour of day (local time) in the window."""

    counts = dict(
        SalesInvoice.objects.filter(status=SalesInvoiceStatus.POSTED, invoice_date__gte=start, invoice_date__lte=end)
        .annotate(hour=ExtractHour("created_at", tzinfo=timezone.get_current_timezone()))
        .values("hour")
        .annotate(n=Count("id"))
        .values_list("hour", "n")
    )
    return [(hour, counts.get(hour, 0)) for hour in range(24)]


def top_items(start, end, limit=5, by="sales"):
    lines = SalesLine.objects.filter(invoice__status=SalesInvoiceStatus.POSTED, invoice__invoice_date__gte=start, invoice__invoice_date__lte=end)
    rows = (
        lines.values("item_id", "item__item_code", "item__item_name")
        .annotate(sales=Sum("line_total_amount"), profit=Sum("line_profit_amount"), quantity=Sum("quantity"))
        .order_by(f"-{by}")[:limit]
    )
    return [
        {"code": row["item__item_code"], "name": row["item__item_name"], "sales": money_round(row["sales"] or ZERO), "profit": money_round(row["profit"] or ZERO), "quantity": row["quantity"] or ZERO}
        for row in rows
    ]


def _stock_by_item():
    totals = StockMovement.objects.filter(item__active=True, item__is_stock_tracked=True).values("item_id").annotate(
        in_qty=Sum("quantity", filter=Q(movement_type__in=STOCK_IN_TYPES)),
        out_qty=Sum("quantity", filter=Q(movement_type__in=STOCK_OUT_TYPES)),
    )
    return {row["item_id"]: (row["in_qty"] or ZERO) - (row["out_qty"] or ZERO) for row in totals}


def stock_health(today, limit=5):
    """Slow movers (money sitting on the shelf) and items about to run out."""

    on_hand = {item_id: qty for item_id, qty in _stock_by_item().items() if qty > 0}
    if not on_hand:
        return {"slow": [], "slow_value": ZERO, "slow_count": 0, "running_out": []}
    items = {item.pk: item for item in Item.objects.filter(pk__in=on_hand)}
    last_sale = dict(
        SalesLine.objects.filter(invoice__status=SalesInvoiceStatus.POSTED, item_id__in=on_hand).values("item_id").annotate(last=Max("invoice__invoice_date")).values_list("item_id", "last")
    )
    window_start = today - timedelta(days=29)
    sold_30 = dict(
        SalesLine.objects.filter(invoice__status=SalesInvoiceStatus.POSTED, item_id__in=on_hand, invoice__invoice_date__gte=window_start, invoice__invoice_date__lte=today)
        .values("item_id").annotate(q=Sum("quantity")).values_list("item_id", "q")
    )
    slow, running_out = [], []
    for item_id, qty in on_hand.items():
        item = items[item_id]
        value = money_round(qty * item.average_cost)
        last = last_sale.get(item_id)
        idle_days = (today - last).days if last else None
        if idle_days is None or idle_days >= SLOW_DAYS:
            slow.append({"code": item.item_code, "name": item.item_name, "quantity": qty, "value": value, "idle_days": idle_days})
        per_day = (sold_30.get(item_id) or ZERO) / Decimal(30)
        if per_day > 0:
            cover = float(qty / per_day)
            if cover <= COVER_ALERT_DAYS:
                running_out.append({"code": item.item_code, "name": item.item_name, "quantity": qty, "cover_days": round(cover, 1)})
    slow.sort(key=lambda row: -row["value"])
    running_out.sort(key=lambda row: row["cover_days"])
    return {
        "slow": slow[:limit],
        "slow_value": money_round(sum((row["value"] for row in slow), ZERO)),
        "slow_count": len(slow),
        "running_out": running_out[:limit],
    }


def build_analytics(held, period_key, today=None, lang="ar"):
    """Everything the analytics section shows, limited to what ``held`` allows."""

    today = today or timezone.localdate()
    period_key = period_key if period_key in PERIODS else "month"
    start, end, prev_start, prev_end = period_bounds(period_key, today)
    can = lambda code: code in held  # noqa: E731
    out = {"period": period_key, "start": start, "end": end, "prev_start": prev_start, "prev_end": prev_end, "metrics": []}
    if not (can("reports.view_sales_report") and can("reports.view_all_sales_report")):
        out["available"] = False
        return out
    out["available"] = True
    words = METRIC_WORDS["en" if lang == "en" else "ar"]

    def metric(key, value, previous=None, unit="currency", good_up=True, extra=None):
        delta = change(value, previous) if previous is not None else None
        out["metrics"].append({
            "key": key,
            "label": words[key]["label"],
            "how": words[key]["how"],
            "value": value,
            "previous": previous,
            "delta": delta,
            "trend": None if delta is None else ("up" if delta > 0 else "down" if delta < 0 else "flat"),
            "good": None if delta is None or delta == 0 else ((delta > 0) == good_up),
            "unit": unit,
            **(extra or {}),
        })

    now, before = _sales(start, end), _sales(prev_start, prev_end)
    metric("net_sales", now["net"], before["net"])
    average = money_round(now["net"] / now["count"]) if now["count"] else ZERO
    previous_average = money_round(before["net"] / before["count"]) if before["count"] else ZERO
    metric("avg_invoice", average, previous_average, extra={"count": now["count"]})

    if can("reports.view_profit_report"):
        profit_now = profit_totals(start, end)
        profit_before = profit_totals(prev_start, prev_end)
        margin = float(profit_now["profit"] / profit_now["sales"] * 100) if profit_now["sales"] else None
        metric("gross_profit", money_round(profit_now["profit"]), money_round(profit_before["profit"]), extra={"margin": margin})
        if can("cashboxes.view_expenses"):
            from expenses.services import expense_total

            exp_now, exp_before = expense_total(start, end), expense_total(prev_start, prev_end)
            metric("expenses", exp_now, exp_before, good_up=False)
            metric("net_profit", money_round(profit_now["profit"] - exp_now), money_round(profit_before["profit"] - exp_before))

    if can("cashboxes.view_finance"):
        cash = money_round(sum((row["balance"] for row in cashbox_report()), ZERO))
        metric("cash", cash, unit="currency")

    if can("reports.view_customer_report"):
        from .aging import aging_rows, aging_totals

        totals = aging_totals(aging_rows("customers", today))
        metric("receivables", totals["total"], good_up=False, extra={"overdue": totals["overdue"]})

    if can("reports.view_supplier_report"):
        payable = money_round(sum((row["balance"] for row in supplier_report() if row["balance"] > 0), ZERO))
        metric("payables", payable, good_up=False)

    days = daily_sales(start, end) if period_key != "today" else daily_sales(today - timedelta(days=6), today)
    previous_days = daily_sales(prev_start, prev_end) if period_key != "today" else daily_sales(today - timedelta(days=13), today - timedelta(days=7))
    out["daily"] = _chart(days, previous_days)
    out["daily"]["previous_range"] = (previous_days[0][0], previous_days[-1][0]) if previous_days else None
    out["spark"] = {"net_sales": _spark([value for _, value in days])}
    out["hero"] = _hero(days, today)
    if can("reports.view_profit_report"):
        profit_window = (start, end) if period_key != "today" else (today - timedelta(days=6), today)
        profit_days = daily_profit(*profit_window)
        out["profit_daily"] = _chart(profit_days, [])
        out["spark"]["gross_profit"] = _spark([value for _, value in profit_days])
    out["weekdays"] = _weekday_chart(weekday_pattern(today), lang)
    split = payment_split(start, end)
    out["split"] = _donut(split["paid"], split["credit"])
    out["hours"] = _hours(hourly_sales(today - timedelta(days=29), today))
    out["top"] = top_items(start, end, by="profit" if can("reports.view_profit_report") else "sales")
    out["top_by_profit"] = can("reports.view_profit_report")
    if can("inventory.view_cost"):
        out["stock"] = stock_health(today)
    return out


def _chart(days, previous_days):
    """Bars for this period, a quiet line for the previous one, on one axis."""

    values = [value for _, value in days]
    previous = [value for _, value in previous_days][: len(values)]
    top = max(values + previous + [ZERO])
    if top <= 0:
        return {"bars": [], "empty": True}
    step = _nice_step(top)
    ceiling = step * 4
    width, height, left, bottom = 640, 200, 44, 24
    plot_w, plot_h = width - left - 8, height - bottom - 8
    slot = plot_w / max(len(values), 1)
    bar_w = min(24, max(4, slot * 0.6))
    bars = []
    for index, (day, value) in enumerate(days):
        h = float(value / ceiling) * plot_h if value > 0 else 0
        x = left + slot * index + (slot - bar_w) / 2
        y = 8 + plot_h - h
        radius = min(4, bar_w / 2, h)
        # 4px rounded data-end, square at the baseline.
        path = (
            f"M{x:.1f},{8 + plot_h:.1f} V{y + radius:.1f} Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f} "
            f"H{x + bar_w - radius:.1f} Q{x + bar_w:.1f},{y:.1f} {x + bar_w:.1f},{y + radius:.1f} V{8 + plot_h:.1f} Z"
        ) if h > 0 else ""
        bars.append({
            "path": path, "hit_x": _n(x - (slot - bar_w) / 2), "hit_w": _n(slot),
            "day": day, "value": value, "previous": previous[index] if index < len(previous) else None,
            "cx": _n(x + bar_w / 2),
        })
    prev_xy = [(left + slot * i + slot / 2, 8 + plot_h - float(v / ceiling) * plot_h) for i, v in enumerate(previous)]
    points = " ".join(f"{round(x, 1)},{round(y, 1)}" for x, y in prev_xy)
    # Dots only where the previous period actually sold, so the line reads as data, not decoration.
    prev_dots = [{"x": _n(x), "y": _n(y)} for (x, y), v in zip(prev_xy, previous) if v > 0]
    grid = [{"y": _n(8 + plot_h - plot_h * i / 4), "label": step * i, "text": _short(step * i)} for i in range(5)]
    label_every = max(1, len(days) // 6)
    ticks = [{"x": bar["cx"], "label": bar["day"]} for i, bar in enumerate(bars) if i % label_every == 0]
    best = max(bars, key=lambda bar: bar["value"])
    return {"bars": bars, "previous_points": points, "prev_dots": prev_dots, "grid": grid, "ticks": ticks, "width": width, "height": height, "left": left, "label_x": left - 6, "tick_y": height - 6, "best": best, "empty": False}


def _spark(values, width=120, height=32):
    """A tiny trend line for a metric card: points only, no axis."""

    if len(values) < 2 or not any(values):
        return ""
    low, high = min(values + [ZERO]), max(values)
    span = float(high - low) or 1.0
    step = width / (len(values) - 1)
    return " ".join(f"{_n(step * i)},{_n(2 + (height - 4) * (1 - float(v - low) / span))}" for i, v in enumerate(values))


def _smooth(points):
    """A Catmull-Rom curve through the points, as SVG cubic segments, so the
    banner's trend reads as a flowing line rather than a saw blade."""

    if len(points) < 2:
        return ""
    d = [f"M{_n(points[0][0])},{_n(points[0][1])}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{_n(c1[0])},{_n(c1[1])} {_n(c2[0])},{_n(c2[1])} {_n(p2[0])},{_n(p2[1])}")
    return " ".join(d)


def _hero(days, today, width=300, height=96):
    """The welcome banner's live card: the period's trend (a 3-day rolling
    average, smoothed) and today's sales against the average day before it."""

    values = [value for _, value in days]
    rolling = [sum(values[max(0, i - 2): i + 1], ZERO) / len(values[max(0, i - 2): i + 1]) for i in range(len(values))]
    line, area, last = "", "", None
    if len(rolling) >= 2 and any(rolling):
        top = max(rolling) or Decimal(1)
        step = width / (len(rolling) - 1)
        points = [(step * i, 6 + (height - 12) * (1 - float(v / top))) for i, v in enumerate(rolling)]
        line = _smooth(points)
        area = f"{line} L{_n(width)},{_n(height)} L0,{_n(height)} Z"
        last = (_n(points[-1][0]), _n(points[-1][1]))
    earlier = [value for day, value in days if day < today]
    average = sum(earlier, ZERO) / len(earlier) if earlier else ZERO
    today_value = next((value for day, value in days if day == today), ZERO)
    pct = round(float(today_value / average) * 100) if average > 0 else None
    circumference = 2 * 3.141592653589793 * 30
    ring = min(pct, 100) / 100 * circumference if pct is not None else 0
    return {
        "line": line, "area": area, "width": width, "height": height, "last": last,
        "today": money_round(today_value), "average": money_round(average), "pct": pct,
        "ring": f"{_n(ring)} {_n(circumference)}",
    }


WEEKDAY_WORDS = {
    "ar": {5: "السبت", 6: "الأحد", 0: "الاتنين", 1: "التلات", 2: "الأربع", 3: "الخميس", 4: "الجمعة"},
    "en": {5: "Sat", 6: "Sun", 0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"},
}


def _weekday_chart(rows, lang):
    """Horizontal bars, one per weekday, widths relative to the best day."""

    top = max((value for _, value in rows), default=ZERO)
    if top <= 0:
        return {"rows": [], "empty": True}
    words = WEEKDAY_WORDS["en" if lang == "en" else "ar"]
    best = max(rows, key=lambda row: row[1])[0]
    return {"empty": False, "rows": [
        {"weekday": weekday, "label": words[weekday], "value": value, "pct": _n(float(value / top) * 100), "best": weekday == best}
        for weekday, value in rows
    ]}


def _donut(paid, credit, radius=52):
    """Two arcs on one ring (stroke-dasharray), with a 2px gap between them."""

    total = paid + credit
    if total <= 0:
        return {"empty": True}
    circumference = 2 * 3.141592653589793 * radius
    paid_len = float(paid / total) * circumference
    credit_len = circumference - paid_len
    gap = 2 if paid and credit else 0
    return {
        "empty": False, "paid": paid, "credit": credit, "total": money_round(total),
        "paid_pct": round(float(paid / total) * 100), "credit_pct": 100 - round(float(paid / total) * 100),
        "r": radius, "c": _n(circumference),
        "paid_dash": f"{_n(max(paid_len - gap, 0))} {_n(circumference)}",
        "credit_dash": f"{_n(max(credit_len - gap, 0))} {_n(circumference)}",
        "credit_offset": _n(-paid_len),
    }


def _n(value):
    """An SVG coordinate as text. Never let the template localize it: under the
    Arabic locale 44.3 renders as "44,3", which SVG reads as two numbers."""

    return f"{value:.1f}"


def _short(value):
    """Axis label: 2500 -> 2.5K, 1200000 -> 1.2M."""

    value = float(value)
    for limit, suffix in ((1_000_000, "M"), (1_000, "K")):
        if abs(value) >= limit:
            text = f"{value / limit:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{value:.0f}"


def _nice_step(top):
    raw = float(top) / 4
    magnitude = 10 ** (len(str(int(raw))) - 1) if raw >= 1 else 1
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * magnitude:
            return Decimal(str(factor * magnitude))
    return Decimal(str(10 * magnitude))


def _hours(rows):
    busiest = max((count for _, count in rows), default=0)
    open_hours = [(hour, count) for hour, count in rows if count] or rows[8:22]
    first = min(hour for hour, _ in open_hours) if any(count for _, count in rows) else 8
    last = max(hour for hour, _ in open_hours) if any(count for _, count in rows) else 21
    cells = []
    for hour, count in rows[first:last + 1]:
        level = 0 if not busiest or not count else min(4, 1 + int(3 * count / busiest))
        cells.append({"hour": hour, "count": count, "level": level})
    peak = max(rows, key=lambda row: row[1]) if busiest else None
    return {"cells": cells, "peak": peak[0] if peak else None, "busiest": busiest}


METRIC_WORDS = {
    "ar": {
        "net_sales": {"label": "صافي المبيعات", "how": "إجمالي فواتير البيع المرحّلة في الفترة ناقص مرتجعات البيع المرحّلة في نفس الفترة. المقارنة بفترة قبلها بنفس عدد الأيام."},
        "avg_invoice": {"label": "متوسط الفاتورة", "how": "صافي المبيعات ÷ عدد فواتير البيع المرحّلة في الفترة."},
        "gross_profit": {"label": "مجمل الربح", "how": "المبيعات ناقص تكلفة البضاعة المباعة بتكلفتها الفعلية في المخزن، بعد المرتجعات. الهامش = مجمل الربح ÷ المبيعات."},
        "expenses": {"label": "المصروفات", "how": "مجموع المصروفات المرحّلة بتاريخ جوّه الفترة. المصروف الملغي مش محسوب."},
        "net_profit": {"label": "صافي الربح", "how": "مجمل الربح ناقص المصروفات في نفس الفترة."},
        "cash": {"label": "السيولة في الخزن", "how": "رصيد كل الخزن النشطة دلوقتي: الرصيد الافتتاحي + كل حركات الدخول − كل حركات الخروج."},
        "receivables": {"label": "فلوسك برّه (آجل العملاء)", "how": "مجموع أرصدة العملاء المدينة. «متأخر» = اللي عدّى مدة الآجل المسموحة، والأقدم بيتسدد الأول."},
        "payables": {"label": "المستحق للموردين", "how": "مجموع أرصدة الموردين الدائنة دلوقتي."},
    },
    "en": {
        "net_sales": {"label": "Net sales", "how": "Posted sales invoices in the period minus posted sales returns in the same period, compared with the previous period of the same length."},
        "avg_invoice": {"label": "Average invoice", "how": "Net sales ÷ number of posted sales invoices in the period."},
        "gross_profit": {"label": "Gross profit", "how": "Sales minus the actual stock cost of the goods sold, after returns. Margin = gross profit ÷ sales."},
        "expenses": {"label": "Expenses", "how": "Posted expenses dated in the period. Cancelled expenses are not counted."},
        "net_profit": {"label": "Net profit", "how": "Gross profit minus expenses for the same period."},
        "cash": {"label": "Cash on hand", "how": "Balance of every active cashbox now: opening balance + all money in − all money out."},
        "receivables": {"label": "Owed to you (customers)", "how": "Sum of customer balances owed to you. Overdue = past the allowed credit days, oldest settled first."},
        "payables": {"label": "Owed to suppliers", "how": "Sum of what you owe suppliers now."},
    },
}
