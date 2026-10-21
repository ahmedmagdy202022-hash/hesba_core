"""T1 (MARKET-PARITY): quotations and sales orders (HG-054).

Nothing here posts, moves stock or touches a balance. A confirmed order is
invoiced on the ordinary sales invoice form (``/sales/new/?order=<pk>``), which
saves an ordinary draft through ``taxes.services.create_sales_draft_with_tax``
and then calls :func:`link_invoice`, so units, serial numbers, the cashbox and
posting all work exactly as on any invoice. What an order still has to invoice
is read from the invoice lines it is linked to, never stored: a cancelled
invoice frees its quantities by itself.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .models import OFFERED, OrderInvoiceLine, SalesOrder, SalesOrderLine, Status

ZERO = Decimal("0")
VIEW, MANAGE = "sales.view_sales_invoices", "sales.create_sales_invoice"
CAPABILITY = "quotations"
#: How long a quotation stays valid when the form leaves it empty.
DEFAULT_VALID_DAYS = 14
#: The ordinary invoice form takes up to 20 lines; a longer order is invoiced in parts.
INVOICE_LINES = 20

#: Where a quotation or an order may go next.
NEXT = {
    Status.DRAFT: (Status.SENT, Status.CONFIRMED, Status.REJECTED, Status.CANCELLED),
    Status.SENT: (Status.CONFIRMED, Status.REJECTED, Status.CANCELLED),
    Status.CONFIRMED: (Status.CLOSED, Status.CANCELLED),
    Status.REJECTED: (Status.DRAFT,),
}

STATUS_LABELS = {
    "ar": {"draft": "مسودة", "sent": "اتبعت للعميل", "confirmed": "أمر بيع مفتوح", "rejected": "العميل رفض", "cancelled": "اتلغى",
           "closed": "اتقفل", "done": "اتفوتر بالكامل", "expired": "انتهت صلاحيته"},
    "en": {"draft": "Draft", "sent": "Sent to the customer", "confirmed": "Open sales order", "rejected": "Rejected", "cancelled": "Cancelled",
           "closed": "Closed", "done": "Fully invoiced", "expired": "Expired"},
}

#: What each move is called on its button.
MOVE_LABELS = {
    "ar": {"sent": "علّم إنه اتبعت", "confirmed": "العميل وافق: حوّله لأمر بيع", "rejected": "العميل رفض", "cancelled": "إلغاء",
           "closed": "اقفل الأمر (الباقي مش هيتفوتر)", "draft": "رجّعه مسودة"},
    "en": {"sent": "Mark as sent", "confirmed": "Customer accepted: make it a sales order", "rejected": "Customer rejected", "cancelled": "Cancel",
           "closed": "Close the order (the rest will not be invoiced)", "draft": "Back to draft"},
}

MESSAGES = {
    "ar": {"customer": "اختار العميل.", "date": "التاريخ مش مظبوط.", "valid": "تاريخ الصلاحية لازم يكون بعد تاريخ العرض.",
           "item": "اختار الصنف أو الخدمة.", "quantity": "الكمية لازم تكون أكبر من صفر.", "price": "السعر لازم يكون رقم صفر أو أكبر.",
           "discount": "الخصم نسبة من 0 لـ 100.", "locked": "العرض ده مش مسودة ولا مبعوت؛ مينفعش يتعدّل.",
           "move": "مينفعش تنقل «{number}» من «{from_}» لـ «{to}».", "empty": "ضيف صنف واحد على الأقل قبل ما تحوّله لأمر بيع.",
           "invoiced": "الأمر ده عليه فواتير؛ اقفله بدل ما تلغيه.", "not_order": "ده لسه عرض سعر؛ العميل لازم يوافق الأول.",
           "nothing_left": "الأمر ده اتفوتر بالكامل.", "line_invoiced": "البند ده عليه فواتير؛ مينفعش يتشال.",
           "note_invoice": "أمر بيع {number}", "no_capability": "عروض الأسعار مقفولة في الإعدادات."},
    "en": {"customer": "Choose the customer.", "date": "The date is not valid.", "valid": "“Valid until” must be after the quotation date.",
           "item": "Choose the item or service.", "quantity": "The quantity must be more than zero.", "price": "The price must be zero or more.",
           "discount": "The discount is a percentage from 0 to 100.", "locked": "This quotation is no longer a draft or sent, so it cannot change.",
           "move": "“{number}” cannot go from “{from_}” to “{to}”.", "empty": "Add at least one line before making it a sales order.",
           "invoiced": "This order has invoices; close it instead of cancelling it.", "not_order": "This is still a quotation; the customer has to accept it first.",
           "nothing_left": "This order is fully invoiced.", "line_invoiced": "This line has invoices and cannot be removed.",
           "note_invoice": "Sales order {number}", "no_capability": "Quotations are switched off in Settings."},
}


# ---- who may do what ----

def enabled():
    from settings_core.capabilities import capability_enabled

    return capability_enabled(CAPABILITY)


def can_view(user):
    return user_has_permission(user, VIEW)


def can_manage(user):
    return user_has_permission(user, MANAGE)


def scoped(queryset=None):
    return entity_scope.scope((queryset if queryset is not None else SalesOrder.objects).all(), "entity")


def _audit(user, action, order, after, event=AuditEventType.UPDATE, before=None):
    AuditLog.objects.create(event_type=event, actor=user, module="orders", action=action, object_type="orders.SalesOrder",
                            object_id=str(order.pk), before_data=before or {}, after_data={"number": order.display_number, **after})


def _require(user, words):
    if not enabled():
        raise ValidationError(words["no_capability"])
    if not can_manage(user):
        raise PermissionDenied("Quotations need sales.create_sales_invoice.")


def _locked(order, words, *, offered=True):
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if offered and order.status not in OFFERED:
        raise ValidationError(words["locked"])
    return order


# ---- reading numbers ----

def _decimal(raw, words, key, default=None):
    if raw in (None, "") and default is not None:
        return default
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError(words[key])
    if not value.is_finite() or value >= Decimal("10000000000"):
        raise ValidationError(words[key])
    return value


def _date(raw, words, default=None):
    if raw in (None, ""):
        return default
    if hasattr(raw, "year"):
        return raw
    from datetime import date

    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise ValidationError(words["date"])


# ---- the quotation ----

def _header(data, words, *, quote_date):
    valid_until = _date(data.get("valid_until"), words, quote_date + timedelta(days=DEFAULT_VALID_DAYS))
    if valid_until < quote_date:
        raise ValidationError(words["valid"])
    salesperson = data.get("salesperson") if hasattr(data.get("salesperson"), "pk") else None
    return {"valid_until": valid_until, "salesperson": salesperson, "notes": (data.get("notes") or "").strip()[:255],
            "terms": (data.get("terms") or "").strip()[:4000]}


@transaction.atomic
def create_quote(data, user, lang="ar"):
    """A new quotation for a customer. Returns the SalesOrder, still a draft."""

    from config.numbering import next_in_series
    from master_data.models import Customer

    words = MESSAGES[lang]
    _require(user, words)
    customer = data.get("customer")
    if not isinstance(customer, Customer) or not customer.active:
        raise ValidationError(words["customer"])
    quote_date = _date(data.get("quote_date"), words, timezone.localdate())
    values = {"customer": customer, "quote_date": quote_date, **_header(data, words, quote_date=quote_date),
              "entity": entity_scope.entity() or None, "created_by": user}
    for attempt in range(5):
        number = next_in_series(SalesOrder, "number", "QT-")
        try:
            with transaction.atomic():
                order = SalesOrder.objects.create(number=number, **values)
            break
        except IntegrityError:
            if attempt == 4 or not SalesOrder.objects.filter(number=number).exists():
                raise
    _audit(user, "create_quote", order, {"customer": customer.customer_code}, AuditEventType.CREATE)
    return order


@transaction.atomic
def update_header(order, data, user, lang="ar"):
    words = MESSAGES[lang]
    _require(user, words)
    order = _locked(order, words)
    quote_date = _date(data.get("quote_date"), words, order.quote_date)
    values = _header(data, words, quote_date=quote_date)
    before = {"valid_until": str(order.valid_until or ""), "notes": order.notes}
    order.quote_date = quote_date
    for key, value in values.items():
        setattr(order, key, value)
    order.save(update_fields=["quote_date", *values])
    _audit(user, "update_quote", order, {"valid_until": str(order.valid_until or ""), "notes": order.notes}, before=before)
    return order


@transaction.atomic
def add_line(order, item, user, *, quantity="1", unit_price=None, discount_percent="0", description="", lang="ar"):
    from pricing.services import price_for

    words = MESSAGES[lang]
    _require(user, words)
    order = _locked(order, words)
    if item is None or not item.active:
        raise ValidationError(words["item"])
    quantity = _decimal(quantity, words, "quantity", Decimal("1")).quantize(Decimal("0.001"))
    if quantity <= 0:
        raise ValidationError(words["quantity"])
    price = _decimal(unit_price, words, "price", price_for(item, order.customer))
    if price < 0:
        raise ValidationError(words["price"])
    percent = _decimal(discount_percent, words, "discount", ZERO)
    if percent < 0 or percent > 100:
        raise ValidationError(words["discount"])
    line = SalesOrderLine.objects.create(order=order, item=item, description=(description or "").strip()[:255], quantity=quantity,
                                         unit_price=money_round(price), discount_percent=percent.quantize(Decimal("0.01")))
    _audit(user, "add_line", order, {"item": item.item_code, "quantity": str(quantity), "price": str(line.unit_price),
                                     "discount_percent": str(line.discount_percent)})
    return line


@transaction.atomic
def remove_line(line, user, lang="ar"):
    words = MESSAGES[lang]
    _require(user, words)
    order = _locked(line.order, words)
    if line.invoiced.exists():
        raise ValidationError(words["line_invoiced"])
    _audit(user, "remove_line", order, {}, before={"item": line.item.item_code, "quantity": str(line.quantity), "price": str(line.unit_price)})
    line.delete()


@transaction.atomic
def move(order, to, user, lang="ar"):
    """Send, accept (it becomes a sales order with its SO- number), reject, cancel or close."""

    from config.numbering import next_in_series

    words = MESSAGES[lang]
    _require(user, words)
    order = _locked(order, words, offered=False)
    labels = STATUS_LABELS[lang]
    if to not in NEXT.get(order.status, ()):
        raise ValidationError(words["move"].format(number=order.display_number, from_=labels.get(order.status, order.status), to=labels.get(to, to)))
    if to == Status.CONFIRMED and not order.lines.exists():
        raise ValidationError(words["empty"])
    if to == Status.CANCELLED and order.status == Status.CONFIRMED and _linked(order).exists():
        raise ValidationError(words["invoiced"])
    before = order.status
    order.status = to
    fields = ["status"]
    if to == Status.CONFIRMED:
        order.confirmed_at = timezone.now()
        fields.append("confirmed_at")
        if not order.order_number:
            for attempt in range(5):
                number = next_in_series(SalesOrder, "order_number", "SO-")
                try:
                    with transaction.atomic():
                        SalesOrder.objects.filter(pk=order.pk).update(order_number=number)
                    order.order_number = number
                    break
                except IntegrityError:
                    if attempt == 4 or not SalesOrder.objects.filter(order_number=number).exists():
                        raise
    order.save(update_fields=fields)
    _audit(user, "move", order, {"status": to}, before={"status": before})
    return order


# ---- the figures ----

def _linked(order):
    """The live links of ``order``: those on invoices that are not cancelled."""

    return OrderInvoiceLine.objects.filter(order_line__order=order).exclude(sales_line__invoice__status="cancelled")


def quantities(order):
    """Per line: ordered, invoiced (posted), on a draft invoice, and what is left to invoice."""

    lines = list(order.lines.select_related("item"))
    posted, drafted = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for link in _linked(order).select_related("sales_line__invoice"):
        bucket = posted if link.sales_line.invoice.status == "posted" else drafted
        bucket[link.order_line_id] += link.quantity
    rows = []
    for line in lines:
        left = line.quantity - posted[line.pk] - drafted[line.pk]
        rows.append({"line": line, "ordered": line.quantity, "invoiced": posted[line.pk], "drafted": drafted[line.pk],
                     "left": left if left > 0 else ZERO})
    return rows


def _tax(rows):
    from taxes.services import compute_lines, vat_enabled

    return sum((tax for *_, tax in compute_lines(rows)), ZERO) if rows and vat_enabled() else ZERO


def totals(order):
    """Gross, discount, VAT and the total of the whole quotation, the way an invoice for all of it would add up."""

    lines = list(order.lines.select_related("item"))
    gross = sum((line.gross for line in lines), ZERO)
    discount = sum((line.discount for line in lines), ZERO)
    rows = [{"item": line.item, "quantity": line.quantity, "unit_sale_price": line.unit_price, "line_discount_amount": line.discount} for line in lines]
    tax = money_round(_tax(rows))
    return {"lines": lines, "gross": gross, "discount": discount, "net": gross - discount, "tax": tax, "total": gross - discount + tax}


def state(order, today=None, rows=None):
    """The one word the lists show: the status, or "fully invoiced" / "expired" when that says more."""

    if order.status == Status.CONFIRMED:
        rows = rows if rows is not None else quantities(order)
        if rows and all(row["left"] <= 0 and row["drafted"] <= 0 for row in rows):
            return "done"
    if order.is_expired(today):
        return "expired"
    return order.status


def invoices(order):
    """The ordinary sales invoices made from ``order``, cancelled ones included (they show as cancelled)."""

    from sales.models import SalesInvoice

    ids = OrderInvoiceLine.objects.filter(order_line__order=order).values_list("sales_line__invoice_id", flat=True)
    return SalesInvoice.objects.filter(pk__in=ids).order_by("invoice_date", "pk")


# ---- invoicing an order on the ordinary invoice form ----

def order_for_invoice(user, raw_pk):
    """The confirmed order ``/sales/new/?order=`` points at, or None (no capability, not visible, not an order)."""

    if not raw_pk or not str(raw_pk).isdigit() or not enabled() or not can_manage(user):
        return None
    return scoped().filter(pk=int(raw_pk), status=Status.CONFIRMED).select_related("customer", "salesperson").first()


def invoice_initial(order, lang="ar"):
    """(header initial, line initials) for the ordinary invoice form: what is left on the order, at the order's prices."""

    rows = [row for row in quantities(order) if row["left"] > 0][:INVOICE_LINES]
    lines = [{"item": row["line"].item, "description": row["line"].description, "quantity": row["left"],
              "unit_sale_price": row["line"].unit_price, "line_discount_amount": row["line"].discount_for(row["left"])} for row in rows]
    header = {"customer": order.customer, "notes": MESSAGES[lang]["note_invoice"].format(number=order.display_number)}
    if order.salesperson_id:
        header["salesperson"] = order.salesperson
    return header, lines


@transaction.atomic
def link_invoice(order, invoice, user):
    """Remember which lines of the new invoice take which order quantities.

    Each invoice line goes to the order's lines of the same item, oldest first,
    up to what each still has left; anything beyond that was sold on top of the
    order and is not linked. Called inside the transaction that saved the draft."""

    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != Status.CONFIRMED:
        return []
    left = {row["line"].pk: row["left"] for row in quantities(order)}
    by_item = defaultdict(list)
    for line in order.lines.all():
        by_item[line.item_id].append(line)
    links = []
    for sales_line in invoice.lines.order_by("line_number"):
        want = sales_line.quantity
        for order_line in by_item.get(sales_line.item_id, ()):
            if want <= 0:
                break
            take = min(want, left.get(order_line.pk, ZERO))
            if take <= 0:
                continue
            links.append(OrderInvoiceLine.objects.create(order_line=order_line, sales_line=sales_line, quantity=take))
            left[order_line.pk] -= take
            want -= take
    _audit(user, "link_invoice", order, {"invoice": invoice.invoice_number, "lines": len(links)})
    return links


# ---- the list ----

def counts(user=None):
    rows = scoped()
    return {"offered": rows.filter(status__in=OFFERED).count(), "open": rows.filter(status=Status.CONFIRMED).count()}
