"""R1: the alternatives the till shows for one item (the «بدائل» sheet on a cart line).

Read-only. The sheet lists the items with the same active ingredient that are on the shelf at the till's selling
location (from :func:`pharmacy.services.alternatives`, same strength and form first), and one line naming the drugs of
the reference list with that ingredient the pharmacy does not have. Tapping an alternative adds it to the cart through
the till's own add path in ``pos.js``, so prices, stock, expiry batches, prescriptions and posting are exactly what
they are when the cashier picks that item by hand. Nothing here posts, prices, moves or changes anything.
"""

from decimal import Decimal

from entities import scope as entity_scope
from master_data.models import Customer, Location

from . import services
from .models import DrugInfo
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
           "more": "، والباقي", "order": "تقدر تطلبه من المورد وتضيفه كصنف بعدين.", "sep": "، ",
           "foot": "أسعار أصنافك انت، مش سعر القايمة. ده اقتراح والقرار للصيدلي.", "close": "قفل",
           "out": "خلص من المخزن", "only": "الموجود {n} بس", "remove": "شيله؟", "remove_label": "شيل {name} من السلة",
           "added": "اتضاف {alt}. شيل {name} اللي خلص؟", "error": "مقدرناش نجيب البدائل دلوقتي."},
    "en": {"open": "Alternatives", "title": "Alternatives to {name}", "intro": "Same active ingredient ({ingredient}). Tap one to add it to the cart.",
           "have": "In your stock", "same": "Same strength", "other": "Other strength or form", "rx": "Rx", "stock": "{n} in stock",
           "low": "Only {n} left", "empty": "No alternative with the same ingredient in stock right now.", "missing": "Not in your stock ({n})",
           "more": " and more", "order": "You can order it from the supplier and add it as an item later.", "sep": ", ",
           "foot": "Your own item prices, not the list's. A suggestion: the pharmacist decides.", "close": "Close",
           "out": "Out of stock", "only": "Only {n} in stock", "remove": "Remove?", "remove_label": "Remove {name} from the cart",
           "added": "Added {alt}. Remove {name}, which is out of stock?", "error": "Could not load the alternatives right now."},
}


#: The largest key a request may name (an ``integer`` column on PostgreSQL); a longer number is no row, not a 500.
MAX_PK = 2147483647


def as_pk(raw):
    """``raw`` (a query-string value) as a row key, or None: ASCII digits only, so ``²`` or ``٣`` is no key (``int()``
    would refuse the first and read the second), and never past :data:`MAX_PK`."""

    text = str(raw or "")
    if not (text.isascii() and text.isdecimal()) or len(text) > len(str(MAX_PK)):
        return None
    value = int(text)
    return value if 0 < value <= MAX_PK else None


def drug_item_ids():
    """The active items that carry drug data, in one query: the only cart lines the till asks the sheet about."""

    return list(DrugInfo.objects.filter(item__active=True).order_by("item_id").values_list("item_id", flat=True))


def selling_locations():
    """The selling locations the till offers (``sales.pos.pos``), in the current entity."""

    return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True)


def till_location(raw):
    """The till's chosen location when it is one of :func:`selling_locations`, else the till's default one."""

    locations = selling_locations()
    pk = as_pk(raw)
    chosen = locations.filter(pk=pk).first() if pk is not None else None
    return chosen or locations.filter(is_default=True).first() or locations.first()


def _quantity(value):
    return format((value or Decimal("0")).quantize(Decimal("0.001")).normalize(), "f")


def missing_from_shop(drug, lang="ar"):
    """(count, up to NAMES names) of the reference rows with the drug's ingredient that the pharmacy does not have, the
    same strength first. "Does not have" is the drug page's rule (``picker.unstocked_rows``), so the two screens agree."""

    if drug is None or not drug.ingredient_key:
        return 0, []
    from .picker import unstocked_rows

    left = list(unstocked_rows(drug).values("pk", "name_en", "name_ar", "strength_rank")[:REFERENCE_ROWS])
    first, second = ("name_en", "name_ar") if lang == "en" else ("name_ar", "name_en")
    left.sort(key=lambda row: (row["strength_rank"], fold(row[first] or row[second]), row["pk"]))
    return len(left), [row[first] or row[second] for row in left[:NAMES]]


def alternatives_for(item, *, location=None, customer=None, lang="ar"):
    """What the till's sheet shows for ``item`` at the till's ``location`` for ``customer`` (both as the till sends them)."""

    from batches.services import stock_on_hand
    from pricing.services import customer_list, list_prices

    lang = "en" if lang == "en" else "ar"
    location = till_location(location)
    customer_pk = as_pk(customer)
    customer = Customer.objects.filter(pk=customer_pk, active=True).first() if customer_pk is not None else None
    drug = getattr(item, "drug", None)
    # Asked without a customer, alternatives() reads no price list (one query per row otherwise); the customer's list is
    # applied below in one read, the way the till's own price book prices a line.
    rows = services.alternatives(item, limit=200) if drug is not None else []
    stock = stock_on_hand([item.pk] + [row["item"].pk for row in rows], [location]) if location is not None else {}
    shelf = [row for row in rows if stock.get(row["item"].pk, services.ZERO) > 0][:SHOWN]
    price_list = customer_list(customer) if shelf else None
    prices = list_prices(price_list, [row["item"] for row in shelf]) if price_list is not None else {}
    labels = services.FORM_LABELS[lang]
    count, names = missing_from_shop(drug, lang)
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
