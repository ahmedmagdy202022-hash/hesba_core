"""CAP-001: business capabilities, suggested per activity and switchable later.

A *module* is a whole section of Hesba (sales, purchases, inventory…). A
*capability* is a way of working inside those sections that only some shops
need: a quick till, barcode labels, wholesale price lists, sizes and colours,
batches and expiry dates, serial numbers, instalments, e-invoicing…

Hesba does not ship one program per trade. Every capability is built once, for
every commercial activity, and the chosen sub-activity only decides which ones
start switched on (``SUGGESTED``). The owner confirms or changes them at the
end of setup and can switch any of them later from Settings; nothing is
deleted when one is switched off.

Capabilities marked ``available=False`` are on the roadmap: the catalog lists
them so setup can show what is coming, but they cannot be switched on and have
no screens yet.

Storage: one ``FeatureFlag`` row per capability, ``capability.<slug>``. An
installation made before capabilities existed has no rows; there the ones it
already had (``legacy_on``) count as on, so upgrading never hides a screen
someone uses, and newer ones stay off until switched on.
"""

from django.db import transaction

from audit.models import AuditEventType, AuditLog

from . import setup_catalog as catalog
from .models import ClientProfile, FeatureFlag


FLAG_PREFIX = "capability."
SUGGESTED = catalog.SUGGESTED
OPTIONAL = catalog.OPTIONAL

#: Declaration order is display order. ``paths`` are the URL prefixes the
#: capability owns; they close when it is off (see module_gate).
CAPABILITIES = {
    "pos": {
        "ar": "الكاشير السريع", "en": "Quick till (POS)",
        "about_ar": "شاشة بيع سريعة بالباركود، وحساب الباقي، وإيصال 80mm.",
        "about_en": "A fast sales screen with barcode scanning, change due and an 80mm receipt.",
        "available": True, "legacy_on": True, "paths": ("/sales/pos/",),
    },
    "barcode": {
        "ar": "الباركود والملصقات", "en": "Barcodes & labels",
        "about_ar": "توليد باركود للأصناف وطباعة ملصقات الأسعار.",
        "about_en": "Generate item barcodes and print price labels.",
        "available": True, "legacy_on": True, "paths": ("/barcode/",),
    },
    "price_lists": {
        "ar": "قوائم الأسعار (قطاعي / جملة)", "en": "Price lists (retail / wholesale)",
        "about_ar": "أكتر من سعر للصنف، وسعر خاص لكل عميل أو فئة عملاء.",
        "about_en": "More than one price per item, and special prices per customer or customer group.",
        "available": True, "paths": ("/pricing/",),
    },
    "units": {
        "ar": "وحدات القياس", "en": "Units of measure",
        "about_ar": "بيع وشراء بالكرتونة أو العلبة أو القطعة مع تحويل تلقائي.",
        "about_en": "Buy and sell by carton, box or piece with automatic conversion.",
        "available": True, "paths": ("/units/",),
    },
    "variants": {
        "ar": "المقاسات والألوان", "en": "Sizes & colours",
        "about_ar": "صنف واحد بمقاسات وألوان، وكل واحد له مخزونه وباركوده.",
        "about_en": "One item in several sizes and colours, each with its own stock and barcode.",
        "available": True, "paths": ("/variants/",),
    },
    "batches_expiry": {
        "ar": "التشغيلات وتاريخ الصلاحية", "en": "Batches & expiry dates",
        "about_ar": "متابعة كل تشغيلة وتاريخ انتهائها، وتنبيه قبل الانتهاء.",
        "about_en": "Track each batch and its expiry date, with alerts before it expires.",
        "available": True, "paths": ("/batches/",),
    },
    "serials": {
        "ar": "السيريال / IMEI والضمان", "en": "Serial / IMEI & warranty",
        "about_ar": "تتبّع كل قطعة برقمها من الشراء للبيع، وفترة الضمان.",
        "about_en": "Track every unit by its number from purchase to sale, with its warranty.",
        "available": True, "paths": ("/serials/",),
    },
    "installments": {
        "ar": "البيع بالتقسيط", "en": "Instalment sales",
        "about_ar": "جدول أقساط للعميل، ومواعيد استحقاق، وتذكير بالتحصيل.",
        "about_en": "An instalment schedule per customer, due dates and collection reminders.",
        "available": True, "paths": ("/instalments/",),
    },
    "vat": {
        "ar": "ضريبة القيمة المضافة على الأصناف", "en": "VAT on items",
        "about_ar": "نسبة ضريبة لكل صنف (14٪ أو معفى)، تتحسب تلقائيًا في الفاتورة والكاشير، وتقرير ضريبة المبيعات.",
        "about_en": "A tax rate per item (14% or exempt), charged automatically on invoices and at the till, with a sales VAT report.",
        "note_ar": "الأفضل تشغّلها أو تقفلها من أول الشهر: الفواتير اللي اتعملت قبل التغيير بتفضل بالضريبة اللي اتحسبت وقتها، فالإقرار بتاع الشهر يبقى مخلوط.",
        "note_en": "Best switched on or off at the start of a month: invoices made before the change keep the tax they were charged, so that month's VAT return would be mixed.",
        "available": True, "paths": ("/taxes/",),
    },
    "e_invoice": {
        "ar": "الفاتورة الإلكترونية (مصلحة الضرائب)", "en": "E-invoicing (Egyptian Tax Authority)",
        "about_ar": "بيانات الشركة والعملاء وأكواد الأصناف، وملف كل فاتورة بصيغة المنظومة مع فحص الناقص. التوقيع والإرسال مرحلة جاية.",
        "about_en": "Company, customer and item-code data, and each invoice's document in the portal format with a check of what is missing. Signing and sending come next.",
        "available": True, "paths": ("/einvoice/",),
    },
    "fixed_assets": {
        "ar": "الأصول الثابتة والإهلاك", "en": "Fixed assets & depreciation",
        "about_ar": "سجل للأصول (عربيات، أجهزة، ديكور) وإهلاك شهري يدخل في صافي الربح.",
        "about_en": "An asset register (vehicles, equipment, fit-out) with monthly depreciation in net profit.",
        "available": False, "paths": (),
    },
}
CAPABILITY_SLUGS = tuple(CAPABILITIES)

#: Which capabilities start on for each commercial sub-activity. Anything not
#: listed is OPTIONAL. Services activities suggest none of the trade ones.
PRESETS = {
    "retail": {"pos": SUGGESTED, "barcode": SUGGESTED},
    "grocery": {"pos": SUGGESTED, "barcode": SUGGESTED, "units": SUGGESTED, "batches_expiry": SUGGESTED},
    "fashion": {"pos": SUGGESTED, "barcode": SUGGESTED, "variants": SUGGESTED},
    "electronics": {"pos": SUGGESTED, "barcode": SUGGESTED, "serials": SUGGESTED, "installments": SUGGESTED},
    "pharmacy": {"pos": SUGGESTED, "barcode": SUGGESTED, "batches_expiry": SUGGESTED, "units": SUGGESTED},
    "wholesale": {"barcode": SUGGESTED, "price_lists": SUGGESTED, "units": SUGGESTED, "vat": SUGGESTED, "e_invoice": SUGGESTED},
    "online": {"barcode": SUGGESTED, "variants": SUGGESTED},
    "other": {"pos": SUGGESTED, "barcode": SUGGESTED},
}


def flag_code(slug):
    return f"{FLAG_PREFIX}{slug}"


def label(slug, lang="ar"):
    entry = CAPABILITIES.get(slug) or {}
    return entry.get(lang) or catalog.fallback_label(slug)


def is_available(slug):
    return bool(CAPABILITIES.get(slug, {}).get("available"))


def preset_state(activity, sub_activity, slug):
    if activity != catalog.COMMERCIAL:
        return OPTIONAL
    return PRESETS.get(sub_activity, {}).get(slug, OPTIONAL)


def suggested(activity, sub_activity):
    """Every capability the activity suggests, shipped or not, in display order."""

    return tuple(slug for slug in CAPABILITY_SLUGS if preset_state(activity, sub_activity, slug) == SUGGESTED)


def default_selection(activity, sub_activity):
    """What setup pre-ticks: the suggested capabilities that exist today."""

    return tuple(slug for slug in suggested(activity, sub_activity) if is_available(slug))


def parse(raw):
    """A submitted comma-separated list, keeping only switchable capabilities."""

    submitted = {part.strip() for part in (raw or "").split(",") if part.strip()}
    return tuple(slug for slug in CAPABILITY_SLUGS if slug in submitted and is_available(slug))


def enabled_capabilities():
    rows = dict(FeatureFlag.objects.filter(code__startswith=FLAG_PREFIX).values_list("code", "enabled"))
    return tuple(
        slug for slug in CAPABILITY_SLUGS
        # No row yet: an installation from before CAP-001, where the
        # capabilities it already had (legacy_on) stay on and new ones stay off.
        if is_available(slug) and rows.get(flag_code(slug), bool(CAPABILITIES[slug].get("legacy_on")))
    )


def capability_enabled(slug):
    return slug in enabled_capabilities()


def closed_capability(path):
    """The capability that owns `path` and is switched off, else None."""

    owner = next((slug for slug in CAPABILITY_SLUGS for prefix in CAPABILITIES[slug]["paths"] if path.startswith(prefix)), None)
    if owner is None:
        return None
    profile = ClientProfile.get_active()
    if profile is None or not profile.setup_is_complete:
        return None
    return None if capability_enabled(owner) else owner


def _write(slug, enabled):
    FeatureFlag.objects.update_or_create(
        code=flag_code(slug),
        defaults={"name": label(slug, "en"), "description": label(slug, "ar"), "enabled": enabled},
    )


def apply_setup_choice(activity, sub_activity, raw=None):
    """Store the capabilities chosen at setup. ``raw=None`` means use the preset."""

    chosen = set(default_selection(activity, sub_activity) if raw is None else parse(raw))
    for slug in CAPABILITY_SLUGS:
        if is_available(slug):
            _write(slug, slug in chosen)
    return tuple(slug for slug in CAPABILITY_SLUGS if slug in chosen)


class CapabilityChangeRefused(ValueError):
    """A capability switch that is not allowed."""


@transaction.atomic
def set_capability_enabled(profile, slug, enabled, user=None):
    if profile is None or not profile.setup_is_complete:
        raise CapabilityChangeRefused("Finish setup before changing capabilities.")
    if slug not in CAPABILITIES:
        raise CapabilityChangeRefused(f"Unknown capability: {slug!r}")
    if not is_available(slug):
        raise CapabilityChangeRefused(f"{slug} is not available yet.")
    was = capability_enabled(slug)
    if was == enabled:
        return False
    _write(slug, enabled)
    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user if user is not None and user.is_authenticated else None,
        module="settings",
        action="enable_capability" if enabled else "disable_capability",
        object_type="settings_core.FeatureFlag",
        object_id=flag_code(slug),
        before_data={"enabled": was},
        after_data={"enabled": enabled},
        reason="Capability switched from the settings screen.",
    )
    return True


def settings_rows(profile, lang="ar"):
    enabled = set(enabled_capabilities())
    activity = profile.activity_slug if profile else ""
    sub_activity = profile.sub_activity_slug if profile else ""
    rows = []
    for slug in CAPABILITY_SLUGS:
        entry = CAPABILITIES[slug]
        state = "soon" if not entry["available"] else ("on" if slug in enabled else "off")
        rows.append({
            "slug": slug,
            "label": entry[lang],
            "about": entry[f"about_{lang}"],
            "state": state,
            "suggested": preset_state(activity, sub_activity, slug) == SUGGESTED,
            "note": entry.get(f"note_{lang}", ""),
        })
    return rows
