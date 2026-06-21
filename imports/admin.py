from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .apply_services import apply_import_batch
from .models import ImportBatch, ImportRaw, ImportReview
from .services import approve_import_batch
from .validators import validate_import_batch


class ImportRawInline(admin.TabularInline):
    model = ImportRaw
    extra = 0
    fields = ("row_number", "row_status", "target_model", "target_object_id")
    readonly_fields = ("row_number", "row_status", "target_model", "target_object_id")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ImportReviewInline(admin.TabularInline):
    model = ImportReview
    extra = 0
    fields = ("raw_row", "review_status", "reviewed_by", "reviewed_at", "notes")
    readonly_fields = ("raw_row", "review_status", "reviewed_by", "reviewed_at", "notes")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_code",
        "target_type",
        "status",
        "source_file_name",
        "total_rows",
        "valid_rows",
        "invalid_rows",
        "imported_rows",
        "created_at",
    )
    search_fields = ("batch_code", "source_file_name", "notes")
    list_filter = ("target_type", "status", "go_live_date", "created_at")
    autocomplete_fields = ("created_by",)
    inlines = (ImportRawInline, ImportReviewInline)
    actions = ("validate_selected_batches", "approve_selected_batches", "apply_selected_batches")

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Validate selected import batches")
    def validate_selected_batches(self, request, queryset):
        success_count = 0
        for batch in queryset:
            try:
                result = validate_import_batch(batch.id)
            except ValidationError as exc:
                self.message_user(request, f"{batch.batch_code}: {exc}", level=messages.ERROR)
                continue
            success_count += 1
            self.message_user(
                request,
                f"{batch.batch_code}: validated {result['valid']} valid / {result['invalid']} invalid rows.",
                level=messages.INFO,
            )
        if success_count:
            self.message_user(request, f"Validated {success_count} import batch(es).", level=messages.SUCCESS)

    @admin.action(description="Approve selected import batches")
    def approve_selected_batches(self, request, queryset):
        success_count = 0
        for batch in queryset:
            try:
                approve_import_batch(batch.id)
            except ValidationError as exc:
                self.message_user(request, f"{batch.batch_code}: {exc}", level=messages.ERROR)
                continue
            success_count += 1
        if success_count:
            self.message_user(request, f"Approved {success_count} import batch(es).", level=messages.SUCCESS)

    @admin.action(description="Apply selected import batches")
    def apply_selected_batches(self, request, queryset):
        success_count = 0
        for batch in queryset:
            try:
                applied_rows = apply_import_batch(batch.id, user=request.user)
            except ValidationError as exc:
                self.message_user(request, f"{batch.batch_code}: {exc}", level=messages.ERROR)
                continue
            success_count += 1
            self.message_user(
                request,
                f"{batch.batch_code}: applied {len(applied_rows)} row(s) to controlled tables.",
                level=messages.INFO,
            )
        if success_count:
            self.message_user(request, f"Applied {success_count} import batch(es).", level=messages.SUCCESS)


@admin.register(ImportRaw)
class ImportRawAdmin(admin.ModelAdmin):
    list_display = ("batch", "row_number", "row_status", "target_model", "target_object_id", "created_at")
    search_fields = ("batch__batch_code", "target_model", "target_object_id")
    list_filter = ("row_status", "created_at")
    autocomplete_fields = ("batch",)
    readonly_fields = (
        "batch",
        "row_number",
        "raw_data",
        "row_status",
        "validation_errors",
        "target_model",
        "target_object_id",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportReview)
class ImportReviewAdmin(admin.ModelAdmin):
    list_display = ("batch", "raw_row", "review_status", "reviewed_by", "reviewed_at")
    search_fields = ("batch__batch_code", "notes")
    list_filter = ("review_status", "reviewed_at", "created_at")
    autocomplete_fields = ("batch", "raw_row", "reviewed_by")
