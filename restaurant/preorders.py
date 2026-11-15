"""Restaurants R10 (HG-119): pre-orders with a pick-up time and a deposit («طلبات مسبقة»): the bakery's birthday cake.

Rules, stated once:

* a pre-order belongs to one branch (entity): the one it was made in; a branch's screens only ever list its own pre-orders,
  and another branch's answers 404 (R12);
* it is always for a **named customer**: the walk-in is refused (a deposit needs a name, and S5 refuses the walk-in anyway).
  Its name and phone default to the customer's;
* each line keeps the price **agreed at booking** (``unit_price``, a snapshot: the menu price when no other price is typed).
  A later menu price never changes it. A line is a plain dish with a note: a combo's own item, a charge item, and a dish whose
  choices are required are refused, as ``services.add_item`` refuses them;
* booked → confirmed → ready («جاهز») → collected («اتسلّم»); cancel and no-show from any of the first three. Only booked or
  confirmed is edited (S5 takes a deposit on those two only); ready, collected, cancelled and no-show are never edited again;
* **«اتسلّم»** opens a takeaway restaurant ``Order`` through ``services.open_order`` (the pre-order's branch and customer),
  adds every line at its agreed price through ``services.add_agreed_line``, and links it. Collecting twice, or collecting a
  cancelled or no-show pre-order, is refused. The bill is the order's own: ``services.pay`` unchanged in every step;
* the held deposit of a collected pre-order settles that order's bill when it is paid in the pre-order customer's name, the
  way R8's seated reservation does (``reservations.bill_deposit`` → ``bill_deposit`` here, then ``settle_at_bill``: S5's
  ``attach`` and ``apply``); the drawer is asked for the rest only;
* cancel and no-show run the S5 cancellation policy on a held deposit (``reservations.run_policy``, R8's steps): late (inside
  the policy's hours, or a no-show) forfeits it into a «رسوم إلغاء» invoice with VAT inside; early refunds it through HG-068's gate;
* the **production list** of a day sums the quantity of each dish over that day's live pre-orders (booked, confirmed, ready),
  per branch. «حضّر» calls ``kitchen.prepare`` unchanged, into the pre-order branch's own selling store, so the plates are
  in the store the bill sells from: at payment ``kitchen.prepare_shortfall`` makes only what is missing, and a cake made the
  evening before is sold from stock, never made twice.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from entities.current import current_entity, working_in
from entities.services import main_entity

from .models import LIVE_PREORDER_STATUSES, OrderKind, PreOrder, PreOrderLine, PreOrderStatus

EDITABLE = (PreOrderStatus.BOOKED, PreOrderStatus.CONFIRMED)
MAX_QUANTITY = Decimal("999")
MAX_PRICE = Decimal("1000000")
MAX_LINES = 30
NUMBER_ATTEMPTS = 5

MESSAGES = {
    "ar": {
        "customer": "الطلب المسبق لازم يكون باسم عميل مسجل؛ اختار العميل (العميل النقدي مينفعش).",
        "when": "اختار يوم وساعة الاستلام.", "phone": "اكتب رقم موبايل صحيح.",
        "lines": "ضيف صنف واحد على الأقل للطلب.", "too_many": "الطلب المسبق لحد {max} سطر بس.",
        "item": "اختار صنف من المنيو لكل سطر.", "qty": "كمية «{item}» لازم أكبر من صفر ولحد {max}.",
        "price": "سعر «{item}» لازم يكون رقم صفر أو أكبر.",
        "state": "الطلب المسبق ده حالته «{status}»؛ مينفعش يتعدّل.", "not_booked": "التأكيد للطلب اللي لسه محجوز بس.",
        "not_live": "الطلب المسبق ده حالته «{status}»؛ الخطوة دي مش متاحة.",
        "collected": "الطلب المسبق ده اتسلّم خلاص.", "customer_locked": "على الطلب عربون؛ العميل مينفعش يتغيّر.",
        "foreign": "الطلب المسبق ده مش من طلبات الفرع اللي شغال فيه.", "cashbox": "اختار خزنة من خزن فرع الطلب.",
        "bad_status": "اختار إلغاء أو ما جاش.", "order_note": "طلب مسبق {number}",
        "not_kitchen": "«{item}» مش بيتحضّر في المطبخ.", "branch": "الفرع ده مش من فروعك.",
        "statuses": {"booked": "محجوز", "confirmed": "مؤكد", "ready": "جاهز", "collected": "اتسلّم", "cancelled": "اتلغى", "no_show": "ما جاش"},
    },
    "en": {
        "customer": "A pre-order is for a registered customer; choose the customer (not the walk-in).",
        "when": "Choose the pick-up day and time.", "phone": "Enter a valid mobile number.",
        "lines": "Add at least one item to the order.", "too_many": "A pre-order holds up to {max} lines.",
        "item": "Choose a menu item for each line.", "qty": "The quantity of “{item}” must be above zero and up to {max}.",
        "price": "The price of “{item}” must be zero or more.",
        "state": "This pre-order is “{status}”; it cannot be changed.", "not_booked": "Only a pre-order that is still booked can be confirmed.",
        "not_live": "This pre-order is “{status}”; that step is not available.",
        "collected": "This pre-order was already collected.", "customer_locked": "This pre-order holds a deposit; its customer cannot change.",
        "foreign": "This pre-order is not one of the branch you are working in.", "cashbox": "Choose one of the pre-order's branch's cashboxes.",
        "bad_status": "Choose cancel or no-show.", "order_note": "Pre-order {number}",
        "not_kitchen": "“{item}” is not made in the kitchen.", "branch": "This branch is not one of yours.",
        "statuses": {"booked": "Booked", "confirmed": "Confirmed", "ready": "Ready", "collected": "Collected", "cancelled": "Cancelled", "no_show": "No-show"},
    },
}


def status_label(preorder, lang="ar"):
    return MESSAGES[lang]["statuses"].get(preorder.status, preorder.status)


def _audit(preorder, user, action, after, before=None, event=AuditEventType.UPDATE, reason=""):
    AuditLog.objects.create(event_type=event, actor=user, module="restaurant", action=action, object_type="restaurant.PreOrder",
                            object_id=str(preorder.pk), before_data=before or {}, after_data=after, reason=reason)


def _snapshot(preorder):
    return {"status": preorder.status, "customer": preorder.customer_id, "name": preorder.name, "phone": preorder.phone,
            "starts_at": preorder.starts_at.isoformat() if preorder.starts_at else "", "notes": preorder.notes,
            "lines": [[line.item_id, str(line.quantity), str(line.unit_price), line.note] for line in preorder.lines.all()]}


# --- reading -----------------------------------------------------------------

def scoped(queryset=None):
    """The pre-orders of the entity being worked in (the whole group sees every branch's)."""

    return entity_scope.scope(queryset if queryset is not None else PreOrder.objects.all(), "entity")


def for_day(day):
    """One pick-up day's pre-orders, by time: what the day view lists."""

    from .reservations import day_bounds

    start, end = day_bounds(day)
    return (scoped(PreOrder.objects.filter(starts_at__gte=start, starts_at__lt=end))
            .select_related("customer", "order", "entity").prefetch_related("lines__item"))


def cashboxes(preorder):
    """The cashboxes a deposit on this pre-order may go into: its branch's, active, never a held kind."""

    from .reservations import cashboxes as branch_cashboxes

    return branch_cashboxes(preorder)  # it reads only ``entity``


def selling_location(entity):
    """The branch's default selling store: where the bill of a collected pre-order sells from (``branches.selling_location``)."""

    from master_data.models import Location

    with working_in(entity):
        return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


# --- cleaning ----------------------------------------------------------------

def _customer(raw, words):
    from .reservations import _customer_of

    customer = _customer_of(raw)  # the walk-in, an inactive or an unknown customer is None
    if customer is None:
        raise ValidationError(words["customer"])
    return customer


def clean(data, words):
    """The pre-order's own fields, checked: customer (instance or pk), name and phone (the customer's when empty), starts_at (or
    day and time), notes."""

    from .reservations import _phone, _when

    customer = _customer(data.get("customer"), words)
    name = ((data.get("name") or "").strip() or customer.name)[:120]
    try:
        starts_at = _when(data, {"when": words["when"]})
    except ValidationError:
        raise ValidationError(words["when"])
    phone = _phone(data.get("phone") or customer.phone or "", {"phone": words["phone"]})
    return {"customer": customer, "name": name, "phone": phone, "starts_at": starts_at, "notes": (data.get("notes") or "").strip()[:255]}


def _decimal(raw):
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _item(raw):
    from master_data.models import Item

    if isinstance(raw, Item):
        return raw
    raw = str(raw or "").strip()
    return Item.objects.filter(pk=int(raw)).first() if raw.isdigit() else None


def clean_lines(rows, words, lang="ar"):
    """The lines, checked: ``rows`` is [{item (instance or pk), quantity, unit_price (empty → the menu price now), note}]; a row
    with no item and nothing else is skipped. Each item is one ``services.add_agreed_line`` will take at «اتسلّم»."""

    from . import charges, modifiers
    from .models import Combo
    from .services import OFF_MENU_CODES

    lines = []
    for row in rows:
        raw_item = row.get("item")
        if raw_item in (None, "") and not str(row.get("quantity") or "").strip() and not str(row.get("unit_price") or "").strip():
            continue
        item = _item(raw_item)
        if item is None or not item.active or item.item_code in OFF_MENU_CODES or Combo.objects.filter(item=item).exists():
            raise ValidationError(words["item"])
        charges.refuse_charge_item(item, lang)
        modifiers.choose(item, [], lang)  # a dish whose choices are required is ordered from the menu, not ahead
        quantity = _decimal(row.get("quantity") if str(row.get("quantity") or "").strip() else "1")
        if quantity is None or quantity <= 0 or quantity > MAX_QUANTITY or quantity.quantize(Decimal("0.001")) <= 0:
            raise ValidationError(words["qty"].format(item=item.item_name, max=MAX_QUANTITY))
        raw_price = row.get("unit_price")
        price = _decimal(raw_price) if str(raw_price if raw_price is not None else "").strip() else Decimal(item.default_sale_price or 0)
        if price is None or price < 0 or price >= MAX_PRICE:
            raise ValidationError(words["price"].format(item=item.item_name))
        lines.append({"item": item, "quantity": quantity.quantize(Decimal("0.001")), "unit_price": money_round(price),
                      "note": (row.get("note") or "").strip()[:120]})
    if not lines:
        raise ValidationError(words["lines"])
    if len(lines) > MAX_LINES:
        raise ValidationError(words["too_many"].format(max=MAX_LINES))
    return lines


# --- making and changing ---------------------------------------------------------

def _target(preorder, entity, words):
    working = current_entity()
    target = preorder.entity if preorder is not None else (entity or working or main_entity())
    if working is not None and target.pk != working.pk:
        raise ValidationError(words["foreign"])
    return target


def _new_number(day):
    from config.numbering import next_in_series

    return next_in_series(PreOrder, "number", f"PRE-{day:%Y%m%d}-", width=3)


def _create(values, **extra):
    """A new pre-order under a fresh number (two made in the same instant: the second's savepoint rolls back on the UNIQUE
    ``number`` and it takes the next)."""

    for attempt in range(NUMBER_ATTEMPTS):
        try:
            with transaction.atomic():
                return PreOrder.objects.create(number=_new_number(timezone.localdate()), **values, **extra)
        except IntegrityError:
            if attempt == NUMBER_ATTEMPTS - 1:
                raise


def _locked(preorder):
    # ``of=self``: PostgreSQL refuses FOR UPDATE on the nullable side of an outer join (order).
    return PreOrder.objects.select_for_update(of=("self",)).select_related("customer", "entity").get(pk=preorder.pk)


def _write_lines(preorder, lines):
    preorder.lines.all().delete()
    PreOrderLine.objects.bulk_create([PreOrderLine(preorder=preorder, **line) for line in lines])


@transaction.atomic
def save_preorder(data, rows, user, preorder=None, lang="ar", entity=None):
    """Make or change a pre-order. ``data``: customer, name, phone, starts_at (or day and time), notes; ``rows``: the lines (see
    ``clean_lines``). A new one lands in ``entity`` (else the working entity, else the main one) and never changes branch. An
    edit replaces the lines; a line typed with no price takes the menu price now, a line kept keeps its agreed price."""

    words = MESSAGES[lang]
    if preorder is not None:
        preorder = _locked(preorder)
        if preorder.status not in EDITABLE:
            raise ValidationError(words["state"].format(status=status_label(preorder, lang)))
    target = _target(preorder, entity, words)
    values = clean(data, words)
    lines = clean_lines(rows, words, lang)
    if preorder is not None and values["customer"].pk != preorder.customer_id:
        from deposits import services as deposits

        if deposits.held_for(preorder) is not None:
            raise ValidationError(words["customer_locked"])
    if preorder is None:
        preorder = _create(values, entity=target, created_by=user)
        _write_lines(preorder, lines)
        _audit(preorder, user, "create_preorder", {**_snapshot(preorder), "number": preorder.number, "entity": target.pk}, event=AuditEventType.CREATE)
        return preorder
    before = _snapshot(preorder)
    for key, value in values.items():
        setattr(preorder, key, value)
    preorder.save()
    _write_lines(preorder, lines)
    _audit(preorder, user, "change_preorder", _snapshot(preorder), before)
    return preorder


def _step(preorder, user, allowed, status, action, lang, refusal="not_live"):
    words = MESSAGES[lang]
    preorder = _locked(preorder)
    _target(preorder, None, words)
    if preorder.status not in allowed:
        raise ValidationError(words[refusal].format(status=status_label(preorder, lang)))
    before = preorder.status
    preorder.status = status
    preorder.save(update_fields=["status", "updated_at"])
    _audit(preorder, user, action, {"status": status}, {"status": before})
    return preorder


@transaction.atomic
def confirm(preorder, user, lang="ar"):
    """booked → confirmed."""

    return _step(preorder, user, (PreOrderStatus.BOOKED,), PreOrderStatus.CONFIRMED, "confirm_preorder", lang, "not_booked")


@transaction.atomic
def mark_ready(preorder, user, lang="ar"):
    """«جاهز»: booked or confirmed → ready (it is made and waiting for its customer)."""

    return _step(preorder, user, EDITABLE, PreOrderStatus.READY, "ready_preorder", lang)


@transaction.atomic
def collect(preorder, user, lang="ar"):
    """«اتسلّم»: the customer came. Opens a takeaway restaurant order in the pre-order's branch for its customer
    (``services.open_order``), adds each line at its agreed price (``services.add_agreed_line``), and links it. The bill is then
    paid on the order like any other; its held deposit settles part of it (``bill_deposit``). Returns the pre-order."""

    from . import services

    words = MESSAGES[lang]
    preorder = _locked(preorder)
    _target(preorder, None, words)
    if preorder.status == PreOrderStatus.COLLECTED:
        raise ValidationError(words["collected"])
    if preorder.status not in LIVE_PREORDER_STATUSES:
        raise ValidationError(words["not_live"].format(status=status_label(preorder, lang)))
    lines = list(preorder.lines.select_related("item"))
    if not lines:
        raise ValidationError(words["lines"])
    note = words["order_note"].format(number=preorder.number)
    order = services.open_order(user, kind=OrderKind.TAKEAWAY, customer=preorder.customer, phone=preorder.phone, entity=preorder.entity, lang=lang,
                                notes=f"{note} · {preorder.notes}" if preorder.notes else note)
    for line in lines:
        services.add_agreed_line(order, line.item, user, line.quantity, line.unit_price, line.note, lang)
    before = preorder.status
    preorder.status, preorder.order = PreOrderStatus.COLLECTED, order
    preorder.save(update_fields=["status", "order", "updated_at"])
    _audit(preorder, user, "collect_preorder", {"status": preorder.status, "order": order.number}, {"status": before})
    return preorder


@transaction.atomic
def cancel(preorder, status, user, lang="ar", reason=""):
    """Cancel or mark a no-show, and run the S5 cancellation policy on a held deposit in the same transaction (R8's
    ``reservations.cancel`` steps: the row locked first, the status set before the policy, then ``run_policy``).

    Returns {"preorder", "deposit", "outcome", "invoice", "detail"}; ``outcome`` as R8's: None (no held deposit), "forfeited",
    "refunded", "refund_pending", "forfeit_not_allowed", "forfeit_refused", "refund_not_allowed", "refund_blocked"."""

    from deposits import services as deposits

    from .reservations import run_policy

    words = MESSAGES[lang]
    if status not in (PreOrderStatus.CANCELLED, PreOrderStatus.NO_SHOW):
        raise ValidationError(words["bad_status"])
    preorder = _locked(preorder)
    if preorder.status not in LIVE_PREORDER_STATUSES:
        raise ValidationError(words["not_live"].format(status=status_label(preorder, lang)))
    _target(preorder, None, words)
    deposit = deposits.held_for(preorder)
    before = preorder.status
    preorder.status = status
    preorder.save(update_fields=["status", "updated_at"])
    _audit(preorder, user, "cancel_preorder" if status == PreOrderStatus.CANCELLED else "no_show_preorder",
           {"status": status, **({"deposit": deposit.payment.payment_number} if deposit else {})}, {"status": before}, reason=(reason or "").strip()[:255])
    result = {"preorder": preorder, "deposit": deposit, "outcome": None, "invoice": None, "detail": ""}
    if deposit is None:
        return result
    note = (reason or "").strip() or (f"Pre-order {preorder.number} cancelled" if lang == "en" else f"إلغاء الطلب المسبق {preorder.number}")
    return run_policy(result, preorder, deposit, user, lang, note)


def take_deposit(preorder, cashbox, amount, user, lang="ar"):
    """A deposit on the pre-order: ``deposits.services.take`` as it is (a payment on account; a switched-off feature and a
    pre-order that is not booked or confirmed are refused there). The cashbox must be the pre-order's branch's."""

    from deposits import services as deposits

    words = MESSAGES[lang]
    if cashbox is None or not cashboxes(preorder).filter(pk=cashbox.pk).exists():
        raise ValidationError(words["cashbox"])
    return deposits.take(preorder, preorder.customer, cashbox, amount, user, lang)


# --- the deposit at the bill -----------------------------------------------------

def bill_deposit(order, customer):
    """The collected pre-order of this order (or of an order merged into it) whose held deposit is ``customer``'s, as
    (pre-order, deposit); (None, None) when there is none. A deposit of another customer is not used: it stays held."""

    from deposits import services as deposits

    if order is None or customer is None:
        return None, None
    for preorder in PreOrder.objects.filter(Q(order=order) | Q(order__merged_into=order), status=PreOrderStatus.COLLECTED).order_by("pk"):
        deposit = deposits.held_for(preorder)
        if deposit is not None and deposit.payment.customer_id == customer.pk:
            return preorder, deposit
    return None, None


# --- the production list -----------------------------------------------------------

def production(day):
    """What the kitchen makes for ``day``'s pick-ups: one row per (branch, dish) over the day's live pre-orders here, with the
    quantity wanted, whether the dish is made in the kitchen, and (for those) what is in the branch's store now and still to make:
    [{"entity", "item", "quantity", "kitchen", "in_stock", "missing"}], by branch then dish name."""

    from inventory.services import get_item_location_stock_quantity

    from . import kitchen
    from .reservations import day_bounds

    start, end = day_bounds(day)
    lines = (PreOrderLine.objects.filter(preorder__in=scoped(PreOrder.objects.filter(starts_at__gte=start, starts_at__lt=end, status__in=LIVE_PREORDER_STATUSES)))
             .select_related("item", "preorder__entity"))
    totals = {}
    for line in lines:
        key = (line.preorder.entity_id, line.item_id)
        if key not in totals:
            totals[key] = {"entity": line.preorder.entity, "item": line.item, "quantity": Decimal("0")}
        totals[key]["quantity"] += line.quantity
    made = kitchen.kitchen_dish_ids([row["item"] for row in totals.values()])
    stores = {}
    rows = []
    for row in sorted(totals.values(), key=lambda r: (r["entity"].pk, r["item"].item_name, r["item"].pk)):
        row["kitchen"] = row["item"].pk in made
        row["in_stock"] = row["missing"] = None
        if row["kitchen"]:
            if row["entity"].pk not in stores:
                stores[row["entity"].pk] = selling_location(row["entity"])
            store = stores[row["entity"].pk]
            have = get_item_location_stock_quantity(row["item"], store) if store is not None else Decimal("0")
            row["in_stock"] = have
            row["missing"] = max(row["quantity"] - have, Decimal("0")).quantize(Decimal("0.001"))
        rows.append(row)
    return rows


def prepare(item, quantity, user, entity, lang="ar"):
    """«حضّر»: ``kitchen.prepare`` unchanged (its own permissions, recipe and quantity checks), into ``entity``'s selling store:
    the store a collected pre-order's bill sells from, so the plates made now are sold from stock then, not made again."""

    from . import kitchen

    words = MESSAGES[lang]
    if not kitchen.is_kitchen_made(item):
        raise ValidationError(words["not_kitchen"].format(item=item.item_name))
    location = selling_location(entity)
    if location is None:
        raise ValidationError(kitchen.MESSAGES["en" if lang == "en" else "ar"]["setup"])
    return kitchen.prepare(item, quantity, user, lang, location=location)


def default_day():
    """The production list's day unless one is chosen: tomorrow (made the evening before)."""

    return timezone.localdate() + timedelta(days=1)
