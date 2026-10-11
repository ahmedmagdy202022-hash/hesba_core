"""SVC-003: hours worked for customers, billed when the office is ready.

An accountant, a consultant or a designer logs the hours they worked for a
customer, against a service and at its rate. "Bill the hours" turns a
customer's unbilled hours into one ordinary draft sales invoice (one line an
entry, quantity = hours) through the tax-aware sales engine; posting it is what
charges the customer. An entry stays unbilled until it is on a live invoice: a
cancelled invoice frees its hours again. A billed entry can no longer be
removed.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from .models import TimeEntry
from .services import MANAGE, VIEW, ZERO, _date, draft_invoice, selling_location

SUB_ACTIVITIES = ("professional", "digital_marketing", "general", "other")
MAX_HOURS = Decimal("24")
RATE_LIMIT = Decimal("1000000")

MESSAGES = {
    "ar": {"customer": "اختار العميل.", "service": "اختار الخدمة.", "hours": "الساعات من 0.25 لـ 24.", "rate": "سعر الساعة لازم يكون رقم صفر أو أكبر.",
           "date": "التاريخ مش مظبوط.", "future": "مينفعش تسجّل ساعات في يوم لسه مجاش.", "setup": "لازم يكون فيه مخزن بيع نشط.",
           "log": "تسجيل الساعات للي عنده صلاحية إنشاء فاتورة بيع، أو لموظف مربوط بحسابه.", "manage": "فوترة الساعات محتاجة صلاحية إنشاء فاتورة بيع.",
           "nothing": "مفيش ساعات لسه ماتفوترتش للعميل ده.", "billed": "الساعات دي اتفوترت؛ مينفعش تتمسح. الغِ الفاتورة الأول لو فيها غلط.",
           "not_yours": "تقدر تمسح ساعاتك أنت بس.", "line": "{date} — {who}{description}", "note": "ساعات عمل: {hours} ساعة"},
    "en": {"customer": "Choose the customer.", "service": "Choose the service.", "hours": "Hours are from 0.25 to 24.", "rate": "The hourly rate must be zero or more.",
           "date": "The date is not valid.", "future": "Hours cannot be logged on a day still to come.", "setup": "An active selling location is needed.",
           "log": "Logging hours needs the create-sales-invoice permission, or an employee linked to the account.",
           "manage": "Billing hours needs the create-sales-invoice permission.", "nothing": "This customer has no unbilled hours.",
           "billed": "These hours are billed and cannot be removed. Cancel the invoice first if it is wrong.",
           "not_yours": "You can remove your own hours only.", "line": "{date} — {who}{description}", "note": "Hours worked: {hours} h"},
}


def is_hours_business():
    from entities.current import effective_activity

    activity, sub = effective_activity()
    return activity == "services" and (sub or "") in SUB_ACTIVITIES


def _employee_of(user):
    employee = getattr(user, "employee", None) if getattr(user, "is_authenticated", False) else None
    return employee if employee is not None and employee.active else None


def can_log(user):
    return user_has_permission(user, MANAGE) or _employee_of(user) is not None


def can_view(user):
    return is_hours_business() and (user_has_permission(user, VIEW) or _employee_of(user) is not None)


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def unbilled():
    """Entries on no live invoice."""

    return TimeEntry.objects.filter(Q(invoice__isnull=True) | Q(invoice__status="cancelled"))


def _audit(user, action, entry, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="agreements", action=action, object_type="agreements.TimeEntry",
                            object_id=str(entry.pk), before_data=before or {}, after_data=after)


def _decimal(raw, message):
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError(message)
    if not value.is_finite():
        raise ValidationError(message)
    return value


@transaction.atomic
def log_time(data, user, lang="ar"):
    """One entry. Someone without the invoice permission logs as themselves (their linked employee)."""

    words = _words(lang)
    own = _employee_of(user)
    manager = user_has_permission(user, MANAGE)
    if not manager and own is None:
        raise PermissionDenied(words["log"])
    customer, service = data.get("customer"), data.get("service")
    if customer is None:
        raise ValidationError(words["customer"])
    if service is None:
        raise ValidationError(words["service"])
    hours = _decimal(data.get("hours") or "", words["hours"])
    if hours < Decimal("0.25") or hours > MAX_HOURS:
        raise ValidationError(words["hours"])
    raw_rate = data.get("rate")
    rate = _decimal(raw_rate, words["rate"]) if raw_rate not in (None, "") else Decimal(service.default_sale_price or 0)
    if rate < 0 or rate >= RATE_LIMIT:
        raise ValidationError(words["rate"])
    day = _date(data.get("worked_on") or timezone.localdate(), words)
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    employee = data.get("employee") if manager else own
    entry = TimeEntry.objects.create(customer=customer, service=service, employee=employee or (own if manager else None), worked_on=day,
                                     hours=hours.quantize(Decimal("0.01")), rate=money_round(rate),
                                     description=(data.get("description") or "").strip()[:200], created_by=user)
    _audit(user, "log_time_entry", entry, {"customer": customer.pk, "service": service.pk, "employee": entry.employee_id, "worked_on": day.isoformat(),
                                           "hours": str(entry.hours), "rate": str(entry.rate)}, event=AuditEventType.CREATE)
    return entry


@transaction.atomic
def remove_entry(entry, user, lang="ar"):
    words = _words(lang)
    entry = TimeEntry.objects.select_for_update(of=("self",)).select_related("invoice").get(pk=entry.pk)  # Postgres: never lock the nullable invoice side
    if not user_has_permission(user, MANAGE):
        own = _employee_of(user)
        if own is None or entry.employee_id != own.pk:
            raise PermissionDenied(words["not_yours"])
    if entry.invoice_id and entry.invoice.status != "cancelled":
        raise ValidationError(words["billed"])
    before = {"customer": entry.customer_id, "worked_on": entry.worked_on.isoformat(), "hours": str(entry.hours), "rate": str(entry.rate)}
    _audit(user, "remove_time_entry", entry, {}, before, AuditEventType.DELETE)
    entry.delete()


@transaction.atomic
def bill_hours(customer, user, lang="ar"):
    """One draft invoice for all the customer's unbilled hours. Returns the invoice."""

    words = _words(lang)
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied(words["manage"])
    location = selling_location()
    if location is None:
        raise ValidationError(words["setup"])
    entries = list(unbilled().filter(customer=customer).select_for_update(of=("self",)).select_related("service", "employee")
                   .order_by("worked_on", "pk"))  # two clicks never put an hour on two invoices
    if not entries:
        raise ValidationError(words["nothing"])
    rows = []
    for entry in entries:
        who = f"{entry.employee.name} — " if entry.employee_id else ""
        description = entry.description or entry.service.item_name
        rows.append({"item": entry.service, "quantity": entry.hours, "unit_sale_price": entry.rate, "line_discount_amount": ZERO,
                     "description": words["line"].format(date=entry.worked_on.isoformat(), who=who, description=description)[:255]})
    total_hours = sum((entry.hours for entry in entries), ZERO)
    invoice = draft_invoice(customer, rows, words["note"].format(hours=total_hours), user, location)
    TimeEntry.objects.filter(pk__in=[entry.pk for entry in entries]).update(invoice=invoice)
    for entry in entries:
        _audit(user, "bill_time_entry", entry, {"invoice": invoice.invoice_number})
    return invoice


def by_customer():
    """Unbilled hours per customer: [{customer, hours, amount, entries}], the most owed first."""

    groups = {}
    for entry in unbilled().select_related("customer", "service", "employee").order_by("worked_on", "pk"):
        row = groups.setdefault(entry.customer_id, {"customer": entry.customer, "hours": ZERO, "amount": ZERO, "entries": []})
        entry.amount = money_round(entry.hours * entry.rate)
        row["hours"] += entry.hours
        row["amount"] += entry.amount
        row["entries"].append(entry)
    return sorted(groups.values(), key=lambda row: (-row["amount"], row["customer"].name))


def by_employee(first, last):
    """Hours each employee logged in the period, and how much of it is billed: [{employee, hours, billed, amount}]."""

    rows = {}
    for entry in TimeEntry.objects.filter(worked_on__gte=first, worked_on__lte=last).select_related("employee", "invoice"):
        row = rows.setdefault(entry.employee_id, {"employee": entry.employee, "hours": ZERO, "billed": ZERO, "amount": ZERO})
        row["hours"] += entry.hours
        row["amount"] += money_round(entry.hours * entry.rate)
        if entry.invoice_id and entry.invoice.status != "cancelled":
            row["billed"] += entry.hours
    for row in rows.values():
        row["billed_pct"] = int(row["billed"] / row["hours"] * 100) if row["hours"] else 0
    return sorted(rows.values(), key=lambda row: -row["hours"])
