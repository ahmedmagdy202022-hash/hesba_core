"""S8 (services activity 17): loyalty and marketing campaigns, put on the outbox one message per customer.

A ``Campaign`` is a message with placeholders and an audience filter. Queuing it writes one ``OutboxMessage``
(kind CAMPAIGN, key ``campaign:<id>:<customer id>``) per customer with a valid mobile, spread over days by the
daily cap (``settings.MESSAGING_CAMPAIGN_DAILY_CAP``); the outbox stays the only thing that sends (a click opens
WhatsApp, or the WhatsApp Business account sends it when the CAMPAIGN kind has an approved template). Nothing here
touches money, stock, a ledger or a permission: the audience is only read from customers, posted sales and the
loyalty ledger.

The outbox lists and sends only today's messages, so a message queued for a later day stays hidden until then, and
one left waiting from an earlier day moves forward (``carry``, called by ``services.build``). The customer has no
opt-out or consent field, so none is honoured yet (an open question for the owner, not a schema change here).
"""

import re
from collections import Counter
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Count, Exists, Max, OuterRef, Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from entities.current import current_entity, working_in
from entities.services import main_entity

from . import services
from .models import Campaign, CampaignAudience, CampaignStatus, MessageKind, MessageTemplate, OutboxMessage, OutboxStatus

PLACEHOLDERS = ("{name}", "{company}", "{points}", "{days}")
#: Audiences that need N days / N points / a category.
DAY_AUDIENCES = (CampaignAudience.INACTIVE, CampaignAudience.CATEGORY, CampaignAudience.LAPSED)
CATEGORY_AUDIENCES = (CampaignAudience.CATEGORY, CampaignAudience.LAPSED)
BATCH = 500
#: First key of the PostgreSQL advisory lock that serialises the daily-cap allocation of one entity (second key: its id).
CAP_LOCK_KEY = 7208001

MESSAGES = {
    "ar": {
        "forbidden": "الحملات محتاجة صلاحية إعدادات الرسائل.", "name": "اكتب اسم للحملة.", "text": "نص الرسالة فاضي.",
        "placeholder": "النص فيه {bad} ومش معروف؛ المسموح: {allowed}.", "audience": "اختار مين هيستلم.", "points": "اكتب أقل عدد نقاط (1 أو أكتر).",
        "days": "اكتب عدد الأيام (من 1 لـ 3650).", "category": "اختار الفئة.", "day": "اليوم مش مظبوط، اكتبه بالشكل 2026-11-08.",
        "past": "اليوم ده عدّى؛ اختار النهاردة أو بعده.", "loyalty_off": "الولاء مقفول في الإعدادات، فمفيش نقاط تتفلتر بيها.",
        "queued": "الحملة دي اتحطت قبل كده ومينفعش تتعدل أو تتحط تاني.", "off": "رسائل الحملات مقفولة. افتحها من إعدادات الحملات الأول.",
        "empty": "مفيش عملاء بيطابقوا الشريحة دي ومعاهم رقم موبايل صالح.", "wa_template": "اسم قالب واتساب: حروف إنجليزي صغيرة وأرقام و _ بس، زي ما اتسجل عند ميتا.",
        "sample": "سلمى", "campaign": "الحملة",
    },
    "en": {
        "forbidden": "Campaigns need the message-settings permission.", "name": "Give the campaign a name.", "text": "The message is empty.",
        "placeholder": "The text has {bad}, which is unknown; allowed: {allowed}.", "audience": "Choose who receives it.", "points": "Enter the fewest points (1 or more).",
        "days": "Enter the number of days (1 to 3650).", "category": "Choose the category.", "day": "The day is not valid; write it like 2026-11-08.",
        "past": "That day has passed; choose today or later.", "loyalty_off": "Loyalty is off in Settings, so there are no points to filter by.",
        "queued": "This campaign was already queued and cannot be edited or queued again.", "off": "Campaign messages are switched off. Switch them on in the campaign settings first.",
        "empty": "No customer matches this audience with a valid mobile number.", "wa_template": "The WhatsApp template name: lower-case letters, digits and _ only, as registered with Meta.",
        "sample": "Sara", "campaign": "Campaign",
    },
}


# ---- settings and who may do what ----

def daily_cap():
    """The most campaign messages that reach the outbox in one day, across every campaign of the entity."""

    try:
        return max(1, int(getattr(settings, "MESSAGING_CAMPAIGN_DAILY_CAP", 50)))
    except (TypeError, ValueError):
        return 50


def can_view(user):
    return services.can_view(user)


def can_edit(user):
    return services.can_edit(user)  # the permission that edits the outbox wording: settings.manage_settings


def active():
    return services.template(MessageKind.CAMPAIGN).active


def visible(user):
    """The campaigns of the entity being worked in, never another's (HG-034)."""

    return entity_scope.for_user(entity_scope.scope(Campaign.objects.all(), "entity"), "entity", user)


def new_entity():
    return current_entity() or main_entity()


@transaction.atomic
def save_settings(data, user, lang="ar"):
    """The on/off switch of the CAMPAIGN kind and its approved WhatsApp template (the outbox's per-kind controls)."""

    if not can_edit(user):
        raise PermissionDenied("Campaign settings need settings.manage_settings.")
    wa_template = (data.get("wa_template") or "").strip()
    if wa_template and not re.fullmatch(r"[a-z0-9_]{1,120}", wa_template):
        raise ValidationError(MESSAGES[lang]["wa_template"])
    current = services.template(MessageKind.CAMPAIGN)
    row, _created = MessageTemplate.objects.update_or_create(kind=MessageKind.CAMPAIGN, defaults={
        "text": current.text, "every_days": current.every_days, "active": data.get("active") in (True, "1", "on"), "wa_template": wa_template, "updated_by": user})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="messaging", action="save_campaign_settings", object_type="messaging.MessageTemplate",
                            object_id=str(row.pk), before_data={}, after_data={"kind": row.kind, "active": row.active, "wa_template": wa_template})
    return row


# ---- placeholders ----

def render_text(text, name, company, points="", days=""):
    for key, value in (("name", name), ("company", company), ("points", points), ("days", days)):
        text = text.replace("{" + key + "}", str(value))
    return text


# ---- drafts ----

def clean(data, lang="ar", today=None):
    """The fields of a campaign from a form, or a ValidationError in ``lang``."""

    words = MESSAGES[lang]
    today = today or timezone.localdate()
    name = (data.get("name") or "").strip()[:120]
    if not name:
        raise ValidationError(words["name"])
    text = (data.get("text") or "").strip()[:1000]
    if not text:
        raise ValidationError(words["text"])
    bad = [found for found in re.findall(r"\{[^}]*\}", text) if found not in PLACEHOLDERS]
    if bad:
        raise ValidationError(words["placeholder"].format(bad=" ".join(bad), allowed=" ".join(PLACEHOLDERS)))
    audience = data.get("audience")
    if audience not in CampaignAudience.values:
        raise ValidationError(words["audience"])
    try:
        threshold = int(str(data.get("threshold") or 0).strip())
    except ValueError:
        threshold = -1
    category = None
    if audience == CampaignAudience.POINTS:
        from loyalty import services as loyalty

        if threshold < 1:
            raise ValidationError(words["points"])
        if not loyalty.enabled():
            raise ValidationError(words["loyalty_off"])
    elif audience in DAY_AUDIENCES:
        if not 1 <= threshold <= 3650:
            raise ValidationError(words["days"])
    else:
        threshold = 0
    if audience in CATEGORY_AUDIENCES:
        from master_data.models import Category

        raw = str(data.get("category") or "").strip()
        category = Category.objects.filter(pk=int(raw)).first() if raw.isdecimal() else None
        if category is None:
            raise ValidationError(words["category"])
    try:
        day = date.fromisoformat(str(data.get("scheduled_day") or "").strip()) if data.get("scheduled_day") else today
    except ValueError:
        raise ValidationError(words["day"])
    return {"name": name, "text": text, "audience": audience, "threshold": threshold, "category": category, "scheduled_day": day}


def _check_day(fields, today, lang):
    if fields["scheduled_day"] < today:
        raise ValidationError(MESSAGES[lang]["past"])


@transaction.atomic
def save_draft(data, user, lang="ar", campaign=None, today=None):
    """Create a draft in the entity being worked in, or change an unqueued one."""

    if not can_edit(user):
        raise PermissionDenied("Campaigns need settings.manage_settings.")
    today = today or timezone.localdate()
    fields = clean(data, lang, today)
    _check_day(fields, today, lang)
    if campaign is None:
        return Campaign.objects.create(entity=new_entity(), created_by=user, **fields)
    campaign = visible(user).select_for_update(of=("self",)).filter(pk=campaign.pk).first()
    if campaign is None:
        raise PermissionDenied("This campaign belongs to another entity.")
    if campaign.status != CampaignStatus.DRAFT:
        raise ValidationError(MESSAGES[lang]["queued"])
    for key, value in fields.items():
        setattr(campaign, key, value)
    campaign.save()
    return campaign


@transaction.atomic
def delete_draft(campaign, user, lang="ar"):
    if not can_edit(user):
        raise PermissionDenied("Campaigns need settings.manage_settings.")
    campaign = visible(user).select_for_update(of=("self",)).filter(pk=campaign.pk).first()
    if campaign is None:
        raise PermissionDenied("This campaign belongs to another entity.")
    if campaign.status != CampaignStatus.DRAFT:
        raise ValidationError(MESSAGES[lang]["queued"])
    AuditLog.objects.create(event_type=AuditEventType.DELETE, actor=user, module="messaging", action="delete_campaign", object_type="messaging.Campaign",
                            object_id=str(campaign.pk), before_data={"name": campaign.name}, after_data={})
    campaign.delete()


# ---- the audience ----

def _category_ids(category):
    """The category and everything below it."""

    from master_data.models import Category

    found, frontier = {category.pk}, [category.pk]
    while frontier:
        frontier = [pk for pk in Category.objects.filter(parent_id__in=frontier).values_list("pk", flat=True) if pk not in found]
        found.update(frontier)
    return found


def _matching(campaign, today):
    """Customers matching the campaign's filter: active, never the walk-in customer, and a customer's purchases are
    read in the campaign's entity only (customers themselves are shared by the whole group)."""

    from master_data.models import Customer
    from sales.models import SalesInvoice, SalesInvoiceStatus, SalesLine
    from sales.pos import WALK_IN_CODE

    customers = Customer.objects.filter(active=True).exclude(customer_code=WALK_IN_CODE)
    audience, threshold = campaign.audience, campaign.threshold
    if audience == CampaignAudience.ALL:
        return customers
    if audience == CampaignAudience.POINTS:
        from loyalty.models import PointsEntry

        raw = PointsEntry.objects.values("customer").annotate(n=Sum("points")).filter(n__gte=threshold).values("customer")  # expiry only lowers it
        return customers.filter(pk__in=raw)
    cutoff = today - timedelta(days=threshold)  # a purchase within the last N days is dated after this
    with working_in(campaign.entity or main_entity()):
        invoices = entity_scope.scope(SalesInvoice.objects.filter(status=SalesInvoiceStatus.POSTED), entity_scope.SALES_INVOICE)
        lines = entity_scope.scope(SalesLine.objects.filter(invoice__status=SalesInvoiceStatus.POSTED), "invoice__" + entity_scope.SALES_INVOICE)
    if audience == CampaignAudience.INACTIVE:
        ever = Exists(invoices.filter(customer=OuterRef("pk")))
        recent = Exists(invoices.filter(customer=OuterRef("pk"), invoice_date__gt=cutoff))
        return customers.filter(ever).exclude(recent)
    lines = lines.filter(item__category__in=_category_ids(campaign.category))
    ever = Exists(lines.filter(invoice__customer=OuterRef("pk")))
    recent = Exists(lines.filter(invoice__customer=OuterRef("pk"), invoice__invoice_date__gt=cutoff))
    return customers.filter(ever & recent) if audience == CampaignAudience.CATEGORY else customers.filter(ever).exclude(recent)


class Audience:
    """Who a campaign reaches: ``rows`` are ``(customer id, name, number)`` of customers with a valid mobile, one per
    number; ``match`` counts the filter's customers before the phone check."""

    def __init__(self, match, rows, no_phone, duplicates):
        self.match, self.rows, self.no_phone, self.duplicates = match, rows, no_phone, duplicates

    @property
    def count(self):
        return len(self.rows)


def audience(campaign, today=None):
    from loyalty import services as loyalty

    today = today or timezone.localdate()
    customers = _matching(campaign, today).order_by("pk")
    if campaign.audience == CampaignAudience.POINTS:  # the SQL sum ignores expiry and drafts holding points: confirm on the few candidates
        customers = [customer for customer in customers if loyalty.balance(customer, today)["points"] >= campaign.threshold]
    else:
        customers = customers.only("pk", "name", "phone", "whatsapp")
    match, rows, no_phone, duplicates, seen = 0, [], 0, 0, set()
    for customer in customers:
        match += 1
        number = services._number(customer.whatsapp or customer.phone) or services._number(customer.phone)
        if not number:
            no_phone += 1
        elif number in seen:
            duplicates += 1
        else:
            seen.add(number)
            rows.append((customer.pk, customer.name, number))
    return Audience(match, rows, no_phone, duplicates)


def _values(campaign, rows, today):
    """``{customer id: (points, days)}`` for the placeholders the text actually uses."""

    ids = [row[0] for row in rows]
    points, days = {}, {}
    if "{points}" in campaign.text:
        from loyalty import services as loyalty
        from master_data.models import Customer

        for customer in Customer.objects.filter(pk__in=ids):
            points[customer.pk] = max(loyalty.balance(customer, today)["points"], 0)
    if "{days}" in campaign.text and ids:
        from sales.models import SalesInvoice, SalesInvoiceStatus

        with working_in(campaign.entity or main_entity()):
            last = entity_scope.scope(SalesInvoice.objects.filter(status=SalesInvoiceStatus.POSTED, customer_id__in=ids), entity_scope.SALES_INVOICE)
        days = {row["customer"]: (today - row["last"]).days for row in last.values("customer").annotate(last=Max("invoice_date"))}
    return points, days


def preview(campaign, result, lang="ar", today=None):
    """The text as the first customer of the audience would get it (a sample name when nobody matches)."""

    today = today or timezone.localdate()
    company, _currency = services._company()
    if result.rows:
        sample = result.rows[:1]
        points, days = _values(campaign, sample, today)
        pk, name, _number = sample[0]
        return render_text(campaign.text, name, company, points.get(pk, 0), days.get(pk, ""))
    return render_text(campaign.text, MESSAGES[lang]["sample"], company, 0, campaign.threshold if campaign.audience in DAY_AUDIENCES else "")


# ---- the days ----

def lock_cap(entity_id):
    """Serialise cap allocation across the campaigns of one entity: each request only locks its own Campaign row, so on
    PostgreSQL two campaigns could both read the same free room and together pass the cap. A transaction-level advisory
    lock, taken before capacity is read and released at commit; a no-op on SQLite, where writes already serialise."""

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [CAP_LOCK_KEY, entity_id or 0])


def _entity_rows(entity):
    rows = OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).exclude(status=OutboxStatus.WITHDRAWN)
    return rows.filter(Q(entity=entity) | Q(entity__isnull=True)) if entity.is_main else rows.filter(entity=entity)


def plan(entity, count, start, cap=None):
    """``[(day, n)]``: ``count`` messages from ``start``, each day filled up to the cap counting what the entity's
    campaigns already put on that day."""

    cap = cap or daily_cap()
    used = Counter(_entity_rows(entity).filter(day__gte=start).values_list("day", flat=True))
    days, day, left = [], start, count
    while left > 0:
        take = min(max(cap - used[day], 0), left)
        if take:
            days.append((day, take))
            left -= take
        day += timedelta(days=1)
    return days


def start_day(campaign, today=None):
    return max(campaign.scheduled_day, today or timezone.localdate())


# ---- queue ----

def _key(campaign, customer_pk):
    return f"campaign:{campaign.pk}:{customer_pk}"


@transaction.atomic
def queue(campaign, user, lang="ar", today=None):
    """Put one outbox message per customer of the audience. Returns ``{"count", "days": [(day, n)]}``.

    Once only: the campaign is locked and must still be a draft, and no customer is given a second message of the
    same campaign even on another day. Nothing is queued when the CAMPAIGN kind is switched off or nobody matches.
    No message is sent here."""

    words = MESSAGES[lang]
    if not can_edit(user):
        raise PermissionDenied("Campaigns need settings.manage_settings.")
    today = today or timezone.localdate()
    campaign = visible(user).select_for_update(of=("self",)).select_related("category", "entity").filter(pk=campaign.pk).first()
    if campaign is None:
        raise PermissionDenied("This campaign belongs to another entity.")
    if campaign.status != CampaignStatus.DRAFT:
        raise ValidationError(words["queued"])
    if not active():
        raise ValidationError(words["off"])
    result = audience(campaign, today)
    if not result.count:
        raise ValidationError(words["empty"])
    entity = campaign.entity or main_entity()
    lock_cap(entity.pk)
    company, _currency = services._company()
    points, days = _values(campaign, result.rows, today)
    already = set(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN, key__startswith=f"campaign:{campaign.pk}:").values_list("key", flat=True))
    rows = [row for row in result.rows if _key(campaign, row[0]) not in already]
    schedule = plan(entity, len(rows), start_day(campaign, today))
    messages, index = [], 0
    for day, number_of in schedule:
        for pk, name, number in rows[index:index + number_of]:
            messages.append(OutboxMessage(
                kind=MessageKind.CAMPAIGN, key=_key(campaign, pk), day=day, name=name[:255], phone=number[:30], link=f"/parties/customer/{pk}/", entity=entity,
                text=render_text(campaign.text, name, company, points.get(pk, 0), days.get(pk, ""))))
        index += number_of
    OutboxMessage.objects.bulk_create(messages, batch_size=BATCH)
    campaign.status, campaign.queued_by, campaign.queued_at, campaign.queued_count = CampaignStatus.QUEUED, user, timezone.now(), len(messages)
    campaign.save(update_fields=["status", "queued_by", "queued_at", "queued_count", "updated_at"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="messaging", action="queue_campaign", object_type="messaging.Campaign",
                            object_id=str(campaign.pk), before_data={"status": CampaignStatus.DRAFT},
                            after_data={"name": campaign.name, "audience": campaign.audience, "threshold": campaign.threshold, "messages": len(messages),
                                        "first_day": str(schedule[0][0]) if schedule else "", "last_day": str(schedule[-1][0]) if schedule else "",
                                        "daily_cap": daily_cap(), "excluded_no_phone": result.no_phone, "excluded_duplicate_numbers": result.duplicates})
    return {"count": len(messages), "days": schedule}


# ---- the outbox's day rule ----

def carry(today):
    """Called by ``services.build``. A campaign message left waiting from an earlier day moves to today or the next
    day with room under the cap (the outbox lists and sends only today's). When the CAMPAIGN kind is switched off,
    waiting messages are withdrawn (kept as the once-only record, never sendable, text cleared) rather than sent."""

    waiting = OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN, status=OutboxStatus.PENDING)
    if not waiting.exists():
        return
    if not active():
        waiting.update(status=OutboxStatus.WITHDRAWN, text="")
        return
    cap = daily_cap()
    stale = waiting.filter(day__lt=today)
    for entity_id in set(stale.values_list("entity_id", flat=True)):
        with transaction.atomic():
            _carry_entity(entity_id, today, cap)


def _carry_entity(entity_id, today, cap):
    lock_cap(entity_id)
    stale = OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN, status=OutboxStatus.PENDING, day__lt=today)
    used = Counter(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN, entity_id=entity_id, day__gte=today)
                   .exclude(status=OutboxStatus.WITHDRAWN).values_list("day", flat=True))
    moved, day = [], today
    for message in stale.filter(entity_id=entity_id).order_by("day", "pk"):
        while used[day] >= cap:
            day += timedelta(days=1)
        message.day = day
        used[day] += 1
        moved.append(message)
    OutboxMessage.objects.bulk_update(moved, ["day"], batch_size=BATCH)


# ---- after queuing ----

def _messages(campaign):
    return OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN, key__startswith=f"campaign:{campaign.pk}:")


def sync_status(campaigns):
    """A queued campaign whose messages are all sent, skipped or withdrawn is done."""

    for campaign in campaigns:
        if campaign.status == CampaignStatus.QUEUED and not _messages(campaign).filter(status=OutboxStatus.PENDING).exists():
            Campaign.objects.filter(pk=campaign.pk, status=CampaignStatus.QUEUED).update(status=CampaignStatus.DONE)
            campaign.status = CampaignStatus.DONE


def schedule_of(campaign):
    """``[{"day", "n", "sent", "waiting"}]`` of a queued campaign's messages, by day."""

    rows = _messages(campaign).values("day").annotate(
        n=Count("pk"), sent=Count("pk", filter=Q(status=OutboxStatus.SENT)), waiting=Count("pk", filter=Q(status=OutboxStatus.PENDING))).order_by("day")
    return list(rows)
