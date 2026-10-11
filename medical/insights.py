"""MED-002: how the clinic is doing, read from the records it already keeps.

Read-only. Visits come from ``ClinicalVisit``. Revenue comes from the posted
invoices of done appointments (the same figure the appointments performance
report uses: before tax, after posted returns), counted only in the entity
being worked in (HG-034). Nothing here writes or changes a ledger.

- A visit is a *first visit* when it is the patient's earliest visit on record;
  every later one is a *follow-up*.
- A patient is *new* in a period when their first visit falls in it, and
  *returning* when they were seen in it but first came earlier.
- A follow-up is *missed* when its date has passed, the patient has not been
  seen since the visit that asked for it, and nothing is booked for them.
"""

from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal

from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from config.money import money_round
from entities import scope as entity_scope

from .models import ClinicalVisit

ZERO = Decimal("0")
#: How far back a passed follow-up is still worth a call.
MISSED_WINDOW_DAYS = 90
#: How far ahead a follow-up counts as coming up.
DUE_WINDOW_DAYS = 7


def _first_visits(patient_ids):
    """{patient id: pk of their earliest visit} (by date, then by entry order)."""

    first = {}
    for patient, pk in ClinicalVisit.objects.filter(patient_id__in=patient_ids).order_by("visit_date", "pk").values_list("patient_id", "pk"):
        first.setdefault(patient, pk)
    return first


def _span(first, last):
    start = timezone.make_aware(timezone.datetime.combine(first, timezone.datetime.min.time()))
    return start, timezone.make_aware(timezone.datetime.combine(last + timedelta(days=1), timezone.datetime.min.time()))


def _billed(first, last):
    """Done appointments in [first, last] whose invoice is posted, in this entity, with what each earned."""

    from appointments.models import Appointment, AppointmentStatus
    from appointments.services import net_sales

    start, end = _span(first, last)
    rows = (entity_scope.scope(Appointment.objects, "invoice__" + entity_scope.SALES_INVOICE)
            .filter(status=AppointmentStatus.DONE, starts_at__gte=start, starts_at__lt=end, invoice__status="posted")
            .select_related("invoice", "employee", "service"))
    return [(appointment, net_sales(appointment.invoice)) for appointment in rows]


def _steps_billed(first, last):
    """MED-003: dental steps done in [first, last] whose invoice is posted, in this entity, each with its share of the invoice's net sales."""

    from appointments.services import net_sales

    from .models import StepStatus, TreatmentStep

    steps = list(entity_scope.scope(TreatmentStep.objects, "invoice__" + entity_scope.SALES_INVOICE)
                 .filter(status=StepStatus.DONE, done_on__gte=first, done_on__lte=last, invoice__status="posted")
                 .select_related("invoice", "doctor", "service"))
    by_invoice = {}
    for step in steps:
        by_invoice.setdefault(step.invoice_id, []).append(step)
    rows = []
    for group in by_invoice.values():
        net, total = net_sales(group[0].invoice), sum((step.price for step in group), ZERO)
        for step in group:  # the invoice's net (after discount and returns) shared by price
            rows.append((step, net * step.price / total if total > 0 else ZERO))
    return rows


def period_report(first, last, with_money=True):
    """The period per doctor and per service, and the patients seen.

    Returns {"doctors": [...], "services": [...], "totals": {...}}. Revenue figures are zero when ``with_money`` is False."""

    visits = list(ClinicalVisit.objects.filter(visit_date__gte=first, visit_date__lte=last).select_related("doctor").order_by("visit_date", "pk"))
    firsts = _first_visits({visit.patient_id for visit in visits})
    doctors = OrderedDict()

    def doctor_row(employee):
        key = employee.pk if employee else None
        return doctors.setdefault(key, {"doctor": employee, "visits": 0, "first": 0, "follow_up": 0, "patients": set(), "revenue": ZERO})

    for visit in visits:
        row = doctor_row(visit.doctor)
        row["visits"] += 1
        row["first" if firsts.get(visit.patient_id) == visit.pk else "follow_up"] += 1
        row["patients"].add(visit.patient_id)
    services = OrderedDict()
    if with_money:
        for appointment, earned in _billed(first, last):
            doctor_row(appointment.employee)["revenue"] += earned
            service = services.setdefault(appointment.service_id, {"service": appointment.service, "count": 0, "revenue": ZERO})
            service["count"] += 1
            service["revenue"] += earned
        for step, earned in _steps_billed(first, last):  # dental plans are billed by step, not by appointment
            doctor_row(step.doctor)["revenue"] += earned
            service = services.setdefault(step.service_id, {"service": step.service, "count": 0, "revenue": ZERO})
            service["count"] += 1
            service["revenue"] += earned
    doctor_rows = sorted(doctors.values(), key=lambda row: (row["doctor"] is None, -row["revenue"], -row["visits"], row["doctor"].name if row["doctor"] else ""))
    for row in doctor_rows:
        row["patients"] = len(row["patients"])
        row["revenue"] = money_round(row["revenue"])
    service_rows = sorted(services.values(), key=lambda row: (-row["revenue"], row["service"].item_name if row["service"] else ""))
    for row in service_rows:
        row["revenue"] = money_round(row["revenue"])
        row["average"] = money_round(row["revenue"] / row["count"]) if row["count"] else ZERO
    seen = {visit.patient_id for visit in visits}
    new = {visit.patient_id for visit in visits if firsts.get(visit.patient_id) == visit.pk}
    totals = {
        "visits": len(visits),
        "first": sum(row["first"] for row in doctor_rows),
        "follow_up": sum(row["follow_up"] for row in doctor_rows),
        "patients": len(seen),
        "new": len(new),
        "returning": len(seen - new),
        "revenue": money_round(sum((row["revenue"] for row in doctor_rows), ZERO)),
    }
    return {"doctors": doctor_rows, "services": service_rows, "totals": totals}


def _booked_patients(today):
    from appointments.models import Appointment, AppointmentStatus

    start, _ = _span(today, today)
    return set(Appointment.objects.filter(starts_at__gte=start, status__in=[AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED])
               .values_list("customer_id", flat=True))


def _latest_asking(since, until):
    """Each patient's latest visit, when that visit asks for a follow-up dated in [since, until]."""

    latest = ClinicalVisit.objects.filter(patient=OuterRef("patient")).order_by("-visit_date", "-pk").values("pk")[:1]
    return list(ClinicalVisit.objects.filter(follow_up_date__gte=since, follow_up_date__lte=until).annotate(latest=Subquery(latest))
                .filter(pk=F("latest")).select_related("patient", "doctor"))


def missed_follow_ups(today=None):
    """Follow-ups whose date passed (within the last 90 days) with no visit since and nothing booked; most overdue first."""

    today = today or timezone.localdate()
    booked = _booked_patients(today)
    rows = [{"visit": visit, "patient": visit.patient, "doctor": visit.doctor, "due": visit.follow_up_date, "late": (today - visit.follow_up_date).days}
            for visit in _latest_asking(today - timedelta(days=MISSED_WINDOW_DAYS), today - timedelta(days=1)) if visit.patient_id not in booked]
    return sorted(rows, key=lambda row: (-row["late"], row["patient"].name))


def coming_follow_ups(today=None):
    """Follow-ups due today or in the next 7 days that are not booked yet: the patients to call."""

    today = today or timezone.localdate()
    booked = _booked_patients(today)
    rows = [{"visit": visit, "patient": visit.patient, "doctor": visit.doctor, "due": visit.follow_up_date}
            for visit in _latest_asking(today, today + timedelta(days=DUE_WINDOW_DAYS)) if visit.patient_id not in booked]
    return sorted(rows, key=lambda row: (row["due"], row["patient"].name))
