from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.models import Role
from permissions.services import user_has_permission

from .models import ClientProfile, FeatureFlag, SystemSetting
from .setup_services import ModuleChangeRefused, module_settings_rows, set_module_enabled
from . import setup_catalog as catalog


STRINGS = {
    "ar": {"page_title": "الإعدادات", "dashboard": "لوحة القيادة", "language": "English", "settings": "إعدادات التشغيل", "roles": "الأدوار والصلاحيات", "back": "العودة للإعدادات", "hidden": "قيمة حساسة مخفية",
           "modules": "الموديولات", "modules_lead": "شغّل أو اقفل أي موديول. القفل بيخفي الموديول ويقفل شاشاته بس، وبياناته بتفضل محفوظة زي ما هي.",
           "state_on": "مفعّل", "state_off": "مقفول", "state_required": "أساسي لنشاطك", "state_soon": "قريبًا",
           "turn_on": "تفعيل", "turn_off": "قفل", "view_only": "تقدر تشوف الموديولات بس؛ التغيير لصاحب الحساب.",
           "setup_first": "كمّل الإعداد الأول قبل ما تغيّر الموديولات.", "saved_on": "اتفعّل موديول «{module}».", "saved_off": "اتقفل موديول «{module}». بياناته محفوظة.", "refused": "التغيير ده مش مسموح: الموديول أساسي لنشاطك أو لسه مش متاح."},
    "en": {"page_title": "Settings", "dashboard": "Dashboard", "language": "العربية", "settings": "Operational settings", "roles": "Roles and permissions", "back": "Back to settings", "hidden": "Sensitive value hidden",
           "modules": "Modules", "modules_lead": "Switch any module on or off. Switching off only hides the module and closes its screens; its data is kept as it is.",
           "state_on": "On", "state_off": "Off", "state_required": "Required for your activity", "state_soon": "Coming soon",
           "turn_on": "Switch on", "turn_off": "Switch off", "view_only": "You can view modules; changing them is for the account owner.",
           "setup_first": "Finish setup before changing modules.", "saved_on": "The “{module}” module is on.", "saved_off": "The “{module}” module is off. Its data is kept.", "refused": "That change is not allowed: the module is required for your activity or not available yet."},
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



@require_permission("settings.view_settings")
def module_settings(request):
    """SETTINGS-001: switch modules on and off without the admin site."""

    profile = ClientProfile.get_active()
    lang = _lang(request)
    words = STRINGS[lang]
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Changing modules needs settings.manage_settings.")
        slug = request.POST.get("module", "")
        enabled = request.POST.get("enabled") == "1"
        try:
            changed = set_module_enabled(profile, slug, enabled, user=request.user)
        except ModuleChangeRefused:
            messages.error(request, words["setup_first"] if profile is None or not profile.setup_is_complete else words["refused"])
        else:
            if changed:
                key = "saved_on" if enabled else "saved_off"
                messages.success(request, words[key].format(module=catalog.module_label(slug, lang)))
        return redirect(f"{reverse('settings_core:modules')}?lang={lang}")
    return render(
        request,
        "settings_core/modules.html",
        _context(
            request,
            rows=module_settings_rows(profile, lang) if profile is not None else [],
            setup_complete=profile is not None and profile.setup_is_complete,
            can_manage=can_manage,
        ),
    )
