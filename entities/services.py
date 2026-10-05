"""ENT-001: entities and the group-wide view of stock."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from audit.models import AuditEventType, AuditLog

from .models import Entity, EntityKind

ZERO = Decimal("0")

MESSAGES = {
    "ar": {"code": "اكتب كود للكيان.", "code_taken": "الكود ده مستخدم لكيان تاني.", "name": "اكتب اسم الكيان.",
           "main_inactive": "الكيان الرئيسي مينفعش يتوقف.", "in_use": "الكيان ده عليه مخازن أو خزن نشطة، انقلهم الأول."},
    "en": {"code": "Enter a code for the entity.", "code_taken": "Another entity already uses this code.", "name": "Enter the entity's name.",
           "main_inactive": "The main entity cannot be deactivated.", "in_use": "This entity still has active locations or cashboxes; move them first."},
}


def main_entity():
    """The entity every pre-ENT-001 record belongs to; created on first need."""

    entity = Entity.objects.filter(is_main=True).first()
    if entity is not None:
        return entity
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    name = (profile.display_name if profile is not None else "") or "الكيان الرئيسي"
    entity, _ = Entity.objects.get_or_create(code="MAIN", defaults={"name_ar": name, "name_en": "Main entity", "is_main": True})
    return entity


def entities(active_only=True):
    qs = Entity.objects.all()
    return qs.filter(active=True) if active_only else qs


def is_multi_entity():
    return Entity.objects.filter(active=True).count() > 1


def activity_of(entity):
    """The entity's activity, falling back to the installation's."""

    if entity is not None and entity.activity_slug:
        return entity.activity_slug, entity.sub_activity_slug
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return ((profile.activity_slug or ""), (profile.sub_activity_slug or "")) if profile else ("", "")


@transaction.atomic
def save_entity(data, user, entity=None, lang="ar"):
    words = MESSAGES["en" if lang == "en" else "ar"]
    code = (data.get("code") or "").strip().upper()[:20]
    name = (data.get("name_ar") or "").strip()[:255]
    if not code:
        raise ValidationError(words["code"])
    if not name:
        raise ValidationError(words["name"])
    if Entity.objects.filter(code=code).exclude(pk=getattr(entity, "pk", None)).exists():
        raise ValidationError(words["code_taken"])
    active = data.get("active", True) not in (False, "0", "", None)
    if entity is not None and entity.is_main and not active:
        raise ValidationError(words["main_inactive"])
    if entity is not None and not active and (entity.locations.filter(active=True).exists() or entity.cashboxes.filter(active=True).exists()):
        raise ValidationError(words["in_use"])
    values = {
        "code": code, "name_ar": name, "name_en": (data.get("name_en") or "").strip()[:255],
        "kind": data.get("kind") if data.get("kind") in EntityKind.values else EntityKind.BRANCH,
        "activity_slug": (data.get("activity_slug") or "").strip()[:40], "sub_activity_slug": (data.get("sub_activity_slug") or "").strip()[:40],
        "tax_registration_number": (data.get("tax_registration_number") or "").strip()[:50],
        "document_prefix": (data.get("document_prefix") or "").strip().upper()[:10], "active": active,
    }
    created = entity is None
    before = {} if created else {key: str(getattr(entity, key)) for key in values}
    if created:
        main_entity()  # the group always has its main entity first
        entity = Entity(**values)
    else:
        for key, value in values.items():
            setattr(entity, key, value)
    entity.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if created else AuditEventType.UPDATE, actor=user, module="entities",
                            action="create_entity" if created else "update_entity", object_type="entities.Entity", object_id=str(entity.pk),
                            before_data=before, after_data={key: str(value) for key, value in values.items()})
    return entity


def group_stock(items, include_cost=False):
    """{item_id: [{entity, location, quantity, value?}]} over every active location of every entity.

    Quantities come from stock movements, the same way the stock report counts
    them; a location with nothing on hand is left out.
    """

    from inventory.services import get_item_authoritative_average_cost
    from master_data.models import Location
    from reports.selectors import STOCK_IN_TYPES, STOCK_OUT_TYPES
    from inventory.models import StockMovement

    item_ids = [item.pk for item in items]
    rows = (
        StockMovement.objects.filter(item_id__in=item_ids, location__active=True)
        .values("item_id", "location_id")
        .annotate(in_qty=Sum("quantity", filter=Q(movement_type__in=STOCK_IN_TYPES)),
                  out_qty=Sum("quantity", filter=Q(movement_type__in=STOCK_OUT_TYPES)))
    )
    locations = {loc.pk: loc for loc in Location.objects.filter(active=True).select_related("entity")}
    costs = {item.pk: get_item_authoritative_average_cost(item) for item in items} if include_cost else {}
    out = defaultdict(list)
    for row in rows:
        quantity = (row["in_qty"] or ZERO) - (row["out_qty"] or ZERO)
        location = locations.get(row["location_id"])
        if location is None or quantity == 0:
            continue
        entry = {"entity": location.entity, "location": location, "quantity": quantity}
        if include_cost:
            entry["value"] = (quantity * costs[row["item_id"]]).quantize(Decimal("0.01"))
        out[row["item_id"]].append(entry)
    for entries in out.values():
        entries.sort(key=lambda e: (not (e["entity"] and e["entity"].is_main), e["entity"].code if e["entity"] else "", e["location"].location_code))
    return out


def stock_elsewhere(item, location):
    """Where else this item is on hand, for 'not here? it is there' hints."""

    return [row for row in group_stock([item]).get(item.pk, []) if row["location"].pk != getattr(location, "pk", None) and row["quantity"] > 0]
