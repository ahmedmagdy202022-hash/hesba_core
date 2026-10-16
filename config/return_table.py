"""UI-TRACK F15: a return starts from the invoice's own lines.

The return screens used to show five empty "source line" lists. Now each line
of the invoice is a row: the item, how much was sold, how much is already
back, what is left, and one box for how much comes back now. Rows left empty
are not part of the return. The return services still check every quantity.
"""

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Sum


class InvoiceLineField(forms.Field):
    """Codex #233: a row's hidden line id, checked against the invoice lines already loaded, so saving asks no query per row."""

    default_error_messages = {"invalid_choice": forms.ModelChoiceField.default_error_messages["invalid_choice"]}

    def __init__(self, by_id, **kwargs):
        self.by_id = by_id
        super().__init__(required=False, widget=forms.HiddenInput, **kwargs)

    def prepare_value(self, value):
        return getattr(value, "pk", value)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        line = self.by_id.get(int(value)) if str(value).isdigit() else None
        if line is None:
            raise ValidationError(self.error_messages["invalid_choice"], code="invalid_choice")
        return line


def return_table(formset_class, invoice, data, lang, document_field):
    """The bound or unbound formset with one row per invoice line, each row carrying its numbers.

    ``document_field`` names the return document on a return line ("sales_return" or "purchase_return").
    """

    lines = list(invoice.lines.select_related("item").order_by("line_number"))
    formset = formset_class(
        data,
        prefix="lines",
        form_kwargs={"invoice": invoice, "lang": lang},
        initial=[{"source_line": line.pk} for line in lines],
    )
    by_id = {line.pk: line for line in lines}
    # Codex #233: what is already back, for every line in one query.
    return_line_model = invoice.lines.model._meta.get_field("return_lines").related_model
    back_by_line = dict(
        return_line_model.objects.filter(source_line__in=lines, **{f"{document_field}__status": "posted"})
        .values("source_line").annotate(q=Sum("quantity")).values_list("source_line", "q")
    )
    for index, form in enumerate(formset.forms):
        form.fields["source_line"] = InvoiceLineField(by_id, label=form.fields["source_line"].label)
        line = lines[index] if index < len(lines) else None
        if data is not None:  # posted: the row says which line it is
            raw = form.data.get(form.add_prefix("source_line"), "")
            line = by_id.get(int(raw)) if str(raw).isdigit() else line
        form.line = line
        if line is None:
            continue
        back = back_by_line.get(line.pk) or Decimal("0")
        form.returned = back
        form.left = line.quantity - back
        form.fields["quantity"].widget.attrs.update({"inputmode": "decimal", "min": "0", "max": f"{form.left}", "step": "any", "placeholder": "0"})
    return formset


def chosen_lines(formset):
    """The rows that return something, as the services expect them."""

    return [row.cleaned_data for row in formset.forms if row.cleaned_data.get("source_line") and row.cleaned_data.get("quantity")]
