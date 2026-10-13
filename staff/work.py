"""FB-STAFF: what each person did this month, from wherever their work is recorded.

The team page promised "see their work and commission" but showed only a
form. This gathers, read-only, what the existing reports already count:
- appointments and visits marked done, the sales billed for them and the
  commission on it (appointments.services.performance);
- as a sales rep: net sales and the commission on it (reports.reps);
- the groups they teach (education), the orders they served and the
  deliveries they took (restaurants).
Only sources that hold something for this person are shown, so a shop
assistant's page is not full of a clinic's or a school's empty boxes.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

ZERO = Decimal("0.00")


def month_range(today=None):
    today = today or timezone.localdate()
    return today.replace(day=1), today


def _appointments(date_from, date_to):
    from appointments.services import performance

    return {row["employee"].pk: row for row in performance(date_from, date_to)}


def _reps(date_from, date_to):
    from reports.reps import rep_performance

    rows, _ = rep_performance(date_from, date_to)
    return {row["rep"].pk: row for row in rows if row["rep"] is not None}


def team_month(employees, today=None):
    """{employee pk: {"done", "sales", "commission"}} for the list's "this month" columns."""

    date_from, date_to = month_range(today)
    booked, sold = _appointments(date_from, date_to), _reps(date_from, date_to)
    result = {}
    for employee in employees:
        visit, rep = booked.get(employee.pk), sold.get(employee.pk)
        result[employee.pk] = {
            "done": visit["done"] if visit else 0,
            "sales": (visit["sales"] if visit else ZERO) + (rep["net_sales"] if rep else ZERO),
            "commission": (visit["commission"] if visit else ZERO) + (rep["commission"] if rep else ZERO),
        }
    return result


def employee_month(employee, today=None):
    """The blocks of one person's page: only the ones with something in them."""

    date_from, date_to = month_range(today)
    blocks = []
    visit = _appointments(date_from, date_to).get(employee.pk)
    if visit and (visit["done"] or visit["sales"]):
        blocks.append({"key": "appointments", "done": visit["done"], "sales": visit["sales"], "commission": visit["commission"]})
    rep = _reps(date_from, date_to).get(employee.pk)
    if rep and (rep["invoices"] or rep["customers"]):
        blocks.append({"key": "rep", "invoices": rep["invoices"], "sales": rep["net_sales"], "commission": rep["commission"],
                       "customers": rep["customers"], "collected": rep["collected"]})
    today = date_to
    groups = [group for group in employee.study_groups.filter(active=True).select_related("course").order_by("name")
              if not group.ends_on or group.ends_on >= today]
    if groups:
        blocks.append({"key": "groups", "groups": groups})
    start = timezone.make_aware(timezone.datetime.combine(date_from, timezone.datetime.min.time()))
    end = timezone.make_aware(timezone.datetime.combine(date_to + timedelta(days=1), timezone.datetime.min.time()))
    served = employee.restaurant_orders.filter(opened_at__gte=start, opened_at__lt=end, status="paid").count()
    delivered = employee.deliveries.filter(opened_at__gte=start, opened_at__lt=end, status="paid").count()
    if served or delivered:
        blocks.append({"key": "floor", "served": served, "delivered": delivered})
    upcoming = list(employee.appointments.filter(starts_at__gte=timezone.now(), status__in=("booked", "confirmed"))
                    .select_related("customer").order_by("starts_at")[:5])
    if upcoming:
        blocks.append({"key": "upcoming", "rows": upcoming})
    return {"from": date_from, "to": date_to, "blocks": blocks}
