"""RESTO-001: take an order at a table (or for takeaway / delivery), send it to the kitchen, bill it.

Rules, stated once:

* a table holds at most one open order (a database constraint, plus a lock on
  the table row); opening an occupied table joins the order already there;
* lines can be added while the order is open. A line not yet sent to the
  kitchen can be changed or removed freely; one already sent can only be
  voided, which is audited, so the kitchen's work never disappears silently;
* "send to kitchen" gathers every unsent line into one numbered ticket to
  print; nothing new means no ticket;
* paying an order calls the cashier screen's own ``checkout`` (the same call
  POS-001 makes), so VAT, stock, cashbox, customer ledger and period rules are
  the sales engine's, unchanged. The order only keeps a link to the posted
  invoice. Stock-tracked lines (a canned drink) leave stock; dishes are
  service items and do not. Ingredient deduction by recipe is not done here.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from .models import DiningTable, KitchenTicket, Order, OrderKind, OrderLine, OrderStatus
from entities import scope as entity_scope


MAX_QUANTITY = Decimal("999")
MESSAGES = {
    "ar": {
        "table_name": "اكتب اسم أو رقم الطاولة.", "table_taken": "فيه طاولة بنفس الاسم.", "seats": "عدد الكراسي لازم من 1 لـ 99.",
        "table_needed": "اختار الطاولة.", "table_off": "الطاولة دي مش شغالة.", "busy": "الطاولة {table} عليها طلب مفتوح.",
        "closed": "الطلب ده اتقفل؛ مينفعش يتعدّل.", "item": "اختار صنف من المنيو.", "qty": "الكمية لازم أكبر من صفر ولحد {max}.",
        "sent": "السطر ده راح المطبخ؛ تقدر تلغيه بس.", "empty": "الطلب فاضي.", "address": "الدليفري محتاج عنوان.",
        "setup": "لازم يكون فيه مخزن بيع نشط.", "dine_in_only": "النقل للطاولات بس لطلبات الصالة.", "has_open": "الطاولة دي عليها طلبات مفتوحة؛ اقفلها الأول.",
    },
    "en": {
        "table_name": "Enter the table name or number.", "table_taken": "A table with this name exists.", "seats": "Seats must be 1 to 99.",
        "table_needed": "Choose the table.", "table_off": "This table is not active.", "busy": "Table {table} has an open order.",
        "closed": "This order is closed; it cannot be changed.", "item": "Choose an item from the menu.", "qty": "The quantity must be above zero and up to {max}.",
        "sent": "This line went to the kitchen; it can only be voided.", "empty": "The order is empty.", "address": "A delivery needs an address.",
        "setup": "An active selling location is needed.", "dine_in_only": "Only dine-in orders move between tables.", "has_open": "This table has open orders; close them first.",
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
def save_table(data, user, table=None, lang="ar"):
    words = MESSAGES[lang]
    name = (data.get("name") or "").strip()[:40]
    if not name:
        raise ValidationError(words["table_name"])
    if DiningTable.objects.filter(name__iexact=name).exclude(pk=getattr(table, "pk", None)).exists():
        raise ValidationError(words["table_taken"])
    try:
        seats = int(data.get("seats") or 4)
    except (TypeError, ValueError):
        seats = 0
    if not 1 <= seats <= 99:
        raise ValidationError(words["seats"])
    active = data.get("active", True) not in (False, "", "0", "off", None)
    if table is not None and not active and table.orders.filter(status=OrderStatus.OPEN).exists():
        raise ValidationError(words["has_open"])
    values = {"name": name, "area": (data.get("area") or "").strip()[:60], "seats": seats, "active": active,
              "sort_order": int(data.get("sort_order") or 0) if str(data.get("sort_order") or "0").isdigit() else 0}
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


def open_order(user, *, kind=OrderKind.DINE_IN, table=None, guests=1, customer=None, waiter=None, address="", phone="", notes="", lang="ar"):
    """A new order, or, for a table already taken, the order already open there."""

    words = MESSAGES[lang]
    kind = kind if kind in OrderKind.values else OrderKind.DINE_IN
    if kind == OrderKind.DINE_IN:
        if table is None:
            raise ValidationError(words["table_needed"])
    else:
        table = None
    address = (address or (getattr(customer, "address", "") if customer else "") or "").strip()[:255]
    if kind == OrderKind.DELIVERY and not address:
        raise ValidationError(words["address"])
    try:
        guests = max(1, min(int(guests or 1), 99))
    except (TypeError, ValueError):
        guests = 1
    for _attempt in range(5):
        try:
            with transaction.atomic():
                if table is not None:
                    table = DiningTable.objects.select_for_update().get(pk=table.pk)
                    if not table.active:
                        raise ValidationError(words["table_off"])
                    existing = Order.objects.filter(table=table, status=OrderStatus.OPEN).first()
                    if existing:
                        return existing
                order = Order.objects.create(
                    number=_next_number(timezone.localdate()), kind=kind, table=table, guests=guests, customer=customer, waiter=waiter,
                    address=address if kind == OrderKind.DELIVERY else "", phone=(phone or (getattr(customer, "phone", "") if customer else "") or "").strip()[:50],
                    notes=(notes or "").strip()[:255], opened_by=user,
                )
                _audit(order, user, "open_order", {"number": order.number, "kind": kind, "table": table.name if table else ""}, event=AuditEventType.CREATE)
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
def add_item(order, item, user, quantity=1, note="", lang="ar"):
    words = MESSAGES[lang]
    order = _locked_open(order, words)
    if item is None or not item.active:
        raise ValidationError(words["item"])
    quantity = _quantity(quantity, words)
    note = (note or "").strip()[:120]
    line = order.lines.filter(item=item, note=note, ticket__isnull=True, voided=False).first()
    if line:
        line.quantity = _quantity(line.quantity + quantity, words)
        line.save(update_fields=["quantity"])
    else:
        line = OrderLine.objects.create(order=order, item=item, quantity=quantity, unit_price=money_round(item.default_sale_price), note=note)
    return line


@transaction.atomic
def change_line(line, user, quantity=None, note=None, lang="ar"):
    """Change an unsent line; quantity 0 removes it."""

    words = MESSAGES[lang]
    _locked_open(line.order, words)
    line = OrderLine.objects.get(pk=line.pk)
    if line.ticket_id or line.voided:
        raise ValidationError(words["sent"])
    if quantity is not None and str(quantity).strip() in ("0", "0.0", "0.000"):
        line.delete()
        return None
    if quantity is not None:
        line.quantity = _quantity(quantity, words)
    if note is not None:
        line.note = note.strip()[:120]
    line.save(update_fields=["quantity", "note"])
    return line


@transaction.atomic
def void_line(line, user, lang="ar"):
    """Remove a line: an unsent one just goes; a sent one is kept, marked void, and audited."""

    words = MESSAGES[lang]
    order = _locked_open(line.order, words)
    line = OrderLine.objects.select_related("item").get(pk=line.pk)
    if not line.ticket_id:
        line.delete()
        return None
    if not line.voided:
        line.voided = True
        line.save(update_fields=["voided"])
        _audit(order, user, "void_order_line", {"item": line.item.item_code, "quantity": str(line.quantity), "amount": str(line.amount)})
    return line


@transaction.atomic
def send_to_kitchen(order, user, lang="ar"):
    """One ticket with every line not yet sent; None when there is nothing new."""

    order = _locked_open(order, MESSAGES[lang])
    pending = order.lines.filter(ticket__isnull=True, voided=False)
    if not pending.exists():
        return None
    ticket = KitchenTicket.objects.create(order=order, sequence=order.tickets.count() + 1, sent_by=user)
    pending.update(ticket=ticket)
    return ticket


@transaction.atomic
def move_table(order, table, user, lang="ar"):
    words = MESSAGES[lang]
    order = _locked_open(order, words)
    if order.kind != OrderKind.DINE_IN:
        raise ValidationError(words["dine_in_only"])
    if table is None:
        raise ValidationError(words["table_needed"])
    table = DiningTable.objects.select_for_update().get(pk=table.pk)
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
    order = _locked_open(order, MESSAGES[lang])
    order.status = OrderStatus.CANCELLED
    order.closed_at = timezone.now()
    order.save(update_fields=["status", "closed_at"])
    _audit(order, user, "cancel_order", {"reason": (reason or "").strip()[:255], "total": str(order.total),
                                         "sent_lines": order.lines.filter(ticket__isnull=False, voided=False).count()})
    return order


@transaction.atomic
def pay(order, user, *, cashbox, discount=Decimal("0"), tendered=Decimal("0"), customer=None, lang="ar"):
    """Post the order as one sales invoice through the cashier's checkout. Returns (invoice, change)."""

    from master_data.models import Location
    from sales.pos import checkout, walk_in_customer

    words = MESSAGES[lang]
    order = _locked_open(order, words)
    lines = [line for line in order.lines.select_related("item").filter(voided=False)]
    if not lines:
        raise ValidationError(words["empty"])
    location = entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()
    if location is None or cashbox is None:
        raise ValidationError(words["setup"])
    customer = customer or order.customer or walk_in_customer()
    where = order.table.name if order.table else order.get_kind_display()
    invoice, change = checkout(
        lines=[{"item": line.item, "quantity": line.quantity, "unit_sale_price": line.unit_price, "line_discount_amount": Decimal("0"),
                "description": line.note} for line in lines],
        customer=customer, location=location, cashbox=cashbox, discount=discount, tendered=tendered, user=user,
        notes=f"Restaurant {order.number} ({where})",
    )
    order.invoice = invoice
    order.customer = customer
    order.status = OrderStatus.PAID
    order.closed_at = timezone.now()
    order.save(update_fields=["invoice", "customer", "status", "closed_at"])
    _audit(order, user, "pay_order", {"invoice": invoice.invoice_number, "total": str(invoice.total_amount)})
    return invoice, change


def board():
    """Every active table with its open order (if any), plus open takeaway and delivery orders."""

    open_orders = Order.objects.filter(status=OrderStatus.OPEN).select_related("table", "customer", "waiter").prefetch_related("lines")
    by_table = {order.table_id: order for order in open_orders if order.table_id}
    tables = [{"table": table, "order": by_table.get(table.pk)} for table in DiningTable.objects.filter(active=True)]
    others = [order for order in open_orders if not order.table_id]
    return tables, others
