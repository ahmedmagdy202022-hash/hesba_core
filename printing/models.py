"""PRINT-003: the company details as they were when a document was posted.

A reprint of an old invoice must show the address, phone and tax number the
customer was given that day, not today's. When a sales or purchase invoice, a
return or a payment reaches "posted", its company details are copied here
once and never changed; printing uses them from then on. The logo is not
copied (it is not a legal detail and would repeat hundreds of kilobytes per
document): documents always show the current logo.
"""

from django.db import models


class DocumentCompanySnapshot(models.Model):
    kind = models.CharField(max_length=30)
    object_id = models.PositiveBigIntegerField()
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "object_id"], name="printing_one_snapshot_per_document")]

    def __str__(self):
        return f"{self.kind} #{self.object_id}"
