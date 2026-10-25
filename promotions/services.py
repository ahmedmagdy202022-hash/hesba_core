"""T5 (MARKET-PARITY): promotions and coupons (HG-057).

``apply`` runs on the lines of a sale before its draft is saved, on the invoice form and at the till. It only
fills each line's own discount field; ``create_sales_draft`` and posting use the line amounts they are given, as
for a discount typed by hand, and a return refunds the discounted amount as it always has.

The rules:

* only promotions switched on, inside their dates and their hours, count; one with a coupon code counts only when
  that code is typed;
* the best single promotion wins (the one that takes the most off) — promotions never stack;
* a line with a discount typed by hand keeps it and takes no promotion;
* the price on the line is whatever it already is (the item's price or the customer's price list).

A sale made offline at the till keeps the price shown when it was made: syncing it applies nothing.
"""

from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from .models import InvoicePromotion, Promotion, PromotionKind

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
MANAGE = "master_data.manage_items"

MESSAGES = {
    "ar": {
        "name": "اكتب اسم العرض.", "kind": "اختار نوع العرض.", "percent": "النسبة لازم من 1 لـ 100.", "amount": "المبلغ لازم أكبر من صفر.",
        "min_total": "اكتب الحد الأدنى للفاتورة.", "buy_get": "اكتب «اشتري كام» و«خد كام ببلاش» (أرقام صحيحة).", "dates": "تاريخ النهاية قبل البداية.",
        "times": "اكتب الوقتين (من وإلى) أو سيبهم فاضيين.", "coupon_taken": "الكود ده مستخدم في عرض تاني شغال.", "coupon": "الكوبون «{code}» مش موجود أو مش شغال دلوقتي.",
        "number": "الرقم ده مش مفهوم.", "date": "التاريخ مش مفهوم.", "time": "الوقت مش مفهوم.", "target": "اختار صنف أو قسم، مش الاتنين.",
    },
    "en": {
        "name": "Type the promotion's name.", "kind": "Choose the kind of promotion.", "percent": "The percentage must be from 1 to 100.",
        "amount": "The amount must be above zero.", "min_total": "Type the invoice's minimum total.", "buy_get": "Type “buy how many” and “get how many free” (whole numbers).",
        "dates": "The end date is before the start.", "times": "Type both times (from and to) or leave both empty.", "coupon_taken": "This code is used by another active promotion.",
        "coupon": "The coupon “{code}” does not exist or is not running now.", "number": "This number is not understood.", "date": "The date is not understood.",
        "time": "The time is not understood.", "target": "Choose an item or a section, not both.",
    },
}


# ---- who may do what ----

def can_manage(user):
    return user_has_permission(user, MANAGE)


def enabled():
    from settings_core.capabilities import capability_enabled

    return capability_enabled("promotions")


# ---- which promotions run now ----

def running(now=None):
    """Promotions switched on, inside their dates and hours at ``now`` (local time)."""

    now = timezone.localtime(now) if now is not None else timezone.localtime()
    day, clock = now.date(), now.time()
    rows = Promotion.objects.filter(active=True).filter(Q(starts_on__isnull=True) | Q(starts_on__lte=day)).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=day))
    return [promo for promo in rows.select_related("item", "category") if _in_hours(promo, clock)]


def _in_hours(promo, clock):
    if promo.from_time is None or promo.to_time is None:
        return True
    if promo.from_time <= promo.to_time:
        return promo.from_time <= clock <= promo.to_time
    return clock >= promo.from_time or clock <= promo.to_time  # an evening that runs past midnight


def _covers(promo, item):
    if promo.item_id:
        return item.pk == promo.item_id
    if promo.category_id:
        return item.category_id == promo.category_id
    return True


def _gross(line):
    return money_round(Decimal(line["quantity"]) * Decimal(line["unit_sale_price"]))


def _open(line):
    """A line that may take a promotion: no discount typed by hand."""

    return not (line.get("line_discount_amount") or ZERO)


def discounts(promo, lines):
    """What ``promo`` takes off each line (zero where it does not apply)."""

    result = [ZERO for _ in lines]
    eligible = [index for index, line in enumerate(lines) if _open(line) and _covers(promo, line["item"]) and _gross(line) > 0]
    if promo.kind == PromotionKind.PERCENT and promo.percent:
        for index in eligible:
            result[index] = money_round(_gross(lines[index]) * promo.percent / HUNDRED)
    elif promo.kind == PromotionKind.BUY_GET and promo.buy_quantity and promo.get_quantity:
        group = Decimal(promo.buy_quantity + promo.get_quantity)
        for index in eligible:
            line = lines[index]
            free = (Decimal(line["quantity"]) / group).to_integral_value(rounding=ROUND_FLOOR) * promo.get_quantity
            result[index] = min(money_round(free * Decimal(line["unit_sale_price"])), _gross(line))
    elif promo.kind == PromotionKind.AMOUNT_OVER and promo.amount:
        base = sum((_gross(lines[index]) for index in eligible), ZERO)
        if eligible and base > 0 and base >= (promo.min_total or ZERO):
            off = min(money_round(promo.amount), base)
            left = off
            for position, index in enumerate(eligible):  # spread over the lines it covers, the last takes the rounding
                share = left if position == len(eligible) - 1 else money_round(off * _gross(lines[index]) / base)
                share = min(share, _gross(lines[index]), left)
                result[index], left = share, left - share
    return [min(value, _gross(line)) for value, line in zip(result, lines)]


def apply(lines, now=None, coupon="", lang="ar"):
    """Fill the best promotion's discount into ``lines`` (dicts as the invoice and the till build them).

    Returns ``(lines, promotion, discount)``; ``promotion`` is None when nothing applies. An unknown or stopped
    coupon is refused, so the cashier knows it did not count.
    """

    if not enabled():
        return lines, None, ZERO
    code = str(coupon or "").strip()
    candidates = [promo for promo in running(now) if not promo.coupon_code or (code and promo.coupon_code.lower() == code.lower())]
    if code and not any(promo.coupon_code and promo.coupon_code.lower() == code.lower() for promo in candidates):
        raise ValidationError(MESSAGES[lang]["coupon"].format(code=code))
    best, best_off, best_total = None, None, ZERO
    for promo in candidates:
        off = discounts(promo, lines)
        total = sum(off, ZERO)
        if total > best_total:
            best, best_off, best_total = promo, off, total
    if best is None:
        return lines, None, ZERO
    filled = []
    for line, off in zip(lines, best_off):
        filled.append(dict(line, line_discount_amount=off) if off else line)
    return filled, best, best_total


def record(invoice, promotion, discount, coupon=""):
    """Remember which promotion gave the invoice its discount (for the report)."""

    if promotion is None or not discount:
        return None
    code = str(coupon or "").strip() if promotion.coupon_code else ""
    return InvoicePromotion.objects.create(invoice=invoice, promotion=promotion, discount=money_round(discount), coupon_code=code[:40])


# ---- managing promotions ----

def _decimal(raw, words, required=False, key="number"):
    text = str(raw or "").replace(",", "").strip()
    if not text:
        if required:
            raise ValidationError(words[key])
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValidationError(words["number"])


def _whole(raw, words):
    value = _decimal(raw, words)
    if value is None:
        return None
    if value != value.to_integral_value() or value < 1:
        raise ValidationError(words["buy_get"])
    return int(value)


def _date(raw, words):
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return timezone.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(words["date"])


def _time(raw, words):
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return timezone.datetime.strptime(text[:5], "%H:%M").time()
    except ValueError:
        raise ValidationError(words["time"])


@transaction.atomic
def save(data, user, promo=None, lang="ar"):
    """Create or change a promotion from the screen's form."""

    from master_data.models import Category, Item

    if not can_manage(user):
        raise PermissionDenied("Promotions need master_data.manage_items.")
    words = MESSAGES[lang]
    name = str(data.get("name") or "").strip()[:120]
    if not name:
        raise ValidationError(words["name"])
    kind = data.get("kind")
    if kind not in PromotionKind.values:
        raise ValidationError(words["kind"])
    item = Item.objects.filter(pk=data.get("item")).first() if str(data.get("item") or "").isdigit() else None
    category = Category.objects.filter(pk=data.get("category")).first() if str(data.get("category") or "").isdigit() else None
    if item and category:
        raise ValidationError(words["target"])
    fields = {"name": name, "kind": kind, "item": item, "category": category, "active": data.get("active") in (True, "1", "on"),
              "coupon_code": str(data.get("coupon_code") or "").strip()[:40], "starts_on": _date(data.get("starts_on"), words),
              "ends_on": _date(data.get("ends_on"), words), "from_time": _time(data.get("from_time"), words), "to_time": _time(data.get("to_time"), words),
              "percent": None, "amount": None, "min_total": None, "buy_quantity": None, "get_quantity": None}
    if fields["starts_on"] and fields["ends_on"] and fields["ends_on"] < fields["starts_on"]:
        raise ValidationError(words["dates"])
    if (fields["from_time"] is None) != (fields["to_time"] is None):
        raise ValidationError(words["times"])
    if kind == PromotionKind.PERCENT:
        percent = _decimal(data.get("percent"), words, required=True, key="percent")
        if not Decimal("0") < percent <= HUNDRED:
            raise ValidationError(words["percent"])
        fields["percent"] = percent
    elif kind == PromotionKind.BUY_GET:
        fields["buy_quantity"], fields["get_quantity"] = _whole(data.get("buy_quantity"), words), _whole(data.get("get_quantity"), words)
        if not fields["buy_quantity"] or not fields["get_quantity"]:
            raise ValidationError(words["buy_get"])
    else:
        amount = _decimal(data.get("amount"), words, required=True, key="amount")
        if amount <= 0:
            raise ValidationError(words["amount"])
        fields["amount"] = money_round(amount)
        fields["min_total"] = money_round(_decimal(data.get("min_total"), words, required=True, key="min_total"))
    if fields["coupon_code"] and fields["active"]:
        taken = Promotion.objects.filter(active=True, coupon_code__iexact=fields["coupon_code"])
        if promo is not None:
            taken = taken.exclude(pk=promo.pk)
        if taken.exists():
            raise ValidationError(words["coupon_taken"])
    if promo is None:
        promo = Promotion.objects.create(created_by=user, **fields)
        event = AuditEventType.CREATE
    else:
        for key, value in fields.items():
            setattr(promo, key, value)
        promo.save()
        event = AuditEventType.UPDATE
    AuditLog.objects.create(event_type=event, actor=user, module="promotions", action="save_promotion", object_type="promotions.Promotion",
                            object_id=str(promo.pk), before_data={}, after_data={"name": promo.name, "kind": promo.kind, "active": promo.active,
                                                                                  "coupon_code": promo.coupon_code})
    return promo


def report(date_from=None, date_to=None):
    """Each promotion's posted invoices and the discount it gave."""

    uses = InvoicePromotion.objects.filter(invoice__status="posted")
    if date_from:
        uses = uses.filter(invoice__invoice_date__gte=date_from)
    if date_to:
        uses = uses.filter(invoice__invoice_date__lte=date_to)
    rows = uses.values("promotion_id").annotate(invoices=Count("id"), discount=Sum("discount"))
    return {row["promotion_id"]: {"invoices": row["invoices"], "discount": row["discount"] or ZERO} for row in rows}
