"""DIGEST-001: the owner's summary of one day, on one page and as one WhatsApp message.

Read-only. Every figure comes from the same selectors as the reports and the
dashboard (so the numbers match them), and every section is built only for a
viewer who may see it on its own report.
"""

from decimal import Decimal

from django.db.models import Count, Sum

from permissions.services import user_has_permission
from settings_core.capabilities import capability_enabled
from settings_core.templatetags.hesba_format import money


ZERO = Decimal("0.00")


def _can(user, *codes):
    return all(user_has_permission(user, code) for code in codes)


def daily_digest(user, day):
    from sales.models import CustomerPayment, SalesInvoice, SalesLine, SalesReturn

    from .selectors import cashbox_report, profit_totals, stock_alert_counts

    data = {"day": day, "sections": []}
    if _can(user, "reports.view_sales_report", "reports.view_all_sales_report"):
        invoices = SalesInvoice.objects.filter(status="posted", invoice_date=day)
        totals = invoices.aggregate(count=Count("id"), total=Sum("total_amount"), cash=Sum("paid_now"), credit=Sum("remaining_due"))
        returns = SalesReturn.objects.filter(status="posted", return_date=day).aggregate(total=Sum("total_amount"))["total"] or ZERO
        top = list(SalesLine.objects.filter(invoice__in=invoices).values("item__item_name").annotate(qty=Sum("quantity"), amount=Sum("line_total_amount")).order_by("-amount")[:5])
        data["sales"] = {"count": totals["count"] or 0, "total": totals["total"] or ZERO, "cash": totals["cash"] or ZERO, "credit": totals["credit"] or ZERO,
                         "returns": returns, "top": top}
    if _can(user, "reports.view_profit_report"):
        profit = profit_totals(day, day)
        data["profit"] = {"sales": profit["sales"], "cost": profit["cost"], "gross": profit["profit"]}
        if _can(user, "cashboxes.view_expenses"):
            from expenses.services import expense_total

            data["profit"]["expenses"] = expense_total(day, day)
            data["profit"]["net"] = profit["profit"] - data["profit"]["expenses"]
    if _can(user, "reports.view_cashbox_report"):
        rows = cashbox_report(date_from=day, date_to=day)
        data["cash"] = {"rows": rows, "in": sum((row["cash_in"] for row in rows), ZERO), "out": sum((row["cash_out"] for row in rows), ZERO),
                        "balance": sum((row["balance"] for row in rows), ZERO)}
    if _can(user, "reports.view_customer_report"):
        from reports.aging import aging_rows

        collected = CustomerPayment.objects.filter(status="posted", payment_date=day).aggregate(total=Sum("amount"))["total"] or ZERO
        aging = aging_rows("customers", day)
        data["customers"] = {"collected": collected, "owed": sum((row["total"] for row in aging), ZERO), "overdue": sum((row["overdue"] for row in aging), ZERO),
                             "overdue_count": sum(1 for row in aging if row["overdue"] > 0)}
        if capability_enabled("installments"):
            from installments.services import overdue_summary

            data["customers"]["instalments"] = overdue_summary(day)
    if _can(user, "reports.view_inventory_report"):
        counts = stock_alert_counts()
        data["stock"] = {"low": counts["low_stock"], "out": counts["out_of_stock"]}
        if capability_enabled("batches_expiry"):
            from batches.services import expiry_alerts

            expiring = expiry_alerts(day)
            data["stock"].update({"expired": len(expiring["expired"]), "expiring": len(expiring["soon"])})
    if _can(user, "cashboxes.view_finance"):
        from shifts.models import Shift

        shifts = Shift.objects.filter(status="closed", closed_at__date=day).select_related("cashier")
        data["shifts"] = [{"cashier": shift.cashier.get_username(), "difference": shift.difference} for shift in shifts if shift.difference]
    return data


def digest_text(data, company, lang="ar"):
    """The same summary as plain lines, for a WhatsApp message."""

    def m(value):
        return f"{money(value)} {company['currency']}"

    en = lang == "en"
    lines = [f"{'Daily summary' if en else 'ملخص يوم'} {data['day']:%Y-%m-%d} — {company['name']}"]
    if "sales" in data:
        s = data["sales"]
        lines.append(f"{'Sales' if en else 'المبيعات'}: {m(s['total'])} ({s['count']} {'invoices' if en else 'فاتورة'}) — {'cash' if en else 'نقدي'} {m(s['cash'])}, {'credit' if en else 'آجل'} {m(s['credit'])}")
        if s["returns"]:
            lines.append(f"{'Returns' if en else 'المرتجعات'}: {m(s['returns'])}")
    if "profit" in data:
        p = data["profit"]
        lines.append(f"{'Gross profit' if en else 'مجمل الربح'}: {m(p['gross'])}" + (f" — {'net after expenses' if en else 'الصافي بعد المصروفات'} {m(p['net'])}" if "net" in p else ""))
    if "cash" in data:
        c = data["cash"]
        lines.append(f"{'Cash' if en else 'الخزن'}: {'in' if en else 'داخل'} {m(c['in'])}، {'out' if en else 'خارج'} {m(c['out'])}، {'balance' if en else 'الرصيد'} {m(c['balance'])}")
    if "customers" in data:
        k = data["customers"]
        lines.append(f"{'Collected' if en else 'التحصيلات'}: {m(k['collected'])} — {'owed by customers' if en else 'مستحق على العملاء'} {m(k['owed'])}" +
                     (f"، {'overdue' if en else 'متأخر'} {m(k['overdue'])} ({k['overdue_count']})" if k["overdue"] else ""))
        if k.get("instalments", {}).get("count"):
            lines.append(f"{'Overdue instalments' if en else 'أقساط متأخرة'}: {k['instalments']['count']} — {m(k['instalments']['amount'])}")
    if "stock" in data:
        st = data["stock"]
        parts = [f"{'out of stock' if en else 'خلصان'} {st['out']}", f"{'below minimum' if en else 'تحت الحد'} {st['low']}"]
        if st.get("expired") or st.get("expiring"):
            parts.append(f"{'expired batches' if en else 'تشغيلات منتهية'} {st.get('expired', 0)}، {'expiring soon' if en else 'قربت تنتهي'} {st.get('expiring', 0)}")
        lines.append(f"{'Stock' if en else 'المخزون'}: " + "، ".join(parts))
    for shift in data.get("shifts", []):
        lines.append(f"{'Shift' if en else 'وردية'} {shift['cashier']}: {'difference' if en else 'فرق'} {m(shift['difference'])}")
    return "\n".join(lines)
