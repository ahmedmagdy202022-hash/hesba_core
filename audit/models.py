from django.conf import settings
from django.db import models


class AuditEventType(models.TextChoices):
    LOGIN = "login", "Login"
    LOGOUT = "logout", "Logout"
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    DELETE = "delete", "Delete"
    EXPORT = "export", "Export"
    IMPORT = "import", "Import"
    PERMISSION_CHANGE = "permission_change", "Permission change"
    SUPPORT_ACCESS = "support_access", "Support access"
    CLOSING = "closing", "Closing"
    REOPENING = "reopening", "Reopening"
    ADJUSTMENT = "adjustment", "Adjustment"


class AuditLog(models.Model):
    """Traceable audit record for sensitive Hesba actions.

    Audit rows are append-only by business rule. UI and reports must read them;
    normal users must not edit or delete them.
    """

    event_type = models.CharField(max_length=40, choices=AuditEventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    module = models.CharField(max_length=80)
    action = models.CharField(max_length=120)
    object_type = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=120, blank=True)
    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    support_access_identifier = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["event_type"]),
            models.Index(fields=["module", "action"]),
            models.Index(fields=["object_type", "object_id"]),
        ]
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"

    def __str__(self):
        return f"{self.created_at} - {self.event_type} - {self.action}"
