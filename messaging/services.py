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

EDIT = "settings.manage_settings"
#: An instalment gets its message this many days before it falls due (and every day it is late, within ``every_days``).
INSTALMENT_AHEAD_DAYS = 2

DEFAULT_TEXTS = {
    MessageKind.DUE: "أهلاً {name}، ده تذكير ودّي من {company} بالمبلغ المستحق على حضرتك: {amount}. شكرًا لحضرتك.",
    MessageKind.INSTALMENT: "أهلاً {name}، تذكير من {company}: القسط بتاع فاتورة {number} بمبلغ {amount} ميعاده {date}. شكرًا لحضرتك.",
    MessageKind.APPOINTMENT: "أهلاً {name}، تذكير من {company} بميعاد حضرتك بكرة {date} الساعة {time}. لو محتاج تغيّر الميعاد ابعتلنا.",
    MessageKind.REFILL: "أهلاً {name}، روشتة حضرتك {number} ميعاد تجديدها {date}. تحب نجهزهالك؟ — {company}",
}
DEFAULT_EVERY = {MessageKind.DUE: 7, MessageKind.INSTALMENT: 3, MessageKind.APPOINTMENT: 1, MessageKind.REFILL: 3}
PLACEHOLDERS = ("{name}", "{company}", "{amount}", "{date}", "{time}", "{number}")

KIND_LABELS = {
    "ar": {"due": "مبلغ مستحق", "instalment": "قسط", "appointment": "ميعاد بكرة", "refill": "تجديد روشتة"},
    "en": {"due": "Amount due", "instalment": "Instalment", "appointment": "Appointment tomorrow", "refill": "Prescription refill"},
}
MESSAGES = {
    "ar": {"text": "نص الرسالة فاضي.", "placeholder": "النص فيه {bad} ومش معروف؛ المسموح: {allowed}.", "every": "التكرار من 1 لـ 90 يوم.",
           "done": "الرسالة دي اتبعتت أو اتشالت قبل كده."},
    "en": {"text": "The message is empty.", "placeholder": "The text has {bad}, which is unknown; allowed: {allowed}.", "every": "Repeat after 1 to 90 days.",
           "done": "This message was already sent or skipped."},
}


# ---- who may do what ----

def can_view(user):
    from reports.navigation import sees_customers

    return sees_customers(user)


def can_edit(user):
    return user_has_permission(user, EDIT)


def api_connected():
    """Whether a WhatsApp Business account sends the messages by itself. Not yet: it needs the shop's own Meta account (T9 live part)."""

    return False


# ---- templates ----

def template(kind):
    row = MessageTemplate.objects.filter(kind=kind).first()
    return row or MessageTemplate(kind=kind, text=DEFAULT_TEXTS[kind], every_days=DEFAULT_EVERY[kind])


def templates():
    return [template(kind) for kind in MessageKind.values]


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
    row, _created = MessageTemplate.objects.update_or_create(kind=kind, defaults={
        "text": text, "active": data.get("active") in (True, "1", "on"), "every_days": every, "updated_by": user})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="messaging", action="save_template", object_type="messaging.MessageTemplate",
                            object_id=str(row.pk), before_data={}, after_data={"kind": kind, "active": row.active, "every_days": every})
    return row


def render(text, **values):
    for key in ("name", "company", "amount", "date", "time", "number"):
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

    for row in aging_rows("customers", today):
        if row["overdue"] > 0 and row["whatsapp"]:
            yield {"key": f"due:customer:{row['party'].pk}", "name": row["name"], "phone": row["whatsapp"], "link": f"/parties/customer/{row['party'].pk}/",
                   "values": {"amount": f"{row['overdue']:,.2f} {currency}".strip()}}


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

    tomorrow = today + timedelta(days=1)
    for appointment in day_agenda(tomorrow).filter(status__in=(AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED)):
        phone = appointment.customer.whatsapp or appointment.customer.phone
        if not phone:
            continue
        yield {"key": f"appointment:{appointment.pk}", "name": appointment.customer.name, "phone": phone, "link": f"/appointments/{appointment.pk}/",
               "values": {"date": tomorrow.isoformat(), "time": timezone.localtime(appointment.starts_at).strftime("%H:%M")}}


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


SOURCES = {MessageKind.DUE: _due, MessageKind.INSTALMENT: _instalments, MessageKind.APPOINTMENT: _appointments, MessageKind.REFILL: _refills}
#: Appointments carry no entity of their own; like every record without one, they belong to the main entity (HG-034).
UNSCOPED = {MessageKind.APPOINTMENT}


def _entities():
    """Each entity is gathered on its own, so a message belongs to one entity and the amount is that entity's."""

    from entities.models import Entity
    from entities.services import is_multi_entity

    if not is_multi_entity():
        return [None]
    return list(Entity.objects.filter(active=True).order_by("-is_main", "code"))


def build(today=None):
    """Queue today's messages. Running it again the same day adds only what is new. A customer sent a reminder within
    the kind's ``every_days`` is not reminded again; a reminder still waiting from an earlier day moves to today with
    today's figures instead of being queued twice. Returns how many messages were added."""

    today = today or timezone.localdate()
    company, currency = _company()
    added = 0
    for entity in _entities():
        with working_in(entity):
            for kind, source in SOURCES.items():
                if entity is not None and not entity.is_main and kind in UNSCOPED:
                    continue
                row = template(kind)
                if row.active:
                    added += _gather(kind, source, row, entity, today, company, currency)
                else:
                    _waiting(kind, entity).delete()  # switched off: nothing of this kind is left to send
    return added


def _gather(kind, source, row, entity, today, company, currency):
    since = today - timedelta(days=row.every_days - 1)
    done = OutboxMessage.objects.filter(kind=kind, day__gte=since).filter(
        Q(status=OutboxStatus.SENT) | Q(status=OutboxStatus.SKIPPED, day=today))
    recent = set(done.values_list("key", flat=True))
    waiting = {message.key: message for message in _waiting(kind, entity)}
    seen, added = set(), 0
    for candidate in source(today, company, currency):
        key = candidate["key"] if entity is None else f"{candidate['key']}@{entity.pk}"
        phone = _number(candidate["phone"])
        if key in recent or not phone:
            continue
        seen.add(key)
        fields = {"name": candidate["name"][:255], "phone": phone[:30], "link": candidate["link"][:255], "entity": entity,
                  "text": render(row.text, name=candidate["name"], company=company, **candidate["values"])}
        earlier = waiting.get(key)
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
    rows = OutboxMessage.objects.filter(kind=kind, status=OutboxStatus.PENDING)
    return rows if entity is None else rows.filter(entity=entity)


def visible(user):
    """The messages this user may see and send: the entity being worked in, never another (HG-034)."""

    return entity_scope.for_user(entity_scope.scope(OutboxMessage.objects.all(), "entity"), "entity", user)


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
    message.status = status
    message.channel = "link" if status == OutboxStatus.SENT else ""
    message.sent_at, message.sent_by = timezone.now(), user
    message.save(update_fields=["status", "channel", "sent_at", "sent_by"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="messaging", action=f"message_{status}", object_type="messaging.OutboxMessage",
                            object_id=str(message.pk), before_data={}, after_data={"kind": message.kind, "key": message.key, "day": str(message.day)})
    return message


def counts(user, today=None):
    today = today or timezone.localdate()
    rows = visible(user).filter(day=today)
    return {status: rows.filter(status=status).count() for status in OutboxStatus.values}
