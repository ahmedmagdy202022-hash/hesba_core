"""AUTONUM: codes and document numbers nobody has to make up.

Left blank, a code or number becomes the next in its series, for example
C-00012 or SI-00031: one more than the highest number already used with that
prefix. Hand-typed codes outside the series ("KARIM", "DEMO-SI-3") are simply
skipped. The empty field shows the number it would get, and anyone may still
type their own.
"""

import re

from django.db import IntegrityError, transaction

PLACEHOLDER = {"ar": "تلقائي: {next}", "en": "Automatic: {next}"}


def next_in_series(model, field, prefix, width=5):
    """The next free ``prefix`` + zero-padded number for ``model.field``."""

    manager = model._base_manager
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    top = 0
    for value in manager.filter(**{f"{field}__startswith": prefix}).values_list(field, flat=True).iterator():
        match = pattern.match(value or "")
        if match:
            top = max(top, int(match.group(1)))
    number = top + 1
    while manager.filter(**{field: f"{prefix}{number:0{width}d}"}).exists():
        number += 1
    return f"{prefix}{number:0{width}d}"


class AutoNumbered:
    """Form mixin: ``auto_number = (form field, model, model field, prefix)``.

    Call ``offer_auto_number(lang)`` from ``__init__``; ``clean()`` fills the
    field when it was left blank. An existing record keeps its code required.
    """

    auto_number = None
    auto_filled = False
    ATTEMPTS = 5

    def offer_auto_number(self, lang="ar"):
        name, model, _, prefix = self.auto_number
        field = self.fields.get(name)
        instance = getattr(self, "instance", None)
        if field is None or (instance is not None and instance.pk):
            return
        field.required = False
        field.widget.attrs["placeholder"] = PLACEHOLDER["en" if lang == "en" else "ar"].format(next=self._next_number())
        field.widget.attrs["data-auto-number"] = prefix

    def _next_number(self):
        _, model, model_field, prefix = self.auto_number
        return next_in_series(model, model_field, prefix)

    def clean(self):
        cleaned = super().clean()
        name = self.auto_number[0]
        instance = getattr(self, "instance", None)
        if name in self.fields and not (instance is not None and instance.pk) and not (cleaned.get(name) or "").strip():
            cleaned[name] = self._next_number()
            self.auto_filled = True
        return cleaned

    def create_with_fresh_number(self, create):
        """Run ``create()``; if someone took the automatic number in the meantime
        (two saves at once), take the next one and try again."""

        name, _, model_field, _ = self.auto_number
        for attempt in range(self.ATTEMPTS):
            try:
                with transaction.atomic():
                    return create()
            except IntegrityError:
                if not self.auto_filled or attempt == self.ATTEMPTS - 1:
                    raise
                self.cleaned_data[name] = self._next_number()
                instance = getattr(self, "instance", None)
                if instance is not None:
                    setattr(instance, model_field, self.cleaned_data[name])

    def save(self, commit=True):
        """Model forms: the same retry when saving a new record with an automatic code."""

        parent = super(AutoNumbered, self).save
        if not commit or not self.auto_filled:
            return parent(commit)
        return self.create_with_fresh_number(lambda: parent(commit))
