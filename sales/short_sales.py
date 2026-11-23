"""HG-065: selling an item that is not in stock (Ahmed's notes #20, #21, #24; approved 26 Oct 2026).

Off by default; only the account owner switches it on. With it off a sale above
the warehouse's stock is refused exactly as before.

With it on, the till and the invoice name each short item and post only after
the seller agrees; the agreement goes in the audit log with who agreed. The
units no stock anywhere stands behind are costed at an estimate (the last
purchase, else the item's purchase price) and recorded as a ``ShortSale``.

When stock arrives, ``rebalance`` settles them, oldest first: a dated pair of
stock movements on the sale takes the units back at the estimate and issues
them again at the cost they really came in at. The difference lands in the cost
of sales on the arrival's date (the ledger books a sales invoice's stock rows to
cost of sales), and the stock left is valued as if the purchase had come first.
Nothing already posted is rewritten. If stock leaves again and the hole grows
back (a purchase cancelled), the latest settlements are reopened the same way.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum

from audit.models import AuditEventType, AuditLog
from config.money import cost_round, money_round

ZERO = Decimal("0")
QUANTITY = Decimal("0.001")
SETTING_KEY = "sales.sell_short"


# ---- the switch ----------------------------------------------------------


def enabled():
    from settings_core.models import SystemSetting

    return SystemSetting.objects.filter(key=SETTING_KEY, active=True, value="1").exists()


def is_owner(user):
    """The account owner: the owner account itself, or a user whose role is the owner role."""

    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    profile = getattr(user, "hesba_profile", None)
    role = getattr(profile, "role", None)
    return bool(profile is not None and profile.active and role is not None and role.active and role.code == "owner")


def set_enabled(on, user):
    from settings_core.models import SystemSetting

    if not is_owner(user):
        raise PermissionDenied("Only the account owner can allow selling without stock.")
    on = bool(on)
    if enabled() == on:
        return False
    SystemSetting.objects.update_or_create(key=SETTING_KEY, defaults={
        "value": "1" if on else "0", "data_type": SystemSetting.DataType.BOOLEAN, "active": True,
        "description": "HG-065: allow selling an item that is not in stock, after the seller agrees."})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="sales", action="sell_short_setting",
                            object_type="SystemSetting", object_id=SETTING_KEY, after_data={"enabled": on})
    return True


# ---- the refusal -----------------------------------------------------------


class StockShortage(ValidationError):
    """The stock check's refusal, naming every short item: [{item, available, required}]."""

    def __init__(self, shortages):
        first = shortages[0]
        super().__init__(
            f"Not enough stock for item {first['item']}. Available: {first['available']}, required: {first['required']}."
        )
        self.shortages = shortages


def shortages(invoice, lines):
    """Each stock item the lines need more of than the selling warehouse holds, in the order the lines name them."""

    from inventory.services import get_item_location_stock_quantity

    required, items = {}, {}
    for line in lines:
        if line.item.is_stock_tracked:
            required[line.item_id] = required.get(line.item_id, ZERO) + line.quantity
            items[line.item_id] = line.item
    found = []
    for item_id, need in required.items():
        available = get_item_location_stock_quantity(items[item_id], invoice.selling_location)
        if available < need:
            found.append({"item": items[item_id], "available": available, "required": need})
    return found


def record_agreement(invoice, found, user, source):
    AuditLog.objects.create(
        event_type=AuditEventType.ADJUSTMENT,
        actor=user,
        module="sales",
        action="sell_short",
        object_type="SalesInvoice",
        object_id=str(invoice.pk),
        reason={"pos": "Agreed at the till to sell without stock",
                "offline": "An offline till sale synced after the stock ran out",
                }.get(source, "Agreed on the invoice to sell without stock"),
        after_data={
            "invoice_number": invoice.invoice_number,
            "source": source,
            "location_id": invoice.selling_location_id,
            "items": [{"item_id": row["item"].pk, "item": str(row["item"]), "available": str(row["available"]),
                       "required": str(row["required"])} for row in found],
        },
    )


# ---- costing a line ------------------------------------------------------------


def estimate(item):
    """(unit cost, source) for units sold with no stock behind them: the last purchase, else the purchase price."""

    from inventory.models import StockMovement, StockMovementType

    last = (
        StockMovement.objects.filter(item=item, reversal_of__isnull=True, unit_cost__gt=0)
        .filter(Q(movement_type=StockMovementType.PURCHASE_IN, purchase_invoice__isnull=False)
                | Q(movement_type=StockMovementType.OPENING_STOCK))
        .order_by("-movement_date", "-pk").values_list("unit_cost", flat=True).first()
    )
    if last:
        return last, "last_purchase"
    if item.default_purchase_price and item.default_purchase_price > 0:
        return cost_round(item.default_purchase_price), "purchase_price"
    return ZERO, "none"


def line_cost(item, quantity):
    """(unit cost, estimate or None) for selling ``quantity`` of a stock item now.

    The part the item's stock (all warehouses: the average is item-wide) covers
    takes the authoritative average; the rest takes the estimate, and the line's
    unit cost is the blend."""

    from inventory.services import get_item_authoritative_average_cost, get_item_stock_quantity

    on_hand = get_item_stock_quantity(item)
    covered = min(max(on_hand, ZERO), quantity)
    missing = quantity - covered
    average = get_item_authoritative_average_cost(item)
    if missing <= 0:
        return average, None
    cost, source = estimate(item)
    unit = cost_round((covered * average + missing * cost) / quantity)
    return unit, {"estimated_quantity": missing, "estimated_unit_cost": cost, "estimate_source": source}


def record_line(line, location, location_short, found, user):
    from .models import ShortSale

    found = found or {}
    if location_short <= 0 and not found:
        return None
    return ShortSale.objects.create(
        sales_line=line, item=line.item, location=location, location_short=max(location_short, ZERO),
        estimated_quantity=found.get("estimated_quantity", ZERO), estimated_unit_cost=found.get("estimated_unit_cost", ZERO),
        estimate_source=found.get("estimate_source", "none"), agreed_by=user if location_short > 0 else None,
    )


# ---- what is open and settled --------------------------------------------------


def _used(short_sale):
    return short_sale.entries.aggregate(total=Sum("quantity"))["total"] or ZERO


def open_quantity(short_sale):
    return short_sale.estimated_quantity - _used(short_sale)


def settled_amount(short_sale):
    from .models import ShortSaleEntry

    return short_sale.entries.filter(kind=ShortSaleEntry.Kind.SETTLEMENT).aggregate(total=Sum("amount"))["total"] or ZERO


def _posted(item):
    from .models import SalesInvoiceStatus, ShortSale

    return (ShortSale.objects.filter(item=item, estimated_quantity__gt=0, sales_line__invoice__status=SalesInvoiceStatus.POSTED)
            .select_related("sales_line__invoice__selling_location").annotate(used=Sum("entries__quantity")).order_by("pk"))


def _open_rows(item):
    rows = []
    for short_sale in _posted(item):
        left = short_sale.estimated_quantity - (short_sale.used or ZERO)
        if left > 0:
            rows.append((short_sale, left))
    return rows


def _standing(**where):
    """Settlements still standing (net of their reversals), newest first: [(entry, quantity left)]."""

    from .models import ShortSaleEntry

    entries = (ShortSaleEntry.objects.filter(kind=ShortSaleEntry.Kind.SETTLEMENT, reversal_of__isnull=True, **where)
               .select_related("short_sale__sales_line__invoice__selling_location", "short_sale__item", "movement_in", "movement_out")
               .annotate(back=Sum("reversals__quantity")).order_by("-pk"))
    rows = []
    for entry in entries:
        left = entry.quantity + (entry.back or ZERO)  # reversal rows carry negative quantities
        if left > 0:
            rows.append((entry, left))
    return rows


def _period_open(day):
    from closing.services import ensure_period_is_open

    try:
        ensure_period_is_open(day)
    except ValidationError:
        return False
    return True


# ---- the stock movements of a settlement ------------------------------------------


def _pair(short_sale, day, quantity, first_type, first_cost, second_type, second_cost, user, note, reversal_of=(None, None)):
    from inventory.models import StockMovement

    line = short_sale.sales_line
    invoice = line.invoice
    made = []
    for movement_type, unit_cost, original in ((first_type, first_cost, reversal_of[0]), (second_type, second_cost, reversal_of[1])):
        movement = StockMovement(
            movement_date=day, movement_type=movement_type, item=short_sale.item, location=invoice.selling_location,
            quantity=quantity, unit_cost=unit_cost, sales_invoice=invoice, sales_line=line, reversal_of=original,
            notes=f"{note} {invoice.invoice_number}", created_by=user,
        )
        movement.full_clean()
        movement.save()
        made.append(movement)
    return made


def _settle(short_sale, quantity, unit_cost, day, user):
    from inventory.models import StockMovementType

    from .models import ShortSaleEntry

    back, out = _pair(short_sale, day, quantity, StockMovementType.SALE_RETURN_IN, short_sale.estimated_unit_cost,
                      StockMovementType.SALE_OUT, unit_cost, user, "Cost settlement (sold before it arrived)")
    return ShortSaleEntry.objects.create(
        short_sale=short_sale, kind=ShortSaleEntry.Kind.SETTLEMENT, entry_date=day, quantity=quantity, unit_cost=unit_cost,
        amount=money_round(quantity * unit_cost) - money_round(quantity * short_sale.estimated_unit_cost),
        movement_in=back, movement_out=out, created_by=user,
    )


def _reverse_settlement(entry, quantity, day, user):
    from inventory.models import StockMovementType

    from .models import ShortSaleEntry

    short_sale = entry.short_sale
    out, back = _pair(short_sale, day, quantity, StockMovementType.SALE_OUT, short_sale.estimated_unit_cost,
                      StockMovementType.SALE_RETURN_IN, entry.unit_cost, user, "Reverse cost settlement",
                      reversal_of=(entry.movement_in, entry.movement_out))
    amount = entry.amount if quantity == entry.quantity else money_round(quantity * entry.unit_cost) - money_round(quantity * short_sale.estimated_unit_cost)
    return ShortSaleEntry.objects.create(
        short_sale=short_sale, kind=ShortSaleEntry.Kind.SETTLEMENT, entry_date=day, quantity=-quantity, unit_cost=entry.unit_cost,
        amount=-amount, movement_in=back, movement_out=out, reversal_of=entry, created_by=user,
    )


# ---- keeping the estimates in step with the stock ---------------------------------


def rebalance(item, day, user=None):
    """Settle the short sales that stock now stands behind, or reopen settlements when the hole grew back.

    The item's units below zero are the hole; the short sales' open units should
    match it. Stock that arrived beyond the hole settles open units, oldest sale
    first, at the cost of the stock that is really there: the stock value with
    the open estimates put back, over the quantity with the open units put back.
    That is the arriving cost when the item was at or below zero. Each settlement
    is dated the later of ``day`` and the sale's date; one whose month is closed
    waits for the next arrival. Callers hold the item's lock."""

    from inventory.services import (
        get_item_stock_quantity,
        get_item_stock_value,
        lock_items_for_stock_check,
        recalculate_item_average_cost,
    )

    from .models import ShortSale

    if not item.is_stock_tracked or not ShortSale.objects.filter(item=item, estimated_quantity__gt=0).exists():
        return  # never sold short: nothing to settle, and no lock taken
    lock_items_for_stock_check([item.pk])
    rows = _open_rows(item)
    total_open = sum((left for _, left in rows), ZERO)
    on_hand = get_item_stock_quantity(item)
    hole = max(-on_hand, ZERO)
    changed = False
    if total_open > hole:
        fill = total_open - hole
        real_value = get_item_stock_value(item) + sum((left * short_sale.estimated_unit_cost for short_sale, left in rows), ZERO)
        unit_cost = cost_round(max(real_value, ZERO) / (on_hand + total_open))
        for short_sale, left in rows:
            if fill <= 0:
                break
            on = max(day, short_sale.sales_line.invoice.invoice_date)
            if not _period_open(on):
                continue
            quantity = min(left, fill)
            _settle(short_sale, quantity, unit_cost, on, user)
            fill -= quantity
            changed = True
    elif hole > total_open:
        reopen = hole - total_open
        from .models import SalesInvoiceStatus

        for entry, left in _standing(short_sale__item=item, short_sale__sales_line__invoice__status=SalesInvoiceStatus.POSTED):
            if reopen <= 0:
                break
            on = max(day, entry.entry_date)
            if not _period_open(on):
                continue
            quantity = min(left, reopen)
            _reverse_settlement(entry, quantity, on, user)
            reopen -= quantity
            changed = True
    if changed:
        recalculate_item_average_cost(item)


def rebalance_items(items, day, user=None):
    for item in sorted({item.pk: item for item in items}.values(), key=lambda item: item.pk):
        rebalance(item, day, user)


# ---- cancellations and returns of a short sale -----------------------------------


def reverse_for_cancel(invoice, user):
    """A cancelled sale gives back its settlements first, each on its own date, so the stock it returns at the
    line's cost leaves the books as if it had never been sold. Its open units close with it (it is no longer posted)."""

    for entry, left in _standing(short_sale__sales_line__invoice=invoice):
        if not _period_open(entry.entry_date):
            raise ValidationError("A cost settlement of this sale is in a closed period; reopen it to cancel the sale.")
        _reverse_settlement(entry, left, entry.entry_date, user)


def return_cost(line, quantity, prior_quantity, prior_cost):
    """(unit cost, cost amount) a return of a short line comes back at: its share of what the units still sold cost,
    the settlements so far included. None for a line that is not short."""

    short_sale = getattr(line, "short_sale", None)
    if short_sale is None or short_sale.estimated_quantity <= 0:
        return None
    remaining_quantity = line.quantity - prior_quantity
    remaining_cost = line.line_cost_amount + settled_amount(short_sale) - prior_cost
    if quantity == remaining_quantity:
        cost_amount = money_round(remaining_cost)
    else:
        cost_amount = money_round(remaining_cost * quantity / remaining_quantity)
    return cost_round(remaining_cost / remaining_quantity), max(cost_amount, ZERO)


def release_for_return(return_line, sales_return, prior_quantity, user):
    """The returned units take their share of the line's open estimate with them."""

    from .models import ShortSaleEntry

    line = return_line.source_line
    short_sale = getattr(line, "short_sale", None)
    if short_sale is None or short_sale.estimated_quantity <= 0:
        return None
    left = open_quantity(short_sale)
    if left <= 0:
        return None
    remaining_quantity = line.quantity - prior_quantity
    if return_line.quantity >= remaining_quantity:
        quantity = left
    else:
        quantity = min(left, (left * return_line.quantity / remaining_quantity).quantize(QUANTITY, rounding=ROUND_HALF_UP))
    if quantity <= 0:
        return None
    return ShortSaleEntry.objects.create(short_sale=short_sale, kind=ShortSaleEntry.Kind.RELEASE, entry_date=sales_return.return_date,
                                         quantity=quantity, sales_return=sales_return, created_by=user)


def reopen_for_return_cancel(sales_return, day, user):
    from .models import ShortSaleEntry

    for entry in sales_return.short_sale_entries.filter(kind=ShortSaleEntry.Kind.RELEASE, reversal_of__isnull=True):
        ShortSaleEntry.objects.create(short_sale=entry.short_sale, kind=ShortSaleEntry.Kind.RELEASE, entry_date=day,
                                      quantity=-entry.quantity, sales_return=sales_return, reversal_of=entry, created_by=user)
