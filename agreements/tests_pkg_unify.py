"""PKG-UNIFY: the data migration moves physiotherapy packages, and their sessions, into the shared package tables."""

from datetime import date
from decimal import Decimal as D

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("agreements", "0002_package_packageuse_timeentry"), ("medical", "0007_insurance")]
AFTER = [("agreements", "0003_move_physio_packages"), ("medical", "0008_remove_session_packages")]


class MovePhysioPackagesTests(TransactionTestCase):
    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        old = executor.loader.project_state(BEFORE).apps
        user = old.get_model("auth", "User").objects.create(username="pkg_mover")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="منى")
        service = old.get_model("master_data", "Item").objects.create(item_code="SRV-PHY", item_name="جلسة", is_stock_tracked=False)
        location = old.get_model("master_data", "Location").objects.create(location_code="CL", name_ar="العيادة")
        invoice = old.get_model("sales", "SalesInvoice").objects.create(invoice_number="SI-00009", invoice_date=date(2026, 10, 1), customer=customer,
                                                                        selling_location=location, status="posted", created_by=user)
        employee = old.get_model("staff", "Employee").objects.create(code="E-1", name="د. ياسمين")
        old.get_model("agreements", "Package").objects.create(number="PK-0004", customer=customer, service=service, sessions=1, price=D("1"),
                                                              invoice=old.get_model("sales", "SalesInvoice").objects.create(
                                                                  invoice_number="SI-00010", invoice_date=date(2026, 10, 1), customer=customer,
                                                                  selling_location=location, status="posted", created_by=user), created_by=user)
        package = old.get_model("medical", "SessionPackage").objects.create(patient=customer, service=service, sessions=10, price=D("3000"),
                                                                           invoice=invoice, notes="ظهر", created_by=user)
        sessions = old.get_model("medical", "PackageSession")
        sessions.objects.create(package=package, used_on=date(2026, 10, 2), therapist=employee, notes="أول جلسة", created_by=user)
        sessions.objects.create(package=package, used_on=date(2026, 10, 3), therapist=None, cancelled=True, created_by=user)
        self.ids = {"customer": customer.pk, "service": service.pk, "invoice": invoice.pk, "employee": employee.pk}
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(AFTER)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_each_package_and_session_is_moved_as_it_was(self):
        from agreements.models import Package, PackageUse

        moved = Package.objects.get(invoice_id=self.ids["invoice"])
        self.assertEqual((moved.number, moved.customer_id, moved.service_id, moved.sessions, moved.price, moved.notes),
                         ("PK-0005", self.ids["customer"], self.ids["service"], 10, D("3000.00"), "ظهر"))  # numbered after the existing PK-0004
        uses = list(PackageUse.objects.filter(package=moved).order_by("used_on"))
        self.assertEqual([(u.used_on, u.employee_id, u.cancelled, u.notes) for u in uses],
                         [(date(2026, 10, 2), self.ids["employee"], False, "أول جلسة"), (date(2026, 10, 3), None, True, "")])
        from medical import physio

        self.assertEqual(physio.state(moved), {"used": 1, "left": 9, "status": "active"})  # the cancelled session gave its place back

