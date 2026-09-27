import json

from django.db import transaction

from audit.models import AuditEventType, AuditLog
from master_data.models import Item

from .symbology import internal_ean13


@transaction.atomic
def assign_missing_barcodes(user):
    """Give every active item without a barcode a shop-internal EAN-13.

    Codes come from the item's id, so they are stable and never collide with
    each other; a code that some other item already carries is skipped forward.
    Returns the number of items updated.
    """

    taken = set(Item.objects.exclude(barcode="").values_list("barcode", flat=True))
    updated = []
    for item in Item.objects.select_for_update().filter(active=True, barcode="").order_by("pk"):
        sequence = item.pk
        code = internal_ean13(sequence)
        while code in taken:
            sequence += 1_000_000
            code = internal_ean13(sequence)
        item.barcode = code
        item.save(update_fields=["barcode", "updated_at"])
        taken.add(code)
        updated.append({"item_id": item.pk, "barcode": code})
    if updated:
        AuditLog.objects.create(
            event_type=AuditEventType.UPDATE,
            actor=user,
            module="barcode",
            action="assign_missing_barcodes",
            object_type="Item",
            object_id=",".join(str(row["item_id"]) for row in updated)[:100],
            after_data={"items": updated},
        )
    return len(updated)


def item_catalog(sale_prices=True, purchase_prices=False):
    """What the invoice forms need to find an item from a scan, in one list.

    Never includes cost. Purchase prices only for the purchase form.
    """

    rows = []
    for item in Item.objects.filter(active=True).only("id", "item_code", "barcode", "item_name", "size", "color", "default_sale_price", "default_purchase_price"):
        row = {"id": item.pk, "code": item.item_code, "barcode": item.barcode, "label": item.search_label}
        if sale_prices:
            row["price"] = str(item.default_sale_price)
        if purchase_prices:
            row["price"] = str(item.default_purchase_price)
        rows.append(row)
    return rows


SCAN_WORDS = {
    "ar": {"added": "اتضاف: ", "not_found": "مفيش صنف بالكود ده: ", "full": "وصلت لأقصى عدد سطور في الفاتورة."},
    "en": {"added": "Added: ", "not_found": "No item with this code: ", "full": "The invoice has reached its line limit."},
}


def scan_words(lang):
    return json.dumps(SCAN_WORDS["en" if lang == "en" else "ar"], ensure_ascii=False)
