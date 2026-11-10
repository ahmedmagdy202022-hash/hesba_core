"""R1: the alternatives the till shows for one item (the «بدائل» sheet on a cart line).

Read-only. The sheet lists the items with the same active ingredient that are on the shelf at the till's selling
location (from :func:`pharmacy.services.alternatives`, same strength and form first), and one line naming the drugs of
the reference list with that ingredient the pharmacy does not have. Tapping an alternative adds it to the cart through
the till's own add path in ``pos.js``, so prices, stock, expiry batches, prescriptions and posting are exactly what
they are when the cashier picks that item by hand. Nothing here posts, prices, moves or changes anything.
"""

from decimal import Decimal

from entities import scope as entity_scope
from master_data.models import Customer, Item, Location

from . import services
from .models import DrugReference
from .reference import fold

#: The till's own permission (``sales.pos.pos``): whoever may ring up a sale may see what else is on the shelf.
PERMISSION = "sales.create_sales_invoice"
#: At most this many alternatives in the sheet, and this many reference names on its one line.
SHOWN = 20
NAMES = 3
#: The reference rows read for one ingredient; a real list has a few dozen at most.
REFERENCE_ROWS = 500
#: A stock chip says "only N left" at or under this.
LOW = Decimal("2")

WORDS = {
    "ar": {"open": "بدائل", "title": "بدائل {name}", "intro": "بنفس المادة الفعالة ({ingredient}). المس البديل يتضاف للسلة.",
           "have": "في مخزنك", "same": "نفس التركيز", "other": "تركيز أو شكل تاني", "rx": "بروشتة", "stock": "عندك {n}",
           "low": "باقي {n}", "empty": "مفيش بديل بنفس المادة عندك دلوقتي.", "missing": "مش عندك ({n})",
           "more": "، والباقي", "order": "للطلب من المورد. للإضافة كصنف افتح الصنف من المخزن.", "sep": "، ",
           "foot": "أسعار أصنافك انت، مش سعر القايمة. ده اقتراح والقرار للصيدلي.", "close": "قفل",
           "out": "خلص من المخزن", "only": "الموجود {n} بس", "remove": "شيله؟", "remove_label": "شيل {name} من السلة",
           "added": "اتضاف {alt}. شيل {name} الخالص؟", "error": "مقدرناش نجيب البدائل دلوقتي."},
    "en": {"open": "Alternatives", "title": "Alternatives to {name}", "intro": "Same active ingredient ({ingredient}). Tap one to add it to the cart.",
           "have": "In your stock", "same": "Same strength", "other": "Other strength or form", "rx": "Rx", "stock": "{n} in stock",
           "low": "Only {n} left", "empty": "No alternative with the same ingredient in stock right now.", "missing": "Not in your stock ({n})",
           "more": " and more", "order": "To order from the supplier. To add one as an item, open the item from Inventory.", "sep": ", ",
           "foot": "Your own item prices, not the list's. A suggestion: the pharmacist decides.", "close": "Close",
           "out": "Out of stock", "only": "Only {n} in stock", "remove": "Remove?", "remove_label": "Remove {name} from the cart",
           "added": "Added {alt}. Remove {name}, which is out of stock?", "error": "Could not load the alternatives right now."},
}


def selling_locations():
    """The selling locations the till offers (``sales.pos.pos``), in the current entity."""

    return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True)


def till_location(raw):
    """The till's chosen location when it is one of :func:`selling_locations`, else the till's default one."""

    locations = selling_locations()
    chosen = locations.filter(pk=int(raw)).first() if str(raw or "").isdigit() else None
    return chosen or locations.filter(is_default=True).first() or locations.first()


def _quantity(value):
    return format((value or Decimal("0")).quantize(Decimal("0.001")).normalize(), "f")


def known_to_shop(rows, items):
    """The reference rows that are already one of the shop's ``items``.

    A reference row is "already an item" when its barcode is the barcode of any active item, or when its English or
    Arabic trade name, folded as the reference list folds names (case, hamza forms, ة/ى, digits, spaces), equals the
    folded name of one of ``items`` (the item asked about and the items with the same active ingredient)."""

    names = {fold(item.item_name) for item in items} - {""}
    barcodes = {row["barcode"] for row in rows if row["barcode"]}
    taken = set(Item.objects.filter(active=True, barcode__in=barcodes).values_list("barcode", flat=True)) if barcodes else set()
    return [row for row in rows if (row["barcode"] and row["barcode"] in taken) or fold(row["name_en"]) in names or fold(row["name_ar"]) in names]


def missing_from_shop(drug, items, lang="ar"):
    """(count, up to NAMES names) of the active reference rows with the drug's ingredient that are not one of ``items``,
    the same strength first."""

    if drug is None or not drug.ingredient_key:
        return 0, []
    rows = list(DrugReference.objects.filter(is_active=True, ingredient_key=drug.ingredient_key)
                .values("pk", "name_en", "name_ar", "strength_key", "barcode")[:REFERENCE_ROWS])
    known = {row["pk"] for row in known_to_shop(rows, items)}
    left = [row for row in rows if row["pk"] not in known]
    first, second = ("name_en", "name_ar") if lang == "en" else ("name_ar", "name_en")
    left.sort(key=lambda row: (row["strength_key"] != drug.strength_key, fold(row[first] or row[second]), row["pk"]))
    return len(left), [row[first] or row[second] for row in left[:NAMES]]


def alternatives_for(item, *, location=None, customer=None, lang="ar"):
    """What the till's sheet shows for ``item`` at the till's ``location`` for ``customer`` (both as the till sends them)."""

    from batches.services import stock_on_hand
    from pricing.services import customer_list, list_prices

    lang = "en" if lang == "en" else "ar"
    location = till_location(location)
    customer = Customer.objects.filter(pk=int(customer), active=True).first() if str(customer or "").isdigit() else None
    drug = getattr(item, "drug", None)
    # Asked without a customer, alternatives() reads no price list (one query per row otherwise); the customer's list is
    # applied below in one read, the way the till's own price book prices a line.
    rows = services.alternatives(item, limit=200) if drug is not None else []
    stock = stock_on_hand([item.pk] + [row["item"].pk for row in rows], [location]) if location is not None else {}
    shelf = [row for row in rows if stock.get(row["item"].pk, services.ZERO) > 0][:SHOWN]
    price_list = customer_list(customer) if shelf else None
    prices = list_prices(price_list, [row["item"] for row in shelf]) if price_list is not None else {}
    labels = services.FORM_LABELS[lang]
    count, names = missing_from_shop(drug, [item] + [row["item"] for row in rows], lang)
    return {
        "ok": True,
        "item": {"id": item.pk, "name": item.display_name, "drug": drug is not None, "on_hand": _quantity(stock.get(item.pk)),
                 "ingredient": " ".join(part for part in (drug.active_ingredient, drug.strength) if part) if drug else ""},
        "location": location.pk if location is not None else None,
        "stocked": [{"id": row["item"].pk, "name": row["item"].display_name, "strength": row["drug"].strength, "form": row["drug"].form,
                     "form_label": labels.get(row["drug"].form, ""), "price": str(prices.get(row["item"].pk, row["price"])),
                     "on_hand": _quantity(stock.get(row["item"].pk)), "low": stock.get(row["item"].pk, services.ZERO) <= LOW,
                     "same_strength": row["match"] == "same", "prescription_only": row["drug"].prescription_only} for row in shelf],
        "unstocked": {"count": count, "names": names},
    }
