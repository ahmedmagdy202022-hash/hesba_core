from datetime import date

from django.db.models import Q
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from master_data.models import Item, Location
from approvals.gate import held
from permissions.decorators import require_any_permission, require_permission
from permissions.services import user_has_permission
from settings_core.display_labels import localized_choices

from .forms import StockAdjustmentForm, StockOperationReversalForm, StockTransferForm
from .models import StockMovement, StockOperation, StockOperationType
from .services import (
    adjust_stock,
    cancel_stock_operation,
    get_item_location_stock_quantity,
    get_item_stock_quantity,
    transfer_stock,
)
from entities import scope as entity_scope
from config.paging import page_of


STRINGS = {
    "ar": {
        "page_title": "المخزون",
        "stock": "المخزون حسب الصنف والموقع",
        "movements": "سجل حركات المخزون",
        "search": "بحث",
        "all_locations": "كل المواقع",
        "all_types": "كل أنواع الحركة",
        "empty": "لا توجد نتائج مطابقة.",
        "empty_ops": "مفيش تحويلات أو تسويات لسه.",
        "dashboard": "لوحة القيادة",
        "language": "English",
        "back": "العودة للمخزون",
        "low": "مخزون منخفض",
        "out": "نفد المخزون",
        "healthy": "متاح",
        "operations": "التحويلات والتسويات",
        "transfer": "تحويل مخزون",
        "adjustment": "تسوية مخزون",
        "saved": "تم تسجيل حركة المخزون.",
        "reversed": "تم عكس حركة المخزون.",
    },
    "en": {
        "page_title": "Inventory",
        "stock": "Stock by item and location",
        "movements": "Stock movement history",
        "search": "Search",
        "all_locations": "All locations",
        "all_types": "All movement types",
        "empty": "No matching results.",
        "empty_ops": "No transfers or adjustments yet.",
        "dashboard": "Dashboard",
        "language": "العربية",
        "back": "Back to inventory",
        "low": "Low stock",
        "out": "Out of stock",
        "healthy": "Available",
        "operations": "Transfers and adjustments",
        "transfer": "Stock transfer",
        "adjustment": "Stock adjustment",
        "saved": "Stock operation posted.",
        "reversed": "Stock operation reversed.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


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
    locations = entity_scope.locations(Location.objects).filter(active=True)
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
    page = page_of(request, items)
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
    if can_view_cost:
        sold_out = last_costs(row["item"].pk for row in rows if row["quantity"] <= 0)
        for row in rows:
            row["last_cost"] = sold_out.get(row["item"].pk)

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
            can_transfer=user_has_permission(request.user, "inventory.transfer_stock"),
            can_adjust=user_has_permission(request.user, "inventory.adjust_stock"),
            # DEMO-FEEDBACK: warehouses were only reachable by typing the URL.
            can_view_locations=user_has_permission(request.user, "master_data.view_master_data"),
            can_group_stock=user_has_permission(request.user, "inventory.view_group_stock"),
            can_add_location=user_has_permission(request.user, "master_data.manage_locations"),
        ),
    )




def last_costs(item_ids):
    """HG-052 (display only): the unit cost of each item's latest purchase or opening stock.

    Shown in place of the average cost when nothing is on hand, so a sold-out item does not read
    as costing 0.00. Only the entity being worked in counts; a cancelled purchase, a reversal row
    and the stock a cancelled return brought back are not purchases. A zero price is a real price.
    ``Item.average_cost`` and the costing logic are not touched."""

    from purchases.models import PurchaseInvoiceStatus

    from .models import StockMovementType

    costs = {}
    movements = (StockMovement.objects
                 .filter(item_id__in=list(item_ids), reversal_of__isnull=True, purchase_return__isnull=True,
                         movement_type__in=(StockMovementType.PURCHASE_IN, StockMovementType.OPENING_STOCK))
                 .exclude(purchase_invoice__status=PurchaseInvoiceStatus.CANCELLED))
    rows = (entity_scope.scope(movements, entity_scope.STOCK_MOVEMENT)
            .order_by("item_id", "-movement_date", "-id").values_list("item_id", "unit_cost"))
    for item_id, cost in rows:
        costs.setdefault(item_id, cost)
    return costs


@require_permission("inventory.view_stock")
def item_detail(request, pk):
    item = get_object_or_404(Item, pk=pk, active=True, is_stock_tracked=True)
    locations = entity_scope.locations(Location.objects).filter(active=True)
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
    movements = entity_scope.scope(item.stock_movements.select_related("location"), entity_scope.STOCK_MOVEMENT)[:100]
    # UI-TRACK grocery G02: what of this item is on the shelf, batch by batch, soonest expiry first.
    from batches.services import DEFAULT_WARN_DAYS, batch_positions, batches_enabled, expiry_state
    from batches.views import WORDS as BATCH_WORDS
    from batches.words import expiry_only, words as expiry_words

    batch_rows = []
    if batches_enabled():
        today = timezone.localdate()
        # Codex #234: each warehouse shares out only its own stock among its own batches.
        for location in locations:
            for row in batch_positions(items=[item], locations=[location])[0]:
                if row["remaining"] > 0:
                    batch_rows.append({**row, "state": expiry_state(row["batch"].expiry_date, today, DEFAULT_WARN_DAYS)})
        batch_rows.sort(key=lambda row: (row["batch"].expiry_date or date.max, row["batch"].pk))
    return render(
        request,
        "inventory/item_detail.html",
        _context(
            request,
            item=item,
            location_rows=location_rows,
            movements=movements,
            batch_rows=batch_rows,
            batch_words={**BATCH_WORDS["en" if _lang(request) == "en" else "ar"], **expiry_words(_lang(request))},  # AN8
            show_batch_no=not expiry_only(),
            can_view_cost=user_has_permission(request.user, "inventory.view_cost"),
            last_cost=last_costs([item.pk]).get(item.pk) if all(row["quantity"] <= 0 for row in location_rows) else None,
        ),
    )


@require_permission("inventory.view_stock")
def movement_list(request):
    queryset = entity_scope.scope(StockMovement.objects.select_related(
        "item", "location", "created_by", "purchase_invoice", "sales_invoice", "stock_operation",
        "purchase_return", "sales_return"
    ), entity_scope.STOCK_MOVEMENT)
    query = request.GET.get("q", "").strip()
    movement_type = request.GET.get("type", "").strip()
    location_id = request.GET.get("location", "").strip()
    if query:
        queryset = queryset.filter(
            Q(item__item_code__icontains=query)
            | Q(item__item_name__icontains=query)
            | Q(notes__icontains=query)
            | Q(purchase_return__return_number__icontains=query)
            | Q(sales_return__return_number__icontains=query)
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
    page = page_of(request, queryset)
    return render(
        request,
        "inventory/movements.html",
        _context(
            request,
            page=page,
            query=query,
            movement_type=movement_type,
            movement_choices=localized_choices(StockMovement, "movement_type", _lang(request)),
            location_id=location_id,
            locations=entity_scope.locations(Location.objects).filter(active=True),
            can_view_cost=user_has_permission(request.user, "inventory.view_cost"),
        ),
    )


@require_any_permission("inventory.transfer_stock", "inventory.adjust_stock")
def operation_list(request):
    operations = entity_scope.scope(StockOperation.objects.select_related(
        "item", "source_location", "destination_location", "created_by", "cancelled_by"
    ), entity_scope.STOCK_OPERATION).distinct()
    page = page_of(request, operations)
    return render(
        request,
        "inventory/operations.html",
        _context(
            request,
            page=page,
            can_transfer=user_has_permission(request.user, "inventory.transfer_stock"),
            can_adjust=user_has_permission(request.user, "inventory.adjust_stock"),
            reversal_form=StockOperationReversalForm(lang=_lang(request)),
        ),
    )


@require_permission("inventory.transfer_stock")
def transfer_create(request):
    lang = _lang(request)
    start = request.GET.get("from", "")
    # R2-7: "transfer from here" on a warehouse page names the source.
    form = StockTransferForm(request.POST or None, lang=lang, initial={"source_location": start} if start.isdigit() else None)
    if request.method == "POST" and form.is_valid():
        try:
            form.create_with_fresh_number(lambda: transfer_stock(user=request.user, **form.cleaned_data))  # UI-TRACK F10
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, STRINGS[lang]["saved"])
            return redirect(f"/inventory/operations/?lang={lang}")
    return render(
        request,
        "inventory/operation_form.html",
        _context(request, form=form, title=STRINGS[lang]["transfer"]),
    )


@require_permission("inventory.adjust_stock")
def adjustment_create(request):
    lang = _lang(request)
    can_view_cost = user_has_permission(request.user, "inventory.view_cost")
    form = StockAdjustmentForm(
        request.POST or None, lang=lang, can_view_cost=can_view_cost
    )
    if request.method == "POST" and form.is_valid():
        try:
            form.create_with_fresh_number(lambda: adjust_stock(user=request.user, **form.cleaned_data))  # UI-TRACK F10
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, STRINGS[lang]["saved"])
            return redirect(f"/inventory/operations/?lang={lang}")
    return render(
        request,
        "inventory/operation_form.html",
        _context(request, form=form, title=STRINGS[lang]["adjustment"]),
    )


@require_any_permission("inventory.transfer_stock", "inventory.adjust_stock")
def operation_cancel(request, pk):
    if request.method != "POST":
        return redirect("inventory:operations")
    lang = _lang(request)
    operation = entity_scope.get_or_404(StockOperation.objects.distinct(), entity_scope.STOCK_OPERATION, pk=pk)
    form = StockOperationReversalForm(request.POST, lang=lang)
    if form.is_valid() and held(request, "inventory.reverse_operation", pk, form.cleaned_data, operation.reference_number):  # FB-27
        return redirect(f"/inventory/operations/?lang={lang}")
    if form.is_valid():
        try:
            cancel_stock_operation(pk, user=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, STRINGS[lang]["reversed"])
    else:
        messages.error(request, "; ".join(error for errors in form.errors.values() for error in errors))
    return redirect(f"/inventory/operations/?lang={lang}")
