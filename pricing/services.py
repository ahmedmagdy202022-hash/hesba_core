"""PRICE-001: which price the sales screens suggest, and how lists are kept.

Resolution for an item sold to a customer:
1. the customer's active price list names the item -> that price;
2. the list has an ``adjust_percent`` -> retail price adjusted by it;
3. otherwise (no list, inactive list, or capability off) -> the retail price
   (``Item.default_sale_price``).

The result is only a suggestion filled into the form; posting reads the price
on the invoice line exactly as before.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from master_data.models import Customer, Item
from settings_core.capabilities import capability_enabled

from .models import CustomerPriceList, PriceList, PriceListItem


HUNDRED = Decimal("100")


def adjusted(retail, percent):
    return money_round(Decimal(retail) * (HUNDRED + Decimal(percent)) / HUNDRED)


def customer_list(customer):
    if customer is None or not capability_enabled("price_lists"):
        return None
    link = CustomerPriceList.objects.select_related("price_list").filter(customer=customer).first()
    return link.price_list if link and link.price_list.active else None


def price_for(item, customer=None, price_list=None):
    """The suggested unit price of ``item`` for ``customer`` (or an explicit list)."""

    price_list = price_list or customer_list(customer)
    if price_list is None:
        return money_round(item.default_sale_price)
    explicit = PriceListItem.objects.filter(price_list=price_list, item=item).values_list("price", flat=True).first()
    if explicit is not None:
        return money_round(explicit)
    return adjusted(item.default_sale_price, price_list.adjust_percent)


def list_prices(price_list, items=None):
    """{item_id: price} for every active item on ``price_list``."""

    items = items if items is not None else Item.objects.filter(active=True).only("id", "default_sale_price")
    explicit = dict(PriceListItem.objects.filter(price_list=price_list).values_list("item_id", "price"))
    return {
        item.pk: money_round(explicit[item.pk]) if item.pk in explicit else adjusted(item.default_sale_price, price_list.adjust_percent)
        for item in items
    }


def price_book():
    """Everything the sales screens need to price client-side, or None when off.

    {"customers": {customer_id: list_id}, "lists": {list_id: {"name": ..., "prices": {item_id: "12.50"}}}}
    Only lists some customer actually uses are included.
    """

    if not capability_enabled("price_lists"):
        return None
    links = dict(
        CustomerPriceList.objects.filter(price_list__active=True, customer__active=True).values_list("customer_id", "price_list_id")
    )
    if not links:
        return None
    items = list(Item.objects.filter(active=True).only("id", "default_sale_price"))
    lists = {}
    for price_list in PriceList.objects.filter(pk__in=set(links.values())):
        lists[str(price_list.pk)] = {
            "name_ar": price_list.name_ar,
            "name_en": price_list.name_en or price_list.name_ar,
            "prices": {str(item_id): str(price) for item_id, price in list_prices(price_list, items).items()},
        }
    return {"customers": {str(customer): str(price_list) for customer, price_list in links.items()}, "lists": lists}


def _audit(user, action, price_list, before=None, after=None, reason=""):
    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE if before is not None else AuditEventType.CREATE,
        actor=user if user is not None and user.is_authenticated else None,
        module="pricing",
        action=action,
        object_type="pricing.PriceList",
        object_id=str(price_list.pk),
        before_data=before or {},
        after_data=after or {},
        reason=reason,
    )


@transaction.atomic
def save_price_list(*, user, code, name_ar, name_en="", adjust_percent=Decimal("0"), active=True, instance=None):
    code = (code or "").strip().upper()
    if not code or not (name_ar or "").strip():
        raise ValidationError("A price list needs a code and an Arabic name.")
    adjust_percent = Decimal(adjust_percent or 0)
    if not Decimal("-100") <= adjust_percent <= Decimal("1000"):
        raise ValidationError("The adjustment must be between -100% and +1000%.")
    if PriceList.objects.filter(code=code).exclude(pk=getattr(instance, "pk", None)).exists():
        raise ValidationError("Another price list already uses this code.")
    before = None
    if instance is None:
        instance = PriceList(created_by=user if user is not None and user.is_authenticated else None)
    else:
        before = {"code": instance.code, "name_ar": instance.name_ar, "adjust_percent": str(instance.adjust_percent), "active": instance.active}
    # Deactivating is allowed even with customers on it: they fall back to retail.
    instance.code, instance.name_ar, instance.name_en = code, name_ar.strip(), (name_en or "").strip()
    instance.adjust_percent, instance.active = adjust_percent, bool(active)
    instance.full_clean()
    instance.save()
    _audit(user, "save_price_list", instance, before, {"code": instance.code, "name_ar": instance.name_ar, "adjust_percent": str(adjust_percent), "active": instance.active})
    return instance


@transaction.atomic
def set_item_prices(price_list, prices, user):
    """Apply {item: Decimal or None}. None removes the explicit price (back to the rule)."""

    changed = {}
    for item, price in prices.items():
        current = PriceListItem.objects.filter(price_list=price_list, item=item).first()
        if price is None:
            if current is not None:
                current.delete()
                changed[item.item_code] = [str(current.price), None]
            continue
        price = money_round(price)
        if price < 0:
            raise ValidationError("A price cannot be negative.")
        if current is None:
            PriceListItem.objects.create(price_list=price_list, item=item, price=price)
            changed[item.item_code] = [None, str(price)]
        elif current.price != price:
            changed[item.item_code] = [str(current.price), str(price)]
            current.price = price
            current.save(update_fields=["price", "updated_at"])
    if changed:
        _audit(user, "set_item_prices", price_list, {"prices": {code: pair[0] for code, pair in changed.items()}}, {"prices": {code: pair[1] for code, pair in changed.items()}})
    return len(changed)


@transaction.atomic
def assign_customers(price_list, customers, user):
    """Put exactly ``customers`` on ``price_list`` (moving them from any other list)."""

    wanted = {customer.pk for customer in customers}
    current = set(CustomerPriceList.objects.filter(price_list=price_list).values_list("customer_id", flat=True))
    removed = current - wanted
    CustomerPriceList.objects.filter(price_list=price_list, customer_id__in=removed).delete()
    for customer_id in wanted - current:
        CustomerPriceList.objects.update_or_create(customer_id=customer_id, defaults={"price_list": price_list})
    if removed or wanted - current:
        codes = dict(Customer.objects.filter(pk__in=current | wanted).values_list("pk", "customer_code"))
        _audit(user, "assign_customers", price_list, {"customers": sorted(codes[pk] for pk in current)}, {"customers": sorted(codes[pk] for pk in wanted)})
    return len(wanted)
