"""Restaurants R7 (HG-116): the call centre. The caller by phone number, their saved addresses, the delivery zone and «كرر».

Rules, stated once:

* the number is **typed** (caller ID from a phone line needs a telephony integration and is not built). It is normalised
  before it is looked up: Arabic-Indic digits become Latin, then it goes through the one lookup the till and the table bill
  already use (``offers.find_customer``: an exact match by digits, or the only number ending with what was typed, so
  «01001234567», «+20 100 123 4567» and «1001234567» find one customer). Nothing here is a third lookup;
* a new caller is added through the till's own quick-add (``sales.pos_customers.quick_add_customer``) by someone who may
  manage customers, as the till requires; a second call with the same number, spelt any way, finds the card and adds nothing.
  Anyone who takes orders can still open a delivery from a typed phone and address, without a card;
* a customer keeps any number of saved addresses (``CustomerAddress``), at most one of them the default. ``Customer.address``
  stays the card's single line and is only offered to be saved;
* a zone (``DeliveryZone``) belongs to one branch and carries the delivery fee and the usual time. Choosing an address opens
  the delivery with that address on the order (``Order.address`` and ``phone``, which are already there) and the zone's fee in
  the order's own fee box, which still reads exactly as before and can be changed. The fee is billed by the existing
  ``DELIVERY-FEE`` line: no posting changes. A zone of another branch, or one switched off, gives no fee;
* «كرر» opens a delivery and copies a past order's dishes into it **at today's menu prices**, unsent. A dish no longer on the
  menu, or whose choices changed so that they cannot be rebuilt, is skipped and listed. Add-ons and choices go back through
  ``services.add_item`` and combos through ``services.add_combo``, so every R1a and R1b rule applies as for any new line;
* a zone that is used by an address is switched off, never deleted.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from entities.current import current_entity
from entities.services import main_entity
from master_data.models import Customer
from permissions.services import user_has_permission

from . import branches, modifiers, offers, services
from .models import CustomerAddress, DeliveryZone, Order, OrderKind, OrderStatus

MANAGE_PARTIES = "master_data.manage_parties"
CUSTOMER_REPORT = "reports.view_customer_report"
LAST_ORDERS = 5
MAX_MINUTES = 600

MESSAGES = {
    "ar": {
        "phone_short": "اكتب رقم الموبايل (٧ أرقام على الأقل).", "phone_bad": "الرقم ده مش مظبوط؛ اكتب أرقام بس.", "name_needed": "اكتب اسم العميل.",
        "no_manage": "إضافة عميل جديد لصاحب صلاحية العملاء؛ تقدر تفتح الدليفري برقم وعنوان من غير كارت.",
        "customer_gone": "العميل ده مش موجود أو مش شغال.", "address_gone": "العنوان ده مش للعميل ده أو اتشال.",
        "address_needed": "اكتب العنوان.", "zone_foreign": "المنطقة دي مش من مناطق الفرع.", "zone_off": "المنطقة دي مش شغالة.",
        "zone_name": "اكتب اسم المنطقة.", "zone_taken": "فيه منطقة بنفس الاسم في الفرع ده.", "zone_minutes": "الوقت بالدقايق من 1 لحد {max}.",
        "zone_used": "المنطقة دي مستخدمة في عناوين عملاء؛ اقفلها بدل ما تمسحها.",
        "off_menu": "«{item}» مش على المنيو دلوقتي.", "choice_gone": "«{item}» اتغيّرت اختياراته: «{choice}» مش متاحة دلوقتي.",
        "combo_changed": "«{combo}» اتغيّرت مكوّناتها ومقدرناش نرجّعها زي ما كانت.", "skipped": "ما اتكررش:",
        "repeat_done": "اتكرر {count} من الطلب {number} بأسعار النهارده، ولسه ما اتبعتش للمطبخ.",
        "repeat_none": "مفيش أصناف اتكررت من الطلب {number}.", "repeat_gone": "الطلب ده مش من طلبات العميل ده.",
        "opened": "اتفتح طلب الدليفري {number}.", "opened_fee": "اتفتح طلب الدليفري {number} ورسوم التوصيل {fee}.",
        "customer_added": "اتضاف العميل {name}.", "address_saved": "اتحفظ العنوان.", "address_default": "العنوان ده بقى الافتراضي.", "address_off": "اتشال العنوان.",
        "zone_saved": "اتحفظت المنطقة.", "zone_deleted": "اتمسحت المنطقة.",
    },
    "en": {
        "phone_short": "Enter the mobile number (7 digits at least).", "phone_bad": "This number is not valid; digits only.", "name_needed": "Enter the customer's name.",
        "no_manage": "Adding a new customer needs the customers right; you can still open the delivery from a number and an address, with no card.",
        "customer_gone": "This customer does not exist or is not active.", "address_gone": "This address is not this customer's, or it was removed.",
        "address_needed": "Enter the address.", "zone_foreign": "This zone is not one of the branch's zones.", "zone_off": "This zone is not active.",
        "zone_name": "Enter the zone's name.", "zone_taken": "A zone with this name exists in this branch.", "zone_minutes": "The time is in minutes, 1 to {max}.",
        "zone_used": "This zone is used by customers' addresses; switch it off instead of deleting it.",
        "off_menu": "“{item}” is not on the menu now.", "choice_gone": "“{item}” has other choices now: “{choice}” is not available.",
        "combo_changed": "“{combo}” has other parts now and could not be rebuilt as it was.", "skipped": "Not repeated:",
        "repeat_done": "Repeated {count} from order {number} at today's prices; not sent to the kitchen yet.",
        "repeat_none": "Nothing was repeated from order {number}.", "repeat_gone": "This order is not this customer's.",
        "opened": "Delivery order {number} opened.", "opened_fee": "Delivery order {number} opened, delivery fee {fee}.",
        "customer_added": "Customer {name} added.", "address_saved": "Address saved.", "address_default": "This address is now the default.", "address_off": "Address removed.",
        "zone_saved": "Zone saved.", "zone_deleted": "Zone deleted.",
    },
}


def _audit(obj, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="restaurant", action=action, object_type=f"restaurant.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


def _on(value):
    return value not in (False, "", "0", "off", None)


# --- the number --------------------------------------------------------------

def latin_digits(text):
    """Arabic-Indic and Persian digits as Latin ones (the booking page's rule), everything else as it was."""

    from appointments.booking import latin_digits as booking_digits

    return booking_digits(text)


def clean_phone(raw, lang="ar"):
    """The number as stored and searched: Latin digits, trimmed. Refuses fewer than seven digits and anything that is no phone number."""

    from master_data.phones import is_plain_phone

    words = MESSAGES[lang]
    typed = latin_digits(raw).strip()[:50]
    count = sum(1 for ch in typed if ch.isdigit())
    if not typed or count < 7:
        raise ValidationError(words["phone_short"])
    if not is_plain_phone(typed):
        raise ValidationError(words["phone_bad"])
    return typed


def find_caller(raw):
    """The active customer who owns the number as typed, or None: R6's own lookup (the till's directory), after the digits are made Latin."""

    from sales.pos import WALK_IN_CODE

    found = offers.find_customer(latin_digits(raw))
    return None if found is None or found.customer_code == WALK_IN_CODE else found


def new_caller(name, raw_phone, user, lang="ar"):
    """«عميل جديد»: ``(customer, created)``. Needs the customers right, as the till does; a number already on a card (any spelling) returns that card."""

    from sales.pos_customers import quick_add_customer

    words = MESSAGES[lang]
    if not user_has_permission(user, MANAGE_PARTIES):
        raise PermissionDenied("Adding customers needs master_data.manage_parties.")
    phone = clean_phone(raw_phone, lang)
    name = (name or "").strip()[:255]
    if not name:
        raise ValidationError(words["name_needed"])
    found = find_caller(phone)
    if found is not None:
        return found, False
    try:
        customer, created = quick_add_customer(name, phone, user)
    except ValidationError as exc:
        raise ValidationError(words["name_needed"] if exc.messages[:1] == ["name"] else words["phone_bad"])
    if created:
        _audit(customer, user, "call_centre_new_caller", {"code": customer.customer_code, "phone": phone}, event=AuditEventType.CREATE)
    return customer, created


def customer_balance(customer, user):
    """What the customer owes (positive) or is owed, only for someone who may read the customer report, else None."""

    if not user_has_permission(user, CUSTOMER_REPORT):
        return None
    from reports.services import get_customer_balance

    return get_customer_balance(customer)


# --- zones -------------------------------------------------------------------

def zones(queryset=None):
    """The zones of the entity being worked in (the whole group sees every branch's)."""

    return entity_scope.scope(queryset if queryset is not None else DeliveryZone.objects.all(), "entity")


def offered_zones(entity=None):
    """The active zones a screen offers: the entity's when one is chosen, else everything the person may see."""

    rows = zones(DeliveryZone.objects.select_related("entity").filter(active=True))
    return rows.filter(entity=entity) if entity is not None else rows


def _minutes(raw, words):
    raw = str(raw if raw is not None else "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_MINUTES:
        raise ValidationError(words["zone_minutes"].format(max=MAX_MINUTES))
    return int(raw)


@transaction.atomic
def save_zone(data, user, zone=None, lang="ar", entity=None):
    """Create or change a zone. A new one lands in ``entity`` (else the working entity, else the main one); it never changes branch afterwards
    (addresses point at it). ``data``: name_ar, name_en, fee, minutes, active."""

    words = MESSAGES[lang]
    name_ar = (data.get("name_ar") or "").strip()[:80]
    if not name_ar:
        raise ValidationError(words["zone_name"])
    working = current_entity()
    target = zone.entity if zone is not None else (entity or working or main_entity())
    if working is not None and target.pk != working.pk:
        raise ValidationError(services.MESSAGES[lang]["not_this_branch"])
    if DeliveryZone.objects.filter(entity=target, name_ar__iexact=name_ar).exclude(pk=getattr(zone, "pk", None)).exists():
        raise ValidationError(words["zone_taken"])
    values = {"name_ar": name_ar, "name_en": (data.get("name_en") or "").strip()[:80], "fee": services._fee(data.get("fee"), services.MESSAGES[lang]),
              "minutes": _minutes(data.get("minutes"), words), "active": _on(data.get("active", True))}
    created = zone is None
    before = {} if created else {key: str(getattr(zone, key)) if key == "fee" else getattr(zone, key) for key in values}
    zone = zone or DeliveryZone(entity=target)
    for key, value in values.items():
        setattr(zone, key, value)
    zone.save()
    _audit(zone, user, "create_zone" if created else "change_zone", {**values, "fee": str(values["fee"]), "entity": target.pk}, before,
           AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return zone


@transaction.atomic
def delete_zone(zone, user, lang="ar"):
    """Delete a zone nobody uses (a typing mistake). One an address uses is refused: it is switched off instead."""

    zone = DeliveryZone.objects.select_for_update().get(pk=zone.pk)
    if CustomerAddress.objects.filter(zone=zone).exists():
        raise ValidationError(MESSAGES[lang]["zone_used"])
    snapshot = {"name_ar": zone.name_ar, "fee": str(zone.fee), "entity": zone.entity_id}
    _audit(zone, user, "delete_zone", {}, snapshot, AuditEventType.DELETE)
    zone.delete()


# --- addresses ---------------------------------------------------------------

def addresses_of(customer):
    """The customer's active addresses, the default first."""

    return list(CustomerAddress.objects.filter(customer=customer, active=True).select_related("zone", "zone__entity").order_by("-is_default", "pk"))


def _lock_customer(customer, lang="ar"):
    customer = Customer.objects.select_for_update().filter(pk=customer.pk, active=True).first()
    if customer is None:
        raise ValidationError(MESSAGES[lang]["customer_gone"])
    return customer


@transaction.atomic
def save_address(customer, data, user, lang="ar"):
    """Save a new address for the customer. ``data``: label, address, area, landmark, zone (a pk of a zone the person may see, active), is_default.

    The customer's first address is their default; making another the default takes it from the old one, in one step."""

    words = MESSAGES[lang]
    customer = _lock_customer(customer, lang)
    address = (data.get("address") or "").strip()[:255]
    if not address:
        raise ValidationError(words["address_needed"])
    raw_zone = str(data.get("zone") or "").strip()
    zone = None
    if raw_zone:
        zone = zones(DeliveryZone.objects.filter(pk=raw_zone)).first() if raw_zone.isdigit() else None
        if zone is None:
            raise ValidationError(words["zone_foreign"])
        if not zone.active:
            raise ValidationError(words["zone_off"])
    has_default = CustomerAddress.objects.filter(customer=customer, active=True, is_default=True).exists()
    make_default = _on(data.get("is_default")) or not has_default
    if make_default:
        CustomerAddress.objects.filter(customer=customer, is_default=True).update(is_default=False)
    row = CustomerAddress.objects.create(customer=customer, label=(data.get("label") or "").strip()[:60], address=address,
                                         area=(data.get("area") or "").strip()[:80], landmark=(data.get("landmark") or "").strip()[:120],
                                         zone=zone, is_default=make_default)
    _audit(row, user, "add_address", {"customer": customer.pk, "address": address, "zone": zone.pk if zone else None, "default": make_default},
           event=AuditEventType.CREATE)
    return row


def _own_address(customer, address, words):
    row = CustomerAddress.objects.select_for_update().filter(pk=getattr(address, "pk", address), customer=customer, active=True).first()
    if row is None:
        raise ValidationError(words["address_gone"])
    return row


@transaction.atomic
def make_default(customer, address, user, lang="ar"):
    customer = _lock_customer(customer, lang)
    row = _own_address(customer, address, MESSAGES[lang])
    CustomerAddress.objects.filter(customer=customer, is_default=True).exclude(pk=row.pk).update(is_default=False)
    row.is_default = True
    row.save(update_fields=["is_default"])
    _audit(row, user, "default_address", {"customer": customer.pk})
    return row


@transaction.atomic
def remove_address(customer, address, user, lang="ar"):
    """Switch an address off (it is never deleted: orders keep what was printed on them). A default passes to the oldest address left."""

    customer = _lock_customer(customer, lang)
    row = _own_address(customer, address, MESSAGES[lang])
    was_default = row.is_default
    row.active = row.is_default = False
    row.save(update_fields=["active", "is_default"])
    if was_default:
        heir = CustomerAddress.objects.filter(customer=customer, active=True).order_by("pk").first()
        if heir is not None:
            heir.is_default = True
            heir.save(update_fields=["is_default"])
    _audit(row, user, "remove_address", {"customer": customer.pk})
    return row


def address_text(row):
    """What goes on a delivery order for this address: the street, the area and the landmark."""

    return ", ".join(part for part in (row.address, row.area, row.landmark) if part)[:255]


# --- the delivery ------------------------------------------------------------

def zone_for(address, entity):
    """The address's zone as it counts for an order of ``entity``: active, and of that very branch; else None (no fee)."""

    zone = address.zone if address is not None else None
    return zone if zone is not None and zone.active and zone.entity_id == entity.pk else None


@transaction.atomic
def open_delivery(user, *, customer=None, address=None, text="", phone="", fee=None, entity=None, waiter=None, lang="ar"):
    """Open a delivery order. With a saved ``address`` (the customer's, active) the order carries its text and the customer's phone, and the
    fee is the address's zone's (an empty or unusable zone means none), unless ``fee`` is given: the fee box as it was posted, which wins.
    With none, ``text`` and ``phone`` as typed and no card. Everything else is ``services.open_order``. Returns the order."""

    words = MESSAGES[lang]
    if customer is not None:
        customer = _lock_customer(customer, lang)
    if address is not None:
        if customer is None:
            raise ValidationError(words["address_gone"])
        address = _own_address(customer, address, words)
        text = address_text(address)
    working = current_entity()
    target = entity or working or main_entity()
    if working is not None and target.pk != working.pk:
        raise ValidationError(services.MESSAGES[lang]["not_this_branch"])
    zone = zone_for(address, target)
    chosen = fee if fee is not None else (zone.fee if zone is not None else Decimal("0"))
    phone = (latin_digits(phone) or (customer.phone if customer is not None else "") or "").strip()
    return services.open_order(user, kind=OrderKind.DELIVERY, customer=customer, address=text, phone=phone, delivery_fee=chosen, entity=entity,
                               waiter=waiter, lang=lang)


# --- the last orders and «كرر» ------------------------------------------------

def last_orders(customer, limit=LAST_ORDERS):
    """The customer's last paid orders in the entity being worked in (a part of a split bill is not an order of its own), newest first,
    each as {order, summary (the dishes), count, total (the invoice's, VAT included)}."""

    rows = (branches.orders(Order.objects.filter(customer=customer, status=OrderStatus.PAID, split_from__isnull=True))
            .select_related("invoice", "entity").order_by("-opened_at", "-pk")[:limit])
    result = []
    for order in rows:
        dishes = [line for line in order.lines.select_related("item") if not line.voided and not line.parent_id]
        names = []
        for line in dishes:
            label = f"{line.item.item_name} × {line.quantity.normalize():f}"
            names.append(label)
        result.append({"order": order, "summary": "، ".join(names[:4]) + (" …" if len(names) > 4 else ""), "count": len(dishes),
                       "total": order.invoice.total_amount if order.invoice_id else order.total})
    return result


def own_past_order(customer, raw):
    """One of the customer's last orders by a pk as a form sends it (the same list the screen shows), or None."""

    raw = str(raw or "")
    return next((row["order"] for row in last_orders(customer) if str(row["order"].pk) == raw), None) if raw.isdigit() else None


def _rebuilt(line, children, lang):
    """The ``ModifierOption`` pks a past dish line had: each priced add-on's option (by its item), each free choice (by its printed name,
    either language). Raises ``ValidationError`` naming the choice that is no longer offered."""

    words = MESSAGES[lang]
    offered = [option for group in modifiers.groups_of(line.item) for option in group.live_options]
    picked = []
    for child in children:
        option = next((one for one in offered if one.item_id == child.item_id and one.pk not in picked), None)
        if option is None:
            raise ValidationError(words["choice_gone"].format(item=line.item.item_name, choice=child.options or child.item.item_name))
        picked.append(option.pk)
    for token in [part.strip() for part in (line.options or "").split(modifiers.SEPARATOR) if part.strip()]:
        option = next((one for one in offered if not one.item_id and one.pk not in picked and token in (one.name_ar, one.name_en)), None)
        if option is None:
            raise ValidationError(words["choice_gone"].format(item=line.item.item_name, choice=token))
        picked.append(option.pk)
    return picked


def _repeat_dish(order, line, children, user, lang):
    words = MESSAGES[lang]
    item = line.item
    if not item.active or item.item_code in services.OFF_MENU_CODES:
        raise ValidationError(words["off_menu"].format(item=item.item_name))
    options = _rebuilt(line, children, lang)
    services.add_item(order, item, user, quantity=line.quantity, note=line.note, lang=lang, options=options)


def _repeat_combo(order, members, user, lang):
    """One combo taken: the components, in the order they were taken, go back to the slots in the order the combo has now."""

    from . import combos

    words = MESSAGES[lang]
    head = members[0][0]
    combo = head.combo
    slots = list(combo.slots.order_by("sort", "pk").prefetch_related("choices__item"))
    changed = ValidationError(words["combo_changed"].format(combo=combo.label(lang)))
    if len(slots) != len(members):
        raise changed
    choices, options = {}, {}
    for slot, (line, children) in zip(slots, members):
        choice = next((one for one in slot.choices.all() if one.item_id == line.item_id and one.active and one.item.active), None)
        if choice is None:
            raise changed
        choices[slot.pk] = choice.pk
        options[slot.pk] = _rebuilt(line, children, lang)
    first = services.add_combo(order, combo, user, choices, quantity=1, note=head.note, lang=lang, options=options)
    # add_combo writes the note on the head only; each component's own kitchen note («من غير ملح») comes back too
    rebuilt = order.lines.filter(bundle=first.bundle, parent__isnull=True).order_by("pk")
    for line, (taken, _children) in zip(rebuilt, members):
        if line.note != taken.note:
            line.note = taken.note
            line.save(update_fields=["note"])


@transaction.atomic
def repeat_order(order, source, user, lang="ar"):
    """«كرر»: put the dishes of the past order ``source`` on the open ``order``, unsent, at today's prices. Returns ``(count, skipped)``:
    how many dishes (a combo counts once) were added, and the sentences saying what was not and why."""

    words = MESSAGES[lang]
    lines = [line for line in source.lines.select_related("item", "combo").order_by("pk") if not line.voided]
    children = {}
    for line in lines:
        if line.parent_id:
            children.setdefault(line.parent_id, []).append(line)
    count, skipped, bundles = 0, [], {}
    for line in lines:
        if line.parent_id:
            continue
        if line.bundle:
            bundles.setdefault(line.bundle, []).append((line, children.get(line.pk, [])))
            continue
        try:
            with transaction.atomic():
                _repeat_dish(order, line, children.get(line.pk, []), user, lang)
            count += 1
        except ValidationError as exc:
            skipped.append(" ".join(exc.messages))
    for members in bundles.values():
        try:
            with transaction.atomic():
                _repeat_combo(order, members, user, lang)
            count += 1
        except ValidationError as exc:
            skipped.append(" ".join(exc.messages))
    _audit(order, user, "repeat_order", {"from": source.number, "added": count, "skipped": len(skipped)})
    return count, skipped


@transaction.atomic
def repeat_as_delivery(user, source, *, customer, address, fee=None, entity=None, lang="ar"):
    """«كرر» from the call screen: open the delivery for ``customer`` at ``address`` (as ``open_delivery`` does) and repeat ``source`` into it, in one
    step. Returns ``(order, count, skipped)``."""

    order = open_delivery(user, customer=customer, address=address, fee=fee, entity=entity, lang=lang)
    count, skipped = repeat_order(order, source, user, lang)
    return order, count, skipped
