from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from cashboxes.models import Cashbox
from master_data.models import Item, Location, Supplier


PURCHASE_LABELS = {
    "ar": {
        "invoice_number": "رقم الفاتورة",
        "invoice_date": "تاريخ الفاتورة",
        "supplier": "المورد",
        "receiving_location": "موقع الاستلام",
        "cashbox": "الخزنة (عند الدفع الآن)",
        "discount_amount": "خصم على الفاتورة",
        "tax_amount": "ضريبة الفاتورة",
        "paid_now": "المدفوع الآن",
        "notes": "ملاحظات",
        "item": "الصنف / الخدمة",
        "description": "الوصف",
        "quantity": "الكمية",
        "unit_purchase_price": "سعر الشراء للوحدة",
        "line_discount_amount": "خصم السطر",
    },
    "en": {
        "invoice_number": "Invoice number",
        "invoice_date": "Invoice date",
        "supplier": "Supplier",
        "receiving_location": "Receiving location",
        "cashbox": "Cashbox (when paying now)",
        "discount_amount": "Invoice discount",
        "tax_amount": "Invoice tax",
        "paid_now": "Paid now",
        "notes": "Notes",
        "item": "Item / service",
        "description": "Description",
        "quantity": "Quantity",
        "unit_purchase_price": "Unit purchase price",
        "line_discount_amount": "Line discount",
    },
}


class PurchaseDraftForm(forms.Form):
    invoice_number = forms.CharField(max_length=80)
    invoice_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    receiving_location = forms.ModelChoiceField(queryset=Location.objects.none())
    cashbox = forms.ModelChoiceField(queryset=Cashbox.objects.none(), required=False)
    discount_amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, initial=0)
    tax_amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, initial=0)
    paid_now = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, initial=0)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        labels = PURCHASE_LABELS[lang]
        for name, field in self.fields.items():
            field.label = labels[name]
        self.fields["supplier"].queryset = Supplier.objects.filter(active=True)
        self.fields["receiving_location"].queryset = Location.objects.filter(
            active=True, is_receiving_location=True
        )
        self.fields["cashbox"].queryset = Cashbox.objects.filter(active=True)
        if not self.is_bound:
            self.initial.setdefault("invoice_date", timezone.localdate())


class PurchaseLineInputForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.none(), required=False)
    description = forms.CharField(max_length=255, required=False)
    quantity = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, required=False)
    unit_purchase_price = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    line_discount_amount = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, required=False, initial=0
    )

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        labels = PURCHASE_LABELS[lang]
        for name, field in self.fields.items():
            field.label = labels[name]
        self.fields["item"].queryset = Item.objects.filter(active=True)


class BasePurchaseLineFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        complete_lines = 0
        for form in self.forms:
            data = form.cleaned_data
            supplied = any(
                data.get(name) not in (None, "")
                for name in ("item", "description", "quantity", "unit_purchase_price")
            )
            if not supplied:
                continue
            complete_lines += 1
            for name in ("item", "quantity", "unit_purchase_price"):
                if data.get(name) in (None, ""):
                    form.add_error(name, "This field is required for a purchase line.")
        if complete_lines == 0:
            raise forms.ValidationError("Add at least one purchase line.")


PurchaseLineFormSet = formset_factory(
    PurchaseLineInputForm,
    formset=BasePurchaseLineFormSet,
    extra=5,
    max_num=20,
    validate_max=True,
)

