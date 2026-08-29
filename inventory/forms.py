from decimal import Decimal

from django import forms
from django.utils import timezone

from master_data.models import Item, Location

from .models import StockAdjustmentDirection, StockOperation


LABELS = {
    "ar": {
        "reference_number": "رقم المرجع",
        "operation_date": "تاريخ الحركة",
        "item": "الصنف",
        "source_location": "الموقع المصدر",
        "destination_location": "الموقع المستلم",
        "location": "الموقع",
        "direction": "اتجاه التسوية",
        "quantity": "الكمية",
        "unit_cost": "تكلفة الوحدة",
        "reason": "السبب / الملاحظات",
        "reversal_date": "تاريخ العكس",
    },
    "en": {
        "reference_number": "Reference number",
        "operation_date": "Operation date",
        "item": "Item",
        "source_location": "Source location",
        "destination_location": "Destination location",
        "location": "Location",
        "direction": "Adjustment direction",
        "quantity": "Quantity",
        "unit_cost": "Unit cost",
        "reason": "Reason / notes",
        "reversal_date": "Reversal date",
    },
}


class StockOperationForm(forms.Form):
    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = LABELS[lang][name]
        if "item" in self.fields:
            self.fields["item"].queryset = Item.objects.filter(
                active=True, is_stock_tracked=True
            )
        for name in ("source_location", "destination_location", "location"):
            if name in self.fields:
                self.fields[name].queryset = Location.objects.filter(active=True)
        if not self.is_bound and "operation_date" in self.fields:
            self.initial["operation_date"] = timezone.localdate()

    def clean_reference_number(self):
        number = self.cleaned_data["reference_number"]
        if StockOperation.objects.filter(reference_number=number).exists():
            raise forms.ValidationError("A stock operation with this reference already exists.")
        return number


class StockTransferForm(StockOperationForm):
    reference_number = forms.CharField(max_length=100)
    operation_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    item = forms.ModelChoiceField(queryset=Item.objects.none())
    source_location = forms.ModelChoiceField(queryset=Location.objects.none())
    destination_location = forms.ModelChoiceField(queryset=Location.objects.none())
    quantity = forms.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_location")
        destination = cleaned.get("destination_location")
        if source and destination and source.pk == destination.pk:
            self.add_error("destination_location", "Transfer locations must be different.")
        return cleaned


class StockAdjustmentForm(StockOperationForm):
    reference_number = forms.CharField(max_length=100)
    operation_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    item = forms.ModelChoiceField(queryset=Item.objects.none())
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    direction = forms.ChoiceField(choices=StockAdjustmentDirection.choices)
    quantity = forms.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    unit_cost = forms.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"), required=False
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, can_view_cost=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not can_view_cost:
            self.fields.pop("unit_cost")


class StockOperationReversalForm(forms.Form):
    reversal_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = LABELS[lang][name]
        if not self.is_bound:
            self.initial["reversal_date"] = timezone.localdate()
