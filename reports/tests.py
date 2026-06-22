from django.test import SimpleTestCase
from django.urls import reverse


class FirstUiNavigationMapTests(SimpleTestCase):
    def test_home_page_renders_first_ui_navigation_map(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP")
        self.assertContains(response, "خريطة تشغيل أول شاشة UI")
        self.assertContains(response, "فتح لوحة Admin")

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
