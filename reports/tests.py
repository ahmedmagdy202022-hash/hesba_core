import json

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


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
        for path in ["/setup/", "/dashboard/", "/reports/", "/status/"]:
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)

    def test_manifest_start_url_points_to_login(self):
        manifest_path = settings.BASE_DIR / "static" / "hesba" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["start_url"], "/login/")


class SetupGateWebSmokeTests(TestCase):
    def test_setup_gate_route_renders_component_rebuild(self):
        response = self.client.get(reverse("setup_gate"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "بوابة الإعداد - حِسْبَة")
        self.assertContains(response, "hesba/css/setup_gate_web.css")
        self.assertContains(response, "setup-shell")
        self.assertContains(response, "setup-header")
        self.assertContains(response, "hero-card")
        self.assertContains(response, "hero-actions")
        self.assertContains(response, "steps-section")
        self.assertContains(response, "step-card")
        self.assertContains(response, "activities-section")
        self.assertContains(response, "activity-pill")
        self.assertContains(response, "hesba/setup_gate/icons/activity_commercial_icon.png")
        self.assertNotContains(response, "setup_gate_web_background_approved.png")
        self.assertContains(response, "جهّز حِسْبَة حسب نشاطك")
        self.assertContains(response, "ابدأ الإعداد")
        self.assertContains(response, "خطوات الإعداد")
        self.assertContains(response, "الأنشطة المدعومة")

    def test_setup_gate_keeps_text_as_real_translatable_ui(self):
        response = self.client.get(reverse("setup_gate"))

        required_translation_keys = [
            "heroKicker",
            "heroTitle",
            "heroLead",
            "startSetup",
            "stepsTitle",
            "activitiesTitle",
            "commercial",
            "service",
            "manufacturing",
            "contracting",
            "restaurant",
            "medical",
            "education",
            "other",
        ]
        for key in required_translation_keys:
            self.assertContains(response, f'data-i18n="{key}"')

        self.assertContains(response, "Set up Hesba for your activity")
        self.assertContains(response, "Supported activities")

    def test_setup_gate_css_uses_responsive_components_not_overlay_coordinates(self):
        css_path = settings.BASE_DIR / "static" / "hesba" / "css" / "setup_gate_web.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(".setup-shell", css)
        self.assertIn("display:grid", css)
        self.assertIn("display:flex", css)
        self.assertIn("max-width", css)
        self.assertNotIn(".setup-bg", css)
        self.assertNotIn("setup_gate_web_background_approved.png", css)
        self.assertNotIn("mask-image", css)
        self.assertNotIn("-webkit-mask", css)

        # Limited absolute positioning is allowed for small decorative badges/arrows only.
        self.assertIn(".step-badge", css)
        self.assertIn(".step-item:not(:last-child)::after", css)


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
