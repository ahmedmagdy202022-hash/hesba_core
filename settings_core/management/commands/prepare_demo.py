"""DEMO-001: turn an empty database into a showcase shop, once.

Only runs when DEMO_MODE is on. Creates the client and the demo logins
(owner, manager, cashier, stock keeper, accountant), finishes setup as a
retail shop with every module on, posts sample trade through the real
services (seed_demo_business), and adds a few restaurant tables,
appointments and a recipe so every screen has something to show. Running it
again on a filled database does nothing.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Fill an empty demo database with a sample shop (DEMO_MODE only)."

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("prepare_demo only runs with DEMO_MODE=True; it writes sample data and known passwords.")
        from settings_core.models import ClientProfile

        if ClientProfile.get_active() is not None:
            self.stdout.write("Demo data already present.")
            return
        password = settings.DEMO_PASSWORD
        call_command("bootstrap_client", display_name="محل حِسبة التجريبي", client_code="DEMO", password=password, verbosity=0)
        from settings_core import setup_catalog as catalog
        from settings_core.setup_services import complete_setup

        profile = ClientProfile.get_active()
        complete_setup(profile, "commercial", "retail", ",".join(catalog.MODULE_SLUGS))
        call_command("seed_demo_users", password=password, force=True, verbosity=0)
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        self._extras()
        self.stdout.write(self.style.SUCCESS(f"Demo shop ready. Sign in as owner / {password}"))

    def _extras(self):
        from django.contrib.auth import get_user_model

        from master_data.models import Customer, Item
        from restaurant import services as tables
        from staff.services import save_employee

        owner = get_user_model().objects.get(username="owner")
        for name in ("1", "2", "3", "4", "5", "6"):
            tables.save_table({"name": name, "seats": 4}, owner)
        tech = save_employee({"name": "أحمد الفني", "commission_percent": "10"}, owner)
        service, _ = Item.objects.get_or_create(item_code="SRV-01", defaults={"item_name": "خدمة تركيب", "default_sale_price": 150, "is_stock_tracked": False})
        customer = Customer.objects.filter(active=True).exclude(customer_code="WALK-IN").first()
        if customer:
            from appointments.services import book

            tomorrow = timezone.localdate() + timedelta(days=1)
            for hour in (10, 13):
                book({"customer": customer, "employee": tech, "service": service,
                      "starts_at": timezone.make_aware(datetime.combine(tomorrow, time(hour))), "duration_minutes": 60}, owner)
