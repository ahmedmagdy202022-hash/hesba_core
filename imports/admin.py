from django.contrib import admin

from .models import ImportBatch, ImportRaw, ImportReview


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
    list_display = ("batch_code", "target_type", "status", "source_file_name", "total_rows", "valid_rows", "invalid_rows", "imported_rows", "created_at")
    search_fields = ("batch_code", "source_file_name", "notes")
    list_filter = ("target_type", "status", "go_live_date", "created_at")
    autocomplete_fields = ("created_by",)
    inlines = (ImportRawInline, ImportReviewInline)


@admin.register(ImportRaw)
class ImportRawAdmin(admin.ModelAdmin):
    list_display = ("batch", "row_number", "row_status", "target_model", "target_object_id", "created_at")
    search_fields = ("batch__batch_code", "target_model", "target_object_id")
    list_filter = ("row_status", "created_at")
    autocomplete_fields = ("batch",)
    readonly_fields = ("raw_data", "validation_errors", "created_at")


@admin.register(ImportReview)
class ImportReviewAdmin(admin.ModelAdmin):
    list_display = ("batch", "raw_row", "review_status", "reviewed_by", "reviewed_at")
    search_fields = ("batch__batch_code", "notes")
    list_filter = ("review_status", "reviewed_at", "created_at")
    autocomplete_fields = ("batch", "raw_row", "reviewed_by")
