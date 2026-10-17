"""AN7 (G06, Ahmed approved): the supermarket's sample shelf looks like a supermarket."""

from decimal import Decimal

from django.test import override_settings

from master_data.models import Item
from purchases.models import PurchaseInvoice
from sales.models import SalesInvoice

from .tests_demo_catalogs import SampleBusinessPerActivityTests


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class GroceryShelfTests(SampleBusinessPerActivityTests):
    def test_a_supermarket_has_a_shelf_in_sections(self):
        from batches.models import Batch
        from cashboxes.models import Cashbox
        from units.models import ItemUnit

        catalog = self.fill("commercial", "grocery")
        self.assert_a_working_business(catalog)  # the five items keep their planted roles
        shelf = Item.objects.filter(item_code__startswith="DEMO-SH-")
        self.assertEqual(shelf.count(), sum(len(products) for *_, products in catalog.shelf))
        self.assertGreaterEqual(Item.objects.filter(active=True).count(), 40)
        self.assertEqual(shelf.values("category").distinct().count(), 6)
        weighed = shelf.get(item_name="طماطم")
        self.assertEqual((weighed.unit, weighed.barcode), ("كيلو", ""))  # weighed at the till: nothing to scan
        self.assertTrue(shelf.get(item_name="مكرونة 400 جم").barcode)
        self.assertTrue(ItemUnit.objects.filter(item__item_name="مياه معدنية 1.5 لتر", name_ar="كرتونة", factor=6).exists())
        self.assertTrue(Batch.objects.filter(item__item_name="لبن كامل الدسم 1 لتر").exists())
        bought = PurchaseInvoice.objects.get(invoice_number="DEMO-PI-SHELF")
        self.assertEqual((bought.status, bought.paid_now), ("posted", Decimal("0.00")))  # on credit: the cash never goes below zero
        self.assertTrue(all(cashbox.opening_balance >= 0 for cashbox in Cashbox.objects.all()))
        sold = SalesInvoice.objects.filter(invoice_number__startswith="DEMO-SI-SH-", status="posted")
        self.assertEqual(sold.count(), 10)
        self.assertTrue(all(invoice.remaining_due == 0 for invoice in sold))

    def test_the_till_starts_with_what_has_no_barcode(self):
        from barcode.services import item_catalog
        from sales.pos import quick_pick_ids

        self.fill("commercial", "grocery")
        first = [pick["name"] for pick in quick_pick_ids(item_catalog())][:8]
        self.assertIn("عيش بلدي (5 أرغفة)", first)
        self.assertIn("طماطم", first)
        self.assertFalse(Item.objects.filter(item_name__in=first).exclude(barcode="").exists())

    def test_running_it_twice_adds_nothing_more(self):
        from settings_core.management.commands.prepare_demo import Command as Prepare

        self.fill("commercial", "grocery")
        count = Item.objects.count(), SalesInvoice.objects.count()
        Prepare()._extras()
        self.assertEqual((Item.objects.count(), SalesInvoice.objects.count()), count)

    def test_other_shops_keep_their_five_items(self):
        self.fill("commercial", "fashion")
        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-SH-").exists())
