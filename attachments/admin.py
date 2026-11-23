from django.contrib import admin

from .models import Attachment


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    """Metadata only: the raw bytes are never shown or editable here."""

    exclude = ("data",)
    list_display = ("pk", "kind", "stage", "original_filename", "content_type_name", "size", "uploaded_by", "created_at")
    list_filter = ("kind", "stage")
    readonly_fields = ("content_type", "object_id", "entity", "kind", "stage", "original_filename", "content_type_name", "size", "sha256",
                       "uploaded_by", "created_at")

    def get_queryset(self, request):
        return super().get_queryset(request).defer("data")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
