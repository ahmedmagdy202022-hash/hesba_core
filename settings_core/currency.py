"""SETTINGS-002: one currency per company, chosen by the owner.

Hesba records amounts without converting them, so the currency is a label on
every figure. Changing it after money has been recorded would relabel those
figures as another currency. The change is therefore allowed only before any
financial record exists; afterwards the screen explains why it is locked.
"""

from django.db import transaction

from audit.models import AuditEventType, AuditLog


# ISO 4217 codes offered in the picker, nearest markets first.
CURRENCIES = (
    ("EGP", {"ar": "جنيه مصري", "en": "Egyptian pound"}),
    ("SAR", {"ar": "ريال سعودي", "en": "Saudi riyal"}),
    ("AED", {"ar": "درهم إماراتي", "en": "UAE dirham"}),
    ("KWD", {"ar": "دينار كويتي", "en": "Kuwaiti dinar"}),
    ("QAR", {"ar": "ريال قطري", "en": "Qatari riyal"}),
    ("BHD", {"ar": "دينار بحريني", "en": "Bahraini dinar"}),
    ("OMR", {"ar": "ريال عماني", "en": "Omani rial"}),
    ("JOD", {"ar": "دينار أردني", "en": "Jordanian dinar"}),
    ("USD", {"ar": "دولار أمريكي", "en": "US dollar"}),
    ("EUR", {"ar": "يورو", "en": "Euro"}),
)
CURRENCY_CODES = tuple(code for code, _ in CURRENCIES)


class CurrencyChangeRefused(ValueError):
    pass


def currency_choices(lang="ar"):
    return [(code, f"{labels[lang]} ({code})") for code, labels in CURRENCIES]


def has_financial_records():
    """True once any amount has been recorded in the current currency."""

    from cashboxes.models import Cashbox, CashboxMovement
    from master_data.models import Customer, Supplier
    from purchases.models import PurchaseInvoice, SupplierPayment
    from sales.models import CustomerPayment, SalesInvoice

    if any(model.objects.exists() for model in (SalesInvoice, PurchaseInvoice, CustomerPayment, SupplierPayment, CashboxMovement)):
        return True
    return any(model.objects.exclude(opening_balance=0).exists() for model in (Customer, Supplier, Cashbox))


@transaction.atomic
def change_company_currency(profile, code, user=None):
    """Set the company currency and keep every (still unused) cashbox on it."""

    from cashboxes.models import Cashbox

    if profile is None:
        raise CurrencyChangeRefused("No client profile yet.")
    if code not in CURRENCY_CODES:
        raise CurrencyChangeRefused(f"Unsupported currency: {code!r}")
    if code == profile.default_currency:
        return False
    if has_financial_records():
        raise CurrencyChangeRefused("Amounts are already recorded in the current currency.")

    before = profile.default_currency
    profile.default_currency = code
    profile.save(update_fields=["default_currency", "updated_at"])
    Cashbox.objects.update(currency=code)
    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user if user is not None and user.is_authenticated else None,
        module="settings",
        action="change_currency",
        object_type="settings_core.ClientProfile",
        object_id=str(profile.pk),
        before_data={"default_currency": before},
        after_data={"default_currency": code},
        reason="Company currency changed from the settings screen before any financial record.",
    )
    return True
