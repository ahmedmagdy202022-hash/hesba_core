from django import forms

from .models import Category, Customer, Item, Location, Supplier


LABELS = {
    "ar": {
        "location_code": "كود الموقع / المخزن",
        "name_ar": "الاسم بالعربية",
        "name_en": "الاسم بالإنجليزية",
        "description": "الوصف",
        "is_default": "افتراضي",
        "is_receiving_location": "متاح للاستلام",
        "is_selling_location": "متاح للبيع",
        "active": "نشط",
        "supplier_code": "كود المورد",
        "customer_code": "كود العميل",
        "name": "الاسم",
        "phone": "الهاتف",
        "whatsapp": "واتساب",
        "email": "البريد الإلكتروني",
        "address": "العنوان",
        "opening_balance": "الرصيد الافتتاحي",
        "credit_limit": "الحد الائتماني",
        "notes": "ملاحظات",
        "category_code": "كود التصنيف",
        "parent": "التصنيف الأب",
        "item_code": "كود الصنف / الخدمة",
        "barcode": "الباركود",
        "item_name": "اسم الصنف / الخدمة",
        "category": "التصنيف",
        "size": "المقاس",
        "color": "اللون",
        "unit": "الوحدة",
        "default_sale_price": "سعر البيع الافتراضي",
        "default_purchase_price": "سعر الشراء الافتراضي",
        "min_stock": "الحد الأدنى للمخزون",
        "is_stock_tracked": "يتابع كمخزون",
    },
    "en": {
        "location_code": "Location code",
        "name_ar": "Arabic name",
        "name_en": "English name",
        "description": "Description",
        "is_default": "Default",
        "is_receiving_location": "Receiving enabled",
        "is_selling_location": "Selling enabled",
        "active": "Active",
        "supplier_code": "Supplier code",
        "customer_code": "Customer code",
        "name": "Name",
        "phone": "Phone",
        "whatsapp": "WhatsApp",
        "email": "Email",
        "address": "Address",
        "opening_balance": "Opening balance",
        "credit_limit": "Credit limit",
        "notes": "Notes",
        "category_code": "Category code",
        "parent": "Parent category",
        "item_code": "Item / service code",
        "barcode": "Barcode",
        "item_name": "Item / service name",
        "category": "Category",
        "size": "Size",
        "color": "Color",
        "unit": "Unit",
        "default_sale_price": "Default sale price",
        "default_purchase_price": "Default purchase price",
        "min_stock": "Minimum stock",
        "is_stock_tracked": "Track inventory",
    },
}


class HesbaModelForm(forms.ModelForm):
    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        words = LABELS["en" if lang == "en" else "ar"]
        for name, field in self.fields.items():
            if name in words:
                field.label = words[name]
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("rows", 3)
            if not isinstance(field.widget, (forms.CheckboxInput, forms.Select)):
                field.widget.attrs.setdefault("autocomplete", "off")


class LocationForm(HesbaModelForm):
    class Meta:
        model = Location
        fields = (
            "location_code",
            "name_ar",
            "name_en",
            "description",
            "is_default",
            "is_receiving_location",
            "is_selling_location",
            "active",
        )


class SupplierForm(HesbaModelForm):
    class Meta:
        model = Supplier
        fields = (
            "supplier_code",
            "name",
            "phone",
            "whatsapp",
            "email",
            "address",
            "opening_balance",
            "notes",
            "active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Correction semantics after operations are not approved yet.
            self.fields["opening_balance"].disabled = True


class CustomerForm(HesbaModelForm):
    class Meta:
        model = Customer
        fields = (
            "customer_code",
            "name",
            "phone",
            "whatsapp",
            "email",
            "address",
            "opening_balance",
            "credit_limit",
            "notes",
            "active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Preserve the original financial starting point until a dedicated
            # audited adjustment flow is approved.
            self.fields["opening_balance"].disabled = True


class CategoryForm(HesbaModelForm):
    class Meta:
        model = Category
        fields = ("category_code", "name_ar", "name_en", "parent", "active")

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        if parent is None or not self.instance.pk:
            return parent

        cursor = parent
        visited = set()
        while cursor is not None and cursor.pk not in visited:
            if cursor.pk == self.instance.pk:
                raise forms.ValidationError(
                    "لا يمكن جعل التصنيف تابعًا لنفسه أو لأحد فروعه."
                )
            visited.add(cursor.pk)
            cursor = cursor.parent
        return parent


class ItemForm(HesbaModelForm):
    class Meta:
        model = Item
        fields = (
            "item_code",
            "barcode",
            "item_name",
            "category",
            "size",
            "color",
            "unit",
            "default_sale_price",
            "default_purchase_price",
            "min_stock",
            "is_stock_tracked",
            "active",
        )

    def __init__(self, *args, can_view_cost=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not can_view_cost:
            self.fields.pop("default_purchase_price", None)
