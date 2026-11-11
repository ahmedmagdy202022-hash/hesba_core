"""T9 (MARKET-PARITY): messages to customers, gathered every day (HG-064, additive tables only).

A ``MessageTemplate`` is the wording of one kind of message (an amount due, an
instalment, tomorrow's appointment, a prescription refill) and whether it is
sent at all. ``OutboxMessage`` is one message waiting to go out today: it is
sent from the outbox through WhatsApp (a click opens the chat with the text
written), skipped, or, once a WhatsApp Business account is connected, sent by
itself. ``key`` stops the same reminder being queued twice.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class MessageKind(models.TextChoices):
    DUE = "due", "Amount due"
    INSTALMENT = "instalment", "Instalment"
    APPOINTMENT = "appointment", "Appointment tomorrow"
    REFILL = "refill", "Prescription refill"
    WORKORDER = "workorder", "Work order status"
    #: M3: a clinic's reminder of the follow-up date the doctor asked for (date and clinic only, never anything clinical).
    FOLLOWUP = "followup", "Follow-up visit"
    #: S8: a loyalty / marketing campaign message (see ``Campaign`` and messaging/campaigns.py).
    CAMPAIGN = "campaign", "Campaign"


class MessageTemplate(models.Model):
    kind = models.CharField(max_length=12, choices=MessageKind.choices, unique=True)
    text = models.TextField()
    active = models.BooleanField(default=True)
    #: The same customer is not reminded of the same kind again for this many days.
    every_days = models.PositiveSmallIntegerField(default=7, validators=[MinValueValidator(1), MaxValueValidator(90)])
    #: T9 live: the name of the template Meta approved for this kind (blank: this kind is sent by a click only).
    #: Its body takes two parameters: {{1}} the company, {{2}} the message (see messaging.whatsapp).
    wa_template = models.CharField(max_length=120, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)


class OutboxStatus(models.TextChoices):
    PENDING = "pending", "Waiting"
    SENT = "sent", "Sent"
    SKIPPED = "skipped", "Skipped"
    #: S7: a work-order message that stopped being true before it went out. Never sendable and never listed; it stays only
    #: so the order does not get a second message for the same status (its text is cleared).
    WITHDRAWN = "withdrawn", "Withdrawn"


class OutboxMessage(models.Model):
    kind = models.CharField(max_length=12, choices=MessageKind.choices, db_index=True)
    #: What the message is about, e.g. "due:customer:12" or "appointment:345"; with ``day`` it is never queued twice.
    key = models.CharField(max_length=80, db_index=True)
    day = models.DateField(db_index=True)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30)
    text = models.TextField()
    link = models.CharField(max_length=255, blank=True, help_text="The record the message is about, inside Hesba.")
    #: The entity whose record the message is about; only people working in it see and send it (HG-034).
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=OutboxStatus.choices, default=OutboxStatus.PENDING, db_index=True)
    #: link (opened in WhatsApp by a person) or api (sent by the WhatsApp Business account).
    channel = models.CharField(max_length=10, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    #: T9 live: the id WhatsApp gave a message it took, or why it refused the last try.
    provider_id = models.CharField(max_length=120, blank=True)
    error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-day", "kind", "pk"]
        constraints = [models.UniqueConstraint(fields=["key", "day"], name="messaging_one_per_key_per_day")]


class CampaignAudience(models.TextChoices):
    ALL = "all", "Every customer with a mobile"
    POINTS = "points", "Loyalty points at least N"
    INACTIVE = "inactive", "No purchase in the last N days"
    CATEGORY = "category", "Bought the category in the last N days"
    LAPSED = "lapsed", "Bought the category, but not in the last N days"


class CampaignStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    QUEUED = "queued", "Queued"
    DONE = "done", "Done"


class Campaign(models.Model):
    """S8 (HG-064 area, additive table): one message to a slice of customers, put on the outbox when queued.

    Queuing writes one ``OutboxMessage`` (kind CAMPAIGN, key ``campaign:<id>:<customer id>``) per customer; sending
    stays the outbox's job. ``threshold`` is N of the audience (points, or days); ``category`` is used by the two
    category audiences. A queued campaign is never edited or queued again.
    """

    name = models.CharField(max_length=120)
    text = models.TextField(max_length=1000)
    audience = models.CharField(max_length=10, choices=CampaignAudience.choices, default=CampaignAudience.ALL)
    threshold = models.PositiveIntegerField(default=0)
    category = models.ForeignKey("master_data.Category", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    #: The first day its messages may reach the outbox; a daily cap spreads the rest over the next days.
    scheduled_day = models.DateField()
    status = models.CharField(max_length=10, choices=CampaignStatus.choices, default=CampaignStatus.DRAFT, db_index=True)
    #: The entity it was made in (HG-034); its messages carry it and only people working in it see it.
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    queued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    queued_at = models.DateTimeField(null=True, blank=True)
    queued_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
