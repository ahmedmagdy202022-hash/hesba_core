from decimal import Decimal

from django import forms
from django.utils import timezone


class OpeningBalanceAdjustmentForm(forms.Form):
    adjustment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(max_digits=14, decimal_places=2)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    LABELS = {
        "ar": {
            "adjustment_date": "تاريخ التسوية",
            "amount": "قيمة التسوية (+ زيادة / - تخفيض)",
            "reason": "سبب التسوية",
        },
        "en": {
            "adjustment_date": "Adjustment date",
            "amount": "Adjustment amount (+ increase / - decrease)",
            "reason": "Adjustment reason",
        },
    }

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = self.LABELS[lang][name]
        if not self.is_bound:
            self.initial["adjustment_date"] = timezone.localdate()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount == Decimal("0"):
            raise forms.ValidationError("Adjustment amount cannot be zero.")
        return amount


class OpeningBalanceReversalForm(forms.Form):
    reversal_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    LABELS = {
        "ar": {"reversal_date": "تاريخ العكس", "reason": "سبب العكس"},
        "en": {"reversal_date": "Reversal date", "reason": "Reversal reason"},
    }

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = self.LABELS[lang][name]
        if not self.is_bound:
            self.initial["reversal_date"] = timezone.localdate()
