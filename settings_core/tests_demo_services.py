"""DEMO-SVC: picking "services" on the demo fills in the workshop's and the salon's own features, once, and only there."""

import re
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from appointments import services as appointments
from appointments.models import Appointment, AppointmentKind, AppointmentStatus
from appointments.views_week import week_start_of
from master_data.models import Item
from payments.services import outstanding
from messaging.models import Campaign, CampaignStatus, MessageKind, OutboxMessage
from reports.demo_catalogs import catalog_for
from settings_core import activity_fit
from settings_core import demo_services
from settings_core.models import ClientProfile
from staff.models import Employee
from workorders import services as wo
from workorders.models import Status, WorkOrder

PUBLIC = "https://demo.example.test"


class DemoServicesTests(TestCase):
    def fill(self, activity, sub, more=()):
        """The demo's "add sample data" for one kind of business, as the sample button runs it (``more``: modules switched on besides the defaults)."""

        from settings_core.management.commands.prepare_demo import Command as Prepare
        from settings_core.setup_services import complete_setup

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_setup(ClientProfile.get_active(), activity, sub, ",".join(activity_fit.default_modules(activity, sub) + tuple(more)))
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        prepare = Prepare()
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._spread_sale_times()
        prepare._extras()
        return catalog_for(activity, sub)

    def seed_again(self, catalog):
        from django.contrib.auth import get_user_model

        from reports.management.commands.seed_demo_business import Command as Seed

        master = Seed()._seed_master_data(catalog)
        owner = get_user_model().objects.get(username="owner")
        return demo_services.seed(master, None, owner, catalog, {"appointments", "workorders"})

    def counts(self):
        return {"employees": Employee.objects.count(), "items": Item.objects.count(), "orders": WorkOrder.objects.count(),
                "appointments": Appointment.objects.count(), "campaigns": Campaign.objects.count(), "outbox": OutboxMessage.objects.count()}

    @override_settings(PUBLIC_BASE_URL=PUBLIC)
    def test_a_repair_workshop_gets_its_service_features(self):
        with self.captureOnCommitCallbacks(execute=True):
            # A workshop starts without the appointments module (DEMO-FIT); its visits are seeded once the owner switches it on.
            catalog = self.fill("services", "maintenance", more=("appointments_visits",))
        today = timezone.localdate()
        # Technicians, each on a commission rate of their own (S9).
        crew = {employee.name: employee.commission_percent for employee in Employee.objects.filter(title__startswith="فني")}
        self.assertEqual({name: crew[name] for name in ("كريم محمود", "حسام الدسوقي", "عمرو السيد")},
                         {"كريم محمود": Decimal("8.00"), "حسام الدسوقي": Decimal("10.00"), "عمرو السيد": Decimal("7.00")})
        # Services that are not kept in stock.
        self.assertEqual(Item.objects.filter(item_code__startswith="DEMO-SVC-").count(), 4)
        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-SVC-", is_stock_tracked=True).exists())
        # About ten work orders across the statuses, the customer's own answers among them (S2).
        self.assertGreaterEqual(WorkOrder.objects.count(), 10)
        statuses = set(WorkOrder.objects.values_list("status", flat=True))
        self.assertTrue({Status.RECEIVED, Status.QUOTED, Status.IN_REPAIR, Status.READY, Status.DELIVERED, Status.CANCELLED} <= statuses, statuses)
        answers = WorkOrder.objects.filter(serial__startswith="SVC-", events__by_customer=True)
        self.assertEqual(set(answers.values_list("events__status", flat=True)), {"approved", "refused"})
        # Customer status messages carry the signed link, and the link opens the customer's page (S7, S2).
        links = [match.group(0) for message in OutboxMessage.objects.filter(kind=MessageKind.WORKORDER)
                 for match in [re.search(re.escape(PUBLIC) + r"\S+", message.text)] if match]
        self.assertGreaterEqual(len(links), 2)
        page = self.client.get(links[0].replace(PUBLIC, ""))
        self.assertEqual(page.status_code, 200)
        # The delivered job was invoiced and paid through the sales engine, and the technician's commission shows (S9).
        delivered = WorkOrder.objects.get(serial="SVC-5203")
        self.assertEqual((delivered.status, delivered.invoice.status, outstanding(delivered.invoice)), (Status.DELIVERED, "posted", Decimal("0.00")))
        row = next(row for row in wo.technician_report(today - timedelta(days=7), today) if row["technician"] == delivered.technician)
        self.assertGreater(row["commission"], 0)
        # Appointments over this week and the next, visits with an address, none overlapping on the calendar.
        first = week_start_of(today)
        mine = Appointment.objects.filter(starts_at__date__gte=first, starts_at__date__lt=first + timedelta(days=14))
        self.assertGreaterEqual(mine.count(), 12)
        self.assertTrue(mine.filter(starts_at__date__lt=first + timedelta(days=7)).exists())
        self.assertTrue(mine.filter(starts_at__date__gte=first + timedelta(days=7)).exists())
        self.assertFalse(mine.filter(kind=AppointmentKind.VISIT, address="").exists())
        self.assertTrue(mine.filter(status=AppointmentStatus.CANCELLED).exists() and mine.filter(status__in=(AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED)).exists())
        self.assertEqual(len(appointments.week_agenda(first)), mine.filter(starts_at__date__lt=first + timedelta(days=7)).count())
        # One campaign, still a draft: nothing was queued to anyone.
        campaign = Campaign.objects.get()
        self.assertEqual((campaign.status, campaign.queued_count), (CampaignStatus.DRAFT, 0))
        self.assertFalse(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).exists())
        # Running it again adds nothing.
        before = self.counts()
        self.assertFalse(self.seed_again(catalog))
        self.assertEqual(self.counts(), before)

    def test_a_salon_gets_appointments_staff_and_a_campaign_but_no_work_orders(self):
        self.fill("services", "beauty")
        self.assertEqual(WorkOrder.objects.count(), 0)
        crew = Employee.objects.filter(name__in=("نهى سمير", "دينا فتحي", "ياسمين طارق"))
        self.assertEqual(sorted(crew.values_list("commission_percent", flat=True)), [Decimal("10.00"), Decimal("10.00"), Decimal("12.00")])
        self.assertEqual(Item.objects.filter(item_code__startswith="DEMO-SVC-", is_stock_tracked=False).count(), 4)
        first = week_start_of(timezone.localdate())
        mine = Appointment.objects.filter(starts_at__date__gte=first, starts_at__date__lt=first + timedelta(days=14))
        self.assertGreaterEqual(mine.count(), 12)
        self.assertFalse(mine.filter(kind=AppointmentKind.VISIT).exists())
        self.assertTrue(mine.filter(starts_at__date__gte=first + timedelta(days=7)).exists())
        # Up to two finished appointments were billed and posted through the usual services, so the commission has something to show.
        # (How many are finished depends on the weekday the test runs on, so the expectation is worked out from the rows.)
        finished = mine.filter(status=AppointmentStatus.DONE).exclude(customer__customer_code="DEMO-CUST-03").count()
        self.assertEqual(mine.filter(invoice__status="posted").count(), min(2, finished))
        rows = appointments.performance(first, first + timedelta(days=13))
        self.assertEqual(any(row["commission"] > 0 for row in rows), finished > 0)
        self.assertEqual(Campaign.objects.get().status, CampaignStatus.DRAFT)
        before = self.counts()
        self.assertFalse(self.seed_again(catalog_for("services", "beauty")))
        self.assertEqual(self.counts(), before)

    def assert_none_of_it(self, catalog):
        self.assertFalse(demo_services.applies(catalog))
        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-SVC-").exists())
        self.assertFalse(WorkOrder.objects.filter(serial__startswith="SVC-").exists())
        self.assertFalse(Campaign.objects.exists())
        self.assertFalse(Employee.objects.filter(name__in=("كريم محمود", "نهى سمير")).exists())

    def test_a_services_business_without_these_screens_gets_none_of_it(self):
        self.assert_none_of_it(self.fill("services", "professional"))

    def test_an_ordinary_shop_gets_none_of_it(self):
        self.assert_none_of_it(self.fill("commercial", "retail"))


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", PUBLIC_BASE_URL=PUBLIC)
class DemoSampleButtonServicesTests(TestCase):
    def test_the_sample_button_fills_a_workshop_once(self):
        """The visitor's own path: restart, pick services, press "sample data" (twice); the second press adds nothing."""

        from django.urls import reverse

        from settings_core.setup_services import complete_setup

        self.client.post(reverse("demo_restart"), {"lang": "ar"})
        complete_setup(ClientProfile.get_active(), "services", "maintenance", ",".join(activity_fit.default_modules("services", "maintenance")))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("demo_sample"), {"lang": "ar"})
        orders = WorkOrder.objects.count()
        self.assertGreaterEqual(orders, 10)
        self.assertTrue(OutboxMessage.objects.filter(kind=MessageKind.WORKORDER, text__contains=PUBLIC).exists())
        before = {"employees": Employee.objects.count(), "items": Item.objects.count(), "campaigns": Campaign.objects.count()}
        self.client.post(reverse("demo_sample"), {"lang": "ar"})
        self.assertEqual(WorkOrder.objects.count(), orders)
        self.assertEqual({"employees": Employee.objects.count(), "items": Item.objects.count(), "campaigns": Campaign.objects.count()}, before)
        self.assertEqual(self.client.get("/work-orders/?lang=ar").status_code, 200)
