"""HG-084: the data migration keeps an upgraded clinic's clinicians working and takes the files from everyone else.

In the style of ``agreements/tests_pkg_unify.py``: build the old state with the historical models, migrate
forward, check, and (for the reverse) migrate back.
"""

from datetime import date
from decimal import Decimal as D

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("medical", "0009_insured_bill_kinds")]
AFTER = [("medical", "0010_hg084_clinician_access")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor


class MigrationCase(TransactionTestCase):
    # As in tests_pkg_unify: restore the migration seed instead of letting the flush re-create content types.
    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def old_apps(self):
        executor = _migrate(BEFORE)
        nodes = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "medical"] + BEFORE  # every other app as it is now
        return executor.loader.project_state(nodes).apps

    def profile_role(self, username):
        from accounts.models import UserProfile

        profile = UserProfile.objects.filter(user__username=username).select_related("role").first()
        return None if profile is None else (profile.role.code if profile.role else None)


class UpgradedClinicTests(MigrationCase):
    def setUp(self):
        old = self.old_apps()
        User = old.get_model("auth", "User")
        Role = old.get_model("permissions", "Role")
        Profile = old.get_model("accounts", "UserProfile")
        Employee = old.get_model("staff", "Employee")
        old.get_model("settings_core", "ClientProfile").objects.create(client_code="C", legal_name="عيادة", display_name="عيادة",
                                                                       activity_slug="medical", sub_activity_slug="clinic")
        owner = User.objects.create(username="hg_owner")
        Profile.objects.create(user=owner, role=Role.objects.get(code="owner"))
        patient = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="منى")
        service = old.get_model("master_data", "Item").objects.create(item_code="SRV", item_name="كشف", is_stock_tracked=False)

        def user(username, role=None, employee=True, profile=True, profile_active=True, employee_active=True):
            account = User.objects.create(username=username)
            if profile:
                Profile.objects.create(user=account, role=Role.objects.get(code=role) if role else None, active=profile_active)
            if employee:
                return Employee.objects.create(code=f"E-{username}", name=username, user=account, active=employee_active), account
            return None, account

        visits = old.get_model("medical", "ClinicalVisit")

        def visit(doctor):
            visits.objects.create(patient=patient, doctor=doctor, visit_date=date(2026, 10, 1), diagnosis="برد", created_by=owner)

        self.employees = {}
        dr, _ = user("dr_cashier", "cashier")  # the doctor whose login was a cashier's
        visit(dr)
        user("desk", "cashier")  # reception on the payroll: linked, no clinical record
        user("keeper", "stock_keeper")
        therapist, _ = user("therapist", "stock_keeper")
        location = old.get_model("master_data", "Location").objects.create(location_code="CL", name_ar="العيادة")
        invoice = old.get_model("sales", "SalesInvoice").objects.create(invoice_number="SI-1", invoice_date=date(2026, 10, 1), customer=patient,
                                                                        selling_location=location, status="posted", created_by=owner)
        package = old.get_model("agreements", "Package").objects.create(number="PK-1", customer=patient, service=service, sessions=10, price=D("1"),
                                                                        invoice=invoice, created_by=owner)
        old.get_model("agreements", "PackageUse").objects.create(package=package, used_on=date(2026, 10, 2), employee=therapist, created_by=owner)
        _, tech = user("lab_tech", "stock_keeper")
        test = old.get_model("medical", "LabTest").objects.create(name="سكر")
        old.get_model("medical", "LabResult").objects.create(patient=patient, test=test, taken_on=date(2026, 10, 3), value=D("90"), entered_by=tech)
        dentist, _ = user("dentist", "cashier")
        old.get_model("medical", "TreatmentStep").objects.create(patient=patient, service=service, price=D("1"), doctor=dentist, created_by=owner)
        boss, _ = user("dr_manager", "manager")
        visit(boss)
        visit(user("dr_no_role")[0])
        visit(user("dr_no_profile", profile=False)[0])
        visit(user("dr_off", "cashier", profile_active=False)[0])
        visit(user("dr_left", "cashier", employee_active=False)[0])
        visit(Employee.objects.create(code="E-NOLOGIN", name="د. بلا حساب"))  # most doctors never sign in
        _migrate(AFTER)

    def test_clinicians_keep_access_and_everyone_else_loses_it(self):
        from django.contrib.auth import get_user_model

        from medical import services
        from permissions.models import Role

        roles = {name: self.profile_role(name) for name in ("dr_cashier", "desk", "keeper", "therapist", "lab_tech", "dentist", "dr_manager",
                                                             "dr_no_role", "dr_no_profile", "dr_off", "dr_left")}
        self.assertEqual(roles, {"dr_cashier": "custom_hg084_cashier", "desk": "cashier", "keeper": "stock_keeper",
                                 "therapist": "custom_hg084_stock_keeper", "lab_tech": "custom_hg084_stock_keeper", "dentist": "custom_hg084_cashier",
                                 "dr_manager": "manager", "dr_no_role": "doctor", "dr_no_profile": "doctor", "dr_off": "cashier", "dr_left": "cashier"})
        users = {u.username: u for u in get_user_model().objects.all()}
        reads = {name: services.can_view(users[name]) for name in roles}
        self.assertEqual(reads, {"dr_cashier": True, "desk": False, "keeper": False, "therapist": True, "lab_tech": True, "dentist": True,
                                 "dr_manager": True, "dr_no_role": True, "dr_no_profile": True, "dr_off": False, "dr_left": False})
        for name in ("dr_cashier", "therapist", "dentist"):
            with self.subTest(name=name):
                self.assertTrue(services.can_write(users[name]))

    def test_a_copy_keeps_everything_its_role_could_do(self):
        from permissions.models import Role

        def codes(code):
            return set(Role.objects.get(code=code).rolepermission_set.filter(allow=True).values_list("permission__code", flat=True))

        for source in ("cashier", "stock_keeper"):
            with self.subTest(source=source):
                clone = Role.objects.get(code=f"custom_hg084_{source}")
                self.assertEqual(codes(clone.code), codes(source) | {"medical.treat_patients"})
                self.assertFalse(clone.is_system_role)  # the owner can edit or retire it from the roles screen
                self.assertIn("كشف المرضى", clone.name_ar)
                self.assertIn("HG-084", clone.description)
        self.assertEqual(codes("doctor"), {"medical.treat_patients"})
        self.assertNotIn("medical.treat_patients", codes("cashier"))  # the seeded roles stay as they were

    def test_every_move_is_audited(self):
        from accounts.models import UserProfile
        from audit.models import AuditLog

        rows = AuditLog.objects.filter(action="hg084_clinician_role")
        self.assertEqual(rows.count(), 6)
        dr = UserProfile.objects.get(user__username="dr_cashier")
        row = rows.get(object_id=str(dr.pk))
        self.assertEqual((row.before_data["role"], row.after_data["role"], row.event_type), ("cashier", "custom_hg084_cashier", "permission_change"))

    def test_the_owner_can_make_a_doctor_role_of_his_own(self):
        """The owner holds the new permission, so the roles screen lets him put it in a role (role_beyond_actor)."""

        from django.contrib.auth import get_user_model

        from permissions.role_services import create_role

        owner = get_user_model().objects.get(username="hg_owner")
        role = create_role(name_ar="استقبال ودكتور", codes=["sales.create_sales_invoice", "medical.treat_patients"], actor=owner)
        self.assertTrue(role.rolepermission_set.filter(permission__code="medical.treat_patients").exists())

    def test_the_reverse_puts_everyone_back(self):
        from accounts.models import UserProfile
        from permissions.models import Permission, Role

        _migrate(BEFORE)
        roles = dict(UserProfile.objects.values_list("user__username", "role__code"))
        for name, role in (("dr_cashier", "cashier"), ("therapist", "stock_keeper"), ("lab_tech", "stock_keeper"), ("dentist", "cashier"),
                           ("dr_manager", "manager"), ("dr_no_role", None), ("dr_no_profile", None), ("desk", "cashier")):
            with self.subTest(name=name):
                self.assertEqual(roles[name], role)
        self.assertFalse(Role.objects.filter(code__startswith="custom_hg084_").exists())
        self.assertFalse(Role.objects.filter(code="doctor").exists())
        self.assertFalse(Permission.objects.filter(code="medical.treat_patients").exists())
        self.assertEqual(Permission.objects.get(code="medical.view_records").name_ar, "عرض الملفات الطبية للمرضى")
        _migrate(AFTER)  # and forward again lands in the same place
        self.assertEqual(self.profile_role("dr_cashier"), "custom_hg084_cashier")
        self.assertEqual(self.profile_role("desk"), "cashier")


class FreshInstallTests(MigrationCase):
    def test_nobody_to_move_on_a_fresh_database(self):
        from audit.models import AuditLog
        from permissions.models import Permission, Role

        self.old_apps()
        _migrate(AFTER)
        self.assertFalse(Role.objects.filter(code__startswith="custom_hg084_").exists())
        self.assertFalse(AuditLog.objects.filter(action="hg084_clinician_role").exists())
        doctor = Role.objects.get(code="doctor")
        self.assertTrue(doctor.is_system_role)
        holders = set(Permission.objects.get(code="medical.treat_patients").rolepermission_set.values_list("role__code", flat=True))
        self.assertEqual(holders, {"doctor", "owner", "manager"})
        self.assertIn("للإدارة", Permission.objects.get(code="medical.view_records").name_ar)  # the practice-wide one says so on the roles screen

    def test_a_salons_package_sessions_make_nobody_a_clinician(self):
        old = self.old_apps()
        User = old.get_model("auth", "User")
        Role = old.get_model("permissions", "Role")
        old.get_model("settings_core", "ClientProfile").objects.create(client_code="S", legal_name="صالون", display_name="صالون",
                                                                       activity_slug="services", sub_activity_slug="beauty")
        owner = User.objects.create(username="s_owner")
        stylist = User.objects.create(username="stylist")
        old.get_model("accounts", "UserProfile").objects.create(user=stylist, role=Role.objects.get(code="cashier"))
        employee = old.get_model("staff", "Employee").objects.create(code="E-1", name="هالة", user=stylist)
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="C-1", name="عميلة")
        service = old.get_model("master_data", "Item").objects.create(item_code="SRV", item_name="سشوار", is_stock_tracked=False)
        location = old.get_model("master_data", "Location").objects.create(location_code="SH", name_ar="الصالون")
        invoice = old.get_model("sales", "SalesInvoice").objects.create(invoice_number="SI-1", invoice_date=date(2026, 10, 1), customer=customer,
                                                                        selling_location=location, status="posted", created_by=owner)
        package = old.get_model("agreements", "Package").objects.create(number="PK-1", customer=customer, service=service, sessions=10, price=D("1"),
                                                                        invoice=invoice, created_by=owner)
        old.get_model("agreements", "PackageUse").objects.create(package=package, used_on=date(2026, 10, 2), employee=employee, created_by=owner)
        _migrate(AFTER)
        self.assertEqual(self.profile_role("stylist"), "cashier")
