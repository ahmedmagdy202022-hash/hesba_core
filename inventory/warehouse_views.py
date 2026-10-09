"""R2-7: the warehouses hub and one warehouse's page."""

from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from entities import scope as entity_scope
from master_data.models import Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import warehouses
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
    total = sum((card["value"] for card in cards), warehouses.ZERO) if flags["can_view_cost"] else None
    return render(request, "inventory/warehouses.html", _context(request, cards=cards, total_value=total, section="warehouses", **flags))


@require_permission("inventory.view_stock")
def warehouse_detail(request, pk):
    flags = _flags(request.user)
    location = get_object_or_404(entity_scope.locations(Location.objects), pk=pk)
    data = warehouses.detail(location, timezone.localdate(), with_value=flags["can_view_cost"], lang=_lang(request))
    return render(request, "inventory/warehouse_detail.html", _context(
        request, location=location, slow_days=warehouses.SLOW_DAYS, section="warehouses", **data, **flags))
