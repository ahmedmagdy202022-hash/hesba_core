"""Restaurants R12 (HG-108): which branch a table and an order belong to, and what each screen sees.

Rules, stated once:

* a table belongs to one entity, and its name is unique there: the database refuses a second «T1» in one branch, and
  another branch has its own «T1»;
* an order belongs to the entity it was opened in, and keeps it: its table's for dine-in, the working entity's for
  takeaway and delivery (in whole-group mode the person picks the branch);
* the board, the order page, the kitchen ticket, the tables setup and the kitchen screen see only the entity being worked
  in (``entities.scope``); another entity's order, table or ticket answers 404, exactly as one that does not exist. The
  whole group sees every branch, the board grouped by branch;
* an order's bill posts to its own entity's default selling store and only into one of its own cashboxes, whatever entity
  the biller works in (the M7 rule, ``medical.inpatient._selling_location``); plates made at payment are made in that
  store. The checkout, the stock, cashbox and ledger engines are the sales engine's, unchanged.
"""

from cashboxes.models import Cashbox
from entities import scope as entity_scope
from entities.current import allowed_entities, current_entity, working_in
from entities.services import is_multi_entity, main_entity

#: Where each restaurant record finds its entity.
TABLE = ORDER = "entity"
TICKET = "order__entity"
TICKET_LINE = "ticket__order__entity"


def group_mode():
    """The whole group of a business with several entities: the board is grouped by branch and new records ask for one."""

    return current_entity() is None and is_multi_entity()


def working_entity():
    """The entity a new record lands in when none is said: the one being worked in, else the main one."""

    return current_entity() or main_entity()


def tables(queryset=None):
    from .models import DiningTable

    return entity_scope.scope(queryset if queryset is not None else DiningTable.objects.all(), TABLE)


def orders(queryset=None):
    from .models import Order

    return entity_scope.scope(queryset if queryset is not None else Order.objects.all(), ORDER)


def tickets(queryset=None):
    from .models import KitchenTicket

    return entity_scope.scope(queryset if queryset is not None else KitchenTicket.objects.all(), TICKET)


def choices(user, lang="ar"):
    """The branches this person may put a new table or order in: [{pk, code, label, entity}]."""

    return [{"pk": entity.pk, "code": entity.code, "label": entity.display(lang), "entity": entity} for entity in allowed_entities(user)]


def pick(user, raw):
    """One of this person's branches by a pk as a form sends it, or None."""

    raw = str(raw or "").strip()
    if not raw.isdigit():
        return None
    return next((entity for entity in allowed_entities(user) if entity.pk == int(raw)), None)


def selling_location(order):
    """The order's entity's default selling store, whatever entity the biller works in."""

    from master_data.models import Location

    with working_in(order.entity):
        return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


def cashboxes(order):
    """The cashboxes and bank accounts a bill of this order may be paid into: its entity's, active, never a held kind."""

    with working_in(order.entity):
        return entity_scope.cashboxes(Cashbox.objects).filter(active=True)


def owns_cashbox(order, cashbox):
    """Whether ``cashbox`` belongs to the order's entity (a cashbox with none belongs to the main one, HG-034)."""

    return order.entity_id == (cashbox.entity_id or main_entity().pk)
