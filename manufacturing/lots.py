"""Manufacturing F3 (HG-131): a lot on every production run, and the ingredient lots it used.

A batch is a tracking record (HG-019): nothing here posts, moves or values stock. Two things are recorded, both only
while ``batches_enabled()`` and both inside the transaction of the call they wrap:

* **The product's lot.** A finished order or a plain run creates a ``Batch`` for the product at the run's location:
  the lot number defaults to the run number, the expiry to the run date plus the recipe's ``shelf_life_days``, the
  quantity to the units put into stock. It counts while its run is posted (``batches.services.counted_batches``);
  cancelling the run stops it counting, nothing is deleted.
* **The ingredient lots.** The batch screens do not book lots per movement: they hand the on-hand quantity to the
  latest-expiring lots first, so what leaves is the unbatched stock and then the earliest-expiring lot. The lots an
  issue used are therefore **the positions just before the issue minus the positions just after it**, per component
  at the issue's location, written to ``LotUse`` at that moment. Computed later they would differ, because positions
  move with stock. The worker never picks a lot (owner question 4): a pick would disagree with every other screen.

An ingredient lot that is past its expiry on the issue date and that this issue would use is refused, and the whole
transaction rolls back (owner question 6): the lot is written off with an ordinary stock adjustment first. Unbatched
stock never refuses. The product's expiry is never capped by its ingredients' (owner question 5); the run page shows
a warning instead.

The inventory engine, average cost and ``services.issue`` / ``services.produce`` are not edited. These wrappers call
them unchanged. The restaurant kitchen keeps calling ``services.produce`` directly, so it records no lots by design.
The demo seed starts orders through ``orders.advance`` (never finishes one), so it records the lots its orders use.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType
from batches.models import Batch
from batches.services import batch_positions, batches_enabled

from . import services
from .models import LotUse

ZERO = Decimal("0")
MAX_LABELS = 500

MESSAGES = {
    "ar": {
        "expired": "{item} {lot} خلص تاريخه؛ اشطبه الأول",
        "lot_expiry": "تاريخ صلاحية المنتج لازم يكون في يوم التشغيل أو بعده.",
    },
    "en": {
        "expired": "{item} {lot} has expired; write it off first",
        "lot_expiry": "The product's expiry date must be on or after the run date.",
    },
}


def _positions(items, location):
    """{batch pk: (batch, what is left of it)} for these items, the lots and the stock at this location only."""

    rows, _uncovered = batch_positions(items=items, locations=[location])
    return {row["batch"].pk: (row["batch"], row["remaining"]) for row in rows}


def lot_name(batch):
    return batch.batch_no or (batch.expiry_date.isoformat() if batch.expiry_date else "-")


def _clean_lot(lot_no, lot_expiry, run_date, lang):
    if lot_expiry is not None and lot_expiry < run_date:
        raise ValidationError(MESSAGES[lang]["lot_expiry"])
    return (lot_no or "").strip()[:60], lot_expiry


def record_uses(operations, before, after, when, lang, *, run=None, order=None):
    """Write ``LotUse`` for the issue ``operations`` from positions ``before`` and ``after`` it, and refuse an expired lot.

    Every component gets rows that add up to its issued quantity: one per lot used, and a "no lot" row (``batch`` NULL)
    for whatever no lot covered (including a component that has no lots at all)."""

    rows = []
    for operation in operations:
        used = []
        for pk, (batch, was) in before.items():
            if batch.item_id != operation.item_id:
                continue
            left = after[pk][1] if pk in after else ZERO
            if was - left > 0:
                used.append((batch, was - left))
        for batch, _quantity in used:
            if batch.expiry_date is not None and batch.expiry_date < when:
                raise ValidationError(MESSAGES[lang]["expired"].format(item=operation.item.item_name, lot=lot_name(batch)))
        unbatched = operation.quantity - sum((quantity for _batch, quantity in used), ZERO)
        if unbatched > 0:
            rows.append(LotUse(run=run, order=order, item_id=operation.item_id, batch=None, quantity=unbatched))
        for batch, quantity in sorted(used, key=lambda pair: (pair[0].expiry_date is None, pair[0].expiry_date, pair[0].pk)):
            rows.append(LotUse(run=run, order=order, item_id=operation.item_id, batch=batch, quantity=quantity))
    LotUse.objects.bulk_create(rows)
    return rows


def _lock(recipe):
    from inventory.services import lock_items_for_stock_check

    # Before the "before" snapshot, so a sale that lands between the two cannot end up in the difference.
    lock_items_for_stock_check(list(recipe.lines.values_list("component_id", flat=True)))


@transaction.atomic
def issue(recipe, user, *, batches, location, reference, issue_date=None, lang="ar", order=None):
    """``services.issue`` (an order's start), then the ingredient lots it used, for ``order``."""

    if not batches_enabled() or location is None:
        return services.issue(recipe, user, batches=batches, location=location, reference=reference, issue_date=issue_date, lang=lang)
    when = issue_date or timezone.localdate()
    _lock(recipe)
    items = [line.component for line in recipe.lines.select_related("component")]
    before = _positions(items, location)
    operations = services.issue(recipe, user, batches=batches, location=location, reference=reference, issue_date=when, lang=lang)
    record_uses(operations, before, _positions(items, location), when, lang, order=order)
    return operations


@transaction.atomic
def produce(recipe, user, *, batches, location, run_date=None, lang="ar", lot_no="", lot_expiry=None, **kwargs):
    """A plain run (``services.produce`` consuming its materials now), with its ingredient lots and its product's lot."""

    if not batches_enabled() or location is None:
        return services.produce(recipe, user, batches=batches, location=location, run_date=run_date, lang=lang, **kwargs)
    when = run_date or timezone.localdate()
    lot_no, lot_expiry = _clean_lot(lot_no, lot_expiry, when, lang)
    _lock(recipe)
    items = [line.component for line in recipe.lines.select_related("component")]
    before = _positions(items, location)
    run = services.produce(recipe, user, batches=batches, location=location, run_date=when, lang=lang, **kwargs)
    operations = [consumption.operation for consumption in run.consumptions.select_related("operation", "operation__item")]
    record_uses(operations, before, _positions(items, location), when, lang, run=run)
    record_product_lot(run, user, lot_no, lot_expiry)
    return run


def check_lot(lot_no, lot_expiry, lang="ar"):
    """What an order's finish asks before it posts: a typed expiry earlier than today is refused up front."""

    return _clean_lot(lot_no, lot_expiry, timezone.localdate(), lang)


def record_product_lot(run, user, lot_no="", lot_expiry=None, location=None):
    """The ``Batch`` of the run's product at its location (only while batches are on). Returns it, or None.

    Manufacturing F5 (HG-136): ``location`` is where the product really entered stock when that is not the run's own
    location (the quality hold place); the lot is written there and follows the release (``quality``)."""

    if not batches_enabled():
        return None
    days = run.recipe.shelf_life_days
    expiry = lot_expiry or (run.run_date + timedelta(days=days) if days else None)
    lot = Batch.objects.create(item=run.recipe.product, location=location or run.location, batch_no=((lot_no or "").strip() or run.number)[:60], expiry_date=expiry,
                               quantity=run.output_quantity, received_on=run.run_date, production_run=run, created_by=user)
    services._audit(run, user, "record_product_lot", {"lot": lot.batch_no, "expiry": str(expiry or ""), "quantity": str(lot.quantity)}, AuditEventType.CREATE)
    return lot


def after_finish(order, run, user, lot_no="", lot_expiry=None, location=None):
    """An order's finish: the lots its start used now belong to the run too, and the product gets its lot."""

    LotUse.objects.filter(order=order, run__isnull=True).update(run=run)
    return record_product_lot(run, user, lot_no, lot_expiry, location)


# ---- what the run page shows ----

def run_lots(run):
    """(the product's lot or None, [LotUse rows of the run], the ingredient lots that expire before the product's lot)."""

    lot = Batch.objects.filter(production_run=run).first()
    uses = list(LotUse.objects.filter(run=run).select_related("item", "batch", "batch__purchase_line__invoice__supplier").order_by("item__item_name", "pk"))
    early = []
    if lot is not None and lot.expiry_date is not None:
        early = [use for use in uses if use.batch_id and use.batch.expiry_date is not None and use.batch.expiry_date < lot.expiry_date]
    return lot, uses, early


def label_count(run):
    """«اطبع ملصقات التشغيلة»: one label per unit, or per carton when the product has a carton unit (at most MAX_LABELS)."""

    import math

    from units.models import ItemUnit

    product = run.recipe.product
    cartons = [unit for unit in ItemUnit.objects.filter(item=product, active=True)
               if "كرتون" in unit.name_ar or "carton" in (unit.name_en or "").lower()]
    if cartons:
        return max(1, min(math.ceil(run.output_quantity / max(unit.factor for unit in cartons)), MAX_LABELS)), True
    return max(1, min(math.ceil(run.output_quantity), MAX_LABELS)), False

