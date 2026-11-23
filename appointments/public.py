"""M3: the patient's page behind the signed appointment link (a WhatsApp reminder carries it).

The page shows the clinic's name, the day and time and the service's name, and nothing else: no patient name, phone or file
number, no doctor, no diagnosis and no other appointment. The patient may **confirm**, or **ask to cancel**. Asking
is only a request that reception sees on the agenda and acts on (``services.patient_request_cancel``): a
cancellation can forfeit a held deposit, which moves money, so the page never cancels by itself.

Both actions are POST only (a link preview that opens the address must change nothing) and CSRF-protected. A link that is
forged, altered, expired, of another kind, or of an appointment that is cancelled, a no-show, arrived, done, already past
or gone, is the same expired page with the same status, on a GET and on a POST.
"""

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from . import booking, online, services
from .models import Appointment, AppointmentStatus


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _private(response):
    """No caching, no indexing and no referrer: the address itself is the patient's key."""

    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"  # the stricter "no referrer" policy makes browsers post "Origin: null" (CSRF 403)
    patch_vary_headers(response, ("Accept",))
    return response


def _expired(request):
    from printing.share import expired_page

    return expired_page(request, _lang(request))


def _target(token):
    """(the appointment's id, the patient the link was made for), or None when the token is not an appointment's or
    names no patient. The patient is checked against the appointment on every request (``services.link_alive``): an
    appointment moved to someone else must not open for the person who held the old link."""

    from printing.share import read_token_data

    data = read_token_data(token)
    if data is None or data["k"] != "appointment" or not isinstance(data.get("o"), int):
        return None
    return data["id"], data["o"]


WORDS = {
    "ar": {
        "eyebrow": "ميعادك", "when_day": "اليوم", "when_time": "الساعة", "service": "الخدمة", "place": "المكان",
        "open_h": "أكّد حضورك", "open_p": "اضغط «تأكيد الحضور» لو حضرتك هتيجي في الميعاد ده.", "confirm": "تأكيد الحضور", "ask": "طلب إلغاء",
        "confirmed": "اتأكد ميعادك. في انتظارك.", "confirmed_p": "لو مش هتقدر تيجي، تقدر تبعت طلب إلغاء.",
        "requested": "وصلنا طلب الإلغاء.", "requested_p": "هنتواصل مع حضرتك. الميعاد بيفضل محجوز لحد ما نرد، ولأي تعديل تواصل معانا.",
        "ask_h": "طلب إلغاء الميعاد", "ask_p": "ده طلب بس: الميعاد مش بيتلغي من هنا. هنتواصل مع حضرتك، وبيفضل محجوز لحد ما نرد.",
        "ask_yes": "إرسال الطلب", "back": "رجوع",
        "note": "الرابط ده خاص بحضرتك، فياريت ماتشاركوش مع حد.",
    },
    "en": {
        "eyebrow": "Your appointment", "when_day": "Day", "when_time": "Time", "service": "Service", "place": "Place",
        "open_h": "Confirm you are coming", "open_p": "Tap confirm if you will come at this time.", "confirm": "Confirm", "ask": "Ask to cancel",
        "confirmed": "Your appointment is confirmed. We are expecting you.", "confirmed_p": "If you cannot make it, you can send a cancellation request.",
        "requested": "We received your cancellation request.", "requested_p": "We will contact you. The appointment stays booked until we reply, so call us if you would rather change it.",
        "ask_h": "Ask to cancel", "ask_p": "This is a request only: nothing is cancelled from here. We will contact you, and the appointment stays booked until we reply.",
        "ask_yes": "Yes, send the request", "back": "Back",
        "note": "This link is private to you. Please do not share it.",
    },
}


def _many_entities():
    from entities.services import is_multi_entity

    return is_multi_entity()


def _context(request, appointment, lang, at=None):
    from printing.company import company_details
    from reports.aging import whatsapp_number

    company = company_details()
    token = request.resolver_match.kwargs.get("token", "") if request.resolver_match else ""
    here = reverse("share_document", args=[token]) if token else request.path
    local = timezone.localtime(appointment.starts_at)
    clock, suffix = booking.clock_label(appointment.starts_at, lang)
    requested = appointment.pk in services.pending_cancel_requests([appointment.pk])
    entity = appointment.entity
    other = "ar" if lang == "en" else "en"
    at = at or online.now()
    return {
        "call": online.page_box(appointment, lang, at), "call_words": online.PAGE_WORDS[lang],  # M10 (HG-096): the link is in the box only inside the window
        "can_act": appointment.status in (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED) and appointment.starts_at > at,
        "arrived": appointment.status == AppointmentStatus.ARRIVED,
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "company": company, "phone": company["phone"],
        "whatsapp": whatsapp_number(company["phone"]),
        "clinic": entity.display(lang) if entity is not None and _many_entities() else company["name"],
        "entity_name": entity.display(lang) if entity is not None and _many_entities() else "",
        "day_text": booking.day_label(local.date(), lang), "clock": clock, "suffix": suffix,
        "service": appointment.service.item_name if appointment.service_id else "",
        "confirmed": appointment.status == AppointmentStatus.CONFIRMED, "requested": requested,
        "confirm_url": reverse("share_appointment_confirm", args=[token]) if token else "",
        "ask_url": reverse("share_appointment_cancel_request", args=[token]) if token else "",
        "switch_url": f"{here}?lang={other}",
    }


def page(request, pk):
    """GET (called by the share dispatcher): the patient's page, or the expired page for a dead link."""

    target = _target(request.resolver_match.kwargs.get("token", "") if request.resolver_match else "")
    appointment = Appointment.objects.select_related("service", "entity", "invoice").filter(pk=pk).first()
    at = online.now()
    # M10: an online booking's page also lives through its call window (after the start), which the confirm and cancel posts never do
    if target is None or target[0] != pk or not (services.link_alive(appointment, target[1], at) or online.join_page_alive(appointment, target[1], at)):
        return _expired(request)
    return _private(render(request, "appointments/confirm_public.html", _context(request, appointment, _lang(request), at)))


def _answer(request, token, action):
    target = _target(token)
    result = action(*target) if target is not None else None
    if result is None:
        return _expired(request)
    return _private(HttpResponseRedirect(f"{reverse('share_document', args=[token])}?lang={_lang(request)}", status=303))


@require_POST
@csrf_protect
def confirm(request, token):
    return _answer(request, token, services.patient_confirm)


@require_POST
@csrf_protect
def cancel_request(request, token):
    return _answer(request, token, services.patient_request_cancel)
