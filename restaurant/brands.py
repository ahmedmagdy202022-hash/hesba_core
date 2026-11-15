"""Restaurants R11 (HG-117): several brands from one kitchen.

Rules, stated once:

* a brand is a **label** on the order («برجر هاوس», «بيتزا نايل»). It never touches money or stock: the bill, the invoice, the
  tax registration, the e-receipt, the kitchen's stock and the till are the company's, one for every brand. Nothing in this module
  reads or writes a posting, a ledger, a stock movement or a cashbox;
* a brand belongs to one branch (entity), like a table or a zone; its name is unique there; at most one brand of a branch is its
  **default** (a database constraint; setting a new default moves it under a lock);
* with **no active brand** in the branch, nothing changes anywhere: no picker, no word on a page, a ticket or a receipt, and the
  order's brand stays empty;
* a takeaway or a delivery takes the brand the person picked (an active brand of the order's own branch, else the order is
  refused); an unchosen one takes the branch's default. A dine-in order always takes the default (a table does not pick a brand).
  A part of a bill keeps its parent's brand and a merged order keeps the receiving order's;
* the waiter's menu on an order shows the brand's categories (and what sits under them); a brand with none shows the whole menu.
  This is a screen filter, not a rule: a dish repeated by «كرر» or added by a crafted post still goes on the order;
* the first brand of a branch becomes its default; the default cannot be switched off while another brand is active (choose
  another default first); switching off the only active brand clears its default and takes the branch back to no brand;
* a brand an order carries is switched off, never deleted.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from entities.current import current_entity
from entities.services import main_entity
from master_data.models import Category

from .models import Brand, Order

MESSAGES = {
    "ar": {
        "name": "اكتب اسم البراند.", "name_taken": "فيه براند بنفس الاسم في الفرع ده.", "sort": "الترتيب لازم يكون رقم.",
        "gone": "البراند ده مش من براندات الفرع أو مش شغال.", "foreign": "البراند ده مش من براندات الفرع اللي شغال فيه.",
        "used": "البراند ده عليه طلبات؛ اقفله بدل ما تمسحه.", "default_off": "ده البراند الافتراضي؛ اختار براند تاني وخليه الافتراضي الأول.",
        "default_delete": "ده البراند الافتراضي؛ اختار براند تاني وخليه الافتراضي الأول.",
        "saved": "اتحفظ البراند.", "deleted": "اتمسح البراند.",
    },
    "en": {
        "name": "Enter the brand's name.", "name_taken": "A brand with this name exists in this branch.", "sort": "The order must be a number.",
        "gone": "This brand is not one of the branch's brands, or it is not active.", "foreign": "This brand is not one of the branch you are working in.",
        "used": "This brand has orders; switch it off instead of deleting it.", "default_off": "This is the default brand; choose another brand as the default first.",
        "default_delete": "This is the default brand; choose another brand as the default first.",
        "saved": "Brand saved.", "deleted": "Brand deleted.",
    },
}


def _on(value):
    return value not in (False, "", "0", "off", None)


def _audit(brand, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="restaurant", action=action, object_type="restaurant.Brand",
                            object_id=str(brand.pk), before_data=before or {}, after_data=after)


# --- reading -----------------------------------------------------------------

def scoped(queryset=None):
    """The brands of the entity being worked in (the whole group sees every branch's)."""

    return entity_scope.scope(queryset if queryset is not None else Brand.objects.all(), "entity")


def exist():
    """Whether any brand was ever set up in what the person may see: the report's «حسب البراند» and the set-up screen's rows."""

    return scoped().exists()


def offered(entity):
    """The active brands of one branch, in the order the set-up screen sorts them: what a picker lists."""

    return list(Brand.objects.filter(entity=entity, active=True))


def default_for(entity):
    """The branch's default brand (active), or None."""

    return Brand.objects.filter(entity=entity, active=True, is_default=True).first()


def choices(entities):
    """For the board's pickers, which list many branches at once: ``[(entity, [brands])]`` for the entities that have an active brand."""

    result = []
    for entity in entities:
        rows = offered(entity)
        if rows:
            result.append((entity, rows))
    return result


def resolve(entity, raw, lang="ar"):
    """The brand a new order of ``entity`` takes. ``raw`` is what was picked (a Brand, a pk as a form sends it, or nothing).

    Nothing picked: the branch's default (None when it has no brand). Something picked that is not an active brand of this very branch
    is refused, so a crafted post can neither reach another branch's brand nor use one that was switched off."""

    if raw is None or raw == "":
        return default_for(entity)
    pk = raw.pk if isinstance(raw, Brand) else str(raw).strip()
    brand = Brand.objects.filter(pk=pk, entity=entity, active=True).first() if str(pk).isdigit() else None
    if brand is None:
        raise ValidationError(MESSAGES[lang]["gone"])
    return brand


def label(order, lang="ar"):
    """The order's brand name as the pieces print it, or ''."""

    return order.brand.label(lang) if order.brand_id else ""


def menu_items(items, brand):
    """The waiter's menu for an order of ``brand``: only the dishes under the brand's categories (a category holds what sits under it).
    A brand with no categories, or no brand, leaves the menu as it was."""

    if brand is None:
        return items
    held = set(brand.categories.values_list("pk", flat=True))
    if not held:
        return items
    from .stations import _category_chain

    parents = dict(Category.objects.values_list("pk", "parent_id"))
    return [item for item in items if any(pk in held for pk in _category_chain(item.category_id, parents))]


def print_block(invoice, lang="ar"):
    """What the printed receipt of a posted invoice says about the brand: ``{"name", "header"}``, or None when the invoice is not a restaurant
    bill or its order carries no brand. Read only: the issuer, the tax registration and the e-receipt are the company's, not the brand's."""

    order = Order.objects.filter(invoice=invoice, brand__isnull=False).select_related("brand").first()
    if order is None:
        return None
    return {"name": order.brand.label(lang), "header": order.brand.header}


# --- the set-up --------------------------------------------------------------

def _target(brand, entity, lang):
    working = current_entity()
    target = brand.entity if brand is not None else (entity or working or main_entity())
    if working is not None and target.pk != working.pk:
        raise ValidationError(MESSAGES[lang]["foreign"])
    return target


@transaction.atomic
def save_brand(data, user, brand=None, lang="ar", entity=None):
    """Create or change a brand. A new one lands in ``entity`` (else the working entity, else the main one) and never changes branch afterwards.
    ``data``: name_ar, name_en, header, sort, active, is_default and ``categories`` (ids)."""

    words = MESSAGES[lang]
    name_ar = (data.get("name_ar") or "").strip()[:60]
    if not name_ar:
        raise ValidationError(words["name"])
    target = _target(brand, entity, lang)
    siblings = list(Brand.objects.select_for_update().filter(entity=target))  # the branch's brands, locked: the default moves safely
    if brand is not None:
        brand = next(one for one in siblings if one.pk == brand.pk)  # as it is now, under the lock (a caller's copy may be stale)
    if any(one.name_ar.casefold() == name_ar.casefold() and one.pk != getattr(brand, "pk", None) for one in siblings):
        raise ValidationError(words["name_taken"])
    raw_sort = str(data.get("sort") or "0").strip()
    if not raw_sort.isdigit() or len(raw_sort) > 6:
        raise ValidationError(words["sort"])
    active = _on(data.get("active", True))
    others_active = [one for one in siblings if one.active and one.pk != getattr(brand, "pk", None)]
    was_default = brand is not None and brand.is_default
    if was_default and not active and others_active:
        raise ValidationError(words["default_off"])
    wanted = [int(raw) for raw in data.get("categories", []) if str(raw).isdigit()]
    categories = list(Category.objects.filter(pk__in=wanted, active=True).order_by("category_code"))
    # the default: asked for, or it already is, or the branch has no other active default (its first brand, its only one); never an inactive one
    mine = getattr(brand, "pk", None)
    default = active and (_on(data.get("is_default", False)) or was_default or not any(one.is_default and one.active and one.pk != mine for one in siblings))
    values = {"name_ar": name_ar, "name_en": (data.get("name_en") or "").strip()[:60], "header": (data.get("header") or "").strip()[:80],
              "sort": int(raw_sort), "active": active, "is_default": default}
    created = brand is None
    before = {} if created else {**{key: getattr(brand, key) for key in values}, "categories": sorted(brand.categories.values_list("pk", flat=True))}
    if default:
        Brand.objects.filter(entity=target, is_default=True).exclude(pk=getattr(brand, "pk", None)).update(is_default=False)
    brand = brand or Brand(entity=target)
    for key, value in values.items():
        setattr(brand, key, value)
    brand.save()
    brand.categories.set(categories)
    _audit(brand, user, "create_brand" if created else "change_brand", {**values, "entity": target.pk, "categories": sorted(category.pk for category in categories)},
           before, AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return brand


@transaction.atomic
def delete_brand(brand, user, lang="ar"):
    """Delete a brand nothing uses (a typing mistake). One an order carries is refused: it is switched off instead."""

    words = MESSAGES[lang]
    siblings = list(Brand.objects.select_for_update().filter(entity=brand.entity_id))
    brand = next(one for one in siblings if one.pk == brand.pk)
    if Order.objects.filter(brand=brand).exists():
        raise ValidationError(words["used"])
    if brand.is_default and any(one.active and one.pk != brand.pk for one in siblings):
        raise ValidationError(words["default_delete"])
    snapshot = {"name_ar": brand.name_ar, "entity": brand.entity_id}
    _audit(brand, user, "delete_brand", {}, snapshot, AuditEventType.DELETE)
    brand.delete()
