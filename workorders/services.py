"""SVC-001: work orders, from receiving the device to the delivery invoice (HG-041, design A).

Nothing here posts or moves stock by itself. Delivery hands the order's lines
to the ordinary sales engine (``taxes.services.create_sales_draft_with_tax``
and ``sales.services.post_sales_invoice``), so parts leave stock at their
authoritative movement cost and the customer's account is that of any sale.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .models import OPEN, Status, WorkOrder, WorkOrderEvent, WorkOrderLine

ZERO = Decimal("0")
VIEW, MANAGE = "sales.view_sales_invoices", "sales.create_sales_invoice"
#: The service sub-activities that work from a work order (a workshop, a repair counter, field service).
WORK_SUBS = ("maintenance", "general", "other")

#: Where an order may go next. Delivery has its own step (it makes the invoice).
NEXT = {
    Status.RECEIVED: (Status.DIAGNOSED, Status.IN_REPAIR, Status.CANCELLED),
    Status.DIAGNOSED: (Status.QUOTED, Status.APPROVED, Status.IN_REPAIR, Status.CANCELLED),
    Status.QUOTED: (Status.APPROVED, Status.REFUSED, Status.CANCELLED),
    Status.APPROVED: (Status.IN_REPAIR, Status.CANCELLED),
    Status.IN_REPAIR: (Status.READY, Status.CANCELLED),
    Status.REFUSED: (Status.READY,),
    Status.READY: (Status.IN_REPAIR,),
}
#: A technician may move their own order along the bench; quoting, cancelling and delivering stay with the counter.
TECHNICIAN_MOVES = (Status.DIAGNOSED, Status.IN_REPAIR, Status.READY)

STATUS_LABELS = {
    "ar": {"received": "اتستلم", "diagnosed": "اتشخّص", "quoted": "مستني موافقة العميل", "approved": "العميل وافق", "in_repair": "تحت الإصلاح",
           "ready": "جاهز للتسليم", "refused": "العميل رفض العرض", "delivered": "اتسلّم", "cancelled": "اتلغى"},
    "en": {"received": "Received", "diagnosed": "Diagnosed", "quoted": "Waiting for approval", "approved": "Approved", "in_repair": "In repair",
           "ready": "Ready for pickup", "refused": "Quote refused", "delivered": "Delivered", "cancelled": "Cancelled"},
}

MESSAGES = {
    "ar": {"customer": "اختار العميل أو اكتب اسمه.", "device": "اكتب الجهاز أو الخدمة المطلوبة.", "problem": "اكتب العطل أو المطلوب.",
           "date": "التاريخ مش مظبوط.", "days": "أيام الضمان لازم تكون رقم من 0 لـ 3650.", "move": "مينفعش تنقل الأمر من «{from_}» لـ «{to}».",
           "closed": "الأمر ده اتقفل؛ مينفعش يتعدّل.", "item": "اختار الصنف أو الخدمة.", "quantity": "الكمية لازم تكون أكبر من صفر.",
           "price": "السعر لازم يكون رقم صفر أو أكبر.", "not_ready": "الأمر لازم يكون «جاهز للتسليم» الأول.", "setup": "لازم يكون فيه مخزن بيع نشط.",
           "paid": "المدفوع لازم يكون بين صفر وإجمالي الفاتورة.", "cashbox": "اختار الخزنة اللي هيتحصّل فيها.", "own": "تقدر تشتغل على أوامرك بس.",
           "warranty_of": "الضمان ده خلص أو الأمر مش متسلّم.", "note_invoice": "أمر شغل {number}: {device}",
           "no_new_customer": "إضافة عميل جديد محتاجة صلاحية إدارة العملاء؛ اختار عميل موجود.", "reopened": "فاتورة التسليم {invoice} اتلغت؛ الأمر رجع جاهز للتسليم.",
           "serial": "«{item}» بيتباع بالسيريال؛ بيعه من فاتورة البيع عشان يتسجّل السيريال.",
           "refused": "العميل رفض العرض؛ اتشالت بنود العرض. لو فيه رسم كشف، ضيفه كبند."},
    "en": {"customer": "Choose the customer or type their name.", "device": "Enter the device or the service asked for.", "problem": "Enter the fault or what is needed.",
           "date": "The date is not valid.", "days": "Warranty days must be a number from 0 to 3650.", "move": "The order cannot go from “{from_}” to “{to}”.",
           "closed": "This order is closed and cannot change.", "item": "Choose the item or service.", "quantity": "The quantity must be more than zero.",
           "price": "The price must be zero or more.", "not_ready": "The order must be “Ready for pickup” first.", "setup": "An active selling location is needed.",
           "paid": "The amount paid must be between zero and the invoice total.", "cashbox": "Choose the cashbox that takes the payment.",
           "own": "You can work on your own orders only.", "warranty_of": "That warranty has ended, or the order was not delivered.",
           "note_invoice": "Work order {number}: {device}",
           "no_new_customer": "Adding a new customer needs the customer-management permission; choose an existing customer.",
           "reopened": "Delivery invoice {invoice} was cancelled; the order is back to ready for pickup.",
           "serial": "“{item}” is sold by serial number; sell it on a sales invoice so its serial is recorded.",
           "refused": "The customer refused the quote; its lines were taken off. Add an inspection fee as a line if one is charged."},
}


# ---- who may do what ----

def technician_of(user):
    employee = getattr(user, "employee", None) if getattr(user, "is_authenticated", False) else None
    return employee if employee is not None and employee.active else None


def can_view(user):
    return user_has_permission(user, VIEW)


def can_manage(user):
    return user_has_permission(user, MANAGE)


def is_workshop():
    from entities.current import effective_activity

    activity, sub = effective_activity()
    return activity == "services" and (sub or "") in WORK_SUBS


def sees_work_orders(user):
    """The menu entry: a workshop's counter, or a technician who has orders."""

    return is_workshop() and (can_view(user) or technician_of(user) is not None)


def scoped(queryset):
    return entity_scope.scope(queryset.all(), "entity")


def visible_to(user, queryset=None):
    rows = scoped(queryset if queryset is not None else WorkOrder.objects)
    if can_view(user):
        return rows
    technician = technician_of(user)
    return rows.filter(technician=technician) if technician else rows.none()


def _audit(user, action, order, after, event=AuditEventType.UPDATE, before=None):
    AuditLog.objects.create(event_type=event, actor=user, module="workorders", action=action, object_type="workorders.WorkOrder",
                            object_id=str(order.pk), before_data=before or {}, after_data={"number": order.number, **after})


def _event(order, user, note=""):
    WorkOrderEvent.objects.create(order=order, status=order.status, note=(note or "")[:255], user=user)


# ---- receiving ----

def _date(raw, words):
    from datetime import date

    if raw in (None, ""):
        return None
    if hasattr(raw, "year"):
        return raw
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise ValidationError(words["date"])


def _days(raw, words):
    try:
        days = int(str(raw if raw not in (None, "") else "0").strip())
    except ValueError:
        raise ValidationError(words["days"])
    if not 0 <= days <= 3650:
        raise ValidationError(words["days"])
    return days


def _customer(data, user, words):
    from master_data.models import Customer

    chosen = data.get("customer")
    if isinstance(chosen, Customer):
        return chosen
    name = (data.get("customer_name") or "").strip()
    if not name:
        raise ValidationError(words["customer"])
    if not user_has_permission(user, "master_data.manage_parties"):
        raise ValidationError(words["no_new_customer"])  # the same rule as the cashier's quick add
    from sales.pos_customers import quick_add_customer

    customer, _ = quick_add_customer(name[:255], (data.get("customer_phone") or "").strip()[:50], user)
    return customer


@transaction.atomic
def receive(data, user, lang="ar"):
    """A new order: the device (or request) is in. Returns the WorkOrder."""

    from config.numbering import next_in_series

    words = MESSAGES[lang]
    if not can_manage(user):
        raise PermissionDenied("Receiving a work order needs sales.create_sales_invoice.")
    device = (data.get("device") or "").strip()[:160]
    problem = (data.get("problem") or "").strip()
    if not device:
        raise ValidationError(words["device"])
    if not problem:
        raise ValidationError(words["problem"])
    sync_cancelled(user)
    warranty_of = data.get("warranty_of")
    if warranty_of is not None:  # back under warranty: the same customer as the order it came back on
        warranty_of.refresh_from_db()  # its invoice may just have been cancelled
        if warranty_of.status != Status.DELIVERED or not warranty_of.warranty_until or warranty_of.warranty_until < timezone.localdate():
            raise ValidationError(words["warranty_of"])
        customer = warranty_of.customer
    else:
        customer = _customer(data, user, words)
    technician = data.get("technician") if hasattr(data.get("technician"), "pk") else None
    values = {"customer": customer, "device": device, "serial": (data.get("serial") or "").strip()[:80],
              "accessories": (data.get("accessories") or "").strip()[:255], "problem": problem, "technician": technician,
              "promised_on": _date(data.get("promised_on"), words), "warranty_days": _days(data.get("warranty_days"), words),
              "warranty_of": warranty_of, "notes": (data.get("notes") or "").strip()[:255], "entity": entity_scope.entity() or None,
              "created_by": user}
    for attempt in range(5):
        number = next_in_series(WorkOrder, "number", "WO-")
        try:
            with transaction.atomic():
                order = WorkOrder.objects.create(number=number, **values)
            break
        except IntegrityError:
            if attempt == 4 or not WorkOrder.objects.filter(number=number).exists():
                raise
    _event(order, user, problem[:255])
    _audit(user, "receive", order, {"customer": customer.customer_code, "device": device, "warranty_of": warranty_of.number if warranty_of else ""},
           AuditEventType.CREATE)
    return order


def _locked(order, words):
    order = WorkOrder.objects.select_for_update().get(pk=order.pk)
    if not order.is_open:
        raise ValidationError(words["closed"])
    return order


def _may_work(user, order, words, *, counter_only=False):
    if can_manage(user):
        return
    technician = technician_of(user)
    if counter_only or technician is None or order.technician_id != technician.pk:
        raise PermissionDenied(words["own"])


@transaction.atomic
def move(order, to, user, note="", lang="ar"):
    """Move the order to its next status (not delivery)."""

    words = MESSAGES[lang]
    order = _locked(order, words)
    labels = STATUS_LABELS[lang]
    if to not in NEXT.get(order.status, ()):
        raise ValidationError(words["move"].format(from_=labels.get(order.status, order.status), to=labels.get(to, to)))
    _may_work(user, order, words, counter_only=to not in TECHNICIAN_MOVES)
    before = order.status
    order.status = to
    order.save(update_fields=["status"])
    if to == Status.REFUSED:
        _drop_quote(order, user, lang)
    _event(order, user, note)
    _audit(user, "move", order, {"status": to, "note": note[:255]}, before={"status": before})
    return order


def _drop_quote(order, user, lang):
    """A refused quote is not charged on pickup: its lines come off, kept in the audit (Codex on #190).

    An inspection fee, if the shop charges one, is added afterwards as its own line."""

    lines = list(order.lines.select_related("item"))
    if not lines:
        return
    _audit(user, "drop_quote", order, {}, before={"lines": [{"item": line.item.item_code, "quantity": str(line.quantity), "price": str(line.unit_price),
                                                               "warranty": line.is_warranty} for line in lines]})
    order.lines.all().delete()
    WorkOrderEvent.objects.create(order=order, status=order.status, note=MESSAGES[lang]["refused"][:255], user=user)


@transaction.atomic
def update_details(order, data, user, lang="ar"):
    """The bench's notes: diagnosis, technician, promised date, warranty days."""

    words = MESSAGES[lang]
    order = _locked(order, words)
    _may_work(user, order, words)
    before = {"diagnosis": order.diagnosis, "technician": order.technician_id, "promised_on": str(order.promised_on or ""), "warranty_days": order.warranty_days}
    order.diagnosis = (data.get("diagnosis") if data.get("diagnosis") is not None else order.diagnosis).strip()
    if can_manage(user):  # who works on it, and what the customer was told, are the counter's to change
        if "technician" in data:
            order.technician = data.get("technician") if hasattr(data.get("technician"), "pk") else None
        if "promised_on" in data:
            order.promised_on = _date(data.get("promised_on"), words)
        if "warranty_days" in data:
            order.warranty_days = _days(data.get("warranty_days"), words)
    order.save()
    after = {"diagnosis": order.diagnosis, "technician": order.technician_id, "promised_on": str(order.promised_on or ""), "warranty_days": order.warranty_days}
    _audit(user, "update", order, after, before=before)
    return order


# ---- parts and labour ----

def _serial_tracked(items):
    """The items sold by serial number, while serials are on: they go through the sales invoice, which records the serial (Codex on #190)."""

    from serials.services import serials_enabled, tracked_settings

    return set(tracked_settings([item.pk for item in items])) if items and serials_enabled() else set()


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


@transaction.atomic
def add_line(order, item, user, *, quantity="1", unit_price=None, description="", is_warranty=False, lang="ar"):
    words = MESSAGES[lang]
    order = _locked(order, words)
    _may_work(user, order, words, counter_only=True)  # what the customer pays is the counter's
    if item is None:
        raise ValidationError(words["item"])
    if _serial_tracked([item]):
        raise ValidationError(words["serial"].format(item=item.item_name))
    quantity = _decimal(quantity, words, "quantity", Decimal("1")).quantize(Decimal("0.001"))
    if quantity <= 0:
        raise ValidationError(words["quantity"])
    price = _decimal(unit_price, words, "price", item.default_sale_price or ZERO)
    if price < 0:
        raise ValidationError(words["price"])
    covered = bool(is_warranty)  # the line says; a warranty return only ticks the box by default on the form (Codex on #190)
    line = WorkOrderLine.objects.create(order=order, item=item, description=(description or "").strip()[:255], quantity=quantity,
                                        unit_price=money_round(price), is_warranty=covered)
    _audit(user, "add_line", order, {"item": item.item_code, "quantity": str(quantity), "price": str(line.unit_price), "warranty": covered})
    return line


@transaction.atomic
def remove_line(line, user, lang="ar"):
    words = MESSAGES[lang]
    order = _locked(line.order, words)
    _may_work(user, order, words, counter_only=True)
    _audit(user, "remove_line", order, {}, before={"item": line.item.item_code, "quantity": str(line.quantity), "price": str(line.unit_price)})
    line.delete()


def _rows(lines):
    return [{"item": line.item, "quantity": line.quantity, "unit_sale_price": ZERO if line.is_warranty else line.unit_price,
             "line_discount_amount": ZERO, "description": (line.description or line.item.item_name)[:255]} for line in lines]


def _tax(rows):
    from taxes.services import compute_lines, vat_enabled

    return sum((tax for *_, tax in compute_lines(rows)), ZERO) if rows and vat_enabled() else ZERO


def totals(order):
    """Parts, labour, VAT and the total the delivery invoice will come to."""

    lines = list(order.lines.select_related("item"))
    parts = sum((line.amount for line in lines if line.item.is_stock_tracked), ZERO)
    labour = sum((line.amount for line in lines if not line.item.is_stock_tracked), ZERO)
    tax = money_round(_tax(_rows(lines)))
    return {"lines": lines, "parts": parts, "labour": labour, "tax": tax, "total": parts + labour + tax}


def reserved_parts():
    """{item id: quantity} planned on open orders: stock that is spoken for until those jobs are delivered."""

    from django.db.models import Sum

    rows = (WorkOrderLine.objects.filter(order__in=scoped(WorkOrder.objects).filter(status__in=OPEN), item__is_stock_tracked=True)
            .values("item").annotate(total=Sum("quantity")))
    return {row["item"]: row["total"] for row in rows}


# ---- delivery ----

def _location():
    from master_data.models import Location

    return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


@transaction.atomic
def deliver(order, user, *, paid_now="0", cashbox=None, invoice_date=None, lang="ar"):
    """Hand the device back: the order's lines become one posted sales invoice, and its warranty starts."""

    from config.numbering import next_in_series
    from sales.models import SalesInvoice
    from sales.services import post_sales_invoice
    from taxes.services import create_sales_draft_with_tax

    words = MESSAGES[lang]
    order = _locked(order, words)
    _may_work(user, order, words, counter_only=True)
    if order.status != Status.READY:
        raise ValidationError(words["not_ready"])
    today = timezone.localdate()
    lines = list(order.lines.select_related("item"))
    tracked = _serial_tracked([line.item for line in lines])  # a line added before serials were switched on
    if tracked:
        raise ValidationError(words["serial"].format(item=next(line.item.item_name for line in lines if line.item_id in tracked)))
    invoice = None
    if lines:
        location = _location()
        if location is None:
            raise ValidationError(words["setup"])
        rows = _rows(lines)
        total = sum((line.amount for line in lines), ZERO) + money_round(_tax(rows))
        paid = money_round(_decimal(paid_now, words, "paid", ZERO))
        if paid < 0 or paid > total:  # never take more than the invoice will come to
            raise ValidationError(words["paid"])
        if paid > 0 and cashbox is None:
            raise ValidationError(words["cashbox"])
        header = {"invoice_date": invoice_date or today, "customer": order.customer, "selling_location": location,
                  "cashbox": cashbox if paid > 0 else None, "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": paid,
                  "notes": words["note_invoice"].format(number=order.number, device=order.device)[:255]}
        for attempt in range(5):
            number = next_in_series(SalesInvoice, "invoice_number", "SI-")
            try:
                with transaction.atomic():
                    invoice = create_sales_draft_with_tax({**header, "invoice_number": number}, rows, user)
                break
            except IntegrityError:
                if attempt == 4 or not SalesInvoice.objects.filter(invoice_number=number).exists():
                    raise
        post_sales_invoice(invoice.pk, user)
        invoice.refresh_from_db()
    order.invoice = invoice
    order.status = Status.DELIVERED
    order.delivered_at = timezone.now()
    order.warranty_until = today + timedelta(days=order.warranty_days) if order.warranty_days else None
    order.save(update_fields=["invoice", "status", "delivered_at", "warranty_until"])
    _event(order, user, invoice.invoice_number if invoice else "")
    _audit(user, "deliver", order, {"invoice": invoice.invoice_number if invoice else "", "total": str(invoice.total_amount if invoice else ZERO),
                                     "warranty_until": str(order.warranty_until or "")})
    return order


def sync_cancelled(user=None):
    """An order whose delivery invoice was cancelled on the sales page goes back to ready (Codex on #190).

    Cancelling reversed the stock, cash and accounts; the order follows: it is open again, its warranty is void,
    and it can be delivered (and invoiced) once more. Its timeline keeps the cancelled invoice's number."""

    stale = list(scoped(WorkOrder.objects).filter(status=Status.DELIVERED, invoice__status="cancelled").select_related("invoice"))
    reopened = 0
    for order in stale:
        number = order.invoice.invoice_number
        # Only while it is still delivered on that very invoice: a second caller must not undo a redelivery made in between (Codex on #190).
        if not WorkOrder.objects.filter(pk=order.pk, status=Status.DELIVERED, invoice_id=order.invoice_id).update(
                status=Status.READY, invoice=None, delivered_at=None, warranty_until=None):
            continue
        order.status = Status.READY
        WorkOrderEvent.objects.create(order=order, status=Status.READY, note=MESSAGES["ar"]["reopened"].format(invoice=number)[:255],
                                      user=user or order.created_by)
        reopened += 1
    return reopened


# ---- the workshop's figures ----

def late(today=None):
    today = today or timezone.localdate()
    return scoped(WorkOrder.objects).filter(status__in=OPEN, promised_on__lt=today).exclude(status=Status.READY)


def technician_report(first, last):
    """Per technician, orders delivered in [first, last]: count, billed (before VAT), average days in the shop, warranty returns."""

    from collections import OrderedDict

    delivered = (scoped(WorkOrder.objects).filter(status=Status.DELIVERED, delivered_at__date__gte=first, delivered_at__date__lte=last)
                 .select_related("technician", "invoice"))
    rows = OrderedDict()
    for order in delivered:
        key = order.technician_id or 0
        row = rows.setdefault(key, {"technician": order.technician, "count": 0, "billed": ZERO, "days": 0, "returns": 0})
        row["count"] += 1
        if order.invoice is not None and order.invoice.status == "posted":
            row["billed"] += order.invoice.total_amount - (order.invoice.tax_amount or ZERO)
        row["days"] += max((order.delivered_at - order.received_at).days, 0)
    returns = scoped(WorkOrder.objects).filter(warranty_of__isnull=False, received_at__date__gte=first, received_at__date__lte=last).select_related("warranty_of")
    for order in returns:
        key = order.warranty_of.technician_id or 0
        if key in rows:
            rows[key]["returns"] += 1
    out = list(rows.values())
    for row in out:
        row["avg_days"] = round(row["days"] / row["count"], 1) if row["count"] else None
    return sorted(out, key=lambda row: -row["count"])
