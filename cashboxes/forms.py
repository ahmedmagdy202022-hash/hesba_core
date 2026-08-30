from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Cashbox, CashboxOperation, CashboxOperationType


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


class CashboxOperationForm(forms.Form):
    reference_number = forms.CharField(max_length=100)
    operation_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    operation_type = forms.ChoiceField(choices=CashboxOperationType.choices)
    source_cashbox = forms.ModelChoiceField(
        queryset=Cashbox.objects.none(), required=False
    )
    destination_cashbox = forms.ModelChoiceField(
        queryset=Cashbox.objects.none(), required=False
    )
    amount = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    LABELS = {
        "ar": {
            "reference_number": "رقم المرجع",
            "operation_date": "تاريخ الحركة",
            "operation_type": "نوع الحركة",
            "source_cashbox": "الخزنة المصدر",
            "destination_cashbox": "الخزنة المستلمة",
            "amount": "المبلغ",
            "reason": "السبب",
        },
        "en": {
            "reference_number": "Reference number",
            "operation_date": "Operation date",
            "operation_type": "Operation type",
            "source_cashbox": "Source cashbox",
            "destination_cashbox": "Destination cashbox",
            "amount": "Amount",
            "reason": "Reason",
        },
    }

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        cashboxes = Cashbox.objects.filter(active=True)
        self.fields["source_cashbox"].queryset = cashboxes
        self.fields["destination_cashbox"].queryset = cashboxes
        for name, field in self.fields.items():
            field.label = self.LABELS[lang][name]
        if not self.is_bound:
            self.initial["operation_date"] = timezone.localdate()

    def clean_reference_number(self):
        number = self.cleaned_data["reference_number"].strip()
        if CashboxOperation.objects.filter(reference_number=number).exists():
            raise forms.ValidationError("A cash operation with this reference already exists.")
        return number

    def clean(self):
        cleaned = super().clean()
        operation_type = cleaned.get("operation_type")
        source = cleaned.get("source_cashbox")
        destination = cleaned.get("destination_cashbox")
        if operation_type == CashboxOperationType.DIRECT_IN:
            if not destination:
                self.add_error("destination_cashbox", "Destination cashbox is required.")
            if source:
                self.add_error("source_cashbox", "Direct cash in does not use a source cashbox.")
        elif operation_type == CashboxOperationType.DIRECT_OUT:
            if not source:
                self.add_error("source_cashbox", "Source cashbox is required.")
            if destination:
                self.add_error(
                    "destination_cashbox", "Direct cash out does not use a destination cashbox."
                )
        elif operation_type == CashboxOperationType.TRANSFER:
            if not source:
                self.add_error("source_cashbox", "Source cashbox is required.")
            if not destination:
                self.add_error("destination_cashbox", "Destination cashbox is required.")
            if source and destination and source.pk == destination.pk:
                self.add_error("destination_cashbox", "Transfer cashboxes must be different.")
            if source and destination and source.currency != destination.currency:
                self.add_error("destination_cashbox", "Cashbox currencies must match.")
        return cleaned


class CashboxOperationReversalForm(forms.Form):
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
