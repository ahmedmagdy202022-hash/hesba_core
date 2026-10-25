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


class MessageTemplate(models.Model):
    kind = models.CharField(max_length=12, choices=MessageKind.choices, unique=True)
    text = models.TextField()
    active = models.BooleanField(default=True)
    #: The same customer is not reminded of the same kind again for this many days.
    every_days = models.PositiveSmallIntegerField(default=7, validators=[MinValueValidator(1), MaxValueValidator(90)])
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)


class OutboxStatus(models.TextChoices):
    PENDING = "pending", "Waiting"
    SENT = "sent", "Sent"
    SKIPPED = "skipped", "Skipped"


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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-day", "kind", "pk"]
        constraints = [models.UniqueConstraint(fields=["key", "day"], name="messaging_one_per_key_per_day")]
