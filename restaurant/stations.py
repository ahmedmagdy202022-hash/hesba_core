"""Restaurants R4 (HG-097): kitchen stations, routing a send to them, and what a station's screen shows.

Rules, stated once:

* an item goes to the station that holds its category (or the nearest parent category a station holds); whatever no station
  holds goes to the default station; with no station set up at all, the whole send is one ticket with no station, exactly as
  before stations existed;
* a category belongs to one station, so where an item goes never depends on which was added first;
* a send is routed to the stations of the order's own entity, and a screen shows the tickets of the orders of the entity being
  worked in (Restaurants R12, HG-108: an order carries its branch), so a branch's food never reaches another branch's screen,
  whoever sent it and from wherever; a ticket with no station follows its order too;
* the screen's actions only move the station's own marks (a line ready, a ticket done, the kitchen having seen a void). They
  never touch the bill, stock, a cashbox or a ledger.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from entities.current import current_entity
from entities.services import main_entity
from master_data.models import Category

from .models import KitchenStation, KitchenTicket, OrderLine, OrderStatus

MESSAGES = {
    "ar": {
        "name": "اكتب اسم المحطة.", "name_taken": "فيه محطة بنفس الاسم.", "sort": "الترتيب لازم يكون رقم.",
        "category_taken": "الفئة «{category}» متربطة بمحطة «{station}»؛ الفئة بتتبعت لمحطة واحدة بس.",
        "has_open": "فيه تذاكر لسه ما خلصتش على المحطة دي؛ خلّصها الأول.",
    },
    "en": {
        "name": "Enter the station's name.", "name_taken": "A station with this name exists.", "sort": "The order must be a number.",
        "category_taken": "The category “{category}” already goes to the station “{station}”; a category goes to one station only.",
        "has_open": "This station still has unfinished tickets; finish them first.",
    },
}

#: How long a ticket waits before the card turns amber, then red (seconds): the age colour on the screen.
WARN_AFTER = 8 * 60
LATE_AFTER = 15 * 60
#: A ticket nobody finished stops showing after a day (a forgotten card must not sit on the screen for ever).
SHOWN_FOR = timedelta(hours=24)
#: «رجّع» can bring back a ticket finished within this long.
RECALL_WITHIN = timedelta(hours=2)


def _audit(station, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="restaurant", action=action, object_type="restaurant.KitchenStation",
                            object_id=str(station.pk), before_data=before or {}, after_data=after)


# --- stations ---------------------------------------------------------------

def working_entity():
    """The entity a send is routed in: the one being worked in, the main entity when working across the group."""

    return current_entity() or main_entity()


def scoped_stations():
    """Stations the person may see and change: the entity being worked in (the whole group sees them all)."""

    return entity_scope.scope(KitchenStation.objects.all(), "entity")


def routing_stations(entity=None):
    """Active stations a send is routed to: only ``entity``'s (the order's; the working entity's when none is said), never another branch's."""

    entity = entity or working_entity()
    condition = Q(entity=entity)
    if entity.is_main:
        condition |= Q(entity__isnull=True)
    return KitchenStation.objects.filter(condition, active=True)


def _holder(categories, entity_filter, station):
    """The other station of the entity already holding one of ``categories``, as (category, station), or None."""

    other = (KitchenStation.objects.filter(entity_filter, categories__in=categories).exclude(pk=getattr(station, "pk", None))
             .order_by("sort", "pk").distinct().first())
    if other is None:
        return None
    clash = next(category for category in categories if other.categories.filter(pk=category.pk).exists())
    return clash, other


@transaction.atomic
def save_station(data, user, station=None, lang="ar"):
    """Create or change a station of the entity being worked in. ``data`` carries name_ar, name_en, sort, active, is_default and category ids."""

    words = MESSAGES[lang]
    name_ar = (data.get("name_ar") or "").strip()[:60]
    if not name_ar:
        raise ValidationError(words["name"])
    entity = station.entity if station is not None else working_entity()
    in_entity = Q(entity=entity) | (Q(entity__isnull=True) if entity.is_main else Q())
    if KitchenStation.objects.filter(in_entity, name_ar__iexact=name_ar).exclude(pk=getattr(station, "pk", None)).exists():
        raise ValidationError(words["name_taken"])
    raw_sort = str(data.get("sort") or "0").strip()
    if not raw_sort.isdigit():
        raise ValidationError(words["sort"])
    active = data.get("active", True) not in (False, "", "0", "off", None)
    if station is not None and station.active and not active and shown_tickets(station).filter(done_at__isnull=True).exists():  # what its screen still shows
        raise ValidationError(words["has_open"])
    wanted = [int(raw) for raw in data.get("categories", []) if str(raw).isdigit()]
    categories = list(Category.objects.filter(pk__in=wanted, active=True).order_by("category_code"))
    taken = _holder(categories, in_entity, station) if categories else None
    if taken:
        raise ValidationError(words["category_taken"].format(category=taken[0].name_ar, station=taken[1].name_ar))
    values = {"name_ar": name_ar, "name_en": (data.get("name_en") or "").strip()[:60], "sort": int(raw_sort), "active": active,
              "is_default": data.get("is_default", False) not in (False, "", "0", "off", None)}
    created = station is None
    before = {} if created else {**{key: getattr(station, key) for key in values}, "categories": sorted(station.categories.values_list("pk", flat=True))}
    station = station or KitchenStation(entity=entity)
    for key, value in values.items():
        setattr(station, key, value)
    station.save()
    station.categories.set(categories)
    if station.is_default:  # one default per entity: the last one chosen
        KitchenStation.objects.filter(in_entity, is_default=True).exclude(pk=station.pk).update(is_default=False)
    _audit(station, user, "create_station" if created else "change_station", {**values, "categories": sorted(category.pk for category in categories)}, before,
           AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return station


# --- routing a send ---------------------------------------------------------

def _category_chain(category_id, parents):
    """The category and its parents, nearest first (a station that holds «المشروبات» also takes what sits under it)."""

    seen = []
    while category_id is not None and category_id not in seen:
        seen.append(category_id)
        category_id = parents.get(category_id)
    return seen


def route(lines, entity=None):
    """Split the lines of one send by station: ``[(station or None, [lines])]``, in station order, the unassigned group last.

    No station set up: one group, no station (the send is one ticket, as it always was)."""

    lines = list(lines)
    stations = list(routing_stations(entity).prefetch_related("categories"))
    if not stations:
        return [(None, lines)]
    holder, default = {}, None
    for station in stations:
        default = default or (station if station.is_default else None)
        for category in station.categories.all():
            holder.setdefault(category.pk, station)
    parents = dict(Category.objects.values_list("pk", "parent_id")) if holder else {}
    groups = {}
    for line in lines:
        station = next((holder[pk] for pk in _category_chain(line.item.category_id, parents) if pk in holder), None) or default
        groups.setdefault(station.pk if station else None, (station, []))[1].append(line)
    ordered = [groups[station.pk] for station in stations if station.pk in groups]
    return ordered + ([groups[None]] if None in groups else [])


# --- the screen -------------------------------------------------------------

def shown_tickets(station=None, now=None):
    """Tickets on a station's screen (all of the entity's stations when ``station`` is None): the ones not finished, and a finished
    one that still carries a void the kitchen has not seen. Scoped by the order's entity (R12); oldest first."""

    now = now or timezone.now()
    tickets = entity_scope.scope(KitchenTicket.objects.all(), "order__entity").filter(sent_at__gte=now - SHOWN_FOR)
    if station is not None:
        tickets = tickets.filter(station=station)
    tickets = tickets.filter(Q(done_at__isnull=True) | Q(lines__voided=True, lines__ready_at__isnull=True)).distinct()
    return (tickets.select_related("order", "order__table", "order__split_from__table", "order__waiter", "station")
            .prefetch_related("lines__item").order_by("sent_at", "pk"))


def last_done(station=None, now=None):
    """The ticket finished most recently (within ``RECALL_WITHIN``), which «رجّع» brings back."""

    now = now or timezone.now()
    tickets = entity_scope.scope(KitchenTicket.objects.all(), "order__entity").filter(done_at__gte=now - RECALL_WITHIN)
    if station is not None:
        tickets = tickets.filter(station=station)
    return tickets.select_related("order", "order__table", "station").order_by("-done_at", "-pk").first()


def level(age_seconds):
    return "late" if age_seconds >= LATE_AFTER else "warn" if age_seconds >= WARN_AFTER else "fresh"


def ticket_card(ticket, now, kind_label, table_word, lang="ar"):
    """One ticket as the screen's feed carries it. ``now`` is the server's, so ages never depend on the tablet's clock."""

    from .modifiers import addon_label, grouped

    order = ticket.order
    age = max(0, int((now - ticket.sent_at).total_seconds()))
    lines = []
    for line, addons in grouped(ticket.lines.all()):
        row = {"id": line.pk, "name": line.item.item_name, "quantity": str(line.quantity.normalize() if line.quantity % 1 else int(line.quantity)),
               "note": line.note, "ready": bool(line.ready_at) and not line.voided, "void": line.voided, "reason": line.void_reason,
               "seen": bool(line.ready_at) if line.voided else True}
        # R1a (HG-109): a dish carries its free choices and its add-ons as ``extras`` under it (only when it has any, so a
        # plain dish's entry is as before); an add-on is never a line of its own
        extras = ([line.options] if line.options else []) + [addon_label(addon) for addon in addons]
        if extras:
            row["extras"] = extras
        lines.append(row)
    return {"id": ticket.pk, "sequence": ticket.sequence, "number": order.number, "age": age, "level": level(age),
            "where": f"{table_word} {order.home_table.name}" if order.home_table else kind_label,  # R2a: a part's ticket names its table's
            "waiter": order.waiter.name if order.waiter_id else "", "station": ticket.station.label(lang) if ticket.station_id else "",
            "cancelled": order.status == OrderStatus.CANCELLED, "done": bool(ticket.done_at), "notes": order.notes, "lines": lines,
            "sent": timezone.localtime(ticket.sent_at).strftime("%H:%M")}


@transaction.atomic
def mark_line(line, now=None):
    """Tap a line: ready, tapped again: not ready. A voided line is the kitchen seeing the void (tapped again changes nothing).
    A line on a finished ticket can only be a void being seen."""

    now = now or timezone.now()
    # ``of=("self",)``: the ticket is a nullable join, and PostgreSQL refuses to lock the nullable side of an outer join
    line = OrderLine.objects.select_for_update(of=("self",)).select_related("ticket").get(pk=line.pk)
    if line.ticket_id is None or line.parent_id:  # R1a: an add-on is tapped with its dish, never on its own
        return line
    if line.voided:
        if line.ready_at is None:
            line.ready_at = now
            line.save(update_fields=["ready_at"])
            line.children.filter(voided=True, ready_at__isnull=True).update(ready_at=now)  # its add-ons' void is seen with it
    elif line.ticket.done_at is None:
        line.ready_at = None if line.ready_at else now
        line.save(update_fields=["ready_at"])
        line.children.update(ready_at=line.ready_at)  # its add-ons are ready with it
    return line


@transaction.atomic
def finish_ticket(ticket, now=None):
    """Tap the card: the whole ticket is done. Every live line not yet ready becomes ready and every unseen void is seen, all
    with the one stamp, so «رجّع» can undo exactly what this did."""

    now = now or timezone.now()
    ticket = KitchenTicket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.done_at is None:
        OrderLine.objects.filter(ticket=ticket, ready_at__isnull=True).update(ready_at=now)
        ticket.done_at = now
        ticket.save(update_fields=["done_at"])
    else:  # finished already: only the voids that came after still need seeing
        OrderLine.objects.filter(ticket=ticket, voided=True, ready_at__isnull=True).update(ready_at=now)
    return ticket


@transaction.atomic
def recall_ticket(ticket):
    """«رجّع»: a finished ticket is back on the screen as it was before it was finished."""

    ticket = KitchenTicket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.done_at is not None:
        OrderLine.objects.filter(ticket=ticket, ready_at=ticket.done_at).update(ready_at=None)
        ticket.done_at = None
        ticket.save(update_fields=["done_at"])
    return ticket
