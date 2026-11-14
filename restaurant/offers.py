"""Restaurants R6 (HG-114): coupons, points, gift cards and happy hour on the table bill.

The table bill takes them exactly as the cashier screen does (``sales.pos.pos`` is the precedent), through the same
unchanged services, in the same order:

1. **the offer** (``promotions.apply``) runs on the bill's food rows before anything is posted: the best single running
   promotion, a coupon only when typed. Each dish carries ``at`` = when it was ordered (``OrderLine.created_at``), so a
   happy hour is judged by the order's time, not the payment's: a tea ordered at 18:30 under a 17:00–19:00 offer and paid
   at 19:30 keeps it (HG-114, the one change inside ``promotions``). The offer only fills each row's own line discount;
   ``checkout(promotion=…)`` records which promotion gave it, as at the till;
2. **which rows take it:** dishes and their priced add-ons (R1a). Never a combo's component (R1b already sells it below its
   menu price; a component whose share rounded to 0.00 is skipped all the same, HG-112's trap), nor an add-on of one (the
   whole combo block takes no offer), nor the delivery fee, the minimum's top-up or the service (R3): those are not order
   lines and are added after the offer. Leaving the combo rows out of the offer's input also leaves them out of a «fixed
   amount over a total» offer's minimum, as the till leaves out a line with a typed discount;
3. **the service (R3)** is worked out on the food after the offer (the guest pays service on what the food costs them);
   the minimum's top-up is measured on the food before it (an offer never turns into a top-up);
4. **points** (``loyalty.redeem``, T6) come off after tax, added to the bill discount, as at the till: VAT is unchanged and,
   as the bill discount never does (R3), they never reduce the service line. A walk-in cannot spend points;
5. **a gift card** (``giftcards``, HG-071) pays part of the bill: ``checkout(gift=…)`` leaves that part due and redeems it
   from the card in the same transaction. With split tenders (R2b) the tenders pay the rest: tenders + card = the bill.

Everything is posted by the order's branch (R12): its store, its cashbox, and a gift card of that branch's entity only.
A cancelled invoice gives the points and the card's money back through the listeners that already follow the sales
engine (``loyalty._on_cancelled``, ``giftcards._on_audit``); nothing here reverses anything.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from config.money import money_round

ZERO = Decimal("0.00")

WORDS = {
    "ar": {
        "box": "خصم أو نقاط أو كارت هدية", "phone": "موبايل العميل", "coupon": "كود خصم", "points": "اصرف نقاط", "gift_code": "كارت هدية",
        "gift_amount": "المبلغ من الكارت", "apply": "احسب الحساب", "clear": "شيل الخصومات",
        "customer_found": "العميل: {name}", "balance": "رصيده {points} نقطة (تساوي {value})",
        "no_customer": "مفيش عميل بالرقم ده. اختاره من القائمة أو سجّله من شاشة العملاء.",
        "offer_row": "عرض: {name}", "points_row": "نقاط ({points})", "points_row_short": "نقاط", "gift_row": "كارت هدية {card}",
        "bill": "الحساب", "due": "المطلوب", "cash_due": "الباقي بعد الكارت",
        "hint": "العرض بيتحسب بوقت طلب الصنف؛ والنقاط والكارت بيتخصموا بعد الضريبة ومش بيقلّلوا الخدمة.",
        "points_walk_in": "النقاط بتتصرف لعميل باسمه؛ اكتب موبايله الأول.",
    },
    "en": {
        "box": "Discount, points or a gift card", "phone": "Customer's mobile", "coupon": "Discount code", "points": "Spend points",
        "gift_code": "Gift card", "gift_amount": "Amount from the card", "apply": "Work out the bill", "clear": "Remove the discounts",
        "customer_found": "Customer: {name}", "balance": "Balance {points} points (worth {value})",
        "no_customer": "No customer has this number. Pick them from the list or add them on the customers screen.",
        "offer_row": "Offer: {name}", "points_row": "Points ({points})", "points_row_short": "Points", "gift_row": "Gift card {card}",
        "bill": "The bill", "due": "To pay", "cash_due": "Left after the card",
        "hint": "An offer goes by when each dish was ordered; points and the gift card come off after VAT and never reduce the service charge.",
        "points_walk_in": "Points are spent by a named customer; enter their mobile first.",
    },
}


def words(lang):
    return WORDS["en" if lang == "en" else "ar"]


@dataclass(frozen=True)
class Offer:
    """What the cashier typed on the bill: a coupon, and the customer (a members-only offer and points are theirs)."""

    coupon: str = ""
    customer: object = None


def promotions_on():
    from promotions.services import enabled

    return enabled()


def loyalty_on():
    from loyalty.services import enabled

    return enabled()


def gift_cards_on(user=None):
    from sales.pos import _gift_cards_on

    return _gift_cards_on(user)


def any_on(user=None):
    return promotions_on() or loyalty_on() or gift_cards_on(user)


def takes_offer(line, by_pk):
    """A dish or its add-on; never a combo's component or an add-on of one (R1b, HG-112's 0.00 share included)."""

    if line.combo_id:
        return False
    parent = by_pk.get(line.parent_id) if line.parent_id else None
    return not (parent is not None and parent.combo_id)


def promote(rows, offer, lang="ar"):
    """Fill the best promotion into the food rows (dicts with ``line``, the order line behind each). Each row taking the
    offer gets it added to its ``line_discount_amount`` and as ``offer``; every other row gets ``offer`` 0.00. Returns
    ``(promotion, discount)``: ``(None, 0.00)`` when nothing applies or promotions are off. A coupon that is unknown or not
    running is refused (``ValidationError``), as at the till."""

    from promotions import services as promotions

    for row in rows:
        row["offer"] = ZERO
    if offer is None or not promotions.enabled():
        return None, ZERO
    by_pk = {row["line"].pk: row["line"] for row in rows}
    picks = [index for index, row in enumerate(rows) if takes_offer(row["line"], by_pk)]
    lines = [{"item": rows[index]["item"], "quantity": rows[index]["quantity"], "unit_sale_price": rows[index]["unit_sale_price"],
              "line_discount_amount": rows[index]["line_discount_amount"], "description": "", "at": rows[index]["line"].created_at} for index in picks]
    filled, promotion, off = promotions.apply(lines, coupon=offer.coupon, lang="en" if lang == "en" else "ar", customer=offer.customer)
    if promotion is None:
        return None, ZERO
    for index, new in zip(picks, filled):
        extra = money_round(new["line_discount_amount"] - rows[index]["line_discount_amount"])
        if extra:
            rows[index]["offer"] = extra
            rows[index]["line_discount_amount"] = money_round(rows[index]["line_discount_amount"] + extra)
    return promotion, money_round(off)


def find_customer(phone):
    """The active customer whose phone (or WhatsApp) is ``phone``, by digits (POS-002's directory): an exact match, or the one
    number ending with what was typed (seven digits or more, e.g. without the country code). None when none or several."""

    from sales.pos_customers import customer_directory, digits

    typed = digits(phone)
    if len(typed) < 7:
        return None
    rows = [row for row in customer_directory() if row["phone"]]
    exact = [row for row in rows if row["phone"] == typed]
    found = exact or [row for row in rows if row["phone"].endswith(typed) or (typed.endswith(row["phone"]) and len(row["phone"]) >= 7)]
    if len(found) != 1:
        return None
    from master_data.models import Customer

    return Customer.objects.filter(pk=found[0]["id"], active=True).first()


def points_part(customer, raw_points, rows, lang="ar"):
    """T6 at the till: ``(points, amount)`` the customer spends on these bill rows (before VAT), or ``(0, 0.00)``. Refused for a
    walk-in, below the minimum, above the balance or above the bill (``loyalty.redeem``, unchanged)."""

    from loyalty import services as loyalty

    return loyalty.redeem(customer, raw_points, loyalty.lines_total(rows), "en" if lang == "en" else "ar")


def summary(bill, points=0, points_off=ZERO, gift=None):
    """The figures under the bill: ``due`` (the bill after the points, before the cashier's own discount) and what is left
    after the gift card's part."""

    gift_part = money_round(gift[1]) if gift is not None else ZERO
    due = money_round(bill["total"] - points_off)
    return {"points": points, "points_off": points_off, "gift": gift, "gift_part": gift_part, "due": due, "cash_due": money_round(due - gift_part)}


def clean_rows(rows):
    """The invoice's own keys only: what ``checkout`` is given (never ``line``, ``kind``, ``offer`` or ``at``)."""

    return [{key: row[key] for key in ("item", "quantity", "unit_sale_price", "line_discount_amount", "description")} for row in rows]


def check_seen(seen, actual):
    """The till's rule (Codex #268 / #274): the payment was taken for what the bill box showed; a figure that changed since
    (an offer edited or stopped, a point's value changed) refuses the payment. Checked only when the box sent it."""

    if seen is None:
        return
    if abs(money_round(seen) - money_round(actual)) > Decimal("0.005"):
        raise ValidationError("err_promo_changed")


def seen_value(raw):
    """A ``*_seen`` field as the form sends it: None when absent, a Decimal when readable; anything else refuses the payment."""

    if raw is None:
        return None
    try:
        value = Decimal(str(raw).strip())
    except Exception:
        raise ValidationError("err_promo_changed")
    if not value.is_finite():
        raise ValidationError("err_promo_changed")
    return value
