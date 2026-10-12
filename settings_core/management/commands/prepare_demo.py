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
            self._floor(master, catalog, owner)
        if extras & {"appointments", "patients", "orders", "projects", "workorders"}:
            staff = save_employee({"name": catalog.staff[0], "title": catalog.staff[1], "commission_percent": "10"}, owner)
        if "appointments" in extras:
            self._appointments(master, staff, owner)
        if "patients" in extras:
            self._patients(master, staff, owner)
        if "students" in extras:
            self._students(master, staff, owner)
        if "animals" in extras:
            self._vet(master, staff, owner, timezone.localdate())
        if "projects" in extras:
            self._projects(master, owner)
        if "contracts" in extras:
            self._contracts(master, owner)
        if "packages" in extras:
            self._packages(master, owner)
        if "hours" in extras:
            self._hours(master, owner)
        if "orders" in extras:
            self._orders(master, owner)
        if "batches" in extras:
            self._batches(master, owner)
        if "workorders" in extras:
            self._workorders(master, staff, owner)

    def _workorders(self, master, staff, owner):
        """SVC-001: a workshop's week: devices in, on the bench, waiting for approval, ready, delivered, and one back under warranty."""

        from datetime import timedelta

        from django.utils import timezone

        from workorders import services as wo
        from workorders.models import Status, WorkOrder

        if not wo.is_workshop():
            return
        parts = [item for item in master["items"] if item.is_stock_tracked]
        labour = [item for item in master["items"] if not item.is_stock_tracked]
        customers = [customer for customer in master["customers"] if customer.customer_code != "WALK-IN"]
        if not (parts and labour and customers):
            return
        cashbox = next((box for box in master["cashboxes"] if box.is_default), master["cashboxes"][0]) if master["cashboxes"] else None
        today, now = timezone.localdate(), timezone.now()
        plan = (
            ("غسالة توشيبا 7 كيلو", "مش بتعصر وبتعمل صوت عالي", None, 0, ()),
            ("تكييف شارب 1.5 حصان", "مش بيبرّد", (Status.DIAGNOSED, Status.QUOTED), 3, ((labour[0], 1), (parts[0], 1))),
            ("ثلاجة كريازي", "الفريزر بيعمل تلج زيادة", (Status.IN_REPAIR,), 7, ((labour[-1], 1),)),
            ("غسالة أطباق بوش", "مش بتسحب مياه", (Status.DIAGNOSED, Status.APPROVED, Status.IN_REPAIR, Status.READY), 2, ((labour[-1], 1), (parts[-1], 1))),
            ("ميكروويف شارب", "مش بيسخّن", (Status.IN_REPAIR, Status.READY), 10, ((labour[0], 1), (parts[0], 1))),
        )
        orders = []
        for n, (device, problem, path, days_ago, lines) in enumerate(plan):
            order = wo.receive({"customer": customers[n % len(customers)], "device": device, "problem": problem, "technician": staff if path else None,
                                "promised_on": today + timedelta(days=2 - days_ago // 2), "warranty_days": "30", "serial": f"SN-{4710 + n}"}, owner)
            WorkOrder.objects.filter(pk=order.pk).update(received_at=now - timedelta(days=days_ago))
            for item, quantity in lines:
                wo.add_line(order, item, owner, quantity=str(quantity))
            for status in path or ():
                wo.move(order, status, owner)
            orders.append(order)
        # The microwave went home ten days ago, paid in cash; the dishwasher is waiting at the counter.
        delivered = orders[-1]
        try:
            paid = wo.totals(delivered)["total"]
            wo.deliver(delivered, owner, paid_now=str(paid) if cashbox else "0", cashbox=cashbox)
            WorkOrder.objects.filter(pk=delivered.pk).update(delivered_at=now - timedelta(days=8), warranty_until=today + timedelta(days=22))
            delivered.refresh_from_db()
            back = wo.receive({"warranty_of": delivered, "device": delivered.device, "serial": delivered.serial, "problem": "رجع نفس العطل",
                               "technician": staff}, owner)
            wo.add_line(back, parts[0], owner, quantity="1", is_warranty=True)
        except ValidationError:
            pass  # a part out of stock: the demo keeps it ready instead

    def _floor(self, master, catalog, owner):
        """RESTO-002: dish ingredients, a week of paid orders at different hours, and one waste adjustment."""

        from datetime import datetime as _dt

        from inventory.services import adjust_stock
        from restaurant import insights, services as floor
        from restaurant.models import DiningTable, Order
        from staff.services import save_employee

        dishes = [item for item in master["items"] if not item.is_stock_tracked][:3]
        raw = master["raw"]
        if not dishes or not raw:
            return
        for n, dish in enumerate(dishes):
            lines = [(raw[n % len(raw)], "0.250")]
            if raw[-1].pk != raw[n % len(raw)].pk:
                lines.append((raw[-1], "0.100"))
            insights.save_ingredients(dish, lines, owner)
        waiter = save_employee({"name": catalog.staff[0], "title": catalog.staff[1]}, owner)
        cashbox = master["cashboxes"][0]
        tables = list(DiningTable.objects.order_by("sort_order", "name"))
        today = timezone.localdate()
        plan = ((1, 13, 0, 2, (0, 1)), (1, 14, 1, 4, (0, 2)), (2, 20, 2, 3, (1,)), (3, 21, 0, 2, (0, 0, 2)), (4, 13, None, 1, (2,)),
                (5, 20, 3, 5, (0, 1, 1)), (6, 21, None, 1, (1,)), (0, 13, 1, 2, (0,)))
        for days, hour, table, guests, picks in plan:
            if table is None:
                order = floor.open_order(owner, kind="takeaway", waiter=waiter)
            else:
                order = floor.open_order(owner, table=tables[table], guests=guests, waiter=waiter)
            for pick in picks:
                floor.add_item(order, dishes[pick % len(dishes)], owner)
            floor.pay(order, owner, cashbox=cashbox, tendered=Decimal("100000"))  # paid in cash
            closed = timezone.make_aware(_dt.combine(today - timedelta(days=days), time(hour, 15)))
            Order.objects.filter(pk=order.pk).update(opened_at=closed - timedelta(minutes=40 + 5 * guests), closed_at=closed)
        adjust_stock(f"DEMO-WASTE-{today:%m%d}", today - timedelta(days=2), raw[0], master["location"], "out", "1", "هالك — باظت في التلاجة", owner)
        driver = save_employee({"name": "محمود الطيار", "title": "طيار دليفري"}, owner)  # RESTO-003: a delivery out with its driver and fee
        order = floor.open_order(owner, kind="delivery", customer=master["customers"][0], address="شارع النصر 12، مدينة نصر", delivery_fee="20")
        floor.add_item(order, dishes[0], owner, 2)
        floor.dispatch(order, driver, owner)
        floor.pay(order, owner, cashbox=cashbox, tendered=Decimal("100000"))
        closed = timezone.make_aware(_dt.combine(today - timedelta(days=1), time(21, 40)))
        Order.objects.filter(pk=order.pk).update(opened_at=closed - timedelta(minutes=55), dispatched_at=closed - timedelta(minutes=30), closed_at=closed)

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
        first = master["customers"][0]
        save_visit(first, {"complaint": "كشف أول مرة", "blood_pressure": "160/100", "diagnosis": "ارتفاع ضغط الدم", "treatment": "كونكور 5 مجم",
                           "visit_date": (today - timedelta(days=45)).isoformat(), "doctor": doctor}, owner)  # MED-002: a returning patient
        for customer, (profile, visit) in zip(master["customers"], cards):
            save_profile(file_for(customer), profile, owner)
            save_visit(customer, {**visit, "visit_date": (today - timedelta(days=7)).isoformat(),
                                  "follow_up_date": (today + timedelta(days=7)).isoformat(), "doctor": doctor}, owner)
        self._clinic_month(master, doctor, owner, today)
        self._dental(master, doctor, owner, today)
        self._lab(master, owner, today)
        self._physio(master, doctor, owner, today)
        self._queue(master, doctor, owner, today)

    def _queue(self, master, doctor, owner, today):
        """MED-007: a clinic's waiting list has today's booked patient in, one called and one waiting."""

        from appointments.models import Appointment
        from medical import queue

        if not queue.has_queue():
            return
        booked = Appointment.objects.filter(starts_at__date=today, customer=master["customers"][1]).first()
        first = queue.check_in(master["customers"][2], owner, doctor=doctor)
        queue.check_in(master["customers"][1], owner, appointment=booked)
        queue.move(first, "called", owner)

    def _vet(self, master, doctor, owner, today):
        """MED-006: a vet's first customer owns a dog and a cat; the dog came twice."""

        from medical import vet
        from medical.services import save_visit

        if not vet.is_vet():
            return
        owner_customer = master["customers"][0]
        dog = vet.save_animal(owner_customer, {"name": "روكي", "species": "dog", "breed": "جيرمن شيبرد", "sex": "male",
                                               "date_of_birth": (today - timedelta(days=3 * 365)).isoformat(), "colour": "أسود وبني",
                                               "microchip": "956000012345678", "neutered": True}, owner)
        vet.save_animal(owner_customer, {"name": "مشمش", "species": "cat", "breed": "شيرازي", "sex": "female",
                                         "date_of_birth": (today - timedelta(days=300)).isoformat(), "allergies": "بنسلين"}, owner)
        save_visit(owner_customer, {"animal": dog, "doctor": doctor, "visit_date": (today - timedelta(days=60)).isoformat(), "complaint": "تطعيم سنوي",
                                    "weight": "31", "diagnosis": "حالة عامة جيدة", "treatment": "تطعيم سعار + تطعيم خماسي"}, owner)
        save_visit(owner_customer, {"animal": dog, "doctor": doctor, "visit_date": (today - timedelta(days=5)).isoformat(), "complaint": "عرج في الرجل الشمال",
                                    "weight": "32", "diagnosis": "التواء بسيط", "treatment": "راحة 5 أيام ومسكن",
                                    "follow_up_date": (today + timedelta(days=5)).isoformat()}, owner)

    def _physio(self, master, therapist, owner, today):
        """MED-005: a physiotherapy centre's first patient bought ten sessions and used three."""

        from medical import physio
        from sales.services import post_sales_invoice

        if not physio.is_physio():
            return
        session = next((item for item in master["items"] if not item.is_stock_tracked), None)
        if session is None:
            return
        package = physio.sell(master["customers"][0], {"service": session, "sessions": 10, "price": "3000", "notes": "خشونة ركبة"}, owner)
        post_sales_invoice(package.invoice.pk, owner)
        for days in (14, 10, 7):
            physio.use(package, owner, used_on=today - timedelta(days=days), therapist=therapist)

    def _lab(self, master, owner, today):
        """MED-004: a lab has its tests with normal ranges, and two days of results for its first patient."""

        from medical import lab

        if not lab.is_lab():
            return
        services = {item.item_name: item for item in master["items"] if not item.is_stock_tracked}
        tests = {}
        for position, (name, unit, low, high, service) in enumerate((
                ("هيموجلوبين", "g/dL", "12", "16", "صورة دم كاملة"), ("كرات دم بيضاء", "×10³/µL", "4", "11", "صورة دم كاملة"),
                ("سكر صايم", "mg/dL", "70", "100", ""), ("إنزيم ALT", "U/L", "7", "56", "وظائف كبد"), ("فصيلة الدم", "", "", "", ""))):
            tests[name] = lab.save_test({"name": name, "unit": unit, "low": low, "high": high, "service": services.get(service), "position": position}, owner)
        patient = master["customers"][0]
        lab.record_results(patient, today - timedelta(days=30), {tests["هيموجلوبين"]: ("11.2", ""), tests["سكر صايم"]: ("132", "")}, owner)
        lab.record_results(patient, today - timedelta(days=1), {tests["هيموجلوبين"]: ("12.8", ""), tests["كرات دم بيضاء"]: ("7.5", ""),
                                                                tests["سكر صايم"]: ("118", ""), tests["إنزيم ALT"]: ("31", ""),
                                                                tests["فصيلة الدم"]: ("", "A+")}, owner)

    def _dental(self, master, doctor, owner, today):
        """MED-003: a dental clinic's first patient has a chart and a plan in two stages, one step done."""

        from medical import dental

        if not dental.is_dental():
            return
        services = [item for item in master["items"] if not item.is_stock_tracked]
        if len(services) < 3:
            return
        checkup, root_canal, cleaning = services[:3]
        patient = master["customers"][0]
        for tooth, condition, notes in ((16, "caries", "تسوّس عميق"), (26, "filled", ""), (36, "root_canal", ""), (46, "missing", "اتخلعت 2024"),
                                        (11, "crown", "")):
            dental.set_tooth(patient, tooth, condition, owner, notes)
        first = dental.plan_step(patient, {"service": cleaning, "stage": 1, "doctor": doctor}, owner)
        dental.plan_step(patient, {"service": root_canal, "tooth": 16, "stage": 2, "doctor": doctor}, owner)
        dental.plan_step(patient, {"service": checkup, "stage": 3, "doctor": doctor, "notes": "متابعة بعد العلاج"}, owner)
        dental.complete_step(first, owner, done_on=today - timedelta(days=7))

    def _clinic_month(self, master, doctor, owner, today):
        """MED-002: billed visits for the clinic report, and a follow-up that was missed."""

        from appointments.services import bill, book, set_status
        from medical.services import file_for, save_profile, save_visit
        from sales.pos_customers import quick_add_customer
        from sales.services import post_sales_invoice

        service = next((item for item in master["items"] if not item.is_stock_tracked), None)
        if service is not None:
            for days, customer in ((7, 0), (7, 1), (7, 2)):
                appointment = book({"customer": master["customers"][customer], "employee": doctor, "service": service,
                                    "starts_at": timezone.make_aware(datetime.combine(today - timedelta(days=days), time(10 + customer))),
                                    "duration_minutes": 30}, owner)
                set_status(appointment, "arrived", owner)
                post_sales_invoice(bill(appointment, owner).pk, owner)
            self._insured_visit(service, doctor, owner, today)
        missed, _ = quick_add_customer("سلمى حسن", "01000000105", owner)
        save_profile(file_for(missed), {"gender": "female", "date_of_birth": "1985-01-09", "chronic_conditions": "سكر"}, owner)
        save_visit(missed, {"complaint": "متابعة السكر", "diagnosis": "سكر من النوع الثاني", "treatment": "جلوكوفاج 500 مجم",
                            "visit_date": (today - timedelta(days=21)).isoformat(), "follow_up_date": (today - timedelta(days=6)).isoformat(),
                            "doctor": doctor}, owner)

    def _insured_visit(self, service, doctor, owner, today):
        """MED-008: an insurance company, an insured patient, and one visit billed as two invoices (posted, so it shows on the claim)."""

        from appointments.services import book, set_status
        from medical import insurance
        from sales.pos_customers import quick_add_customer
        from sales.services import post_sales_invoice

        if not insurance.is_insurance_install():
            return
        contract = insurance.save_contract({"name": "مصر للتأمين الصحي", "phone": "0223456789", "coverage_percent": "80", "claim_day": "5"}, owner)
        insured, _ = quick_add_customer("كريم فؤاد", "01000000106", owner)
        insurance.set_cover(insured, {"contract": contract, "member_number": "MI-204518"}, owner)
        appointment = book({"customer": insured, "employee": doctor, "service": service,
                            "starts_at": timezone.make_aware(datetime.combine(today - timedelta(days=3), time(12))), "duration_minutes": 30}, owner)
        set_status(appointment, "arrived", owner)
        bill = insurance.bill_insured(appointment, owner)
        for invoice in (bill.patient_invoice, bill.insurer_invoice):
            if invoice is not None:
                post_sales_invoice(invoice.pk, owner)

    #: CONTRACT-002: a bill of quantities per contracting sub-activity (code, item, unit, quantity, rate).
    BOQ = {
        "contracting.general": (("1", "خرسانة عادية للأساسات", "م³", "120", "1800"), ("2", "خرسانة مسلحة للهيكل", "م³", "450", "3200"),
                                ("3", "مباني طوب أحمر", "م²", "2200", "260"), ("4", "لياسة داخلية وخارجية", "م²", "5200", "95"),
                                ("5", "عزل أسطح", "م²", "600", "180")),
        "contracting.finishing": (("1", "معجون ودهانات بلاستيك", "م²", "3800", "85"), ("2", "أرضيات بورسلين", "م²", "900", "620"),
                                  ("3", "أسقف جبس بورد", "م²", "700", "380"), ("4", "سيراميك حوائط حمامات ومطابخ", "م²", "450", "420")),
        "contracting.electromechanical": (("1", "تأسيس كهرباء (نقطة)", "نقطة", "650", "450"), ("2", "تأسيس سباكة (نقطة)", "نقطة", "180", "900"),
                                          ("3", "توريد وتركيب تكييفات", "جهاز", "24", "24500"), ("4", "لوحات توزيع كهرباء", "لوحة", "12", "8500")),
        "contracting.infrastructure": (("1", "حفر وردم", "م³", "6000", "45"), ("2", "طبقة أساس سن", "م³", "2400", "420"),
                                       ("3", "رصف أسفلت", "م²", "9000", "140"), ("4", "توريد وتركيب بلدورة", "م.ط", "1800", "190")),
    }
    def _students(self, master, teacher, owner):
        """EDU-001: courses from the catalog's services, three groups, parents with their children enrolled."""

        from education import services as edu
        from master_data.models import Customer
        from staff.services import save_employee

        if not edu.is_education_install():
            return  # the same catalog also serves a services business, which has no student files
        today = timezone.localdate()
        courses = [edu.save_course({"name": item.item_name, "fee": item.default_sale_price, "basis": "monthly", "item": item}, owner)
                   for item in master["items"] if not item.is_stock_tracked][:2]
        if not courses:
            return
        second = save_employee({"name": "أ. نادية", "title": teacher.title or "مدرسة"}, owner)
        # EDU-004: each group says how its teacher is paid: per class, a share of the fees, or a fixed month.
        plan = (
            (courses[0], "مجموعة السبت والتلات", teacher, "قاعة 1", "sat,tue", time(16), 12, "percent", "40"),
            (courses[0], "مجموعة الأحد والأربع", second, "قاعة 2", "sun,wed", time(18), 12, "session", "120"),
            (courses[-1], "مجموعة الاتنين والخميس", teacher, "قاعة 1", "mon,thu", time(17), 10, "monthly", "800"),
        )
        groups = [edu.save_group({"course": course, "name": name, "teacher": who, "room": room, "days": days, "start_time": start,
                                  "duration_minutes": "90", "capacity": str(capacity), "starts_on": today - timedelta(days=40),
                                  "teacher_pay": pay_basis, "teacher_rate": rate}, owner)
                  for course, name, who, room, days, start, capacity, pay_basis, rate in plan]
        families = (
            ("محمد السيد (ولي أمر)", "01001234501", "الأب", ("يوسف محمد", "مريم محمد")),
            ("هالة عبد الرحمن (ولية أمر)", "01101234502", "الأم", ("عمر خالد",)),
            ("طارق فهمي (ولي أمر)", "01201234503", "الأب", ("سلمى طارق", "آدم طارق")),
            ("نجلاء حسن (ولية أمر)", "01501234504", "الأم", ("ليلى أحمد",)),
            ("إبراهيم سالم (ولي أمر)", "01001234505", "الأب", ("حمزة إبراهيم",)),
        )
        students = []
        for parent, phone, relation, children in families:
            payer = Customer.objects.filter(phone=phone).first()
            for child in children:
                data = {"name": child, "stage": "تالتة إعدادي", "relation": relation}
                data.update({"payer": payer} if payer else {"payer_name": parent, "payer_phone": phone})
                student = edu.save_student(data, owner)
                payer = student.payer
                students.append(student)
        for n, student in enumerate(students):
            # Brothers and sisters get 10% off; the first two groups share the students, the third takes a few.
            sibling = sum(1 for other in students if other.payer_id == student.payer_id) > 1
            edu.enroll(student, groups[n % 2], owner, start_date=today - timedelta(days=35), discount_percent="10" if sibling else "0")
            if n < 4:
                edu.enroll(student, groups[2], owner, start_date=today - timedelta(days=20))
        # EDU-002: four weeks of attendance, with a few absences and late arrivals.
        from education import attendance

        for group in groups:
            for days_ago in range(28, -1, -1):
                day = today - timedelta(days=days_ago)
                if attendance.weekday(day) not in group.weekdays:
                    continue
                roster = list(attendance.roster_on(group, day))
                marks = {}
                for k, row in enumerate(roster):
                    if (k + days_ago) % 9 == 0:
                        marks[str(row.student_id)] = "absent"
                    elif (k + days_ago) % 11 == 0:
                        marks[str(row.student_id)] = "late"
                if roster:
                    attendance.take(group, owner, day, marks)
        # EDU-003: last month billed and mostly paid, this month billed; two parents still owe.
        from config.numbering import next_in_series
        from education import billing
        from sales.models import CustomerPayment
        from sales.services import record_customer_payment

        last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        cashbox = next((box for box in master["cashboxes"] if box.is_default), master["cashboxes"][0]) if master["cashboxes"] else None
        for month, day in ((last_month, min(last_month + timedelta(days=2), today)), (today.replace(day=1), today)):
            try:
                invoices = billing.bill_month(month, owner, post=True, invoice_date=day)
            except ValidationError:
                continue  # nothing running that month
            if month == last_month and cashbox is not None:
                for invoice in invoices[:-2]:
                    record_customer_payment(next_in_series(CustomerPayment, "payment_number", "CP-"), day + timedelta(days=3), invoice.customer, cashbox,
                                            invoice.total_amount, owner, notes="مصروفات الشهر")
        # EDU-004: last month's teachers paid out of the cashbox; this month still owed.
        from education import pay

        if cashbox is not None:
            for row in pay.teacher_rows(last_month):
                if row["remaining"] > 0:
                    try:
                        pay.pay(row["teacher"], last_month, row["remaining"], cashbox, owner)
                    except ValidationError:
                        pass  # the cashbox cannot cover it: the demo shows it owed

    def _projects(self, master, owner):
        """CONTRACT-002: one project with a bill of quantities, terms, an advance,
        two certificates (one posted and collected, one draft), a subcontractor
        and a budget; one lump-sum project with a draft bill."""

        from projects import contract, costs
        from projects.models import ProjectPaymentKind
        from projects.services import bill_progress, issue_materials, save_project
        from purchases.services import post_purchase_invoice
        from sales.services import post_sales_invoice

        today = timezone.localdate()
        # Site materials: a well-stocked item (not the two kept short for the alerts), else a supply.
        stocked = [item for item in master["items"][:3] if item.is_stock_tracked] + master["raw"]
        boq = self.BOQ.get(master["catalog"].key)
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
            if n == 0 and boq:
                lines = [contract.save_boq_line(project, owner, {"code": code, "description": text, "unit": unit, "quantity": qty, "rate": rate})
                         for code, text, unit, qty, rate in boq]
                contract.save_terms(project, owner, retention_rate="5", advance_recovery_rate="10")
                total = contract.boq_total(project)
                contract.receive_payment(project, owner, kind=ProjectPaymentKind.ADVANCE, cashbox=master["cashboxes"][0],
                                         amount=(total * Decimal("0.15")).quantize(Decimal("1")), payment_date=today - timedelta(days=started))
                first = contract.create_certificate(project, owner, certificate_date=today - timedelta(days=started), description="أعمال الشهر الأول",
                                                    quantities={line.pk: (line.quantity * Decimal("0.25")).quantize(Decimal("1")) for line in lines[:2]})
                post_sales_invoice(first.invoice_id, user=owner)
                contract.receive_payment(project, owner, kind=ProjectPaymentKind.COLLECTION, cashbox=master["cashboxes"][0],
                                         amount=contract.net_payable(first), payment_date=today - timedelta(days=started - 10))
                # CONTRACT-003: the second certificate is posted and still unpaid past the credit days, so it shows as overdue.
                second = contract.create_certificate(project, owner, certificate_date=today - timedelta(days=40), description="أعمال الشهر التاني",
                                                     quantities={line.pk: (line.quantity * Decimal("0.45")).quantize(Decimal("1")) for line in lines[:3]})
                post_sales_invoice(second.invoice_id, user=owner)
                contract.create_certificate(project, owner, certificate_date=today, description="أعمال الشهر التالت",
                                            quantities={line.pk: (line.quantity * Decimal("0.60")).quantize(Decimal("1")) for line in lines[:3]})
                if master["suppliers"]:
                    subcontract = costs.save_subcontract(project, owner, {"supplier": master["suppliers"][-1], "scope": f"مقاولة باطن: {lines[-1].description}",
                                                                          "value": contract.line_amount(lines[-1]) * Decimal("0.8"), "retention_rate": "5"})
                    bill = costs.bill_subcontract(subcontract, owner, amount=(contract.line_amount(lines[-1]) * Decimal("0.2")).quantize(Decimal("1")),
                                                  description="مستخلص 1", bill_date=today - timedelta(days=10))
                    post_purchase_invoice(bill.invoice_id, owner)
                costs.save_budget(project, owner, {"materials": (total * Decimal("0.35")).quantize(Decimal("1")),
                                                   "subcontract": (total * Decimal("0.15")).quantize(Decimal("1")),
                                                   "labour": (total * Decimal("0.2")).quantize(Decimal("1")),
                                                   "equipment": (total * Decimal("0.05")).quantize(Decimal("1"))})
                continue
            invoice = bill_progress(project, owner, amount=Decimal(value) * Decimal("0.2"), description="مستخلص رقم 1")
            if n == 0:
                post_sales_invoice(invoice.id, user=owner)

    def _contracts(self, master, owner):
        """SVC-002: a monthly contract billed for its past months (two posted, one still a draft) and a
        quarterly one with a visit a period that is due, so "bill what is due" has work to do."""

        from agreements.services import add_months, bill_due, save_contract
        from sales.services import post_sales_invoice

        today = timezone.localdate()
        services = [item for item in master["items"] if not item.is_stock_tracked]
        if not services:
            return
        monthly = next((item for item in services if "شهر" in item.item_name), services[-1])
        first = save_contract({"customer": master["customers"][3], "service": monthly, "amount": monthly.default_sale_price, "every": "month",
                               "starts_on": add_months(today, -2).replace(day=1)}, owner)
        for bill in bill_due(owner, contracts=[first])[:2]:
            post_sales_invoice(bill.invoice.id, user=owner)
        save_contract({"customer": master["customers"][2], "service": services[0], "amount": Decimal(services[0].default_sale_price) * 2,
                       "every": "quarter", "visits_per_period": 1, "description": "عقد زيارات دورية",
                       "starts_on": today - timedelta(days=10)}, owner)

    def _packages(self, master, owner):
        """SVC-003: a posted package with two sessions used, and one still on a draft invoice."""

        from agreements.packages import sell_package, use_session
        from sales.services import post_sales_invoice
        from staff.models import Employee

        today = timezone.localdate()
        services = [item for item in master["items"] if not item.is_stock_tracked]
        if not services:
            return
        staff = Employee.objects.filter(active=True).order_by("pk").first()
        first = sell_package({"customer": master["customers"][0], "service": services[0], "sessions": 6,
                              "price": (Decimal(services[0].default_sale_price) * 6 * Decimal("0.85")).quantize(Decimal("0.01")),
                              "expires_on": today + timedelta(days=90), "notes": "خصم 15% على الباكدج"}, owner)
        post_sales_invoice(first.invoice.id, user=owner)
        for days in (9, 2):
            use_session(first, owner, used_on=today - timedelta(days=days), employee=staff)
        sell_package({"customer": master["customers"][1], "service": services[-1], "sessions": 4}, owner)

    def _hours(self, master, owner):
        """SVC-003: a week of hours for two customers; one customer's are billed on a draft invoice, one more hour after it."""

        from agreements.hours import bill_hours, log_time
        from staff.models import Employee

        today = timezone.localdate()
        services = [item for item in master["items"] if not item.is_stock_tracked]
        if not services:
            return
        staff = Employee.objects.filter(active=True).order_by("pk").first()
        work = ((6, 0, "2", "مراجعة المستندات"), (5, 1, "1.5", "اجتماع مع العميل"), (4, 0, "3", "إعداد التقرير"),
                (3, 1, "2.5", "تعديلات مطلوبة"), (2, 0, "1", "متابعة"))
        for days, customer, hours, description in work:
            log_time({"customer": master["customers"][customer], "service": services[0], "employee": staff, "hours": hours,
                      "worked_on": today - timedelta(days=days), "description": description}, owner)
        bill_hours(master["customers"][0], owner)
        log_time({"customer": master["customers"][0], "service": services[0], "employee": staff, "hours": "1", "worked_on": today,
                  "description": "مكالمة متابعة"}, owner)

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
