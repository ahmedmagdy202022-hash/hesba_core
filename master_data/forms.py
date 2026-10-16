from django import forms

from cashboxes.models import Cashbox, OpeningBalanceTarget
from cashboxes.services import target_has_operational_use
from config.numbering import AutoNumbered
from settings_core.templatetags.hesba_format import DEFAULT_UNITS
from settings_core.models import ClientProfile

from .models import Category, Customer, Item, Location, Supplier


LABELS = {
    "ar": {
        "location_code": "كود المخزن",
        "entity": "الكيان / الفرع",
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
        "cashbox_code": "كود الخزنة",
        "currency": "العملة",
        "credit_limit": "الحد الائتماني",
        "sales_rep": "المندوب المسؤول",
        "notes": "ملاحظات",
        "category_code": "كود التصنيف",
        "parent": "التصنيف الأب",
        "item_code": "كود الصنف (SKU)",
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
        "location_code": "Warehouse code",
        "entity": "Entity / branch",
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
        "cashbox_code": "Cashbox code",
        "currency": "Currency",
        "credit_limit": "Credit limit",
        "sales_rep": "Sales rep",
        "notes": "Notes",
        "category_code": "Category code",
        "parent": "Parent category",
        "item_code": "Item code (SKU)",
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


#: FB2-LOC: what each tick box means, shown under it (a bare "Default" or "Selling enabled" says too little).
HELP = {
    "ar": {
        "is_default": "ده اللي بيتختار لوحده في الفواتير والحركات الجديدة.",
        "is_receiving_location": "ينفع تستلم فيه بضاعة المشتريات والتحويلات.",
        "is_selling_location": "ينفع تبيع منه وتخصم من رصيده في فواتير البيع.",
        "active": "لو شلت العلامة بيختفي من الاختيارات، وحركاته القديمة بتفضل زي ما هي.",
    },
    "en": {
        "is_default": "Picked by itself on new invoices and movements.",
        "is_receiving_location": "Purchases and transfers can be received into it.",
        "is_selling_location": "Sales invoices can sell from it and take from its stock.",
        "active": "Untick to hide it from the choices; its past movements stay as they are.",
    },
}

class HesbaModelForm(forms.ModelForm):
    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = "en" if lang == "en" else "ar"
        words = LABELS["en" if lang == "en" else "ar"]
        for name, field in self.fields.items():
            if name in words:
                field.label = words[name]
            if name in HELP[self.lang] and not field.help_text:
                field.help_text = HELP[self.lang][name]
            # Django gives every Textarea rows=10, so setdefault never took: a 270px box for an address.
            if isinstance(field.widget, forms.Textarea) and str(field.widget.attrs.get("rows", "10")) == "10":
                field.widget.attrs["rows"] = 3
            if not isinstance(field.widget, (forms.CheckboxInput, forms.Select)):
                field.widget.attrs.setdefault("autocomplete", "off")


class _EntityField:
    """ENT-001: pick the entity a store or cashbox belongs to (only shown once
    the group has more than one)."""

    def _entity_field(self):
        from entities.services import entities, is_multi_entity, main_entity

        field = self.fields.get("entity")
        if field is None:
            return
        from entities.current import current_entity

        working_in = current_entity()
        # HG-034: inside an entity, a new store or cashbox belongs to it.
        field.queryset = entities().filter(pk=working_in.pk) if working_in else entities()
        field.required = False
        field.empty_label = None
        if not self.instance.pk:
            field.initial = (working_in or main_entity()).pk
        if not is_multi_entity():
            field.widget = forms.HiddenInput()

    def clean_entity(self):
        from entities.current import current_entity

        return self.cleaned_data.get("entity") or current_entity()


class LocationForm(AutoNumbered, _EntityField, HesbaModelForm):
    auto_number = ("location_code", Location, "location_code", "WH-")  # FB2-LOC: like suppliers and items

    class Meta:
        model = Location
        fields = (
            "location_code",
            "name_ar",
            "name_en",
            "entity",
            "description",
            "is_default",
            "is_receiving_location",
            "is_selling_location",
            "active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._entity_field()
        self.offer_auto_number(self.lang)


class SupplierForm(AutoNumbered, HesbaModelForm):
    auto_number = ("supplier_code", Supplier, "supplier_code", "S-")  # AUTONUM

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
        self.offer_auto_number(self.lang)
        if self.instance.pk and target_has_operational_use(
            OpeningBalanceTarget.SUPPLIER, self.instance
        ):
            self.fields["opening_balance"].disabled = True


class CustomerForm(AutoNumbered, HesbaModelForm):
    auto_number = ("customer_code", Customer, "customer_code", "C-")  # AUTONUM

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
            "sales_rep",
            "notes",
            "active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.offer_auto_number(self.lang)
        # PERF-001: the rep who looks after this customer, once the employees
        # module is on and has people in it (FEEDBACK-R1).
        from staff.models import Employee
        from staff.services import reps_offered

        reps = reps_offered()
        if reps.exists() and self.instance.pk and self.instance.sales_rep_id:
            reps = Employee.objects.filter(pk=self.instance.sales_rep_id) | reps
        if reps.exists():
            self.fields["sales_rep"].queryset = reps
        else:
            del self.fields["sales_rep"]
        if self.instance.pk and target_has_operational_use(
            OpeningBalanceTarget.CUSTOMER, self.instance
        ):
            self.fields["opening_balance"].disabled = True


class CashboxForm(AutoNumbered, _EntityField, HesbaModelForm):
    auto_number = ("cashbox_code", Cashbox, "cashbox_code", "CB-")  # FB2-CASHBOX: like suppliers and items

    class Meta:
        model = Cashbox
        fields = (
            "cashbox_code",
            "name_ar",
            "name_en",
            "entity",
            "opening_balance",
            "currency",
            "is_default",
            "notes",
            "active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._entity_field()
        self.offer_auto_number(self.lang)
        if not self.instance.pk:
            # SETTINGS-002: a new cashbox starts in the company currency.
            profile = ClientProfile.get_active()
            if profile is not None:
                self.fields["currency"].initial = profile.default_currency
        if self.instance.pk and target_has_operational_use(
            OpeningBalanceTarget.CASHBOX, self.instance
        ):
            self.fields["opening_balance"].disabled = True
            self.fields["currency"].disabled = True


class CategoryForm(AutoNumbered, HesbaModelForm):
    auto_number = ("category_code", Category, "category_code", "CAT-")  # UI-TRACK F11: like warehouses and items

    class Meta:
        model = Category
        fields = ("category_code", "name_ar", "name_en", "parent", "active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.offer_auto_number(self.lang)

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


class ItemForm(AutoNumbered, HesbaModelForm):
    auto_number = ("item_code", Item, "item_code", "IT-")  # AUTONUM

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
        self.offer_auto_number(self.lang)
        if "unit" in self.fields and str(self.initial.get("unit") or "").strip().casefold() in DEFAULT_UNITS:
            self.initial["unit"] = "piece" if self.lang == "en" else "قطعة"  # UI-TRACK F01: not the English "unit"
        if "category" in self.fields:  # FB-TYPES: a new category without leaving the item
            self.fields["category"].widget.attrs.update({"data-quick-add": "+ New category…" if self.lang == "en" else "+ تصنيف جديد…",
                                                         "data-quick-add-url": "/master-data/categories/quick/"})
        if not can_view_cost:
            self.fields.pop("default_purchase_price", None)
        # UI-TRACK grocery G01: size and colour belong to a trade that sells sizes and colours.
        from settings_core.capabilities import capability_enabled

        if not capability_enabled("variants"):
            for name in ("size", "color"):
                if not getattr(self.instance, name, ""):  # a value already typed stays editable
                    self.fields.pop(name, None)

    def clean_unit(self):
        """Codex #233: the default unit is stored once, language-neutral; screens show it in their language."""

        unit = (self.cleaned_data.get("unit") or "").strip()
        return "unit" if unit.casefold() in DEFAULT_UNITS else unit
