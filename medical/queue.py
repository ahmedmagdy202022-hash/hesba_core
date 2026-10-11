"""MED-007: the reception queue (HG-043, design A).

Reception checks a patient in (for today's appointment, or as a walk-in) and
the patient gets the day's next number. A doctor (or reception) calls the next
one in, and marks the visit done; a patient who leaves before being seen is
marked so. The waiting-room screen shows who is called in and who is next, by
number only. Nothing here moves money: the visit is billed from its
appointment, or on a sales invoice, as before.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from .models import QueueTicket, TicketStatus

#: Reception works the queue with the booking permission, the same one appointments use.
DESK = "sales.create_sales_invoice"
SUB_ACTIVITIES = ("clinic", "medical_center")

STATUS_LABELS = {
    "ar": {"waiting": "مستني", "called": "جوّه عند الدكتور", "done": "خلص", "left": "مشي من غير كشف"},
    "en": {"waiting": "Waiting", "called": "With the doctor", "done": "Done", "left": "Left unseen"},
}
MESSAGES = {
    "ar": {"patient": "اختار المريض.", "twice": "المريض ده في الدور النهارده بالفعل (رقم {number}).", "nobody": "مفيش حد مستني.",
           "move": "الدور ده «{status}»؛ مينفعش يتغيّر كده.", "desk": "الدور محتاج صلاحية الاستقبال (الحجز).",
           "not_yours": "تقدر تنادي على مرضاك أو اللي ملهمش دكتور بس."},
    "en": {"patient": "Choose the patient.", "twice": "This patient is already in today's queue (number {number}).", "nobody": "Nobody is waiting.",
           "move": "This turn is “{status}”; it cannot change that way.", "desk": "The queue needs the reception (booking) permission.",
           "not_yours": "You can call your own patients, or those with no doctor, only."},
}
NEXT = {
    TicketStatus.WAITING: (TicketStatus.CALLED, TicketStatus.LEFT),
    TicketStatus.CALLED: (TicketStatus.DONE, TicketStatus.WAITING, TicketStatus.LEFT),  # back to waiting: called by mistake
}


def has_queue():
    from entities.current import effective_activity

    activity, sub = effective_activity()
    return activity == "medical" and sub in SUB_ACTIVITIES


def _doctor_of(user):
    employee = getattr(user, "employee", None) if getattr(user, "is_authenticated", False) else None
    return employee if employee is not None and employee.active else None


def is_desk(user):
    return user_has_permission(user, DESK)


def can_use(user):
    """Reception (booking permission) and the doctors."""

    return has_queue() and (is_desk(user) or _doctor_of(user) is not None)


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def today_tickets(day=None):
    return QueueTicket.objects.filter(day=day or timezone.localdate()).select_related("patient", "doctor", "appointment")


def _audit(user, action, ticket, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="medical", action=action, object_type="medical.QueueTicket",
                            object_id=str(ticket.pk), before_data=before or {}, after_data=after)


@transaction.atomic
def check_in(patient, user, *, appointment=None, doctor=None, lang="ar"):
    """Give the patient today's next number. An appointment of theirs today is marked arrived."""

    from appointments.models import AppointmentStatus
    from appointments.services import set_status

    words = _words(lang)
    if not is_desk(user):
        raise PermissionDenied(words["desk"])
    if patient is None:
        raise ValidationError(words["patient"])
    from master_data.models import Customer

    Customer.objects.select_for_update().filter(pk=patient.pk).exists()  # a double click checks the patient in once
    day = timezone.localdate()
    open_one = QueueTicket.objects.filter(day=day, patient=patient, status__in=(TicketStatus.WAITING, TicketStatus.CALLED)).first()
    if open_one is not None:
        raise ValidationError(words["twice"].format(number=open_one.number))
    if appointment is not None and (appointment.customer_id != patient.pk or timezone.localtime(appointment.starts_at).date() != day
                                    or appointment.status not in (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED)):
        appointment = None  # only the patient's own live appointment of today
    if appointment is not None and doctor is None:
        doctor = appointment.employee
    linked = appointment if appointment is not None and not QueueTicket.objects.filter(appointment=appointment).exists() else None  # a second turn for it stays unlinked
    ticket = None
    for attempt in range(5):
        number = (QueueTicket.objects.filter(day=day).aggregate(top=Max("number"))["top"] or 0) + 1
        try:
            with transaction.atomic():
                ticket = QueueTicket.objects.create(day=day, number=number, patient=patient, doctor=doctor, arrived_at=timezone.now(), created_by=user,
                                                    appointment=linked)
            break
        except IntegrityError:  # two check-ins at once took the same number: take the next
            if attempt == 4:
                raise
    if appointment is not None and appointment.status in (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED):
        set_status(appointment, AppointmentStatus.ARRIVED, user, lang)
    _audit(user, "queue_check_in", ticket, {"number": ticket.number, "patient": patient.pk, "appointment": getattr(appointment, "pk", None),
                                            "doctor": getattr(doctor, "pk", None)}, event=AuditEventType.CREATE)
    return ticket


def _may_call(user, ticket, words):
    if is_desk(user):
        return
    own = _doctor_of(user)
    if own is None or (ticket.doctor_id not in (None, own.pk)):
        raise PermissionDenied(words["not_yours"])


@transaction.atomic
def move(ticket, to, user, lang="ar"):
    words = _words(lang)
    ticket = QueueTicket.objects.select_for_update().get(pk=ticket.pk)
    _may_call(user, ticket, words)
    if to not in NEXT.get(ticket.status, ()):
        raise ValidationError(words["move"].format(status=STATUS_LABELS["en" if lang == "en" else "ar"][ticket.status]))
    before = {"status": ticket.status}
    ticket.status = to
    now = timezone.now()
    if to == TicketStatus.CALLED:
        ticket.called_at = now
        own = _doctor_of(user)
        if ticket.doctor_id is None and own is not None:
            ticket.doctor = own  # whoever calls an unassigned patient in sees them
    elif to == TicketStatus.WAITING:
        ticket.called_at = None
    else:
        ticket.done_at = now
    ticket.save()
    _audit(user, "queue_move", ticket, {"status": to, "doctor": ticket.doctor_id}, before)
    return ticket


@transaction.atomic
def call_next(user, lang="ar"):
    """Call in the longest-waiting patient this user may see: a doctor gets their own or unassigned ones."""

    words = _words(lang)
    # skip_locked: a doctor calling at the same moment as another gets the next patient, not "nobody is waiting"
    waiting = QueueTicket.objects.select_for_update(skip_locked=True).filter(day=timezone.localdate(), status=TicketStatus.WAITING).order_by("number")
    own = _doctor_of(user)
    if not is_desk(user):
        if own is None:
            raise PermissionDenied(words["not_yours"])
        waiting = waiting.filter(Q(doctor=own) | Q(doctor__isnull=True))
    elif own is not None:
        waiting = waiting.filter(Q(doctor=own) | Q(doctor__isnull=True)) if waiting.filter(Q(doctor=own) | Q(doctor__isnull=True)).exists() else waiting
    ticket = waiting.first()
    if ticket is None:
        raise ValidationError(words["nobody"])
    return move(ticket, TicketStatus.CALLED, user, lang)


def board(day=None):
    """What the waiting-room screen shows: {"called": [...], "next": [...], "waiting": n}."""

    tickets = list(today_tickets(day).filter(status__in=(TicketStatus.WAITING, TicketStatus.CALLED)))
    called = sorted((t for t in tickets if t.status == TicketStatus.CALLED), key=lambda t: t.called_at or t.arrived_at, reverse=True)
    waiting = [t for t in tickets if t.status == TicketStatus.WAITING]
    return {"called": called[:4], "next": waiting[:6], "waiting": len(waiting)}
