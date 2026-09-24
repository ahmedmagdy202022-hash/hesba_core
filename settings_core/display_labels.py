"""Arabic display labels for model choice fields.

The TextChoices labels on the models are English ("Sale out", "Posted"), and
templates rendered them through get_FOO_display regardless of the screen's
language. Changing the model labels would be a schema change (Django records
choices in migrations) on protected models, so translation happens here, at the
display layer, keyed by the stored value.

English keeps the model's own label. Arabic comes from AR_LABELS; a value with
no Arabic entry falls back to the English label rather than to nothing, and
tests_display_labels.py fails CI the moment a mapped field gains a choice this
module does not translate.
"""

from django.apps import apps


DOCUMENT_STATUS = {"draft": "مسودة", "posted": "مُرحّل", "cancelled": "ملغى"}
PAYMENT_STATUS = {"credit": "آجل", "partial": "مدفوع جزئيًا", "paid": "مدفوع"}
FREQUENCY = {
    "monthly": "شهري",
    "quarterly": "ربع سنوي",
    "semi_annual": "نصف سنوي",
    "annual": "سنوي",
}

AR_LABELS = {
    ("sales.salesinvoice", "status"): DOCUMENT_STATUS,
    ("sales.salesinvoice", "payment_status"): PAYMENT_STATUS,
    ("sales.salesreturn", "status"): DOCUMENT_STATUS,
    ("sales.customerpayment", "status"): DOCUMENT_STATUS,
    ("sales.customerledgerentry", "entry_type"): {
        "sales_due": "مستحق مبيعات",
        "customer_payment": "تحصيل من عميل",
        "sales_return": "مرتجع مبيعات",
        "opening_balance": "رصيد افتتاحي",
        "adjustment": "تسوية",
    },
    ("purchases.purchaseinvoice", "status"): DOCUMENT_STATUS,
    ("purchases.purchaseinvoice", "payment_status"): PAYMENT_STATUS,
    ("purchases.purchasereturn", "status"): DOCUMENT_STATUS,
    ("purchases.supplierpayment", "status"): DOCUMENT_STATUS,
    ("purchases.supplierledgerentry", "entry_type"): {
        "purchase_due": "مستحق مشتريات",
        "supplier_payment": "سداد لمورد",
        "purchase_return": "مرتجع مشتريات",
        "opening_balance": "رصيد افتتاحي",
        "adjustment": "تسوية",
    },
    ("inventory.stockmovement", "movement_type"): {
        "purchase_in": "وارد مشتريات",
        "sale_out": "صادر مبيعات",
        "purchase_return_out": "صادر مرتجع مشتريات",
        "sale_return_in": "وارد مرتجع مبيعات",
        "transfer_in": "تحويل وارد",
        "transfer_out": "تحويل صادر",
        "adjustment_in": "تسوية بالزيادة",
        "adjustment_out": "تسوية بالنقص",
        "opening_stock": "رصيد افتتاحي",
    },
    ("inventory.stockoperation", "operation_type"): {
        "transfer": "تحويل",
        "adjustment": "تسوية",
    },
    ("inventory.stockoperation", "adjustment_direction"): {"in": "زيادة", "out": "نقص"},
    ("inventory.stockoperation", "status"): DOCUMENT_STATUS,
    ("cashboxes.cashboxmovement", "movement_type"): {
        "purchase_payment": "سداد مشتريات",
        "sales_receipt": "تحصيل مبيعات",
        "supplier_payment": "سداد لمورد",
        "customer_payment": "تحصيل من عميل",
        "purchase_return": "استرداد مرتجع مشتريات",
        "sales_return": "رد مرتجع مبيعات",
        "direct_in": "إيداع مباشر",
        "direct_out": "صرف مباشر",
        "transfer_in": "تحويل وارد",
        "transfer_out": "تحويل صادر",
        "adjustment": "تسوية",
    },
    ("cashboxes.cashboxmovement", "direction"): {"in": "وارد", "out": "صادر"},
    ("cashboxes.cashboxoperation", "operation_type"): {
        "direct_in": "إيداع نقدي مباشر",
        "direct_out": "صرف نقدي مباشر",
        "transfer": "تحويل بين الخزن",
    },
    ("cashboxes.cashboxoperation", "status"): DOCUMENT_STATUS,
    ("cashboxes.openingbalanceadjustment", "status"): DOCUMENT_STATUS,
    ("closing.period", "frequency"): FREQUENCY,
    ("closing.period", "status"): {
        "open": "مفتوحة",
        "closed": "مقفلة",
        "reopened": "أُعيد فتحها",
    },
    ("closing.closingrun", "status"): {
        "draft": "مسودة",
        "completed": "مكتمل",
        "cancelled": "ملغى",
    },
    ("settings_core.clientprofile", "default_closing_frequency"): FREQUENCY,
}


def _key(model, field_name):
    if isinstance(model, str):
        model = apps.get_model(model)
    return model._meta.label_lower, field_name


def choice_label(obj, field_name, lang):
    """The label for obj.<field_name> in the screen's language."""

    if lang != "en":
        value = getattr(obj, field_name)
        label = AR_LABELS.get(_key(type(obj), field_name), {}).get(value)
        if label:
            return label
    return getattr(obj, f"get_{field_name}_display")()


def localized_choices(model, field_name, lang):
    """A field's (value, label) pairs in the screen's language, model order."""

    if isinstance(model, str):
        model = apps.get_model(model)
    choices = model._meta.get_field(field_name).choices
    if lang == "en":
        return list(choices)
    arabic = AR_LABELS.get(_key(model, field_name), {})
    return [(value, arabic.get(value, label)) for value, label in choices]
