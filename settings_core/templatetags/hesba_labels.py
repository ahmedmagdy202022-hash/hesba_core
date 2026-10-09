"""Template access to settings_core.display_labels.

Both tags read ``lang`` from the template context, which every operational
view already sets, so a call site cannot pick the wrong language by accident.
"""

from django import template

from settings_core.display_labels import choice_label as _choice_label
from settings_core.display_labels import localized_choices as _localized_choices


register = template.Library()


def _lang(context):
    return "en" if context.get("lang") == "en" else "ar"


@register.simple_tag(takes_context=True)
def choice_label(context, obj, field_name):
    """{% choice_label invoice "status" %} in place of get_status_display."""

    return _choice_label(obj, field_name, _lang(context))


@register.simple_tag(takes_context=True)
def localized_choices(context, model_label, field_name):
    """{% localized_choices "sales.salesinvoice" "status" as statuses %}"""

    return _localized_choices(model_label, field_name, _lang(context))


@register.filter(name="ui")
def ui_message(value, lang="ar"):
    """I18N-001: a service message (or a list of them) in the screen's language."""

    from settings_core.ui_messages import translate

    if isinstance(value, (list, tuple)) or hasattr(value, "as_data"):
        return " ".join(translate(item, lang) for item in value)
    return translate(value, lang)


# AUDIT-1: the posting services write their ledger and cashbox descriptions in
# English (they are part of the protected posting logic and stay as stored).
# Arabic screens show them in Arabic; the stored text is never changed.
import re as _re

_POSTED_TEXT = (
    (r"^Cancel sales invoice (\S+)$", "إلغاء فاتورة بيع {0}"),
    (r"^Cancel purchase invoice (\S+)$", "إلغاء فاتورة شراء {0}"),
    (r"^Cancel customer payment (\S+)$", "إلغاء تحصيل {0}"),
    (r"^Cancel supplier payment (\S+)$", "إلغاء دفعة مورد {0}"),
    (r"^Reverse sales return (\S+)$", "إلغاء مرتجع بيع {0}"),
    (r"^Reverse purchase return (\S+)$", "إلغاء مرتجع شراء {0}"),
    (r"^Sales invoice (\S+) paid now$", "فاتورة بيع {0} — المدفوع"),
    (r"^Sales invoice (\S+) remaining due$", "فاتورة بيع {0} — الباقي آجل"),
    (r"^Purchase invoice (\S+) paid now$", "فاتورة شراء {0} — المدفوع"),
    (r"^Purchase invoice (\S+) remaining due$", "فاتورة شراء {0} — الباقي آجل"),
    (r"^Customer payment (\S+)$", "تحصيل من عميل {0}"),
    (r"^Supplier payment (\S+)$", "دفعة لمورد {0}"),
    (r"^Sales return (\S+)$", "مرتجع بيع {0}"),
    (r"^Purchase return (\S+)$", "مرتجع شراء {0}"),
    (r"^Cash operation (\S+)$", "عملية خزنة {0}"),
)
_POSTED_TEXT = tuple((_re.compile(pattern), arabic) for pattern, arabic in _POSTED_TEXT)


@register.simple_tag(takes_context=True)
def posted_text(context, text):
    """A stored posting description, in the page's language."""

    if not text or context.get("lang") == "en":
        return text
    for pattern, arabic in _POSTED_TEXT:
        match = pattern.match(text.strip())
        if match:
            return arabic.format(*match.groups())
    return text


_REF = _re.compile(r"^([a-z_]+)-(\d+)$")
_REF_WORDS = {
    "opening_cashbox": ("رصيد افتتاحي — خزنة", "Opening balance — cashbox"),
    "opening_customer": ("رصيد افتتاحي — عميل", "Opening balance — customer"),
    "opening_supplier": ("رصيد افتتاحي — مورد", "Opening balance — supplier"),
    "opening_stock": ("رصيد افتتاحي — مخزون", "Opening balance — stock"),
    "stock_movement": ("حركة مخزون", "Stock movement"),
    "asset_disposal": ("استبعاد أصل", "Asset disposal"),
    "asset_opening": ("رصيد افتتاحي — أصل", "Opening balance — asset"),
    "depreciation": ("إهلاك", "Depreciation"),
}


@register.simple_tag(takes_context=True)
def entry_ref(context, reference):
    """AUDIT-1: a journal reference made from an internal source type, in words."""

    match = _REF.match(reference or "")
    if not match or match.group(1) not in _REF_WORDS:
        return reference
    words = _REF_WORDS[match.group(1)]
    return f"{words[1] if context.get('lang') == 'en' else words[0]} #{match.group(2)}"


@register.simple_tag(takes_context=True)
def link_ok(context, path):
    """AUDIT-1: True when ``path`` opens for this viewer: its module and
    capability are on and the viewer holds the view's permission. Screens use
    it so a link never leads to a "not enabled" or "not allowed" page."""

    from django.urls import Resolver404, resolve

    from permissions.services import user_has_permission
    from settings_core.capabilities import closed_capability
    from settings_core.module_gate import closed_module

    request = context.get("request")
    cache = getattr(request, "_link_ok", None) if request is not None else None
    if cache is None:
        cache = {}
        if request is not None:
            request._link_ok = cache
    key = path.split("?")[0]
    if key in cache:
        return cache[key]
    ok = closed_module(key) is None and closed_capability(key) is None
    if ok and request is not None:
        try:
            view = resolve(key).func
        except Resolver404:
            ok = False
        else:
            needed = getattr(view, "required_permission", None)
            any_of = getattr(view, "required_permissions", ())
            if needed:
                ok = user_has_permission(request.user, needed)
            elif any_of:
                ok = any(user_has_permission(request.user, code) for code in any_of)
    cache[key] = ok
    return ok
