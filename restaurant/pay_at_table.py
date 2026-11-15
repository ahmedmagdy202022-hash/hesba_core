"""Restaurants R5b (HG-121): pay from the table. The guest presses «ادفع» on the table's QR page and pays the table's bill on
the payment company's page; the verified callback posts the bill as a fully paid sale into «بوابة الدفع».

Rules, stated once:

* **«ادفع» shows** only when the online-payment capability is on (``payment_links``), a gateway is connected, an online-payments
  user is chosen in the restaurant settings (``KEY``) and can still sell, and the table's open order has a bill to pay that
  the guest can pay alone: a dine-in order with live lines, not a part of a bill (R2a) and with no open part, no held deposit
  (R8 / R10) and a «بوابة الدفع» account in its branch. Otherwise the QR page is exactly R5a's and the endpoint refuses;
* **pressing it locks the order for payment:** a pay link is made against the **order** (``payments.create_order_link``),
  for the bill as ``pay`` will post it now, as the online-payments user, for ``payments.table_link_minutes()``. While that link
  is live, adding, changing, voiding, splitting, putting back, merging, moving, cancelling and paying (with or without a
  discount) are refused (``refuse_if_locked``, called by each of those services under the order's row lock). A link that
  expires, is cancelled by staff (``cancel``) or ends in ``review`` frees the order. A second press while it is live sends the
  guest to the same link (never a second gateway order);
* **the callback** (``post_link``, called by ``payments.services`` under the order's lock) posts only when the order is still
  open and exactly as it was when the link was made (``digest``), the bill built now is the link's amount and the gateway's
  amount, the online-payments user can still sell and nothing else stands in the way. Then it is the order's own
  ``services.pay`` (the online-payments user, the branch's «بوابة الدفع» account, tendered = the bill), and the e-receipt is
  issued as for any till sale. Anything else posts nothing and the link goes to ``review`` (``payments._review``);
* **the online-payments user** is a named account chosen by whoever can change the tables page and collect payments. It needs
  ``sales.create_sales_invoice`` (to sell; checked when chosen, when the link is made and when the callback posts) and
  ``restaurant.prepare_at_payment`` (to make a kitchen dish's plates at payment, as the cashier does); nothing more is needed.
"""

import hashlib

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from .models import Order, OrderKind, OrderStatus

KEY = "restaurant.online_payments_user"
SELL = "sales.create_sales_invoice"
MAKE = "restaurant.prepare_at_payment"  # kitchen.PREPARE_AT_PAYMENT (HG-048)
RECEIVE = "sales.receive_customer_payment"  # payments.services.RECEIVE: to cancel a link, as for any collection

MESSAGES = {
    "ar": {
        "locked": "الطلب ده مقفول للدفع: الضيف بيدفع الحساب من الطاولة دلوقتي. استنى لحد ما الدفع يخلص، أو الغي رابط الدفع من صفحة الطلب الأول.",
        "off": "الدفع من الطاولة مش متاح دلوقتي. الدفع مع الجرسون.",
        "nothing": "مفيش حساب مفتوح على الطاولة دي دلوقتي.",
        "part": "الحساب ده متقسم؛ الدفع مع الجرسون.",
        "deposit": "الحساب ده عليه عربون محجوز؛ الدفع مع الجرسون.",
        "no_box": "الدفع من الطاولة مش متاح في الفرع ده دلوقتي. الدفع مع الجرسون.",
        "gateway": "شركة الدفع مش بترد دلوقتي. جرّب كمان شوية، أو ادفع مع الجرسون.",
        "user_pick": "اختار مستخدم من القايمة، أو سيبه فاضي عشان توقف الدفع من الطاولة.",
        "user_rights": "المستخدم ده لازم يكون شغّال وليه صلاحية البيع.",
        "no_live": "مفيش رابط دفع شغال على الطلب ده.",
    },
    "en": {
        "locked": "This order is locked for payment: the guest is paying the bill from the table now. Wait for the payment to finish, or cancel the payment link on the order page first.",
        "off": "Paying from the table is not available now. Pay with your waiter.",
        "nothing": "There is no open bill at this table now.",
        "part": "This bill is split; pay with your waiter.",
        "deposit": "This bill has a deposit held against it; pay with your waiter.",
        "no_box": "Paying from the table is not available at this branch now. Pay with your waiter.",
        "gateway": "The payment company is not answering now. Try again shortly, or pay with your waiter.",
        "user_pick": "Choose a user from the list, or leave it empty to switch paying from the table off.",
        "user_rights": "This user must be active and allowed to sell.",
        "no_live": "There is no live payment link on this order.",
    },
}


def words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


# --- the online-payments user --------------------------------------------------------------------------------------------

def _can_sell(user):
    return user is not None and user.is_active and user_has_permission(user, SELL)


def online_user():
    """The chosen online-payments user, while it is active and can sell; else None («ادفع» does not show)."""

    from settings_core.models import SystemSetting

    raw = SystemSetting.objects.filter(key=KEY, active=True).values_list("value", flat=True).first() or ""
    raw = str(raw).strip()
    if not raw.isdigit():
        return None
    user = get_user_model().objects.filter(pk=int(raw)).first()
    return user if _can_sell(user) else None


def eligible_users():
    """The users the setting may name: active, and allowed to sell."""

    return [user for user in get_user_model().objects.filter(is_active=True).order_by("username") if user_has_permission(user, SELL)]


@transaction.atomic
def save_online_user(raw, actor, lang="ar"):
    """Choose (or, empty, clear) the online-payments user. Audited. The screen checks who may."""

    from settings_core.models import SystemSetting

    text = words(lang)
    raw = str(raw or "").strip()
    user = None
    if raw:
        if not raw.isdigit():
            raise ValidationError(text["user_pick"])
        user = get_user_model().objects.filter(pk=int(raw)).first()
        if user is None:
            raise ValidationError(text["user_pick"])
        if not _can_sell(user):
            raise ValidationError(text["user_rights"])
    before = SystemSetting.objects.filter(key=KEY).values_list("value", flat=True).first() or ""
    SystemSetting.objects.update_or_create(key=KEY, defaults={
        "value": str(user.pk) if user else "", "data_type": SystemSetting.DataType.STRING, "active": True,
        "description": "HG-121: the user a table's online payment is posted as (empty = paying from the table is off)."})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=actor, module="restaurant", action="save_online_payments_user",
                            object_type="settings_core.SystemSetting", object_id=KEY, before_data={"user": before},
                            after_data={"user": str(user.pk) if user else "", "username": user.username if user else ""})
    return user


# --- whether «ادفع» is offered -------------------------------------------------------------------------------------------

def offered():
    """The capability is on, a gateway is connected and an online-payments user is chosen: the QR page may talk about the bill."""

    from payments import services as pay

    return pay.enabled() and pay.connected() is not None and online_user() is not None


def table_order(table):
    """The table's open order (never a part: a part has no table while it is open), or None."""

    return Order.objects.filter(table=table, status=OrderStatus.OPEN, split_from__isnull=True).select_related("entity", "customer").first()


def payer(order):
    from sales.pos import walk_in_customer

    return order.customer or walk_in_customer()


def blocker(order, lang="ar"):
    """Why this order's bill cannot be paid from the table now (a message for the guest), or "" when it can."""

    from payments import services as pay

    from . import reservations

    text = words(lang)
    if not offered():
        return text["off"]
    if order is None or order.status != OrderStatus.OPEN or not order.lines.filter(voided=False).exists():
        return text["nothing"]
    if order.kind != OrderKind.DINE_IN or not order.table_id:
        return text["off"]
    if order.split_from_id or order.splits.filter(status=OrderStatus.OPEN).exists():
        return text["part"]
    if reservations.bill_deposit(order, payer(order))[1] is not None:
        return text["deposit"]
    try:
        pay.gateway_box(order.entity, lang)
    except ValidationError:
        return text["no_box"]
    return ""


# --- the bill and its fingerprint ----------------------------------------------------------------------------------------

def bill_amount(order, lang="ar"):
    """What ``services.pay`` will post for this order now with no discount, no coupon, no points and no gift card: the rows
    of ``services._bill`` (the running offer, the add-ons, the service and the minimum) through ``_checkout_total``, the
    same two functions ``pay`` uses. May make the service charge's item, exactly as paying does."""

    from . import offers, services

    lines = list(order.lines.select_related("item", "combo__item").filter(voided=False))
    rows, _promotion, _off = services._bill(order, lines, lang, offers.Offer(coupon="", customer=payer(order)))
    return services._checkout_total(rows, 0)


def preview(order, lang="ar"):
    """The bill as the guest sees it (rows and total), writing nothing: ``charges.preview``, as «اطبع الشيك» prints it."""

    from . import charges, offers

    lines = list(order.lines.select_related("item", "combo__item"))
    return charges.preview(order, lines, lang, offer=offers.Offer(customer=payer(order)))


def digest(order):
    """The order's money as it is now: every line (live or voided) with its item, quantity, price, discount and parent, and the
    order's customer, kind, guests, fee and branch. Notes, the kitchen's tickets and «جاهز» are not money and are left out, so
    the kitchen can keep working while the guest pays."""

    parts = [f"o:{order.pk}:{order.customer_id}:{order.kind}:{order.guests}:{order.delivery_fee}:{order.entity_id}:{order.split_from_id}"]
    for line in order.lines.order_by("pk").values_list("pk", "item_id", "quantity", "unit_price", "discount", "voided", "parent_id"):
        parts.append("l:" + ":".join(str(value) for value in line))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def link_payable(link):
    """For ``payments.is_payable``: the order is open and exactly as it was when the link was made."""

    order = Order.objects.filter(pk=link.order_id).first()
    return order is not None and order.status == OrderStatus.OPEN and digest(order) == link.order_digest


# --- the lock ------------------------------------------------------------------------------------------------------------

def refuse_if_locked(order, lang="ar", allow=None):
    """Called by every service that changes an order's bill, after it has locked the order's row: refused while a pay link
    on the order is live (an expired one is expired here first). ``allow``: the live link whose own callback is paying."""

    if allow is not None:
        return  # ``post_link`` checked, under the same lock, that ``allow`` is the order's live link
    from payments.services import live_order_link

    if live_order_link(order) is not None:
        raise ValidationError(words(lang)["locked"])


# --- the guest's «ادفع» --------------------------------------------------------------------------------------------------

@transaction.atomic
def start(table, lang="ar"):
    """«ادفع»: the table's order is locked and a pay link is made for its bill now; returns the link (its ``checkout_url`` is
    where the guest pays). A live link on the order is returned as it is."""

    from payments import services as pay

    text = words(lang)
    found = table_order(table)
    order = Order.objects.select_for_update(of=("self",)).select_related("entity", "customer").get(pk=found.pk) if found else None
    reason = blocker(order, lang)
    if reason:
        raise ValidationError(reason)
    live = pay.live_order_link(order)
    if live is not None:
        return live
    amount = bill_amount(order, lang)
    if amount <= 0:
        raise ValidationError(text["nothing"])
    try:
        return pay.create_order_link(order, online_user(), amount, payer(order), digest(order), lang)
    except ValidationError:
        raise ValidationError(text["gateway"])


# --- staff ---------------------------------------------------------------------------------------------------------------

def links(order):
    """The order's pay links, newest first."""

    from payments.models import PaymentLink

    return PaymentLink.objects.filter(order=order).order_by("-pk")


def state(order):
    """For the order page: the latest link and what it says: "waiting" (live, the order locked), "paid", "review" (the money
    was taken and nothing posted, or a transaction needs a look), "ended" (expired, cancelled or failed); None with no link."""

    from payments.models import LinkStatus
    from payments.services import live_order_link

    live = live_order_link(order)
    link = live or links(order).first()
    if link is None:
        return None
    if link.status == LinkStatus.NEW:
        kind = "waiting"
    elif link.status == LinkStatus.REVIEW or link.transactions.filter(needs_review=True).exists():
        kind = "review"
    elif link.status == LinkStatus.PAID:
        kind = "paid"
    else:
        kind = "ended"
    return {"link": link, "kind": kind}


@transaction.atomic
def cancel(order, user, lang="ar"):
    """Staff cancel the order's live link (``payments.cancel_link``, which needs ``sales.receive_customer_payment``): the order
    is free again. A guest who pays after this is not posted: the transaction is flagged for a person."""

    from payments import services as pay

    locked = Order.objects.select_for_update(of=("self",)).get(pk=order.pk)
    link = pay.live_order_link(locked)
    if link is None:
        raise ValidationError(words(lang)["no_live"])
    return pay.cancel_link(link, user, lang)


# --- the callback --------------------------------------------------------------------------------------------------------

def post_link(link, amount):
    """``payments.services._confirm_order``: post the table's bill for a confirmed callback of ``amount`` on the live ``link``.
    Returns the invoice. Refused (ValidationError, nothing kept by the caller's savepoint) unless the order is open and
    unchanged, the bill now is the link's amount, the online-payments user can still sell and nothing else stands in the way.
    The reasons are in English: they go to the transaction's note, which the gateway screen shows."""

    from . import platforms, reservations, services

    order = Order.objects.select_for_update(of=("self",)).select_related("table", "entity", "customer").get(pk=link.order_id)
    if order.status != OrderStatus.OPEN:
        raise ValidationError(f"the order {order.number} is no longer open")
    if digest(order) != link.order_digest:
        raise ValidationError(f"the order {order.number} changed after the link was made")
    user = link.created_by
    if not _can_sell(user):
        raise ValidationError("the online-payments user can no longer sell")
    if order.splits.filter(status=OrderStatus.OPEN).exists() or platforms.of_order(order) is not None:
        raise ValidationError(f"the order {order.number} cannot be paid from the table now")
    if reservations.bill_deposit(order, link.customer)[1] is not None:
        raise ValidationError(f"the order {order.number} has a held deposit")
    now = bill_amount(order)
    if now != link.amount or amount != link.amount:
        raise ValidationError(f"the bill is now {now}; the link was for {link.amount} and {amount} was paid")
    invoice, change = services.pay(order, user, cashbox=link.cashbox, tendered=amount, customer=link.customer, pay_link=link)
    if invoice.total_amount != amount or change:
        raise ValidationError(f"the posted total {invoice.total_amount} is not the {amount} paid")
    return invoice
