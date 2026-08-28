from django.shortcuts import render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.models import Role
from permissions.services import user_has_permission

from .models import ClientProfile, FeatureFlag, SystemSetting


STRINGS = {
    "ar": {"page_title": "الإعدادات", "dashboard": "لوحة القيادة", "language": "English", "settings": "إعدادات التشغيل", "roles": "الأدوار والصلاحيات", "back": "العودة للإعدادات", "hidden": "قيمة حساسة مخفية"},
    "en": {"page_title": "Settings", "dashboard": "Dashboard", "language": "العربية", "settings": "Operational settings", "roles": "Roles and permissions", "back": "Back to settings", "hidden": "Sensitive value hidden"},
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": STRINGS[lang], "page_title": STRINGS[lang]["page_title"]}
    context.update(extra)
    return context


@require_permission("settings.view_settings")
def settings_overview(request):
    can_manage = user_has_permission(request.user, "settings.manage_settings") and request.user.is_staff
    return render(
        request,
        "settings_core/overview.html",
        _context(
            request,
            client=ClientProfile.get_active(),
            settings=SystemSetting.objects.filter(active=True),
            feature_flags=FeatureFlag.objects.all(),
            can_manage=can_manage,
            admin_settings_url=reverse("admin:settings_core_clientprofile_changelist") if can_manage else "",
        ),
    )


@require_permission("settings.view_settings")
def role_list(request):
    can_manage = user_has_permission(request.user, "permissions.manage_roles") and request.user.is_staff
    roles = Role.objects.filter(active=True).prefetch_related("rolepermission_set__permission")
    return render(
        request,
        "settings_core/roles.html",
        _context(
            request,
            roles=roles,
            can_manage=can_manage,
            admin_roles_url=reverse("admin:permissions_role_changelist") if can_manage else "",
            admin_users_url=reverse("admin:auth_user_changelist") if can_manage else "",
        ),
    )

