"""T2 (MARKET-PARITY): purchase orders received on ordinary purchase invoices (HG-054).

Nothing here posts, moves stock or touches a balance. A confirmed purchase
order is received on the ordinary purchase invoice form
(``/purchases/new/?order=<pk>``), which saves an ordinary draft through
``taxes.services.create_purchase_draft_with_tax`` and then calls
:func:`link_receipt`, so batches, expiry dates, serials, units and posting
work as on any purchase. What is still to come is read from the links, never
stored: a cancelled purchase invoice gives its quantities back by itself.
"""

from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .models import OrderReceiptLine, PurchaseOrder, PurchaseOrderLine, PurchaseStatus as Status
from .services import ZERO, _date, _decimal

VIEW, MANAGE = "purchases.view_purchase_invoices", "purchases.create_purchase_invoice"
INVOICE_LINES = 20

NEXT = {
    Status.DRAFT: (Status.SENT, Status.CONFIRMED, Status.CANCELLED),
    Status.SENT: (Status.CONFIRMED, Status.CANCELLED),
    Status.CONFIRMED: (Status.CLOSED, Status.CANCELLED),
}

STATUS_LABELS = {
    "ar": {"draft": "مسودة", "sent": "اتبعت للمورد", "confirmed": "المورد أكّد: مستني الاستلام", "cancelled": "اتلغى", "closed": "اتقفل",
           "done": "اتستلم بالكامل", "late": "اتأخر عن ميعاده"},
    "en": {"draft": "Draft", "sent": "Sent to the supplier", "confirmed": "Confirmed: waiting for delivery", "cancelled": "Cancelled", "closed": "Closed",
           "done": "Fully received", "late": "Late"},
}

MOVE_LABELS = {
    "ar": {"sent": "علّم إنه اتبعت للمورد", "confirmed": "المورد أكّد الطلب", "cancelled": "إلغاء", "closed": "اقفل الأمر (الباقي مش جاي)"},
    "en": {"sent": "Mark as sent to the supplier", "confirmed": "The supplier confirmed", "cancelled": "Cancel", "closed": "Close (the rest is not coming)"},
}

MESSAGES = {
    "ar": {"supplier": "اختار المورد.", "date": "التاريخ مش مظبوط.", "expected": "ميعاد الاستلام لازم يكون بعد تاريخ الأمر.",
           "item": "اختار الصنف.", "quantity": "الكمية لازم تكون أكبر من صفر.", "price": "السعر لازم يكون رقم صفر أو أكبر.",
           "discount": "الخصم نسبة من 0 لـ 100.", "locked": "الأمر ده اتأكد أو اتقفل؛ مينفعش يتعدّل.",
           "move": "مينفعش تنقل «{number}» من «{from_}» لـ «{to}».", "empty": "ضيف صنف واحد على الأقل قبل ما المورد يأكّد.",
           "received": "الأمر ده اتستلم منه حاجة؛ اقفله بدل ما تلغيه.", "same_item": "«{item}» موجود في الأمر؛ شيله وضيفه تاني لو هتغيّر الكمية.",
           "line_received": "البند ده اتستلم منه؛ مينفعش يتشال.", "note_invoice": "أمر شراء {number}",
           "other_supplier": "فاتورة الشراء دي لمورد تاني غير مورد الأمر {number}؛ رجّع المورد أو اعمل الفاتورة من غير الأمر."},
    "en": {"supplier": "Choose the supplier.", "date": "The date is not valid.", "expected": "The delivery date must be after the order date.",
           "item": "Choose the item.", "quantity": "The quantity must be more than zero.", "price": "The price must be zero or more.",
           "discount": "The discount is a percentage from 0 to 100.", "locked": "This order is confirmed or closed and cannot change.",
           "move": "“{number}” cannot go from “{from_}” to “{to}”.", "empty": "Add at least one line before the supplier confirms.",
           "received": "Goods were received on this order; close it instead of cancelling it.",
           "same_item": "“{item}” is already on the order; remove it and add it again to change the quantity.",
           "line_received": "Goods were received on this line, so it cannot be removed.", "note_invoice": "Purchase order {number}",
           "other_supplier": "This purchase invoice is for another supplier than order {number}; put the supplier back or make it without the order."},
}


def can_view(user):
    return user_has_permission(user, VIEW)


def can_manage(user):
    return user_has_permission(user, MANAGE)


def enabled():
    from settings_core.setup_services import enabled_modules

    return "purchases" in enabled_modules()


def scoped(queryset=None):
    return entity_scope.scope((queryset if queryset is not None else PurchaseOrder.objects).all(), "entity")


def _audit(user, action, order, after, event=AuditEventType.UPDATE, before=None):
    AuditLog.objects.create(event_type=event, actor=user, module="orders", action=action, object_type="orders.PurchaseOrder",
                            object_id=str(order.pk), before_data=before or {}, after_data={"number": order.number, **after})


def _require(user):
    if not can_manage(user):
        raise PermissionDenied("Purchase orders need purchases.create_purchase_invoice.")


def _locked(order, words, *, open_only=True):
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if open_only and not order.is_open:
        raise ValidationError(words["locked"])
    return order


@transaction.atomic
def create_order(data, user, lang="ar"):
    from config.numbering import next_in_series
    from master_data.models import Supplier

    words = MESSAGES[lang]
    _require(user)
    supplier = data.get("supplier")
    if not isinstance(supplier, Supplier) or not supplier.active:
        raise ValidationError(words["supplier"])
    order_date = _date(data.get("order_date"), words, timezone.localdate())
    expected = _date(data.get("expected_on"), words, order_date + timedelta(days=7))
    if expected < order_date:
        raise ValidationError(words["expected"])
    values = {"supplier": supplier, "order_date": order_date, "expected_on": expected, "notes": (data.get("notes") or "").strip()[:255],
              "terms": (data.get("terms") or "").strip()[:4000], "entity": entity_scope.entity() or None, "created_by": user}
    for attempt in range(5):
        number = next_in_series(PurchaseOrder, "number", "PO-")
        try:
            with transaction.atomic():
                order = PurchaseOrder.objects.create(number=number, **values)
            break
        except IntegrityError:
            if attempt == 4 or not PurchaseOrder.objects.filter(number=number).exists():
                raise
    _audit(user, "create_purchase_order", order, {"supplier": supplier.supplier_code}, AuditEventType.CREATE)
    return order


@transaction.atomic
def update_header(order, data, user, lang="ar"):
    words = MESSAGES[lang]
    _require(user)
    order = _locked(order, words)
    order.order_date = _date(data.get("order_date"), words, order.order_date)
    order.expected_on = _date(data.get("expected_on"), words, order.expected_on)
    if order.expected_on and order.expected_on < order.order_date:
        raise ValidationError(words["expected"])
    order.notes = (data.get("notes") or "").strip()[:255]
    order.terms = (data.get("terms") or "").strip()[:4000]
    order.save(update_fields=["order_date", "expected_on", "notes", "terms"])
    _audit(user, "update_purchase_order", order, {"expected_on": str(order.expected_on or "")})
    return order


def _default_price(item):
    from inventory.services import get_item_authoritative_average_cost

    price = item.default_purchase_price or ZERO
    if not price:
        try:
            price = get_item_authoritative_average_cost(item) or ZERO
        except Exception:  # an item with no movements yet: no cost to suggest
            price = ZERO
    return money_round(price)


@transaction.atomic
def add_line(order, item, user, *, quantity="1", unit_price=None, discount_percent="0", description="", lang="ar"):
    from decimal import Decimal

    words = MESSAGES[lang]
    _require(user)
    order = _locked(order, words)
    if item is None or not item.active:
        raise ValidationError(words["item"])
    if order.lines.filter(item=item).exists():
        raise ValidationError(words["same_item"].format(item=item.item_name))
    quantity = _decimal(quantity, words, "quantity", Decimal("1")).quantize(Decimal("0.001"))
    if quantity <= 0:
        raise ValidationError(words["quantity"])
    price = _decimal(unit_price, words, "price", _default_price(item))
    if price < 0:
        raise ValidationError(words["price"])
    percent = _decimal(discount_percent, words, "discount", ZERO)
    if percent < 0 or percent > 100:
        raise ValidationError(words["discount"])
    line = PurchaseOrderLine.objects.create(order=order, item=item, description=(description or "").strip()[:255], quantity=quantity,
                                            unit_price=money_round(price), discount_percent=percent.quantize(Decimal("0.01")))
    _audit(user, "add_line", order, {"item": item.item_code, "quantity": str(quantity), "price": str(line.unit_price)})
    return line


@transaction.atomic
def remove_line(line, user, lang="ar"):
    words = MESSAGES[lang]
    _require(user)
    order = _locked(line.order, words)
    if line.received.exists():
        raise ValidationError(words["line_received"])
    _audit(user, "remove_line", order, {}, before={"item": line.item.item_code, "quantity": str(line.quantity)})
    line.delete()


def _linked(order):
    return OrderReceiptLine.objects.filter(order_line__order=order).exclude(purchase_line__invoice__status="cancelled")


@transaction.atomic
def move(order, to, user, lang="ar"):
    words = MESSAGES[lang]
    _require(user)
    order = _locked(order, words, open_only=False)
    labels = STATUS_LABELS[lang]
    if to not in NEXT.get(order.status, ()):
        raise ValidationError(words["move"].format(number=order.number, from_=labels.get(order.status, order.status), to=labels.get(to, to)))
    if to == Status.CONFIRMED and not order.lines.exists():
        raise ValidationError(words["empty"])
    if to == Status.CANCELLED and _linked(order).exists():
        raise ValidationError(words["received"])
    before = order.status
    order.status = to
    fields = ["status"]
    if to == Status.CONFIRMED:
        order.confirmed_at = timezone.now()
        fields.append("confirmed_at")
    order.save(update_fields=fields)
    _audit(user, "move", order, {"status": to}, before={"status": before})
    return order


def quantities(order):
    lines = list(order.lines.select_related("item"))
    posted, drafted = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for link in _linked(order).select_related("purchase_line__invoice"):
        bucket = posted if link.purchase_line.invoice.status == "posted" else drafted
        bucket[link.order_line_id] += link.quantity
    rows = []
    for line in lines:
        left = line.quantity - posted[line.pk] - drafted[line.pk]
        rows.append({"line": line, "ordered": line.quantity, "received": posted[line.pk], "drafted": drafted[line.pk], "left": left if left > 0 else ZERO})
    return rows


def totals(order):
    from taxes.services import compute_lines, vat_enabled

    lines = list(order.lines.select_related("item"))
    gross = sum((line.gross for line in lines), ZERO)
    discount = sum((line.discount for line in lines), ZERO)
    rows = [{"item": line.item, "quantity": line.quantity, "unit_purchase_price": line.unit_price, "line_discount_amount": line.discount} for line in lines]
    tax = money_round(sum((tax for *_, tax in compute_lines(rows, price_key="unit_purchase_price")), ZERO)) if rows and vat_enabled() else ZERO
    return {"lines": lines, "gross": gross, "discount": discount, "net": gross - discount, "tax": tax, "total": gross - discount + tax}


def state(order, today=None, rows=None):
    if order.status == Status.CONFIRMED:
        rows = rows if rows is not None else quantities(order)
        if rows and all(row["left"] <= 0 and row["drafted"] <= 0 for row in rows):
            return "done"
        if order.is_late(today):
            return "late"
    return order.status


def receipts(order):
    from purchases.models import PurchaseInvoice

    ids = OrderReceiptLine.objects.filter(order_line__order=order).values_list("purchase_line__invoice_id", flat=True)
    return PurchaseInvoice.objects.filter(pk__in=ids).order_by("invoice_date", "pk")


def order_for_receipt(user, raw_pk):
    if not raw_pk or not str(raw_pk).isdigit() or not can_manage(user):
        return None
    return scoped().filter(pk=int(raw_pk), status=Status.CONFIRMED).select_related("supplier").first()


def receipt_initial(order, lang="ar"):
    rows = [row for row in quantities(order) if row["left"] > 0][:INVOICE_LINES]
    lines = [{"item": row["line"].item, "description": row["line"].description, "quantity": row["left"], "unit_purchase_price": row["line"].unit_price,
              "line_discount_amount": row["line"].discount_for(row["left"], row["received"] + row["drafted"])} for row in rows]
    return {"supplier": order.supplier, "notes": MESSAGES[lang]["note_invoice"].format(number=order.number)}, lines


@transaction.atomic
def link_receipt(order, invoice, user, lang="ar"):
    """Remember which lines of the new purchase invoice receive which order quantities (one order line per item)."""

    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != Status.CONFIRMED:
        return []
    if invoice.supplier_id != order.supplier_id:
        raise ValidationError(MESSAGES[lang]["other_supplier"].format(number=order.number))
    left = {row["line"].pk: row["left"] for row in quantities(order)}
    by_item = {line.item_id: line for line in order.lines.all()}
    links = []
    for purchase_line in invoice.lines.order_by("line_number"):
        order_line = by_item.get(purchase_line.item_id)
        take = min(purchase_line.quantity, left.get(order_line.pk, ZERO)) if order_line else ZERO
        if take <= 0:
            continue
        links.append(OrderReceiptLine.objects.create(order_line=order_line, purchase_line=purchase_line, quantity=take))
        left[order_line.pk] -= take
    _audit(user, "link_receipt", order, {"invoice": invoice.invoice_number, "lines": len(links)})
    return links


def counts():
    rows = scoped()
    today = timezone.localdate()
    return {"open": rows.filter(status__in=(Status.DRAFT, Status.SENT)).count(), "waiting": rows.filter(status=Status.CONFIRMED).count(),
            "late": rows.filter(status=Status.CONFIRMED, expected_on__lt=today).count()}
