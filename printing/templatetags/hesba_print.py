from django import template

from settings_core.module_gate import closed_module


register = template.Library()


@register.simple_tag
def printing_enabled():
    """False when the owner switched the PDF printing module off."""

    return closed_module("/print/") is None
