"""What the printed documents say about the business itself.

PRINT-002: the owner's own logo is kept as a small data URI in a
SystemSetting (``company.logo``), so it lives in the client's database, moves
with its backups and needs no media storage. Only PNG, JPEG and WebP up to
300 KB are accepted, checked by their file signature, never by the name.

Stored as plain SystemSetting rows (keys below), so no schema change is needed
and the owner edits them from Settings -> Company details.
"""

import base64

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from settings_core.models import ClientProfile, SystemSetting


FIELDS = (
    # key, arabic label, english label, multiline
    ("company.phone", "التليفون", "Phone", False),
    ("company.address", "العنوان", "Address", True),
    ("company.tax_number", "الرقم الضريبي", "Tax registration number", False),
    ("company.commercial_register", "السجل التجاري", "Commercial register", False),
    ("print.footer_note", "ملاحظة أسفل الفاتورة", "Note at the bottom of documents", True),
)
KEYS = tuple(field[0] for field in FIELDS)


def company_details():
    values = dict(SystemSetting.objects.filter(key__in=KEYS, active=True).values_list("key", "value"))
    profile = ClientProfile.get_active()
    return {
        "name": (profile.display_name or profile.legal_name) if profile else "حِسبة",
        "legal_name": profile.legal_name if profile else "",
        "currency": (profile.default_currency if profile else "") or "EGP",
        "phone": values.get("company.phone", ""),
        "address": values.get("company.address", ""),
        "tax_number": values.get("company.tax_number", ""),
        "commercial_register": values.get("company.commercial_register", ""),
        "footer_note": values.get("print.footer_note", ""),
        "logo": _setting_value(LOGO_KEY),
        "show_hesba_brand": _setting_value(BRAND_KEY) != "0",
    }


LOGO_KEY = "company.logo"
BRAND_KEY = "print.show_hesba_brand"
MAX_LOGO_BYTES = 300 * 1024
SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def _setting_value(key):
    return SystemSetting.objects.filter(key=key, active=True).values_list("value", flat=True).first() or ""


def logo_mime(data):
    for signature, mime in SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _audit(user, action, before, after):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="settings", action=action,
                            object_type="SystemSetting", object_id="company", before_data=before, after_data=after)


@transaction.atomic
def save_logo(data, user, lang="ar"):
    if len(data) > MAX_LOGO_BYTES:
        raise ValidationError("حجم اللوجو لازم أقل من 300 كيلوبايت." if lang != "en" else "The logo must be under 300 KB.")
    mime = logo_mime(data)
    if mime is None:
        raise ValidationError("اللوجو لازم يكون صورة PNG أو JPG أو WebP." if lang != "en" else "The logo must be a PNG, JPG or WebP image.")
    had = bool(_setting_value(LOGO_KEY))
    SystemSetting.objects.update_or_create(key=LOGO_KEY, defaults={"value": f"data:{mime};base64,{base64.b64encode(data).decode()}", "active": True,
                                                                  "description": "Logo printed on invoices and receipts"})
    _audit(user, "update_company_logo", {"logo": had}, {"logo": True, "type": mime, "bytes": len(data)})


@transaction.atomic
def remove_logo(user):
    if SystemSetting.objects.filter(key=LOGO_KEY).exclude(value="").exists():
        SystemSetting.objects.filter(key=LOGO_KEY).update(value="")
        _audit(user, "remove_company_logo", {"logo": True}, {"logo": False})


@transaction.atomic
def set_hesba_brand(show, user):
    before = _setting_value(BRAND_KEY) != "0"
    if before == show:
        return
    SystemSetting.objects.update_or_create(key=BRAND_KEY, defaults={"value": "1" if show else "0", "active": True, "description": "Show the Hesba mark at the foot of documents"})
    _audit(user, "set_print_brand", {"show_hesba_brand": before}, {"show_hesba_brand": show})


@transaction.atomic
def save_company_details(values, user):
    before, after = {}, {}
    for key in KEYS:
        new = (values.get(key) or "").strip()
        setting = SystemSetting.objects.filter(key=key).first()
        old = setting.value if setting else ""
        if new == old:
            continue
        before[key], after[key] = old, new
        SystemSetting.objects.update_or_create(key=key, defaults={"value": new, "active": True, "description": "Printed on invoices and receipts"})
    if after:
        AuditLog.objects.create(
            event_type=AuditEventType.UPDATE,
            actor=user,
            module="settings",
            action="update_company_details",
            object_type="SystemSetting",
            object_id="company",
            before_data=before,
            after_data=after,
        )
    return bool(after)
