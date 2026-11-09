"""A1 (HG-079): the voice summary's audio, cached so the same text is never voiced (and paid for) twice.

One row per entity scope, language, Cairo day and text: the text is hashed, so a summary whose figures changed during
the day is a new row, and the same summary heard again is the row already here. The bytes live in the database, as
S1's attachments do, because the host's disk is ephemeral. Rows are a cache: they hold no money, stock or ledger
state, and ``ai.voice_summary.prune`` deletes them after seven days.

The text is kept with its audio so the card can show what it says. It is never written to the audit (HG-078).
"""

from django.conf import settings
from django.db import models


class VoiceSummaryAudio(models.Model):
    #: The entity the summary was built in; empty for the whole group.
    entity = models.ForeignKey("entities.Entity", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    #: ``group`` or ``entity:<id>``. A NULL entity cannot take part in a unique constraint on every database, so the
    #: scope is spelled out here.
    scope_key = models.CharField(max_length=32)
    language = models.CharField(max_length=2)
    day = models.DateField(db_index=True)
    text_sha256 = models.CharField(max_length=64)
    text = models.TextField()
    #: False while the one request that claimed this row is voicing it; any other request waits for it.
    ready = models.BooleanField(default=False)
    content_type = models.CharField(max_length=60, blank=True)
    audio = models.BinaryField(default=b"", editable=False)
    size = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scope_key", "language", "day", "text_sha256"], name="ai_voice_summary_one_per_text"),
        ]

    def __str__(self):
        return f"{self.scope_key} {self.language} {self.day} {self.text_sha256[:8]}"
