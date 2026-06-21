from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "module", "action", "actor", "object_type", "object_id")
    search_fields = ("module", "action", "object_type", "object_id", "reason", "support_access_identifier")
    list_filter = ("event_type", "module", "created_at")
    readonly_fields = (
        "event_type",
        "actor",
        "module",
        "action",
        "object_type",
        "object_id",
        "before_data",
        "after_data",
        "reason",
        "ip_address",
        "user_agent",
        "support_access_identifier",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
