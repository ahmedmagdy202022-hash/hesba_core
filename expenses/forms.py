from decimal import Decimal

from django import forms
from django.utils import timezone

from cashboxes.models import Cashbox

from .models import ExpenseCategory


class ExpenseForm(forms.Form):
    expense_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    category = forms.ModelChoiceField(queryset=ExpenseCategory.objects.none())
    cashbox = forms.ModelChoiceField(queryset=Cashbox.objects.none())
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    payee = forms.CharField(max_length=160, required=False)
    description = forms.CharField(max_length=255)

    LABELS = {
        "ar": {
            "expense_date": "تاريخ المصروف",
            "category": "البند",
            "cashbox": "اتدفع من خزنة",
            "amount": "المبلغ",
            "payee": "اتدفع لمين (اختياري)",
            "description": "البيان",
        },
        "en": {
            "expense_date": "Expense date",
            "category": "Category",
            "cashbox": "Paid from cashbox",
            "amount": "Amount",
            "payee": "Paid to (optional)",
            "description": "Description",
        },
    }

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        categories = ExpenseCategory.objects.filter(active=True)
        self.fields["category"].queryset = categories
        self.fields["category"].label_from_instance = lambda category: category.label(lang)
        cashboxes = Cashbox.objects.filter(active=True)
        self.fields["cashbox"].queryset = cashboxes
        for name, field in self.fields.items():
            field.label = self.LABELS[lang][name]
        self.fields["amount"].widget.attrs.update({"inputmode": "decimal", "step": "0.01"})
        if not self.is_bound:
            self.initial["expense_date"] = timezone.localdate()
            default_cashbox = cashboxes.filter(is_default=True).first() or cashboxes.first()
            if default_cashbox is not None:
                self.initial["cashbox"] = default_cashbox.pk


class ExpenseCancelForm(forms.Form):
    reason = forms.CharField(max_length=255)

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("A cancellation reason is required.")
        return reason


class ExpenseCategoryForm(forms.Form):
    name_ar = forms.CharField(max_length=120)
    name_en = forms.CharField(max_length=120, required=False)

    LABELS = {
        "ar": {"name_ar": "اسم البند بالعربي", "name_en": "اسم البند بالإنجليزي (اختياري)"},
        "en": {"name_ar": "Arabic name", "name_en": "English name (optional)"},
    }

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = self.LABELS[lang][name]

    def clean_name_ar(self):
        name = self.cleaned_data["name_ar"].strip()
        if ExpenseCategory.objects.filter(name_ar=name).exists():
            raise forms.ValidationError("This category already exists." )
        return name
