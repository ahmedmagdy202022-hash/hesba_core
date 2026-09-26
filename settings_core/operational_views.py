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
from .currency import CurrencyChangeRefused, change_company_currency, currency_choices, has_financial_records


STRINGS = {
    "ar": {"page_title": "الإعدادات", "dashboard": "لوحة القيادة", "language": "English", "settings": "إعدادات التشغيل", "roles": "الأدوار والصلاحيات", "back": "العودة للإعدادات", "hidden": "قيمة حساسة مخفية",
           "modules": "الموديولات", "modules_lead": "شغّل أو اقفل أي موديول. القفل بيخفي الموديول ويقفل شاشاته بس، وبياناته بتفضل محفوظة زي ما هي.",
           "state_on": "مفعّل", "state_off": "مقفول", "state_required": "أساسي لنشاطك", "state_soon": "قريبًا",
           "turn_on": "تفعيل", "turn_off": "قفل", "view_only": "تقدر تشوف الموديولات بس؛ التغيير لصاحب الحساب.",
           "setup_first": "كمّل الإعداد الأول قبل ما تغيّر الموديولات.", "saved_on": "اتفعّل موديول «{module}».", "saved_off": "اتقفل موديول «{module}». بياناته محفوظة.", "refused": "التغيير ده مش مسموح: الموديول أساسي لنشاطك أو لسه مش متاح.",
           "currency": "العملة", "currency_lead": "حسبة بتستخدم عملة واحدة للشركة، وبتظهر جنب كل مبلغ.", "currency_field": "عملة الشركة", "save": "حفظ",
           "currency_locked": "العملة مقفولة لأن فيه مبالغ متسجلة بيها بالفعل (فواتير أو حركات خزنة أو أرصدة افتتاحية). حسبة مابتحوّلش المبالغ بين العملات، فتغييرها دلوقتي هيغيّر معنى الأرقام.", "currency_saved": "العملة اتغيرت لـ {code}.", "no_profile": "لسه مفيش بيانات شركة. كمّل الإعداد الأول."},
    "en": {"page_title": "Settings", "dashboard": "Dashboard", "language": "العربية", "settings": "Operational settings", "roles": "Roles and permissions", "back": "Back to settings", "hidden": "Sensitive value hidden",
           "modules": "Modules", "modules_lead": "Switch any module on or off. Switching off only hides the module and closes its screens; its data is kept as it is.",
           "state_on": "On", "state_off": "Off", "state_required": "Required for your activity", "state_soon": "Coming soon",
           "turn_on": "Switch on", "turn_off": "Switch off", "view_only": "You can view modules; changing them is for the account owner.",
           "setup_first": "Finish setup before changing modules.", "saved_on": "The “{module}” module is on.", "saved_off": "The “{module}” module is off. Its data is kept.", "refused": "That change is not allowed: the module is required for your activity or not available yet.",
           "currency": "Currency", "currency_lead": "Hesba uses one currency for the company, shown beside every amount.", "currency_field": "Company currency", "save": "Save",
           "currency_locked": "The currency is locked because amounts are already recorded in it (invoices, cash movements or opening balances). Hesba does not convert amounts between currencies, so changing it now would change what the figures mean.", "currency_saved": "The currency is now {code}.", "no_profile": "There is no company profile yet. Finish setup first."},
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


@require_permission("settings.view_settings")
def currency_settings(request):
    """SETTINGS-002: the company currency, changeable until money is recorded."""

    profile = ClientProfile.get_active()
    lang = _lang(request)
    words = STRINGS[lang]
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    locked = has_financial_records()
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Changing the currency needs settings.manage_settings.")
        code = request.POST.get("currency", "")
        try:
            changed = change_company_currency(profile, code, user=request.user)
        except CurrencyChangeRefused:
            messages.error(request, words["no_profile"] if profile is None else words["currency_locked"])
        else:
            if changed:
                messages.success(request, words["currency_saved"].format(code=code))
        return redirect(f"{reverse('settings_core:currency')}?lang={lang}")
    return render(
        request,
        "settings_core/currency.html",
        _context(
            request,
            profile=profile,
            choices=currency_choices(lang),
            locked=locked,
            can_change=can_manage and profile is not None and not locked,
        ),
    )
