"""M10 (HG-096): the online consultation: the call link of an online booking, who may see it, and when the patient may use it.

Hesba does not run the video call. The call happens on a meeting service outside Hesba (a clinic's own Jitsi server, the public
meet.jit.si, or any https link the staff paste: Google Meet, Zoom, Teams). Hesba only **makes** the link (an unguessable room name
on the clinic's meeting server) or **accepts** a pasted one, keeps it on the booking, and **guards** who sees it:

* staff: the booking's doctor and those who may manage appointments (``may_join``), and nobody else;
* the patient: only on the confirm page of M3 (same signed link, same privacy rules), only for a confirmed (or arrived), open,
  online booking that has a link, only from ``JOIN_OPENS_BEFORE`` before its start until ``JOIN_CLOSES_AFTER`` after it, and, when the
  clinic switched on «paid first», only once the booking is covered (``covered``);
* never in a message, an outbox row, a redirect, a log line or an audit row (the audit only says a link was set, replaced or removed).

Nothing here posts, pays, refunds or counts money: the deposit and the invoice are only **read**.
"""

import re
import secrets
import string
from datetime import timedelta
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from .models import Appointment, AppointmentKind, AppointmentStatus

#: The patient's «join the call» button appears this long before the booking starts (time to test the camera) ...
JOIN_OPENS_BEFORE = timedelta(minutes=15)
#: ... and goes this long after it starts (a long consultation, a late start), after which the confirm page is dead as it always was.
JOIN_CLOSES_AFTER = timedelta(hours=3)
#: A booking the call is still useful for (the same statuses the queue and the agenda treat as live).
LIVE = (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED)
#: Statuses in which the patient has said «I am coming» (or reception checked them in).
CONFIRMED = (AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED)
MANAGE = "sales.create_sales_invoice"  # the booking permission every appointment screen uses

SETTING_BASE = "appointments.online.base_url"
SETTING_PREPAID = "appointments.online.prepaid"
SETTING_PUBLIC = "appointments.online.public_booking"
DEFAULT_BASE = "https://meet.jit.si"
MAX_LINK = 600  # the column's length; Teams links are the longest in use (a few hundred characters)
MAX_BASE = 200
#: 26 lowercase letters and digits: 36 ** 26 is about 2 ** 134. Jitsi (like most meeting servers) reads room names case-insensitively,
#: so the alphabet is lower case only: mixed case would look stronger than it is.
ROOM_ALPHABET = string.ascii_lowercase + string.digits
ROOM_LENGTH = 26

WORDS = {
    "ar": {
        "empty": "اكتب الرابط.", "scheme": "الرابط لازم يبدأ بـ https:// بس (http وjavascript وأي نوع تاني مرفوض).",
        "creds": "الرابط فيه اسم مستخدم أو كلمة سر قبل اسم الموقع؛ ده مينفعش. استخدم رابط من غيرهم.",
        "long": "الرابط طويل أوي؛ الحد الأقصى {n} حرف.", "chars": "الرابط فيه مسافة أو حرف مش مسموح بيه.", "invalid": "الرابط مش مظبوط.",
        "base_extra": "عنوان السيرفر لازم يكون من غير علامة ? ولا #.", "not_online": "الميعاد ده مش كشف أونلاين.",
        "closed": "الميعاد ده خلص أو اتلغى؛ مينفعش تغيّر رابط المكالمة.", "none": "مفيش رابط مكالمة عشان يتشال.", "bad_mode": "اختار طريقة الرابط.",
        "set": "اتحفظ رابط المكالمة.", "replaced": "اتغيّر رابط المكالمة.", "removed": "اتشال رابط المكالمة.",
    },
    "en": {
        "empty": "Enter the link.", "scheme": "The link must start with https:// only (http, javascript and every other kind is refused).",
        "creds": "The link has a user name or password in front of the site's name, which is not allowed. Use a link without them.",
        "long": "The link is too long; the maximum is {n} characters.", "chars": "The link has a space or a character that is not allowed.",
        "invalid": "The link is not valid.", "base_extra": "The server's address must have no ? and no #.", "not_online": "This booking is not an online visit.",
        "closed": "This booking is done or cancelled; its call link cannot be changed.", "none": "There is no call link to remove.",
        "bad_mode": "Choose how to make the link.", "set": "The call link was saved.", "replaced": "The call link was replaced.",
        "removed": "The call link was removed.",
    },
}


def now():
    """The clock of every window rule here; a test patches this one hook (it is ``timezone.now`` otherwise)."""

    return timezone.now()


# ---- the clinic's settings (installation-wide ``SystemSetting`` rows; no new table) ----


def _setting(key):
    from settings_core.models import SystemSetting

    return SystemSetting.objects.filter(key=key, active=True).values_list("value", flat=True).first()


def _save_setting(key, value, data_type, description):
    from settings_core.models import SystemSetting

    SystemSetting.objects.update_or_create(key=key, defaults={"value": value, "data_type": data_type, "active": True, "description": description})


def base_url():
    """The clinic's meeting server (no trailing slash); the public Jitsi server until the clinic points it at its own."""

    raw = _setting(SETTING_BASE)
    if raw:
        try:
            return check_base(raw, "ar")
        except ValidationError:
            pass  # a damaged value is never used to make a link
    return DEFAULT_BASE


def prepaid_required():
    """«الكشف الأونلاين مدفوع مقدّماً»: off by default."""

    return _setting(SETTING_PREPAID) == "1"


def public_choice():
    """«اسمح بحجز كشف أونلاين»: the public booking page offers «in the clinic / online». Off by default."""

    return _setting(SETTING_PUBLIC) == "1"


def save_settings(data, user, lang="ar"):
    """Save the three settings (the caller checks the permission). The base address is checked like a pasted link, minus its length."""

    words = WORDS[lang]
    raw = (data.get("base_url") or "").strip()
    base = check_base(raw, lang) if raw else DEFAULT_BASE
    before = {"base_url": base_url(), "prepaid": prepaid_required(), "public_booking": public_choice()}
    after = {"base_url": base, "prepaid": bool(data.get("prepaid")), "public_booking": bool(data.get("public_booking"))}
    with transaction.atomic():
        _save_setting(SETTING_BASE, base, "string", "M10 (HG-096): the meeting server the clinic's online-visit links are made on.")
        _save_setting(SETTING_PREPAID, "1" if after["prepaid"] else "0", "boolean", "M10 (HG-096): an online visit must be paid (a deposit or a paid invoice) before the patient's join button shows.")
        _save_setting(SETTING_PUBLIC, "1" if after["public_booking"] else "0", "boolean", "M10 (HG-096): the public booking page offers an online visit.")
        AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="appointments", action="online_settings",
                                object_type="settings_core.SystemSetting", object_id="appointments.online", before_data=before, after_data=after)
    return words["set"]


# ---- the link: made or pasted, checked, audited without its text ----


def _https(raw, lang, limit):
    """The text as a plain https URL with a host and no user name or password, or ``ValidationError`` (Arabic or English)."""

    words = WORDS[lang]
    raw = (raw or "").strip()
    if not raw:
        raise ValidationError(words["empty"])
    if len(raw) > limit:
        raise ValidationError(words["long"].format(n=limit))
    if any(ord(char) <= 32 or ord(char) >= 127 or char in "\"'<>\\`" for char in raw):
        raise ValidationError(words["chars"])
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - a port that is not a number raises here
    except ValueError:
        raise ValidationError(words["invalid"])
    if parts.scheme.lower() != "https":
        raise ValidationError(words["scheme"])
    if not parts.hostname:
        raise ValidationError(words["invalid"])
    if "@" in parts.netloc:
        raise ValidationError(words["creds"])
    return raw, parts


def check_link(raw, lang="ar"):
    """A pasted call link: ``https://`` only, a host, no credentials in it, at most ``MAX_LINK`` characters."""

    return _https(raw, lang, MAX_LINK)[0]


def check_base(raw, lang="ar"):
    """The clinic's meeting server: the same checks, with no query or fragment, and its trailing slash dropped."""

    raw, parts = _https(raw, lang, MAX_BASE)
    if parts.query or parts.fragment or "?" in raw or "#" in raw:
        raise ValidationError(WORDS[lang]["base_extra"])
    return raw.rstrip("/")


def _room_prefix():
    """A recognisable, ASCII start for the room name: the clinic's own name when it has letters or digits (never a patient's)."""

    from printing.company import company_details

    return re.sub(r"[^a-z0-9]", "", (company_details().get("name") or "").lower())[:12] or "clinic"


def make_link(prefix=None, base=None):
    """``<base>/<room>``: the room is a fixed prefix and ``ROOM_LENGTH`` random characters from ``secrets``; nothing about the
    patient, the doctor or the booking goes into it."""

    room = (prefix if prefix is not None else _room_prefix()) + "".join(secrets.choice(ROOM_ALPHABET) for _ in range(ROOM_LENGTH))
    return f"{base or base_url()}/{room}"


def _audit(appointment, user, action, after, before=None):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="appointments", action=action,
                            object_type="appointments.Appointment", object_id=str(appointment.pk), before_data=before or {}, after_data=after)


def _locked(appointment, lang):
    words = WORDS[lang]
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if appointment.kind != AppointmentKind.ONLINE:
        raise ValidationError(words["not_online"])
    if appointment.status not in LIVE:
        raise ValidationError(words["closed"])
    return appointment


@transaction.atomic
def set_link(appointment, user, mode, pasted="", lang="ar"):
    """Give the online booking its call link: ``mode`` is ``"generate"`` (Hesba makes one on the clinic's server) or ``"paste"``.
    A booking that has one gets a **new** one. The audit row says «set» or «replaced» and the source, never the link."""

    words = WORDS[lang]
    if mode not in ("generate", "paste"):
        raise ValidationError(words["bad_mode"])
    appointment = _locked(appointment, lang)
    url = make_link() if mode == "generate" else check_link(pasted, lang)
    replaced = bool(appointment.call_url)
    appointment.call_url = url
    appointment.save(update_fields=["call_url"])
    _audit(appointment, user, "set_call_link", {"call_link": "replaced" if replaced else "set", "source": "generated" if mode == "generate" else "pasted"},
           {"call_link": "set" if replaced else "none"})
    return appointment


@transaction.atomic
def remove_link(appointment, user, lang="ar"):
    appointment = _locked(appointment, lang)
    if not appointment.call_url:
        raise ValidationError(WORDS[lang]["none"])
    appointment.call_url = ""
    appointment.save(update_fields=["call_url"])
    _audit(appointment, user, "remove_call_link", {"call_link": "none"}, {"call_link": "set"})
    return appointment


def drop_if_not_online(appointment, user):
    """A booking changed from online to another kind keeps no call link (the caller already holds the row's lock and saved the kind)."""

    if appointment.kind != AppointmentKind.ONLINE and appointment.call_url:
        appointment.call_url = ""
        appointment.save(update_fields=["call_url"])
        _audit(appointment, user, "remove_call_link", {"call_link": "none", "why": "kind_changed"}, {"call_link": "set"})


# ---- staff: who sees the button ----


class Viewer:
    """What one signed-in user may do with call links, worked out once per page: managers (the booking permission) see every
    online booking's button; the doctor of a booking sees theirs (an employee holding a clinical permission, HG-084: a receptionist on the
    payroll is no doctor). Anyone else sees none."""

    def __init__(self, user):
        from medical import queue
        from permissions.services import user_has_permission

        self.manager = bool(getattr(user, "is_authenticated", False) and user_has_permission(user, MANAGE))
        doctor = queue._doctor_of(user)
        self.doctor = doctor.pk if doctor is not None else None

    def url(self, appointment):
        """The call link of ``appointment`` for this viewer, or ``""``. Only an online booking that still has a link and is not finished."""

        if appointment.kind != AppointmentKind.ONLINE or not appointment.call_url or appointment.status not in LIVE:
            return ""
        if self.manager or (self.doctor is not None and appointment.employee_id == self.doctor):
            return appointment.call_url
        return ""


def join_urls(user, appointments):
    """{appointment id: call link} for the bookings of ``appointments`` this user may join (the ones with no link are left out)."""

    viewer = Viewer(user)
    return {appointment.pk: url for appointment in appointments if (url := viewer.url(appointment))}


# ---- the patient: when the button shows ----


def window(appointment):
    """(opens, closes): the moments the patient's button appears and goes."""

    return appointment.starts_at - JOIN_OPENS_BEFORE, appointment.starts_at + JOIN_CLOSES_AFTER


def join_page_alive(appointment, owner, at=None):
    """Whether the patient's confirm page still opens for an online booking after the ordinary link has gone dead (the start has
    passed): a confirmed or arrived booking of the same patient, until the window closes. ``link_alive`` (which guards the confirm
    and cancel-request posts) is **not** loosened by this."""

    at = at or now()
    return (appointment is not None and owner is not None and appointment.customer_id == owner and appointment.kind == AppointmentKind.ONLINE
            and appointment.status in CONFIRMED and at <= window(appointment)[1])


def covered(appointment):
    """Whether the visit is paid for, read from what exists and changing none of it: a deposit taken on the booking (held, or applied to
    its invoice) of at least its price, or its invoice posted and with nothing left to pay. A free visit is covered.

    The invoice branch is a rule only: billing a booking also finishes it (``services.bill`` sets it done), and a finished booking has no
    call, so in practice the deposit is what pays before a call."""

    from deposits import services as deposits
    from deposits.models import DepositStatus
    from payments import services as payments

    price = appointment.price or 0
    if price <= 0:
        return True
    if any(deposit.payment.amount >= price for deposit in deposits.for_target(appointment).filter(status__in=(DepositStatus.HELD, DepositStatus.APPLIED))):
        return True
    invoice = appointment.invoice
    return invoice is not None and invoice.status == "posted" and payments.outstanding(invoice) <= 0


def pay_link(appointment, lang, at):
    """The address of the booking's invoice's live online pay link (S4), if one already exists; never made here. Read from the rows
    directly (``payments.live_link`` expires old links, which writes, and a public page changes nothing)."""

    from payments import services as payments
    from payments.models import LinkStatus, PaymentLink

    invoice = appointment.invoice
    if invoice is None or invoice.status != "posted":
        return ""
    link = PaymentLink.objects.filter(invoice=invoice, status=LinkStatus.NEW, expires_at__gt=at).first()
    return (payments.link_url(link, lang) or "") if link is not None and payments.is_payable(link, at) else ""


PAGE_WORDS = {
    "ar": {
        "title": "المكالمة", "kind": "النوع", "kind_online": "كشف أونلاين", "join": "ادخل المكالمة",
        "open_p": "الدكتور في انتظارك. اضغط للدخول إلى المكالمة من الموبايل أو الكمبيوتر؛ هتفتح في صفحة جديدة.",
        "unconfirmed": "أكّد حضورك أولًا عشان يظهر زر المكالمة.", "no_link": "رابط المكالمة لسه ماتجهّزش. هيظهر هنا لما العيادة تجهّزه.",
        "unpaid": "لازم تدفع قيمة الكشف أولًا عشان تدخل المكالمة. تواصل مع العيادة.", "pay": "ادفع أونلاين",
        "early": "زر دخول المكالمة هيظهر يوم {day} الساعة {time} {suffix} (قبل الميعاد بـ {minutes} دقيقة).",
        "arrived": "ميعادك بدأ الآن.",
        # the staff's controls on the booking's page and the settings screen
        "my_title": "كشوفاتك الأونلاين النهارده", "box_title": "مكالمة الفيديو", "box_none": "لسه مفيش رابط مكالمة للكشف ده.", "generate": "اعمل رابط مكالمة", "regenerate": "اعمل رابط جديد",
        "paste": "حط رابط", "paste_label": "رابط المكالمة (https:// بس)", "remove": "شيل الرابط",
        "box_hint": "الزر ده بيفتح المكالمة في صفحة جديدة. الرابط مش بيتبعت في أي رسالة؛ المريض بيلاقي زر الدخول في صفحة تأكيد ميعاده، من 15 دقيقة قبل الميعاد لحد 3 ساعات بعد بدايته.",
        "prepaid_pending": "الكشف ده لسه مدفوعش مقدّمًا: زر المريض مش هيظهر لحد ما يتسجّل عليه عربون بسعر الكشف على الأقل.",
    },
    "en": {
        "title": "The call", "kind": "Type", "kind_online": "Online visit", "join": "Join the call",
        "open_p": "The doctor is waiting. Tap to join the call from your phone or computer; it opens in a new page.",
        "unconfirmed": "Confirm you are coming first, so the call button can show.", "no_link": "The call link is not ready yet. It will show here once the clinic has set it up.",
        "unpaid": "This visit must be paid before you join the call. Please contact the clinic.", "pay": "Pay online",
        "early": "The join button will show on {day} at {time} {suffix} ({minutes} minutes before the appointment).",
        "arrived": "Your appointment is in progress.",
        "my_title": "Your online visits today", "box_title": "Video call", "box_none": "This visit has no call link yet.", "generate": "Make a call link", "regenerate": "Make a new link",
        "paste": "Use this link", "paste_label": "Call link (https:// only)", "remove": "Remove the link",
        "box_hint": "This button opens the call in a new page. The link is never sent in a message; the patient sees their own button on their confirm page, from 15 minutes before the booking until 3 hours after it starts.",
        "prepaid_pending": "This visit is not paid in advance yet: the patient's button will not show until a deposit of at least the visit's price is taken on it.",
    },
}


#: The public booking page's words for the online choice (a patient reads them before anything is booked).
def staff_reminder(appointment, text, lang="ar"):
    """The booking page's «تذكير واتساب» for an online booking: the plain reminder, plus the patient's own page.

    The automatic reminder goes out the day before, so a visit booked for today never got the link of its confirm page,
    which is the only place the patient's join button lives. The message the staff send by hand therefore carries that
    page's link (the M3 signed link, never the call's own link); with no public address it says to contact the clinic.
    """

    from messaging.services import ONLINE_LINK_TEXTS, ONLINE_NOTES, ONLINE_NOTES_NOLINK
    from printing.share import public_url

    url = public_url("appointment", appointment.pk, lang, owner=appointment.customer_id)
    if not url:
        return f"{text} {ONLINE_NOTES_NOLINK[lang]}"
    return f"{text} {ONLINE_NOTES[lang]} {ONLINE_LINK_TEXTS[lang]} {url}"


PUBLIC_WORDS = {
    "ar": {"mode": "الكشف فين؟", "clinic": "في العيادة", "online": "أونلاين (مكالمة فيديو)", "k_kind": "النوع", "kind_online": "كشف أونلاين",
           "note": "الكشف الأونلاين بيتم بمكالمة فيديو. العيادة هتجهّز رابط المكالمة، وتدخلها من صفحة تأكيد الميعاد."},
    "en": {"mode": "Where is the visit?", "clinic": "At the clinic", "online": "Online (video call)", "k_kind": "Type", "kind_online": "Online visit",
           "note": "An online visit is a video call. The clinic will set up the call link, and you join it from the confirmation page of your appointment."},
}


def page_box(appointment, lang, at=None):
    """What the patient's confirm page shows about the call, or ``None`` for a booking that is not online. ``state`` is one of
    ``unconfirmed``, ``no_link``, ``unpaid``, ``early``, ``open``; the ``url`` is in the result **only** for ``open``."""

    from . import booking

    if appointment.kind != AppointmentKind.ONLINE:
        return None
    at = at or now()
    words = PAGE_WORDS[lang]
    opens, closes = window(appointment)
    box = {"state": "", "note": "", "url": "", "pay_url": "", "opens_at": opens}
    if appointment.status not in CONFIRMED:
        box.update(state="unconfirmed", note=words["unconfirmed"])
    elif not appointment.call_url:
        box.update(state="no_link", note=words["no_link"])
    elif prepaid_required() and not covered(appointment):
        box.update(state="unpaid", note=words["unpaid"], pay_url=pay_link(appointment, lang, at))
    elif at < opens:
        clock, suffix = booking.clock_label(opens, lang)
        box.update(state="early", note=words["early"].format(day=booking.day_label(timezone.localtime(opens).date(), lang), time=clock, suffix=suffix,
                                                             minutes=int(JOIN_OPENS_BEFORE.total_seconds() // 60)))
    elif at <= closes:
        box.update(state="open", url=appointment.call_url)
    return box if box["state"] else None
