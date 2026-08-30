from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from cashboxes.models import Cashbox, OpeningBalanceTarget
from cashboxes.forms import OpeningBalanceAdjustmentForm, OpeningBalanceReversalForm
from cashboxes.services import (
    cancel_opening_balance_adjustment,
    create_opening_balance_adjustment,
    target_has_operational_use,
)
from permissions.services import user_has_permission

from .forms import CashboxForm, CategoryForm, CustomerForm, ItemForm, LocationForm, SupplierForm
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
        "opening_locked": "استُخدم السجل تشغيليًا؛ الرصيد الافتتاحي مقفول ويُصحح فقط من مسار التسوية المراجع.",
        "adjust_opening": "تسوية الرصيد الافتتاحي",
        "adjustment_saved": "تم تسجيل تسوية الرصيد الافتتاحي.",
        "adjustment_reversed": "تم عكس تسوية الرصيد الافتتاحي.",
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
        "opening_locked": "This record has operational use; its opening balance is locked and can only be corrected through the audited adjustment flow.",
        "adjust_opening": "Adjust opening balance",
        "adjustment_saved": "Opening-balance adjustment posted.",
        "adjustment_reversed": "Opening-balance adjustment reversed.",
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
        "form": CashboxForm,
        "view_permission": "cashboxes.view_cashboxes",
        "manage_permission": "cashboxes.manage_cashboxes",
        "title": {"ar": "الخزن", "en": "Cashboxes"},
        "singular": {"ar": "خزنة", "en": "Cashbox"},
        "search_fields": ("cashbox_code", "name_ar", "name_en", "currency"),
        "select_related": (),
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


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

    target_types = {
        "customers": OpeningBalanceTarget.CUSTOMER,
        "suppliers": OpeningBalanceTarget.SUPPLIER,
        "cashboxes": OpeningBalanceTarget.CASHBOX,
    }
    target_type = target_types.get(entity)
    opening_balance_locked = bool(
        instance is not None
        and target_type
        and target_has_operational_use(target_type, instance)
    )
    can_adjust_opening = opening_balance_locked and user_has_permission(
        request.user, "master_data.adjust_opening_balances"
    )

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
            "opening_balance_locked": opening_balance_locked,
            "can_adjust_opening": can_adjust_opening,
            "page_title": f"{config['singular'][lang]} - {'Hesba' if lang == 'en' else 'حِسبة'}",
        },
    )


def _opening_target(entity, pk):
    target_map = {
        "customers": (OpeningBalanceTarget.CUSTOMER, Customer),
        "suppliers": (OpeningBalanceTarget.SUPPLIER, Supplier),
        "cashboxes": (OpeningBalanceTarget.CASHBOX, Cashbox),
    }
    if entity not in target_map:
        raise Http404("This entity has no opening balance.")
    target_type, model = target_map[entity]
    return target_type, get_object_or_404(model, pk=pk)


def opening_balance_adjustment(request, entity, pk):
    _require(request.user, "master_data.adjust_opening_balances")
    config = _config(entity)
    _require(request.user, config["view_permission"])
    target_type, target = _opening_target(entity, pk)
    lang = _lang(request)
    words = STRINGS[lang]
    if not target_has_operational_use(target_type, target):
        messages.error(
            request,
            "Edit the opening balance directly before operational use."
            if lang == "en"
            else "عدّل الرصيد الافتتاحي مباشرة قبل بدء الاستخدام التشغيلي.",
        )
        return redirect(
            f"{reverse('master_data:edit', kwargs={'entity': entity, 'pk': pk})}?lang={lang}"
        )

    if request.method == "POST" and request.POST.get("action") == "create":
        form = OpeningBalanceAdjustmentForm(request.POST, lang=lang)
        if form.is_valid():
            try:
                create_opening_balance_adjustment(
                    target_type=target_type,
                    target_id=target.pk,
                    user=request.user,
                    **form.cleaned_data,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, words["adjustment_saved"])
                return redirect(request.get_full_path())
    else:
        form = OpeningBalanceAdjustmentForm(lang=lang)

    adjustments = target.opening_balance_adjustments.select_related(
        "created_by", "cancelled_by"
    )
    return render(
        request,
        "master_data/opening_balance_adjustment.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": words,
            "entity": entity,
            "target": target,
            "target_type": target_type,
            "form": form,
            "reversal_form": OpeningBalanceReversalForm(lang=lang),
            "adjustments": adjustments,
            "page_title": words["adjust_opening"],
        },
    )


def reverse_opening_balance_adjustment(request, entity, pk, adjustment_id):
    _require(request.user, "master_data.adjust_opening_balances")
    target_type, target = _opening_target(entity, pk)
    adjustment = get_object_or_404(
        target.opening_balance_adjustments,
        pk=adjustment_id,
        target_type=target_type,
    )
    lang = _lang(request)
    if request.method == "POST":
        form = OpeningBalanceReversalForm(request.POST, lang=lang)
        if form.is_valid():
            try:
                cancel_opening_balance_adjustment(
                    adjustment.pk, user=request.user, **form.cleaned_data
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, STRINGS[lang]["adjustment_reversed"])
    return redirect(
        f"{reverse('master_data:opening_adjustment', kwargs={'entity': entity, 'pk': pk})}?lang={lang}"
    )
