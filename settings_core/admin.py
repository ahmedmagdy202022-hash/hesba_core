from django.contrib import admin

from .models import ClientProfile, FeatureFlag, SupportAccessGrant, SystemSetting


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


@admin.register(SupportAccessGrant)
class SupportAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("granted_to_identifier", "starts_at", "expires_at", "revoked_at", "granted_by")
    search_fields = ("granted_to_identifier", "reason")
    list_filter = ("starts_at", "expires_at", "revoked_at")
