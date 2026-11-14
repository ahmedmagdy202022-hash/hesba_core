"""Restaurants R5a (HG-115): the table's QR menu, and ordering from the table (paying from it is R5b, not here).

Rules, stated once:

* a table has a random, unique ``qr_key``; ``/t/<qr_key>/`` is its public page (no account). A wrong key, a key rotated since,
  an inactive table, an inactive branch and the module switched off all answer the same dead page;
* **public input never writes an order line.** A guest's «اطلب» is a ``TableRequest`` holding the normalised cart (item,
  whole quantity, chosen option pks, a short note: no price, no name). A member of staff accepts it, and only then do the
  lines reach the table's order, through the existing ``services.open_order`` and ``services.add_item`` as that waiter, unsent,
  at the menu price of that moment. «نادي الجرسون» and «الحساب» are only marked handled;
* the menu is the waiter's (``views._menu``: active items, minus the ones a bill adds by itself), minus every combo's own item
  (combos are not on the public page) and minus raw ingredients (the order page's own guess, ``views._raw_ingredient_ids``):
  one function, used to draw the page and to check every cart again at request time and at accept time. The menu is
  company-wide (an item carries no entity), so «this branch's menu» is the one menu;
* limits: ``MAX_OPEN`` new requests per table (a call or a bill asked twice is the same one), ``MAX_LINES`` lines,
  ``MAX_QUANTITY`` a line, ``NOTE`` and ``LINE_NOTE`` characters;
* accepting is all-or-nothing under a lock on the request's row: two waiters at once, one wins and the lines are added once. A
  request is stale, and declined, when an order of its table was closed after it was made (the departed party's request must
  not land on the next party's order). Nothing here posts an invoice, moves stock or cash, or touches a ledger or loyalty.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType

from . import branches, modifiers, services
from .models import DiningTable, Order, RequestKind, RequestStatus, TableRequest, new_qr_key

MAX_OPEN = 5
MAX_LINES = 15
MAX_QUANTITY = 10
NOTE = 120
LINE_NOTE = 60
KEY_MAX = 40
MODULE = "tables_orders"

MESSAGES = {
    "ar": {
        "empty": "اختار طبق واحد على الأقل.", "too_many_lines": "الطلب الواحد لحد {max} أصناف. ابعت اللي اخترته وبعدين اطلب الباقي.",
        "quantity": "الكمية لازم تكون من 1 لحد {max}.", "off": "صنف في الطلب مش متاح دلوقتي. حدّث الصفحة واختار تاني.",
        "busy": "فيه طلبات كتير مستنية على الطاولة دي. استنى الجرسون شوية وبعدين جرّب تاني.", "off_table": "الطاولة دي مش شغالة دلوقتي.",
        "already": "الطلب ده اتعامل معاه قبل كده.", "stale": "الطلب ده اتبعت قبل ما حساب الطاولة يتقفل، فاتشال. اطلب من الطاولة من جديد لو لسه محتاج.",
        "item_gone": "«{item}» مش متاح دلوقتي، فمفيش حاجة اتضافت للطلب. ارفض الطلب أو كلّم الضيف.",
        "bad_kind": "نوع الطلب غلط.",
    },
    "en": {
        "empty": "Choose at least one dish.", "too_many_lines": "One order takes up to {max} items. Send what you chose, then order the rest.",
        "quantity": "The quantity must be from 1 to {max}.", "off": "An item in the order is not available now. Refresh the page and choose again.",
        "busy": "There are many requests waiting at this table. Wait for the waiter a little, then try again.", "off_table": "This table is not active now.",
        "already": "This request was already handled.", "stale": "This request was sent before the table's bill was closed, so it was removed. Order again if you still need to.",
        "item_gone": "“{item}” is not available now, so nothing was added to the order. Decline the request or talk to the guest.",
        "bad_kind": "Wrong kind of request.",
    },
}


def words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


class Stale(Exception):
    """The request belongs to a party that has left (an order of its table closed after it was made)."""


# --- the table behind a key ------------------------------------------------------------------------------------------

def table_by_key(key):
    """The active table of an active branch behind a public key, or None. One answer for every invalid case."""

    from settings_core.setup_services import module_is_enabled

    if not key or len(key) > KEY_MAX or not module_is_enabled(MODULE):
        return None
    table = DiningTable.objects.select_related("entity").filter(qr_key=key).first()
    if table is None or not table.active or not table.entity.active:
        return None
    return table


def public_url(table, lang="ar"):
    """The address the QR carries, from ``settings.PUBLIC_BASE_URL`` only (never the request's host: ``printing.share``'s standing
    rule); None when it is not set, since a link with no address is not one to print."""

    from django.conf import settings
    from django.urls import reverse

    base = getattr(settings, "PUBLIC_BASE_URL", "")
    if not base:
        return None
    return f"{base.rstrip('/')}{reverse('table_menu', args=[table.qr_key])}"


@transaction.atomic
def rotate_key(table, user):
    """«غيّر الكود»: a new key; the printed card with the old one stops working at once. The keys are never written to the audit log."""

    table = DiningTable.objects.select_for_update().get(pk=table.pk)
    table.qr_key = new_qr_key()
    table.save(update_fields=["qr_key"])
    services._audit(table, user, "rotate_table_qr", {"table": table.name, "entity": table.entity_id})
    return table


# --- the menu --------------------------------------------------------------------------------------------------------

def public_items():
    """The items a guest may order: the waiter's menu, without combos' own items and without raw ingredients."""

    from .models import Combo
    from .views import _menu, _raw_ingredient_ids

    items, _categories = _menu()
    hidden = set(Combo.objects.values_list("item_id", flat=True)) | _raw_ingredient_ids()
    return [item for item in items if item.pk not in hidden]


def shown_price(item, price, cache):
    """A price as the guest pays it: with the item's own VAT when VAT is on, worked out by the bill's own per-item rate path
    (``charges._row_tax``); the plain price when it is off. Never a second tax calculation."""

    from taxes.services import vat_enabled

    from . import charges

    price = Decimal(price)
    if not vat_enabled():
        return price.quantize(Decimal("0.01"))
    return price + charges._row_tax(item, price, cache)


# --- a cart ----------------------------------------------------------------------------------------------------------

def _ascii_digits(raw):
    """Plain 0-9 only: ``str.isdigit`` also says yes to «٣» and «²», and ``int`` then refuses the second (a public page never trusts it)."""

    return raw.isascii() and raw.isdigit()


def _whole(raw, text):
    raw = str(raw if raw not in (None, "") else "0").strip()
    if not _ascii_digits(raw) or len(raw) > 3:
        raise ValidationError(text["quantity"].format(max=MAX_QUANTITY))
    return int(raw)


def normalise(raw_lines, lang="ar"):
    """The cart checked, nothing written: [{"item", "quantity", "options", "note"}] (item pk, whole quantity, sorted option pks, note).

    ``raw_lines``: [{"item": pk, "quantity": n, "options": [pks], "note": text}]; a line with quantity 0 is a dish not ordered and
    is dropped. Refuses an item that is not on the public menu (inactive, a charge or fee item, a combo, a raw ingredient, not a
    number), a quantity above ``MAX_QUANTITY``, a dish's missing or extra choice (``modifiers.choose``) and too many lines."""

    text = words(lang)
    live = {item.pk: item for item in public_items()}
    lines, seen = [], set()
    for raw in raw_lines:
        quantity = _whole(raw.get("quantity"), text)
        if quantity == 0:
            continue
        if quantity > MAX_QUANTITY:
            raise ValidationError(text["quantity"].format(max=MAX_QUANTITY))
        pk = str(raw.get("item") or "").strip()
        item = live.get(int(pk)) if _ascii_digits(pk) else None
        if item is None or item.pk in seen:
            raise ValidationError(text["off"])
        seen.add(item.pk)
        options = [str(value).strip() for value in (raw.get("options") or [])]
        if not all(_ascii_digits(value) for value in options if value):  # R1a's ``choose`` meets only what is a number
            raise ValidationError(text["off"])
        chosen = modifiers.choose(item, options, lang)  # writes nothing; refuses a missing, extra, off or foreign choice
        lines.append({"item": item.pk, "quantity": quantity, "options": sorted(option.pk for _group, picked in chosen for option in picked),
                      "note": (raw.get("note") or "").strip()[:LINE_NOTE]})
    if not lines:
        raise ValidationError(text["empty"])
    if len(lines) > MAX_LINES:
        raise ValidationError(text["too_many_lines"].format(max=MAX_LINES))
    return lines


# --- a request -------------------------------------------------------------------------------------------------------

@transaction.atomic
def create_request(table, kind, raw_lines=None, note="", lang="ar"):
    """A guest's request. An order's cart is checked again here; a call or a bill asked while one is waiting is that same request.

    The table's row is locked (the lock ``open_order`` takes) before the open ones are counted, so concurrent posts cannot pass
    ``MAX_OPEN``. Returns the request; nothing else is written."""

    text = words(lang)
    if kind not in RequestKind.values:
        raise ValidationError(text["bad_kind"])
    lines = normalise(raw_lines or [], lang) if kind == RequestKind.ORDER else []
    table = DiningTable.objects.select_for_update().select_related("entity").get(pk=table.pk)
    if not table.active or not table.entity.active:
        raise ValidationError(text["off_table"])
    waiting = TableRequest.objects.filter(table=table, status=RequestStatus.NEW)
    if kind != RequestKind.ORDER:
        same = waiting.filter(kind=kind).first()
        if same is not None:
            return same
    if waiting.count() >= MAX_OPEN:
        raise ValidationError(text["busy"])
    return TableRequest.objects.create(table=table, entity=table.entity, kind=kind, items=lines, note=(note or "").strip()[:NOTE])


# --- the staff side --------------------------------------------------------------------------------------------------

def is_stale(request):
    """An order of the request's table was closed (paid, cancelled or merged away) after the request was made. A part of a
    bill (R2a) paid at the table is not the table's order closing."""

    return Order.objects.filter(table=request.table, split_from__isnull=True, closed_at__gt=request.created_at).exists()


def _mark(request, status, user, order=None):
    request.status = status
    request.handled_by = user
    request.handled_at = timezone.now()
    if order is not None:
        request.order = order
    request.save(update_fields=["status", "handled_by", "handled_at", "order"])


def _audit(request, user, action, after):
    services._audit(request, user, action, {"table": request.table.name, "kind": request.kind, "entity": request.entity_id, **after},
                    event=AuditEventType.UPDATE)


def _locked(request, text):
    locked = TableRequest.objects.select_for_update(of=("self",)).select_related("table").get(pk=request.pk)
    if locked.status != RequestStatus.NEW:
        raise ValidationError(text["already"])
    return locked


def _decline_stale(request, user):
    with transaction.atomic():
        found = TableRequest.objects.select_for_update(of=("self",)).select_related("table").get(pk=request.pk)
        if found.status == RequestStatus.NEW:
            _mark(found, RequestStatus.DECLINED, user)
            _audit(found, user, "decline_table_request", {"reason": "table_closed", "auto": user is None})


def sweep(requests):
    """Decline, automatically, the waiting requests whose table's order was closed after they were made. Returns the ones still new."""

    live = []
    for request in requests:
        if request.status == RequestStatus.NEW and is_stale(request):
            _decline_stale(request, None)
        else:
            live.append(request)
    return live


def accept(request, user, lang="ar"):
    """«قبول» on an order request: the table's order is opened if none is open, and each line is added through ``services.add_item``
    (its add-ons and choices through R1a) as ``user``, unsent, at the menu price now. All or nothing. Returns (request, order).

    Refused, with nothing written: an item no longer on the public menu, a choice no longer valid, an inactive table or a request
    already handled. A stale request is declined and the refusal says so. «تمام» on a call or a bill is ``handle``."""

    text = words(lang)
    try:
        with transaction.atomic():
            locked = _locked(request, text)
            if locked.kind != RequestKind.ORDER:
                raise ValidationError(text["bad_kind"])
            if is_stale(locked):
                raise Stale()
            table = DiningTable.objects.select_for_update().get(pk=locked.table_id)
            if not table.active:
                raise ValidationError(text["off_table"])
            live = {item.pk: item for item in public_items()}
            plan = []
            for line in locked.items or []:
                item = live.get(line["item"])
                if item is None:
                    from master_data.models import Item

                    gone = Item.objects.filter(pk=line["item"]).values_list("item_name", flat=True).first() or ""
                    raise ValidationError(text["item_gone"].format(item=gone))
                modifiers.choose(item, line.get("options"), lang)  # the choice may have changed since the guest chose
                plan.append((item, line))
            order = services.open_order(user, table=table, lang=lang)  # joins the table's open order if there is one
            for item, line in plan:
                services.add_item(order, item, user, line["quantity"], line.get("note", ""), lang, options=line.get("options"))
            _mark(locked, RequestStatus.ACCEPTED, user, order)
            _audit(locked, user, "accept_table_request", {"order": order.number, "lines": len(plan)})
            return locked, order
    except Stale:
        _decline_stale(request, user)
        raise ValidationError(text["stale"])


def handle(request, user, lang="ar"):
    """«تمام» on a call-the-waiter or bill request: it is marked handled (accepted). It adds nothing and moves nothing."""

    text = words(lang)
    with transaction.atomic():
        locked = _locked(request, text)
        if locked.kind == RequestKind.ORDER:
            raise ValidationError(text["bad_kind"])
        _mark(locked, RequestStatus.ACCEPTED, user)
        _audit(locked, user, "handle_table_request", {})
        return locked


def decline(request, user, lang="ar"):
    """«رفض»: the request is kept, marked declined; nothing is added to any order."""

    text = words(lang)
    with transaction.atomic():
        locked = _locked(request, text)
        _mark(locked, RequestStatus.DECLINED, user)
        _audit(locked, user, "decline_table_request", {"auto": False})
        return locked


def lines_text(request, lang="ar"):
    """What an order request holds, for the waiter: [{name, quantity, options, note, gone}] at today's names (never at a stored price)."""

    from master_data.models import Item
    from .models import ModifierOption

    items = Item.objects.in_bulk([line["item"] for line in request.items or []])
    options = ModifierOption.objects.in_bulk([pk for line in request.items or [] for pk in line.get("options") or []])
    rows = []
    for line in request.items or []:
        item = items.get(line["item"])
        rows.append({"name": item.item_name if item else "—", "quantity": line["quantity"], "note": line.get("note", ""),
                     "options": [options[pk].label(lang) for pk in line.get("options") or [] if pk in options]})
    return rows


def waiting(table=None):
    """The new requests the staff should see (the entity being worked in only, R12), oldest first; the stale ones are swept first."""

    found = TableRequest.objects.filter(status=RequestStatus.NEW).select_related("table", "entity")
    if table is not None:
        found = found.filter(table=table)
    return sweep(list(branches.requests(found)))


def counts(requests):
    """{table pk: number of requests}."""

    result = {}
    for request in requests:
        result[request.table_id] = result.get(request.table_id, 0) + 1
    return result
