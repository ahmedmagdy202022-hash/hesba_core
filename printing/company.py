"""What the printed documents say about the business itself.

Stored as plain SystemSetting rows (keys below), so no schema change is needed
and the owner edits them from Settings -> Company details.
"""

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
    }


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
