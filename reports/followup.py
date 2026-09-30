"""DIGEST-002: the daily follow-up an owner gets by email, morning and/or evening.

Three parts, each built only from what the recipient may already see on its
own screen (the same permission and module rules as the reports):

* **follow up** — what needs doing: customers who are overdue, suppliers we
  owe, instalments late, items out or running low, batches expiring,
  appointments coming, drafts nobody posted, orders still open;
* **what happened** — the day's figures, from ``reports.digest.daily_digest``
  so they match the daily summary page;
* **key events** — the things an owner wants to hear about: cancellations,
  stock adjustments, cash-shift differences, voided kitchen lines.

The morning email covers yesterday and today's to-dos; the evening email
covers today and tomorrow's. Read-only; nothing is changed.
"""

from datetime import datetime, time, timedelta

from django.db.models import Count
from django.utils import timezone

from permissions.services import user_has_permission
from settings_core.capabilities import capability_enabled
from settings_core.templatetags.hesba_format import money

from .digest import daily_digest

MORNING, EVENING = "morning", "evening"
TOP = 5

WORDS = {
    "ar": {
        MORNING: "متابعة الصبح", EVENING: "ملخص آخر اليوم",
        "overdue_customers": "عملاء متأخرين في السداد", "owed_suppliers": "موردين متأخر لهم فلوس", "instalments": "أقساط متأخرة",
        "out_of_stock": "أصناف خلصت", "low_stock": "أصناف تحت حد الطلب", "expiring": "تشغيلات قربت تنتهي أو انتهت",
        "appointments_today": "مواعيد النهارده", "appointments_tomorrow": "مواعيد بكرة", "sales_drafts": "فواتير بيع مسودة لسه مترحّلتش",
        "purchase_drafts": "فواتير شراء مسودة لسه مترحّلتش", "open_orders": "طلبات مطعم لسه مفتوحة",
        "cancellations": "إلغاءات", "adjustments": "تسويات مخزون", "shift_diff": "فرق في وردية", "voids": "أصناف اتلغت بعد ما راحت المطبخ",
        "nothing_todo": "مفيش حاجة متأخرة. يوم هادي.", "nothing_events": "مفيش أحداث غير عادية.",
    },
    "en": {
        MORNING: "Morning follow-up", EVENING: "End-of-day summary",
        "overdue_customers": "Customers overdue", "owed_suppliers": "Suppliers we owe (overdue)", "instalments": "Overdue instalments",
        "out_of_stock": "Items out of stock", "low_stock": "Items below reorder level", "expiring": "Batches expired or expiring",
        "appointments_today": "Today's appointments", "appointments_tomorrow": "Tomorrow's appointments", "sales_drafts": "Sales drafts not posted",
        "purchase_drafts": "Purchase drafts not posted", "open_orders": "Restaurant orders still open",
        "cancellations": "Cancellations", "adjustments": "Stock adjustments", "shift_diff": "Shift difference", "voids": "Kitchen lines voided",
        "nothing_todo": "Nothing overdue. A quiet day.", "nothing_events": "Nothing unusual happened.",
    },
}


def _can(user, *codes):
    return all(user_has_permission(user, code) for code in codes)


def _module(slug):
    from settings_core.setup_services import module_is_enabled

    return module_is_enabled(slug)


def _day_bounds(day):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)


def _item(key, words, count, lines=(), amount=None, path=""):
    return {"key": key, "title": words[key], "count": count, "amount": amount, "lines": list(lines)[:TOP], "path": path}


def todo(user, today, slot, lang="ar"):
    """What needs following up, most urgent first."""

    words = WORDS[lang]
    items = []
    if _can(user, "reports.view_customer_report"):
        from .aging import aging_rows

        late = [row for row in aging_rows("customers", today) if row["overdue"] > 0]
        if late:
            items.append(_item("overdue_customers", words, len(late), (f"{row['name']} — {money(row['overdue'])}" for row in late),
                               sum(row["overdue"] for row in late), "/reports/aging/?party=customers"))
        if capability_enabled("installments"):
            from installments.services import overdue_summary

            summary = overdue_summary(today)
            if summary["count"]:
                items.append(_item("instalments", words, summary["count"], (), summary["amount"], "/installments/"))
    if _can(user, "reports.view_supplier_report"):
        from .aging import aging_rows

        owed = [row for row in aging_rows("suppliers", today) if row["overdue"] > 0]
        if owed:
            items.append(_item("owed_suppliers", words, len(owed), (f"{row['name']} — {money(row['overdue'])}" for row in owed),
                               sum(row["overdue"] for row in owed), "/reports/aging/?party=suppliers"))
    if _can(user, "reports.view_inventory_report"):
        from .selectors import stock_levels_by_item

        levels = list(stock_levels_by_item())
        out = [row for row in levels if row["quantity"] <= 0]
        low = [row for row in levels if row["quantity"] > 0 and row["min_stock"] > 0 and row["quantity"] <= row["min_stock"]]
        if out:
            items.append(_item("out_of_stock", words, len(out), (row["item_label"] for row in out), path="/reports/inventory/"))
        if low:
            items.append(_item("low_stock", words, len(low), (f"{row['item_label']} ({row['quantity'].normalize():f})" for row in low), path="/reports/inventory/"))
        if capability_enabled("batches_expiry"):
            from batches.services import expiry_alerts

            alerts = expiry_alerts(today)
            batches = alerts["expired"] + alerts["soon"]
            if batches:
                items.append(_item("expiring", words, len(batches), (str(batch) for batch in batches), path="/batches/"))
    if _module("appointments_visits") and _can(user, "sales.view_sales_invoices"):
        from appointments.models import HOLDS_TIME, Appointment

        day = today if slot == MORNING else today + timedelta(days=1)
        start, end = _day_bounds(day)
        rows = Appointment.objects.filter(starts_at__gte=start, starts_at__lt=end, status__in=HOLDS_TIME).select_related("customer", "employee")
        if rows:
            key = "appointments_today" if slot == MORNING else "appointments_tomorrow"
            lines = (f"{timezone.localtime(row.starts_at):%H:%M} {row.customer.name}" + (f" — {row.employee.name}" if row.employee else "") for row in rows)
            items.append(_item(key, words, len(rows), lines, path=f"/appointments/?day={day.isoformat()}"))
    if _can(user, "sales.view_sales_invoices"):
        from sales.models import SalesInvoice

        drafts = SalesInvoice.objects.filter(status="draft", invoice_date__lte=today)
        if drafts.exists():
            items.append(_item("sales_drafts", words, drafts.count(), (d.invoice_number for d in drafts.order_by("invoice_date")[:TOP]), path="/sales/?status=draft"))
        if _module("tables_orders") and slot == EVENING:
            from restaurant.models import Order, OrderStatus

            open_orders = Order.objects.filter(status=OrderStatus.OPEN)
            if open_orders.exists():
                items.append(_item("open_orders", words, open_orders.count(), (o.number for o in open_orders[:TOP]), path="/restaurant/"))
    if _can(user, "purchases.view_purchase_invoices"):
        from purchases.models import PurchaseInvoice

        drafts = PurchaseInvoice.objects.filter(status="draft", invoice_date__lte=today)
        if drafts.exists():
            items.append(_item("purchase_drafts", words, drafts.count(), (d.invoice_number for d in drafts.order_by("invoice_date")[:TOP]), path="/purchases/?status=draft"))
    return items


def events(user, day, lang="ar"):
    """The day's events worth an owner's attention."""

    from audit.models import AuditLog

    words = WORDS[lang]
    found = []
    start, end = _day_bounds(day)
    logs = AuditLog.objects.filter(created_at__gte=start, created_at__lt=end)
    if _can(user, "audit.view_audit_log") or _can(user, "reports.view_all_sales_report"):
        cancels = logs.filter(action__startswith="cancel_").values("action").annotate(n=Count("id")).order_by("-n")
        total = sum(row["n"] for row in cancels)
        if total:
            found.append({"key": "cancellations", "title": words["cancellations"], "count": total,
                          "lines": [f"{row['action'].replace('cancel_', '').replace('_', ' ')}: {row['n']}" for row in cancels][:TOP]})
        voids = logs.filter(action="void_order_line").count()
        if voids:
            found.append({"key": "voids", "title": words["voids"], "count": voids, "lines": []})
    if _can(user, "inventory.view_stock"):
        adjustments = logs.filter(action="adjust_stock").count()
        if adjustments:
            found.append({"key": "adjustments", "title": words["adjustments"], "count": adjustments, "lines": []})
    return found


def followup(user, today, slot, lang="ar"):
    """Everything one recipient's email holds."""

    covered = today - timedelta(days=1) if slot == MORNING else today
    data = daily_digest(user, covered)
    shifts = data.get("shifts", [])
    happened = events(user, covered, lang)
    if shifts:
        happened.append({"key": "shift_diff", "title": WORDS[lang]["shift_diff"], "count": len(shifts),
                         "lines": [f"{s['cashier']}: {money(s['difference'])}" for s in shifts]})
    return {"slot": slot, "title": WORDS[lang][slot], "today": today, "covered": covered, "digest": data,
            "todo": todo(user, today, slot, lang), "events": happened, "words": WORDS[lang]}
