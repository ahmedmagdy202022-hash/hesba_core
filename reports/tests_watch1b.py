"""Watch round 1b on the reports screens: the dashboard speaks the clinic's words (CL-01) and shows its two daily jobs (CL-09),
and the reports hub leads with the activity's own report (SH-12, CL-05 follow-up)."""

import re
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_item, make_location, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from settings_core.activity_fit import default_modules

from .tests_analytics import person
from .tests_dashboard import prepared_client, sell


def a_business(test, activity, sub_activity, username, sale=True):
    """An installation of this activity with one posted sale today, and its owner signed in."""

    prepared_client(activity, sub_activity, ",".join(default_modules(activity, sub_activity)))
    today = timezone.localdate()
    Period.objects.create(period_code="W1B", name="w1b", start_date=today - timedelta(days=90), end_date=today + timedelta(days=30))
    owner = person(RoleCode.OWNER, username)
    if sale:
        location, cashbox, item = make_location(), make_cashbox(cashbox_code="W1B-CASH"), make_item(item_code="W1B-1", item_name="كشف استشارة")
        stock_in(item, location, 100, "6.00", movement_date=today - timedelta(days=60))
        recalculate_item_average_cost(item)
        sell(item, location, cashbox, 3, "100.00", "100.00", owner, number="W1B-S1", when=today)
    test.client.force_login(owner)
    return owner


class DashboardWordsTests(TestCase):
    """CL-01: the sentences on the dashboard that name the sale or the customer."""

    def dashboard(self, **params):
        response = self.client.get(reverse("dashboard_snapshot"), params)
        self.assertEqual(response.status_code, 200)
        return response, response.content.decode()

    def test_a_clinic_says_revenue_and_patients_in_arabic(self):
        a_business(self, "medical", "clinic", "w1b_clinic_ar")
        response, ar = self.dashboard()
        labels = {card["key"]: card["label"] for card in response.context["cards"]}
        self.assertEqual(labels["sales_today"], "إيراد اليوم")
        self.assertEqual(labels["customer_dues"], "مستحقات على المرضى")
        self.assertEqual(response.context["hero_today"], "إيراد النهارده")
        self.assertEqual(response.context["hero_headline_text"].split(" ")[:2], ["صافي", "إيراد"])
        self.assertEqual(response.context["hero_trend"], "اتجاه الإيراد")
        self.assertEqual(response.context["phero_none"], "لسه مفيش إيراد النهارده؛ أول فاتورة هتظهر هنا.")
        metrics = {metric["key"]: metric["label"] for metric in response.context["analytics"]["metrics"]}
        self.assertEqual(metrics["net_sales"], "صافي الإيراد")
        self.assertEqual(metrics["avg_invoice"], "متوسط فاتورة الكشف")
        self.assertEqual(metrics["receivables"], "فلوسك عند المرضى (آجل المرضى)")
        for text in ("إيراد اليوم", "الإيراد يوم بيوم", "آجل على المرضى", "أكتر الخدمات ربحًا", "متوسط فاتورة الكشف", "فلوسك عند المرضى (آجل المرضى)"):
            with self.subTest(text=text):
                self.assertIn(text, ar)
        for shop in ("مبيعات اليوم", "مبيعات النهارده", "المبيعات يوم بيوم", "آجل على العملاء", "أكتر الأصناف ربحًا", "متوسط الفاتورة", "فلوسك برّه"):
            with self.subTest(shop=shop):
                self.assertNotIn(shop, ar)
        self.assertRegex(ar, r"<bdi>1</bdi> فاتورة")  # «N فاتورة»: the figure counts invoices, not visits (CL-05), so it keeps its word

    def test_a_clinic_says_revenue_and_patients_in_english(self):
        a_business(self, "medical", "clinic", "w1b_clinic_en")
        response, en = self.dashboard(lang="en")
        self.assertEqual({card["key"]: card["label"] for card in response.context["cards"]}["customer_dues"], "Patient balances")
        self.assertEqual(response.context["hero_today"], "Revenue today")
        metrics = {metric["key"]: metric["label"] for metric in response.context["analytics"]["metrics"]}
        self.assertEqual(metrics["net_sales"], "Net revenue")
        self.assertEqual(metrics["avg_invoice"], "Average visit bill")
        self.assertEqual(metrics["receivables"], "Owed to you (patients)")
        for text in ("Revenue today", "Revenue by day", "On patient credit", "Most profitable services"):
            with self.subTest(text=text):
                self.assertIn(text, en)
        for shop in ("Sales today", "Sales by day", "On customer credit", "Most profitable items", "Average invoice"):
            with self.subTest(shop=shop):
                self.assertNotIn(shop, en)

    def test_a_commercial_installation_reads_exactly_what_it_always_did(self):
        a_business(self, "commercial", "retail", "w1b_shop")
        response, ar = self.dashboard()
        labels = {card["key"]: card["label"] for card in response.context["cards"]}
        self.assertEqual(labels["sales_today"], "مبيعات اليوم")
        metrics = {metric["key"]: metric["label"] for metric in response.context["analytics"]["metrics"]}
        self.assertEqual(metrics["net_sales"], "صافي المبيعات")
        self.assertEqual(metrics["avg_invoice"], "متوسط الفاتورة")
        self.assertEqual(metrics["receivables"], "فلوسك برّه (آجل العملاء)")
        how = {metric["key"]: metric["how"] for metric in response.context["analytics"]["metrics"]}
        self.assertEqual(how["avg_invoice"], "صافي المبيعات ÷ عدد فواتير البيع النهائية في الفترة.")
        self.assertEqual(how["receivables"], "مجموع أرصدة العملاء المدينة. «متأخر» = اللي عدّى مدة الآجل المسموحة، والأقدم بيتسدد الأول.")
        self.assertEqual(response.context["hero_today"], "مبيعات النهارده")
        self.assertEqual(labels["customer_dues"], "مديونيات العملاء")
        self.assertEqual(response.context["phero_none"], "لسه مفيش بيع النهارده؛ أول فاتورة هتظهر هنا.")
        for text in ("مبيعات اليوم", "المبيعات يوم بيوم", "آجل على العملاء", "أكتر الأصناف ربحًا", "اتجاه المبيعات"):
            with self.subTest(text=text):
                self.assertIn(text, ar)
        self.assertIn("صافي مبيعات", ar)
        response_en, en = self.dashboard(lang="en")
        self.assertEqual({card["key"]: card["label"] for card in response_en.context["cards"]}["customer_dues"], "Customer dues")
        self.assertEqual(response_en.context["hero_today"], "Sales today")
        for text in ("Sales today", "Sales by day", "On customer credit", "Most profitable items", "Average invoice", "Owed to you (customers)"):
            with self.subTest(text=text):
                self.assertIn(text, en)
        self.assertNotIn("Revenue", en)
        self.assertNotIn("إيراد", ar)

    def test_a_workshop_keeps_the_shop_words_it_has_no_word_for(self):
        a_business(self, "services", "maintenance", "w1b_workshop")
        response, ar = self.dashboard()
        self.assertEqual({card["key"]: card["label"] for card in response.context["cards"]}["sales_today"], "مبيعات اليوم")
        self.assertIn("متوسط الفاتورة", ar)
        self.assertNotIn("إيراد", ar)

    def test_the_cashier_of_a_clinic_reads_my_revenue(self):
        a_business(self, "medical", "clinic", "w1b_clinic_owner")
        self.client.force_login(person(RoleCode.CASHIER, "w1b_clinic_cashier"))
        response, _ = self.dashboard()
        labels = {card["key"]: card["label"] for card in response.context["cards"]}
        self.assertEqual(labels.get("sales_today"), "إيرادي اليوم")

    def test_a_vet_clinic_names_the_pet_owner_where_the_clinic_names_the_patient(self):
        a_business(self, "medical", "vet", "w1b_vet")
        response, ar = self.dashboard()
        metrics = {metric["key"]: metric["label"] for metric in response.context["analytics"]["metrics"]}
        self.assertEqual(metrics["receivables"], "فلوسك عند أصحاب الحيوانات (آجل)")
        self.assertIn("آجل على أصحاب الحيوانات", ar)
        self.assertNotIn("آجل على المرضى", ar)
        self.assertIn("إيراد اليوم", ar)

    def test_the_ask_hint_follows_the_activity(self):
        from settings_core.vocabulary import term, vocabulary

        self.assertEqual(term("dash_ask_hint", "ar", vocabulary("medical", "clinic")), "اسأل عن إيرادك ومخزونك وخزنتك ومرضاك")
        self.assertEqual(term("dash_ask_hint", "en", vocabulary("medical", "vet")), "Ask about your revenue, stock, cash or pet owners")
        self.assertEqual(term("dash_ask_hint", "ar", vocabulary("commercial", "retail")), "اسأل عن مبيعاتك ومخزونك وخزنتك وعملاءك")
        self.assertEqual(term("dash_ask_hint", "en", vocabulary("commercial", "retail")), "Ask about your sales, stock, cash or customers")
