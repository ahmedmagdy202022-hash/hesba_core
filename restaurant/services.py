"""RESTO-001: take an order at a table (or for takeaway / delivery), send it to the kitchen, bill it.

Rules, stated once:

* a table holds at most one open order (a database constraint, plus a lock on
  the table row); opening an occupied table joins the order already there;
* lines can be added while the order is open. A line not yet sent to the
  kitchen can be changed or removed freely; one already sent can only be
  voided, by someone who may sell and with a reason (Restaurants R13), which
  is audited, so the kitchen's work never disappears silently;
* "send to kitchen" gathers every unsent line into numbered tickets to
  print, one per kitchen station (Restaurants R4; one ticket when no station
  is set up); nothing new means no ticket;
* paying an order calls the cashier screen's own ``checkout`` (the same call
  POS-001 makes), so VAT, stock, cashbox, customer ledger and period rules are
  the sales engine's, unchanged. The order only keeps a link to the posted
  invoice. Stock-tracked lines (a canned drink) leave stock; dishes are
  service items and do not. Ingredient deduction by recipe is not done here.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from master_data.models import Item
from permissions.services import user_has_permission

from . import branches, stations
from .models import DiningTable, KitchenTicket, Order, OrderKind, OrderLine, OrderStatus
from entities.current import current_entity
from entities.services import main_entity


MAX_QUANTITY = Decimal("999")
MESSAGES = {
    "ar": {
        "table_name": "اكتب اسم أو رقم الطاولة.", "table_taken": "فيه طاولة بنفس الاسم.", "seats": "عدد الكراسي لازم من 1 لـ 99.",
        "table_needed": "اختار الطاولة.", "table_off": "الطاولة دي مش شغالة.", "busy": "الطاولة {table} عليها طلب مفتوح.",
        "closed": "الطلب ده اتقفل؛ مينفعش يتعدّل.", "item": "اختار صنف من المنيو.", "qty": "الكمية لازم أكبر من صفر ولحد {max}.",
        "sent": "السطر ده راح المطبخ؛ تقدر تلغيه بس.", "void_reason": "اكتب سبب إلغاء السطر؛ الطبق ده راح المطبخ.", "empty": "الطلب فاضي.", "address": "الدليفري محتاج عنوان.",
        "setup": "لازم يكون فيه مخزن بيع نشط.", "dine_in_only": "النقل للطاولات بس لطلبات الصالة.", "has_open": "الطاولة دي عليها طلبات مفتوحة؛ اقفلها الأول.",
        "fee": "رسوم التوصيل لازم تكون رقم صفر أو أكبر.", "not_delivery": "ده مش طلب دليفري.", "driver": "اختار الطيار.",
        "fee_line": "رسوم توصيل", "fee_item": "رسوم توصيل",
        "fee_stocked": "الصنف {code} متسجل صنف مخزني؛ رسوم التوصيل لازم تكون خدمة. عدّله من الأصناف وشيل «يتتبع في المخزن».",
        "other_branch": "الطاولة دي تبع فرع تاني.", "branch": "اختار الفرع.", "not_this_branch": "الفرع ده مش الفرع اللي شغال فيه دلوقتي.",
        "cashbox_branch": "الخزنة دي تبع فرع تاني؛ اختار خزنة من خزن فرع الطلب.",
        "split_rows": "الدفع بأكتر من طريقة محتاج مبلغين على الأقل، كل مبلغ في خزنة.",
        "split_cashbox": "اختار لكل مبلغ خزنة من خزن فرع الطلب.",
        "split_total": "مجموع طرق الدفع لازم يساوي الحساب بالظبط: {total}.",
        # R2a (HG-111): «ادفع جزء» and «ادمج مع طاولة»
        "part_empty": "اختار صنف واحد على الأقل للجزء.", "part_line": "السطر ده مش من الطلب ده أو اتلغى.",
        "part_qty": "كمية «{item}» لازم تكون عدد صحيح من 1 لحد {max}.",
        "part_fraction": "كمية «{item}» لازم تكون عدد صحيح من 1 لحد {max}: الأصناف بتتقسم بالعدد، مش بالفلوس. لو هتقسموا الحساب بالتساوي، ادفعوه مرة واحدة بـ«ادفع بأكتر من طريقة».",
        "part_all": "كده الحساب كله اتختار؛ ادفع حساب الطاولة نفسه.",
        "part_of_part": "ده جزء من حساب؛ قسّم من حساب الطاولة نفسه.",
        "part_delivery": "الدليفري بيتدفع فاتورة واحدة؛ لو هيتقسم، استخدم «ادفع بأكتر من طريقة» على نفس الحساب.",
        "part_open": "فيه جزء من الحساب لسه مفتوح ({number})؛ ادفعه أو رجّعه للحساب الأول.",
        "part_move": "الجزء بيتبع طاولة حسابه؛ انقل حساب الطاولة نفسه.",
        "part_cancel": "الجزء مبيتلغيش؛ رجّعه للحساب، والإلغاء يبقى من حساب الطاولة نفسه.",
        "not_part": "ده مش جزء من حساب.",
        "merge_pick": "اختار الطاولة اللي هتتدمج.", "merge_self": "مينفعش تدمج الطاولة في نفسها.",
        "merge_kind": "الدمج لطلبات الصالة المفتوحة بس.", "merge_closed": "طلب الطاولة التانية اتقفل؛ مينفعش يتدمج.",
        "merge_part": "الجزء من حساب مينفعش يتدمج؛ ادمج حساب الطاولة نفسه.",
    },
    "en": {
        "table_name": "Enter the table name or number.", "table_taken": "A table with this name exists.", "seats": "Seats must be 1 to 99.",
        "table_needed": "Choose the table.", "table_off": "This table is not active.", "busy": "Table {table} has an open order.",
        "closed": "This order is closed; it cannot be changed.", "item": "Choose an item from the menu.", "qty": "The quantity must be above zero and up to {max}.",
        "sent": "This line went to the kitchen; it can only be voided.", "void_reason": "Write why the line is voided; this dish went to the kitchen.", "empty": "The order is empty.", "address": "A delivery needs an address.",
        "setup": "An active selling location is needed.", "dine_in_only": "Only dine-in orders move between tables.", "has_open": "This table has open orders; close them first.",
        "fee": "The delivery fee must be zero or more.", "not_delivery": "This is not a delivery order.", "driver": "Choose the driver.",
        "fee_line": "Delivery fee", "fee_item": "رسوم توصيل",
        "fee_stocked": "Item {code} is a stocked item; the delivery fee must be a service. Edit it under Items and untick stock tracking.",
        "other_branch": "This table belongs to another branch.", "branch": "Choose the branch.", "not_this_branch": "This is not the branch you are working in.",
        "cashbox_branch": "This cashbox belongs to another branch; choose one of the order's branch's cashboxes.",
        "split_rows": "Paying several ways needs at least two amounts, each into a cashbox.",
        "split_cashbox": "Choose one of the order's branch's cashboxes for each amount.",
        "split_total": "The ways of paying must add up to the bill exactly: {total}.",
        "part_empty": "Choose at least one item for the part.", "part_line": "This line is not on this order, or it was voided.",
        "part_qty": "The quantity of “{item}” must be a whole number from 1 to {max}.",
        "part_fraction": "The quantity of “{item}” must be a whole number from 1 to {max}: items are split by count, not by money. To share the bill equally, pay it once with “Pay several ways”.",
        "part_all": "That is the whole bill; pay the table's bill itself.",
        "part_of_part": "This is a part of a bill; split from the table's bill itself.",
        "part_delivery": "A delivery is paid as one invoice; to share it, use “Pay several ways” on the same bill.",
        "part_open": "A part of the bill is still open ({number}); pay it or put it back first.",
        "part_move": "A part follows its bill's table; move the table's bill itself.",
        "part_cancel": "A part is not cancelled; put it back, and cancel from the table's bill itself.",
        "not_part": "This is not a part of a bill.",
        "merge_pick": "Choose the table to merge.", "merge_self": "A table cannot be merged into itself.",
        "merge_kind": "Only open dine-in orders can be merged.", "merge_closed": "The other table's order is closed; it cannot be merged.",
        "merge_part": "A part of a bill cannot be merged; merge the table's bill itself.",
    },
}


def _audit(obj, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="restaurant", action=action, object_type=f"restaurant.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


def _quantity(value, words):
    try:
        number = Decimal(str(value if value not in (None, "") else "1").strip().replace(",", "."))
    except InvalidOperation:
        number = Decimal("-1")
    if not number.is_finite() or number <= 0 or number > MAX_QUANTITY:
        raise ValidationError(words["qty"].format(max=MAX_QUANTITY))
    return number.quantize(Decimal("0.001"))


# --- tables -----------------------------------------------------------------

@transaction.atomic
@transaction.atomic
def save_table(data, user, table=None, lang="ar", entity=None):
    """Create or change a table. R12: a new table lands in ``entity`` (else the working entity, else the main one); a free
    table may move to another branch from the whole group; its name is unique within its branch."""

    words = MESSAGES[lang]
    if table is not None:
        # The lock ``open_order`` and ``move_table`` take: an order being opened on this table right now is seen by the
        # open-orders check below, so the table is not moved to another branch or switched off under it.
        table = DiningTable.objects.select_for_update().get(pk=table.pk)
    name = (data.get("name") or "").strip()[:40]
    if not name:
        raise ValidationError(words["table_name"])
    working = current_entity()
    target = entity or (table.entity if table is not None else None) or working or main_entity()
    if working is not None and target.pk != working.pk:
        raise ValidationError(words["not_this_branch"])
    if DiningTable.objects.filter(entity=target, name__iexact=name).exclude(pk=getattr(table, "pk", None)).exists():
        raise ValidationError(words["table_taken"])
    try:
        seats = int(data.get("seats") or 4)
    except (TypeError, ValueError):
        seats = 0
    if not 1 <= seats <= 99:
        raise ValidationError(words["seats"])
    active = data.get("active", True) not in (False, "", "0", "off", None)
    moving = table is not None and target.pk != table.entity_id
    if table is not None and (not active or moving) and table.orders.filter(status=OrderStatus.OPEN).exists():
        raise ValidationError(words["has_open"])
    values = {"name": name, "area": (data.get("area") or "").strip()[:60], "seats": seats, "active": active,
              "sort_order": int(data.get("sort_order") or 0) if str(data.get("sort_order") or "0").isdigit() else 0, "entity_id": target.pk}
    created = table is None
    before = {} if created else {key: getattr(table, key) for key in values}
    table = table or DiningTable()
    for key, value in values.items():
        setattr(table, key, value)
    table.save()
    _audit(table, user, "create_table" if created else "change_table", values, before, AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return table


# --- orders -----------------------------------------------------------------

def _next_number(day):
    prefix = f"R-{day:%Y%m%d}-"
    number = Order.objects.filter(number__startswith=prefix).count() + 1
    while Order.objects.filter(number=f"{prefix}{number:03d}").exists():
        number += 1
    return f"{prefix}{number:03d}"


def _fee(raw, words):
    """RESTO-003: a delivery fee as typed (blank = 0)."""

    if raw in (None, ""):
        return Decimal("0")
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError(words["fee"])
    if not value.is_finite() or value < 0 or value >= Decimal("100000"):
        raise ValidationError(words["fee"])
    return money_round(value)


def open_order(user, *, kind=OrderKind.DINE_IN, table=None, guests=1, customer=None, waiter=None, address="", phone="", notes="", lang="ar",
               delivery_fee=None, entity=None):
    """A new order, or, for a table already taken, the order already open there.

    R12: a dine-in order belongs to its table's entity; a takeaway or a delivery to ``entity``, else the working entity,
    else the main one. Neither may land outside the entity being worked in."""

    words = MESSAGES[lang]
    working = current_entity()
    kind = kind if kind in OrderKind.values else OrderKind.DINE_IN
    if kind == OrderKind.DINE_IN:
        if table is None:
            raise ValidationError(words["table_needed"])
        target = None  # the table's, read under its lock below
    else:
        table = None
        target = entity or working or main_entity()
        if working is not None and target.pk != working.pk:
            raise ValidationError(words["not_this_branch"])
    address = (address or (getattr(customer, "address", "") if customer else "") or "").strip()[:255]
    if kind == OrderKind.DELIVERY and not address:
        raise ValidationError(words["address"])
    try:
        guests = max(1, min(int(guests or 1), 99))
    except (TypeError, ValueError):
        guests = 1
    fee = _fee(delivery_fee, words) if kind == OrderKind.DELIVERY else Decimal("0")
    for _attempt in range(5):
        try:
            with transaction.atomic():
                if table is not None:
                    table = DiningTable.objects.select_for_update().get(pk=table.pk)
                    if (working is not None and table.entity_id != working.pk) or (entity is not None and table.entity_id != entity.pk):
                        raise ValidationError(words["other_branch"])
                    if not table.active:
                        raise ValidationError(words["table_off"])
                    target = table.entity
                    existing = Order.objects.filter(table=table, status=OrderStatus.OPEN).first()
                    if existing:
                        return existing
                order = Order.objects.create(
                    number=_next_number(timezone.localdate()), kind=kind, table=table, guests=guests, customer=customer, waiter=waiter,
                    address=address if kind == OrderKind.DELIVERY else "", phone=(phone or (getattr(customer, "phone", "") if customer else "") or "").strip()[:50],
                    notes=(notes or "").strip()[:255], opened_by=user, delivery_fee=fee, entity=target,
                )
                _audit(order, user, "open_order", {"number": order.number, "kind": kind, "table": table.name if table else "",
                                                     "entity": target.pk}, event=AuditEventType.CREATE)
                return order
        except IntegrityError:
            continue  # the same number, or the table, was taken a moment ago; look again
    raise ValidationError(words["busy"].format(table=table.name if table else ""))


def _locked_open(order, words):
    order = Order.objects.select_for_update(of=("self",)).select_related("table").get(pk=order.pk)
    if order.status != OrderStatus.OPEN:
        raise ValidationError(words["closed"])
    return order


@transaction.atomic
def add_item(order, item, user, quantity=1, note="", lang="ar", options=None):
    """Add a dish. R1a (HG-109): ``options`` are the option pks chosen on the sheet, checked by ``modifiers.choose`` before
    anything is written; a priced add-on becomes its own line under the dish, a free choice is printed with it. A dish with
    nothing chosen (no groups, or optional ones left empty) merges with an identical plain unsent line, as before."""

    from . import modifiers

    words = MESSAGES[lang]
    order = _locked_open(order, words)
    if item is None or not item.active:
        raise ValidationError(words["item"])
    quantity = _quantity(quantity, words)
    note = (note or "").strip()[:120]
    chosen = modifiers.choose(item, options, lang)
    if not chosen:
        line = (order.lines.filter(item=item, note=note, ticket__isnull=True, voided=False, parent__isnull=True, options="", children__isnull=True)
                .order_by("pk").first())
        if line:
            line.quantity = _quantity(line.quantity + quantity, words)
            line.save(update_fields=["quantity"])
            return line
    line = OrderLine.objects.create(order=order, item=item, quantity=quantity, unit_price=money_round(item.default_sale_price), note=note,
                                    options=modifiers.free_text(chosen, lang))
    for option in modifiers.priced(chosen):  # never merged: each plate keeps its own add-ons
        OrderLine.objects.create(order=order, item=option.item, quantity=quantity, unit_price=money_round(option.item.default_sale_price), parent=line,
                                 options=option.label(lang)[:255])
    return line


def _dish_line(line, lang):
    """R1a: an add-on moves only with its dish."""

    if line.parent_id:
        from . import modifiers

        raise ValidationError(modifiers.MESSAGES["en" if lang == "en" else "ar"]["addon_alone"])
    return line


@transaction.atomic
def change_line(line, user, quantity=None, note=None, lang="ar"):
    """Change an unsent line; quantity 0 removes it. R1a: a dish's add-ons take its new quantity (and go with it)."""

    words = MESSAGES[lang]
    _locked_open(line.order, words)
    line = _dish_line(OrderLine.objects.get(pk=line.pk), lang)
    if line.ticket_id or line.voided:
        raise ValidationError(words["sent"])
    if quantity is not None and str(quantity).strip() in ("0", "0.0", "0.000"):
        line.delete()  # its add-ons go with it (CASCADE)
        return None
    if quantity is not None:
        line.quantity = _quantity(quantity, words)
    if note is not None:
        line.note = note.strip()[:120]
    line.save(update_fields=["quantity", "note"])
    line.children.update(quantity=line.quantity)
    return line


#: Restaurants R13 (HG-094): voiding a line the kitchen already has needs the selling right, as cancelling the order does.
VOID_SENT_RIGHT = "sales.create_sales_invoice"


@transaction.atomic
def void_line(line, user, lang="ar", *, reason=""):
    """Remove a line: an unsent one just goes; a sent one needs the selling right and a reason, is kept, marked void, and audited."""

    words = MESSAGES[lang]
    order = _locked_open(line.order, words)
    line = _dish_line(OrderLine.objects.select_related("item", "ticket").get(pk=line.pk), lang)
    if not line.ticket_id:
        line.delete()  # R1a: its add-ons go with it (CASCADE)
        return None
    if line.voided:
        return line
    if not user_has_permission(user, VOID_SENT_RIGHT):  # checked under the order's lock: the line may have been sent a moment ago
        raise PermissionDenied("Voiding a line already sent to the kitchen needs sales.create_sales_invoice.")
    reason = (reason or "").strip()[:255]
    if not reason:
        raise ValidationError(words["void_reason"])
    line.voided = True
    line.void_reason = reason  # R4: the kitchen's card and the order page show it
    line.ready_at = None  # R4: on a voided line this stamp means "the kitchen has seen it"; a void that arrives after the dish was ready is unseen
    line.save(update_fields=["voided", "void_reason", "ready_at"])
    addons = list(line.children.select_related("item"))  # R1a: the add-ons are voided with their dish, for the same reason
    line.children.update(voided=True, void_reason=reason, ready_at=None)
    after = {"line": line.pk, "item": line.item.item_code, "item_name": line.item.item_name, "quantity": str(line.quantity),
             "amount": str(line.amount), "ticket": line.ticket.sequence, "reason": reason}
    if addons:
        after["addons"] = [{"line": addon.pk, "item": addon.item.item_code, "amount": str(addon.amount)} for addon in addons]
    _audit(order, user, "void_order_line", after)
    return line


@transaction.atomic
def send_to_kitchen(order, user, lang="ar"):
    """Send every line not yet sent: one ticket per kitchen station (Restaurants R4), or one ticket when no station is set up.

    Returns the first ticket (its ``batch`` holds every ticket this send made, in station order), or None when there is nothing new."""

    order = _locked_open(order, MESSAGES[lang])
    pending = list(order.lines.filter(ticket__isnull=True, voided=False).select_related("item"))
    dishes = [line for line in pending if not line.parent_id]  # R1a: an add-on goes on its dish's ticket, never routed on its own
    if not dishes:
        return None
    sequence = max(order.tickets.values_list("sequence", flat=True), default=0)
    batch = []
    for station, lines in stations.route(dishes, entity=order.entity):  # R12: the order's branch's stations
        sequence += 1
        ticket = KitchenTicket.objects.create(order=order, sequence=sequence, sent_by=user, station=station)
        dish_ids = [line.pk for line in lines]
        OrderLine.objects.filter(pk__in=dish_ids).update(ticket=ticket)
        OrderLine.objects.filter(parent_id__in=dish_ids, ticket__isnull=True).update(ticket=ticket)
        batch.append(ticket)
    batch[0].batch = batch
    return batch[0]


@transaction.atomic
def move_table(order, table, user, lang="ar"):
    words = MESSAGES[lang]
    order = _locked_open(order, words)
    if order.split_from_id:  # R2a: a part has no table of its own while open; it follows its parent's
        raise ValidationError(words["part_move"])
    if order.kind != OrderKind.DINE_IN:
        raise ValidationError(words["dine_in_only"])
    if table is None:
        raise ValidationError(words["table_needed"])
    table = DiningTable.objects.select_for_update().get(pk=table.pk)
    if table.entity_id != order.entity_id:  # R12: an order stays in its branch
        raise ValidationError(words["other_branch"])
    if not table.active:
        raise ValidationError(words["table_off"])
    if table.pk == order.table_id:
        return order
    if Order.objects.filter(table=table, status=OrderStatus.OPEN).exists():
        raise ValidationError(words["busy"].format(table=table.name))
    before = order.table.name if order.table else ""
    order.table = table
    order.save(update_fields=["table"])
    _audit(order, user, "move_order", {"table": table.name}, {"table": before})
    return order


@transaction.atomic
def cancel_order(order, user, reason="", lang="ar"):
    words = MESSAGES[lang]
    order = _locked_open(order, words)
    if order.split_from_id:  # R2a: a part's lines go back to the bill, never away with it
        raise ValidationError(words["part_cancel"])
    _no_open_part(order, words)
    order.status = OrderStatus.CANCELLED
    order.closed_at = timezone.now()
    order.save(update_fields=["status", "closed_at"])
    _audit(order, user, "cancel_order", {"reason": (reason or "").strip()[:255], "total": str(order.total),
                                         "sent_lines": order.lines.filter(ticket__isnull=False, voided=False).count()})
    return order


@transaction.atomic
def dispatch(order, driver, user, *, delivery_fee=None, lang="ar"):
    """RESTO-003: a delivery leaves with its driver (the fee can be corrected here). Sent again, it changes the driver."""

    words = MESSAGES[lang]
    order = _locked_open(order, words)
    if order.kind != OrderKind.DELIVERY:
        raise ValidationError(words["not_delivery"])
    if driver is None:
        raise ValidationError(words["driver"])
    before = {"driver": order.driver_id, "fee": str(order.delivery_fee), "dispatched_at": order.dispatched_at.isoformat() if order.dispatched_at else ""}
    order.driver = driver
    if delivery_fee is not None:  # the form always sends the fee: a box emptied on purpose means no fee, as when the order was opened
        order.delivery_fee = _fee(delivery_fee, words)
    order.dispatched_at = order.dispatched_at or timezone.now()
    order.save(update_fields=["driver", "delivery_fee", "dispatched_at"])
    _audit(order, user, "dispatch_delivery", {"driver": driver.pk, "fee": str(order.delivery_fee), "dispatched_at": order.dispatched_at.isoformat()}, before)
    return order


FEE_ITEM_CODE = "DELIVERY-FEE"


def _fee_item(words):
    """The service item a delivery fee is billed as; made once (two first payments at once share it), like projects' billing item."""

    item, _ = Item.objects.get_or_create(item_code=FEE_ITEM_CODE, defaults={"item_name": words["fee_item"], "is_stock_tracked": False,
                                                                            "default_sale_price": Decimal("0")})
    if item.is_stock_tracked:  # a fee must never move stock
        raise ValidationError(words["fee_stocked"].format(code=FEE_ITEM_CODE))
    return item


def _payable(order, cashbox, words):
    """Paying, step 1 (validate): the order is still open (and now locked), has live lines, and there is a store and a cashbox.

    R12 (HG-108): the store is the order's own entity's default selling store, and the cashbox must be one of that entity's,
    whatever entity the biller works in."""

    order = _locked_open(order, words)
    _no_open_part(order, words)  # R2a: the rest of a split bill is paid after its open parts (or once they are put back)
    lines = [line for line in order.lines.select_related("item").filter(voided=False)]
    if not lines:
        raise ValidationError(words["empty"])
    location = branches.selling_location(order)
    if location is None or cashbox is None:
        raise ValidationError(words["setup"])
    if not branches.owns_cashbox(order, cashbox):
        raise ValidationError(words["cashbox_branch"])
    return order, lines, location


def bill_rows(order, lines, lang="ar"):
    """Paying, step 2 (draft): the invoice rows of the order's live lines, plus the delivery fee as its own line.

    Later restaurant tasks (add-ons, service charge, offers) add their rows here, so ``pay()`` stays one call per step."""

    from . import modifiers

    words = MESSAGES[lang]
    by_pk = {line.pk: line for line in lines}
    # R1a (HG-109): a priced add-on is an ordinary row right after its dish, «+ جبنة زيادة (برجر)»; a dish's row is as before
    # (its description is its note: the e-receipt prints a description in place of the item's name, so free choices stay off it).
    rows = [{"item": line.item, "quantity": line.quantity, "unit_sale_price": line.unit_price, "line_discount_amount": Decimal("0"),
             "description": modifiers.bill_description(line, by_pk[line.parent_id]) if line.parent_id in by_pk else line.note}
            for line in modifiers.families(lines)]
    if order.kind == OrderKind.DELIVERY and order.delivery_fee > 0:  # RESTO-003: the fee is its own line on the same invoice
        rows.append({"item": _fee_item(words), "quantity": Decimal("1"), "unit_sale_price": order.delivery_fee, "line_discount_amount": Decimal("0"),
                     "description": words["fee_line"]})
    return rows


def _bill_notes(order, lang):
    from settings_core.display_labels import choice_label

    table = order.home_table  # R2a: a part's invoice names its table's, as the table's own bill does
    where = table.name if table else choice_label(order, "kind", lang)
    return f"Restaurant {order.number} ({where})" if lang == "en" else f"طلب مطعم {order.number} ({where})"  # TRACK-ACTS W11


def _settle_and_post(order, rows, *, customer, location, cashbox, discount, tendered, user, lang, tenders=None):
    """Paying, steps 3 and 4 (settle, post): the cashier's own ``checkout``, unchanged. It drafts, takes the payment and posts in one go.

    R2b (HG-110): ``tenders`` (only when the bill is paid several ways) goes to ``checkout`` as it is."""

    from sales.pos import checkout

    if tenders is None:
        return checkout(lines=rows, customer=customer, location=location, cashbox=cashbox, discount=discount, tendered=tendered, user=user,
                        notes=_bill_notes(order, lang))
    return checkout(lines=rows, customer=customer, location=location, cashbox=cashbox, discount=discount, tendered=tendered, user=user,
                    notes=_bill_notes(order, lang), tenders=tenders, lang=lang)


def _split_cashboxes(order, tenders, words):
    """R2b (HG-110): a bill paid several ways has two amounts or more, each into one of the order's branch's cashboxes
    (R12). Returns the first tender's cashbox, which is the invoice's own."""

    if len(tenders) < 2:
        raise ValidationError(words["split_rows"])
    for box, _amount in tenders:
        if box is None:
            raise ValidationError(words["split_cashbox"])
        if not branches.owns_cashbox(order, box):
            raise ValidationError(words["cashbox_branch"])
    return tenders[0][0]


def bill_total(order, lines):
    """R2b (HG-110): what the bill asks before its discount, VAT included, worked out as ``checkout`` does, for the bill
    box's running «الباقي». Writes nothing (the delivery fee's VAT is at its item's rate, or the default rate before that
    item exists). The server checks the real total again when the bill is paid."""

    from taxes.services import compute_lines, default_rate, rate_for, vat_enabled

    rows = [{"item": line.item, "quantity": line.quantity, "unit_sale_price": line.unit_price, "line_discount_amount": Decimal("0")}
            for line in lines if not line.voided]
    total = sum((money_round(row["quantity"] * row["unit_sale_price"]) for row in rows), Decimal("0"))
    fee = order.delivery_fee if order.kind == OrderKind.DELIVERY and order.delivery_fee > 0 else Decimal("0")
    total += fee
    if vat_enabled():
        total += sum((tax for *_, tax in compute_lines(rows)), Decimal("0"))
        if fee:
            fee_item = Item.objects.filter(item_code=FEE_ITEM_CODE).first()
            rate = rate_for(fee_item) if fee_item is not None else default_rate()
            total += money_round(fee * (rate.rate if rate is not None else Decimal("0")) / Decimal("100"))
    return money_round(total)


def _close(order, invoice, customer, user):
    """Paying, step 5 (close): the order keeps its invoice and the table is free again."""

    order.invoice = invoice
    order.customer = customer
    order.status = OrderStatus.PAID
    order.closed_at = timezone.now()
    fields = ["invoice", "customer", "status", "closed_at"]
    if order.split_from_id and not order.table_id:  # R2a: a paid part takes its table's (the constraint covers open orders only)
        order.table_id = Order.objects.filter(pk=order.split_from_id).values_list("table_id", flat=True).first()
        fields.append("table")
    order.save(update_fields=fields)
    _audit(order, user, "pay_order", {"invoice": invoice.invoice_number, "total": str(invoice.total_amount)})


@transaction.atomic
def pay(order, user, *, cashbox, discount=Decimal("0"), tendered=Decimal("0"), customer=None, lang="ar", tenders=None):
    """Post the order as one sales invoice through the cashier's checkout. Returns (invoice, change).

    R2b (HG-110): ``tenders`` [(cashbox, amount), ...] pays the bill several ways (``cashbox`` and ``tendered`` are then
    unused): every cashbox the order's branch's, and the amounts adding up to the invoice's total exactly, or nothing is kept."""

    from sales.pos import walk_in_customer

    from . import kitchen

    words = MESSAGES[lang]
    if tenders is not None:
        cashbox = _split_cashboxes(order, tenders, words)
    order, lines, location = _payable(order, cashbox, words)
    kitchen.prepare_shortfall(lines, location, user, lang)  # RESTO-004: a kitchen dish is made from its ingredients before it is sold
    customer = customer or order.customer or walk_in_customer()
    rows = bill_rows(order, lines, lang)
    invoice, change = _settle_and_post(order, rows, customer=customer, location=location, cashbox=cashbox, discount=discount, tendered=tendered,
                                       user=user, lang=lang, tenders=tenders)
    if tenders is not None and money_round(sum((amount for _box, amount in tenders), Decimal("0"))) != invoice.total_amount:
        raise ValidationError(words["split_total"].format(total=f"{invoice.total_amount:,.2f}"))  # the whole payment is undone
    _close(order, invoice, customer, user)
    return invoice, change


def board():
    """Every active table with its open order (if any), plus open takeaway and delivery orders: the entity being worked in only (R12)."""

    open_orders = list(branches.orders(Order.objects.filter(status=OrderStatus.OPEN)).select_related("table", "customer", "waiter", "entity").prefetch_related("lines"))
    parts = {}  # R2a: an open part is money still to collect at its table: shown under its parent, never as a stray takeaway
    for order in open_orders:
        if order.split_from_id:
            parts.setdefault(order.split_from_id, []).append(order)
    by_table = {order.table_id: order for order in open_orders if order.table_id}
    tables = []
    for table in branches.tables(DiningTable.objects.filter(active=True)).select_related("entity"):
        order = by_table.get(table.pk)
        tables.append({"table": table, "order": order, "parts": parts.get(order.pk, []) if order else []})
    others = [order for order in open_orders if not order.table_id and not order.split_from_id]
    for order in others:
        order.open_parts = parts.get(order.pk, [])
    return tables, others


# --- R2a (HG-111): «ادفع جزء» (split by items), «رجّع للحساب» (put a part back), «ادمج مع طاولة» (merge two tables) ---------
#
# Rules, stated once:
#
# * a part is an ordinary order opened with some of a bill's lines, paid by the unchanged ``pay()`` on its own invoice. A
#   line is split by whole plates and cups (2 of 3 teas), never by money: VAT is rounded per line, so a third of a line
#   would put a piastre on one bill and not the other (trap 1); equal shares are R2b's tenders on one bill. A dish's add-ons
#   follow it in the same count, its free choices are copied, and the moved lines keep their kitchen ticket and ready state;
# * a part has no table while open (the database allows one open order per table) and takes its parent's once paid. It
#   cannot be split again, moved, merged or cancelled; while it is open the parent is not paid or cancelled. Choosing every
#   live line is refused: that is the bill itself. A part put back before it is paid returns its lines (a split line folds
#   back into the line it came from) and its own tickets, and becomes MERGED into its parent;
# * merging takes two open dine-in orders of one branch. The receiving order keeps its table and waiter and takes the
#   other's lines, tickets (numbered after its own), guests and parts; the other becomes MERGED and its table is free.
#   Both orders are locked in pk order, so two people merging the same two tables either way round never deadlock.


def _lock_one(pk):
    return Order.objects.select_for_update(of=("self",)).select_related("table").get(pk=pk)


def _lock_pair(first, second):
    """Both orders locked, the lower pk first; returned as (first, second)."""

    low, high = sorted((first.pk, second.pk))
    locked = {}
    locked[low] = _lock_one(low)
    locked[high] = _lock_one(high)
    return locked[first.pk], locked[second.pk]


def _no_open_part(order, words):
    if order.split_from_id:
        return
    part = order.splits.filter(status=OrderStatus.OPEN).order_by("pk").first()
    if part is not None:
        raise ValidationError(words["part_open"].format(number=part.number))


def _shown(quantity):
    return f"{quantity.normalize():f}" if quantity % 1 else str(int(quantity))


def _part_quantity(raw, line, words):
    """How many of ``line`` go to the part: a whole number up to the line's quantity (or the line's whole quantity, even if
    that is a fraction such as 1.5 kg). 0 or blank means none of it. Anything else is refused."""

    try:
        number = Decimal(str(raw if raw not in (None, "") else "0").strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        number = None
    shown = {"item": line.item.item_name, "max": _shown(line.quantity)}
    if number is None or not number.is_finite() or number < 0 or number > MAX_QUANTITY:
        raise ValidationError(words["part_qty"].format(**shown))
    if number == 0:
        return None
    if number > line.quantity:
        raise ValidationError(words["part_qty"].format(**shown))
    if number != line.quantity and number % 1:
        raise ValidationError(words["part_fraction"].format(**shown))
    return number.quantize(Decimal("0.001"))


def _open_part(order, user, words):
    for _attempt in range(5):
        try:
            with transaction.atomic():
                return Order.objects.create(number=_next_number(timezone.localdate()), kind=order.kind, table=None, guests=0, customer=order.customer,
                                            waiter=order.waiter, opened_by=user, entity_id=order.entity_id, split_from=order)
        except IntegrityError:
            continue  # the same number was taken a moment ago
    raise ValidationError(words["busy"].format(table=order.home_table.name if order.home_table else order.number))


def _copy_line(line, order, quantity, parent=None):
    copy = OrderLine.objects.create(order=order, item_id=line.item_id, quantity=quantity, unit_price=line.unit_price, note=line.note, ticket_id=line.ticket_id,
                                    ready_at=line.ready_at, voided=line.voided, void_reason=line.void_reason, options=line.options, parent=parent)
    OrderLine.objects.filter(pk=copy.pk).update(created_at=line.created_at)  # when it was ordered stays the line's (R6's happy hour reads it)
    return copy


def _move_part(line, quantity, part):
    """Move ``quantity`` of a dish (and its add-ons, in the same count) to ``part``: the line itself when it all goes, else a
    copy, the dish first so the invoice keeps family order."""

    addons = list(line.children.order_by("pk"))
    if quantity == line.quantity:
        OrderLine.objects.filter(pk__in=[line.pk] + [addon.pk for addon in addons]).update(order=part)
        return line
    rest = line.quantity - quantity
    copy = _copy_line(line, part, quantity)
    for addon in addons:
        _copy_line(addon, part, quantity, parent=copy)
    line.quantity = rest
    line.save(update_fields=["quantity"])
    line.children.update(quantity=rest)
    return copy


def _line_audit(line, quantity):
    return {"line": line.pk, "item": line.item.item_code, "quantity": str(quantity), "amount": str(money_round(quantity * line.unit_price))}


@transaction.atomic
def split_order(order, parts, user, lang="ar"):
    """«ادفع جزء»: open a part of ``order``'s bill with ``parts`` {line or line pk: quantity}. Returns the part (an open order).

    Every quantity is checked under the order's lock before anything is written; a refusal changes nothing."""

    words = MESSAGES[lang]
    order = _locked_open(order, words)
    if order.split_from_id:
        raise ValidationError(words["part_of_part"])
    if order.kind == OrderKind.DELIVERY:
        raise ValidationError(words["part_delivery"])
    lines = {line.pk: line for line in order.lines.select_related("item")}
    chosen = []
    for raw, quantity in (parts or {}).items():
        raw = raw.pk if isinstance(raw, OrderLine) else str(raw).strip()
        line = lines.get(int(raw)) if str(raw).isdigit() else None
        if line is None or line.voided:
            raise ValidationError(words["part_line"])
        _dish_line(line, lang)
        quantity = _part_quantity(quantity, line, words)
        if quantity:
            chosen.append((line, quantity))
    if not chosen:
        raise ValidationError(words["part_empty"])
    taken = {line.pk: quantity for line, quantity in chosen}
    if all(taken.get(line.pk) == line.quantity for line in lines.values() if not line.voided and not line.parent_id):
        raise ValidationError(words["part_all"])
    part = _open_part(order, user, words)
    for line, quantity in sorted(chosen, key=lambda pair: pair[0].pk):
        _move_part(line, quantity, part)
    moved = [_line_audit(line, quantity) for line, quantity in chosen]
    _audit(order, user, "split_order", {"part": part.number, "lines": moved, "amount": str(part.total)})
    _audit(part, user, "open_part", {"number": part.number, "from": order.number, "table": order.table.name if order.table else "", "lines": moved},
           event=AuditEventType.CREATE)
    return part


def _move_tickets(source, target):
    """``source``'s kitchen tickets become ``target``'s, numbered after its last (the numbering is unique per order).
    Returns [(old sequence, new sequence)]."""

    last = max(target.tickets.values_list("sequence", flat=True), default=0)
    moved = []
    for ticket in source.tickets.order_by("sequence"):
        last += 1
        KitchenTicket.objects.filter(pk=ticket.pk).update(order=target, sequence=last)
        moved.append((ticket.sequence, last))
    return moved


def _addons_of(line):
    return sorted((addon.item_id, addon.unit_price, addon.options, addon.note, addon.voided) for addon in line.children.all())


def _origin(order, line):
    """The live line of ``order`` that ``line`` was split from: the same dish, price, note, choices, ticket, ready state and add-ons."""

    candidates = order.lines.filter(item_id=line.item_id, unit_price=line.unit_price, note=line.note, options=line.options, ticket_id=line.ticket_id,
                                    voided=False, parent__isnull=True, ready_at__isnull=line.ready_at is None).order_by("pk")
    signature = _addons_of(line)
    return next((candidate for candidate in candidates if _addons_of(candidate) == signature), None)


@transaction.atomic
def put_back(part, user, lang="ar"):
    """«رجّع للحساب»: an open part's lines and tickets go back to its parent; the part becomes MERGED into it. Returns the parent."""

    words = MESSAGES[lang]
    current = Order.objects.get(pk=part.pk)
    if not current.split_from_id:
        raise ValidationError(words["not_part"])
    order, part = _lock_pair(Order(pk=current.split_from_id), current)
    if part.status != OrderStatus.OPEN or order.status != OrderStatus.OPEN:
        raise ValidationError(words["closed"])
    tickets = _move_tickets(part, order)
    returned = []
    for line in part.lines.filter(parent__isnull=True).select_related("item").order_by("pk"):
        returned.append(_line_audit(line, line.quantity))
        origin = None if line.voided else _origin(order, line)
        if origin is None:
            OrderLine.objects.filter(Q(pk=line.pk) | Q(parent_id=line.pk)).update(order=order)
            continue
        origin.quantity = _quantity(origin.quantity + line.quantity, words)
        origin.save(update_fields=["quantity"])
        origin.children.update(quantity=origin.quantity)
        line.delete()  # its add-on copies go with it (CASCADE)
    part.status = OrderStatus.MERGED
    part.merged_into = order
    part.closed_at = timezone.now()
    part.save(update_fields=["status", "merged_into", "closed_at"])
    _audit(order, user, "put_back_part", {"part": part.number, "lines": returned, "tickets": tickets, "total": str(order.total)})
    _audit(part, user, "merged_into", {"into": order.number, "put_back": True})
    return order


@transaction.atomic
def merge_orders(order, other, user, lang="ar"):
    """«ادمج مع طاولة»: ``other``'s table comes to ``order``'s. Returns ``order``, which keeps its table, waiter and customer."""

    words = MESSAGES[lang]
    if other is None:
        raise ValidationError(words["merge_pick"])
    if order.pk == other.pk:
        raise ValidationError(words["merge_self"])
    order, other = _lock_pair(order, other)
    if order.status != OrderStatus.OPEN:
        raise ValidationError(words["closed"])
    if other.status != OrderStatus.OPEN:
        raise ValidationError(words["merge_closed"])
    if order.split_from_id or other.split_from_id:
        raise ValidationError(words["merge_part"])
    if order.kind != OrderKind.DINE_IN or other.kind != OrderKind.DINE_IN:
        raise ValidationError(words["merge_kind"])
    if order.entity_id != other.entity_id:  # R12: an order stays in its branch
        raise ValidationError(words["other_branch"])
    before = {"guests": order.guests, "total": str(order.total)}
    lines = [_line_audit(line, line.quantity) for line in other.lines.select_related("item").filter(parent__isnull=True).order_by("pk")]
    tickets = _move_tickets(other, order)
    OrderLine.objects.filter(order=other).update(order=order)
    parts = list(other.splits.order_by("pk").values_list("number", flat=True))
    Order.objects.filter(split_from=other).update(split_from=order)  # its parts, open and paid, are this bill's now (its guests come too)
    order.guests = min(order.guests + other.guests, 999)
    order.save(update_fields=["guests"])
    other.status = OrderStatus.MERGED
    other.merged_into = order
    other.closed_at = timezone.now()
    other.save(update_fields=["status", "merged_into", "closed_at"])
    _audit(order, user, "merge_orders", {"from": other.number, "table": other.table.name if other.table else "", "guests": order.guests, "lines": lines,
                                         "tickets": tickets, "parts": parts, "total": str(order.total)}, before)
    _audit(other, user, "merged_into", {"into": order.number, "table": order.table.name if order.table else "", "guests": other.guests})
    return order
