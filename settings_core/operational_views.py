from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.models import Role
from permissions.services import user_has_permission

from .models import ClientProfile, SystemSetting
from .setup_services import ModuleChangeRefused, module_settings_rows, set_module_enabled
from . import capabilities as caps
from . import setup_catalog as catalog
from .currency import CurrencyChangeRefused, change_company_currency, currency_choices, has_financial_records


STRINGS = {
    "ar": {"page_title": "الإعدادات", "dashboard": "لوحة القيادة", "language": "English", "settings": "إعدادات التشغيل", "roles": "الأدوار والصلاحيات", "back": "العودة للإعدادات", "hidden": "قيمة حساسة مخفية",
           "modules": "الموديولات", "modules_lead": "دي الموديولات اللي تناسب نشاطك. شغّل أو اقفل أي موديول: القفل بيخفي الموديول ويقفل شاشاته بس، وبياناته بتفضل محفوظة زي ما هي.",
           "your_activity": "نشاطك", "inside": "فيه لنشاطك:", "open": "افتح", "features": "مميزات نشاطك", "features_lead": "شاشات جاية مع نشاطك نفسه، مش محتاجة تتشغّل.",
           "may_need": "ممكن تحتاجها", "change_activity": "النشاط بيتغيّر من إعداد النشاط.",
           "state_on": "مفعّل", "state_off": "مقفول", "state_required": "أساسي لنشاطك", "state_soon": "قريبًا",
           "turn_on": "تفعيل", "turn_off": "قفل", "view_only": "تقدر تشوف الموديولات بس؛ التغيير لصاحب الحساب.",
           "setup_first": "كمّل الإعداد الأول قبل ما تغيّر الموديولات.", "saved_on": "اتفعّل موديول «{module}».", "saved_off": "اتقفل موديول «{module}». بياناته محفوظة.", "refused": "التغيير ده مش مسموح: الموديول أساسي لنشاطك أو لسه مش متاح.",
           "currency": "العملة", "currency_lead": "حسبة بتستخدم عملة واحدة للشركة، وبتظهر جنب كل مبلغ.", "currency_field": "عملة الشركة", "save": "حفظ",
           "currency_locked": "العملة مقفولة لأن فيه مبالغ متسجلة بيها بالفعل (فواتير أو حركات خزنة أو أرصدة افتتاحية). حسبة مابتحوّلش المبالغ بين العملات، فتغييرها دلوقتي هيغيّر معنى الأرقام.", "currency_saved": "العملة اتغيرت لـ {code}.", "no_profile": "لسه مفيش بيانات شركة. كمّل الإعداد الأول.",
           "capabilities": "قدرات النشاط", "capabilities_lead": "طرق شغل بيحتاجها بعض الأنشطة بس. اللي عليه «مقترح» مناسب لنشاطك، وتقدر تشغّل أو تقفل أي حاجة في أي وقت من غير ما بيانات تتمسح.",
           "suggested": "مقترح لنشاطك", "cap_saved_on": "اتفعّلت «{capability}».", "cap_saved_off": "اتقفلت «{capability}». بياناتها محفوظة.", "cap_refused": "التغيير ده مش مسموح: القدرة لسه مش متاحة."},
    "en": {"page_title": "Settings", "dashboard": "Dashboard", "language": "العربية", "settings": "Operational settings", "roles": "Roles and permissions", "back": "Back to settings", "hidden": "Sensitive value hidden",
           "modules": "Modules", "modules_lead": "These are the modules that suit your activity. Switch any of them on or off: switching off only hides the module and closes its screens; its data is kept as it is.",
           "your_activity": "Your activity", "inside": "For your activity:", "open": "Open", "features": "Your activity's features", "features_lead": "Screens that come with your activity itself; nothing to switch on.",
           "may_need": "You may need", "change_activity": "The activity is changed from the activity setup.",
           "state_on": "On", "state_off": "Off", "state_required": "Required for your activity", "state_soon": "Coming soon",
           "turn_on": "Switch on", "turn_off": "Switch off", "view_only": "You can view modules; changing them is for the account owner.",
           "setup_first": "Finish setup before changing modules.", "saved_on": "The “{module}” module is on.", "saved_off": "The “{module}” module is off. Its data is kept.", "refused": "That change is not allowed: the module is required for your activity or not available yet.",
           "currency": "Currency", "currency_lead": "Hesba uses one currency for the company, shown beside every amount.", "currency_field": "Company currency", "save": "Save",
           "currency_locked": "The currency is locked because amounts are already recorded in it (invoices, cash movements or opening balances). Hesba does not convert amounts between currencies, so changing it now would change what the figures mean.", "currency_saved": "The currency is now {code}.", "no_profile": "There is no company profile yet. Finish setup first.",
           "capabilities": "Business capabilities", "capabilities_lead": "Ways of working that only some businesses need. Those marked “Suggested” suit your activity; switch anything on or off at any time without losing data.",
           "suggested": "Suggested for you", "cap_saved_on": "“{capability}” is on.", "cap_saved_off": "“{capability}” is off. Its data is kept.", "cap_refused": "That change is not allowed: the capability is not available yet."},
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": STRINGS[lang], "page_title": STRINGS[lang]["page_title"]}
    context.update(extra)
    return context


def lang_of(request):
    return "en" if (request.GET.get("lang") or request.POST.get("lang")) == "en" else "ar"


#: R2: the screen each feature opens on (a capability's paths are the prefixes
#: it owns, not always a page of their own).
FEATURE_LANDING = {
    "pos": "sales:pos", "barcode": "barcode:labels", "price_lists": "pricing:list", "units": "units:index",
    "variants": "variants:index", "batches_expiry": "batches:index", "serials": "serials:index",
    "installments": "installments:list", "vat": "taxes:settings", "e_invoice": "einvoice:issuer", "fixed_assets": "fixed_assets:list",
}


def _opens_for(user, url):
    """The landing page's own permission gate, read from its view, applied to ``user``."""

    from django.urls import resolve

    view = resolve(url.split("?")[0]).func
    needed = getattr(view, "required_permission", None)
    if needed:
        return user_has_permission(user, needed)
    any_of = getattr(view, "required_permissions", ())
    return not any_of or any(user_has_permission(user, code) for code in any_of)


def _features(lang, user):
    """R2: each optional feature in plain words: on or off, and where it lives once on.

    The Open link is left out when the viewer could not open that screen."""

    from .capabilities import CAPABILITIES, about, enabled_capabilities, fits

    on = set(enabled_capabilities())
    profile = ClientProfile.get_active()
    activity = profile.activity_slug if profile else ""
    rows = []
    for slug, info in CAPABILITIES.items():
        if not info.get("available"):
            continue
        if not fits(activity, slug) and slug not in on:  # AUDIT-3
            continue
        name = FEATURE_LANDING.get(slug)
        url = reverse(name) if name else ""
        rows.append({
            "slug": slug,
            "label": info["en" if lang == "en" else "ar"],
            "about": about(activity, slug, lang),
            "on": slug in on,
            "url": url if url and _opens_for(user, url) else "",
        })
    return rows


MONTHS = {
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
}


GROUP_LINKS = {
    "company": "settings_core:company", "entities": "entities:list", "currency": "settings_core:currency",
    "modules": "settings_core:modules", "capabilities": "settings_core:capabilities",
    "users": "settings_core:users", "roles": "settings_core:roles",
    "taxes": "taxes:settings", "einvoice": "einvoice:issuer",
    "backups": "settings_core:backup_key", "storage": "settings_core:storage", "imports": "imports:home",
    "daily_email": "settings_core:daily_email",
}


def _group_links(user):
    """{key: True} for each grouped settings link whose page ``user`` may open."""

    return {key: _opens_for(user, reverse(name)) for key, name in GROUP_LINKS.items()}


def _plain_profile(client, lang):
    """R2-10: the company's settings in words an owner reads, not field values."""

    if client is None:
        return None
    from . import setup_catalog as catalog
    from .setup_services import enabled_modules

    month = client.fiscal_year_start_month
    zones = {"Africa/Cairo": ("القاهرة", "Cairo")}
    zone = zones.get(client.timezone)
    return {
        "activity": catalog.activity_label(client.activity_slug, lang) if client.activity_slug else "",
        "sub_activity": catalog.sub_activity_label(client.activity_slug, client.sub_activity_slug, lang) if client.sub_activity_slug else "",
        "language": {"ar": "العربية", "en": "English"}.get(client.default_language, client.default_language),
        "timezone": (zone[1] if lang == "en" else zone[0]) if zone else client.timezone,
        # A month outside 1-12 (the field has no validator) is shown as stored, flagged, never wrapped.
        "fiscal_start": MONTHS["en" if lang == "en" else "ar"][month - 1] if month in range(1, 13) else "",
        "fiscal_bad": "" if month in range(1, 13) else str(month),
        "modules_on": len(enabled_modules()), "modules_total": len(catalog.MODULE_SLUGS),
    }


@require_permission("settings.view_settings")
def settings_overview(request):
    can_manage = user_has_permission(request.user, "settings.manage_settings") and request.user.is_superuser
    lang = lang_of(request)
    client = ClientProfile.get_active()
    features = _features(lang, request.user)
    return render(
        request,
        "settings_core/overview.html",
        _context(
            request,
            client=client,
            plain=_plain_profile(client, lang),
            opens=_group_links(request.user),
            features_on=sum(1 for feature in features if feature["on"]),
            settings=SystemSetting.objects.filter(active=True),
            features=features,
            can_manage=can_manage,
            admin_settings_url=reverse("admin:settings_core_clientprofile_changelist") if can_manage else "",
        ),
    )


#: AUDIT-1: the roles screen speaks in sections and plain names, never in permission codes.
PERMISSION_SECTIONS = {
    "sales": ("المبيعات", "Sales"), "purchases": ("المشتريات", "Purchases"), "inventory": ("المخزون", "Inventory"),
    "master_data": ("البيانات الأساسية", "Master data"), "cashboxes": ("الخزائن والمصروفات", "Cash and expenses"),
    "reports": ("التقارير", "Reports"), "accounting": ("الحسابات العامة", "Accounting"), "closing": ("إقفال الفترات", "Period closing"),
    "settings": ("الإعدادات", "Settings"), "permissions": ("الصلاحيات", "Permissions"), "audit": ("سجل المراجعة", "Audit log"),
    "imports": ("الاستيراد", "Imports"), "medical": ("الملفات الطبية", "Medical records"), "barcode": ("الباركود", "Barcodes"),
    "restaurant": ("الطاولات والطلبات", "Tables & orders"), "projects": ("المشاريع", "Projects"),
}
ROLE_ABOUT = {
    "owner": ("كل حاجة: الأرقام والإعدادات والصلاحيات والإقفال.", "Everything: figures, settings, permissions and closing."),
    "manager": ("الشغل اليومي كله: بيع وشراء ومخزون وتقارير، من غير الإقفال والصلاحيات.", "All the daily work: selling, buying, stock and reports, without closing and permissions."),
    "accountant": ("الحسابات والتحصيل والدفع والمصروفات والتقارير المالية.", "Accounts, collections, payments, expenses and financial reports."),
    "cashier": ("البيع والتحصيل من العملاء وخزنة اليوم.", "Selling, collecting from customers and the day's cashbox."),
    "stock_keeper": ("المخزون والمخازن والاستلام من الموردين.", "Stock, warehouses and receiving from suppliers."),
    "support": ("الدعم الفني: يشوف الإعدادات من غير ما يغيّر في الأرقام.", "Technical support: sees settings without touching the figures."),
    "waiter": ("الطاولات والطلبات والمطبخ، من غير ما يحاسب أو يشوف الفواتير والخزنة.", "Tables, orders and the kitchen, without billing or seeing invoices and the cashbox."),
    "site_engineer": ("المشاريع والمقايسة، الكميات المنفذة في مستخلص مسودة، وصرف الخامات للموقع.",
                      "Projects and the bill of quantities, quantities done on a draft certificate, materials issued to the site."),
}


def _role_sections(role, lang):
    sections = {}
    for grant in role.rolepermission_set.all():
        permission = grant.permission
        if not (grant.allow and permission.active):
            continue
        ar, en = PERMISSION_SECTIONS.get(permission.module, (permission.module, permission.module))
        name = (permission.name_en or permission.name_ar) if lang == "en" else permission.name_ar
        sections.setdefault(en if lang == "en" else ar, []).append(name)
    return [{"name": name, "items": sorted(items)} for name, items in sections.items()]


@require_permission("settings.view_settings")
def role_list(request):
    can_manage = user_has_permission(request.user, "permissions.manage_roles") and request.user.is_superuser
    lang = lang_of(request)
    roles = list(Role.objects.filter(active=True).prefetch_related("rolepermission_set__permission"))
    for role in roles:
        role.sections = _role_sections(role, lang)
        about = ROLE_ABOUT.get(role.code)
        role.about = (about[1] if lang == "en" else about[0]) if about else ""
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
    rows = module_settings_rows(profile, lang) if profile is not None else []
    return render(
        request,
        "settings_core/modules.html",
        _context(
            request,
            rows=rows,
            groups=_groups(rows, lang),
            activity_name=_activity_name(profile, lang),
            setup_complete=profile is not None and profile.setup_is_complete,
            can_manage=can_manage,
        ),
    )


#: ACT-FIT: how the modules screen groups what fits the activity.
GROUP_TITLES = {
    "ar": {"required": "أساسي لنشاطك", "suggested": "مقترح لنشاطك", "optional": "ممكن تحتاجه", "extra": "مش من موديولات نشاطك (شغال حاليًا)"},
    "en": {"required": "Core for your activity", "suggested": "Suggested for your activity", "optional": "You may need", "extra": "Not one of your activity's modules (on now)"},
}


def _groups(rows, lang):
    titles = GROUP_TITLES["en" if lang == "en" else "ar"]
    return [{"key": key, "title": titles[key], "rows": [row for row in rows if row["group"] == key]}
            for key in ("required", "suggested", "optional", "extra") if any(row["group"] == key for row in rows)]


def _activity_name(profile, lang):
    if profile is None or not profile.activity_slug:
        return ""
    name = catalog.activity_label(profile.activity_slug, lang)
    if profile.sub_activity_slug and catalog.is_valid_sub_activity(profile.activity_slug, profile.sub_activity_slug):
        name = f"{name} — {catalog.sub_activity_label(profile.activity_slug, profile.sub_activity_slug, lang)}"
    return name


@require_permission("settings.view_settings")
def capability_settings(request):
    """CAP-001: switch business capabilities on and off after setup."""

    profile = ClientProfile.get_active()
    lang = _lang(request)
    words = STRINGS[lang]
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Changing capabilities needs settings.manage_settings.")
        slug = request.POST.get("capability", "")
        enabled = request.POST.get("enabled") == "1"
        try:
            changed = caps.set_capability_enabled(profile, slug, enabled, user=request.user)
        except caps.CapabilityChangeRefused:
            messages.error(request, words["setup_first"] if profile is None or not profile.setup_is_complete else words["cap_refused"])
        else:
            if changed:
                key = "cap_saved_on" if enabled else "cap_saved_off"
                messages.success(request, words[key].format(capability=caps.label(slug, lang)))
                note = caps.CAPABILITIES[slug].get(f"note_{lang}")
                if note:
                    messages.warning(request, note)
        return redirect(f"{reverse('settings_core:capabilities')}?lang={lang}")
    from . import activity_fit

    rows = caps.settings_rows(profile, lang)
    return render(
        request,
        "settings_core/capabilities.html",
        _context(
            request,
            rows=rows,
            suggested_rows=[row for row in rows if row["suggested"]],
            other_rows=[row for row in rows if not row["suggested"]],
            activity_features=activity_fit.features(profile.activity_slug, profile.sub_activity_slug, lang) if profile is not None else [],
            activity_name=_activity_name(profile, lang),
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
