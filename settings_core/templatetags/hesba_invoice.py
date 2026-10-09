"""R2-6: how the invoice form lays out its fields.

The header keeps who, when and where on one row; the money (discount, tax,
paid now, cashbox) goes to the summary beside the lines; anything else
(notes, the salesperson...) folds under "more details". Each line shows the
fields typed on every line in one row; the rest (description, serials,
batch and expiry) open under it when needed.
"""

from django import template

register = template.Library()

HEAD_MAIN = ("invoice_number", "invoice_date", "customer", "supplier", "selling_location", "receiving_location")
HEAD_MONEY = ("discount_amount", "tax_amount", "paid_now", "cashbox")
LINE_MAIN = ("item", "unit", "quantity", "unit_sale_price", "unit_purchase_price", "line_discount_amount")

SHORT = {
    "item": ("الصنف / الخدمة", "Item / service"),
    "unit": ("الوحدة", "Unit"),
    "quantity": ("الكمية", "Qty"),
    "unit_sale_price": ("السعر", "Price"),
    "unit_purchase_price": ("السعر", "Price"),
    "line_discount_amount": ("خصم", "Discount"),
}


def _fields(form, names, keep):
    present = [name for name in form.fields if (name in names) == keep]
    if keep:
        present.sort(key=names.index)
    return [form[name] for name in present]


@register.filter
def head_main(form):
    return _fields(form, HEAD_MAIN, True)


@register.filter
def head_more(form):
    return _fields(form, HEAD_MAIN + HEAD_MONEY, False)


@register.filter
def field_named(form, name):
    return form[name] if name in form.fields else None


@register.filter
def line_main(form):
    return _fields(form, LINE_MAIN, True)


@register.filter
def line_extra(form):
    return _fields(form, LINE_MAIN, False)


@register.filter
def has_errors(fields):
    return any(field.errors for field in fields)


@register.filter
def has_values(fields):
    return any(field.value() not in (None, "", []) for field in fields)


@register.simple_tag
def short_label(field, lang):
    pair = SHORT.get(field.name)
    if pair is None:
        return field.label
    return pair[1] if lang == "en" else pair[0]
