"""T14 (MARKET-PARITY): shipping companies and cash on delivery (HG-061, offline part).

* ``ship``: a posted invoice goes out with a courier; the amount to collect defaults to what is still due on it.
* ``deliver``: the courier collected it. The customer's payment is recorded into the courier's box (sales service,
  called, not changed), so the customer owes nothing and the money is a receivable from the courier.
* ``settle``: the courier hands over what it collected for some delivered parcels, less its fees. The net is a
  transfer from its box to a bank or cashbox; the fees are an expense paid from its box.
* ``came_back``: the parcel returned; nothing was collected, and the goods come back through the ordinary return.
* ``lose``: the courier lost it. What it should have collected is recorded as collected and written off at once as
  an expense with the reason, so the customer owes nothing and the loss shows in the expenses.

A customer payment that belongs to a shipment is undone only from the shipments screen (``undo_delivery``).
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from cashboxes.models import Cashbox, CashboxKind
from config.money import money_round
from config.numbering import next_in_series
from permissions.services import user_has_permission

from .models import Courier, Settlement, Shipment, ShipmentStatus

ZERO = Decimal("0.00")
SHIP = "sales.create_sales_invoice"
COLLECT = "sales.receive_customer_payment"
SETTLE = "cashboxes.move_cash"

MESSAGES = {
    "ar": {
        "name": "اكتب اسم شركة الشحن.", "number": "الرقم ده مش مفهوم.", "negative": "المبلغ مينفعش يبقى بالسالب.", "not_posted": "الفاتورة لازم تكون مترحّلة.",
        "more": "المبلغ المطلوب تحصيله أكتر من الباقي على الفاتورة ({left}).", "state": "الشحنة حالتها «{state}»؛ الخطوة دي مش ليها.", "date": "التاريخ مش مفهوم.",
        "reason": "اكتب السبب.", "none": "اختار شحنة واحدة على الأقل اتسلّمت ولسه ماتسوّتش.", "fees": "مصاريف الشحن أكتر من اللي اتحصّل.",
        "other_courier": "كل الشحنات لازم تكون مع نفس شركة الشحن.", "short": "اللي مع شركة الشحن ({have}) أقل من المطلوب تسويته.",
        "paid": "تحصيل شحنة {number} عن طريق {courier}", "lost": "شحنة {number} ضاعت مع {courier}: {reason}", "fee": "مصاريف شحن {courier} — تسوية {count} شحنة",
        "settle": "تسوية {courier}: {count} شحنة", "active": "شركة الشحن دي متوقفة.", "dest": "اختار الخزنة أو البنك اللي هتستلم فيه.",
        "twice": "الفاتورة دي معاها شحنة لسه مفتوحة.", "entity": "شركة الشحن دي متسجّلة لكيان تاني غير كيان الفاتورة أو الخزنة.",
        "shortfall": "فرق مصاريف شحن {courier} — تسوية {count} شحنة",
    },
    "en": {
        "name": "Type the shipping company's name.", "number": "This number is not understood.", "negative": "The amount cannot be negative.",
        "not_posted": "The invoice must be posted.", "more": "The amount to collect is more than is left on the invoice ({left}).",
        "state": "This shipment is “{state}”; this step does not apply.", "date": "The date is not understood.", "reason": "Type the reason.",
        "none": "Choose at least one delivered shipment not settled yet.", "fees": "The shipping fees are more than what was collected.",
        "other_courier": "All shipments must be with the same courier.", "short": "What the courier holds ({have}) is less than what is being settled.",
        "paid": "Collection of shipment {number} through {courier}", "lost": "Shipment {number} lost by {courier}: {reason}",
        "fee": "{courier} shipping fees — settlement of {count} shipments", "settle": "Settlement with {courier}: {count} shipments",
        "active": "This courier is switched off.", "dest": "Choose the cashbox or bank that receives it.", "twice": "This invoice already has an open shipment.",
        "entity": "This courier is set up for another entity than the invoice's or the cashbox's.",
        "shortfall": "{courier} shipping fees beyond what it collected — settlement of {count} shipments",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _entity_pk(entity_id):
    """Records without an entity belong to the main one."""

    from entities.services import main_entity

    return entity_id or main_entity().pk


def scoped(queryset):
    """Shipments of the entity being worked in (HG-034), through their invoice (Codex #272)."""

    from entities import scope as entity_scope

    return entity_scope.scope(queryset, "invoice__" + entity_scope.SALES_INVOICE)


def couriers(queryset=None):
    """Couriers of the entity being worked in: each courier's box, and so its collections, is one entity's (Codex #272)."""

    from entities import scope as entity_scope

    return entity_scope.scope(Courier.objects.all() if queryset is None else queryset, "cashbox__" + entity_scope.CASHBOX)


def can_ship(user):
    return user_has_permission(user, SHIP)


def _need(user, code):
    if not user_has_permission(user, code):
        raise PermissionDenied(f"This needs {code}.")


def _amount(raw, words, default=None):
    text = str(raw if raw is not None else "").replace(",", "").strip()
    if not text:
        return default
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValidationError(words["number"])
    if value < 0:
        raise ValidationError(words["negative"])
    return money_round(value)


def _day(raw, words):
    from datetime import date

    text = str(raw or "").strip()
    if not text:
        return timezone.localdate()
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValidationError(words["date"])


def _audit(user, action, obj, after):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="shipping", action=action, object_type=f"shipping.{obj.__class__.__name__}",
                            object_id=str(obj.pk), before_data={}, after_data=after)


def _state(shipment, allowed, words, lang):
    if shipment.status not in allowed:
        from settings_core.display_labels import choice_label

        raise ValidationError(words["state"].format(state=choice_label(shipment, "status", lang)))


def _locked(shipment):
    return Shipment.objects.select_for_update(of=("self",)).select_related("invoice__customer", "courier__cashbox").get(pk=shipment.pk)


def _expense_category():
    from expenses.models import ExpenseCategory

    return ExpenseCategory.objects.filter(code="transport", active=True).first() or ExpenseCategory.objects.filter(active=True).order_by("pk").first()


def _payment(number_note, day, customer, box, amount, user):
    from sales.models import CustomerPayment
    from sales.services import record_customer_payment

    for _attempt in range(5):
        try:
            with transaction.atomic():
                number = next_in_series(CustomerPayment, "payment_number", "COD-")
                return record_customer_payment(number, day, customer, box, amount, user=user, notes=number_note)
        except IntegrityError:
            continue
    raise ValidationError("Could not allocate a payment number; try again.")


# ---- couriers ----

@transaction.atomic
def save_courier(data, user, courier=None, lang="ar"):
    _need(user, SETTLE)
    words = _words(lang)
    name = str(data.get("name") or "").strip()[:120]
    if not name:
        raise ValidationError(words["name"])
    fee = _amount(data.get("fee_per_parcel"), words, ZERO)
    if courier is None:
        from entities.current import current_entity
        from entities.services import main_entity
        from settings_core.models import ClientProfile

        # Codex #272: the box belongs to the entity being worked in, so what the courier collects lands in that entity's books,
        # and it is in the company's currency, so settling into the company's cashboxes is a same-currency transfer.
        currency = getattr(ClientProfile.get_active(), "default_currency", None) or "EGP"
        code = next_in_series(Cashbox, "cashbox_code", "COURIER-")
        box = Cashbox.objects.create(cashbox_code=code, name_ar=f"تحصيلات {name}", name_en=f"{name} collections", kind=CashboxKind.COURIER,
                                     entity=current_entity() or main_entity(), currency=currency)
        courier = Courier.objects.create(name=name, phone=str(data.get("phone") or "").strip()[:50], fee_per_parcel=fee, cashbox=box)
    else:
        courier.name, courier.phone, courier.fee_per_parcel = name, str(data.get("phone") or "").strip()[:50], fee
        courier.active = data.get("active") in (True, "1", "on")
        courier.save()
    return courier


def held(courier):
    from cashboxes.services import get_cashbox_balance

    return get_cashbox_balance(courier.cashbox)


# ---- a shipment's life ----

@transaction.atomic
def ship(invoice, courier, user, data=None, lang="ar"):
    from sales.models import SalesInvoice

    _need(user, SHIP)
    words = _words(lang)
    data = data or {}
    invoice = SalesInvoice.objects.select_for_update().select_related("selling_location").get(pk=invoice.pk)
    if invoice.status != "posted":
        raise ValidationError(words["not_posted"])
    if not courier.active:
        raise ValidationError(words["active"])
    if _entity_pk(courier.cashbox.entity_id) != _entity_pk(invoice.selling_location.entity_id):
        raise ValidationError(words["entity"])  # Codex #272: the collection is credited in the courier box's entity
    if Shipment.objects.filter(invoice=invoice, status=ShipmentStatus.WITH_COURIER).exists():
        raise ValidationError(words["twice"])
    # Codex #272: what earlier shipments of this invoice collected, and what posted returns took off its debt, are not due
    # any more (neither a payment nor a return touches remaining_due).
    from sales.models import SalesReturn, SalesReturnStatus

    earlier = Shipment.objects.filter(invoice=invoice, payment__isnull=False).aggregate(total=Sum("cod_amount"))["total"] or ZERO
    returned = SalesReturn.objects.filter(source_invoice=invoice, status=SalesReturnStatus.POSTED).aggregate(total=Sum("due_amount"))["total"] or ZERO
    left = max(ZERO, invoice.remaining_due - earlier - returned)
    cod = _amount(data.get("cod_amount"), words, left)
    if cod > left:
        raise ValidationError(words["more"].format(left=f"{left:,.2f}"))
    shipment = Shipment.objects.create(invoice=invoice, courier=courier, tracking_number=str(data.get("tracking_number") or "").strip()[:80], cod_amount=cod,
                                       fee=_amount(data.get("fee"), words, courier.fee_per_parcel), shipped_on=_day(data.get("shipped_on"), words), created_by=user)
    _audit(user, "ship", shipment, {"invoice": invoice.invoice_number, "courier": courier.name, "cod": str(cod)})
    return shipment


@transaction.atomic
def deliver(shipment, user, day=None, lang="ar"):
    _need(user, COLLECT)
    words = _words(lang)
    shipment = _locked(shipment)
    _state(shipment, (ShipmentStatus.WITH_COURIER,), words, lang)
    day = _day(day, words)
    if shipment.cod_amount > 0:
        note = words["paid"].format(number=shipment.invoice.invoice_number, courier=shipment.courier.name)
        shipment.payment = _payment(note, day, shipment.invoice.customer, shipment.courier.cashbox, shipment.cod_amount, user)
    shipment.status, shipment.closed_on = ShipmentStatus.DELIVERED, day
    shipment.save(update_fields=["payment", "status", "closed_on"])
    _audit(user, "deliver", shipment, {"cod": str(shipment.cod_amount)})
    return shipment


@transaction.atomic
def undo_delivery(shipment, user, reason, lang="ar"):
    """A delivery marked by mistake, before the courier settled it: the payment is cancelled with its reversal rows."""

    from sales.services import cancel_customer_payment

    _need(user, COLLECT)
    words = _words(lang)
    shipment = _locked(shipment)
    _state(shipment, (ShipmentStatus.DELIVERED,), words, lang)
    if not str(reason or "").strip():
        raise ValidationError(words["reason"])
    payment = shipment.payment
    shipment.payment, shipment.status, shipment.closed_on = None, ShipmentStatus.WITH_COURIER, None
    shipment.save(update_fields=["payment", "status", "closed_on"])  # unlinked first: the payment guard lets it go
    if payment is not None:
        cancel_customer_payment(payment.pk, user, str(reason).strip())
    _audit(user, "undo_delivery", shipment, {"reason": str(reason).strip()})
    return shipment


@transaction.atomic
def came_back(shipment, user, note="", day=None, lang="ar"):
    _need(user, SHIP)
    words = _words(lang)
    shipment = _locked(shipment)
    _state(shipment, (ShipmentStatus.WITH_COURIER,), words, lang)
    shipment.status, shipment.closed_on, shipment.note = ShipmentStatus.RETURNED, _day(day, words), str(note or "").strip()[:255]
    shipment.save(update_fields=["status", "closed_on", "note"])
    _audit(user, "came_back", shipment, {"note": shipment.note})
    return shipment


@transaction.atomic
def lose(shipment, user, reason, day=None, lang="ar"):
    from expenses.services import record_expense

    _need(user, COLLECT)
    words = _words(lang)
    shipment = _locked(shipment)
    _state(shipment, (ShipmentStatus.WITH_COURIER,), words, lang)
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError(words["reason"])
    day = _day(day, words)
    text = words["lost"].format(number=shipment.invoice.invoice_number, courier=shipment.courier.name, reason=reason)
    if shipment.cod_amount > 0:
        shipment.payment = _payment(text, day, shipment.invoice.customer, shipment.courier.cashbox, shipment.cod_amount, user)
        shipment.loss = record_expense(category=_expense_category(), cashbox=shipment.courier.cashbox, amount=shipment.cod_amount, expense_date=day,
                                       description=text[:255], user=user, payee=shipment.courier.name)
    shipment.status, shipment.closed_on, shipment.note = ShipmentStatus.LOST, day, reason[:255]
    shipment.save(update_fields=["payment", "loss", "status", "closed_on", "note"])
    _audit(user, "lose", shipment, {"reason": reason, "cod": str(shipment.cod_amount)})
    return shipment


@transaction.atomic
def settle(shipments, destination, user, data=None, lang="ar"):
    """The courier hands over what it collected for ``shipments``, less its fees (the shipments' own, unless typed)."""

    from cashboxes.models import CashboxOperationType
    from cashboxes.services import create_cashbox_operation
    from expenses.services import record_expense

    _need(user, SETTLE)
    words = _words(lang)
    data = data or {}
    ids = sorted({getattr(row, "pk", row) for row in shipments})
    rows = list(scoped(Shipment.objects.select_for_update(of=("self",))).select_related("courier__cashbox").filter(
        pk__in=ids, status=ShipmentStatus.DELIVERED, settlement__isnull=True).order_by("pk"))  # Codex #272: the entity's own only
    if not rows or len(rows) != len(ids):
        raise ValidationError(words["none"])
    courier = rows[0].courier
    if any(row.courier_id != courier.pk for row in rows):
        raise ValidationError(words["other_courier"])
    if destination is None or destination.kind in (CashboxKind.COURIER, CashboxKind.CHEQUES_IN, CashboxKind.CHEQUES_OUT):
        raise ValidationError(words["dest"])
    if _entity_pk(destination.entity_id) != _entity_pk(courier.cashbox.entity_id):
        raise ValidationError(words["entity"])
    collected = money_round(sum((row.cod_amount for row in rows), ZERO))
    fees = _amount(data.get("fees"), words, money_round(sum((row.fee for row in rows), ZERO)))
    have = held(courier)
    if have < collected:
        raise ValidationError(words["short"].format(have=f"{have:,.2f}"))
    day = _day(data.get("settled_on"), words)
    settlement = Settlement.objects.create(courier=courier, settled_on=day, destination=destination, collected=collected, fees=fees,
                                           net=collected - fees, created_by=user)
    count = len(rows)
    if settlement.net > 0:
        settlement.transfer = create_cashbox_operation(f"SET-{settlement.pk}", day, CashboxOperationType.TRANSFER, settlement.net,
                                                       words["settle"].format(courier=courier.name, count=count), user,
                                                       source_cashbox=courier.cashbox, destination_cashbox=destination)
    elif settlement.net < 0:
        # Codex #272: fees above what was collected (a prepaid parcel, a small COD) are paid in from the chosen box first,
        # so the fee expense still leaves the courier's box and that box ends at what it held before.
        settlement.transfer = create_cashbox_operation(f"SET-{settlement.pk}", day, CashboxOperationType.TRANSFER, -settlement.net,
                                                       words["shortfall"].format(courier=courier.name, count=count), user,
                                                       source_cashbox=destination, destination_cashbox=courier.cashbox)
    if fees > 0:
        settlement.fee_expense = record_expense(category=_expense_category(), cashbox=courier.cashbox, amount=fees, expense_date=day,
                                                description=words["fee"].format(courier=courier.name, count=count)[:255], user=user, payee=courier.name)
    settlement.save(update_fields=["transfer", "fee_expense"])
    Shipment.objects.filter(pk__in=[row.pk for row in rows]).update(status=ShipmentStatus.SETTLED, settlement=settlement)
    _audit(user, "settle", settlement, {"courier": courier.name, "collected": str(collected), "fees": str(fees), "shipments": count})
    return settlement


def courier_rows():
    """Each courier with what it holds and how many parcels are out with it."""

    out = dict(Shipment.objects.filter(status=ShipmentStatus.WITH_COURIER).values("courier").annotate(n=Sum("cod_amount")).values_list("courier", "n"))
    return [{"courier": courier, "held": held(courier), "out": out.get(courier.pk) or ZERO} for courier in couriers().select_related("cashbox")]
