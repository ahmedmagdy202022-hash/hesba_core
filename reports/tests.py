import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from permissions.models import Role, RoleCode


class LoginAndDeviceShellSmokeTests(TestCase):
    def test_login_route_returns_200(self):
        response = self.client.get("/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تسجيل الدخول")
        self.assertContains(response, "الدخول إلى حسبة")
        self.assertContains(response, "hesba/css/login.css")
        self.assertContains(response, "data-lang-option")

    def test_root_redirects_to_login(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("login"))

    def test_existing_safe_routes_still_return_200(self):
        for path in ["/dashboard/", "/reports/", "/status/"]:
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)

    def test_manifest_start_url_points_to_login(self):
        manifest_path = settings.BASE_DIR / "static" / "hesba" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["start_url"], "/login/")


class SetupGateSmokeTests(TestCase):
    def test_setup_gate_route_returns_approved_gate(self):
        response = self.client.get("/setup/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "جهّز حِسبة حسب نشاطك")
        self.assertContains(response, "نظام إدارة متكامل قابل للتخصيص حسب نشاطك")
        self.assertContains(response, "hesba/css/setup.css")
        self.assertContains(response, "hesba/icons/hesba-icon.svg")
        self.assertContains(response, "/setup/activity/")
        self.assertContains(response, "تسجيل الخروج")
        self.assertContains(response, "تجاري")
        self.assertContains(response, "مطاعم")
        self.assertContains(response, "data-lang-option")
        self.assertNotContains(response, "ERP System")

    def test_setup_gate_is_english_ready(self):
        response = self.client.get("/setup/?lang=en")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Set up Hesba for your activity")
        self.assertContains(response, "customizable management system")
        self.assertContains(response, "Start Setup")
        self.assertContains(response, "Logout")

    def test_authenticated_non_owner_cannot_start_setup(self):
        user = get_user_model().objects.create_user(username="cashier", password="test-pass")
        role = Role.objects.create(code=RoleCode.CASHIER, name_ar="كاشير", name_en="Cashier")
        UserProfile.objects.create(user=user, role=role, display_name="Cashier")
        self.client.force_login(user)

        response = self.client.get("/setup/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ليس لديك صلاحية إعداد النظام")
        self.assertContains(response, "يرجى التواصل مع مالك النظام")
        self.assertContains(response, "aria-disabled=\"true\"")


class FirstUiNavigationMapTests(TestCase):
    def test_home_page_renders_first_ui_navigation_map(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP")
        self.assertContains(response, "خريطة تشغيل أول شاشة UI")
        self.assertContains(response, "فتح لوحة Admin")
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Reports")
        self.assertContains(response, "Status")

    def test_home_page_keeps_full_business_cycle_visible(self):
        response = self.client.get(reverse("home"))

        required_labels = [
            "Supplier",
            "Purchase Invoice",
            "Inventory by Location",
            "Sales Invoice",
            "Customer",
            "Cashbox",
            "Reports",
        ]
        for label in required_labels:
            self.assertContains(response, label)

    def test_home_page_documents_protected_rules(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, "المبيعات لا تنشئ مستحقات للموردين")
        self.assertContains(response, "المشتريات لا تنشئ مديونية للعملاء")
        self.assertContains(response, "الخزن تتحرك بالمبلغ المدفوع فعليًا فقط")
        self.assertContains(response, "التقارير قراءة فقط")

    def test_dashboard_snapshot_page_renders_read_only_checkpoint(self):
        response = self.client.get(reverse("dashboard_snapshot"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "094_FOUNDATION_DASHBOARD_SNAPSHOT")
        self.assertContains(response, "Dashboard Snapshot قراءة فقط")
        self.assertContains(response, "KPIs آمنة لاحقًا")
        self.assertContains(response, "حالة المخزون")
        self.assertContains(response, "حالة الخزن")
        self.assertContains(response, "Status")

    def test_dashboard_snapshot_keeps_sensitive_finance_protected(self):
        response = self.client.get(reverse("dashboard_snapshot"))

        self.assertContains(response, "الربح")
        self.assertContains(response, "التكلفة")
        self.assertContains(response, "صلاحيات حقيقية")

    def test_report_hub_renders_read_only_report_map(self):
        response = self.client.get(reverse("report_hub"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "096_FOUNDATION_READ_ONLY_REPORT_HUB")
        self.assertContains(response, "مركز التقارير قراءة فقط")
        self.assertContains(response, "Customer Report")
        self.assertContains(response, "Supplier Report")
        self.assertContains(response, "Inventory Report")
        self.assertContains(response, "Cashbox Report")
        self.assertContains(response, "Status Counts")

    def test_report_hub_keeps_reports_read_only_and_profit_protected(self):
        response = self.client.get(reverse("report_hub"))

        self.assertContains(response, "التقارير قراءة فقط")
        self.assertContains(response, "Profit Report")
        self.assertContains(response, "Sales - Cost of Goods Sold")
        self.assertContains(response, "صلاحيات حقيقية")

    def test_status_counts_report_renders_expanded_non_sensitive_counts(self):
        response = self.client.get(reverse("status_counts_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100_FOUNDATION_EXPANDED_SAFE_STATUS_COUNTS")
        self.assertContains(response, "تقرير حالة آمن موسّع")
        self.assertContains(response, "Suppliers")
        self.assertContains(response, "Customers")
        self.assertContains(response, "Items")
        self.assertContains(response, "Locations")
        self.assertContains(response, "Cashboxes")
        self.assertContains(response, "Purchase Invoices")
        self.assertContains(response, "Purchase Lines")
        self.assertContains(response, "Sales Invoices")
        self.assertContains(response, "Sales Lines")
        self.assertContains(response, "Supplier Payments")
        self.assertContains(response, "Customer Payments")
        self.assertContains(response, "Stock Movements")
        self.assertContains(response, "Cashbox Movements")

    def test_status_counts_report_does_not_expose_sensitive_finance(self):
        response = self.client.get(reverse("status_counts_report"))

        self.assertContains(response, "لا يعرض مبالغ أو أرصدة مالية")
        self.assertContains(response, "لا يعرض تكلفة أو ربح")
        self.assertContains(response, "No money totals")
        self.assertContains(response, "No balances")
        self.assertContains(response, "No cost")
        self.assertContains(response, "No profit")

    def test_shared_top_navigation_separates_dashboard_reports_and_status(self):
        for url_name in ["home", "dashboard_snapshot", "report_hub", "status_counts_report"]:
            response = self.client.get(reverse(url_name))
            self.assertContains(response, "Dashboard")
            self.assertContains(response, "Reports")
            self.assertContains(response, "Status")
