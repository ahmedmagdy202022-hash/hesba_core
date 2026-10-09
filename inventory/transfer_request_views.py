"""HG-037 screens: a warehouse's min/max levels, and transfer requests."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from entities import scope as entity_scope
from master_data.models import Item, Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.display_labels import choice_label

from . import transfer_requests as tr
from .models import LocationStockLevel, TransferRequest, TransferRequestStatus
from .views import _context, _lang

EXTRA_ROWS = 3
NEW_ROWS = 8


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(exc.messages), lang)


def _places(request):
    return list(entity_scope.locations(Location.objects).filter(active=True).exclude(location_code__startswith="TRANSIT-").order_by("-is_default", "location_code"))


def _stock_items():
    return list(Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_code"))


def _place(places, value):
    return next((place for place in places if str(place.pk) == str(value or "")), None)


def _item(items, value):
    return next((item for item in items if str(item.pk) == str(value or "")), None)


def _may_cancel(item, user, can_transfer):
    """Before sending, the requester may withdraw it. Once sent, cancelling
    returns goods from transit, which is a transfer: transfer users only."""

    if item.status == TransferRequestStatus.REQUESTED:
        return can_transfer or item.requested_by_id == user.pk
    return item.status == TransferRequestStatus.SENT and can_transfer


@require_permission("inventory.view_stock")
def levels(request, pk):
    lang = _lang(request)
    location = get_object_or_404(entity_scope.locations(Location.objects), pk=pk)
    can_edit = user_has_permission(request.user, "master_data.manage_locations")
    # An item made inactive after its level was set stays here, so the level can be removed.
    configured = LocationStockLevel.objects.filter(location=location).values_list("item_id", flat=True)
    items = list(Item.objects.filter(is_stock_tracked=True).filter(Q(active=True) | Q(pk__in=configured)).order_by("item_code"))
    error = ""
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Setting warehouse levels needs master_data.manage_locations.")
        rows, index = [], 0
        while f"item_{index}" in request.POST:
            rows.append((_item(items, request.POST.get(f"item_{index}")), request.POST.get(f"min_{index}"), request.POST.get(f"max_{index}")))
            index += 1
        try:
            tr.save_levels(location, rows, request.user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, "اتحفظت الحدود." if lang == "ar" else "Levels saved.")
            return redirect(f"{reverse('inventory:warehouse_detail', args=[location.pk])}?lang={lang}")
    existing = {level.item_id: level for level in LocationStockLevel.objects.filter(location=location)}
    held = tr._on_hand([item.pk for item in items], [location.pk])
    shown = [item for item in items if item.pk in existing or held[(item.pk, location.pk)] > 0]
    rows = [{"item": item, "have": held[(item.pk, location.pk)], "level": existing.get(item.pk)} for item in shown]
    return render(request, "inventory/warehouse_levels.html", _context(
        request, location=location, rows=rows, items=items, extra=range(len(rows), len(rows) + EXTRA_ROWS), error=error, can_edit=can_edit,
        section="warehouses"))


@require_permission("inventory.view_stock")
def request_list(request):
    places = entity_scope.locations(Location.objects)
    requests = (TransferRequest.objects.filter(Q(source__in=places) | Q(destination__in=places))
                .select_related("source", "destination", "requested_by").prefetch_related("lines"))
    show = request.GET.get("show", "open")
    if show == "open":
        requests = requests.filter(status__in=(TransferRequestStatus.REQUESTED, TransferRequestStatus.SENT))
    lang = _lang(request)
    rows = [{"request": item, "status_label": choice_label(item, "status", lang)} for item in requests[:200]]
    return render(request, "inventory/transfer_requests.html", _context(request, rows=rows, show=show, section="warehouses",
                                                                        can_transfer=user_has_permission(request.user, "inventory.transfer_stock")))


@require_permission("inventory.view_stock")
def request_new(request):
    lang = _lang(request)
    places, items = _places(request), _stock_items()
    error = ""
    source = _place(places, request.POST.get("source") or request.GET.get("from"))
    destination = _place(places, request.POST.get("destination") or request.GET.get("to"))
    if request.method == "POST":
        lines = [(_item(items, request.POST.get(f"item_{i}")), request.POST.get(f"qty_{i}")) for i in range(NEW_ROWS)]
        try:
            made = tr.create_request(source, destination, lines, request.user, request.POST.get("note", ""), lang)
        except ValidationError as exc:
            error = _error(exc, lang)
            prefill = [(request.POST.get(f"item_{i}", ""), request.POST.get(f"qty_{i}", "")) for i in range(NEW_ROWS)]
        else:
            messages.success(request, "الطلب اتبعت للمخزن." if lang == "ar" else "Request sent to the warehouse.")
            return redirect(f"{reverse('inventory:transfer_request', args=[made.pk])}?lang={lang}")
    else:
        prefill = []
        if destination is not None and request.GET.get("refill"):
            # Everything at or below its minimum here, from the warehouse with the most to spare.
            for row in tr.level_rows(destination, places):
                if row["below"] and row["can_fill"] > 0 and (source is None or row["source"] == source):
                    source = source or row["source"]
                    prefill.append((str(row["item"].pk), str(row["can_fill"].normalize())))
        prefill = (prefill + [("", "")] * NEW_ROWS)[:NEW_ROWS]
    return render(request, "inventory/transfer_request_new.html", _context(
        request, places=places, items=items, source=source, destination=destination, prefill=prefill, error=error,
        note=request.POST.get("note", ""), section="warehouses"))


@require_permission("inventory.view_stock")
def request_detail(request, pk):
    lang = _lang(request)
    places = entity_scope.locations(Location.objects)
    item = get_object_or_404(TransferRequest.objects.filter(Q(source__in=places) | Q(destination__in=places)).select_related("source", "destination"), pk=pk)
    can_transfer = user_has_permission(request.user, "inventory.transfer_stock")
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("send", "receive") and not can_transfer:
            raise PermissionDenied("Sending and receiving need inventory.transfer_stock.")
        if action == "cancel" and not _may_cancel(item, request.user, can_transfer):
            raise PermissionDenied("Before sending, the requester or a transfer user may cancel; after sending, only a transfer user.")
        quantities = {key.split("_", 1)[1]: value for key, value in request.POST.items() if key.startswith("qty_")}
        try:
            if action == "send":
                tr.send(item, request.user, quantities, lang)
                note = "اتبعت؛ البضاعة في الطريق." if lang == "ar" else "Sent; the goods are in transit."
            elif action == "receive":
                tr.receive(item, request.user, quantities, lang)
                note = "اتستلم ودخل المخزن." if lang == "ar" else "Received into the warehouse."
            elif action == "cancel":
                tr.cancel(item, request.user, request.POST.get("reason", ""), lang)
                note = "الطلب اتلغى." if lang == "ar" else "Request cancelled."
            else:
                raise ValidationError("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, note)
            return redirect(f"{reverse('inventory:transfer_request', args=[item.pk])}?lang={lang}")
        item.refresh_from_db()
    lines = list(item.lines.select_related("item"))
    return render(request, "inventory/transfer_request.html", _context(
        request, item=item, lines=lines, status_label=choice_label(item, "status", lang), error=error, can_transfer=can_transfer,
        can_cancel=_may_cancel(item, request.user, can_transfer),
        section="warehouses"))
