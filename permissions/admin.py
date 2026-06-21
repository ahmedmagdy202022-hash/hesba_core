from django.contrib import admin

from .models import Permission, Role, RolePermission


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0
    autocomplete_fields = ("permission",)


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "module", "is_report_permission", "is_sensitive_finance", "active")
    search_fields = ("code", "name_ar", "name_en")
    list_filter = ("module", "is_report_permission", "is_sensitive_finance", "active")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "is_system_role", "active")
    search_fields = ("code", "name_ar", "name_en")
    list_filter = ("is_system_role", "active")
    inlines = (RolePermissionInline,)


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission", "allow")
    search_fields = ("role__code", "permission__code")
    list_filter = ("allow", "permission__module", "permission__is_sensitive_finance")
