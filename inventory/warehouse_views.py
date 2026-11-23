"""R2-7: the warehouses hub and one warehouse's page."""

from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from entities import scope as entity_scope
from master_data.models import Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import transfer_requests, warehouses
from .models import TransferRequest, TransferRequestStatus
from .views import _context, _lang


def _flags(user):
    return {
        "can_view_cost": user_has_permission(user, "inventory.view_cost"),
        "can_transfer": user_has_permission(user, "inventory.transfer_stock"),
        "can_adjust": user_has_permission(user, "inventory.adjust_stock"),
        "can_manage_locations": user_has_permission(user, "master_data.manage_locations"),
    }


@require_permission("inventory.view_stock")
def warehouse_list(request):
    flags = _flags(request.user)
    locations = entity_scope.locations(Location.objects).filter(active=True).order_by("-is_default", "location_code")
    cards = warehouses.summaries(locations, timezone.localdate(), with_value=flags["can_view_cost"])
    # HG-037: goods in transit show only while something is on the way.
    cards = [card for card in cards if card["items"] or not card["location"].location_code.startswith("TRANSIT-")]
    below = transfer_requests.below_counts([card["location"] for card in cards])
    for card in cards:
        card["below"] = below.get(card["location"].pk, 0)
    open_requests = TransferRequest.objects.filter(status__in=(TransferRequestStatus.REQUESTED, TransferRequestStatus.SENT)).filter(
        Q(source__in=locations) | Q(destination__in=locations)).count()
    total = sum((card["value"] for card in cards), warehouses.ZERO) if flags["can_view_cost"] else None
    return render(request, "inventory/warehouses.html", _context(request, cards=cards, total_value=total, open_requests=open_requests, section="warehouses", **flags))


@require_permission("inventory.view_stock")
def warehouse_detail(request, pk):
    flags = _flags(request.user)
    location = get_object_or_404(entity_scope.locations(Location.objects), pk=pk)
    data = warehouses.detail(location, timezone.localdate(), with_value=flags["can_view_cost"], lang=_lang(request))
    places = entity_scope.locations(Location.objects).filter(active=True).exclude(location_code__startswith="TRANSIT-")
    levels = transfer_requests.level_rows(location, list(places))
    return render(request, "inventory/warehouse_detail.html", _context(
        request, location=location, slow_days=warehouses.SLOW_DAYS, section="warehouses", levels=levels,
        below=[row for row in levels if row["below"]], **data, **flags))
