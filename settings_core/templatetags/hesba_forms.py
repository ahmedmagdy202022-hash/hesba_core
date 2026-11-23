"""AN3: small helpers for the shared form layout (partials/form_sections.html)."""

from django import forms
from django import template

register = template.Library()


@register.filter
def is_wide(bound_field):
    """A note or an address takes the whole row."""

    return isinstance(bound_field.field.widget, forms.Textarea)


@register.filter
def error_fields(form):
    """UX Wave 4 (forms): the fields of a bound form that carry an error, in the form's order, for the summary at its top
    (partials/form_error_summary.html). Each links to its field; the messages themselves still sit beside the fields."""

    if form is None or not getattr(form, "is_bound", False) or not form.errors:
        return []
    return [field for field in form if field.errors and not field.is_hidden]


@register.filter
def line_errors(formset):
    """UX Wave 4 (forms): one entry per invoice line with an error: its number (1-based, as on screen) and the first
    field that needs fixing, so the summary can say «سطر ٢: الكمية»."""

    if formset is None or not getattr(formset, "is_bound", False):
        return []
    lines = []
    for number, line_form in enumerate(formset.forms, start=1):
        fields = [field for field in line_form if field.errors and not field.is_hidden]
        if fields:
            lines.append({"number": number, "field": fields[0]})
    return lines
