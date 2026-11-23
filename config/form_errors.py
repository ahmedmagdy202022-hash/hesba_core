"""Show a service's ValidationError on a form without a server error.

``form.add_error(None, exc)`` is wrong for the dict-style errors that the
models raise from ``full_clean()`` (``ValidationError({"remaining_due": ...})``):
Django then tries to attach every key to a form field, and raises ValueError
("has no field named ...") when the form has no such field, which was a 500.
``add_form_error`` sends keys that are form fields to those fields, and
everything else to the form's non-field errors.
"""

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError


def add_form_error(form, error, overrides=None):
    """Add ``error`` to ``form`` safely.

    ``overrides`` maps an error key (a model field name) to a friendlier
    message that replaces the model's own text for that key. The same message
    is added once, however many of its keys the model reported.
    """

    if not isinstance(error, ValidationError) or not hasattr(error, "error_dict"):
        form.add_error(None, error)
        return
    overrides = overrides or {}
    given = set()  # (target, message) already added, so an override is never repeated
    for key, errors in error.error_dict.items():
        target = key if key in form.fields else None
        if key in overrides:
            if (target, overrides[key]) not in given:
                given.add((target, overrides[key]))
                form.add_error(target, overrides[key])
        elif target is None and key != NON_FIELD_ERRORS:
            form.add_error(None, [message for item in errors for message in item.messages])
        else:
            form.add_error(target, errors)
