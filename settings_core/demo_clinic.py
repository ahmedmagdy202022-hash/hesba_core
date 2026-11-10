"""M4: what a visitor who picks the clinic on the demo finds ready on the public booking page.

The page is set up and open: the clinic's hours, two doctors who take bookings (the catalog's own and a second one on
her own afternoon hours) and the visits a patient books («كشف», «إعادة كشف», «استشارة»). It all goes through the real
services: the doctor through ``staff.services``, the settings through ``appointments.booking.save_settings``, the same
service the settings screen runs. The «الحجز أونلاين» capability is switched on by ``config.demo_templates.demo_capabilities``
(demo-only, like the retail shop's extras); a real install keeps it off until its owner switches it on.

Idempotent: ``DEMO-MED-01`` (the first extra visit) is the sentinel, created inside the same transaction as the rest.
Only for the clinic: any other business, medical or not, gets none of it.
"""

from decimal import Decimal

from django.db import transaction

SENTINEL = "DEMO-MED-01"

#: (code, name, unit, price): the visits a patient books beside the catalog's own «كشف».
VISITS = ((SENTINEL, "إعادة كشف", "كشف", "200.00"), ("DEMO-MED-02", "استشارة", "كشف", "300.00"))
#: Booked minutes: the catalog's «كشف», then the two above.
MINUTES = (30, 15, 45)
#: The second doctor: (name, title, commission %), on her own hours (afternoons).
SECOND_DOCTOR = ("د. منى عبد الله", "أخصائية أطفال", "10")
SHOP_HOURS = ("10:00", "21:00")
OWN_HOURS = ("15:00", "21:00")


def applies(catalog):
    """Only the medical clinic (never a business that merely resembles one)."""

    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return bool(profile and (profile.activity_slug, profile.sub_activity_slug) == ("medical", "clinic") and catalog.key == "medical.clinic")


def seed(master, doctor, owner, catalog, extras):
    """Set the page up once. ``doctor`` is the catalog's own (already created, and already booked by the old sample)."""

    from appointments import booking
    from appointments.models import Weekday
    from entities.services import main_entity
    from master_data.models import Item
    from staff.services import save_employee

    if doctor is None or "appointments" not in extras or Item.objects.filter(item_code=SENTINEL).exists():
        return False
    with transaction.atomic():
        first_visit = next((item for item in master["items"] if not item.is_stock_tracked), None)
        if first_visit is None:
            return False
        visits = [first_visit]
        for code, name, unit, price in VISITS:
            item, _ = Item.objects.get_or_create(item_code=code, defaults={
                "item_name": name, "category": first_visit.category, "unit": unit, "default_sale_price": Decimal(price),
                "default_purchase_price": Decimal("0"), "min_stock": Decimal("0"), "is_stock_tracked": False, "active": True,
                "import_batch_id": "DEMO-BUSINESS"})
            visits.append(item)
        second = save_employee({"name": SECOND_DOCTOR[0], "title": SECOND_DOCTOR[1], "commission_percent": SECOND_DOCTOR[2]}, owner)
        data = {"accepting": "1", "show_prices": "1", "slot_minutes": "15", "min_notice_minutes": "60", "max_days_ahead": "14"}
        for weekday in Weekday.values:
            if weekday != Weekday.FRIDAY:
                data.update({f"day_{weekday}_on": "1", f"day_{weekday}_opens": SHOP_HOURS[0], f"day_{weekday}_closes": SHOP_HOURS[1],
                             f"emp_{second.pk}_day_{weekday}_on": "1", f"emp_{second.pk}_day_{weekday}_opens": OWN_HOURS[0],
                             f"emp_{second.pk}_day_{weekday}_closes": OWN_HOURS[1]})
        for item, minutes in zip(visits, MINUTES):
            data.update({f"service_{item.pk}": "1", f"duration_{item.pk}": str(minutes)})
        for person in (doctor, second):
            data[f"staff_{person.pk}"] = "1"
        booking.save_settings(main_entity(), data, owner)
    return True
