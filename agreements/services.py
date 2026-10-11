"""SVC-002: service contracts and their period billing.

A contract bills ``amount`` once per period (month, quarter or year) from
``starts_on`` until ``ends_on`` (or for as long as it is active). "Bill what is
due" makes one ordinary draft sales invoice per contract and due period through
``taxes.services.create_sales_draft_with_tax``; posting it on the sales page is
what touches the customer's account and the cashbox. A period whose invoice was
cancelled is due again; a period is never on two live invoices (the contract
row is locked while it is billed). Visits are read from the customer's done
appointments for the contract's service in the period.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from .models import ContractBill, ContractStatus, Every, ServiceContract

ZERO = Decimal("0")
VIEW, MANAGE = "sales.view_sales_invoices", "sales.create_sales_invoice"
SUB_ACTIVITIES = ("general", "maintenance", "professional", "digital_marketing", "other")
STEP = {Every.MONTH: 1, Every.QUARTER: 3, Every.YEAR: 12}
AMOUNT_LIMIT = Decimal("10000000")

EVERY_LABELS = {
    "ar": {"month": "كل شهر", "quarter": "كل 3 شهور", "year": "كل سنة"},
    "en": {"month": "Every month", "quarter": "Every quarter", "year": "Every year"},
}
STATUS_LABELS = {
    "ar": {"active": "شغال", "paused": "متوقف مؤقتًا", "ended": "انتهى"},
    "en": {"active": "Active", "paused": "Paused", "ended": "Ended"},
}
MESSAGES = {
    "ar": {"customer": "اختار العميل.", "service": "اختار الخدمة.", "amount": "قيمة الفترة لازم تكون رقم أكبر من صفر.",
           "every": "اختار كل قد إيه بيتفوتر.", "date": "التاريخ مش مظبوط.", "ends": "تاريخ النهاية لازم يكون بعد البداية.",
           "visits": "عدد الزيارات من 0 لـ 60.", "status": "الحالة مش صحيحة.", "setup": "لازم يكون فيه مخزن بيع نشط.",
           "manage": "العقود محتاجة صلاحية إنشاء فاتورة بيع.", "nothing": "مفيش فترات مستحقة تتفوتر.",
           "locked": "العقد اتفوتر قبل كده: تاريخ البداية وكل قد إيه بيتفوتر مينفعش يتغيروا. أنهِ العقد واعمل عقد جديد.",
           "line": "{description} — من {start} لحد {end}", "note": "عقد {number}: فترة {start}"},
    "en": {"customer": "Choose the customer.", "service": "Choose the service.", "amount": "The amount per period must be a number above zero.",
           "every": "Choose how often it is billed.", "date": "The date is not valid.", "ends": "The end date must be after the start.",
           "visits": "Visits are from 0 to 60.", "status": "The status is not valid.", "setup": "An active selling location is needed.",
           "manage": "Contracts need the create-sales-invoice permission.", "nothing": "No periods are due to be billed.",
           "locked": "This contract has been billed: its start date and billing period cannot change. End it and make a new one.",
           "line": "{description} — {start} to {end}", "note": "Contract {number}: period {start}"},
}


def is_contract_business():
    from entities.current import effective_activity

    activity, sub = effective_activity()
    return (activity == "services" and (sub or "") in SUB_ACTIVITIES) or (activity, sub) == ("contracting", "maintenance_contracts")


def can_view(user):
    return is_contract_business() and user_has_permission(user, VIEW)


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def next_start(contract, start):
    """The period after the one starting ``start``, counted from the contract's own start so a 31st stays a 31st."""

    first = contract.starts_on
    index = ((start.year - first.year) * 12 + start.month - first.month) // STEP[contract.every]
    return add_months(first, STEP[contract.every] * (index + 1))


def period_end(contract, start):
    end = next_start(contract, start) - timedelta(days=1)
    return min(end, contract.ends_on) if contract.ends_on else end


def periods(contract, until):
    """Every period start from ``starts_on`` up to ``until`` (and before ``ends_on``)."""

    out, n = [], 0
    while True:
        start = add_months(contract.starts_on, STEP[contract.every] * n)
        if start > until or (contract.ends_on and (start > contract.ends_on or (n > 0 and start == contract.ends_on))):
            return out  # a period starting on the end day itself is not billed (unless it is the only one)
        out.append(start)
        n += 1
        if n > 1200:  # a hundred years of months: never in practice
            return out


def billed_periods(contract):
    """Period starts on a live (not cancelled) invoice."""

    return set(contract.bills.exclude(invoice__status="cancelled").values_list("period_start", flat=True))


def due_periods(contract, today=None):
    if contract.status != ContractStatus.ACTIVE:
        return []
    today = today or timezone.localdate()
    done = billed_periods(contract)
    return [start for start in periods(contract, today) if start not in done and (contract.billing_from is None or start >= contract.billing_from)]


def current_period(contract, today=None):
    today = today or timezone.localdate()
    starts = periods(contract, today)
    return starts[-1] if starts else None


def visits_in(contract, start):
    """Done appointments of the contract's customer for its service in the period."""

    from appointments.models import Appointment, AppointmentStatus

    end = period_end(contract, start)
    first = timezone.make_aware(timezone.datetime.combine(start, timezone.datetime.min.time()))
    last = timezone.make_aware(timezone.datetime.combine(end + timedelta(days=1), timezone.datetime.min.time()))
    return Appointment.objects.filter(customer=contract.customer, service=contract.service, status=AppointmentStatus.DONE,
                                      starts_at__gte=first, starts_at__lt=last).count()


def _date(raw, words, allow_blank=False):
    if raw in (None, ""):
        if allow_blank:
            return None
        raise ValidationError(words["date"])
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise ValidationError(words["date"])


def _audit(user, action, contract, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="agreements", action=action, object_type="agreements.ServiceContract",
                            object_id=str(contract.pk), before_data=before or {}, after_data=after)


def _snapshot(contract):
    return {"customer": contract.customer_id, "service": contract.service_id, "amount": str(contract.amount), "every": contract.every,
            "starts_on": contract.starts_on.isoformat(), "ends_on": contract.ends_on.isoformat() if contract.ends_on else "",
            "visits": contract.visits_per_period, "status": contract.status,
            "billing_from": contract.billing_from.isoformat() if contract.billing_from else ""}


@transaction.atomic
def save_contract(data, user, contract=None, lang="ar"):
    from config.numbering import next_in_series

    words = _words(lang)
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied(words["manage"])
    customer, service = data.get("customer"), data.get("service")
    if customer is None:
        raise ValidationError(words["customer"])
    if service is None:
        raise ValidationError(words["service"])
    try:
        amount = Decimal(str(data.get("amount") or "").strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError(words["amount"])
    if not amount.is_finite() or money_round(amount) <= 0 or amount >= AMOUNT_LIMIT:  # 0.004 is not an amount
        raise ValidationError(words["amount"])
    every = data.get("every") or Every.MONTH
    if every not in Every.values:
        raise ValidationError(words["every"])
    starts_on = _date(data.get("starts_on") or timezone.localdate(), words)
    ends_on = _date(data.get("ends_on"), words, allow_blank=True)
    if ends_on and ends_on < starts_on:
        raise ValidationError(words["ends"])
    try:
        visits = int(str(data.get("visits_per_period") or 0).strip())
    except ValueError:
        raise ValidationError(words["visits"])
    if not 0 <= visits <= 60:
        raise ValidationError(words["visits"])
    status = data.get("status") or ContractStatus.ACTIVE
    if status not in ContractStatus.values:
        raise ValidationError(words["status"])
    billing_from = _date(data.get("billing_from"), words, allow_blank=True)
    creating = contract is None
    before = {} if creating else _snapshot(contract)
    if contract is None:
        contract = ServiceContract(created_by=user, location=selling_location())
        was_active = True
    else:
        contract = ServiceContract.objects.select_for_update().get(pk=contract.pk)
        if (contract.starts_on != starts_on or contract.every != every) and billed_periods(contract):
            raise ValidationError(words["locked"])  # billed periods would no longer line up
        was_active = contract.status == ContractStatus.ACTIVE
    contract.customer, contract.service, contract.amount, contract.every = customer, service, money_round(amount), every
    contract.starts_on, contract.ends_on, contract.visits_per_period, contract.status = starts_on, ends_on, visits, status
    contract.description = (data.get("description") or "").strip()[:255]
    contract.notes = (data.get("notes") or "").strip()[:255]
    if billing_from is not None:
        contract.billing_from = billing_from
    elif not was_active and status == ContractStatus.ACTIVE:  # resumed: the months it was on hold are never due
        current = current_period(contract)
        contract.billing_from = max(filter(None, (contract.billing_from, current))) if current else contract.billing_from
    if creating:
        for attempt in range(5):  # two contracts saved at the same moment took the same number: take the next
            contract.number = next_in_series(ServiceContract, "number", "SC-", 4)
            try:
                with transaction.atomic():
                    contract.save()
                break
            except IntegrityError:
                if attempt == 4 or not ServiceContract.objects.filter(number=contract.number).exists():
                    raise
    else:
        contract.save()
    _audit(user, "save_service_contract", contract, {"number": contract.number, **_snapshot(contract)}, before,
           AuditEventType.CREATE if creating else AuditEventType.UPDATE)
    return contract


def selling_location():
    from entities import scope as entity_scope
    from master_data.models import Location

    return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


def in_scope(queryset):
    """The contracts of the entity being worked in (all of them in the whole-group view)."""

    from entities import scope as entity_scope

    return entity_scope.scope(queryset, "location__entity")


def draft_invoice(customer, rows, notes, user, location):
    """An ordinary draft sales invoice (nothing paid, no cashbox) through the tax-aware sales engine; a number taken by
    someone else at the same moment is retried. Shared by contracts, packages and hours."""

    from config.numbering import next_in_series
    from sales.models import SalesInvoice
    from taxes.services import create_sales_draft_with_tax

    header = {"invoice_date": timezone.localdate(), "customer": customer, "selling_location": location, "cashbox": None,
              "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO, "notes": notes[:255]}
    for attempt in range(5):
        number = next_in_series(SalesInvoice, "invoice_number", "SI-")
        try:
            with transaction.atomic():
                return create_sales_draft_with_tax({**header, "invoice_number": number}, rows, user)
        except IntegrityError:
            if attempt == 4 or not SalesInvoice.objects.filter(invoice_number=number).exists():
                raise


def _bill_one(contract, start, user, location, words):
    end = period_end(contract, start)
    description = contract.description or contract.service.item_name
    rows = [{"item": contract.service, "quantity": Decimal("1"), "unit_sale_price": contract.amount, "line_discount_amount": ZERO,
             "description": words["line"].format(description=description, start=start.isoformat(), end=end.isoformat())[:255]}]
    invoice = draft_invoice(contract.customer, rows, words["note"].format(number=contract.number, start=start.isoformat()), user, location)
    return ContractBill.objects.create(contract=contract, period_start=start, invoice=invoice, created_by=user)


@transaction.atomic
def bill_due(user, today=None, contracts=None, lang="ar"):
    """Draft invoices for every due period of the active contracts (or the given ones). Returns the new bills."""

    words = _words(lang)
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied(words["manage"])
    today = today or timezone.localdate()
    fallback = selling_location()
    queryset = in_scope(ServiceContract.objects.filter(status=ContractStatus.ACTIVE))
    if contracts is not None:
        queryset = queryset.filter(pk__in=[contract.pk for contract in contracts])
    made = []
    for contract in queryset.select_for_update(of=("self",)).select_related("customer", "service", "location").order_by("pk"):  # two clicks never bill a period twice
        location = contract.location if contract.location and contract.location.active else fallback  # billed where it was made
        if location is None:
            raise ValidationError(words["setup"])
        for start in due_periods(contract, today):
            bill = _bill_one(contract, start, user, location, words)
            made.append(bill)
            _audit(user, "bill_service_contract", contract, {"period": start.isoformat(), "invoice": bill.invoice.invoice_number,
                                                             "amount": str(bill.invoice.total_amount)})
    if not made:
        raise ValidationError(words["nothing"])
    return made


def due_count(today=None):
    today = today or timezone.localdate()
    return sum(len(due_periods(contract, today)) for contract in in_scope(ServiceContract.objects.filter(status=ContractStatus.ACTIVE)))


def overview(contract, today=None):
    """What the contract page and list show: next period due, the current period's visits, live bills."""

    today = today or timezone.localdate()
    due = due_periods(contract, today)
    current = current_period(contract, today)
    nxt = None
    if contract.status == ContractStatus.ACTIVE:
        candidate = next_start(contract, current) if current else contract.starts_on
        if not contract.ends_on or candidate <= contract.ends_on:
            nxt = candidate
    return {"due": due, "current": current, "current_end": period_end(contract, current) if current else None,
            "visits": visits_in(contract, current) if current else 0, "next": nxt}


def unpaid(contract):
    """What the customer owes now, from their own ledger (payments and returns included), when it is above zero."""

    from parties.services import balance

    return max(balance("customer", contract.customer), ZERO)
