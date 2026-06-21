from django.contrib import admin

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "role", "active", "is_support_user", "must_change_password")
    search_fields = ("user__username", "user__email", "display_name", "phone")
    list_filter = ("role", "active", "is_support_user", "must_change_password")
