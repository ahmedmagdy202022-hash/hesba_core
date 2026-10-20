"""SETTINGS-DESIGN: what every settings screen shares — who the business is, and where everything else is.

Ahmed, 20 Oct: "شاشات الإعدادات تقليدية جدا كشكل ومفهاش روح". Each settings screen used to stand alone
behind a "back to settings" link. They now share one frame (templates/settings_core/base.html):
the business's own card at the top, and beside it the map of every settings section with what it
says today (4 of 15 modules on, the backup key not made yet, 12% of the space used), so the owner
moves between them without going back, and sees what still needs doing.

Read only: nothing here changes a setting.
"""

from django.urls import reverse

from permissions.services import user_has_permission

WORDS = {
    "ar": {
        "groups": {"business": "نشاطك", "work": "طريقة الشغل", "people": "الناس", "data": "بياناتك"},
        "overview": "نظرة عامة", "company": "بيانات الشركة", "entities": "الفروع", "currency": "العملة",
        "modules": "أقسام البرنامج", "capabilities": "خصائص الشغل", "taxes": "الضرايب", "einvoice": "الفاتورة الإلكترونية",
        "users": "المستخدمون", "roles": "الأدوار والصلاحيات", "backups": "النسخ الاحتياطي", "storage": "المساحة",
        "imports": "استيراد البيانات", "daily_email": "الإيميل اليومي",
        "complete": "كاملة", "missing": "ناقص {what}", "phone": "التليفون", "address": "العنوان", "tax_number": "الرقم الضريبي",
        "of": "{on} من {total} شغّال", "on_n": "{n} شغّالة", "paused_n": "{n} واقفة", "users_n": "{n} مستخدم", "roles_n": "{n} دور",
        "vat_on": "شغّالة", "vat_off": "مش شغّالة", "key_on": "متأمّنة", "key_off": "مفيش مفتاح لسه", "drive_on": "على Google Drive",
        "used": "{p}% مستخدمة", "email_on": "شغّال {when}", "email_off": "مش شغّال", "lang_ar": "العربية", "lang_en": "English",
        "fiscal": "السنة المالية من {month}", "edit": "تعديل بيانات الشركة", "menu": "أقسام الإعدادات",
    },
    "en": {
        "groups": {"business": "Your business", "work": "How it works", "people": "People", "data": "Your data"},
        "overview": "Overview", "company": "Company details", "entities": "Branches", "currency": "Currency",
        "modules": "Modules", "capabilities": "Features", "taxes": "Taxes", "einvoice": "E-invoicing",
        "users": "Users", "roles": "Roles & permissions", "backups": "Backups", "storage": "Storage",
        "imports": "Import data", "daily_email": "Daily email",
        "complete": "Complete", "missing": "Missing {what}", "phone": "phone", "address": "address", "tax_number": "tax number",
        "of": "{on} of {total} on", "on_n": "{n} on", "paused_n": "{n} paused", "users_n": "{n} users", "roles_n": "{n} roles",
        "vat_on": "On", "vat_off": "Off", "key_on": "Encrypted", "key_off": "No key yet", "drive_on": "On Google Drive",
        "used": "{p}% used", "email_on": "On {when}", "email_off": "Off", "lang_ar": "العربية", "lang_en": "English",
        "fiscal": "Financial year from {month}", "edit": "Edit company details", "menu": "Settings sections",
    },
}

#: (key, group, url name, icon). Order is the menu's order.
SECTIONS = (
    ("overview", "business", "settings_core:overview", "settings"),
    ("company", "business", "settings_core:company", "company"),
    ("entities", "business", "entities:list", "branch"),
    ("currency", "business", "settings_core:currency", "currency"),
    ("modules", "work", "settings_core:modules", "modules"),
    ("capabilities", "work", "settings_core:capabilities", "features"),
    ("taxes", "work", "taxes:settings", "taxes"),
    ("einvoice", "work", "einvoice:issuer", "einvoice"),
    ("users", "people", "settings_core:users", "users"),
    ("roles", "people", "settings_core:roles", "roles"),
    ("backups", "data", "settings_core:backup_key", "backup"),
    ("storage", "data", "settings_core:storage", "storage"),
    ("imports", "data", "imports:home", "import"),
    ("daily_email", "data", "settings_core:daily_email", "mail"),
)
#: Screens that belong to a section without being its landing page.
ACTIVE_ALIASES = {"role_new": "roles", "role_edit": "roles", "user_create": "users", "user_edit": "users",
                  "user_reset_password": "users", "user_reset_two_factor": "users", "backup_drive_callback": "backups"}
#: A section that belongs to a capability shows only while that capability is on (its screens close otherwise).
NEEDS_CAPABILITY = {"taxes": "vat", "einvoice": "e_invoice"}


def _words(lang):
    return WORDS["en" if lang == "en" else "ar"]


def _opens(user, url):
    from .operational_views import _opens_for

    try:
        return _opens_for(user, url)
    except Exception:  # a section whose app is not installed in this build
        return False


def _company_state(words):
    from printing.company import company_details

    details = company_details()
    missing = [words[key] for key in ("phone", "address", "tax_number") if not details.get(key)]
    return details, (words["missing"].format(what="، ".join(missing) if words is WORDS["ar"] else ", ".join(missing)) if missing else words["complete"]), not missing


def status_of(key, words, lang, cache):
    """(text, tone) for one section: tone is "ok", "todo" or "" (nothing to say)."""

    from . import capabilities as caps

    if key == "company":
        _, text, done = cache.setdefault("company", _company_state(words))
        return text, "ok" if done else "todo"
    if key == "currency":
        from .currency import CURRENCIES
        from .models import ClientProfile

        profile = ClientProfile.get_active()
        code = profile.default_currency if profile else ""
        return dict(CURRENCIES).get(code, {}).get("en" if lang == "en" else "ar", code), ""
    if key == "modules":
        from .models import ClientProfile
        from .setup_services import module_settings_rows

        rows = module_settings_rows(ClientProfile.get_active(), lang)
        live = [row for row in rows if row["state"] in ("on", "off", "required")]
        on = sum(1 for row in live if row["state"] in ("on", "required"))
        return words["of"].format(on=on, total=len(live)), ""
    if key == "capabilities":
        switched, working = caps.switched_on(), caps.enabled_capabilities()
        paused = len(switched) - len(working)
        if paused:
            return words["paused_n"].format(n=paused), "todo"
        return words["on_n"].format(n=len(working)), ""
    if key in ("taxes", "einvoice"):
        return (words["vat_on"], "ok") if caps.capability_enabled(NEEDS_CAPABILITY[key]) else (words["vat_off"], "")
    if key == "users":
        from django.contrib.auth import get_user_model

        return words["users_n"].format(n=get_user_model().objects.filter(is_active=True).count()), ""
    if key == "roles":
        from permissions.models import Role

        return words["roles_n"].format(n=Role.objects.filter(active=True).count()), ""
    if key == "backups":
        from .backup_crypto import configured_public_key
        from .drive_backup import is_connected

        if configured_public_key() is None:
            return words["key_off"], "todo"
        return (words["drive_on"] if is_connected() else words["key_on"]), "ok"
    if key == "storage":
        from .storage import database_size_bytes, level_for, limit_bytes

        used, limit = database_size_bytes(), limit_bytes()
        if used is None or not limit:
            return "", ""
        level = level_for(used, limit)
        return words["used"].format(p=int(used * 100 / limit)), "todo" if level == "red" else ("" if level == "green" else "todo")
    if key == "daily_email":
        from .daily_email import config

        conf = config()
        if not conf["slots"]:
            return words["email_off"], ""
        when = "، ".join(f"{conf['hours'][slot]:02d}:00" for slot in conf["slots"])
        return words["email_on"].format(when=when), "ok"
    return "", ""


def sections(user, lang, active=""):
    """The menu: [{key, label, url, icon, status, tone, active}] by group, only what ``user`` may open."""

    from . import capabilities as caps

    words = _words(lang)
    cache, groups = {}, []
    for key, group, name, icon in SECTIONS:
        if key in NEEDS_CAPABILITY and not caps.capability_enabled(NEEDS_CAPABILITY[key]):
            continue  # its screens are closed; the features screen is where it is switched on
        try:
            url = reverse(name)
        except Exception:
            continue
        if not _opens(user, url):
            continue
        text, tone = status_of(key, words, lang, cache) if key != "overview" else ("", "")
        row = {"key": key, "label": words[key], "url": url, "icon": icon, "status": text, "tone": tone, "active": key == active}
        if not groups or groups[-1]["key"] != group:
            groups.append({"key": group, "title": words["groups"][group], "rows": []})
        groups[-1]["rows"].append(row)
    return groups


def identity(lang):
    """The business card at the top: name, logo or initial, activity, and its everyday facts."""

    from printing.company import company_details

    from . import setup_catalog as catalog
    from .models import ClientProfile

    words = _words(lang)
    profile = ClientProfile.get_active()
    if profile is None:
        return None
    details = company_details()
    name = profile.display_name or profile.legal_name
    activity = catalog.activity_label(profile.activity_slug, lang) if profile.activity_slug else ""
    if profile.sub_activity_slug and catalog.is_valid_sub_activity(profile.activity_slug, profile.sub_activity_slug):
        activity = f"{activity} · {catalog.sub_activity_label(profile.activity_slug, profile.sub_activity_slug, lang)}"
    from .currency import CURRENCIES
    from .operational_views import MONTHS

    month = profile.fiscal_year_start_month
    facts = [dict(CURRENCIES).get(profile.default_currency, {}).get("en" if lang == "en" else "ar", profile.default_currency),
             words["lang_en"] if profile.default_language == "en" else words["lang_ar"]]
    if month in range(1, 13):
        facts.append(words["fiscal"].format(month=MONTHS["en" if lang == "en" else "ar"][month - 1]))
    return {"name": name, "initial": (name or "ح").strip()[:1], "logo": details.get("logo", ""), "activity": activity, "facts": facts}


def active_section(request):
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    name = match.url_name or ""
    for key, _, url_name, _ in SECTIONS:
        if url_name == f"{match.namespace}:{name}":
            return key
    return ACTIVE_ALIASES.get(name, "") if match.namespace == "settings_core" else ""


def readiness(user, lang):
    """SETTINGS-DESIGN: what makes the account ready, done or not: [{key, label, done, url}]."""

    from django.contrib.auth import get_user_model

    from .backup_crypto import configured_public_key
    from .daily_email import config

    ar = lang != "en"
    details, _, details_done = _company_state(_words(lang))
    steps = [
        ("company", "اكتب تليفون الشركة وعنوانها ورقمها الضريبي" if ar else "Add the company's phone, address and tax number",
         details_done, "settings_core:company", "settings.manage_settings"),
        ("logo", "ارفع اللوجو، علشان يطلع على الفواتير" if ar else "Upload the logo, so it prints on invoices",
         bool(details.get("logo")), "settings_core:company", "settings.manage_settings"),
        ("team", "ضيف حد من فريقك بدور يناسبه" if ar else "Add someone from your team with a fitting role",
         get_user_model().objects.filter(is_active=True).count() > 1, "settings_core:users", "permissions.manage_roles"),
        ("backup", "اعمل مفتاح النسخ الاحتياطي، علشان نسخ بياناتك تتشفّر" if ar else "Make the backup key, so your backups are encrypted",
         configured_public_key() is not None, "settings_core:backup_key", "settings.manage_settings"),
        ("daily_email", "شغّل إيميل المتابعة اليومية" if ar else "Switch on the daily follow-up email",
         bool(config()["slots"]), "settings_core:daily_email", "settings.manage_settings"),
    ]
    rows = []
    for key, label, done, name, permission in steps:
        if not user_has_permission(user, permission) and not done:
            continue
        rows.append({"key": key, "label": label, "done": done, "url": reverse(name)})
    return {"rows": rows, "done": sum(1 for row in rows if row["done"]), "total": len(rows),
            "percent": int(100 * sum(1 for row in rows if row["done"]) / len(rows)) if rows else 100}
