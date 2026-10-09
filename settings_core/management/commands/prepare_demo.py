"""DEMO-001: turn an empty database into a showcase shop, once.

Only runs when DEMO_MODE is on. Creates the client and the demo logins
(owner, manager, cashier, stock keeper, accountant), finishes setup as a
retail shop with every module on, posts sample trade through the real
services (seed_demo_business), and adds what that kind of business also
uses: tables, appointments, patient files, projects, production orders or
expiry dates (R2-5). Names and prices follow the chosen activity
(reports.demo_catalogs).

Running it again on a filled database only tops up today's trade
(seed_demo_business does that idempotently), so the "today" cards are never
all zero the day after the demo was prepared.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.exceptions import ValidationError
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
        seed.stock_in(master, "DEMO-PI-HIST", today - timedelta(days=29), master["suppliers"][0], sorted(need.items()), Decimal("0.00"), owner)
        for n, (days_ago, customer, lines, share) in enumerate(self.HISTORY_SALES, start=1):
            seed.sell(master, f"DEMO-SH-{n:03d}", today - timedelta(days=days_ago), customer, lines, owner, share=share)

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
        """What this kind of business also shows, beyond buying and selling (R2-5):
        tables for a restaurant, appointments and patient files for a clinic,
        projects for a builder, production orders for a factory, expiry dates
        for a pharmacy."""

        from django.contrib.auth import get_user_model

        from reports.demo_catalogs import active_catalog
        from reports.management.commands.seed_demo_business import Command as Seed
        from staff.services import save_employee

        owner = get_user_model().objects.get(username="owner")
        catalog = active_catalog()
        master = Seed()._seed_master_data(catalog)
        extras = catalog.extras
        if "tables" in extras:
            from restaurant import services as tables

            for name in ("1", "2", "3", "4", "5", "6"):
                tables.save_table({"name": name, "seats": 4}, owner)
        if extras & {"appointments", "patients", "orders", "projects"}:
            staff = save_employee({"name": catalog.staff[0], "title": catalog.staff[1], "commission_percent": "10"}, owner)
        if "appointments" in extras:
            self._appointments(master, staff, owner)
        if "patients" in extras:
            self._patients(master, staff, owner)
        if "projects" in extras:
            self._projects(master, owner)
        if "orders" in extras:
            self._orders(master, owner)
        if "batches" in extras:
            self._batches(master, owner)

    def _appointments(self, master, staff, owner):
        from appointments.services import book
        from master_data.models import Item

        service = next((item for item in master["items"] if not item.is_stock_tracked), None)
        if service is None:
            service, _ = Item.objects.get_or_create(item_code="SRV-01", defaults={"item_name": "خدمة تركيب", "default_sale_price": 150, "is_stock_tracked": False})
        today = timezone.localdate()
        for days, hour, customer in ((0, 17, 1), (1, 10, 0), (1, 13, 2), (2, 11, 3)):
            book({"customer": master["customers"][customer], "employee": staff, "service": service,
                  "starts_at": timezone.make_aware(datetime.combine(today + timedelta(days=days), time(hour))), "duration_minutes": 60}, owner)

    def _patients(self, master, doctor, owner):
        from medical.services import file_for, save_profile, save_visit

        today = timezone.localdate()
        cards = (
            ({"date_of_birth": "1978-03-14", "gender": "male", "blood_type": "A+", "chronic_conditions": "ضغط مرتفع", "current_medications": "كونكور 5 مجم"},
             {"complaint": "صداع ودوخة", "blood_pressure": "150/95", "pulse": "84", "diagnosis": "ارتفاع ضغط الدم", "treatment": "متابعة الضغط يوميًا وتقليل الملح"}),
            ({"date_of_birth": "1990-11-02", "gender": "female", "blood_type": "O+", "allergies": "بنسلين"},
             {"complaint": "كحة وسخونية", "temperature": "38.2", "diagnosis": "التهاب حلق", "treatment": "مضاد حيوي بديل للبنسلين وراحة"}),
            ({"date_of_birth": "2015-06-20", "gender": "male", "blood_type": "B+"},
             {"complaint": "متابعة دورية", "weight": "32", "height": "135", "diagnosis": "حالة عامة جيدة", "treatment": "لا يوجد"}),
        )
        for customer, (profile, visit) in zip(master["customers"], cards):
            save_profile(file_for(customer), profile, owner)
            save_visit(customer, {**visit, "visit_date": (today - timedelta(days=7)).isoformat(),
                                  "follow_up_date": (today + timedelta(days=7)).isoformat(), "doctor": doctor}, owner)

    def _projects(self, master, owner):
        from projects.services import bill_progress, issue_materials, save_project
        from sales.services import post_sales_invoice

        today = timezone.localdate()
        # Site materials: a well-stocked item (not the two kept short for the alerts), else a supply.
        stocked = [item for item in master["items"][:3] if item.is_stock_tracked] + master["raw"]
        rows = (
            (3, "عمارة سكنية - التجمع", "التجمع الخامس، القاهرة الجديدة", "2500000", 60),
            (2, "مبنى مدرسة - المرحلة الأولى", "مدينة نصر", "1200000", 25),
        )
        for n, (customer, name, site, value, started) in enumerate(rows):
            project = save_project({"name": name, "customer": master["customers"][customer], "site": site, "contract_value": value,
                                    "start_date": today - timedelta(days=started), "end_date": today + timedelta(days=180)}, owner)
            if stocked:
                issue_materials(project, owner, item=stocked[0], location=master["location"], quantity=5 - 2 * n,
                                operation_date=today - timedelta(days=3))
            invoice = bill_progress(project, owner, amount=Decimal(value) * Decimal("0.2"), description="مستخلص رقم 1")
            if n == 0:
                post_sales_invoice(invoice.id, user=owner)

    def _orders(self, master, owner):
        from manufacturing import orders
        from manufacturing.models import Recipe

        today = timezone.localdate()
        recipes = list(Recipe.objects.filter(product__in=master["items"], active=True).order_by("product__item_code"))
        for n, recipe in enumerate(recipes[:3]):
            order = orders.create_order({"recipe": recipe, "location": master["location"], "quantity": str(10 + 5 * n),
                                         "customer": master["customers"][3] if n == 0 else None, "due_date": today + timedelta(days=5 + n * 3),
                                         "labor_cost": "500", "overhead_cost": "200"}, owner)
            for _ in range(2 - n):  # two stages in, one stage in, not started
                try:  # HG-036: starting issues the materials; a short one stays planned (a real shortage on the board)
                    orders.advance(order, owner)
                except ValidationError:
                    break

    def _batches(self, master, owner):
        from batches.services import register_batch, stock_on_hand

        today = timezone.localdate()
        on_hand = stock_on_hand([item.pk for item in master["items"]])
        # Two run out soon, so the expiry alerts have something true to say.
        for item, days in zip(master["items"], (25, 45, 300, 200, 400)):
            quantity = on_hand.get(item.pk) or Decimal("0")
            if quantity > 0:
                register_batch(item=item, location=master["location"], batch_no=f"LOT-{item.item_code[-2:]}{today:%y%m}",
                               expiry_date=today + timedelta(days=days), quantity=quantity, received_on=today - timedelta(days=20), user=owner)
