"""AN3: small helpers for the shared form layout (partials/form_sections.html)."""

from django import forms
from django import template

register = template.Library()


@register.filter
def is_wide(bound_field):
    """A note or an address takes the whole row."""

    return isinstance(bound_field.field.widget, forms.Textarea)
