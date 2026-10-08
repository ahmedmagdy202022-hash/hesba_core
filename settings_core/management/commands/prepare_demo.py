"""DEMO-001: turn an empty database into a showcase shop, once.

Only runs when DEMO_MODE is on. Creates the client and the demo logins
(owner, manager, cashier, stock keeper, accountant), finishes setup as a
retail shop with every module on, posts sample trade through the real
services (seed_demo_business), and adds a few restaurant tables,
appointments and a recipe so every screen has something to show.

Running it again on a filled database only tops up today's trade
(seed_demo_business does that idempotently), so the "today" cards are never
all zero the day after the demo was prepared.
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
            call_command("seed_demo_business", username="owner", force=True, verbosity=0)
            self.stdout.write("Demo data already present; today's trade topped up.")
            return
        password = settings.DEMO_PASSWORD
        call_command("bootstrap_client", display_name="محل حِسبة التجريبي", client_code="DEMO", password=password, verbosity=0)
        from settings_core import setup_catalog as catalog
        from settings_core.setup_services import complete_setup

        profile = ClientProfile.get_active()
        # The ready-made shop also shows employees and reps (FEEDBACK-R1 ties reps to that module).
        complete_setup(profile, "commercial", "retail", ",".join(catalog.default_modules("commercial") + ("employees_technicians",)))
        call_command("seed_demo_users", password=password, force=True, verbosity=0)
        self._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        self._spread_sale_times()
        self._extras()
        self.stdout.write(self.style.SUCCESS(f"Demo shop ready. Sign in as owner / {password}"))

    #: (days ago, customer index, ((item index, quantity), ...), share paid now)
    #: A month of uneven trade so the charts, weekdays and peak hours have a
    #: shape. Item 5 is never touched: it must stay out of stock for the alert.
    HISTORY_SALES = (
        (27, 0, ((0, 2),), 1), (26, 3, ((1, 1), (2, 2)), 1), (24, 1, ((3, 1),), 0.5),
        (23, 0, ((2, 3),), 1), (21, 3, ((0, 3), (1, 2)), 0.5), (20, 0, ((2, 2),), 1),
        (19, 1, ((0, 1), (3, 1)), 1), (17, 3, ((1, 3),), 0), (16, 0, ((2, 4),), 1),
        (14, 0, ((0, 2), (2, 1)), 1), (13, 3, ((3, 2),), 0.5), (12, 1, ((1, 1),), 1),
        (10, 0, ((0, 4),), 1), (9, 3, ((2, 5), (1, 1)), 0.5), (7, 0, ((3, 1),), 1),
        (6, 1, ((0, 2), (2, 2)), 1), (5, 3, ((1, 2),), 0), (3, 0, ((2, 3),), 1),
        (1, 3, ((0, 3), (3, 1)), 0.5),
    )

    def _history(self):
        """Sales across the last four weeks, through the real posting services."""

        from decimal import Decimal

        from django.contrib.auth import get_user_model

        from reports.management.commands.seed_demo_business import Command as Seed

        seed = Seed()
        owner = get_user_model().objects.get(username="owner")
        master = seed._seed_master_data()
        today = timezone.localdate()
        need = {}
        for _, _, lines, _ in self.HISTORY_SALES:
            for index, quantity in lines:
                need[index] = need.get(index, 0) + quantity
        seed._post_purchase(
            number="DEMO-PI-HIST", invoice_date=today - timedelta(days=29), supplier=master["suppliers"][0],
            location=master["location"], cashbox=master["cashboxes"][0],
            lines=[(master["items"][i], Decimal(qty)) for i, qty in sorted(need.items())], paid_now=Decimal("0.00"), actor=owner,
        )
        for n, (days_ago, customer, lines, share) in enumerate(self.HISTORY_SALES, start=1):
            items = [(master["items"][i], Decimal(qty)) for i, qty in lines]
            total = sum((item.default_sale_price * qty for item, qty in items), Decimal("0.00"))
            seed._post_sale(
                number=f"DEMO-SH-{n:03d}", invoice_date=today - timedelta(days=days_ago), customer=master["customers"][customer],
                location=master["location"], cashbox=master["cashboxes"][0], lines=items,
                paid_now=(total * Decimal(str(share))).quantize(Decimal("0.01")), actor=owner,
            )

    def _spread_sale_times(self):
        """Past invoices were all created this second; give them shop hours so
        the peak-hours chart shows a working day instead of one bar at "now"."""

        from sales.models import SalesInvoice

        today = timezone.localdate()
        hours = (10, 12, 13, 19, 20, 18, 11, 21, 17, 14, 19, 20)
        for n, invoice in enumerate(SalesInvoice.objects.filter(invoice_date__lt=today).order_by("pk")):
            stamp = timezone.make_aware(datetime.combine(invoice.invoice_date, time(hours[n % len(hours)], (n * 17) % 60)))
            SalesInvoice.objects.filter(pk=invoice.pk).update(created_at=stamp)

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
