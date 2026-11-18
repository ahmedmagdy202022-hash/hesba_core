"""Manufacturing F3 (HG-131): trace a lot, from ingredient to customer and back. Reads only; nothing here writes.

* **Backward** from a product lot: its run, the ingredient lots the run used (``LotUse``, written at the issue) and the
  purchase invoice and supplier each came on.
* **Forward** from a lot: the posted runs that used it as an ingredient, the product lots they made, and the sales of that
  product from that location between the run and the moment the lot was used up; a lot made by a run also shows its own sales.

Sales do not record a lot (HG-019), so the forward half cannot say which customer got which lot. It says who bought
that product from that place while the lot was the one the batch screens count as being sold: the batches' own rule
hands the on-hand quantity to the latest-expiring lots first, so the earliest-expiring lot is deemed used first. The
screen labels it «حسب أقرب انتهاء» / "by earliest expiry" and nothing more is claimed.

The moment a product lot is used up is found by replaying the product's stock movements at the location in the order
they were written (as ``services._left_since`` does), not by date: a lot is used up when the on-hand quantity no longer
exceeds the quantity of the lots that are handed stock before it. Only lots received by then count (a lot that arrives
later cannot have held stock earlier).
"""

from decimal import Decimal

from django.db.models import Sum

from batches.models import Batch
from batches.services import FAR_FUTURE, counted_batches
from inventory.models import StockMovement, StockMovementType
from inventory.services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES

from .models import LotUse, OrderStatus, RunStatus

ZERO = Decimal("0")


def _rank(batch):
    """The order batches are handed stock in is the reverse of this (``batch_positions``)."""

    return (batch.expiry_date or FAR_FUTURE, batch.received_on, batch.pk)


def _arrived(batch):
    """The stock movement a lot arrived on at the place it is in: its run's output or its purchase's receipt; None for a
    lot registered by hand.

    Manufacturing F5 (HG-136): a lot that entered the quality hold place and was released is in another place than the
    one it was made or received in; there it arrived on the release's transfer in, so nothing sold from that place
    before the release is read as this lot's."""

    if batch.production_run_id:
        row = batch.production_run.output_operation.movements.filter(reversal_of__isnull=True).order_by("pk").first()
        owner = {"stock_operation__quality_release__order__run_id": batch.production_run_id}
    elif batch.purchase_line_id:
        row = StockMovement.objects.filter(purchase_line_id=batch.purchase_line_id, reversal_of__isnull=True).order_by("pk").first()
        owner = {"stock_operation__quality_release__purchase_line_id": batch.purchase_line_id}
    else:
        return None
    if row is not None and row.location_id != batch.location_id:
        released = (StockMovement.objects.filter(location_id=batch.location_id, movement_type=StockMovementType.TRANSFER_IN, reversal_of__isnull=True,
                                                 stock_operation__status="posted", **owner).order_by("pk").first())
        return released or row
    return row


def _arrival(batch):
    """The pk of the stock movement a lot arrived on (see ``_arrived``), or None for one registered by hand."""

    row = _arrived(batch)
    return row.pk if row is not None else None


def source_of(batch):
    """Where a lot came from: ("purchase", invoice), ("run", run), or ("manual", None)."""

    if batch.purchase_line_id:
        return "purchase", batch.purchase_line.invoice
    if batch.production_run_id:
        return "run", batch.production_run
    return "manual", None


def backward(lot):
    """The ingredient lots behind a product lot: [{"item", "batch", "quantity", "kind", "source"}]. Empty for any other lot."""

    run = lot.production_run
    if run is None:
        return []
    uses = (LotUse.objects.filter(run=run)
            .select_related("item", "batch", "batch__purchase_line__invoice__supplier", "batch__production_run")
            .order_by("item__item_name", "pk"))
    rows = []
    for use in uses:
        kind, source = source_of(use.batch) if use.batch_id else ("none", None)
        rows.append({"item": use.item, "batch": use.batch, "quantity": use.quantity, "kind": kind, "source": source})
    return rows


def purchase_of(lot):
    """The purchase invoice a bought lot came on (for the trace of an ingredient lot itself), or None."""

    return lot.purchase_line.invoice if lot.purchase_line_id else None


def sales_after(lot):
    """Sales of the lot's product from its location from the run until the lot is used up, by earliest expiry.

    Returns {"sales": [{"invoice", "customer", "quantity"}], "open": the lot is not used up yet, "from": date, "to": date or None}.
    Only for a lot made by a run."""

    from sales.models import SalesInvoice, SalesInvoiceStatus, SalesLine

    run = lot.production_run
    made = _arrived(lot) if run is not None else None  # F5 (HG-136): the release's transfer in, for a lot released from the quality hold
    if made is None:
        return {"sales": [], "open": True, "from": lot.received_on, "to": None}
    movements = list(StockMovement.objects.filter(item_id=lot.item_id, location_id=lot.location_id).order_by("pk")
                     .values_list("pk", "movement_date", "movement_type", "quantity", "sales_invoice_id", "reversal_of_id"))
    # The lots that are handed the stock before this one: later expiry, or the same expiry and received later. Each with
    # the movement it arrived on (its run's output, its purchase's receipt), so that a lot written later the same day is
    # not counted as ahead of a sale made before it existed; a lot registered by hand has none and counts from its date.
    ahead = [(batch, _arrival(batch)) for batch in counted_batches().filter(item_id=lot.item_id, location_id=lot.location_id).select_related("production_run")
             if batch.pk != lot.pk and _rank(batch) > _rank(lot)]
    # Replay every movement. The lot holds stock while what is on hand is more than the lots ahead of it hold; a sale made
    # while it does is listed. A lot used up can hold stock again (a cancelled sale or a return brings units back, and the
    # positions hand them to it), so the replay never stops at the first time it runs out (Codex #438): later sales from the
    # restored stock are listed too, and "open" / "to" describe the last state.
    def ahead_of(pk, moved_on):
        return sum((batch.quantity for batch, arrived in ahead if (arrived < pk if arrived is not None else batch.received_on <= moved_on)), ZERO)

    on_hand, end_date, invoice_ids, holding = ZERO, None, [], True
    for pk, moved_on, kind, quantity, invoice_id, reversal in movements:
        before = on_hand
        if kind in IN_MOVEMENT_TYPES:
            on_hand += quantity
        elif kind in OUT_MOVEMENT_TYPES:
            on_hand -= quantity
        if pk <= made.pk:
            continue
        ahead_now = ahead_of(pk, moved_on)
        if (kind == StockMovementType.SALE_OUT and invoice_id and reversal is None and before > ahead_now
                and invoice_id not in invoice_ids):
            invoice_ids.append(invoice_id)
        was_holding, holding = holding, on_hand > ahead_now
        if was_holding and not holding:
            end_date = moved_on  # the last time it ran out
    invoices = (SalesInvoice.objects.filter(pk__in=invoice_ids, status=SalesInvoiceStatus.POSTED, selling_location_id=lot.location_id)
                .select_related("customer").order_by("invoice_date", "pk"))
    sold = dict(SalesLine.objects.filter(invoice__in=invoices, item_id=lot.item_id).order_by().values_list("invoice_id").annotate(total=Sum("quantity")))
    sales = [{"invoice": invoice, "customer": invoice.customer, "quantity": sold.get(invoice.pk, ZERO)} for invoice in invoices]
    return {"sales": sales, "open": holding, "from": run.run_date, "to": None if holding else end_date}


def forward(lot):
    """[{"label", "run", "order", "product_lot", "sold"}]: where a lot went.

    A lot made by a posted run is first its own path (who bought it). Every lot, a made one included, then lists the
    posted runs that used it, and an unfinished order that did. A semi-finished lot (dough) is both: it shows its own
    sales and the runs that took it as an ingredient, so a recall can go flour lot, dough lot, cake lot, customers."""

    paths, seen = [], set()
    own = lot.production_run
    if own is not None and own.status == RunStatus.POSTED:
        paths.append({"label": own.number, "run": own, "order": None, "product_lot": lot, "sold": sales_after(lot)})
    uses = (LotUse.objects.filter(batch=lot).select_related("run", "run__recipe__product", "order", "order__recipe__product")
            .order_by("pk"))
    for use in uses:
        if use.run_id and use.run.status == RunStatus.POSTED:
            if use.run_id in seen:
                continue
            seen.add(use.run_id)
            product_lot = Batch.objects.filter(production_run=use.run).first()
            paths.append({"label": use.run.number, "run": use.run, "order": use.order, "product_lot": product_lot,
                          "sold": sales_after(product_lot) if product_lot else None})
        elif use.run_id is None and use.order_id and use.order.status == OrderStatus.IN_PROGRESS and ("o", use.order_id) not in seen:
            seen.add(("o", use.order_id))
            paths.append({"label": use.order.number, "run": None, "order": use.order, "product_lot": None, "sold": None})
    return paths
