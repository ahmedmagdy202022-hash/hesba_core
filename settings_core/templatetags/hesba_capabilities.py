"""CAP-001: ask in a template whether a capability is switched on."""

from django import template

from settings_core.capabilities import capability_enabled


register = template.Library()


@register.simple_tag
def capability_on(slug):
    return capability_enabled(slug)
