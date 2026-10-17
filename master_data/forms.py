from django import forms

from cashboxes.models import Cashbox, OpeningBalanceTarget
from cashboxes.services import target_has_operational_use
from config.form_defaults import ZeroWhenBlank
from config.form_sections import Sectioned
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

class HesbaModelForm(Sectioned, forms.ModelForm):
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
    sections_spec = (
        ("main", "بيانات المخزن", "The store", "اسم المخزن زي ما بتقوله في الشغل.", "The name you use for it day to day.", ("name_ar", "name_en", "location_code", "entity")),
        ("more", "وصف", "Description", "", "", ("description",)),
    )

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


class SupplierForm(ZeroWhenBlank, AutoNumbered, HesbaModelForm):
    zero_when_blank = ("opening_balance",)
    sections_spec = (
        ("main", "بيانات المورد", "The supplier", "الاسم بس كفاية، والكود بيتعمل لوحده.", "The name is enough; the code is made for you.", ("name", "supplier_code")),
        ("contact", "التواصل", "Contact", "", "", ("phone", "whatsapp", "email", "address")),
        ("account", "الحساب", "Account", "لو ليه فلوس عندك من قبل البرنامج اكتبها هنا، وإلا سيبها فاضية.", "Money you already owed before Hesba; leave it empty if none.", ("opening_balance", "notes")),
    )

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


class CustomerForm(ZeroWhenBlank, AutoNumbered, HesbaModelForm):
    zero_when_blank = ("opening_balance", "credit_limit")
    sections_spec = (
        ("main", "بيانات العميل", "The customer", "الاسم بس كفاية، والكود بيتعمل لوحده.", "The name is enough; the code is made for you.", ("name", "customer_code", "sales_rep")),
        ("contact", "التواصل", "Contact", "", "", ("phone", "whatsapp", "email", "address")),
        ("account", "الحساب والآجل", "Account and credit", "سيبها فاضية لو مفيش رصيد قديم ولا حد للآجل.", "Leave empty for no old balance and no credit limit.", ("opening_balance", "credit_limit", "notes")),
    )

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


class CashboxForm(ZeroWhenBlank, AutoNumbered, _EntityField, HesbaModelForm):
    zero_when_blank = ("opening_balance",)
    sections_spec = (
        ("main", "بيانات الخزنة", "The cashbox", "", "", ("name_ar", "name_en", "cashbox_code", "currency", "entity")),
        ("money", "الرصيد", "Balance", "الفلوس اللي فيها النهارده قبل أي حركة، أو سيبها فاضية.", "The cash in it today before any movement, or leave it empty.", ("opening_balance", "notes")),
    )

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
    sections_spec = (
        ("main", "بيانات التصنيف", "The category", "", "", ("name_ar", "name_en", "category_code", "parent")),
    )

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


class ItemForm(ZeroWhenBlank, AutoNumbered, HesbaModelForm):
    zero_when_blank = ("min_stock",)
    sections_spec = (
        ("main", "الصنف", "The item", "الاسم والتصنيف. الكود بيتعمل لوحده، والباركود لو موجود.", "Name and category. The code is made for you; add the barcode if it has one.", ("item_name", "category", "unit", "item_code", "barcode")),
        ("variant", "المقاس واللون", "Size and colour", "", "", ("size", "color")),
        ("prices", "الأسعار", "Prices", "سعر الشراء والبيع للوحدة.", "Purchase and sale price per unit.", ("default_purchase_price", "default_sale_price")),
        ("stock", "المخزون", "Stock", "لو الكمية نزلت عن الحد الأدنى هيظهرلك تنبيه. سيبها فاضية لو مش محتاج.", "You get a warning when stock drops below it. Leave empty if you don't need one.", ("min_stock",)),
    )

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
        else:
            self._suggest_sizes()
            if not self.instance.pk:
                self._offer_variants()

    def _suggest_sizes(self):
        """AN7 (F32): a single item's size and colour offer the same standard lists as the variants picker,
        and "لارج" is kept as "L", so one size is never two in the reports."""

        from variants.standard import COLORS, NUMBER_SIZES, SIZES, SuggestInput

        if "size" in self.fields:
            self.fields["size"].widget = SuggestInput("sizes", SIZES + NUMBER_SIZES, self.fields["size"].widget.attrs)
        if "color" in self.fields:
            self.fields["color"].widget = SuggestInput("colors", COLORS["en" if self.lang == "en" else "ar"], self.fields["color"].widget.attrs)

    def clean_size(self):
        from variants.standard import standard_size

        return standard_size(self.cleaned_data.get("size", ""))

    def clean_color(self):
        from variants.standard import standard_color

        return standard_color(self.cleaned_data.get("color", ""))

    #: AN5 (Ahmed: "اسم صنف واحد وتحته مقاسات وألوان كتير، وكل واحد بكوده وباركوده زي أمازون ونون").
    VARIANT_FIELDS = ("has_variants", "sizes_std", "sizes_extra", "colors_std", "colors_extra")
    VARIANT_WORDS = {
        "ar": {"has_variants": "الصنف ده له مقاسات وألوان", "has_variants_help": "هيتعمل صنف لكل مقاس ولون، كل واحد بكوده وباركوده ومخزونه.",
               "sizes": "المقاسات", "colors": "الألوان", "extra": "غير اللي فوق؟ اكتبها وافصل بفاصلة", "none": "اختار مقاس أو لون واحد على الأقل."},
        "en": {"has_variants": "This item comes in sizes and colours", "has_variants_help": "One item is made per size and colour, each with its own code, barcode and stock.",
               "sizes": "Sizes", "colors": "Colours", "extra": "Others? Type them, comma separated", "none": "Pick at least one size or one colour."},
    }
    variant_sizes = variant_colors = ()

    def _offer_variants(self):
        from variants.standard import color_choices, size_choices

        words = self.VARIANT_WORDS[self.lang]
        self.fields["has_variants"] = forms.BooleanField(required=False, label=words["has_variants"], help_text=words["has_variants_help"])
        self.fields["sizes_std"] = forms.MultipleChoiceField(required=False, label=words["sizes"], choices=size_choices(), widget=forms.CheckboxSelectMultiple)
        self.fields["colors_std"] = forms.MultipleChoiceField(required=False, label=words["colors"], choices=color_choices(self.lang), widget=forms.CheckboxSelectMultiple)
        for kind in ("sizes", "colors"):
            self.fields[f"{kind}_extra"] = forms.CharField(required=False, max_length=400, label=words["extra"],
                                                           widget=forms.TextInput(attrs={"autocomplete": "off", "data-variant-extra": kind}))
        self.drawn_apart = self.VARIANT_FIELDS
        self.section_inserts = {"main": "variants/_item_picker.html"}

    @property
    def offers_variants(self):
        return "has_variants" in self.fields

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("has_variants"):
            return cleaned
        from variants.services import parse_values

        self.variant_sizes = parse_values(",".join([*cleaned.get("sizes_std", []), cleaned.get("sizes_extra", "")]))
        self.variant_colors = parse_values(",".join([*cleaned.get("colors_std", []), cleaned.get("colors_extra", "")]))
        if not self.variant_sizes and not self.variant_colors:
            self.add_error("has_variants", self.VARIANT_WORDS[self.lang]["none"])
        cleaned["size"] = cleaned["color"] = ""  # every combination carries its own
        cleaned["barcode"] = ""  # and its own barcode
        return cleaned

    def create_variants(self, user):
        """AN5: the model and one item per size and colour (variants.services), instead of a single item."""

        from config.numbering import next_in_series
        from variants.models import VariantGroup
        from variants.services import create_group

        data = self.cleaned_data
        # A code typed by hand names the model; an automatic one comes from the models' own series,
        # so the next plain item can never be handed the same number.
        code = next_in_series(VariantGroup, "code", "MD-") if self.auto_filled else data["item_code"]
        group, created = create_group(
            code=code, name=data["item_name"], sizes=list(self.variant_sizes), colors=list(self.variant_colors),
            sale_price=data.get("default_sale_price") or 0, purchase_price=data.get("default_purchase_price") or 0,
            unit=data.get("unit") or "unit", category=data.get("category"), barcodes=True, user=user,
        )
        if data.get("min_stock"):
            Item.objects.filter(pk__in=[item.pk for item in created]).update(min_stock=data["min_stock"])
        return group, created

    def clean_unit(self):
        """Codex #233: the default unit is stored once, language-neutral; screens show it in their language."""

        unit = (self.cleaned_data.get("unit") or "").strip()
        return "unit" if unit.casefold() in DEFAULT_UNITS else unit
