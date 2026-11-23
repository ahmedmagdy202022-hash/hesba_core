"""S9: "my jobs today", the technician's phone screen (roadmap item 18: a web screen, not a store app).

Behaviour lives here; the view only renders and redirects. Rules, stated once:

* the screen is personal: it lists the jobs of the employee linked to the signed-in user, for a technician and for a
  manager alike (a manager who also works the bench sees their own jobs here, the counter keeps the rest);
* work orders come through ``workorders.services.visible_to`` (the existing technician scoping) narrowed to this
  employee, and are moved only through ``workorders.services.move`` with the moves a technician already has;
* appointments and visits are this employee's own rows of the day. Reading them here is the one new read for a
  technician without the sales permission; changing their status keeps the appointments screen's rule exactly
  (``sales.create_sales_invoice``), so a plain technician gets no buttons there;
* map links are plain Google Maps URLs built from the address text: no key, no call out, nothing stored.
"""

from urllib.parse import quote, urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from permissions.services import user_has_permission

MAPS_SEARCH = "https://www.google.com/maps/search/?api=1&"
MAPS_ROUTE = "https://www.google.com/maps/dir/?api=1&"
#: Google Maps takes a destination plus up to nine waypoints on a phone.
MAX_ROUTE_STOPS = 10
BOOK = "sales.create_sales_invoice"
VIEW_APPOINTMENTS = "sales.view_sales_invoices"

#: What a technician may do to a visit from the phone, and the status each button moves it to.
APPOINTMENT_MOVES = ("arrived", "done")
#: Work orders that are still the technician's to move: not waiting for the customer (quoted) or for pickup (ready).
BENCH = ("received", "diagnosed", "approved", "in_repair", "refused")

ACTION_LABELS = {
    "ar": {"diagnosed": "خلّصت التشخيص", "in_repair": "ابدأ الإصلاح", "ready": "جاهز للتسليم", "arrived": "وصلت", "done": "خلّصت"},
    "en": {"diagnosed": "Diagnosed", "in_repair": "Start repair", "ready": "Ready for pickup", "arrived": "I arrived", "done": "Done"},
}
MESSAGES = {
    "ar": {"not_linked": "الحساب ده مش مربوط بفني؛ شاشة «شغلي النهارده» للفنيين بس.", "forbidden": "تعديل المواعيد لصاحب الصلاحية بس.",
           "moved": "اتحدّثت حالة {ref}.", "bad_move": "الحالة دي مش متاحة من الموبايل.", "bad_kind": "طلب غلط.",
           "module_off": "المواعيد والزيارات مش شغّالة في الإعداد الحالي.",
           "overview": "حسابك مش مربوط بموظف، فبنوريك شغل الفريق كله هنا بدل «شغلي النهارده»."},
    "en": {"not_linked": "This account is not linked to a technician; “My jobs today” is for technicians.", "forbidden": "Only users with the permission can change appointments.",
           "moved": "{ref} was updated.", "bad_move": "That status is not available from the phone.", "bad_kind": "Invalid request.",
           "module_off": "Appointments and visits are not switched on.",
           "overview": "Your account is not linked to an employee, so here is the whole team's work instead of “My jobs today”."},
}


# ---- links ----

def clean_address(address):
    """One line of text: customers type addresses on several lines."""

    return " ".join(str(address or "").split())


def map_link(address):
    """The Google Maps search for an address, or "" when there is none."""

    address = clean_address(address)
    return MAPS_SEARCH + urlencode({"query": address}, quote_via=quote) if address else ""


def route_link(addresses):
    """One Google Maps directions link through the stops, in order, from the person's own position.

    The last stop is the destination and the earlier ones are waypoints; one stop has no waypoints and none has no link.
    A stop repeated right after itself is one stop. Longer days are cut to what Maps accepts (the earliest stops)."""

    stops = []
    for address in map(clean_address, addresses):
        if address and (not stops or stops[-1] != address):
            stops.append(address)
    stops = stops[:MAX_ROUTE_STOPS]
    if not stops:
        return ""
    params = {"destination": stops[-1]}
    if len(stops) > 1:
        params["waypoints"] = "|".join(stops[:-1])
    return MAPS_ROUTE + urlencode(params, quote_via=quote)


def tel_link(phone):
    from .services import clean_phone

    number = clean_phone(phone)
    return f"tel:{number}" if number else ""


# ---- who ----

def employee_of(user):
    from workorders.services import technician_of

    return technician_of(user)


def sees_my_day(user):
    """The menu entry: a linked technician who does not run the counter (managers keep their own menus)."""

    from workorders.services import can_view

    return employee_of(user) is not None and not can_view(user)


def overview_url(user):
    """Where someone who may see everyone's work, but has no technician record of their own, finds the day's work instead:
    the work-orders board in a workshop, otherwise the appointments agenda. ``""`` when they may see neither."""

    from django.urls import reverse

    from workorders.services import can_view, is_workshop

    if is_workshop() and can_view(user):
        return reverse("workorders:list")
    if _appointments_on() and user_has_permission(user, VIEW_APPOINTMENTS):
        return reverse("appointments:agenda")
    return ""


def can_change_appointments(user):
    return user_has_permission(user, BOOK)


# ---- the day ----

def _appointments_on():
    from settings_core.setup_services import module_is_enabled

    return module_is_enabled("appointments_visits")


def _visit_address(appointment):
    if appointment.address:
        return appointment.address
    return appointment.customer.address if appointment.kind == "visit" else ""


def _appointment_card(appointment, lang, can_change, can_open):
    from appointments.models import AppointmentKind, AppointmentStatus
    from appointments.services import NEXT
    from settings_core.display_labels import choice_label

    done = appointment.status == AppointmentStatus.DONE
    address = clean_address(_visit_address(appointment))
    is_visit = appointment.kind == AppointmentKind.VISIT
    moves = [status for status in NEXT.get(appointment.status, ()) if status in APPOINTMENT_MOVES] if can_change else []
    return {
        "kind": "appointment", "pk": appointment.pk, "ref": appointment.number, "time": timezone.localtime(appointment.starts_at).strftime("%H:%M"),
        "is_visit": is_visit, "type": "visit" if is_visit else "shop", "customer": appointment.customer.name,
        "what": appointment.service.item_name if appointment.service else appointment.notes, "status": appointment.status,
        "status_label": choice_label(appointment, "status", lang), "done": done, "late": False,
        "phone": appointment.customer.phone, "tel": tel_link(appointment.customer.phone), "address": address, "map": map_link(address),
        "actions": [(status, ACTION_LABELS[lang][status]) for status in moves], "can_open": can_open,
    }


def _work_order_card(order, lang, today):
    from workorders import services as wo

    address = clean_address(order.customer.address)
    moves = [status for status in wo.NEXT.get(order.status, ()) if status in wo.TECHNICIAN_MOVES]
    return {
        "kind": "work_order", "pk": order.pk, "ref": order.number, "time": "", "type": "work_order", "is_visit": False, "customer": order.customer.name,
        "what": f"{order.device}: {order.problem}"[:160] if order.problem else order.device, "status": order.status,
        "status_label": wo.STATUS_LABELS[lang].get(order.status, order.status), "done": False,
        "late": bool(order.promised_on and order.promised_on < today), "due_today": order.promised_on == today, "promised_on": order.promised_on,
        "phone": order.customer.phone, "tel": tel_link(order.customer.phone), "address": address, "map": map_link(address),
        "actions": [(status, ACTION_LABELS[lang][status]) for status in moves], "can_open": True,
    }


def my_day(user, lang="ar", today=None):
    """Everything the screen shows: the timed jobs first, then the bench, plus the route through today's visits.

    A work order has no time of day, so the open orders follow the appointments, late and due-today ones first."""

    from appointments.models import Appointment, AppointmentStatus
    from appointments.services import day_agenda
    from workorders import services as wo

    employee = employee_of(user)
    if employee is None:
        raise PermissionDenied(MESSAGES[lang]["not_linked"])
    today = today or timezone.localdate()
    can_change, can_open = can_change_appointments(user), user_has_permission(user, VIEW_APPOINTMENTS)
    cards = []
    if _appointments_on():
        rows = day_agenda(today, employee=employee).exclude(status__in=(AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW)).order_by("starts_at", "pk")
        cards += [_appointment_card(row, lang, can_change, can_open) for row in rows]
    visits = len(cards)
    if wo.is_workshop():
        orders = wo.visible_to(user).filter(technician=employee, status__in=BENCH).select_related("customer")
        ordered = sorted(orders, key=lambda order: (order.promised_on is None, order.promised_on or today, order.received_at, order.pk))
        cards += [_work_order_card(order, lang, today) for order in ordered]
    pending = [card for card in cards if card["kind"] == "appointment" and not card["done"]]
    if pending:
        pending[0]["is_next"] = True
    stops = [{"time": card["time"], "customer": card["customer"], "address": card["address"], "map": card["map"], "ref": card["ref"]}
             for card in cards if card["kind"] == "appointment" and card["is_visit"] and not card["done"] and card["address"]]
    return {
        "employee": employee, "today": today, "cards": cards, "visit_cards": cards[:visits], "order_cards": cards[visits:],
        "visits": visits, "orders": len(cards) - visits,
        "visits_done": sum(1 for card in cards if card["kind"] == "appointment" and card["done"]),
        "stops": stops, "route": route_link([stop["address"] for stop in stops]), "route_cut": len(stops) > MAX_ROUTE_STOPS,
    }


# ---- doing ----

def _fail(lang, key):
    raise ValidationError(MESSAGES[lang][key])


@transaction.atomic
def act(user, kind, pk, to, lang="ar"):
    """One quick action on one of the signed-in technician's own jobs; returns its reference for the message."""

    from appointments import services as appt
    from appointments.models import Appointment
    from workorders import services as wo
    from workorders.models import WorkOrder

    employee = employee_of(user)
    if employee is None:
        raise PermissionDenied(MESSAGES[lang]["not_linked"])
    if kind == "work_order":
        order = get_object_or_404(wo.visible_to(user, WorkOrder.objects.filter(technician=employee)), pk=pk)
        if to not in wo.TECHNICIAN_MOVES:
            _fail(lang, "bad_move")
        return wo.move(order, to, user, lang=lang).number
    if kind == "appointment":
        if not _appointments_on():  # the screen hides them with the module; the POST closes with it too
            raise PermissionDenied(MESSAGES[lang]["module_off"])
        if not can_change_appointments(user):
            raise PermissionDenied(MESSAGES[lang]["forbidden"])
        appointment = get_object_or_404(appt.scoped(Appointment.objects.filter(employee=employee)), pk=pk)
        if to not in APPOINTMENT_MOVES:
            _fail(lang, "bad_move")
        return appt.set_status(appointment, to, user, lang).number
    _fail(lang, "bad_kind")
