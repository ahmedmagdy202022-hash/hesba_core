from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from cashboxes.models import Cashbox
from permissions.services import user_has_permission

from .forms import CategoryForm, CustomerForm, ItemForm, LocationForm, SupplierForm
from .models import Category, Customer, Item, Location, Supplier


STRINGS = {
    "ar": {
        "page_title": "البيانات الأساسية - حِسبة",
        "hub_title": "البيانات الأساسية",
        "hub_note": "جهّز بيانات النشاط مرة واحدة لتستخدمها بعد ذلك في المشتريات والمبيعات والمخزون والخزن.",
        "search": "بحث",
        "search_placeholder": "ابحث بالكود أو الاسم...",
        "status": "الحالة",
        "active": "نشط",
        "inactive": "غير نشط",
        "all": "الكل",
        "new": "إضافة جديد",
        "edit": "تعديل",
        "save": "حفظ",
        "cancel": "إلغاء",
        "back": "رجوع",
        "dashboard": "لوحة القيادة",
        "language": "English",
        "empty": "لا توجد بيانات حتى الآن.",
        "readonly": "عرض فقط",
        "yes": "نعم",
        "no": "لا",
        "default": "افتراضي",
        "stock_item": "صنف مخزني",
        "service": "خدمة",
        "saved": "تم حفظ البيانات.",
        "opening_locked": "الرصيد الافتتاحي مقفول بعد إنشاء السجل لحين اعتماد مسار تعديل مالي مستقل.",
    },
    "en": {
        "page_title": "Master data - Hesba",
        "hub_title": "Master data",
        "hub_note": "Set up the business records once, then reuse them across purchases, sales, inventory and cashboxes.",
        "search": "Search",
        "search_placeholder": "Search by code or name...",
        "status": "Status",
        "active": "Active",
        "inactive": "Inactive",
        "all": "All",
        "new": "Add new",
        "edit": "Edit",
        "save": "Save",
        "cancel": "Cancel",
        "back": "Back",
        "dashboard": "Dashboard",
        "language": "العربية",
        "empty": "No records yet.",
        "readonly": "View only",
        "yes": "Yes",
        "no": "No",
        "default": "Default",
        "stock_item": "Stock item",
        "service": "Service",
        "saved": "Saved successfully.",
        "opening_locked": "Opening balance is locked after creation until a dedicated audited financial-adjustment flow is approved.",
    },
}


ENTITY_CONFIG = {
    "locations": {
        "model": Location,
        "form": LocationForm,
        "view_permission": "master_data.view_master_data",
        "manage_permission": "master_data.manage_locations",
        "title": {"ar": "المخازن والمواقع", "en": "Locations"},
        "singular": {"ar": "موقع / مخزن", "en": "Location"},
        "search_fields": ("location_code", "name_ar", "name_en", "description"),
        "select_related": (),
    },
    "suppliers": {
        "model": Supplier,
        "form": SupplierForm,
        "view_permission": "master_data.view_master_data",
        "manage_permission": "master_data.manage_parties",
        "title": {"ar": "الموردون", "en": "Suppliers"},
        "singular": {"ar": "مورد", "en": "Supplier"},
        "search_fields": ("supplier_code", "name", "phone", "whatsapp", "email"),
        "select_related": (),
    },
    "customers": {
        "model": Customer,
        "form": CustomerForm,
        "view_permission": "master_data.view_master_data",
        "manage_permission": "master_data.manage_parties",
        "title": {"ar": "العملاء", "en": "Customers"},
        "singular": {"ar": "عميل", "en": "Customer"},
        "search_fields": ("customer_code", "name", "phone", "whatsapp", "email"),
        "select_related": (),
    },
    "categories": {
        "model": Category,
        "form": CategoryForm,
        "view_permission": "master_data.view_master_data",
        "manage_permission": "master_data.manage_items",
        "title": {"ar": "التصنيفات", "en": "Categories"},
        "singular": {"ar": "تصنيف", "en": "Category"},
        "search_fields": ("category_code", "name_ar", "name_en"),
        "select_related": ("parent",),
    },
    "items": {
        "model": Item,
        "form": ItemForm,
        "view_permission": "master_data.view_master_data",
        "manage_permission": "master_data.manage_items",
        "title": {"ar": "الأصناف والخدمات", "en": "Items & services"},
        "singular": {"ar": "صنف / خدمة", "en": "Item / service"},
        "search_fields": ("item_code", "barcode", "item_name", "size", "color"),
        "select_related": ("category",),
    },
    "cashboxes": {
        "model": Cashbox,
        "form": None,
        "view_permission": "cashboxes.view_cashboxes",
        # No explicit cashbox-master management permission exists today.
        "manage_permission": None,
        "title": {"ar": "الخزن", "en": "Cashboxes"},
        "singular": {"ar": "خزنة", "en": "Cashbox"},
        "search_fields": ("cashbox_code", "name_ar", "name_en", "currency"),
        "select_related": (),
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _require(user, permission_code):
    if not user_has_permission(user, permission_code):
        raise PermissionDenied(f"This screen needs {permission_code}.")


def _config(entity):
    try:
        return ENTITY_CONFIG[entity]
    except KeyError as exc:
        raise Http404("Unknown master-data area.") from exc


def _localized_name(obj, lang):
    if hasattr(obj, "name_ar"):
        return (obj.name_en if lang == "en" and obj.name_en else obj.name_ar)
    return getattr(obj, "name", str(obj))


def _yes_no(value, words):
    return words["yes"] if value else words["no"]


def _rows(entity, objects, lang, words, can_view_cost):
    rows = []
    for obj in objects:
        if entity == "locations":
            values = [
                obj.location_code,
                _localized_name(obj, lang),
                _yes_no(obj.is_receiving_location, words),
                _yes_no(obj.is_selling_location, words),
                words["default"] if obj.is_default else "—",
                words["active"] if obj.active else words["inactive"],
            ]
        elif entity == "suppliers":
            values = [
                obj.supplier_code,
                obj.name,
                obj.phone or "—",
                obj.whatsapp or "—",
                words["active"] if obj.active else words["inactive"],
            ]
        elif entity == "customers":
            values = [
                obj.customer_code,
                obj.name,
                obj.phone or "—",
                f"{obj.credit_limit:,.2f}",
                words["active"] if obj.active else words["inactive"],
            ]
        elif entity == "categories":
            values = [
                obj.category_code,
                _localized_name(obj, lang),
                _localized_name(obj.parent, lang) if obj.parent else "—",
                words["active"] if obj.active else words["inactive"],
            ]
        elif entity == "items":
            values = [
                obj.item_code,
                obj.item_name,
                _localized_name(obj.category, lang) if obj.category else "—",
                obj.unit,
                words["stock_item"] if obj.is_stock_tracked else words["service"],
                f"{obj.default_sale_price:,.2f}",
            ]
            if can_view_cost:
                values.append(f"{obj.default_purchase_price:,.2f}")
            values.append(words["active"] if obj.active else words["inactive"])
        else:
            values = [
                obj.cashbox_code,
                _localized_name(obj, lang),
                obj.currency,
                words["default"] if obj.is_default else "—",
                words["active"] if obj.active else words["inactive"],
            ]
        rows.append({"object": obj, "values": values})
    return rows


def _columns(entity, lang, can_view_cost):
    labels = {
        "ar": {
            "code": "الكود",
            "name": "الاسم",
            "receiving": "استلام",
            "selling": "بيع",
            "default": "افتراضي",
            "status": "الحالة",
            "phone": "الهاتف",
            "whatsapp": "واتساب",
            "credit_limit": "الحد الائتماني",
            "parent": "التصنيف الأب",
            "category": "التصنيف",
            "unit": "الوحدة",
            "type": "النوع",
            "sale_price": "سعر البيع",
            "purchase_price": "سعر الشراء",
            "currency": "العملة",
            "actions": "إجراءات",
        },
        "en": {
            "code": "Code",
            "name": "Name",
            "receiving": "Receiving",
            "selling": "Selling",
            "default": "Default",
            "status": "Status",
            "phone": "Phone",
            "whatsapp": "WhatsApp",
            "credit_limit": "Credit limit",
            "parent": "Parent",
            "category": "Category",
            "unit": "Unit",
            "type": "Type",
            "sale_price": "Sale price",
            "purchase_price": "Purchase price",
            "currency": "Currency",
            "actions": "Actions",
        },
    }[lang]
    keys = {
        "locations": ("code", "name", "receiving", "selling", "default", "status"),
        "suppliers": ("code", "name", "phone", "whatsapp", "status"),
        "customers": ("code", "name", "phone", "credit_limit", "status"),
        "categories": ("code", "name", "parent", "status"),
        "items": ("code", "name", "category", "unit", "type", "sale_price", "status"),
        "cashboxes": ("code", "name", "currency", "default", "status"),
    }[entity]
    keys = list(keys)
    if entity == "items" and can_view_cost:
        keys.insert(-1, "purchase_price")
    return [labels[key] for key in keys]


def master_data_hub(request):
    lang = _lang(request)
    words = STRINGS[lang]
    visible = []
    for key, config in ENTITY_CONFIG.items():
        if not user_has_permission(request.user, config["view_permission"]):
            continue
        visible.append(
            {
                "key": key,
                "title": config["title"][lang],
                "count": config["model"].objects.filter(active=True).count(),
                "can_manage": bool(
                    config["manage_permission"]
                    and user_has_permission(request.user, config["manage_permission"])
                ),
            }
        )
    if not visible:
        raise PermissionDenied("No master-data permission is available.")
    return render(
        request,
        "master_data/hub.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": words,
            "areas": visible,
            "page_title": words["page_title"],
        },
    )


def entity_list(request, entity):
    config = _config(entity)
    _require(request.user, config["view_permission"])

    lang = _lang(request)
    words = STRINGS[lang]
    queryset = config["model"].objects.all()
    if config["select_related"]:
        queryset = queryset.select_related(*config["select_related"])

    query = request.GET.get("q", "").strip()
    if query:
        search = Q()
        for field in config["search_fields"]:
            search |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(search)

    status = request.GET.get("status", "active")
    if status == "active":
        queryset = queryset.filter(active=True)
    elif status == "inactive":
        queryset = queryset.filter(active=False)
    elif status != "all":
        status = "active"
        queryset = queryset.filter(active=True)

    paginator = Paginator(queryset, 25)
    page = paginator.get_page(request.GET.get("page"))

    can_manage = bool(
        config["manage_permission"]
        and user_has_permission(request.user, config["manage_permission"])
    )
    can_view_cost = entity == "items" and user_has_permission(
        request.user, "inventory.view_cost"
    )

    return render(
        request,
        "master_data/list.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": words,
            "entity": entity,
            "entity_title": config["title"][lang],
            "singular": config["singular"][lang],
            "columns": _columns(entity, lang, can_view_cost),
            "rows": _rows(entity, page.object_list, lang, words, can_view_cost),
            "page": page,
            "query": query,
            "status_filter": status,
            "can_manage": can_manage,
            "page_title": f"{config['title'][lang]} - {'Hesba' if lang == 'en' else 'حِسبة'}",
        },
    )


def entity_create(request, entity):
    config = _config(entity)
    if not config["manage_permission"] or config["form"] is None:
        raise PermissionDenied("This master-data area is currently view-only.")
    _require(request.user, config["manage_permission"])
    return _entity_form(request, entity, config, None)


def entity_edit(request, entity, pk):
    config = _config(entity)
    if not config["manage_permission"] or config["form"] is None:
        raise PermissionDenied("This master-data area is currently view-only.")
    _require(request.user, config["manage_permission"])
    instance = get_object_or_404(config["model"], pk=pk)
    return _entity_form(request, entity, config, instance)


def _entity_form(request, entity, config, instance):
    lang = _lang(request)
    words = STRINGS[lang]
    kwargs = {"lang": lang, "instance": instance}
    if entity == "items":
        kwargs["can_view_cost"] = user_has_permission(request.user, "inventory.view_cost")

    if request.method == "POST":
        form = config["form"](request.POST, **kwargs)
        if form.is_valid():
            form.save()
            messages.success(request, words["saved"])
            return redirect(f"{reverse('master_data:list', kwargs={'entity': entity})}?lang={lang}")
    else:
        form = config["form"](**kwargs)

    return render(
        request,
        "master_data/form.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": words,
            "entity": entity,
            "entity_title": config["title"][lang],
            "singular": config["singular"][lang],
            "form": form,
            "editing": instance is not None,
            "opening_balance_locked": bool(
                instance is not None and entity in {"customers", "suppliers"}
            ),
            "page_title": f"{config['singular'][lang]} - {'Hesba' if lang == 'en' else 'حِسبة'}",
        },
    )
