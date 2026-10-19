"""R2-5: the sample business looks like the activity that chose it."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from cashboxes.models import Cashbox
from master_data.models import Customer, Item
from purchases.models import PurchaseInvoice, PurchaseLine
from reports import selectors
from reports.dashboard_data import SharedReads
from settings_core import activity_fit, setup_catalog
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

    def fill(self, activity, sub, modules=None, capabilities=None):
        from settings_core.management.commands.prepare_demo import Command as Prepare
        from settings_core.setup_services import complete_setup

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_setup(ClientProfile.get_active(), activity, sub, modules if modules is not None else ",".join(activity_fit.default_modules(activity, sub)),
                       capabilities_raw=capabilities)
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

    def test_the_sample_month_pays_its_running_costs(self):
        """DM2 (W06): rent, electricity, internet and supplies, so net profit is not the gross profit."""

        from expenses.models import Expense
        from expenses.services import expense_total

        catalog = self.fill("commercial", "retail", modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        self.assert_a_working_business(catalog)
        self.assertEqual(set(Expense.objects.values_list("category__code", flat=True)), {"rent", "utilities", "internet_phone", "supplies"})
        first = timezone.localdate().replace(day=1)
        self.assertTrue(all(expense.expense_date >= first for expense in Expense.objects.all()))
        self.assertGreater(expense_total(first, timezone.localdate()), 0)
        self.assertGreater(sum(row["balance"] for row in selectors.cashbox_report()), 0)  # paid from what the drawer holds

    def test_no_expenses_without_the_expenses_module(self):
        from expenses.models import Expense

        self.fill("commercial", "retail", modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports")
        self.assertFalse(Expense.objects.exists())

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
        # RESTO-002: dishes have ingredients and a margin, the week has paid orders, and one waste adjustment.
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone

        from restaurant import insights
        from restaurant.models import Order

        self.assertIsNotNone(insights.plate_cost(dish))
        self.assertEqual(Order.objects.filter(status="paid", invoice__status="posted").count(), 9)
        today = timezone.localdate()
        report = insights.period_report(today - timedelta(days=7), today)
        self.assertEqual((report["totals"]["orders"], len(report["hours"]) >= 3), (9, True))
        self.assertEqual([(row["orders"], row["fees"], row["minutes"]) for row in insights.deliveries(today - timedelta(days=7), today)], [(1, Decimal("20.00"), 30)])
        self.assertEqual(len(insights.waste(today - timedelta(days=7), today)[0]), 1)

    def test_a_clinic_has_patients_visits_and_appointments(self):
        from appointments.models import Appointment
        from medical.models import ClinicalVisit, PatientFile

        catalog = self.fill("medical", "clinic")
        self.assert_a_working_business(catalog)
        self.assertEqual(PatientFile.objects.count(), 4)
        self.assertEqual(ClinicalVisit.objects.filter(doctor__name="د. أحمد سالم").count(), 5)
        self.assertEqual(Appointment.objects.filter(service__item_name="كشف").count(), 8)  # MED-008: one of them insured
        from medical.models import InsuredBill

        bill = InsuredBill.objects.get()
        self.assertEqual((bill.contract.insurer.name, bill.insurer_invoice.status, bill.patient_invoice.status), ("مصر للتأمين الصحي", "posted", "posted"))
        self.assertEqual(bill.insurer_share + bill.patient_share, bill.price)
        # MED-002: the clinic report and the follow-ups have something to show.
        from datetime import timedelta

        from medical import insights

        today = timezone.localdate()
        report = insights.period_report(today - timedelta(days=30), today)
        self.assertEqual((report["totals"]["returning"], report["totals"]["revenue"] > 0), (1, True))
        self.assertEqual([row["patient"].name for row in insights.missed_follow_ups(today)], ["سلمى حسن"])
        from medical.models import QueueTicket

        self.assertEqual(sorted(QueueTicket.objects.values_list("number", "status")), [(1, "called"), (2, "waiting")])  # MED-007

    def test_a_dental_clinic_has_a_chart_and_a_plan_in_stages(self):
        from medical.models import ToothRecord, TreatmentStep

        catalog = self.fill("medical", "dental")
        self.assert_a_working_business(catalog)
        self.assertEqual(ToothRecord.objects.count(), 5)
        self.assertEqual(sorted(TreatmentStep.objects.values_list("stage", "status")), [(1, "done"), (2, "planned"), (3, "planned")])

    def test_a_lab_has_tests_with_ranges_and_flagged_results(self):
        from medical.models import LabResult, LabTest

        catalog = self.fill("medical", "lab")
        self.assert_a_working_business(catalog)
        self.assertEqual(LabTest.objects.count(), 5)
        self.assertEqual(LabResult.objects.count(), 7)
        self.assertEqual(sorted(LabResult.objects.exclude(flag="normal").values_list("test__name", "flag")),
                         [("سكر صايم", "high"), ("سكر صايم", "high"), ("فصيلة الدم", ""), ("هيموجلوبين", "low")])

    def test_a_physio_centre_has_a_package_in_use(self):
        from medical import physio
        from agreements.models import Package as SessionPackage  # PKG-UNIFY

        catalog = self.fill("medical", "physio")
        self.assert_a_working_business(catalog)
        package = SessionPackage.objects.get()
        self.assertEqual(physio.state(package), {"used": 3, "left": 7, "status": "active"})

    def test_a_vet_has_owners_with_their_animals(self):
        from medical.models import Animal, ClinicalVisit

        catalog = self.fill("medical", "vet")
        self.assert_a_working_business(catalog)
        self.assertEqual(sorted(Animal.objects.values_list("name", "species")), [("روكي", "dog"), ("مشمش", "cat")])
        self.assertEqual(ClinicalVisit.objects.filter(animal__name="روكي").count(), 2)
        self.assertFalse(ClinicalVisit.objects.filter(animal__isnull=True).exists())

    def test_a_builder_has_projects_with_materials_and_a_progress_bill(self):
        from projects.models import Project

        catalog = self.fill("contracting", "general")
        self.assert_a_working_business(catalog)
        self.assertEqual(Project.objects.count(), 2)
        project = Project.objects.get(name__startswith="عمارة")
        self.assertTrue(project.issues.exists())
        self.assertTrue(project.invoices.filter(invoice__status="posted").exists())
        # CONTRACT-002: a bill of quantities, an advance, a posted and a draft certificate, a subcontractor, a budget.
        from ledger.reports import reconciliation
        from projects import contract

        self.assertEqual(project.boq.count(), 5)
        self.assertEqual(list(project.certificates.values_list("invoice__status", flat=True)), ["posted", "posted", "draft"])
        figures = contract.position(project)
        self.assertGreater(figures["retention_held"], 0)
        self.assertGreater(figures["recovered"], 0)
        self.assertGreater(figures["due_now"], 0)  # the first was collected; the second is still owed
        # CONTRACT-003: the second certificate is overdue, and the portfolio reads both projects.
        from projects import insights

        overdue, total, _ = insights.overdue_certificates()
        self.assertEqual([row["certificate"].number for row in overdue], [2])
        self.assertGreater(total, 0)
        rows, totals = insights.portfolio()
        self.assertEqual(len(rows), 2)
        self.assertGreater(totals["retention"], 0)
        self.assertTrue(project.subcontracts.get().bills.filter(invoice__status="posted").exists())
        self.assertEqual(project.budget.count(), 4)
        result = reconciliation()
        self.assertTrue(result["balanced"] and result["ok"], result["rows"])

    def test_a_professional_office_has_contracts_billed_by_the_month(self):
        from agreements import services as contracts
        from agreements.models import ServiceContract

        catalog = self.fill("services", "professional")
        self.assert_a_working_business(catalog)
        monthly = ServiceContract.objects.get(every="month")
        self.assertEqual(monthly.service.item_name, "مراجعة حسابات شهرية")
        self.assertEqual(monthly.bills.filter(invoice__status="posted").count(), 2)
        self.assertEqual(monthly.bills.filter(invoice__status="draft").count(), 1)
        self.assertGreater(contracts.unpaid(monthly), 0)
        self.assertEqual(len(contracts.due_periods(ServiceContract.objects.get(every="quarter"))), 1)
        self.assertEqual(self.client.get("/contracts/").status_code, 200)
        for activity, sub in (("services", "general"), ("services", "maintenance"), ("services", "digital_marketing"),
                              ("contracting", "maintenance_contracts")):
            self.assertIn("contracts", catalog_for(activity, sub).extras)
        self.assertNotIn("contracts", catalog_for("services", "beauty").extras)

    def test_a_salon_sells_session_packages(self):
        from agreements import packages
        from agreements.models import Package

        catalog = self.fill("services", "beauty")
        self.assert_a_working_business(catalog)
        posted, draft = Package.objects.get(sessions=6), Package.objects.get(sessions=4)
        self.assertEqual((packages.state(posted), packages.used(posted), posted.price), ("active", 2, Decimal("1275.00")))
        self.assertEqual(packages.state(draft), "draft")
        self.assertEqual(self.client.get("/contracts/packages/").status_code, 200)

    def test_an_office_bills_its_hours(self):
        from agreements import hours
        from agreements.models import TimeEntry

        catalog = self.fill("services", "digital_marketing")
        self.assert_a_working_business(catalog)
        self.assertEqual(TimeEntry.objects.count(), 6)
        self.assertEqual(TimeEntry.objects.filter(invoice__status="draft").count(), 3)
        waiting = {row["customer"].name: row["hours"] for row in hours.by_customer()}
        self.assertEqual(waiting, {catalog.customers[0]: Decimal("1.00"), catalog.customers[1]: Decimal("4.00")})
        self.assertEqual(self.client.get("/contracts/hours/").status_code, 200)

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

    def test_a_language_centre_teaches_in_levels(self):
        from education.models import PlacementTest, StudyGroup

        catalog = self.fill("education", "languages")
        self.assert_a_working_business(catalog)
        self.assertEqual(sorted(StudyGroup.objects.values_list("level", flat=True)), [1, 1, 2])
        self.assertEqual(sorted(PlacementTest.objects.values_list("level", flat=True)), [1, 2])

    def test_a_nursery_has_its_door_this_morning(self):
        from education import nursery
        from education.models import DayRecord, PickupPerson, Student

        catalog = self.fill("education", "nursery")
        self.assert_a_working_business(catalog)
        children = Student.objects.count()
        self.assertEqual(PickupPerson.objects.count(), 2 * children)  # a parent and one more for every child
        rows, counts = nursery.board()
        self.assertEqual(counts["away"], 1)  # one child not here yet
        self.assertEqual(counts["in"] + counts["left"], children - 1)
        self.assertTrue(all(record.picked_by.student_id == record.student_id for record in DayRecord.objects.exclude(picked_by=None)))
        self.assertEqual(set(Student.objects.values_list("stage", flat=True)), {"كي جي 1"})  # TRACK-ACTS W19: no prep-school pupils in a nursery


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class SampleFollowsSetupTests(TestCase):
    """DEMO-FIT: the sample data follows what was switched on in setup, not only the kind of business."""

    fill = SampleBusinessPerActivityTests.fill
    assert_a_working_business = SampleBusinessPerActivityTests.assert_a_working_business

    EVERY_OPTION = "pos,barcode,price_lists,units,variants,batches_expiry,serials,installments,vat,fixed_assets"

    def test_every_option_switched_on_gets_its_own_sample(self):
        from batches.models import Batch
        from fixed_assets.models import FixedAsset
        from fixed_assets.services import depreciation_between
        from installments.models import InstalmentPlan
        from installments.services import overdue_summary
        from pricing.models import PriceList
        from sales.models import SalesInvoice
        from serials.services import available_serials
        from shifts.models import Shift, ShiftStatus
        from taxes.services import vat_report
        from units.models import ItemUnit
        from variants.models import VariantGroup

        catalog = self.fill("commercial", "retail", capabilities=self.EVERY_OPTION)
        self.assert_a_working_business(catalog)  # the planted alerts and today's profit are untouched
        today = timezone.localdate()
        plan = InstalmentPlan.objects.get()
        self.assertEqual(plan.count, 4)
        self.assertGreaterEqual(overdue_summary(today)["count"], 1)  # the first one is already late
        self.assertEqual(list(PriceList.objects.get().customer_links.values_list("customer__customer_code", flat=True)), ["DEMO-CUST-04"])
        self.assertEqual(ItemUnit.objects.get().factor, Decimal("12"))
        group = VariantGroup.objects.get()
        self.assertEqual(group.variants.count(), 6)
        self.assertTrue(PurchaseLine.objects.filter(item__variant__group=group, invoice__status="posted").exists())
        self.assertTrue(available_serials().exists())
        self.assertEqual(Batch.objects.count(), 4)  # the goods on the shelf, two of them running out soon
        taxed = SalesInvoice.objects.filter(status="posted", tax_amount__gt=0)
        self.assertEqual(taxed.count(), 2)
        self.assertGreater(vat_report(today.replace(day=1), today)["net_tax"], 0)
        self.assertEqual(FixedAsset.objects.count(), 1)
        self.assertGreater(depreciation_between(today.replace(day=1), today), 0)
        self.assertEqual(Shift.objects.filter(status=ShiftStatus.OPEN, cashier__username="cashier").count(), 1)
        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-ITEM-", is_stock_tracked=True, barcode="").exists())
        # The dashboard's own tiles for these options (DASH-FIT) now have something true to show.
        from django.contrib.auth import get_user_model

        from reports.dashboard_fit import Fit, option_tiles

        owner = get_user_model().objects.get(username="owner")
        tiles = {tile["key"]: tile["value"] for tile in option_tiles(Fit.current(today), owner)}
        self.assertGreaterEqual(tiles["opt_instalments"], 1)
        self.assertEqual(tiles["opt_batches_soon"], 2)
        self.assertGreater(tiles["opt_vat"], 0)
        self.assertGreater(tiles["opt_assets"], 0)
        self.assertEqual(tiles["opt_shifts"], 1)
        self.assertGreater(tiles["opt_serials"], 0)

    def test_options_left_off_get_nothing(self):
        from batches.models import Batch
        from installments.models import InstalmentPlan
        from serials.models import SerialNumber
        from shifts.models import Shift

        self.fill("commercial", "pharmacy", capabilities="")
        self.assertFalse(InstalmentPlan.objects.exists() or Shift.objects.exists() or SerialNumber.objects.exists())
        self.assertFalse(Batch.objects.exists())  # a pharmacy that switched expiry dates off has none, even from its catalog
        from sales.models import SalesInvoice

        self.assertFalse(SalesInvoice.objects.filter(tax_amount__gt=0).exists())

    def test_a_salon_starts_with_appointments_on_and_gets_a_day_of_them(self):
        from appointments.models import Appointment

        self.fill("services", "beauty")
        self.assertTrue(Appointment.objects.exists())

    def test_an_extra_whose_module_is_off_is_left_out(self):
        from appointments.models import Appointment

        modules = ",".join(slug for slug in activity_fit.default_modules("services", "beauty") if slug != "appointments_visits")
        self.fill("services", "beauty", modules=modules)
        self.assertFalse(Appointment.objects.exists())  # switched off: its screens are closed, so no appointments are made

    def test_running_the_options_twice_adds_nothing_more(self):
        from installments.models import InstalmentPlan
        from settings_core.management.commands.prepare_demo import Command as Prepare

        self.fill("commercial", "retail", capabilities=self.EVERY_OPTION)
        Prepare()._extras()
        self.assertEqual(InstalmentPlan.objects.count(), 1)


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

    def test_a_rerun_never_rewrites_a_used_cashbox_opening(self):
        from hesba_testing.factories import make_user

        make_user(username="rerun_owner", is_superuser=True)
        call_command("seed_demo_business", verbosity=0, stdout=StringIO())
        Cashbox.objects.filter(cashbox_code="DEMO-CASH-01").update(opening_balance=7777)
        call_command("seed_demo_business", verbosity=0, stdout=StringIO())
        self.assertEqual(Cashbox.objects.get(cashbox_code="DEMO-CASH-01").opening_balance, 7777)
