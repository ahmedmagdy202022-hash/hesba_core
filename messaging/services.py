"""T9 (MARKET-PARITY): the day's messages to customers, in one outbox (HG-064).

Each day the outbox gathers what deserves a message from the screens that
already know it: customers with an amount past their credit days (the aging
report), instalments due in the next days or late, tomorrow's appointments,
and chronic prescriptions due for a refill. Nothing here changes a balance or
a booking. A message is sent from the outbox through WhatsApp (the link opens
the chat with the text written) or skipped; ``key`` and ``every_days`` keep a
customer from getting the same reminder twice. Sending by itself needs a
WhatsApp Business account, which is not connected yet (see ``api_connected``).
"""

import logging
import re
from datetime import timedelta
from urllib.parse import quote

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from entities.current import working_in
from permissions.services import user_has_permission

from .models import MessageKind, MessageTemplate, OutboxMessage, OutboxStatus

logger = logging.getLogger(__name__)

EDIT = "settings.manage_settings"
#: An instalment gets its message this many days before it falls due (and every day it is late, within ``every_days``).
INSTALMENT_AHEAD_DAYS = 2

DEFAULT_TEXTS = {
    MessageKind.DUE: "أهلاً {name}، ده تذكير ودّي من {company} بالمبلغ المستحق على حضرتك: {amount}. شكرًا لحضرتك.",
    MessageKind.INSTALMENT: "أهلاً {name}، تذكير من {company}: قسط فاتورة {number} بمبلغ {amount} ميعاده {date}. شكرًا لحضرتك.",
    MessageKind.APPOINTMENT: "أهلاً {name}، تذكير من {company} بميعاد حضرتك بكرة {date} الساعة {time}. ولأي تغيير في الميعاد، ابعتلنا رسالة.",
    MessageKind.REFILL: "أهلاً {name}، روشتة حضرتك رقم {number} ميعاد تجديدها {date}. تحب نجهّزها لحضرتك؟ — {company}",
    MessageKind.WORKORDER: "أهلاً {name}، من {company}: طلب الصيانة رقم {number}: {status} {link}",
    MessageKind.CAMPAIGN: "أهلاً {name}، وحشتنا في {company}! عندنا جديد يهمّ حضرتك، ومستنيينك قريب.",  # S8: only the starting text of a new campaign
    # M3: the clinic and the date, nothing else. Never the diagnosis, the specialty or any clinical detail (the placeholders cannot carry one).
    MessageKind.FOLLOWUP: "أهلاً {name}، تذكير من {company}: ميعاد المتابعة يوم {date}. تحب نحجزلك ميعاد؟ كلّمنا أو ابعتلنا هنا.",
    # M7: the owner, the animal, the vaccine, the date and the clinic. Never a note or a diagnosis (the placeholders cannot carry one) and no link.
    MessageKind.VACCINE: "أهلاً {name}، تذكير من {company}: ميعاد جرعة {vaccine} لـ {animal} يوم {date}. تحب نحجزلك ميعاد؟ كلّمنا أو ابعتلنا هنا.",
    # E5: the guardian, the student, the group, the day and the centre. Never a reason, a note, a rate, a grade or a link (students are often minors).
    MessageKind.ABSENCE: "أهلاً {name}، بنبلّغ حضرتك بغياب {student} عن {group} يوم {date}. — {company}",
    # E5: the guardian, the amount still due and the centre; one message per guardian, not per child or per invoice.
    MessageKind.FEES: "أهلاً {name}، تذكير ودّي من {company}: المتبقي من مصروفات الدراسة على حضرتك {amount}. ولو حضرتك دفعت بالفعل، تقدر تتجاهل الرسالة دي. شكرًا لحضرتك.",
}
#: S7: the same wording in English, used when the business's default language is English and the text was never edited.
DEFAULT_TEXTS_EN = {MessageKind.WORKORDER: "Hello {name}, from {company}: work order {number} {status} {link}",
                    MessageKind.CAMPAIGN: "Hello {name}, we miss you at {company}! We have something new for you; see you soon.",
                    MessageKind.FOLLOWUP: "Hello {name}, a reminder from {company}: your follow-up is due on {date}. Would you like us to book it? Contact us here.",
                    MessageKind.VACCINE: "Hello {name}, a reminder from {company}: {animal}'s {vaccine} dose is due on {date}. Would you like us to book it? Contact us here.",
                    MessageKind.ABSENCE: "Hello {name}, {student} was absent on {date} from {group} at {company}.",
                    MessageKind.FEES: "Hello {name}, a friendly reminder from {company}: the remaining tuition on your account is {amount}. If you have already paid, please disregard this message. Thank you."}
#: ``every_days`` is unused for work-order messages (one per order per status, whatever the day).
DEFAULT_EVERY = {MessageKind.DUE: 7, MessageKind.INSTALMENT: 3, MessageKind.APPOINTMENT: 1, MessageKind.REFILL: 3, MessageKind.WORKORDER: 1, MessageKind.CAMPAIGN: 1, MessageKind.FOLLOWUP: 7,
                 MessageKind.VACCINE: 7, MessageKind.ABSENCE: 1, MessageKind.FEES: 7}
#: M3: a kind that is off until the owner switches it on (a new kind has no saved row, and no row means "on" for the older kinds).
#: E5: the absence message and the fees reminder are off too: an absence message cannot be taken back once sent, and the fees reminder changes
#: who the general amount-due reminder reaches.
DEFAULT_OFF = (MessageKind.FOLLOWUP, MessageKind.VACCINE, MessageKind.ABSENCE, MessageKind.FEES)
#: M3: a clinic's appointment reminder carries the patient's own link; the sentence in front of it, per language.
APPOINTMENT_LINK_TEXTS = {"ar": "لتأكيد الحضور أو طلب تغيير الميعاد:", "en": "To confirm or ask to change the appointment:"}
APPOINTMENT_TEXT_EN = "Hello {name}, a reminder from {company} of your appointment tomorrow {date} at {time}."
#: M3: a follow-up is reminded this many days ahead of its date (and never again once it was sent or skipped).
FOLLOWUP_AHEAD_DAYS = 2
#: M7: a vaccination is reminded this many days ahead of its due date (and never again once it was sent or skipped). No lead-time column
#: exists on the template (adding one is a schema change), so it is a constant, as the follow-up's is.
VACCINE_AHEAD_DAYS = 3
PLACEHOLDERS = ("{name}", "{company}", "{amount}", "{date}", "{time}", "{number}", "{status}", "{link}", "{animal}", "{vaccine}", "{student}", "{group}")
#: E5: what a student's absence is messaged for: a mark of today or yesterday only, so switching the kind on never sends a term of old absences.
ABSENCE_DAYS = 2
#: S7: what each key status says to the customer ({days} is the order's warranty days).
WORKORDER_STATUSES = ("quoted", "ready", "delivered")
WORKORDER_STATUS_TEXTS = {
    "ar": {"quoted": "عرض السعر جاهز. تقدر توافق عليه أو ترفضه من الرابط ده:", "quoted_nolink": "عرض السعر جاهز. تواصل معانا للموافقة عليه.",
           "ready": "جاهز للاستلام. تابع حالته من هنا:", "ready_nolink": "جاهز للاستلام.",
           "delivered": "تم التسليم. شكرًا لثقة حضرتك.", "delivered_warranty": "تم التسليم. شكرًا لثقة حضرتك، والضمان {days} يوم."},
    "en": {"quoted": "the quote is ready. You can approve or refuse it from this link:", "quoted_nolink": "the quote is ready. Contact us to approve it.",
           "ready": "is ready for pickup. Follow it here:", "ready_nolink": "is ready for pickup.",
           "delivered": "was delivered. Thank you for your trust.", "delivered_warranty": "was delivered. Thank you for your trust; the warranty is {days} days."},
}

KIND_LABELS = {
    "ar": {"due": "مبلغ مستحق", "instalment": "قسط", "appointment": "ميعاد بكرة", "refill": "تجديد روشتة", "workorder": "حالة أمر الشغل", "campaign": "حملة", "followup": "متابعة", "vaccine": "تطعيم", "absence": "غياب طالب", "fees": "مصروفات مستحقة"},
    "en": {"due": "Amount due", "instalment": "Instalment", "appointment": "Appointment tomorrow", "refill": "Prescription refill", "workorder": "Work order status", "campaign": "Campaign", "followup": "Follow-up visit", "vaccine": "Vaccination due", "absence": "Student absence", "fees": "Fees due"},
}
#: E5: a line under the wording of a kind on the outbox page, for the kinds that need one.
KIND_HINTS = {
    "ar": {"absence": "بتتبعت لولي الأمر عن غياب النهارده أو إمبارح بس. لو الغياب اتصحّح لحاضر قبل ما الرسالة تتبعت، بتتشال لوحدها؛ لكن اللي اتبعتت خلاص مينفعش تترجّع.",
           "fees": "رسالة واحدة لكل ولي أمر بإجمالي اللي عليه بعد 3 أيام من الفاتورة، وبتتكرر كل 7 أيام لحد ما يدفع. وهي شغالة، تذكير «مبلغ مستحق» العام مبيبعتش لنفس ولي الأمر."},
    "en": {"absence": "Sent to the guardian for today's or yesterday's absence only. If the mark is corrected to present before the message goes out, it is withdrawn by itself; one already sent cannot be taken back.",
           "fees": "One message per guardian with the total still due, from 3 days after the invoice, repeated every 7 days until it is paid. While it is on, the general «Amount due» reminder does not message the same guardian."},
}
MESSAGES = {
    "ar": {"text": "نص الرسالة فاضي.", "placeholder": "النص فيه {bad} ومش معروف؛ المسموح: {allowed}.", "every": "التكرار من 1 لـ 90 يوم.",
           "done": "الرسالة دي اتبعتت أو اتشالت قبل كده.", "later": "الرسالة دي معادها يوم {day}؛ مينفعش تتبعت قبله.", "wa_template": "اسم قالب واتساب: حروف إنجليزي صغيرة وأرقام و _ بس، زي ما اتسجل عند Meta."},
    "en": {"text": "The message is empty.", "placeholder": "The text has {bad}, which is unknown; allowed: {allowed}.", "every": "Repeat after 1 to 90 days.",
           "done": "This message was already sent or skipped.", "later": "This message is for {day}; it cannot be sent before then.", "wa_template": "The WhatsApp template name: lower-case letters, digits and _ only, as registered with Meta."},
}


# ---- who may do what ----

def can_view(user):
    from reports.navigation import sees_customers

    return sees_customers(user)


def can_edit(user):
    return user_has_permission(user, EDIT)


def api_connected():
    """Whether the shop's WhatsApp Business account is set up to send the messages by itself (T9 live part)."""

    from . import whatsapp

    return not whatsapp.missing()


# ---- templates ----

def template(kind):
    row = MessageTemplate.objects.filter(kind=kind).first()
    return row or MessageTemplate(kind=kind, text=DEFAULT_TEXTS[kind], every_days=DEFAULT_EVERY[kind], active=kind not in DEFAULT_OFF)


def templates():
    """The wording the outbox lets an owner edit. A campaign's words belong to the campaign itself (S8), so its kind is
    not listed here; its on/off switch and WhatsApp template are on the campaigns screen. The follow-up reminder (M3) is a
    clinic's, so it is listed only when some entity is a medical business; the vaccination reminder (M7) only when one is a vet's; the
    absence message and the fees reminder (E5) only when one is a school or a centre."""

    education = set() if _education_entities() else {MessageKind.ABSENCE, MessageKind.FEES}
    hidden = {MessageKind.CAMPAIGN} | (set() if _medical_entities() else {MessageKind.FOLLOWUP}) | (set() if _vet_entities() else {MessageKind.VACCINE}) | education
    return [template(kind) for kind in MessageKind.values if kind not in hidden]


@transaction.atomic
def save_template(kind, data, user, lang="ar"):
    words = MESSAGES[lang]
    if not can_edit(user):
        raise PermissionDenied("Message templates need settings.manage_settings.")
    text = (data.get("text") or "").strip()[:1000]
    if not text:
        raise ValidationError(words["text"])
    bad = [found for found in re.findall(r"\{[^}]*\}", text) if found not in PLACEHOLDERS]
    if bad:
        raise ValidationError(words["placeholder"].format(bad=" ".join(bad), allowed=" ".join(PLACEHOLDERS)))
    try:
        every = int(str(data.get("every_days") or DEFAULT_EVERY[kind]).strip())
    except ValueError:
        raise ValidationError(words["every"])
    if not 1 <= every <= 90:
        raise ValidationError(words["every"])
    wa_template = (data.get("wa_template") or "").strip()
    if wa_template and not re.fullmatch(r"[a-z0-9_]{1,120}", wa_template):  # Meta's template names: lower case, digits, _
        raise ValidationError(words["wa_template"])
    row, _created = MessageTemplate.objects.update_or_create(kind=kind, defaults={
        "text": text, "active": data.get("active") in (True, "1", "on"), "every_days": every, "wa_template": wa_template, "updated_by": user})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="messaging", action="save_template", object_type="messaging.MessageTemplate",
                            object_id=str(row.pk), before_data={}, after_data={"kind": kind, "active": row.active, "every_days": every})
    return row


def render(text, **values):
    for key in ("name", "company", "amount", "date", "time", "number", "status", "link", "animal", "vaccine", "student", "group"):
        text = text.replace("{" + key + "}", str(values.get(key, "")))
    return text


# ---- what deserves a message today ----

def _company():
    from printing.company import company_details

    details = company_details()
    return details["name"], details.get("currency", "")


def _number(phone):
    from reports.aging import whatsapp_number

    return whatsapp_number(phone)


def _due(today, company, currency):
    from settings_core.setup_services import module_is_enabled

    if not module_is_enabled("customers"):
        return
    from reports.aging import aging_rows

    covered = _fee_covered(today)  # E5: a guardian the fees reminder covers is not also sent the general one for the same money
    for row in aging_rows("customers", today):
        if row["overdue"] > 0 and row["whatsapp"] and row["party"].pk not in covered:
            yield {"key": f"due:customer:{row['party'].pk}", "name": row["name"], "phone": row["whatsapp"], "link": f"/parties/customer/{row['party'].pk}/",
                   "values": {"amount": f"{row['overdue']:,.2f} {currency}".strip()}}


def _fee_covered(today):
    """E5: the customers the fees reminder covers in the entity being gathered, so the general amount-due reminder skips them.

    Empty unless the fees kind is on and the entity is a school or a centre: any other business never reaches education code,
    and its amount-due reminder is exactly what it was."""

    if not template(MessageKind.FEES).active:
        return set()
    from education.services import is_education_install

    if not is_education_install():
        return set()
    from education import messages as education_messages

    return {payer for payer, _total in education_messages.fee_payers(today)}


def _instalments(today, company, currency):
    from installments.models import InstalmentPlan, PlanStatus
    from installments.services import installments_enabled, schedule

    if not installments_enabled():
        return
    horizon = today + timedelta(days=INSTALMENT_AHEAD_DAYS)
    plans = (entity_scope.scope(InstalmentPlan.objects, "invoice__" + entity_scope.SALES_INVOICE)
             .filter(status=PlanStatus.ACTIVE, invoice__status="posted", instalments__due_date__lte=horizon).distinct()
             .select_related("invoice", "customer"))
    for plan in plans:
        phone = plan.customer.whatsapp or plan.customer.phone
        if not phone:
            continue
        rows = [row for row in schedule(plan, today)["rows"] if row["open"] > 0 and row["instalment"].due_date <= horizon]
        if rows:
            row = rows[0]
            yield {"key": f"instalment:{plan.pk}", "name": plan.customer.name, "phone": phone, "link": f"/instalments/{plan.pk}/",
                   "values": {"amount": f"{row['open']:,.2f} {currency}".strip(), "date": row["instalment"].due_date.isoformat(),
                              "number": plan.invoice.invoice_number}}


def _appointments(today, company, currency):
    from settings_core.setup_services import module_is_enabled

    if not module_is_enabled("appointments_visits"):
        return
    from appointments.models import AppointmentStatus
    from appointments.services import day_agenda

    from medical.services import is_medical_install

    tomorrow = today + timedelta(days=1)
    medical = is_medical_install()  # M3: only a clinic's reminder carries the patient's own confirm link
    lang = workorder_language()
    for appointment in day_agenda(tomorrow).filter(status__in=(AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED)):
        phone = appointment.customer.whatsapp or appointment.customer.phone
        if not phone:
            continue
        found = {"key": f"appointment:{appointment.pk}", "name": appointment.customer.name, "phone": phone, "link": f"/appointments/{appointment.pk}/",
                 "values": {"date": tomorrow.isoformat(), "time": timezone.localtime(appointment.starts_at).strftime("%H:%M")}}
        if medical:
            from printing.share import public_url

            found["localised"] = True
            found["url"] = public_url("appointment", appointment.pk, lang, owner=appointment.customer_id) or ""  # no PUBLIC_BASE_URL: no link, the plain reminder
        yield found


def _refills(today, company, currency):
    from pharmacy import services as pharmacy

    if not pharmacy.enabled():
        return
    for prescription in pharmacy.due(pharmacy.scoped(), today):
        if not prescription.patient_phone:
            continue
        next_date = pharmacy.next_refill(prescription)
        yield {"key": f"refill:{prescription.pk}", "name": prescription.patient_name, "phone": prescription.patient_phone,
               "link": f"/pharmacy/prescriptions/{prescription.pk}/",
               "values": {"number": prescription.number, "date": next_date.isoformat() if next_date else ""}}


def _medical_entities():
    """The active entities that are medical businesses, main first (an installation with no second entity: its one entity)."""

    from entities.services import activity_of

    return [entity for entity in _entities() if activity_of(entity)[0] == "medical"]


def _vet_entities():
    """The active entities that are vet clinics (M7), main first."""

    from entities.services import activity_of

    return [entity for entity in _entities() if activity_of(entity) == ("medical", "vet")]


def _owner_entity(visit, medical, main):
    """M3: the entity a follow-up is gathered in. Visits carry no entity (patients are group-wide, HG-084), so it is the
    entity of the visit's own appointment (none: the main entity, HG-034) when that is a medical one, else the first medical entity.
    One owner means a patient in a group of two clinics is reminded once, not once per entity."""

    appointment = visit.appointment
    chosen = appointment.entity if appointment is not None and appointment.entity_id else main
    return chosen if any(chosen.pk == entity.pk for entity in medical) else medical[0]


def _followups(today, company, currency):
    """M3: patients whose latest visit asked for a follow-up in the next ``FOLLOWUP_AHEAD_DAYS`` days and who have nothing
    booked (in any entity). The message names the clinic and the date, never anything clinical; each follow-up is reminded
    once (``key`` holds the visit and the date, and a sent or skipped one is never queued again, whatever the day)."""

    from entities.services import main_entity, is_multi_entity
    from medical import insights
    from medical.services import is_medical_install

    current = entity_scope.entity()
    if current is None or not is_medical_install():
        return
    from appointments.models import Appointment, AppointmentStatus

    start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    booked = set(Appointment.objects.filter(starts_at__gte=start, status__in=[AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED])
                 .values_list("customer_id", flat=True))  # group-wide: a patient booked in another entity is not nagged
    medical, main = _medical_entities(), main_entity()
    closed = {key.split("@")[0] for key in OutboxMessage.objects.filter(kind=MessageKind.FOLLOWUP, status__in=(OutboxStatus.SENT, OutboxStatus.SKIPPED))
              .values_list("key", flat=True)}
    clinic = current.display(workorder_language()) if is_multi_entity() else ""
    horizon = today + timedelta(days=FOLLOWUP_AHEAD_DAYS)
    for row in insights.coming_follow_ups(today, booked=booked):
        if row["due"] > horizon:
            continue
        visit = row["visit"]
        if _owner_entity(visit, medical, main).pk != current.pk:
            continue
        key = f"followup:{visit.pk}:{row['due'].isoformat()}"
        patient = visit.patient
        phone = patient.whatsapp or patient.phone
        if key in closed or not phone:
            continue
        yield {"key": key, "name": patient.name, "phone": phone, "link": f"/appointments/?day={row['due'].isoformat()}", "localised": True,
               "values": {"date": row["due"].isoformat(), **({"company": clinic} if clinic else {})}}


def _vaccines(today, company, currency):
    """M7: animals whose last dose of a vaccine is due in the next ``VACCINE_AHEAD_DAYS`` days (today included), in the entity being
    gathered, one message per dose (``key`` holds the dose; a sent or skipped one is never queued again, whatever the day). The text
    names the owner, the animal, the vaccine, the date and the clinic: never the dose's note, a diagnosis or a link. A dose recorded
    before the message goes out closes this one (``open_doses`` stops yielding it), and ``_gather`` withdraws the waiting message.
    A dose already overdue is not messaged (turning the kind on must not send a year of old reminders); it stays on the due list."""

    from entities.services import is_multi_entity
    from medical import vaccines, vet
    from settings_core.templatetags.hesba_format import day_text

    current = entity_scope.entity()
    if current is None or not vet.is_vet():
        return
    lang = workorder_language()
    closed = {key.split("@")[0] for key in OutboxMessage.objects.filter(kind=MessageKind.VACCINE, status__in=(OutboxStatus.SENT, OutboxStatus.SKIPPED))
              .values_list("key", flat=True)}
    clinic = current.display(lang) if is_multi_entity() else ""
    doses = (vaccines.open_doses(vaccines.scoped_vaccinations())
             .filter(next_due__gte=today, next_due__lte=today + timedelta(days=VACCINE_AHEAD_DAYS), animal__active=True, animal__owner__active=True)
             .exclude(animal__owner__customer_code="WALK-IN").select_related("animal__owner", "vaccine").order_by("next_due", "pk"))
    for dose in doses:
        key = f"vaccine:{dose.pk}"
        owner = dose.animal.owner
        phone = owner.whatsapp or owner.phone
        if key in closed or not phone:
            continue
        yield {"key": key, "name": owner.name, "phone": phone, "link": f"/appointments/?day={dose.next_due.isoformat()}", "localised": True,
               "values": {"date": day_text(dose.next_due, lang), "animal": dose.animal.name, "vaccine": dose.vaccine.display(lang), **({"company": clinic} if clinic else {})}}


def _education_entities():
    """The active entities that are schools or centres (E5), main first."""

    from entities.services import activity_of

    return [entity for entity in _entities() if activity_of(entity)[0] == "education"]


def _absences(today, company, currency):
    """E5: a student's absence, messaged to the guardian. The source is ``education.messages`` (imported here, not at the top: that
    module reads this one)."""

    from education import messages as education_messages

    yield from education_messages.absences(today, company, currency)


def _fees(today, company, currency):
    """E5: the fees still due, one message per guardian. The source is ``education.messages``."""

    from education import messages as education_messages

    yield from education_messages.fees(today, company, currency)


SOURCES = {MessageKind.DUE: _due, MessageKind.INSTALMENT: _instalments, MessageKind.APPOINTMENT: _appointments, MessageKind.REFILL: _refills,
           MessageKind.FOLLOWUP: _followups, MessageKind.VACCINE: _vaccines, MessageKind.ABSENCE: _absences, MessageKind.FEES: _fees}
#: Kinds whose records carry no entity; gathered for the main entity only. Empty since HG-074 gave appointments
#: their entity: each entity's appointments are gathered as its own, and the main entity's keys are unchanged.
UNSCOPED = set()


def _entities():
    """Each entity is gathered on its own, so a message belongs to one entity and the amount is that entity's.

    The main entity is always one of them, also before a second entity exists, so its reminders keep the same key
    and the same ``entity`` when the group grows (records without an entity belong to it, HG-034)."""

    from entities.models import Entity
    from entities.services import main_entity

    main = main_entity()
    return [main] + list(Entity.objects.filter(active=True).exclude(pk=main.pk).order_by("code"))


def build(today=None):
    """Queue today's messages. Running it again the same day adds only what is new. A customer sent a reminder within
    the kind's ``every_days`` is not reminded again; a reminder still waiting from an earlier day moves to today with
    today's figures instead of being queued twice. Returns how many messages were added."""

    today = today or timezone.localdate()
    company, currency = _company()
    added = 0
    _carry_workorders(today)
    from . import campaigns

    campaigns.carry(today)
    for entity in _entities():
        with working_in(entity):
            for kind, source in SOURCES.items():
                if not entity.is_main and kind in UNSCOPED:
                    continue
                row = template(kind)
                if row.active:
                    added += _gather(kind, source, row, entity, today, company, currency)
                else:
                    _waiting(kind, entity).delete()  # switched off: nothing of this kind is left to send
    return added


def _carry_workorders(today):
    """S7: a work-order message still waiting is today's (the outbox and the API send only today's), goes to the
    customer's current number, and is rebuilt when it moves to a new day. It is withdrawn (kept, never sendable, so
    the order does not get a second message for the same status) when its order is no longer in the status it
    announces, the customer has no valid number left, or the kind is off."""

    from workorders.models import WorkOrder

    waiting = OutboxMessage.objects.filter(kind=MessageKind.WORKORDER, status=OutboxStatus.PENDING)
    row = template(MessageKind.WORKORDER)
    if not row.active:
        _withdraw(waiting)  # switched off: nothing of this kind is left to send
        return
    for message in list(waiting):
        parts = message.key.split(":")
        if len(parts) != 3 or not parts[1].isdecimal() or parts[2] not in WORKORDER_STATUSES:
            message.delete()  # not a key this module wrote: never let it break the outbox page
            continue
        order = WorkOrder.objects.select_related("customer").filter(pk=int(parts[1]), status=parts[2]).first()
        phone = _number(order.customer.whatsapp or order.customer.phone) if order else ""
        if not phone:
            _withdraw(OutboxMessage.objects.filter(pk=message.pk))  # moved on, or nobody to send it to
            continue
        fields = {"phone": phone[:30], "name": order.customer.name[:255]}
        if message.day != today:
            # Carried to today: the text is rebuilt from the order as it is now, with a fresh link (share links expire).
            fields["day"] = today
            try:
                from workorders.services import status_link

                lang = workorder_language()
                fields["text"] = _workorder_text(order, parts[2], status_link(order, parts[2], lang), lang, row)
            except Exception:  # noqa: BLE001 - the old text still goes rather than the outbox page failing
                logger.exception("S7: could not rebuild the %s message of work order %s.", parts[2], parts[1])
        OutboxMessage.objects.filter(pk=message.pk, status=OutboxStatus.PENDING).update(**fields)


def _withdraw(messages):
    """Make waiting work-order messages unsendable but keep the rows (the once-per-status record); the text, which may
    hold the customer's link, is cleared."""

    messages.filter(status=OutboxStatus.PENDING).update(status=OutboxStatus.WITHDRAWN, text="")


def workorder_language():
    """The language of what the customer is sent: the business's default language (Arabic unless it is English)."""

    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return "en" if profile and profile.default_language == "en" else "ar"


def _workorder_text(order, status, link, lang, row):
    """The words of one work-order message. An edited template is used as saved; the untouched Arabic default is
    swapped for the English default when the business works in English."""

    words = WORKORDER_STATUS_TEXTS[lang]
    link = "" if status == "delivered" else (link or "")
    if status == "delivered":
        sentence = words["delivered_warranty"].format(days=order.warranty_days) if order.warranty_days else words["delivered"]
    else:
        sentence = words[status if link else f"{status}_nolink"]
    text = row.text if row.pk else DEFAULT_TEXTS[MessageKind.WORKORDER]
    if lang == "en" and text == DEFAULT_TEXTS[MessageKind.WORKORDER]:
        text = DEFAULT_TEXTS_EN[MessageKind.WORKORDER]
    company, _currency = _company()
    return render(text, name=order.customer.name, company=company, number=order.number, status=sentence, link=link).strip()


def queue_workorder(order, status, link="", lang=None):
    """S7: queue the message for a work order that reached ``status`` (quoted, ready or delivered). Returns the
    message, or None when nothing is queued (kind switched off, no phone, or this status already had its message).

    One message per order per status, whatever the day, also if it was sent or skipped. ``link`` is the S2 customer
    link (built by the caller from PUBLIC_BASE_URL; empty when it cannot be); DELIVERED never carries one.
    Nothing here touches money, stock or a ledger."""

    from entities.services import main_entity

    status = str(status)
    if status not in WORKORDER_STATUSES:
        return None
    row = template(MessageKind.WORKORDER)
    if not row.active:
        return None
    customer = order.customer
    phone = _number(customer.whatsapp or customer.phone)
    if not phone:
        return None
    key = f"workorder:{order.pk}:{status}"
    if OutboxMessage.objects.filter(kind=MessageKind.WORKORDER, key=key).exists():
        return None
    fields = {"name": customer.name[:255], "phone": phone[:30], "link": f"/workorders/{order.pk}/", "entity": order.entity or main_entity(),
              "text": _workorder_text(order, status, link, lang or workorder_language(), row)}
    try:
        with transaction.atomic():
            return OutboxMessage.objects.create(kind=MessageKind.WORKORDER, key=key, day=timezone.localdate(), **fields)
    except IntegrityError:
        return None  # queued by another request a moment ago


def _link_kept(text, url):
    """The link a waiting message already carries, when it opens the same thing as ``url``; otherwise ``url``.

    A share token is signed with the second it was made in, so every run would mint a different link and rewrite the
    waiting row (the outbox page runs the gathering each time it opens). Within a day the message keeps its link; when it
    is carried to a new day it takes a fresh one, since share links expire."""

    from printing.share import read_token_data as read_token  # the whole content: the object and the person it is bound to

    head, mark, tail = url.partition("/share/")
    token, slash, rest = tail.partition("/")
    wanted = read_token(token) if mark and slash else None
    if wanted is None:
        return url
    for word in text.split():
        if word.startswith(head + mark) and word.endswith(slash + rest) and read_token(word[len(head + mark):len(word) - len(slash + rest)]) == wanted:
            return word
    return url


def _gather(kind, source, row, entity, today, company, currency):
    since = today - timedelta(days=row.every_days - 1)
    done = OutboxMessage.objects.filter(kind=kind, day__gte=since).filter(
        Q(status=OutboxStatus.SENT) | Q(status=OutboxStatus.SKIPPED, day=today))
    recent = set(done.values_list("key", flat=True))
    waiting = {message.key: message for message in _waiting(kind, entity)}
    seen, added = set(), 0
    lang = workorder_language()
    for candidate in source(today, company, currency):
        key = candidate["key"] if entity.is_main else f"{candidate['key']}@{entity.pk}"
        phone = _number(candidate["phone"])
        if key in recent or not phone:
            continue
        seen.add(key)
        base = row.text
        if lang == "en" and candidate.get("localised") and base == DEFAULT_TEXTS[kind]:
            base = DEFAULT_TEXTS_EN.get(kind) or (APPOINTMENT_TEXT_EN if kind == MessageKind.APPOINTMENT else base)
        earlier = waiting.get(key)
        url = candidate.get("url", "")
        if url and earlier is not None and earlier.day == today:
            url = _link_kept(earlier.text, url)  # the same link all day, not a new one every time the outbox opens
        values = {"company": company, **candidate["values"]}
        if url:
            values.setdefault("link", url)  # a template that places {link} itself
        text = render(base, name=candidate["name"], **values)
        if url and "{link}" not in base:
            text = f"{text} {APPOINTMENT_LINK_TEXTS[lang]} {url}"
        fields = {"name": candidate["name"][:255], "phone": phone[:30], "link": candidate["link"][:255], "entity": entity, "text": text}
        if earlier is not None:
            # Still waiting: it carries today's date and today's figures (a part payment lowers the amount).
            OutboxMessage.objects.filter(pk=earlier.pk, status=OutboxStatus.PENDING).update(day=today, **fields)
            recent.add(key)
            continue
        try:
            with transaction.atomic():
                OutboxMessage.objects.create(kind=kind, key=key, day=today, **fields)
        except IntegrityError:
            continue  # queued by another request a moment ago
        recent.add(key)
        added += 1
    # Paid in full, cancelled or moved since it was queued: the message is withdrawn before anyone sends it.
    _waiting(kind, entity).exclude(key__in=seen).delete()
    return added


def _waiting(kind, entity):
    """The kind's messages still waiting in ``entity``; the main entity's include any queued without one."""

    rows = OutboxMessage.objects.filter(kind=kind, status=OutboxStatus.PENDING)
    return rows.filter(Q(entity=entity) | Q(entity__isnull=True)) if entity.is_main else rows.filter(entity=entity)


def visible(user):
    """The messages this user may see and send: the entity being worked in, never another (HG-034)."""

    return entity_scope.for_user(entity_scope.scope(OutboxMessage.objects.all(), "entity"), "entity", user)


# ---- the customer's link in a message (S7) ----

LINK = re.compile(r"https?://\S+")
LINK_MARKS = ("http://", "https://")
LINK_MASK = {"ar": "[رابط العميل]", "en": "[customer link]"}


#: The kinds whose text can carry a customer's own link (S7 work orders, M3 a clinic's appointment).
LINK_KINDS = (MessageKind.WORKORDER, MessageKind.APPOINTMENT)
#: M3: the people who may handle a patient's appointment link: those who book and change appointments.
APPOINTMENT_LINK_PERMISSION = "sales.create_sales_invoice"


def may_hold_link(user, kind=MessageKind.WORKORDER):
    """Whoever holds a work-order message with the S2 link can approve the quote as the customer, so only the people
    who may get the link by hand (``workorders.services.customer_link``) see or send it. An appointment link (M3) lets its
    holder confirm or ask to cancel as the patient, so it is held by those who book appointments (reception)."""

    if kind == MessageKind.APPOINTMENT:
        return user_has_permission(user, APPOINTMENT_LINK_PERMISSION)
    from workorders.services import can_hold_customer_link

    return can_hold_customer_link(user)


def holds_link(message):
    return message.kind in LINK_KINDS and any(mark in message.text for mark in LINK_MARKS)


def shown_text(message, user, lang="ar"):
    """The text this user sees: the customer link is replaced by a placeholder unless they may hold it."""

    if holds_link(message) and not may_hold_link(user, message.kind):
        return LINK.sub(LINK_MASK[lang], message.text)
    return message.text


def sendable(user, messages):
    """``messages`` without the link-bearing ones (work orders, clinic appointments) of a kind ``user`` may not hold the link of."""

    for kind in LINK_KINDS:
        if not may_hold_link(user, kind):
            messages = messages.exclude(Q(text__contains=LINK_MARKS[0]) | Q(text__contains=LINK_MARKS[1]), kind=kind)
    return messages


# ---- sending ----

def whatsapp_url(message):
    return f"https://wa.me/{message.phone}?text={quote(message.text)}"


@transaction.atomic
def mark(message, status, user, lang="ar"):
    """Sent (the person opened the chat with the text) or skipped. Each message moves once."""

    if not can_view(user):
        raise PermissionDenied("The outbox needs a customer-facing permission.")
    message = visible(user).select_for_update().filter(pk=message.pk).first()
    if message is None:
        raise PermissionDenied("This message belongs to another entity.")
    if message.status != OutboxStatus.PENDING:
        raise ValidationError(MESSAGES[lang]["done"])
    if status == OutboxStatus.SENT and message.kind == MessageKind.CAMPAIGN and message.day > timezone.localdate():
        # S8: a campaign message deferred by the daily cap (or moved forward by build() since the page opened) waits for its day
        raise ValidationError(MESSAGES[lang]["later"].format(day=message.day.isoformat()))
    if status == OutboxStatus.SENT and holds_link(message) and not may_hold_link(user, message.kind):
        raise PermissionDenied("This message carries the customer's link; only counter staff may send it.")
    message.status = status
    message.channel = "link" if status == OutboxStatus.SENT else ""
    message.sent_at, message.sent_by = timezone.now(), user
    message.save(update_fields=["status", "channel", "sent_at", "sent_by"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="messaging", action=f"message_{status}", object_type="messaging.OutboxMessage",
                            object_id=str(message.pk), before_data={}, after_data={"kind": message.kind, "key": message.key, "day": str(message.day)})
    return message


def send_by_api(user=None, messages=None, limit=None, seconds=None):
    """T9 live: send today's waiting messages whose kind has an approved template, through the shop's WhatsApp
    Business account. Returns {"sent", "failed", "left"}. A refused message stays waiting with the reason, to go by a
    click or on the next run. ``messages`` narrows it (the outbox sends what its user may see).

    Codex #282: ``limit`` (messages) and ``seconds`` bound one run, so a click on the outbox ends well inside the
    web server's timeout; ``left`` is what that run did not reach, for the next click or the scheduled command."""

    import time

    from django.db.models import Case, IntegerField, Value, When

    from . import whatsapp

    if whatsapp.missing():
        return {"sent": 0, "failed": 0, "left": 0}
    named = {row.kind: row.wa_template for row in MessageTemplate.objects.filter(active=True).exclude(wa_template="")}
    if not named:
        return {"sent": 0, "failed": 0, "left": 0}
    company, _currency = _company()
    rows = (messages if messages is not None else OutboxMessage.objects.all()).filter(
        status=OutboxStatus.PENDING, day=timezone.localdate(), kind__in=list(named))
    # Every eligible message is tried once per run (Codex #282): the ids are read up front, in order, so a run of
    # failures never keeps the later ones from their turn. Those never tried come before those refused already.
    tried_first = Case(When(error="", then=Value(0)), default=Value(1), output_field=IntegerField())
    ids = list(rows.order_by(tried_first, "pk").values_list("pk", flat=True))
    started, sent, failed, done = time.monotonic(), 0, 0, 0
    for pk in ids:
        if (limit is not None and done >= limit) or (seconds is not None and time.monotonic() - started >= seconds):
            break
        done += 1
        with transaction.atomic():
            message = OutboxMessage.objects.select_for_update().filter(pk=pk, status=OutboxStatus.PENDING).first()
            if message is None:
                continue  # sent or skipped a moment ago
            provider_id, error = whatsapp.send(message.phone, named[message.kind], company, message.text)
            if not provider_id:
                message.error = error
                message.save(update_fields=["error"])
                failed += 1
                continue
            message.status, message.channel, message.provider_id, message.error = OutboxStatus.SENT, "api", provider_id, ""
            message.sent_at, message.sent_by = timezone.now(), user
            message.save(update_fields=["status", "channel", "provider_id", "error", "sent_at", "sent_by"])
            AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="messaging", action="message_sent_api",
                                    object_type="messaging.OutboxMessage", object_id=str(message.pk), before_data={},
                                    after_data={"kind": message.kind, "key": message.key, "day": str(message.day), "provider_id": provider_id})
            sent += 1
    return {"sent": sent, "failed": failed, "left": len(ids) - done}


#: M3: the hours (Cairo) the scheduled run may send in, so a reminder never reaches a patient at night.
SEND_FROM_HOUR, SEND_UNTIL_HOUR = 8, 21
#: One scheduled run stops sending after this long, well inside the web server's timeout; the next hourly run continues.
SCHEDULED_SECONDS = 40


def run_scheduled(now=None):
    """M3: what an external scheduler does every hour: gather the day's messages, then send those with an approved
    template through the shop's WhatsApp Business account, inside the sending hours. Returns {"status", "added", "sent",
    "failed", "left"}; ``status`` is ``gathered`` (no WhatsApp keys: the messages wait in the outbox for a click),
    ``outside_hours`` or ``sent``."""

    now = timezone.localtime(now or timezone.now())
    added = build()
    result = {"status": "gathered", "added": added, "sent": 0, "failed": 0, "left": 0}
    if not api_connected():
        return result
    if not SEND_FROM_HOUR <= now.hour < SEND_UNTIL_HOUR:
        return {**result, "status": "outside_hours"}
    sent = send_by_api(seconds=SCHEDULED_SECONDS)
    return {**result, **sent, "status": "sent"}


def counts(user, today=None):
    today = today or timezone.localdate()
    rows = visible(user).filter(day=today)
    return {status: rows.filter(status=status).count() for status in OutboxStatus.values if status != OutboxStatus.WITHDRAWN}
