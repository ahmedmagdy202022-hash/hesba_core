"""T6 (MARKET-PARITY): loyalty points (HG-058).

* A posted invoice earns ``floor(total / amount_per_point)`` points for its customer (never for the walk-in
  customer of the till). Earning listens to the posting's audit row: the posting function itself is not changed.
* Spending: on the invoice form or at the till the seller types the points to spend. ``redeem`` checks them and
  returns what they take off; the view adds that to the invoice's own discount before the draft is saved, and
  ``remember`` keeps the points with the draft. The spending row is booked when the invoice is posted.
* A cancelled invoice gives back what it spent and takes back what it earned; a return takes back the earned points
  in proportion to what came back (rounded down), and cancelling the return gives them back. Every one of these is
  a new row (append-only); the discount already given stays, as a return refunds the discounted amount anyway.
* Expiry: with ``expiry_months`` set, earned points run out that many months later. Spending uses the oldest
  points first, so what runs out is what was left of the oldest.
"""

import calendar
from datetime import date
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.signals import post_save
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from .models import EntryKind, InvoiceRedemption, LoyaltySettings, PointsEntry

ZERO = Decimal("0.00")
MANAGE = "settings.manage_settings"

MESSAGES = {
    "ar": {
        "walk_in": "النقاط بتتحسب لعميل باسمه، مش للعميل النقدي.", "number": "اكتب عدد النقاط رقم صحيح.", "min": "أقل عدد نقاط يتصرف مرة واحدة {n}.",
        "short": "رصيد العميل {have} نقطة بس.", "over": "النقاط دي ({amount}) أكتر من قيمة الفاتورة.", "spent": "رصيد النقاط مبقاش يكفي للفاتورة دي ({have} نقطة).",
        "amount": "المبلغ لازم أكبر من صفر.", "value": "قيمة النقطة لازم أكبر من صفر.", "off": "نقاط الولاء مقفولة.",
    },
    "en": {
        "walk_in": "Points are for a named customer, not the walk-in customer.", "number": "Type the points as a whole number.",
        "min": "At least {n} points are spent at a time.", "short": "The customer has only {have} points.", "over": "These points ({amount}) are more than the invoice.",
        "spent": "The points balance no longer covers this invoice ({have} points).", "amount": "The amount must be above zero.",
        "value": "A point's value must be above zero.", "off": "Loyalty points are switched off.",
    },
}


def enabled():
    from settings_core.capabilities import capability_enabled

    return capability_enabled("loyalty")


def rules():
    return LoyaltySettings.get()


def can_manage(user):
    return user_has_permission(user, MANAGE)


def _walk_in(customer):
    from sales.pos import WALK_IN_CODE

    return customer is None or customer.customer_code == WALK_IN_CODE


def earned_for(amount, settings=None):
    settings = settings or rules()
    if not amount or amount <= 0 or settings.amount_per_point <= 0:
        return 0
    return int((Decimal(amount) / settings.amount_per_point).to_integral_value(rounding=ROUND_FLOOR))


def value_of(points, settings=None):
    settings = settings or rules()
    return money_round(Decimal(points or 0) * settings.point_value)


def _add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# ---- the balance ----

def balance(customer, today=None, besides=None):
    """``{"points", "value", "expired", "pending"}``; ``points`` is what can be spent now, after the points other
    drafts set aside (``besides``: an invoice whose own set-aside points still count as the customer's)."""

    today = today or timezone.localdate()
    rows = list(PointsEntry.objects.filter(customer=customer).order_by("entry_date", "pk"))
    net = {row.pk: row.points for row in rows if row.kind != EntryKind.BACK}
    for row in rows:
        if row.kind == EntryKind.BACK and row.reverses_id in net:
            net[row.reverses_id] += row.points
    earns = [row for row in rows if row.kind == EntryKind.EARN]
    used = -sum(net[row.pk] for row in rows if row.kind == EntryKind.SPEND)  # what was spent, net of what came back
    expired = 0
    for row in earns:  # the oldest points are spent first
        have = max(net[row.pk], 0)
        take = min(have, max(used, 0))
        used -= take
        if row.expires_on and row.expires_on < today:
            expired += have - take
    left = sum(net.values()) - expired
    drafts = InvoiceRedemption.objects.filter(invoice__customer=customer, invoice__status="draft")
    if besides is not None:
        drafts = drafts.exclude(invoice=besides)
    pending = drafts.aggregate(n=Sum("points"))["n"] or 0
    points = left - pending
    return {"points": points, "value": value_of(max(points, 0)), "expired": expired, "pending": pending}


def statement(customer, limit=20):
    return PointsEntry.objects.filter(customer=customer).select_related("invoice", "sales_return")[:limit]


# ---- spending on an invoice ----

def redeem(customer, raw_points, lines_total, lang="ar"):
    """Check the points typed on an invoice; returns ``(points, amount)``, or ``(0, 0)`` when none were typed."""

    words = MESSAGES["en" if lang == "en" else "ar"]
    text = str(raw_points or "").strip()
    if not text or text == "0":
        return 0, ZERO
    if not enabled():
        raise ValidationError(words["off"])
    try:
        points = Decimal(text)
    except InvalidOperation:
        raise ValidationError(words["number"])
    if points != points.to_integral_value() or points < 1:
        raise ValidationError(words["number"])
    points = int(points)
    if _walk_in(customer):
        raise ValidationError(words["walk_in"])
    settings = rules()
    if points < settings.min_points:
        raise ValidationError(words["min"].format(n=settings.min_points))
    have = balance(customer)["points"]
    if points > have:
        raise ValidationError(words["short"].format(have=max(have, 0)))
    amount = value_of(points, settings)
    if amount > money_round(lines_total or 0):
        raise ValidationError(words["over"].format(amount=amount))
    return points, amount


def lines_total(lines):
    return sum((money_round(Decimal(line["quantity"]) * Decimal(line["unit_sale_price"])) - Decimal(line.get("line_discount_amount") or 0) for line in lines), ZERO)


def remember(invoice, points, amount):
    if not points:
        return None
    return InvoiceRedemption.objects.create(invoice=invoice, points=points, amount=amount)


# ---- following the invoice's life (through its audit rows) ----

def _net(row):
    return row.points + (row.backs.aggregate(n=Sum("points"))["n"] or 0)


def _on_posted(invoice, user):
    from sales.models import SalesInvoice

    invoice = SalesInvoice.objects.select_related("customer").get(pk=invoice.pk)
    if _walk_in(invoice.customer):
        return
    redemption = InvoiceRedemption.objects.filter(invoice=invoice).first()
    if redemption is not None and not PointsEntry.objects.filter(invoice=invoice, kind=EntryKind.SPEND).exists():
        have = balance(invoice.customer, besides=invoice)["points"]
        if redemption.points > have:
            raise ValidationError(MESSAGES["ar"]["spent"].format(have=max(have, 0)))
        PointsEntry.objects.create(customer=invoice.customer, kind=EntryKind.SPEND, points=-redemption.points, entry_date=invoice.invoice_date,
                                   invoice=invoice, created_by=user)
    if not enabled():
        return
    settings = rules()
    points = earned_for(invoice.total_amount, settings)
    if points and not PointsEntry.objects.filter(invoice=invoice, kind=EntryKind.EARN).exists():
        PointsEntry.objects.create(customer=invoice.customer, kind=EntryKind.EARN, points=points, entry_date=invoice.invoice_date, invoice=invoice,
                                   expires_on=_add_months(invoice.invoice_date, settings.expiry_months) if settings.expiry_months else None, created_by=user)


def _on_cancelled(invoice, user):
    today = timezone.localdate()
    for row in PointsEntry.objects.filter(invoice=invoice, kind__in=[EntryKind.EARN, EntryKind.SPEND]):
        left = _net(row)
        if left:
            PointsEntry.objects.create(customer=row.customer, kind=EntryKind.BACK, points=-left, entry_date=today, invoice=invoice, reverses=row, created_by=user)


def _on_returned(sales_return, user):
    invoice = sales_return.source_invoice
    earned = PointsEntry.objects.filter(invoice=invoice, kind=EntryKind.EARN).first()
    if earned is None or not invoice.total_amount:
        return
    share = int((Decimal(earned.points) * sales_return.total_amount / invoice.total_amount).to_integral_value(rounding=ROUND_FLOOR))
    take = min(share, max(_net(earned), 0))
    if take:
        PointsEntry.objects.create(customer=earned.customer, kind=EntryKind.BACK, points=-take, entry_date=sales_return.return_date,
                                   sales_return=sales_return, reverses=earned, created_by=user)


def _on_return_cancelled(sales_return, user):
    for earned_id, taken in (PointsEntry.objects.filter(sales_return=sales_return, kind=EntryKind.BACK).values("reverses").annotate(n=Sum("points"))
                             .values_list("reverses", "n")):
        if taken:
            earned = PointsEntry.objects.get(pk=earned_id)
            PointsEntry.objects.create(customer=earned.customer, kind=EntryKind.BACK, points=-taken, entry_date=timezone.localdate(),
                                       sales_return=sales_return, reverses=earned, created_by=user)


def _on_audit(sender, instance, created, **kwargs):
    if not created or instance.module != "sales":
        return
    from sales.models import SalesInvoice, SalesReturn

    action = instance.action
    if action == "post_sales_invoice":
        _on_posted(SalesInvoice(pk=int(instance.object_id)), instance.actor)
    elif action == "cancel_posted_sales_invoice":
        _on_cancelled(SalesInvoice.objects.get(pk=int(instance.object_id)), instance.actor)
    elif action == "create_sales_return":
        _on_returned(SalesReturn.objects.select_related("source_invoice").get(pk=int(instance.object_id)), instance.actor)
    elif action == "cancel_sales_return":
        _on_return_cancelled(SalesReturn.objects.get(pk=int(instance.object_id)), instance.actor)


def connect():
    post_save.connect(_on_audit, sender=AuditLog, dispatch_uid="loyalty-follows-sales", weak=False)


# ---- the rules screen ----

@transaction.atomic
def save_rules(data, user, lang="ar"):
    if not can_manage(user):
        raise PermissionDenied("Loyalty rules need settings.manage_settings.")
    words = MESSAGES["en" if lang == "en" else "ar"]
    settings = rules()
    try:
        amount = Decimal(str(data.get("amount_per_point") or "").replace(",", "").strip())
        value = Decimal(str(data.get("point_value") or "").replace(",", "").strip())
        minimum = int(Decimal(str(data.get("min_points") or "0").strip()))
        months = int(Decimal(str(data.get("expiry_months") or "0").strip()))
    except (InvalidOperation, ValueError):
        raise ValidationError(words["number"])
    if amount <= 0:
        raise ValidationError(words["amount"])
    if value <= 0:
        raise ValidationError(words["value"])
    if minimum < 1 or months < 0:
        raise ValidationError(words["number"])
    before = {"amount_per_point": str(settings.amount_per_point), "point_value": str(settings.point_value), "min_points": settings.min_points,
              "expiry_months": settings.expiry_months}
    settings.amount_per_point, settings.point_value, settings.min_points, settings.expiry_months = money_round(amount), money_round(value), minimum, months
    settings.updated_by = user
    settings.save()
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="loyalty", action="save_loyalty_rules", object_type="loyalty.LoyaltySettings",
                            object_id="1", before_data=before, after_data={"amount_per_point": str(settings.amount_per_point),
                                                                          "point_value": str(settings.point_value), "min_points": minimum, "expiry_months": months})
    return settings


def leaders(limit=30):
    """The customers holding the most points (before expiry), for the loyalty screen."""

    from master_data.models import Customer

    ids = (PointsEntry.objects.values("customer").annotate(n=Sum("points")).filter(n__gt=0).order_by("-n").values_list("customer", flat=True)[:limit])
    customers = Customer.objects.in_bulk(list(ids))
    return [{"customer": customers[pk], **balance(customers[pk])} for pk in ids if pk in customers]
