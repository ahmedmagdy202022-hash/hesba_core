from django.contrib import admin

from .models import ClientProfile, FeatureFlag, SupportAccessGrant, SystemSetting, UsageStatusSnapshot


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ("client_code", "display_name", "activity_type", "edition_code", "is_active")
    search_fields = ("client_code", "legal_name", "display_name")
    list_filter = ("activity_type", "edition_code", "is_active")


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "data_type", "is_sensitive", "active", "updated_at")
    search_fields = ("key", "description")
    list_filter = ("data_type", "is_sensitive", "active")


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "enabled", "updated_at")
    search_fields = ("code", "name")
    list_filter = ("enabled",)


@admin.register(UsageStatusSnapshot)
class UsageStatusSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "status_level",
        "total_rows",
        "active_items_count",
        "active_customers_count",
        "active_suppliers_count",
        "sales_invoices_count",
        "purchase_invoices_count",
    )
    list_filter = ("status_level", "created_at")
    readonly_fields = (
        "created_at",
        "status_level",
        "total_rows",
        "active_items_count",
        "active_customers_count",
        "active_suppliers_count",
        "stock_movements_count",
        "cashbox_movements_count",
        "sales_invoices_count",
        "purchase_invoices_count",
        "warnings",
        "recommendations",
    )


@admin.register(SupportAccessGrant)
class SupportAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("granted_to_identifier", "starts_at", "expires_at", "revoked_at", "granted_by")
    search_fields = ("granted_to_identifier", "reason")
    list_filter = ("starts_at", "expires_at", "revoked_at")
