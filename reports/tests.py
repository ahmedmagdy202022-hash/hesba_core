import json

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class LoginAndDeviceShellSmokeTests(TestCase):
    def test_login_route_returns_200(self):
        response = self.client.get("/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تسجيل الدخول")
        self.assertContains(response, "name=\"language\"")
        self.assertContains(response, "hesba/brand/login_web.final.png")
        self.assertContains(response, "hesba/brand/login_tablet.png")
        self.assertContains(response, "hesba/brand/login_mobile.final.png")
        self.assertContains(response, "#05243f")
        self.assertContains(response, "#07939a")
        self.assertContains(response, "#d7aa4b")
        self.assertNotContains(response, "hesba/brand/login_web.png")
        self.assertNotContains(response, "hesba/brand/login_mobile.png")
        self.assertNotContains(response, "109_LOGIN_AND_DEVICE_SHELL_STABILIZATION")
        self.assertNotContains(response, "111_LOGIN_EXACT_FROM_106_STYLE")
        self.assertNotContains(response, "Desktop ready")
        self.assertNotContains(response, "PWA ready")

    def test_root_redirects_to_login(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("login"))

    def test_existing_safe_routes_still_return_200(self):
        for path in ["/dashboard/", "/reports/", "/status/", "/home/"]:
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)

    def test_manifest_start_url_points_to_login(self):
        manifest_path = settings.BASE_DIR / "static" / "hesba" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["start_url"], "/login/")


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
