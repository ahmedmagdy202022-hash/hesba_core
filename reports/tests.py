from django.test import SimpleTestCase
from django.urls import reverse


class FirstUiNavigationMapTests(SimpleTestCase):
    def test_home_page_renders_first_ui_navigation_map(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP")
        self.assertContains(response, "خريطة تشغيل أول شاشة UI")
        self.assertContains(response, "فتح لوحة Admin")
        self.assertContains(response, "مشاهدة Dashboard Snapshot")

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
        self.assertContains(response, "KPIs آمنة لاحقًا")
        self.assertContains(response, "حالة المخزون")
        self.assertContains(response, "حالة الخزن")

    def test_dashboard_snapshot_keeps_sensitive_finance_protected(self):
        response = self.client.get(reverse("dashboard_snapshot"))

        self.assertContains(response, "الربح")
        self.assertContains(response, "التكلفة")
        self.assertContains(response, "صلاحيات حقيقية")
