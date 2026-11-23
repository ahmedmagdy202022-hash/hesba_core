"""Restaurants R14 (HG-120): delivery-platform orders by hand («طلبات», «elmenus»), with no platform API (that is X2).

A platform is two existing records, linked by one system setting (no schema):

* a named **customer** (``master_data.Customer``): the platform order is posted **on credit** to it, at the prices of the
  customer's price list (``pricing``), so the drawer never holds money the platform collected;
* a **courier** (T14 ``shipping.Courier``) with its own box of kind ``courier``: handing the order over is ``shipping.ship``,
  the platform's collection is ``shipping.deliver`` (the platform's account is paid from the courier box), and the weekly
  statement is ``shipping.settle`` (the net to the bank, the commission an expense). Those services are called, never changed.

The setting ``restaurant.platforms`` (JSON) is ``{"<customer pk>": {"courier": <courier pk>, "commission": "18"}}``: one courier
per platform and the commission percent the platform takes on the order's total (VAT included), filled in as the shipment's
fee when the order is handed over. A platform belongs to the branch its courier box belongs to (``shipping.ship`` refuses a
courier of another entity than the invoice's), so its orders are opened in that branch.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from config.money import money_round

KEY = "restaurant.platforms"
HUNDRED = Decimal("100")
MESSAGES = {
    "ar": {
        "customer": "اختار عميل المنصة (عميل باسمه، مش العميل النقدي).", "courier": "اختار شركة الشحن اللي بتمثّل المنصة.",
        "commission": "العمولة لازم تكون نسبة من 0 لحد 100.", "courier_taken": "شركة الشحن دي متربطة بمنصة تانية ({name}).",
        "courier_off": "شركة الشحن دي متوقفة.", "number": "اكتب رقم الطلب على المنصة.", "platform": "اختار المنصة.",
        "twice": "رقم الطلب ده متسجّل قبل كده على {name} ({order}).",
        "branch": "{name} متسجّلة على {branch}؛ افتح طلباتها وانت شغال في الفرع ده.",
        "credit": "طلب {name} بيتسجّل آجل على حسابها من غير فلوس في الدرج: {name} بتحصّله وبتسوّيه من صندوقها. سيب المدفوع صفر.",
        "payer": "الطلب ده طلب {name}؛ بيتسجّل على حساب {name} بس.",
        "not_its_order": "حساب {name} لطلبات {name} بس؛ افتح الطلب من «طلب منصة» على شاشة الطاولات.",
        "dispatch": "طلب {name} بيستلمه مندوب {name}؛ مفيش طيار ولا رسوم توصيل من عندنا.",
        "not_paid": "الطلب لازم يتدفع (يتسجّل على المنصة) الأول.", "not_platform": "ده مش طلب منصة.",
        "no_shipment": "الطلب ده لسه ما اتسلّمش لمندوب المنصة.", "handed_done": "الطلب ده اتسلّم لمندوب {name} واتحصّل قبل كده؛ مش هيتسلّم تاني.", "note": "{name} #{number}", "receiver": "يستلمه مندوب {name}",
    },
    "en": {
        "customer": "Choose the platform's customer (a named customer, not the walk-in).", "courier": "Choose the courier that stands for the platform.",
        "commission": "The commission must be a percentage from 0 to 100.", "courier_taken": "This courier is linked to another platform ({name}).",
        "courier_off": "This courier is switched off.", "number": "Type the order's number on the platform.", "platform": "Choose the platform.",
        "twice": "This order number is already recorded for {name} ({order}).",
        "branch": "{name} is set up for {branch}; open its orders while working in that branch.",
        "credit": "A {name} order is posted on {name}'s account with nothing in the drawer: {name} collects it and settles through its box. Leave the paid amount at zero.",
        "payer": "This is a {name} order; it is posted on {name}'s account only.",
        "not_its_order": "{name}'s account is for {name} orders only; open the order from “Platform order” on the tables board.",
        "dispatch": "A {name} order is picked up by {name}'s rider; there is no driver or delivery fee of ours.",
        "not_paid": "The order must be paid (posted on the platform) first.", "not_platform": "This is not a platform order.",
        "no_shipment": "This order has not been handed to the platform's rider yet.", "handed_done": "This order was already handed to {name}'s rider and collected; it is not handed over again.", "note": "{name} #{number}", "receiver": "Picked up by {name}'s rider",
    },
}


def words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


@dataclass(frozen=True)
class Platform:
    customer: object
    courier: object
    commission: Decimal

    @property
    def name(self):
        return self.customer.name

    def commission_on(self, amount):
        """The platform's commission on ``amount`` (the invoice total, VAT included), to the piastre."""

        return money_round(Decimal(amount) * self.commission / HUNDRED)


# ---- the setting --------------------------------------------------------------------------------------------------


def _stored():
    """{customer pk: {"courier": pk, "commission": Decimal}} as saved; anything unreadable is left out (read as no platform)."""

    from settings_core.models import SystemSetting

    raw = SystemSetting.objects.filter(key=KEY, active=True).values_list("value", flat=True).first()
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    out = {}
    for key, row in (data.items() if isinstance(data, dict) else ()):
        try:
            percent = Decimal(str(row.get("commission") or "0"))
            out[int(key)] = {"courier": int(row["courier"]), "commission": percent if percent.is_finite() and 0 <= percent <= HUNDRED else Decimal("0")}
        except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation):
            continue
    return out


def _write(stored, user, action, after):
    from settings_core.models import SystemSetting

    value = json.dumps({str(pk): {"courier": row["courier"], "commission": f"{row['commission'].normalize():f}"} for pk, row in sorted(stored.items())})
    setting, _ = SystemSetting.objects.update_or_create(key=KEY, defaults={
        "value": value, "data_type": SystemSetting.DataType.JSON, "active": True,
        "description": "HG-120: delivery platforms: each platform's customer, its courier and the commission percent."})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="restaurant", action=action, object_type="settings_core.SystemSetting",
                            object_id=str(setting.pk), before_data={}, after_data=after)


def platforms():
    """Every platform whose customer and courier still exist, by name."""

    from master_data.models import Customer
    from shipping.models import Courier

    stored = _stored()
    if not stored:
        return []
    customers = Customer.objects.in_bulk(list(stored))
    couriers = Courier.objects.select_related("cashbox__entity").in_bulk([row["courier"] for row in stored.values()])
    found = [Platform(customers[pk], couriers[row["courier"]], row["commission"]) for pk, row in stored.items()
             if pk in customers and row["courier"] in couriers]
    return sorted(found, key=lambda one: one.name)


def for_customer(customer):
    """The platform ``customer`` stands for, or None (also for no customer)."""

    if customer is None:
        return None
    row = _stored().get(customer.pk)
    if row is None:
        return None
    from shipping.models import Courier

    courier = Courier.objects.select_related("cashbox__entity").filter(pk=row["courier"]).first()
    return Platform(customer, courier, row["commission"]) if courier is not None else None


def active():
    """The platforms an order can be opened for now: an active customer and an active courier."""

    return [one for one in platforms() if one.customer.active and one.courier.active]


def _percent(raw, text):
    try:
        value = Decimal(str(raw if raw not in (None, "") else "0").strip().replace(",", ".").rstrip("%"))
    except InvalidOperation:
        raise ValidationError(text["commission"])
    if not value.is_finite() or not 0 <= value <= HUNDRED:
        raise ValidationError(text["commission"])
    return value.quantize(Decimal("0.01"))


@transaction.atomic
def save_platform(customer, courier, commission, user, lang="ar"):
    """Link ``customer`` and ``courier`` as one platform with its commission (a platform saved again is changed). Audited.
    The screen checks who may (the tables page's own permission)."""

    from sales.pos import WALK_IN_CODE

    text = words(lang)
    if customer is None or not customer.active or customer.customer_code == WALK_IN_CODE:
        raise ValidationError(text["customer"])
    if courier is None:
        raise ValidationError(text["courier"])
    if not courier.active:
        raise ValidationError(text["courier_off"])
    percent = _percent(commission, text)
    stored = _stored()
    for pk, row in stored.items():
        if pk != customer.pk and row["courier"] == courier.pk:
            from master_data.models import Customer

            other = Customer.objects.filter(pk=pk).values_list("name", flat=True).first() or pk
            raise ValidationError(text["courier_taken"].format(name=other))
    before = stored.get(customer.pk)
    stored[customer.pk] = {"courier": courier.pk, "commission": percent}
    _write(stored, user, "save_platform", {"customer": customer.pk, "courier": courier.pk, "commission": str(percent),
                                           "before": {"courier": before["courier"], "commission": str(before["commission"])} if before else {}})
    return Platform(customer, courier, percent)


@transaction.atomic
def remove_platform(customer, user):
    """Unlink a platform; its customer, courier, orders and shipments stay as they are."""

    stored = _stored()
    if customer is None or customer.pk not in stored:
        return False
    row = stored.pop(customer.pk)
    _write(stored, user, "remove_platform", {"customer": customer.pk, "courier": row["courier"], "commission": str(row["commission"])})
    return True


# ---- the order ------------------------------------------------------------------------------------------------------


def _entity_pk(entity_id):
    from entities.services import main_entity

    return entity_id or main_entity().pk


def check_branch(platform, entity, lang="ar"):
    """A platform's orders are in its courier box's branch: ``shipping.ship`` refuses a courier of another entity."""

    if _entity_pk(platform.courier.cashbox.entity_id) != _entity_pk(getattr(entity, "pk", entity)):
        from entities.services import main_entity

        branch = platform.courier.cashbox.entity or main_entity()
        raise ValidationError(words(lang)["branch"].format(name=platform.name, branch=branch.display(lang)))


def note(platform, number, lang="ar"):
    return words(lang)["note"].format(name=platform.name, number=number)[:255]


def number_of(order):
    """The platform's order number, from the order's notes («طلبات #12345»)."""

    text = order.notes or ""
    return text.rsplit("#", 1)[1].strip() if "#" in text else ""


def open_order(user, platform, number, *, address="", phone="", brand=None, lang="ar"):
    """A platform order: a delivery order for the platform's customer with the platform's order number in its notes, no
    delivery fee of ours, and the platform's rider as the address when none is typed. Opened through ``services.open_order``,
    in the platform's branch (its courier box's)."""

    from entities.current import current_entity

    from . import services
    from .models import Order, OrderKind, OrderStatus

    text = words(lang)
    if platform is None:
        raise ValidationError(text["platform"])
    number = str(number or "").strip().lstrip("#").strip()[:40]
    if not number:
        raise ValidationError(text["number"])
    entity = platform.courier.cashbox.entity
    if entity is None:
        from entities.services import main_entity

        entity = main_entity()
    working = current_entity()
    if working is not None:
        check_branch(platform, working, lang)
    marked = note(platform, number, "ar")
    with transaction.atomic():
        # The platform's customer row is locked for the check and the creation, so two clerks typing the same platform
        # number at once cannot both pass the check and open two payable orders (Codex #425).
        type(platform.customer).objects.select_for_update().filter(pk=platform.customer.pk).first()
        taken = Order.objects.filter(customer=platform.customer, notes=marked).exclude(status=OrderStatus.CANCELLED).first()
        if taken is not None:
            raise ValidationError(text["twice"].format(name=platform.name, order=taken.number))
        return services.open_order(user, kind=OrderKind.DELIVERY, customer=platform.customer, address=(address or "").strip() or text["receiver"].format(name=platform.name),
                                   phone=phone, notes=marked, lang=lang, delivery_fee=None, entity=entity, brand=brand)


def check_payer(order, customer, *, tendered, tenders, gift, points, lang="ar"):
    """``services.pay``'s platform rules (trap 16: a platform order posted as a cash sale overstates the drawer and loses the
    commission). Returns the order's platform, or None for any other order (which is then paid exactly as before).

    * an order opened for a platform is paid in that platform's name only, and a platform's name pays only its own orders;
    * it is posted **on credit**: nothing tendered, not paid several ways, no gift card and no points;
    * in the platform's branch (its courier box's)."""

    platform = for_customer(order.customer) if order.customer_id else None
    paying = for_customer(customer) if customer is not None and customer.pk != order.customer_id else None
    text = words(lang)
    if paying is not None:
        raise ValidationError(text["not_its_order"].format(name=paying.name))
    if platform is None:
        return None
    if customer.pk != order.customer_id:
        raise ValidationError(text["payer"].format(name=platform.name))
    if money_round(Decimal(tendered or 0)) != 0 or tenders is not None or gift is not None or str(points or "").strip() not in ("", "0"):
        raise ValidationError(text["credit"].format(name=platform.name))
    check_branch(platform, order.entity_id, lang)
    return platform


def of_order(order):
    """The platform of a paid or open order (its customer's), or None."""

    return for_customer(order.customer) if order.customer_id else None


def shipment(order):
    """The order's latest shipment (handed over, collected, settled...), or None."""

    if not order.invoice_id:
        return None
    from shipping.models import Shipment

    return Shipment.objects.filter(invoice_id=order.invoice_id).select_related("courier", "settlement").order_by("-pk").first()


def _paid_platform_order(order, lang):
    from .models import OrderStatus

    text = words(lang)
    platform = of_order(order)
    if platform is None:
        raise ValidationError(text["not_platform"])
    if order.status != OrderStatus.PAID or not order.invoice_id:
        raise ValidationError(text["not_paid"])
    return platform


def hand_over(order, user, lang="ar"):
    """«سلّمه للمندوب»: ``shipping.ship(invoice, the platform's courier)`` with the platform's order number as the tracking number
    and the commission on the invoice total as the shipment's fee. What is collected defaults to what is still due."""

    from shipping import services as shipping

    from sales.models import SalesInvoice
    from shipping.models import Shipment, ShipmentStatus

    platform = _paid_platform_order(order, lang)
    with transaction.atomic():
        # Under the invoice's lock: an order already collected or settled is not handed over again, or a second shipment
        # would carry the commission a second time (Codex #425). After a return or a loss it may go out again.
        invoice = SalesInvoice.objects.select_for_update().get(pk=order.invoice_id)
        if Shipment.objects.filter(invoice=invoice, status__in=(ShipmentStatus.DELIVERED, ShipmentStatus.SETTLED)).exists():
            raise ValidationError(words(lang)["handed_done"].format(name=platform.name))
        return shipping.ship(invoice, platform.courier, user, {"tracking_number": number_of(order), "fee": str(platform.commission_on(invoice.total_amount))}, lang)


def collected(order, user, lang="ar"):
    """«المنصة حصّلته»: ``shipping.deliver`` on the order's shipment that is with the platform: the platform's account is paid
    from its courier box."""

    from shipping import services as shipping
    from shipping.models import ShipmentStatus

    _paid_platform_order(order, lang)
    found = shipment(order)
    if found is None or found.status != ShipmentStatus.WITH_COURIER:
        raise ValidationError(words(lang)["no_shipment"])
    return shipping.deliver(found, user, lang=lang)
