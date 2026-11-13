"""M10 (HG-096): what a visitor who picks the clinic on the demo finds of the online consultation: two online bookings today.

One starts a few minutes from now, is confirmed and already has a generated call link, so it is inside the join window and
the doctor's button (agenda, queue, visit) and the patient's confirm page show it. The other starts later today and has no
link yet, so the screens are not empty and «اعمل لينك مكالمة» has something to do. Both go through the real services
(``appointments.services.book`` / ``set_status`` and ``appointments.online.set_link``); nothing is posted and no money moves.
The link points at the clinic's meeting server (the public Jitsi by default); the demo never opens it.

Idempotent: a clinic that already holds an online booking is left as it is. Only for the clinic.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

#: Minutes from now: the first is inside the patient's window (it opens 15 minutes before the start), the second is later today.
STARTS_IN = (10, 90)
#: Video findings 7 and 8: the two online bookings have patients of their own. They used to take the sample's last two
#: customers, which put «شركة النور للتجارة» (a company) in a consultation, on the agenda and in the dashboard's next
#: appointment; and the sample's own people are already the queue's, the bed's and today's appointment. (code, name, phone)
PATIENTS = (("ياسر الشناوي", "01000000341"), ("شيماء رمزي", "01000000342"))
SLOT_STEP = 30  # a doctor who already has something then is tried again this much later


def applies(catalog):
    """Only the medical clinic (never a business that merely resembles one)."""

    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return bool(profile and (profile.activity_slug, profile.sub_activity_slug) == ("medical", "clinic") and catalog.key == "medical.clinic")


def _patients(owner):
    """The two patients of the online bookings: people of their own, added by the real quick-add service
    (idempotent — it answers with the existing record for a phone number it already knows)."""

    from sales.pos_customers import quick_add_customer

    return [quick_add_customer(name, phone, owner)[0] for name, phone in PATIENTS]


def seed(master, doctor, owner, catalog, extras):
    """Two online bookings today. Returns whether it made them."""

    from appointments import online, services
    from appointments.models import Appointment, AppointmentKind, AppointmentStatus
    from settings_core.demo_clinic import SECOND_DOCTOR
    from staff.models import Employee

    if doctor is None or "appointments" not in extras or Appointment.objects.filter(kind=AppointmentKind.ONLINE).exists():
        return False
    service = next((item for item in master["items"] if not item.is_stock_tracked), None)
    patients = _patients(owner)
    if service is None or len(patients) < 2:
        return False
    person = Employee.objects.filter(name=SECOND_DOCTOR[0], active=True).first() or doctor  # her afternoons are free of the old sample's
    moment = timezone.localtime().replace(second=0, microsecond=0)
    with transaction.atomic():
        for n, (minutes, patient) in enumerate(zip(STARTS_IN, patients)):
            for attempt in range(8):
                starts = moment + timedelta(minutes=minutes + attempt * SLOT_STEP)
                try:
                    with transaction.atomic():
                        appointment = services.book({"customer": patient, "employee": person, "service": service, "starts_at": starts, "duration_minutes": 30,
                                                     "kind": AppointmentKind.ONLINE, "notes": "كشف أونلاين"}, owner)
                    break
                except ValidationError as exc:
                    if getattr(exc, "code", None) != "overlap":
                        raise
            else:
                continue
            if n == 0:
                services.set_status(appointment, AppointmentStatus.CONFIRMED, owner)
                online.set_link(appointment, owner, "generate")
    return True
