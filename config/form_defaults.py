"""UI-TRACK: the choices a form can make for you, so nobody types what is obvious.

- ``preselect(form, *names)``: a warehouse or cashbox list starts on the one
  marked default, or on the only one there is.
- ``ZeroWhenBlank``: an amount that is normally zero (invoice discount, tax,
  paid now) is not marked required; left empty it counts as 0.
"""

from decimal import Decimal


def preselect(form, *names):
    """Start each named choice on its default record (``is_default``), or on the only record."""

    if form.is_bound:
        return form
    for name in names:
        field = form.fields.get(name)
        if field is None or form.initial.get(name) or getattr(field, "queryset", None) is None:
            continue
        queryset = field.queryset
        pick = None
        if any(f.name == "is_default" for f in queryset.model._meta.fields):
            pick = queryset.filter(is_default=True).first()
        if pick is None:
            only = list(queryset[:2])
            pick = only[0] if len(only) == 1 else None
        if pick is not None:
            form.initial[name] = pick.pk
    return form


class ZeroWhenBlank:
    """Form mixin: ``zero_when_blank = ("discount_amount", ...)`` are optional and 0 when empty."""

    zero_when_blank = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.zero_when_blank:
            if name in self.fields:
                self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        for name in self.zero_when_blank:
            if name in self.fields and cleaned.get(name) in (None, ""):
                cleaned[name] = Decimal("0")
        return cleaned
