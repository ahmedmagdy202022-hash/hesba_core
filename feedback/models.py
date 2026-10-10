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


class Visit(models.Model):
    """DEMO-TRACK: one person's time in the demo, kept outside every demo copy.

    A visitor is known by a cookie of their own from the first page, so the
    visit survives signing in and starting over (which change the demo copy).
    The name and phone are only what the visitor chose to give."""

    visitor = models.CharField(max_length=64, unique=True)
    sandbox = models.CharField(max_length=64, blank=True, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_seen_at = models.DateTimeField(db_index=True)
    name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    activity = models.CharField(max_length=40, blank=True)
    sub_activity = models.CharField(max_length=40, blank=True)
    role = models.CharField(max_length=40, blank=True)
    pages = models.PositiveIntegerField(default=0)
    first_path = models.CharField(max_length=500, blank=True)
    last_path = models.CharField(max_length=500, blank=True)
    lang = models.CharField(max_length=5, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-last_seen_at", "-id"]

    def __str__(self):
        return f"{self.name or self.visitor[:8]} · {self.pages} pages"

    @property
    def minutes(self):
        return max(int((self.last_seen_at - self.started_at).total_seconds() // 60), 0)


class PageView(models.Model):
    """DEMO-TRACK: one page a visitor opened, with the activity they were in."""

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="views")
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    path = models.CharField(max_length=500)
    activity = models.CharField(max_length=40, blank=True)
    sub_activity = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["at", "id"]
