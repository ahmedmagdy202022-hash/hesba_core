"""M8 (HG-088): what the demo shows on «الأسرّة».

Two wards with their beds, one patient in a bed with a couple of charges, and one stay that ended two days ago
with its bill waiting as a draft. Everything goes through ``medical.inpatient``, the services the screens run:
nothing is posted, so the draft is there for the visitor to open and post.

Only where «الإقامة والأسرّة» is on: a medical centre (suggested there) and the demo's clinic, which
``config.demo_templates.demo_capabilities`` switches on demo-only (the landing page offers the clinic, and a real
clinic keeps it off until its owner switches it on).

Idempotent: ``DEMO-BED-01`` (the first room's daily service) is the sentinel, created in the same transaction.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

SENTINEL = "DEMO-BED-01"
CATALOGS = ("medical.clinic", "medical.medical_center")
#: (code, name, unit, price): the daily stay services, then the operation.
SERVICES = ((SENTINEL, "إقامة يوم — غرفة مزدوجة", "يوم", "600.00"), ("DEMO-BED-02", "إقامة يوم — غرفة خاصة", "يوم", "1200.00"),
            ("DEMO-OP-01", "عملية صغرى بمخدر موضعي", "عملية", "3500.00"))
#: (Arabic name, English name, ((bed code, index of its daily service), ...))
WARDS = (("الدور الأول", "First floor", (("101", 0), ("102", 0), ("103", 1))),
         ("رعاية اليوم الواحد", "Day care", (("D-1", 0), ("D-2", 0))))


def applies(catalog):
    from medical import inpatient

    return catalog.key in CATALOGS and inpatient.has_inpatient()


def _at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def seed(master, doctor, owner, catalog, extras):
    """Set the beds up once. ``doctor`` is the catalog's own; the stay that ended is the sample's third customer and the
    patient still in a bed its fourth (video finding 8: the second customer was in a bed, in the queue and in an
    appointment marked «جاري» at the same time)."""

    from master_data.models import Item
    from medical import inpatient

    customers = master["customers"]
    if doctor is None or "patients" not in extras or len(customers) < 4 or Item.objects.filter(item_code=SENTINEL).exists():
        return False
    from settings_core.setup_services import usable_modules

    visit = next((item for item in master["items"] if not item.is_stock_tracked), None)
    # A supply is offered only where the business keeps stock («المخزون»); elsewhere the second charge is another service.
    supply = next((item for item in master["items"] if item.is_stock_tracked), None) if "inventory" in set(usable_modules()) else None
    if visit is None:
        return False
    today = timezone.localdate()
    with transaction.atomic():
        made = []
        for code, name, unit, price in SERVICES:
            item, _ = Item.objects.get_or_create(item_code=code, defaults={
                "item_name": name, "category": visit.category, "unit": unit, "default_sale_price": Decimal(price),
                "default_purchase_price": Decimal("0"), "min_stock": Decimal("0"), "is_stock_tracked": False, "active": True,
                "import_batch_id": "DEMO-BUSINESS"})
            made.append(item)
        operation = made[2]
        beds = {}
        for name_ar, name_en, codes in WARDS:
            ward = inpatient.save_ward({"name_ar": name_ar, "name_en": name_en}, owner)
            for code, service in codes:
                beds[code] = inpatient.save_bed(ward, {"code": code, "daily_service": made[service]}, owner)
        inpatient.save_bed(beds["D-2"].ward, {"active": False}, owner, bed=beds["D-2"])  # one bed out of service, as a real floor has

        # A stay that ended: two nights in the private room and a small operation; its bill is a draft, nothing posted.
        ended = inpatient.admit(customers[2], beds["103"], owner, doctor=doctor, diagnosis="فتق إربي — إصلاح جراحي",
                                when=_at(today - timedelta(days=4), 11))
        inpatient.add_charge(ended, {"kind": "operation", "item": operation, "surgeon": doctor, "day": today - timedelta(days=3),
                                     "note": "تمت بدون مضاعفات"}, owner)
        inpatient.discharge_and_bill(ended, owner, when=_at(today - timedelta(days=2), 13))

        # A patient in bed 101 since the day before yesterday, with a couple of charges so far.
        current = inpatient.admit(customers[3], beds["101"], owner, doctor=doctor, diagnosis="التهاب رئوي — ملاحظة ومضاد حيوي بالوريد",
                                  when=_at(today - timedelta(days=2), 9, 30))
        inpatient.add_charge(current, {"kind": "procedure", "item": visit, "day": today - timedelta(days=2)}, owner)
        if supply is not None:
            inpatient.add_charge(current, {"kind": "supply", "item": supply, "quantity": "2", "day": today - timedelta(days=1)}, owner)
        else:
            other = next((item for item in master["items"] if not item.is_stock_tracked and item.pk != visit.pk), visit)
            inpatient.add_charge(current, {"kind": "procedure", "item": other, "day": today - timedelta(days=1)}, owner)
    return True
