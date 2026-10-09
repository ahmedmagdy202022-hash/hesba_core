"""R2-5: the sample business looks like the activity that chose it."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from cashboxes.models import Cashbox
from master_data.models import Customer, Item
from purchases.models import PurchaseInvoice, PurchaseLine
from reports import selectors
from reports.dashboard_data import SharedReads
from settings_core import setup_catalog
from settings_core.models import ClientProfile

from .demo_catalogs import BASE, CATALOGS, all_catalogs, catalog_for
from .management.commands.seed_demo_business import Command as Seed


class CatalogShapeTests(TestCase):
    """Every catalog keeps the roles the seed and the alerts rely on."""

    def test_every_sub_activity_of_the_wizard_has_a_catalog(self):
        for activity, subs in setup_catalog.SUB_ACTIVITY_LABELS.items():
            self.assertIn(activity, CATALOGS)
            for sub in subs:
                self.assertIsNotNone(catalog_for(activity, sub), f"{activity}.{sub}")
        self.assertIs(catalog_for("unknown", "x"), BASE)
        self.assertEqual(catalog_for("commercial", "nothing-like-it").key, "commercial.retail")

    def test_every_catalog_keeps_the_roles(self):
        for catalog in all_catalogs():
            with self.subTest(catalog.key):
                self.assertEqual((len(catalog.items), len(catalog.customers), len(catalog.suppliers)), (5, 4, 2))
                # Item 4 runs low and item 5 sells out: both are goods on the shelf.
                self.assertTrue(catalog.items[3].stock and catalog.items[4].stock)
                for index, product in enumerate(catalog.items):
                    cost = Seed._stocking_cost(catalog, index)
                    self.assertGreater(product.sale, cost, product.name)  # sold at a profit
                    if index in catalog.recipes:
                        self.assertTrue(product.stock)
                        for raw_index, quantity in catalog.recipes[index][1]:
                            self.assertLess(raw_index, len(catalog.raw))
                            self.assertGreater(quantity, 0)
                    elif product.stock:
                        self.assertGreater(product.purchase, 0, product.name)
                codes = [supply.code for supply in catalog.raw]
                self.assertEqual(len(codes), len(set(codes)))

    def test_the_names_fit_the_business(self):
        names = lambda activity, sub: " ".join(p.name for p in catalog_for(activity, sub).items)  # noqa: E731
        self.assertIn("مجم", names("commercial", "pharmacy"))
        self.assertIn("أسمنت", names("contracting", "general"))
        self.assertIn("كشف", names("medical", "clinic"))
        self.assertIn("كرتونة", names("commercial", "wholesale"))
        self.assertNotIn("قميص", names("restaurants", "restaurant"))
        dishes = catalog_for("restaurants", "restaurant").items[:3]
        self.assertFalse(any(dish.stock for dish in dishes))  # dishes are made to order, never bought
        self.assertTrue(catalog_for("manufacturing", "garments").recipes)


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class SampleBusinessPerActivityTests(TestCase):
    """The demo's "add sample data", run for one kind of business at a time."""

    def fill(self, activity, sub):
        from settings_core.management.commands.prepare_demo import Command as Prepare
        from settings_core.setup_services import complete_setup

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_setup(ClientProfile.get_active(), activity, sub, ",".join(setup_catalog.default_modules(activity)))
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        prepare = Prepare()
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._spread_sale_times()
        prepare._extras()
        return catalog_for(activity, sub)

    def assert_a_working_business(self, catalog):
        names = list(Item.objects.filter(item_code__startswith="DEMO-ITEM-").order_by("item_code").values_list("item_name", flat=True))
        self.assertEqual(names, [product.name for product in catalog.items])
        self.assertEqual(sorted(Customer.objects.filter(customer_code__startswith="DEMO-CUST-").values_list("name", flat=True)),
                         sorted(catalog.customers))
        # The same three planted problems as every demo.
        alerts = selectors.stock_alert_counts()
        self.assertEqual((alerts["out_of_stock"], alerts["low_stock"]), (1, 1))
        self.assertTrue(SharedReads().customers_over_limit())
        self.assertGreater(sum(row["balance"] for row in selectors.cashbox_report()), 0)
        self.assertGreater(selectors.profit_totals(date_from=timezone.localdate(), date_to=timezone.localdate())["profit"], 0)
        self.assertTrue(self.client.login(username="owner", password="Demo-pass-1"))
        self.assertEqual(self.client.get("/dashboard/?lang=ar").status_code, 200)

    def test_a_pharmacy_sells_medicines_with_expiry_dates(self):
        from datetime import timedelta

        from batches.models import Batch
        from restaurant.models import DiningTable

        catalog = self.fill("commercial", "pharmacy")
        self.assert_a_working_business(catalog)
        self.assertEqual(Item.objects.get(item_code="DEMO-ITEM-01").unit, "شريط")
        soon = timezone.localdate() + timedelta(days=60)
        self.assertEqual(Batch.objects.filter(expiry_date__lte=soon).count(), 2)  # two batches run out within 60 days
        self.assertFalse(DiningTable.objects.exists())

    def test_a_restaurant_has_tables_and_dishes_it_never_buys(self):
        from restaurant.models import DiningTable

        catalog = self.fill("restaurants", "restaurant")
        self.assert_a_working_business(catalog)
        self.assertEqual(DiningTable.objects.count(), 6)
        dish = Item.objects.get(item_code="DEMO-ITEM-01")
        self.assertFalse(dish.is_stock_tracked)
        self.assertFalse(PurchaseLine.objects.filter(item=dish).exists())
        self.assertTrue(PurchaseLine.objects.filter(item__item_code="DEMO-RAW-01").exists())  # the kitchen's ingredients

    def test_a_clinic_has_patients_visits_and_appointments(self):
        from appointments.models import Appointment
        from medical.models import ClinicalVisit, PatientFile

        catalog = self.fill("medical", "clinic")
        self.assert_a_working_business(catalog)
        self.assertEqual(PatientFile.objects.count(), 3)
        self.assertEqual(ClinicalVisit.objects.filter(doctor__name="د. أحمد سالم").count(), 3)
        self.assertEqual(Appointment.objects.filter(service__item_name="كشف").count(), 4)

    def test_a_builder_has_projects_with_materials_and_a_progress_bill(self):
        from projects.models import Project

        catalog = self.fill("contracting", "general")
        self.assert_a_working_business(catalog)
        self.assertEqual(Project.objects.count(), 2)
        project = Project.objects.get(name__startswith="عمارة")
        self.assertTrue(project.issues.exists())
        self.assertTrue(project.invoices.filter(invoice__status="posted").exists())

    def test_a_factory_makes_its_goods_from_materials(self):
        from manufacturing.models import ProductionOrder, ProductionRun, Recipe

        catalog = self.fill("manufacturing", "garments")
        self.assert_a_working_business(catalog)
        products = Item.objects.filter(item_code__startswith="DEMO-ITEM-")
        # Nothing it sells was bought: it was all made, from bought cloth and thread.
        self.assertFalse(PurchaseLine.objects.filter(item__in=products).exists())
        self.assertTrue(PurchaseLine.objects.filter(item__item_code__startswith="DEMO-RAW-").exists())
        self.assertEqual(Recipe.objects.count(), 5)
        self.assertTrue(ProductionRun.objects.exists())
        self.assertEqual(sorted(ProductionOrder.objects.values_list("stage_index", flat=True)), [0, 1, 2])

    def test_a_tutoring_center_sells_subscriptions_and_notes(self):
        catalog = self.fill("education", "tutoring_center")
        self.assert_a_working_business(catalog)
        self.assertFalse(Item.objects.get(item_code="DEMO-ITEM-01").is_stock_tracked)


@override_settings(DEBUG=True)
class BaseBusinessUnchangedTests(TestCase):
    """Without a chosen activity the seed builds exactly the shop it always did."""

    def test_the_base_shop(self):
        from hesba_testing.factories import make_user

        make_user(username="base_owner", is_superuser=True)
        call_command("seed_demo_business", verbosity=0, stdout=StringIO())
        self.assertEqual(Item.objects.get(item_code="DEMO-ITEM-01").item_name, "قميص قطن")
        self.assertEqual(Cashbox.objects.get(cashbox_code="DEMO-CASH-01").opening_balance, 8000)
        self.assertEqual(Customer.objects.get(customer_code="DEMO-CUST-03").credit_limit, 1500)
        self.assertEqual(PurchaseInvoice.objects.get(invoice_number="DEMO-PI-001").paid_now, 6000)
        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-RAW-").exists())
