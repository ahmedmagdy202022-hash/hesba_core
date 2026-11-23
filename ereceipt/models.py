"""T8 (MARKET-PARITY): the Tax Authority's e-receipt for sales to consumers (receipt v1.2), offline part.

Each till sale gets a receipt when it is posted. A receipt's UUID is the SHA-256 of its serialized content with
the UUID itself empty, and it carries the UUID of the receipt issued before it on the same device, so the device's
receipts form a chain that cannot be reordered. Receipts wait in a queue; the authority accepts one up to 24 hours
after it was issued. Sending them needs the device's credentials on the authority's receipt portal.
"""

from django.db import models


class Device(models.Model):
    """A till registered on the authority's receipt portal."""

    serial_number = models.CharField(max_length=100, unique=True)
    branch_code = models.CharField(max_length=50, default="0")
    #: What the receipt portal registered for this POS, sent with its login (headers ``pososversion`` and
    #: ``posmodelframework``). Its client id, secret and pre-shared key are secrets: they live in the deployment's
    #: environment, never here (``ereceipt.portal``).
    os_version = models.CharField(max_length=100, blank=True)
    model_framework = models.CharField(max_length=100, blank=True)
    #: The UUID of the last receipt issued on this device: the next one's ``previousUUID`` (empty before the first).
    last_uuid = models.CharField(max_length=64, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.serial_number


class ReceiptStatus(models.TextChoices):
    QUEUED = "queued", "Waiting to be sent"
    SUBMITTED = "submitted", "Sent"
    VALID = "valid", "Accepted"
    INVALID = "invalid", "Invalid"
    NOT_ISSUED = "not_issued", "Not issued: data missing"


class Receipt(models.Model):
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="ereceipt")
    device = models.ForeignKey(Device, on_delete=models.PROTECT, null=True, blank=True, related_name="receipts")
    status = models.CharField(max_length=12, choices=ReceiptStatus.choices, default=ReceiptStatus.QUEUED)
    uuid = models.CharField(max_length=64, blank=True, db_index=True)
    previous_uuid = models.CharField(max_length=64, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    #: The receipt exactly as it was hashed, as JSON text: a JSON column may reorder its keys (PostgreSQL's does),
    #: and the UUID is the hash of this text's own order (Codex #271).
    document_text = models.TextField(blank=True)
    #: Why a receipt could not be issued yet (company, item or device data missing).
    problems = models.JSONField(default=list, blank=True)
    message = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    #: The authority's answer: the submission it went in, and the receipt's long id once accepted.
    submission_uuid = models.CharField(max_length=64, blank=True)
    long_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    @property
    def document(self):
        import json

        return json.loads(self.document_text) if self.document_text else {}
