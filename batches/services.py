"""BATCH-001: which batches are still on the shelf, and which expire soon.

Nothing here moves stock. The on-hand quantity of an item always comes from
``inventory`` (its stock movements, across every location). The batches
received for that item share that quantity out, **earliest expiry first
out** (FEFO): what is still on hand is taken to be the batches that expire
last. A batch with no expiry date is treated as expiring last of all.

That is how a pharmacy or a grocery is expected to sell, and it means the
batch figures can never disagree with the stock figure: a sale, a return, a
count adjustment or a cancelled invoice changes on-hand, and the batches
follow. Quantity on hand that no batch covers (stock from before batches were
switched on) shows as "no batch" until it is registered.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from inventory.models import StockMovement
from inventory.services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES
from settings_core.capabilities import capability_enabled

from .models import Batch


ZERO = Decimal("0")
DEFAULT_WARN_DAYS = 60
FAR_FUTURE = date(9999, 12, 31)


def batches_enabled():
    return capability_enabled("batches_expiry")


def counted_batches():
    """Batches that are really in stock: registered by hand, or on a posted purchase."""

    return Batch.objects.filter(active=True).filter(Q(purchase_line__isnull=True) | Q(purchase_line__invoice__status="posted"))


def attach_purchase_batches(invoice, line_data, user=None):
    """Store the batch number / expiry typed on each draft purchase line.

    ``line_data`` is the list the draft was created from, in line order. The
    batch counts only once the invoice is posted, and disappears with a
    cancelled invoice (see ``counted_batches``).
    """

    created = []
    for line, data in zip(invoice.lines.order_by("line_number"), line_data):
        batch_no = (data.get("batch_no") or "").strip()
        expiry = data.get("expiry_date")
        if not batch_no and not expiry:
            continue
        created.append(Batch.objects.create(
            item=line.item, location=invoice.receiving_location, batch_no=batch_no, expiry_date=expiry,
            quantity=line.quantity, received_on=invoice.invoice_date, purchase_line=line, created_by=user,
        ))
    return created


def stock_on_hand(item_ids):
    """{item_id: on-hand quantity across every location}, from stock movements."""

    totals = defaultdict(lambda: ZERO)
    rows = StockMovement.objects.filter(item_id__in=item_ids).values("item_id", "movement_type").annotate(total=Sum("quantity"))
    for row in rows:
        if row["movement_type"] in IN_MOVEMENT_TYPES:
            totals[row["item_id"]] += row["total"] or ZERO
        elif row["movement_type"] in OUT_MOVEMENT_TYPES:
            totals[row["item_id"]] -= row["total"] or ZERO
    return totals


def batch_positions(items=None):
    """Every counted batch with what is left of it, plus the uncovered quantity per item.

    Returns ``(rows, uncovered)``: rows are dicts {batch, remaining}, in
    expiry order; ``uncovered`` is {item_id: on-hand quantity no batch covers}.
    """

    batches = counted_batches().select_related("item", "location")
    if items is not None:
        batches = batches.filter(item__in=items)
    by_item = defaultdict(list)
    for batch in batches:
        by_item[batch.item_id].append(batch)
    on_hand = stock_on_hand(list(by_item))
    rows, uncovered = [], {}
    for item_id, item_batches in by_item.items():
        left = max(on_hand[item_id], ZERO)
        # Hand the on-hand quantity to the batches that expire last.
        for batch in sorted(item_batches, key=lambda b: (b.expiry_date or FAR_FUTURE, b.received_on, b.pk), reverse=True):
            share = min(left, batch.quantity)
            left -= share
            rows.append({"batch": batch, "remaining": share})
        if left > 0:
            uncovered[item_id] = left
    rows.sort(key=lambda row: (row["batch"].expiry_date or FAR_FUTURE, row["batch"].item.item_code, row["batch"].pk))
    return rows, uncovered


def expiry_state(expiry, today, warn_days):
    if expiry is None:
        return "none"
    if expiry < today:
        return "expired"
    if expiry <= today + timedelta(days=warn_days):
        return "soon"
    return "ok"


def expiry_alerts(today=None, warn_days=DEFAULT_WARN_DAYS):
    """{"expired": [...], "soon": [...]} of batches still on hand."""

    today = today or timezone.localdate()
    result = {"expired": [], "soon": []}
    if not batches_enabled():
        return result
    rows, _ = batch_positions()
    for row in rows:
        if row["remaining"] <= 0:
            continue
        state = expiry_state(row["batch"].expiry_date, today, warn_days)
        if state in result:
            result[state].append(row)
    return result


@transaction.atomic
def register_batch(*, item, location, batch_no, expiry_date, quantity, received_on, user, note=""):
    """Record a batch already on the shelf (stock from before batches were used)."""

    batch = Batch(item=item, location=location, batch_no=(batch_no or "").strip(), expiry_date=expiry_date,
                  quantity=quantity, received_on=received_on, note=(note or "").strip(), created_by=user)
    batch.full_clean()
    batch.save()
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE, actor=user, module="batches", action="register_batch",
        object_type="batches.Batch", object_id=str(batch.pk), before_data={},
        after_data={"item": item.item_code, "batch_no": batch.batch_no, "expiry_date": str(expiry_date or ""), "quantity": str(quantity)},
        reason="Batch registered for stock already on hand.",
    )
    return batch


@transaction.atomic
def retire_batch(batch, user, reason=""):
    """Take a registered batch out of the tracking (it was entered by mistake)."""

    batch.active = False
    batch.save(update_fields=["active"])
    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE, actor=user, module="batches", action="retire_batch",
        object_type="batches.Batch", object_id=str(batch.pk), before_data={"active": True}, after_data={"active": False},
        reason=reason or "Batch retired from the batches screen.",
    )
