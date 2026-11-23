"""S1 (MARKET-PARITY, HG-072): photos, the customer's signature and documents, on any record.

The bytes live in the database (``data``) because the host's disk is ephemeral. The owner is generic
(ContentType + object_id) so work orders, appointments and customers can use the same table. Rows are
append-only: nothing here touches money, stock, a cashbox or a ledger.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Kind(models.TextChoices):
    PHOTO = "photo", "Photo"
    SIGNATURE = "signature", "Signature"
    DOCUMENT = "document", "Document"


class Stage(models.TextChoices):
    RECEIVE = "receive", "At receiving"
    DELIVER = "deliver", "At delivery"
    OTHER = "other", "Other"
    # M5 (HG-083): the clinic's categories on a patient file or a visit. Choices only, no column change; an owner type
    # allows only its own stages (``attachments.services.OWNERS[...]["stages"]``), so a work order never takes these.
    SCAN = "scan", "Scan / X-ray"
    LAB = "lab", "Lab report"
    PHOTO = "photo", "Photo"
    REPORT = "report", "Report"


class Attachment(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.PositiveBigIntegerField()
    owner = GenericForeignKey("content_type", "object_id")
    #: Copied from the owner, so the entity data scope (HG-034) holds on the attachment itself too.
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.OTHER)
    original_filename = models.CharField(max_length=255, blank=True)
    #: Sniffed from the bytes, never the client's claim.
    content_type_name = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    data = models.BinaryField(editable=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["content_type", "object_id"], name="attachments_owner_idx")]

    def __str__(self):
        return f"{self.kind} {self.original_filename or self.sha256[:8]}"
