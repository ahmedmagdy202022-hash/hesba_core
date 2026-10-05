"""{% term "customers" %}: the activity's own word (ACT-PROFILE-001)."""

from django import template

from settings_core.vocabulary import term as vocabulary_term

register = template.Library()


@register.simple_tag(takes_context=True)
def term(context, key):
    return vocabulary_term(key, "en" if context.get("lang") == "en" else "ar")
