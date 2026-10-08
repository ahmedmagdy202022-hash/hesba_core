"""DEMO-FEEDBACK: what testers tell Ahmed, kept outside every demo copy."""

from django.db import models


class Feedback(models.Model):
    MOODS = (("great", "great"), ("good", "good"), ("meh", "meh"), ("bad", "bad"))

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    message = models.TextField()
    mood = models.CharField(max_length=10, choices=MOODS, blank=True)
    contact = models.CharField(max_length=120, blank=True)
    path = models.CharField(max_length=500, blank=True)
    lang = models.CharField(max_length=5, blank=True)
    activity = models.CharField(max_length=40, blank=True)
    sub_activity = models.CharField(max_length=40, blank=True)
    role = models.CharField(max_length=40, blank=True)
    sandbox = models.CharField(max_length=64, blank=True, db_index=True)
    viewport = models.CharField(max_length=20, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.message[:40]}"
