"""HG-037: warehouse min/max levels, and transfers by request and receipt.

Rules, stated once:

* a level is per (warehouse, item): a minimum, and a maximum (0 = none). An
  item at or below its minimum in a warehouse needs refilling; the refill
  brings it up to the maximum (or the minimum when there is none);
* a request names a source, a destination and lines. Nothing moves while it
  is only requested;
* sending moves each line, through the engine's own ``transfer_stock``, from
  the source to that entity's "goods in transit" location. The stock is in
  neither warehouse while it travels, and the total never changes;
* receiving moves what arrived from transit to the destination, again through
  ``transfer_stock``. Anything sent but not received is written off from
  transit as a shortage (``adjust_stock`` out), which needs
  ``inventory.adjust_stock``;
* cancelling a sent request returns everything from transit to the source
  (``cancel_stock_operation``). A received request cannot be cancelled.

No movement type, cost or report rule changes: every leg is an ordinary
transfer or adjustment the engine already knows.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from master_data.models import Location
from permissions.services import user_has_permission
from settings_core.templatetags.hesba_format import qty as fmt_qty

from .models import LocationStockLevel, TransferRequest, TransferRequestLine, TransferRequestStatus

ZERO = Decimal("0")

MESSAGES = {
    "ar": {
        "places": "اختار المخزن اللي هيبعت والمخزن اللي هيستلم.", "same": "المخزن اللي هيبعت لازم يختلف عن اللي هيستلم.",
        "lines": "ضيف صنف واحد على الأقل بكمية أكبر من صفر.", "item": "«{item}» مش صنف متتبع في المخزون.",
        "twice": "«{item}» متكرر؛ اجمع كميته في سطر واحد.", "qty": "كمية «{item}» لازم تكون رقم أكبر من صفر.",
        "state": "الطلب مش في الحالة دي.", "short": "«{item}» مش كفاية في {place}: مطلوب {need} والموجود {have}.",
        "nothing_sent": "لازم تبعت كمية من صنف واحد على الأقل.", "sent_range": "الكمية المبعوتة من «{item}» لازم بين صفر والمطلوب.",
        "received_range": "الكمية المستلمة من «{item}» لازم بين صفر واللي اتبعت.", "loss_permission": "في كمية ناقصة عن اللي اتبعت؛ تسجيل العجز محتاج صلاحية تسوية المخزون.",
        "reason": "اكتب السبب.", "received": "الطلب اتستلم خلاص ومينفعش يتلغي؛ اعمل تحويل جديد لو محتاج.",
        "level": "الحد الأدنى والأقصى لـ «{item}» لازم أرقام مش سالبة، والأقصى (لو موجود) مش أقل من الأدنى.",
        "transit": "بضاعة في الطريق",
    },
    "en": {
        "places": "Choose the warehouse that sends and the one that receives.", "same": "The sending and receiving warehouses must differ.",
        "lines": "Add at least one item with a quantity above zero.", "item": "“{item}” is not a stock-tracked item.",
        "twice": "“{item}” is listed twice; put its quantity on one line.", "qty": "The quantity of “{item}” must be a number above zero.",
        "state": "The request is not at that step.", "short": "Not enough “{item}” at {place}: {need} asked, {have} there.",
        "nothing_sent": "Send a quantity of at least one item.", "sent_range": "The quantity sent of “{item}” must be between zero and what was asked.",
        "received_range": "The quantity received of “{item}” must be between zero and what was sent.", "loss_permission": "Less arrived than was sent; recording the shortage needs the stock adjustment permission.",
        "reason": "Write the reason.", "received": "The request was received and cannot be cancelled; make a new transfer if needed.",
        "level": "The minimum and maximum of “{item}” must not be negative, and the maximum (if set) not below the minimum.",
        "transit": "Goods in transit",
    },
}


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        return False
    return number.quantize(Decimal("0.001")) if number.is_finite() else False


def _audit(obj, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="inventory", action=action, object_type=f"inventory.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data={}, after_data=after)


def _on_hand(item_ids, location_ids):
    from collections import defaultdict

    from django.db.models import Sum

    from .models import StockMovement
    from .services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES

    held = defaultdict(lambda: ZERO)
    rows = (StockMovement.objects.filter(item_id__in=item_ids, location_id__in=location_ids)
            .values("item_id", "location_id", "movement_type").annotate(total=Sum("quantity")))
    for row in rows:
        sign = 1 if row["movement_type"] in IN_MOVEMENT_TYPES else -1 if row["movement_type"] in OUT_MOVEMENT_TYPES else 0
        held[(row["item_id"], row["location_id"])] += sign * (row["total"] or ZERO)
    return held


# ---- levels ----

@transaction.atomic
def save_levels(location, rows, user, lang="ar"):
    """``rows`` is [(item, min, max)]. Both blank or zero removes the level."""

    words = MESSAGES[lang]
    changed = []
    for item, low, high in rows:
        if item is None:
            continue
        low, high = _number(low), _number(high)
        if low is False or high is False:
            raise ValidationError(words["level"].format(item=item.item_name))
        low, high = low or ZERO, high or ZERO
        if low < 0 or high < 0 or (high and high < low):
            raise ValidationError(words["level"].format(item=item.item_name))
        if not low and not high:
            LocationStockLevel.objects.filter(location=location, item=item).delete()
        else:
            LocationStockLevel.objects.update_or_create(location=location, item=item, defaults={"min_quantity": low, "max_quantity": high, "updated_by": user})
        changed.append([item.item_code, str(low), str(high)])
    if changed:
        AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="inventory", action="set_location_levels",
                                object_type="master_data.Location", object_id=str(location.pk), before_data={}, after_data={"levels": changed})
    return len(changed)


def level_rows(location, candidates):
    """Every level of ``location`` with what it holds, the refill it needs,
    and the warehouse (among ``candidates``) with the most to spare."""

    levels = list(LocationStockLevel.objects.filter(location=location).select_related("item"))
    if not levels:
        return []
    candidates = [place for place in candidates if place.pk != location.pk]
    item_ids = [level.item_id for level in levels]
    held = _on_hand(item_ids, [location.pk] + [place.pk for place in candidates])
    spare_levels = {(lv.location_id, lv.item_id): lv.min_quantity for lv in
                    LocationStockLevel.objects.filter(item_id__in=item_ids, location__in=candidates)}
    rows = []
    for level in levels:
        have = held[(level.item_id, location.pk)]
        target = level.max_quantity or level.min_quantity
        below = level.min_quantity > 0 and have <= level.min_quantity
        refill = max(target - have, ZERO) if below else ZERO
        best, spare = None, ZERO
        for place in candidates:
            extra = held[(level.item_id, place.pk)] - spare_levels.get((place.pk, level.item_id), ZERO)
            if extra > spare:
                best, spare = place, extra
        rows.append({"level": level, "item": level.item, "have": have, "below": below,
                     "above": bool(level.max_quantity and have > level.max_quantity), "refill": refill,
                     "source": best, "spare": spare, "can_fill": min(refill, spare)})
    rows.sort(key=lambda row: (not row["below"], row["item"].item_code))
    return rows


def below_counts(locations):
    """{location_id: items at or below their minimum there}."""

    locations = list(locations)
    levels = list(LocationStockLevel.objects.filter(location__in=locations, min_quantity__gt=0))
    held = _on_hand({lv.item_id for lv in levels}, [place.pk for place in locations])
    counts = {}
    for level in levels:
        if held[(level.item_id, level.location_id)] <= level.min_quantity:
            counts[level.location_id] = counts.get(level.location_id, 0) + 1
    return counts


# ---- requests ----

def transit_location(entity_id, lang="ar"):
    """The entity's "goods in transit" place, made the first time it is needed."""

    code = f"TRANSIT-{entity_id or 0}"
    place, _ = Location.objects.get_or_create(location_code=code, defaults={
        "name_ar": MESSAGES["ar"]["transit"], "name_en": MESSAGES["en"]["transit"], "entity_id": entity_id,
        "is_default": False, "is_receiving_location": False, "is_selling_location": False,
        "description": "HG-037: stock sent by a transfer request and not yet received."})
    return place


def _next_number(today):
    prefix = f"TR-{today:%Y%m%d}-"
    number = TransferRequest.objects.filter(number__startswith=prefix).count() + 1
    while TransferRequest.objects.filter(number=f"{prefix}{number:03d}").exists():
        number += 1
    return f"{prefix}{number:03d}"


@transaction.atomic
def create_request(source, destination, lines, user, note="", lang="ar"):
    words = MESSAGES[lang]
    if source is None or destination is None:
        raise ValidationError(words["places"])
    if source.pk == destination.pk:
        raise ValidationError(words["same"])
    clean, seen = [], set()
    for item, quantity in lines:
        if item is None:
            continue
        if not item.is_stock_tracked:
            raise ValidationError(words["item"].format(item=item.item_name))
        if item.pk in seen:
            raise ValidationError(words["twice"].format(item=item.item_name))
        quantity = _number(quantity)
        if not quantity or quantity <= 0:
            raise ValidationError(words["qty"].format(item=item.item_name))
        seen.add(item.pk)
        clean.append((item, quantity))
    if not clean:
        raise ValidationError(words["lines"])
    request = TransferRequest.objects.create(number=_next_number(timezone.localdate()), source=source, destination=destination,
                                             note=(note or "").strip()[:255], requested_by=user)
    TransferRequestLine.objects.bulk_create([TransferRequestLine(request=request, item=item, requested_quantity=qty) for item, qty in clean])
    _audit(request, user, "request_transfer", {"number": request.number, "from": source.location_code, "to": destination.location_code,
                                                "lines": [[item.item_code, str(qty)] for item, qty in clean]}, AuditEventType.CREATE)
    return request


def _locked(request):
    return TransferRequest.objects.select_for_update().select_related("source", "destination").get(pk=request.pk)


@transaction.atomic
def send(request, user, quantities=None, lang="ar"):
    """Send what the source has: ``quantities`` is {line pk: quantity}, default the asked quantity."""

    from .services import get_item_location_stock_quantity, lock_items_for_stock_check, transfer_stock

    words = MESSAGES[lang]
    request = _locked(request)
    if request.status != TransferRequestStatus.REQUESTED:
        raise ValidationError(words["state"])
    quantities = quantities or {}
    lines = list(request.lines.select_related("item").order_by("item_id"))
    lock_items_for_stock_check([line.item_id for line in lines])
    plan = []
    for line in lines:
        raw = quantities.get(line.pk, quantities.get(str(line.pk)))
        quantity = line.requested_quantity if raw in (None, "") else _number(raw)
        if quantity is False or quantity is None or quantity < 0 or quantity > line.requested_quantity:
            raise ValidationError(words["sent_range"].format(item=line.item.item_name))
        if quantity:
            have = get_item_location_stock_quantity(line.item, request.source)
            if have < quantity:
                raise ValidationError(words["short"].format(item=line.item.item_name, place=request.source.name_ar if lang == "ar" else (request.source.name_en or request.source.name_ar),
                                                            need=fmt_qty(quantity), have=fmt_qty(have)))
        plan.append((line, quantity))
    if not any(quantity for _, quantity in plan):
        raise ValidationError(words["nothing_sent"])
    transit = transit_location(request.source.entity_id)
    today = timezone.localdate()
    reason = f"طلب تحويل {request.number} / Transfer request {request.number}"
    for index, (line, quantity) in enumerate(plan, start=1):
        line.sent_quantity = quantity
        if quantity:
            line.send_operation = transfer_stock(f"{request.number}-S{index:02d}", today, line.item, request.source, transit, quantity, user, reason)
        line.save(update_fields=["sent_quantity", "send_operation"])
    request.status, request.sent_by, request.sent_at = TransferRequestStatus.SENT, user, timezone.now()
    request.save(update_fields=["status", "sent_by", "sent_at"])
    _audit(request, user, "send_transfer", {"lines": [[line.item.item_code, str(quantity)] for line, quantity in plan]})
    return request


@transaction.atomic
def receive(request, user, quantities=None, lang="ar"):
    """Put what arrived into the destination; write any shortage off from transit."""

    from .models import StockAdjustmentDirection
    from .services import adjust_stock, transfer_stock

    words = MESSAGES[lang]
    request = _locked(request)
    if request.status != TransferRequestStatus.SENT:
        raise ValidationError(words["state"])
    quantities = quantities or {}
    lines = [line for line in request.lines.select_related("item", "send_operation").order_by("pk") if line.sent_quantity > 0]
    plan = []
    for line in lines:
        raw = quantities.get(line.pk, quantities.get(str(line.pk)))
        quantity = line.sent_quantity if raw in (None, "") else _number(raw)
        if quantity is False or quantity is None or quantity < 0 or quantity > line.sent_quantity:
            raise ValidationError(words["received_range"].format(item=line.item.item_name))
        plan.append((line, quantity))
    if any(quantity < line.sent_quantity for line, quantity in plan) and not user_has_permission(user, "inventory.adjust_stock"):
        raise ValidationError(words["loss_permission"])
    today = timezone.localdate()
    reason = f"استلام طلب تحويل {request.number} / Received transfer request {request.number}"
    for index, (line, quantity) in enumerate(plan, start=1):
        transit = line.send_operation.destination_location
        line.received_quantity = quantity
        if quantity:
            line.receive_operation = transfer_stock(f"{request.number}-R{index:02d}", today, line.item, transit, request.destination, quantity, user, reason)
        if quantity < line.sent_quantity:
            line.loss_operation = adjust_stock(f"{request.number}-L{index:02d}", today, line.item, transit, StockAdjustmentDirection.OUT,
                                               line.sent_quantity - quantity, f"عجز تحويل {request.number} / Transfer shortage {request.number}", user)
        line.save(update_fields=["received_quantity", "receive_operation", "loss_operation"])
    request.status, request.received_by, request.received_at = TransferRequestStatus.RECEIVED, user, timezone.now()
    request.save(update_fields=["status", "received_by", "received_at"])
    _audit(request, user, "receive_transfer", {"lines": [[line.item.item_code, str(line.sent_quantity), str(quantity)] for line, quantity in plan]})
    return request


@transaction.atomic
def cancel(request, user, reason, lang="ar"):
    from .services import cancel_stock_operation

    words = MESSAGES[lang]
    request = _locked(request)
    if request.status == TransferRequestStatus.RECEIVED:
        raise ValidationError(words["received"])
    if request.status == TransferRequestStatus.CANCELLED:
        raise ValidationError(words["state"])
    reason = (reason or "").strip()[:255]
    if not reason:
        raise ValidationError(words["reason"])
    for line in request.lines.exclude(send_operation=None):  # back from transit to the source
        cancel_stock_operation(line.send_operation_id, timezone.localdate(), f"{request.number}: {reason}"[:255], user)
    request.status, request.cancelled_by, request.cancelled_at, request.cancel_reason = TransferRequestStatus.CANCELLED, user, timezone.now(), reason
    request.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason"])
    _audit(request, user, "cancel_transfer_request", {"reason": reason})
    return request
