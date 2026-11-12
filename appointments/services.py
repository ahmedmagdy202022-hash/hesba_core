"""APPT-001: book, move, move along and bill appointments and visits.

Rules, stated once:

* an employee cannot hold two overlapping appointments (cancelled and
  no-show ones free the time); bookings for the same employee are
  serialised by locking the employee row, so two receptionists cannot both
  take the last slot;
* statuses move forward only: booked -> confirmed -> in progress -> done,
  and any open one can be cancelled or marked no-show;
* billing makes a *draft* sales invoice through ``create_sales_draft_with_tax``
  with the service at the appointment's price. Nothing is posted here: the
  invoice is posted, collected and printed from the sales screen like any
  other, so stock, cashbox and customer ledger stay the sales engine's own;
* HG-074: an appointment belongs to the entity it was booked in (NULL: the
  main entity, HG-034). The agenda, the performance report and every reader
  narrow to the entity being worked in, and the bill is made on a selling
  store of the *appointment's* entity, whatever entity the biller is working
  in, so it lands in the right books. The employee's diary stays one across
  the group: the overlap check and the day's numbering are group-wide.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from .models import HOLDS_TIME, Appointment, AppointmentKind, AppointmentStatus
from entities import scope as entity_scope


MIN_MINUTES, MAX_MINUTES = 5, 12 * 60
NEXT = {
    AppointmentStatus.BOOKED: (AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED, AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW),
    AppointmentStatus.CONFIRMED: (AppointmentStatus.ARRIVED, AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW),
    AppointmentStatus.ARRIVED: (AppointmentStatus.DONE, AppointmentStatus.CANCELLED),
    AppointmentStatus.DONE: (),
    AppointmentStatus.CANCELLED: (),
    AppointmentStatus.NO_SHOW: (),
}
MESSAGES = {
    "ar": {
        "customer": "اختار العميل.", "when": "اكتب ميعاد صحيح.", "minutes": "المدة لازم من {a} لـ {b} دقيقة.", "price": "السعر مش صحيح.",
        "address": "الزيارة محتاجة عنوان.", "overlap": "{employee} عنده ميعاد تاني من {start} لـ {end}.", "status": "مينفعش تنقل الميعاد من «{old}» لـ «{new}».",
        "closed": "الميعاد ده خلص أو اتلغى؛ مينفعش يتعدّل.", "no_service": "اختار الخدمة الأول عشان تعمل الفاتورة.", "setup": "لازم يكون فيه مخزن بيع نشط.",
        "billed": "الميعاد ده عليه فاتورة بالفعل.", "not_done": "الميعاد لازم يكون خلص أو جاري عشان تعمل فاتورته.",
        "setup_entity": "مفيش مخزن بيع نشط في كيان الميعاد ({entity})؛ ضيف مخزن بيع للكيان ده الأول.",
        "forbidden": "تعديل المواعيد لصاحب الصلاحية بس.", "bad_start": "اكتب ميعاد جديد صحيح.", "bad_staff": "الموظف ده مش موجود أو مش شغال.",
    },
    "en": {
        "customer": "Choose the customer.", "when": "Enter a valid date and time.", "minutes": "The duration must be {a} to {b} minutes.", "price": "Invalid price.",
        "address": "A visit needs an address.", "overlap": "{employee} already has an appointment from {start} to {end}.", "status": "The appointment cannot move from “{old}” to “{new}”.",
        "closed": "This appointment is done or cancelled; it cannot be changed.", "no_service": "Choose the service first to make the invoice.", "setup": "An active selling location is needed.",
        "billed": "This appointment already has an invoice.", "not_done": "The appointment must be in progress or done to be billed.",
        "setup_entity": "The appointment's entity ({entity}) has no active selling location; add one to that entity first.",
        "forbidden": "Only users with the permission can change appointments.", "bad_start": "Enter a valid new date and time.", "bad_staff": "That employee does not exist or is not active.",
    },
}


def scoped(queryset=None):
    """The appointments of the entity being worked in (all of them in the whole-group view). HG-074."""

    return entity_scope.scope((queryset if queryset is not None else Appointment.objects).all(), "entity")


def selling_location_of(appointment):
    """The selling store of the *appointment's* entity, not the biller's (HG-074).

    An appointment with no entity belongs to the main entity (HG-034), so it
    takes a main-entity store (or a pre-entity store with none). Read exactly
    as ``entities.scope`` reads, by working in that entity for the lookup.
    """

    from entities.current import working_in
    from entities.services import main_entity
    from master_data.models import Location

    # main_entity() creates the MAIN row on an install that has none yet; that is the HG-034 default, not new data.
    with working_in(appointment.entity or main_entity()):
        return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


def _audit(appointment, user, action, after, before=None):
    AuditLog.objects.create(event_type=AuditEventType.CREATE if action == "book_appointment" else AuditEventType.UPDATE, actor=user,
                            module="appointments", action=action, object_type="appointments.Appointment", object_id=str(appointment.pk),
                            before_data=before or {}, after_data=after)


def _next_number(day):
    prefix = f"AP-{day:%Y%m%d}-"
    number = Appointment.objects.filter(number__startswith=prefix).count() + 1
    while Appointment.objects.filter(number=f"{prefix}{number:03d}").exists():
        number += 1
    return f"{prefix}{number:03d}"


def _price(value, words):
    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", ""))
    except InvalidOperation:
        raise ValidationError(words["price"])
    if not number.is_finite() or number < 0:
        raise ValidationError(words["price"])
    return money_round(number)


def _check_overlap(employee, starts_at, minutes, words, exclude=None):
    """Lock the employee, then refuse a slot that overlaps one they already hold."""

    if employee is None:
        return
    from staff.models import Employee

    Employee.objects.select_for_update().filter(pk=employee.pk).first()
    ends_at = starts_at + timedelta(minutes=minutes)
    candidates = Appointment.objects.filter(employee=employee, status__in=HOLDS_TIME, starts_at__lt=ends_at, starts_at__gte=starts_at - timedelta(minutes=MAX_MINUTES))
    if exclude is not None:
        candidates = candidates.exclude(pk=exclude.pk)
    for other in candidates:
        if other.ends_at > starts_at:
            local = timezone.localtime
            raise ValidationError(words["overlap"].format(employee=employee.name, start=f"{local(other.starts_at):%H:%M}", end=f"{local(other.ends_at):%H:%M}"), code="overlap")


def _clean(data, words):
    customer = data.get("customer")
    if customer is None:
        raise ValidationError(words["customer"])
    starts_at = data.get("starts_at")
    if starts_at is None:
        raise ValidationError(words["when"])
    try:
        minutes = int(data.get("duration_minutes") or 30)
    except (TypeError, ValueError):
        minutes = -1
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValidationError(words["minutes"].format(a=MIN_MINUTES, b=MAX_MINUTES))
    kind = data.get("kind") if data.get("kind") in AppointmentKind.values else AppointmentKind.APPOINTMENT
    address = (data.get("address") or "").strip()[:255]
    if kind == AppointmentKind.VISIT and not address:
        address = (getattr(customer, "address", "") or "").strip()[:255]
        if not address:
            raise ValidationError(words["address"])
    service = data.get("service")
    price = data.get("price")
    price = _price(price if price not in (None, "") else (service.default_sale_price if service else 0), words)
    return {"customer": customer, "employee": data.get("employee"), "service": service, "price": price, "starts_at": starts_at,
            "duration_minutes": minutes, "kind": kind, "address": address, "notes": (data.get("notes") or "").strip()[:255]}


def _snapshot(values):
    return {key: (value.isoformat() if hasattr(value, "isoformat") else str(getattr(value, "pk", value) if value is not None else "")) for key, value in values.items()}


@transaction.atomic
def book(data, user, lang="ar"):
    words = MESSAGES[lang]
    values = _clean(data, words)
    _check_overlap(values["employee"], values["starts_at"], values["duration_minutes"], words)
    appointment = Appointment.objects.create(number=_next_number(timezone.localtime(values["starts_at"]).date()), created_by=user,
                                             entity=entity_scope.entity() or None, **values)  # HG-074: booked in this entity
    _audit(appointment, user, "book_appointment", _snapshot(values))
    return appointment


@transaction.atomic
def reschedule(appointment, data, user, lang="ar"):
    words = MESSAGES[lang]
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if appointment.status in (AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW) or appointment.invoice_id:
        raise ValidationError(words["closed"])
    values = _clean(data, words)
    _check_overlap(values["employee"], values["starts_at"], values["duration_minutes"], words, exclude=appointment)
    before = _snapshot({key: getattr(appointment, key) for key in values})
    for key, value in values.items():
        setattr(appointment, key, value)
    appointment.save()
    _audit(appointment, user, "change_appointment", _snapshot(values), before)
    from . import online

    online.drop_if_not_online(appointment, user)  # M10: a booking no longer online keeps no call link
    return appointment


@transaction.atomic
def move(appointment, user, new_start, new_staff=None, lang="ar"):
    """S10: drag-to-reschedule. Moves the start (and optionally the employee), keeping the duration.

    Same closed rule as ``reschedule`` and the same overlap check under the same
    employee-row lock. Like booking, it does not refuse past times. Only
    ``starts_at`` and ``employee`` change; no money, stock or ledger is touched.
    A clash raises ``ValidationError(code="overlap")``.
    """

    words = MESSAGES[lang]
    if new_start is None:
        raise ValidationError(words["bad_start"], code="bad_start")
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if appointment.status in (AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW) or appointment.invoice_id:
        raise ValidationError(words["closed"], code="closed")
    employee = new_staff if new_staff is not None else appointment.employee
    _check_overlap(employee, new_start, appointment.duration_minutes, words, exclude=appointment)
    before = _snapshot({"starts_at": appointment.starts_at, "employee": appointment.employee})
    appointment.starts_at = new_start
    appointment.employee = employee
    appointment.save(update_fields=["starts_at", "employee"])
    _audit(appointment, user, "move_appointment", _snapshot({"starts_at": appointment.starts_at, "employee": appointment.employee}), before)
    return appointment


@transaction.atomic
def set_status(appointment, status, user, lang="ar"):
    words = MESSAGES[lang]
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if status not in NEXT.get(appointment.status, ()):
        raise ValidationError(words["status"].format(old=appointment.get_status_display(), new=AppointmentStatus(status).label if status in AppointmentStatus.values else status))
    before = appointment.status
    appointment.status = status
    appointment.save(update_fields=["status"])
    _audit(appointment, user, "appointment_status", {"status": status}, {"status": before})
    return appointment


def _lock_booking(appointment):
    return Appointment.objects.select_for_update().get(pk=appointment.pk)


@transaction.atomic
def cancel_with_deposit_policy(appointment, status, user, lang="ar"):
    """S5 (HG-069): cancel (or mark a no-show) and run the cancellation policy on the booking's deposit, in one transaction.

    The booking row is locked *first*, then its held deposit is read: ``deposits.services.take`` locks the same row, so
    a deposit taken at the same moment is either committed before this lock (and read here) or waits for it and then
    finds the booking cancelled and refuses. The status is set before the policy runs (an invalid move touches no
    money, and a no-show forfeits whatever the hours); late → forfeit into a fee invoice, early → refund through HG-068.

    Returns {"appointment", "deposit", "outcome", "invoice", "detail"}; ``outcome`` is None (no held deposit),
    "forfeited", "refunded", "refund_pending" (waits for a second person), or one of the refusals that leave the
    deposit held as the customer's credit: "forfeit_not_allowed", "forfeit_refused" (``detail`` says why),
    "refund_not_allowed", "refund_blocked" (closed month, ``detail``).
    """

    from approvals.gate import hold
    from deposits import services as deposits
    from permissions.services import user_has_permission

    appointment = _lock_booking(appointment)
    deposit = deposits.held_for(appointment) if status in (AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW) and deposits.enabled() else None
    appointment = set_status(appointment, status, user, lang)
    result = {"appointment": appointment, "deposit": deposit, "outcome": None, "invoice": None, "detail": ""}
    if deposit is None:
        return result
    reason = (f"Booking {appointment.number} cancelled" if lang == "en" else f"إلغاء الحجز {appointment.number}")
    if deposits.policy(appointment) == "forfeit":
        if not user_has_permission(user, deposits.SELL):
            result["outcome"] = "forfeit_not_allowed"
            return result
        try:
            result["invoice"] = deposits.forfeit(deposit, user, lang, reason)  # its own savepoint: a refusal keeps the cancel
        except ValidationError as exc:
            result.update(outcome="forfeit_refused", detail=" ".join(exc.messages))
        else:
            result["outcome"] = "forfeited"
        return result
    if not user_has_permission(user, deposits.RECEIVE):
        result["outcome"] = "refund_not_allowed"
        return result
    blocker = deposits.refund_blocker(deposit, lang)
    if blocker:
        result.update(outcome="refund_blocked", detail=blocker)
        return result
    if hold(user, "sales.cancel_payment", deposit.payment_id, {"reason": reason}, deposit.payment.payment_number) is not None:  # HG-068
        result["outcome"] = "refund_pending"
        return result
    deposits.refund(deposit, user, reason, lang)
    result["outcome"] = "refunded"
    return result


@transaction.atomic
def bill(appointment, user, lang="ar"):
    """The draft sales invoice for a done (or in-progress) appointment; made once."""

    from taxes.services import create_sales_draft_with_tax

    words = MESSAGES[lang]
    appointment = Appointment.objects.select_for_update(of=("self",)).select_related("service", "customer", "entity").get(pk=appointment.pk)
    if appointment.invoice_id:
        return appointment.invoice
    if appointment.status not in (AppointmentStatus.ARRIVED, AppointmentStatus.DONE):
        raise ValidationError(words["not_done"])
    if appointment.service is None:
        raise ValidationError(words["no_service"])
    location = selling_location_of(appointment)  # HG-074: the appointment's entity's store, so the bill lands in its books
    if location is None:
        if appointment.entity is not None:
            raise ValidationError(words["setup_entity"].format(entity=appointment.entity.display(lang)))
        raise ValidationError(words["setup"])
    invoice = create_sales_draft_with_tax(
        {"invoice_number": f"INV-{appointment.number}", "invoice_date": timezone.localdate(), "customer": appointment.customer,
         "selling_location": location, "cashbox": None, "discount_amount": Decimal("0"), "tax_amount": Decimal("0"),
         "paid_now": Decimal("0"), "notes": (f"Appointment {appointment.number}" if lang == "en" else f"ميعاد {appointment.number}")},  # TRACK-ACTS W18
        [{"item": appointment.service, "quantity": Decimal("1"), "unit_sale_price": appointment.price, "line_discount_amount": Decimal("0"),
          "description": appointment.notes or ""}],
        user,
    )
    appointment.invoice = invoice
    fields = ["invoice"]
    if appointment.status != AppointmentStatus.DONE:
        appointment.status = AppointmentStatus.DONE
        fields.append("status")
    appointment.save(update_fields=fields)
    from deposits.services import attach

    attach(appointment, invoice)  # S5 (HG-069): a held deposit settles this invoice when it posts
    _audit(appointment, user, "bill_appointment", {"invoice": invoice.invoice_number, "amount": str(invoice.total_amount)})
    return invoice


def day_agenda(day, employee=None):
    start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
    rows = scoped().filter(starts_at__gte=start, starts_at__lt=start + timedelta(days=1)).select_related("customer", "employee", "service", "invoice")
    if employee is not None:
        rows = rows.filter(employee=employee)
    return rows


def week_window(first_day):
    start = timezone.make_aware(timezone.datetime.combine(first_day, timezone.datetime.min.time()))
    return start, start + timedelta(days=7)


def week_agenda(first_day):
    """The 7 days from ``first_day`` as the future drag calendar needs them: per employee, no money fields."""

    start, end = week_window(first_day)
    rows = scoped().filter(starts_at__gte=start, starts_at__lt=end).select_related("customer", "employee", "service")  # HG-074
    return rows


def net_sales(invoice):
    """What the work earned on a posted invoice: before tax, after its posted returns.

    Returns carry no separate tax figure, so their tax share is taken at the
    invoice's own rate. Only posted invoices count; a draft or cancelled one earns nothing.
    """

    total = Decimal(invoice.total_amount)
    if total <= 0:
        return Decimal("0.00")
    before_tax = total - Decimal(invoice.tax_amount or 0)
    returned = invoice.returns.filter(status="posted").aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    return money_round(max(before_tax - returned * before_tax / total, Decimal("0")))


def performance(date_from, date_to):
    """Per employee: done appointments, sales of their posted invoices and the commission on it."""

    from django.db.models import Q

    from medical.insurance import bills_for, invoices_of
    from staff.models import Employee

    start = timezone.make_aware(timezone.datetime.combine(date_from, timezone.datetime.min.time()))
    end = timezone.make_aware(timezone.datetime.combine(date_to + timedelta(days=1), timezone.datetime.min.time()))
    done = scoped().filter(status=AppointmentStatus.DONE, starts_at__gte=start, starts_at__lt=end)
    rows = []
    for employee in Employee.objects.all():
        mine = done.filter(employee=employee)
        count = mine.count()
        billed = list(mine.filter(Q(invoice__status="posted") | Q(insured_bill__insurer_invoice__status="posted")).select_related("invoice"))
        bills = bills_for(billed)  # MED-008: an insured visit earned on the patient's invoice and the insurer's
        sales = sum((net_sales(invoice) for appointment in billed for invoice in invoices_of(appointment, bills.get(appointment.pk))
                     if invoice.status == "posted"), Decimal("0"))
        if not count and not employee.active:
            continue
        rows.append({"employee": employee, "done": count, "sales": money_round(sales),
                     "commission": money_round(sales * employee.commission_percent / Decimal("100"))})
    return rows


# ---- M3: the patient's own link: confirm, or ask for a cancellation (a request for reception, never a cancellation) ----

CANCEL_REQUESTED = "appointment_cancel_requested"
CANCEL_REQUEST_HANDLED = "appointment_cancel_request_handled"
#: What reception does to an appointment that answers a patient's request: it moves, changes, changes status or is marked as seen.
REQUEST_ANSWERED_BY = ("move_appointment", "change_appointment", "appointment_status", CANCEL_REQUEST_HANDLED)


def patient_token(appointment):
    """The signed token of the patient's link: the appointment, bound to the patient it is booked for now."""

    from printing.share import make_token

    return make_token("appointment", appointment.pk, owner=appointment.customer_id)


def link_alive(appointment, owner, now=None):
    """The patient's link works while the appointment is still ahead and open (booked or confirmed) and still belongs
    to the patient the link was made for (``owner``, from the token). Anything else (cancelled, no-show, arrived, done,
    already past, gone, or moved to another patient since the link went out) is a dead link."""

    return (appointment is not None and owner is not None and appointment.customer_id == owner
            and appointment.status in (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED)
            and appointment.starts_at > (now or timezone.now()))


def pending_cancel_requests(ids):
    """The ids (of ``ids``) with a patient's cancellation request that reception has not answered yet.

    The request is an append-only audit row (no column: nothing here may cancel, and a cancellation can forfeit a held
    deposit). It stays pending until a later row says reception moved, changed or cancelled the appointment, or marked
    the request as seen."""

    ids = [str(pk) for pk in ids]
    if not ids:
        return set()
    pending = {}
    rows = (AuditLog.objects.filter(module="appointments", object_type="appointments.Appointment", object_id__in=ids,
                                    action__in=(CANCEL_REQUESTED, *REQUEST_ANSWERED_BY)).order_by("pk").values_list("object_id", "action"))
    for object_id, action in rows:
        pending[object_id] = action == CANCEL_REQUESTED
    flagged = {int(object_id) for object_id, flag in pending.items() if flag}
    if not flagged:
        return set()
    # Only an appointment still open shows the flag: a status changed without an audit row (or a past day's request) leaves none behind.
    return set(Appointment.objects.filter(pk__in=flagged, status__in=(AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED)).values_list("pk", flat=True))


@transaction.atomic
def patient_confirm(pk, owner):
    """The patient taps «confirm». Returns (appointment, changed), or None for a dead link. Booked becomes confirmed
    through the ordinary status step (one audit row, nobody's account); an appointment already confirmed, or with a cancellation
    request waiting, changes nothing. No money, stock or deposit is touched."""

    from .booking import system_user

    appointment = Appointment.objects.select_for_update().filter(pk=pk).first()
    if not link_alive(appointment, owner):
        return None
    if appointment.status != AppointmentStatus.BOOKED or pk in pending_cancel_requests([pk]):
        return appointment, False
    return set_status(appointment, AppointmentStatus.CONFIRMED, system_user()), True


@transaction.atomic
def patient_request_cancel(pk, owner):
    """The patient taps «ask to cancel». Records one request for reception and returns (appointment, changed), or None
    for a dead link. The appointment keeps its status, its time and its deposit; reception decides. Asking twice records once."""

    appointment = Appointment.objects.select_for_update().filter(pk=pk).first()
    if not link_alive(appointment, owner):
        return None
    if pk in pending_cancel_requests([pk]):
        return appointment, False
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=None, module="appointments", action=CANCEL_REQUESTED,
                            object_type="appointments.Appointment", object_id=str(appointment.pk), before_data={},
                            after_data={"number": appointment.number, "status": appointment.status})  # no personal data
    return appointment, True


@transaction.atomic
def mark_cancel_request_seen(appointment, user):
    """Reception has dealt with the patient's request without changing the appointment (it calls the patient and keeps it)."""

    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if appointment.pk not in pending_cancel_requests([appointment.pk]):
        return False
    _audit(appointment, user, CANCEL_REQUEST_HANDLED, {"status": appointment.status})
    return True
