"""Restaurants R4: what the restaurant demos show of the kitchen screen. Called from ``prepare_demo._floor``; safe to run twice."""

from datetime import timedelta

from django.utils import timezone

from master_data.models import Category

from . import services, stations
from .models import KitchenStation, KitchenTicket


def seed_stations(catalog, owner):
    """Two stations, «المطبخ» (takes everything the bar does not) and «المشروبات» (the bar's category), where the catalog has a bar."""

    if not catalog.bar:
        return []
    from reports.management.commands.seed_demo_business import BAR_CATEGORY_CODE

    bar = Category.objects.filter(category_code=BAR_CATEGORY_CODE).first()
    made = []
    for name_ar, name_en, sort, is_default, categories in (("المطبخ", "Kitchen", 1, True, []), ("المشروبات", "Drinks", 2, False, [bar.pk] if bar else [])):
        station = KitchenStation.objects.filter(name_ar=name_ar).first()
        if station is None:
            station = stations.save_station({"name_ar": name_ar, "name_en": name_en, "sort": sort, "is_default": is_default, "categories": categories}, owner)
        made.append(station)
    return made


def age_tickets(order, minutes_by_station):
    """Backdate a seeded order's tickets (``{station name: minutes ago}``), so the screen shows fresh, amber and red cards."""

    now = timezone.now()
    for ticket in KitchenTicket.objects.filter(order=order).select_related("station"):
        minutes = minutes_by_station.get(ticket.station.name_ar if ticket.station_id else "")
        if minutes is not None:
            KitchenTicket.objects.filter(pk=ticket.pk).update(sent_at=now - timedelta(minutes=minutes))


def mark_ready(order, item):
    """The station marks one seeded line ready (as a tap on the screen does)."""

    line = order.lines.filter(item=item, voided=False).first()
    if line is not None:
        stations.mark_line(line)
    return line


def void_after_firing(order, item, owner, reason):
    line = order.lines.filter(item=item, voided=False, ticket__isnull=False).first()
    if line is not None:
        services.void_line(line, owner, reason=reason)
    return line
