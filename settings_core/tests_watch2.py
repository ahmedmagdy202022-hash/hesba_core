"""Watch round 2 fixes in the demo seed: S2-01 / RS-02 (no car parts on a restaurant, contractor or factory) and
RS-04 / RS-05 (the restaurant's orders are never timed in the future, and a table is in service when the demo opens)."""

import re
from datetime import datetime, time, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from ai.no_network import AiTestCase
from ai.providers.mock import SAMPLE_READING
from master_data.models import Category, Item, Supplier

from . import demo_ai
from .tests_demo_ai import DEMO, SAMPLE_PHOTO


class NoCarPartsDemo:
    """Built once per class as the showcase builds a populated demo: restart, set up the activity, seed the sample month."""

    databases = {"default", "feedback"}
    activity, sub = "", ""

    @classmethod
    def setUpTestData(cls):
        from config.demo_restart import restart_demo
        from config.demo_templates import complete_demo_setup, seed_sample_business

        cls.owner = restart_demo()
        complete_demo_setup(cls.activity, cls.sub)
        seed_sample_business()

    def test_no_car_parts_are_seeded_but_the_samples_supplier_is(self):
        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-AI-").exists())
        self.assertFalse(Item.objects.filter(item_name__in=[row[1] for row in demo_ai.ITEMS]).exists())
        self.assertFalse(Category.objects.filter(category_code=demo_ai.CATEGORY[0]).exists())
        self.assertEqual(Supplier.objects.filter(supplier_code=demo_ai.SUPPLIER_CODE).count(), 1)

    def test_the_try_it_step_still_works_by_mapping_the_lines_to_the_businesss_own_items(self):
        self.client.force_login(self.owner)
        upload = self.client.post("/ai/invoice-photo/?lang=ar", {"photo": SimpleUploadedFile("sample-invoice.jpg", SAMPLE_PHOTO.read_bytes(),
                                                                                              content_type="image/jpeg")})
        self.assertEqual(upload.status_code, 302, upload.content[:400])
        token = re.search(r"/ai/invoice-photo/([^/]+)/", upload["Location"]).group(1)
        review = self.client.get(f"/ai/invoice-photo/{token}/?lang=ar")
        self.assertContains(review, 'data-iph-supplier-chip="matched"')
        reading = review.context["review"]
        self.assertEqual([line["matched"] for line in reading.lines], [False, False, False])
        items = list(Item.objects.filter(active=True).order_by("pk")[:3])
        self.assertEqual(len(items), 3)
        data = {"lang": "ar", "supplier": str(reading.supplier_id), "invoice_number": SAMPLE_READING["invoice_number"],
                "invoice_date": SAMPLE_READING["invoice_date"], "receiving_location": str(reading.store_id)}
        for index, item in enumerate(items):
            line = SAMPLE_READING["lines"][index]
            data.update({f"line-{index}-item": str(item.pk), f"line-{index}-quantity": line["quantity"],
                         f"line-{index}-price": line["unit_price"], f"line-{index}-include": "1"})
        saved = self.client.post(f"/ai/invoice-photo/{token}/?lang=ar", data)
        self.assertEqual(saved.status_code, 302, saved.content[:400])

    def test_seeding_again_adds_nothing(self):
        from reports.management.commands.seed_demo_business import Command as Seed

        suppliers = Supplier.objects.count()
        self.assertFalse(demo_ai.seed(Seed()._seed_master_data(), self.owner, {"purchases"}))
        self.assertEqual(Supplier.objects.count(), suppliers)


@override_settings(**DEMO)
class RestaurantDemoTests(NoCarPartsDemo, AiTestCase):
    activity, sub = "restaurants", "restaurant"

    def test_no_seeded_order_is_timed_after_the_moment_the_demo_was_prepared(self):
        from restaurant.models import Order

        now = timezone.now()
        orders = list(Order.objects.all())
        self.assertGreaterEqual(len(orders), 9)
        for order in orders:
            for stamp in (order.opened_at, order.closed_at, order.dispatched_at):
                if stamp is not None:
                    self.assertLessEqual(stamp, now, f"{order.number} is timed after now")

    def test_the_demo_opens_with_a_table_in_service_that_has_an_order_a_kitchen_ticket_and_a_bill(self):
        from restaurant.models import DiningTable, KitchenTicket, Order, OrderStatus

        busy = [table for table in DiningTable.objects.all() if Order.objects.filter(table=table, status=OrderStatus.OPEN).exists()]
        self.assertEqual(len(busy), 1)
        order = Order.objects.get(table=busy[0], status=OrderStatus.OPEN)
        self.assertEqual(order.lines.filter(voided=False).count(), 3)
        self.assertEqual(KitchenTicket.objects.filter(order=order).count(), 1)
        self.assertEqual(order.lines.filter(voided=False, ticket__isnull=True).count(), 1)  # one line still to send
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(f"/restaurant/orders/{order.pk}/?lang=ar"), "data-send-kitchen")


class ClosingMomentTests(SimpleTestCase):
    """The pure rule behind RS-04, at the hours a demo may be prepared (Cairo time)."""

    def moment(self, now_hour, now_minute, days=0, hour=13):
        from settings_core.management.commands.prepare_demo import Command

        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(now_hour, now_minute)))
        return Command._closing_moment(today, days, hour, now), now

    def test_a_morning_demo_closes_todays_order_half_an_hour_ago_not_at_lunch(self):
        closed, now = self.moment(9, 5)
        self.assertEqual(closed, now - timedelta(minutes=30))

    def test_an_evening_demo_keeps_the_planned_lunch_hour(self):
        closed, now = self.moment(19, 0)
        self.assertEqual(timezone.localtime(closed).time(), time(13, 15))
        self.assertLess(closed, now)

    def test_just_after_midnight_the_order_is_dated_yesterday_at_its_own_hour(self):
        closed, now = self.moment(0, 10)
        self.assertEqual(timezone.localtime(closed), timezone.make_aware(datetime.combine(timezone.localdate() - timedelta(days=1), time(13, 15))))

    def test_earlier_days_are_never_moved(self):
        for hour, minute in ((0, 10), (9, 5), (23, 50)):
            closed, now = self.moment(hour, minute, days=3, hour=21)
            self.assertEqual(timezone.localtime(closed).date(), timezone.localdate() - timedelta(days=3))
            self.assertEqual(timezone.localtime(closed).time(), time(21, 15))


@override_settings(**DEMO)
class ContractingNoCarPartsTests(NoCarPartsDemo, AiTestCase):
    activity, sub = "contracting", "general"


@override_settings(**DEMO)
class FactoryNoCarPartsTests(NoCarPartsDemo, AiTestCase):
    activity, sub = "manufacturing", "food"
