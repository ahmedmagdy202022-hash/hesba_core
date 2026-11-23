from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "role", "active", "is_support_user", "must_change_password")
    search_fields = ("user__username", "user__email", "display_name", "phone")
    list_filter = ("role", "active", "is_support_user", "must_change_password")


class UserProfileInline(admin.StackedInline):
    """Hesba role, edited alongside the user it belongs to.

    Permissions hang off this row, so a user created without one holds nothing.
    Putting it on the user page is what makes that hard to forget.
    """

    model = UserProfile
    can_delete = False
    # A one-to-one inline is headed by the singular name, so both are set or the
    # section shows the model's own "User Profile" instead.
    verbose_name = "Hesba profile"
    verbose_name_plural = "Hesba profile"
    fields = ("role", "display_name", "phone", "active", "is_support_user", "must_change_password")


class UserAdmin(DjangoUserAdmin):
    inlines = [UserProfileInline]
    list_display = DjangoUserAdmin.list_display + ("hesba_role",)

    @admin.display(description="Hesba role", ordering="hesba_profile__role__code")
    def hesba_role(self, obj):
        profile = getattr(obj, "hesba_profile", None)
        if profile is None or profile.role is None:
            return "—"
        return profile.role.code

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("hesba_profile__role")


admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), UserAdmin)
