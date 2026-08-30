from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from cashboxes.models import Cashbox
from master_data.models import Customer, Item, Location

from .models import CustomerPayment, SalesLine, SalesReturn


SALES_LABELS = {
    "ar": {
        "invoice_number": "رقم الفاتورة",
        "invoice_date": "تاريخ الفاتورة",
        "customer": "العميل",
        "selling_location": "موقع البيع",
        "cashbox": "الخزنة (عند التحصيل الآن)",
        "discount_amount": "خصم على الفاتورة",
        "tax_amount": "ضريبة الفاتورة",
        "paid_now": "المحصّل الآن",
        "notes": "ملاحظات",
        "item": "الصنف / الخدمة",
        "description": "الوصف",
        "quantity": "الكمية",
        "unit_sale_price": "سعر البيع للوحدة",
        "line_discount_amount": "خصم السطر",
    },
    "en": {
        "invoice_number": "Invoice number",
        "invoice_date": "Invoice date",
        "customer": "Customer",
        "selling_location": "Selling location",
        "cashbox": "Cashbox (when collecting now)",
        "discount_amount": "Invoice discount",
        "tax_amount": "Invoice tax",
        "paid_now": "Collected now",
        "notes": "Notes",
        "item": "Item / service",
        "description": "Description",
        "quantity": "Quantity",
        "unit_sale_price": "Unit sale price",
        "line_discount_amount": "Line discount",
    },
}


class SalesDraftForm(forms.Form):
    invoice_number = forms.CharField(max_length=80)
    invoice_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    customer = forms.ModelChoiceField(queryset=Customer.objects.none())
    selling_location = forms.ModelChoiceField(queryset=Location.objects.none())
    cashbox = forms.ModelChoiceField(queryset=Cashbox.objects.none(), required=False)
    discount_amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, initial=0)
    tax_amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, initial=0)
    paid_now = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, initial=0)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        labels = SALES_LABELS[lang]
        for name, field in self.fields.items():
            field.label = labels[name]
        self.fields["customer"].queryset = Customer.objects.filter(active=True)
        self.fields["selling_location"].queryset = Location.objects.filter(
            active=True, is_selling_location=True
        )
        self.fields["cashbox"].queryset = Cashbox.objects.filter(active=True)
        if not self.is_bound:
            self.initial.setdefault("invoice_date", timezone.localdate())


class SalesLineInputForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.none(), required=False)
    description = forms.CharField(max_length=255, required=False)
    quantity = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, required=False)
    unit_sale_price = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    line_discount_amount = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, required=False, initial=0
    )

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        labels = SALES_LABELS[lang]
        for name, field in self.fields.items():
            field.label = labels[name]
        self.fields["item"].queryset = Item.objects.filter(active=True)


class BaseSalesLineFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        complete_lines = 0
        for form in self.forms:
            data = form.cleaned_data
            supplied = any(
                data.get(name) not in (None, "")
                for name in ("item", "description", "quantity", "unit_sale_price")
            )
            if not supplied:
                continue
            complete_lines += 1
            for name in ("item", "quantity", "unit_sale_price"):
                if data.get(name) in (None, ""):
                    form.add_error(name, "This field is required for a sales line.")
        if complete_lines == 0:
            raise forms.ValidationError("Add at least one sales line.")


SalesLineFormSet = formset_factory(
    SalesLineInputForm,
    formset=BaseSalesLineFormSet,
    extra=5,
    max_num=20,
    validate_max=True,
)


class CustomerPaymentForm(forms.Form):
    payment_number = forms.CharField(max_length=80)
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    customer = forms.ModelChoiceField(queryset=Customer.objects.none())
    cashbox = forms.ModelChoiceField(queryset=Cashbox.objects.none())
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    LABELS = {
        "ar": {"payment_number": "رقم التحصيل", "payment_date": "تاريخ التحصيل", "customer": "العميل", "cashbox": "الخزنة", "amount": "المبلغ", "notes": "ملاحظات"},
        "en": {"payment_number": "Collection number", "payment_date": "Collection date", "customer": "Customer", "cashbox": "Cashbox", "amount": "Amount", "notes": "Notes"},
    }

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = self.LABELS[lang][name]
        self.fields["customer"].queryset = Customer.objects.filter(active=True)
        self.fields["cashbox"].queryset = Cashbox.objects.filter(active=True)
        if not self.is_bound:
            self.initial.setdefault("payment_date", timezone.localdate())

    def clean_payment_number(self):
        number = self.cleaned_data["payment_number"]
        if CustomerPayment.objects.filter(payment_number=number).exists():
            raise forms.ValidationError("A customer collection with this number already exists.")
        return number


class SalesReturnForm(forms.Form):
    return_number = forms.CharField(max_length=80)
    return_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    LABELS = {
        "ar": {"return_number": "رقم المرتجع", "return_date": "تاريخ المرتجع", "reason": "سبب المرتجع"},
        "en": {"return_number": "Return number", "return_date": "Return date", "reason": "Return reason"},
    }

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = self.LABELS[lang][name]
        if not self.is_bound:
            self.initial["return_date"] = timezone.localdate()

    def clean_return_number(self):
        number = self.cleaned_data["return_number"].strip()
        if SalesReturn.objects.filter(return_number=number).exists():
            raise forms.ValidationError("A sales return with this number already exists.")
        return number


class SalesReturnLineForm(forms.Form):
    source_line = forms.ModelChoiceField(queryset=SalesLine.objects.none(), required=False)
    quantity = forms.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0.001"), required=False
    )

    LABELS = {
        "ar": {"source_line": "بند الفاتورة الأصلي", "quantity": "كمية المرتجع"},
        "en": {"source_line": "Source invoice line", "quantity": "Return quantity"},
    }

    def __init__(self, *args, invoice=None, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = self.LABELS[lang][name]
        if invoice is not None:
            self.fields["source_line"].queryset = invoice.lines.select_related("item")


class BaseSalesReturnLineFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        selected = 0
        for form in self.forms:
            line = form.cleaned_data.get("source_line")
            quantity = form.cleaned_data.get("quantity")
            if line or quantity:
                selected += 1
                if not line:
                    form.add_error("source_line", "Source line is required.")
                if not quantity:
                    form.add_error("quantity", "Return quantity is required.")
        if not selected:
            raise forms.ValidationError("Add at least one sales return line.")


SalesReturnLineFormSet = formset_factory(
    SalesReturnLineForm,
    formset=BaseSalesReturnLineFormSet,
    extra=5,
    max_num=20,
    validate_max=True,
)


class SalesReturnReversalForm(forms.Form):
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
