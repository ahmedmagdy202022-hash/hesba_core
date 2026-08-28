from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from master_data.models import Item, Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .models import StockMovement
from .services import get_item_location_stock_quantity, get_item_stock_quantity


STRINGS = {
    "ar": {
        "page_title": "المخزون",
        "stock": "المخزون حسب الصنف والموقع",
        "movements": "سجل حركات المخزون",
        "search": "بحث",
        "all_locations": "كل المواقع",
        "all_types": "كل أنواع الحركة",
        "empty": "لا توجد نتائج مطابقة.",
        "dashboard": "لوحة القيادة",
        "language": "English",
        "back": "العودة للمخزون",
        "low": "مخزون منخفض",
        "out": "نفد المخزون",
        "healthy": "متاح",
        "blocked": "التحويل والتسوية غير متاحين حتى اعتماد خدمات الحركة المحمية.",
    },
    "en": {
        "page_title": "Inventory",
        "stock": "Stock by item and location",
        "movements": "Stock movement history",
        "search": "Search",
        "all_locations": "All locations",
        "all_types": "All movement types",
        "empty": "No matching results.",
        "dashboard": "Dashboard",
        "language": "العربية",
        "back": "Back to inventory",
        "low": "Low stock",
        "out": "Out of stock",
        "healthy": "Available",
        "blocked": "Transfers and adjustments remain unavailable until protected movement services are approved.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "words": STRINGS[lang],
        "section": "inventory",
        "page_title": STRINGS[lang]["page_title"],
    }
    context.update(extra)
    return context


def _stock_state(quantity, minimum):
    if quantity <= 0:
        return "out"
    if minimum > 0 and quantity <= minimum:
        return "low"
    return "healthy"


@require_permission("inventory.view_stock")
def stock_list(request):
    locations = Location.objects.filter(active=True)
    selected_location = None
    location_id = request.GET.get("location", "").strip()
    if location_id.isdigit():
        selected_location = locations.filter(pk=location_id).first()

    query = request.GET.get("q", "").strip()
    items = Item.objects.filter(active=True, is_stock_tracked=True).select_related("category")
    if query:
        items = items.filter(
            Q(item_code__icontains=query)
            | Q(item_name__icontains=query)
            | Q(barcode__icontains=query)
        )
    page = Paginator(items, 25).get_page(request.GET.get("page"))
    can_view_cost = user_has_permission(request.user, "inventory.view_cost")
    rows = []
    counts = {"healthy": 0, "low": 0, "out": 0}
    for item in page.object_list:
        quantity = (
            get_item_location_stock_quantity(item, selected_location)
            if selected_location
            else get_item_stock_quantity(item)
        )
        state = _stock_state(quantity, item.min_stock)
        counts[state] += 1
        rows.append({"item": item, "quantity": quantity, "state": state})

    return render(
        request,
        "inventory/stock.html",
        _context(
            request,
            rows=rows,
            page=page,
            query=query,
            locations=locations,
            selected_location=selected_location,
            counts=counts,
            can_view_cost=can_view_cost,
        ),
    )


@require_permission("inventory.view_stock")
def item_detail(request, pk):
    item = get_object_or_404(Item, pk=pk, active=True, is_stock_tracked=True)
    locations = Location.objects.filter(active=True)
    location_rows = []
    for location in locations:
        quantity = get_item_location_stock_quantity(item, location)
        location_rows.append(
            {
                "location": location,
                "quantity": quantity,
                "state": _stock_state(quantity, item.min_stock),
            }
        )
    movements = item.stock_movements.select_related("location").all()[:100]
    return render(
        request,
        "inventory/item_detail.html",
        _context(
            request,
            item=item,
            location_rows=location_rows,
            movements=movements,
            can_view_cost=user_has_permission(request.user, "inventory.view_cost"),
        ),
    )


@require_permission("inventory.view_stock")
def movement_list(request):
    queryset = StockMovement.objects.select_related(
        "item", "location", "created_by", "purchase_invoice", "sales_invoice"
    )
    query = request.GET.get("q", "").strip()
    movement_type = request.GET.get("type", "").strip()
    location_id = request.GET.get("location", "").strip()
    if query:
        queryset = queryset.filter(
            Q(item__item_code__icontains=query)
            | Q(item__item_name__icontains=query)
            | Q(notes__icontains=query)
        )
    valid_types = {value for value, _ in StockMovement._meta.get_field("movement_type").choices}
    if movement_type in valid_types:
        queryset = queryset.filter(movement_type=movement_type)
    else:
        movement_type = ""
    if location_id.isdigit():
        queryset = queryset.filter(location_id=location_id)
    else:
        location_id = ""
    page = Paginator(queryset, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "inventory/movements.html",
        _context(
            request,
            page=page,
            query=query,
            movement_type=movement_type,
            movement_choices=StockMovement._meta.get_field("movement_type").choices,
            location_id=location_id,
            locations=Location.objects.filter(active=True),
            can_view_cost=user_has_permission(request.user, "inventory.view_cost"),
        ),
    )
