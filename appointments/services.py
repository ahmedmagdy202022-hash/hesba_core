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
  other, so stock, cashbox and customer ledger stay the sales engine's own.
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
    },
    "en": {
        "customer": "Choose the customer.", "when": "Enter a valid date and time.", "minutes": "The duration must be {a} to {b} minutes.", "price": "Invalid price.",
        "address": "A visit needs an address.", "overlap": "{employee} already has an appointment from {start} to {end}.", "status": "The appointment cannot move from “{old}” to “{new}”.",
        "closed": "This appointment is done or cancelled; it cannot be changed.", "no_service": "Choose the service first to make the invoice.", "setup": "An active selling location is needed.",
        "billed": "This appointment already has an invoice.", "not_done": "The appointment must be in progress or done to be billed.",
    },
}


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
            raise ValidationError(words["overlap"].format(employee=employee.name, start=f"{local(other.starts_at):%H:%M}", end=f"{local(other.ends_at):%H:%M}"))


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
    appointment = Appointment.objects.create(number=_next_number(timezone.localtime(values["starts_at"]).date()), created_by=user, **values)
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


@transaction.atomic
def bill(appointment, user, lang="ar"):
    """The draft sales invoice for a done (or in-progress) appointment; made once."""

    from master_data.models import Location
    from taxes.services import create_sales_draft_with_tax

    words = MESSAGES[lang]
    appointment = Appointment.objects.select_for_update(of=("self",)).select_related("service", "customer").get(pk=appointment.pk)
    if appointment.invoice_id:
        return appointment.invoice
    if appointment.status not in (AppointmentStatus.ARRIVED, AppointmentStatus.DONE):
        raise ValidationError(words["not_done"])
    if appointment.service is None:
        raise ValidationError(words["no_service"])
    location = Location.objects.filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()
    if location is None:
        raise ValidationError(words["setup"])
    invoice = create_sales_draft_with_tax(
        {"invoice_number": f"INV-{appointment.number}", "invoice_date": timezone.localdate(), "customer": appointment.customer,
         "selling_location": location, "cashbox": None, "discount_amount": Decimal("0"), "tax_amount": Decimal("0"),
         "paid_now": Decimal("0"), "notes": f"Appointment {appointment.number}"},
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
    _audit(appointment, user, "bill_appointment", {"invoice": invoice.invoice_number, "amount": str(invoice.total_amount)})
    return invoice


def day_agenda(day, employee=None):
    start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
    rows = Appointment.objects.filter(starts_at__gte=start, starts_at__lt=start + timedelta(days=1)).select_related("customer", "employee", "service", "invoice")
    if employee is not None:
        rows = rows.filter(employee=employee)
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

    from staff.models import Employee

    start = timezone.make_aware(timezone.datetime.combine(date_from, timezone.datetime.min.time()))
    end = timezone.make_aware(timezone.datetime.combine(date_to + timedelta(days=1), timezone.datetime.min.time()))
    done = Appointment.objects.filter(status=AppointmentStatus.DONE, starts_at__gte=start, starts_at__lt=end)
    rows = []
    for employee in Employee.objects.all():
        mine = done.filter(employee=employee)
        count = mine.count()
        sales = sum((net_sales(appointment.invoice) for appointment in mine.filter(invoice__status="posted").select_related("invoice")), Decimal("0"))
        if not count and not employee.active:
            continue
        rows.append({"employee": employee, "done": count, "sales": money_round(sales),
                     "commission": money_round(sales * employee.commission_percent / Decimal("100"))})
    return rows
