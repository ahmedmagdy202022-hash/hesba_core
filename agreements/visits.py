"""SVC-004: book a service contract's visits for the period, spread over its days, in one click.

A contract says how many visits a period carries (``visits_per_period``). Here
the visits still missing in the current period are booked as ordinary
appointments (visits to the customer's address, at no price: the contract
bills the period) through the unchanged ``appointments.services.book``, so
the employee's overlap rule holds. A visit counts toward the period when it is
booked, confirmed, in progress or done; a cancelled or no-show one frees its
place, and booking again tops the period back up. Nothing here bills, posts or
moves money.
"""

from datetime import datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from . import services
from .models import ContractStatus, ServiceContract

FRIDAY = 4
FIRST_HOUR, LAST_HOUR = 10, 17

MESSAGES = {
    "ar": {"manage": "حجز الزيارات محتاج صلاحية إنشاء فاتورة بيع.", "no_visits": "العقد ده مفيهوش زيارات في الفترة.",
           "not_active": "العقد مش شغال؛ مينفعش تتحجز له زيارات.", "no_period": "العقد لسه مابدأش.",
           "address": "العميل {customer} مالوش عنوان؛ اكتب عنوانه الأول عشان تتحجز الزيارات.", "full": "زيارات الفترة دي محجوزة كلها.",
           "busy": "{employee} مشغول طول يوم {day}؛ اختار موظف تاني أو احجز الزيارة بنفسك.", "note": "زيارة ضمن عقد {number} ({n} من {total})"},
    "en": {"manage": "Booking visits needs the create-sales-invoice permission.", "no_visits": "This contract has no visits in a period.",
           "not_active": "The contract is not active; no visits can be booked for it.", "no_period": "The contract has not started yet.",
           "address": "The customer {customer} has no address; add it first to book the visits.", "full": "This period's visits are all booked.",
           "busy": "{employee} is busy all day on {day}; choose another employee or book the visit yourself.", "note": "Contract {number} visit ({n} of {total})"},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _bounds(start, end):
    first = timezone.make_aware(datetime.combine(start, time.min))
    last = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
    return first, last


def booked_in(contract, start):
    """The contract's live visits in the period starting ``start``: its customer's appointments for its service that hold time."""

    from appointments.models import HOLDS_TIME, Appointment

    first, last = _bounds(start, services.period_end(contract, start))
    return Appointment.objects.filter(customer=contract.customer, service=contract.service, status__in=HOLDS_TIME,
                                      starts_at__gte=first, starts_at__lt=last)


def missing(contract, today=None):
    """How many visits the current period still needs booked (0 for a contract without visits, not active, or not started)."""

    today = today or timezone.localdate()
    if contract.status != ContractStatus.ACTIVE or not contract.visits_per_period:
        return 0
    start = services.current_period(contract, today)
    if start is None or services.period_end(contract, start) < today:
        return 0
    return max(contract.visits_per_period - booked_in(contract, start).count(), 0)


def plan_days(begin, end, count):
    """``count`` days spread evenly over [begin, end], Fridays moved to the next day (or the one before, at the end)."""

    span = (end - begin).days + 1
    days = []
    for index in range(count):
        day = begin + timedelta(days=(index * span) // count)
        if day.weekday() == FRIDAY:
            day = day + timedelta(days=1) if day < end else day - timedelta(days=1)
            day = max(day, begin)
        days.append(day)
    return days


@transaction.atomic
def schedule(contract, user, *, employee=None, today=None, lang="ar"):
    """Book the current period's missing visits. Returns the appointments booked (an empty list when none were missing)."""

    from appointments.models import AppointmentKind
    from appointments.services import book

    words = _words(lang)
    if not user_has_permission(user, services.MANAGE):
        raise PermissionDenied(words["manage"])
    contract = ServiceContract.objects.select_for_update().select_related("customer", "service").get(pk=contract.pk)  # two clicks never double the visits
    if not contract.visits_per_period:
        raise ValidationError(words["no_visits"])
    if contract.status != ContractStatus.ACTIVE:
        raise ValidationError(words["not_active"])
    today = today or timezone.localdate()
    start = services.current_period(contract, today)
    if start is None:
        raise ValidationError(words["no_period"])
    end = services.period_end(contract, start)
    count = missing(contract, today)
    if not count:
        return []
    if not (contract.customer.address or "").strip():
        raise ValidationError(words["address"].format(customer=contract.customer.name))
    already = contract.visits_per_period - count
    booked = []
    for n, day in enumerate(plan_days(max(start, today), end, count), start=already + 1):
        booked.append(_book_on(day, contract, employee, words["note"].format(number=contract.number, n=n, total=contract.visits_per_period),
                               user, lang, words, book, AppointmentKind.VISIT))
    AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="agreements", action="schedule_contract_visits",
                            object_type="agreements.ServiceContract", object_id=str(contract.pk), before_data={},
                            after_data={"period_start": start.isoformat(), "appointments": [appointment.number for appointment in booked],
                                        "employee": getattr(employee, "pk", None)})
    return booked


def _book_on(day, contract, employee, note, user, lang, words, book, kind):
    """One visit on ``day``: at 10:00, or the next free hour up to 17:00 when the employee is taken."""

    for hour in range(FIRST_HOUR, LAST_HOUR + 1):
        starts_at = timezone.make_aware(datetime.combine(day, time(hour)))
        try:
            with transaction.atomic():
                return book({"customer": contract.customer, "employee": employee, "service": contract.service, "price": "0",
                             "starts_at": starts_at, "duration_minutes": 60, "kind": kind, "notes": note[:255]}, user, lang)
        except ValidationError:
            if employee is None:
                raise
    raise ValidationError(words["busy"].format(employee=employee.name, day=day.isoformat()))


def missing_count(today=None):
    """Visits still to book this period over every active contract of the entity being worked in (what the list's button books)."""

    today = today or timezone.localdate()
    return sum(missing(contract, today) for contract in _active())


def _active():
    return services.in_scope(ServiceContract.objects.filter(status=ContractStatus.ACTIVE, visits_per_period__gt=0).select_related("customer", "service"))


def schedule_all(user, *, today=None, lang="ar"):
    """Book the missing visits of every active contract. Returns (appointments booked, [(contract, why it was skipped)])."""

    words = _words(lang)
    if not user_has_permission(user, services.MANAGE):
        raise PermissionDenied(words["manage"])
    today = today or timezone.localdate()
    booked, skipped = [], []
    for contract in _active():
        if not missing(contract, today):
            continue
        try:
            booked += schedule(contract, user, today=today, lang=lang)
        except ValidationError as exc:
            skipped.append((contract, " ".join(exc.messages)))
    return booked, skipped
