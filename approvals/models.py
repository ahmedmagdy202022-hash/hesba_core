"""FB-27 (Ahmed's note #27): a second person approves what the owner chose to hold for approval.

A request holds one action on one document, with what the screen sent (the reason, the reversal date). Nothing
moves while it waits. When someone allowed to approve says yes, the same service the screen would have called runs
then, in the requester's name, and the request keeps who approved and when. A no, or the requester withdrawing it,
leaves the document as it was.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class ApprovalStatus(models.TextChoices):
    PENDING = "pending", "Waiting"
    DONE = "done", "Approved and done"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Approved, but could not be done"
    WITHDRAWN = "withdrawn", "Withdrawn"


class ApprovalRequest(models.Model):
    action = models.CharField(max_length=60)
    object_id = models.PositiveBigIntegerField()
    object_label = models.CharField(max_length=200, blank=True)
    data = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approval_requests")
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approval_decisions", null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-pk"]
        indexes = [models.Index(fields=["status"])]
        constraints = [
            # one waiting request per action on a document, so the same cancellation is never asked twice
            models.UniqueConstraint(fields=["action", "object_id"], condition=Q(status="pending"), name="one_pending_approval_per_action"),
        ]

    def __str__(self):
        return f"{self.action} #{self.object_id} ({self.status})"
