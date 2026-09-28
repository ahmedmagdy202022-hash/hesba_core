"""STAFF-001: employees & technicians."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from .models import Employee
from .services import save_employee


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class StaffTests(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="services", sub_activity="maintenance", modules="customers,items_services,cashboxes,reports,employees_technicians")
        self.owner = person(RoleCode.OWNER, "staff_owner")
        self.client.force_login(self.owner)

    def test_add_list_edit_and_audit(self):
        page = self.client.post(reverse("staff:list"), {"name": "أحمد الفني", "title": "فني تكييف", "phone": "٠١٠٠ ١٢٣ ٤٥٦٧", "commission_percent": "12.5", "active": "1"})
        employee = Employee.objects.get()
        self.assertRedirects(page, f"{reverse('staff:list')}?lang=ar", fetch_redirect_response=False)
        self.assertEqual((employee.code, employee.phone, employee.commission_percent, employee.active), ("E-0001", "01001234567", D("12.50"), True))
        self.assertContains(self.client.get(reverse("staff:list")), 'data-employee-row="E-0001"')
        self.client.post(reverse("staff:detail", args=[employee.pk]), {"name": "أحمد الفني", "title": "كبير الفنيين", "phone": "01001234567", "commission_percent": "15", "user": str(self.owner.pk)})
        employee.refresh_from_db()
        self.assertEqual((employee.title, employee.commission_percent, employee.user, employee.active), ("كبير الفنيين", D("15.00"), self.owner, False))
        self.assertEqual(list(AuditLog.objects.filter(module="staff").values_list("action", flat=True).order_by("id")), ["create_employee", "update_employee"])

    def test_refusals(self):
        save_employee({"name": "A", "phone": "0100"}, self.owner)
        for data, text in (({"name": " "}, "اكتب اسم الموظف"), ({"name": "B", "commission_percent": "150"}, "من 0 لـ 100"),
                           ({"name": "B", "commission_percent": "abc"}, "من 0 لـ 100"), ({"name": "B", "phone": "0100"}, "مسجّل لموظف تاني")):
            with self.subTest(data=data):
                page = self.client.post(reverse("staff:list"), data)
                self.assertContains(page, text)
        self.assertEqual(Employee.objects.count(), 1)

    def test_module_gate_nav_and_permissions(self):
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), reverse("staff:list"))
        cashier = person(RoleCode.CASHIER, "staff_cashier")
        self.client.force_login(cashier)
        self.assertIn(self.client.post(reverse("staff:list"), {"name": "X"}).status_code, (302, 403))
        self.assertFalse(Employee.objects.exists())

    def test_the_module_switched_off_closes_the_screens(self):
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(self.profile, "employees_technicians", False, self.owner)
        self.assertNotEqual(self.client.get(reverse("staff:list")).status_code, 200)
