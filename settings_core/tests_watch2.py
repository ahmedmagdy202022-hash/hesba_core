"""Watch round 2 fixes in the demo seed: S2-01 / RS-02 (no car parts on a restaurant, contractor or factory)."""

import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

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
class RestaurantNoCarPartsTests(NoCarPartsDemo, AiTestCase):
    activity, sub = "restaurants", "restaurant"


@override_settings(**DEMO)
class ContractingNoCarPartsTests(NoCarPartsDemo, AiTestCase):
    activity, sub = "contracting", "general"


@override_settings(**DEMO)
class FactoryNoCarPartsTests(NoCarPartsDemo, AiTestCase):
    activity, sub = "manufacturing", "food"
