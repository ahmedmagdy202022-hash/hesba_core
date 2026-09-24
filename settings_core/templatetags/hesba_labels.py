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
