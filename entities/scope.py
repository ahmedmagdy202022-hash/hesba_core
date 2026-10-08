"""HG-034: the entity data scope.

``current_entity()`` (entities/current.py) is the entity being worked in, or
None for the whole group. Every list, document page, report, dashboard figure
and document form narrows its queryset with the helpers here, so one rule
holds everywhere: what you see and what you can pick belong to the entity
you are working in. A restricted user always has one (they cannot choose
the whole group), so for them it is a permission; for the owner it is a
working filter.

Records without an entity (made before ENT-001) belong to the main entity.
Customer and supplier ledger rows belong to the entity of the document that
wrote them, exactly as the general ledger projects them (ledger/projector.py);
party opening balances belong to the main entity.
"""

from django.db.models import Q

from .current import current_entity

#: Where each document finds its entity.
SALES_INVOICE = "selling_location__entity"
SALES_RETURN = "source_invoice__selling_location__entity"
CUSTOMER_PAYMENT = "cashbox__entity"
PURCHASE_INVOICE = "receiving_location__entity"
PURCHASE_RETURN = "source_invoice__receiving_location__entity"
SUPPLIER_PAYMENT = "cashbox__entity"
CASHBOX = "entity"
LOCATION = "entity"
CASHBOX_MOVEMENT = "cashbox__entity"
STOCK_MOVEMENT = "location__entity"
EXPENSE = "cashbox__entity"
#: Two-sided documents belong to every entity they touch.
CASHBOX_OPERATION = ("source_cashbox__entity", "destination_cashbox__entity")
STOCK_OPERATION = ("source_location__entity", "destination_location__entity")


def entity():
    return current_entity()


def is_scoped():
    return current_entity() is not None


def q(path, prefix=""):
    """Q for records reached through ``path`` (``prefix`` for a related lookup)."""

    chosen = current_entity()
    if chosen is None:
        return Q()
    if isinstance(path, tuple):
        condition = Q()
        for one in path:
            condition |= q(one, prefix)
        return condition
    lookup = f"{prefix}{path}"
    condition = Q(**{lookup: chosen})
    if chosen.is_main:
        condition |= Q(**{f"{lookup}__isnull": True})
    return condition


def scope(queryset, path):
    return queryset.filter(q(path))


def locations(queryset):
    return scope(queryset, LOCATION)


def cashboxes(queryset):
    return scope(queryset, CASHBOX)


def for_user(queryset, path, user):
    """Records in any entity this user may work in (not only the current one).

    For work that was started earlier and lands now, such as an offline sale
    syncing after the owner switched entity: it must not be refused for that.
    """

    from .current import allowed_entities, is_group_wide

    if is_group_wide(user):
        return queryset
    allowed = allowed_entities(user)
    condition = Q(**{f"{path}__in": allowed})
    if any(entity.is_main for entity in allowed):
        condition |= Q(**{f"{path}__isnull": True})
    return queryset.filter(condition)


def customer_entries_q(prefix=""):
    """Customer ledger rows that belong to the current entity."""

    chosen = current_entity()
    if chosen is None:
        return Q()
    p = prefix
    rows = (q(SALES_INVOICE, f"{p}sales_invoice__") & Q(**{f"{p}sales_invoice__isnull": False})) \
        | (q(SALES_RETURN, f"{p}sales_return__") & Q(**{f"{p}sales_return__isnull": False})) \
        | (q(CUSTOMER_PAYMENT, f"{p}customer_payment__") & Q(**{f"{p}customer_payment__isnull": False}))
    if chosen.is_main:
        rows |= Q(**{f"{p}sales_invoice__isnull": True, f"{p}sales_return__isnull": True, f"{p}customer_payment__isnull": True})
    return rows


def supplier_entries_q(prefix=""):
    chosen = current_entity()
    if chosen is None:
        return Q()
    p = prefix
    rows = (q(PURCHASE_INVOICE, f"{p}purchase_invoice__") & Q(**{f"{p}purchase_invoice__isnull": False})) \
        | (q(PURCHASE_RETURN, f"{p}purchase_return__") & Q(**{f"{p}purchase_return__isnull": False})) \
        | (q(SUPPLIER_PAYMENT, f"{p}supplier_payment__") & Q(**{f"{p}supplier_payment__isnull": False}))
    if chosen.is_main:
        rows |= Q(**{f"{p}purchase_invoice__isnull": True, f"{p}purchase_return__isnull": True, f"{p}supplier_payment__isnull": True})
    return rows


def includes_openings():
    """Party opening balances count in the main entity and the whole group."""

    chosen = current_entity()
    return chosen is None or chosen.is_main


def get_or_404(model, path, **lookup):
    """A document by key, or 404 when it belongs to another entity than the
    one being worked in — the same answer as a document that does not exist."""

    from django.shortcuts import get_object_or_404

    queryset = model if hasattr(model, "filter") and not hasattr(model, "_meta") else model.objects
    return get_object_or_404(scope(queryset.all(), path), **lookup)
