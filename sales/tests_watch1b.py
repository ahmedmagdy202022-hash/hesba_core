"""Watch round 1b (CL-01): the invoice form speaks the clinic's words and keeps the shop's everywhere else."""

from django.test import TestCase
from django.urls import reverse

from permissions.models import RoleCode
from reports.tests_analytics import person
from reports.tests_dashboard import prepared_client
from settings_core.activity_fit import default_modules


class InvoiceFormWordsTests(TestCase):
    def form(self, activity, sub, username, lang="ar"):
        prepared_client(activity, sub, ",".join(default_modules(activity, sub)))
        self.client.force_login(person(RoleCode.OWNER, username))
        response = self.client.get(reverse("sales:create"), {"lang": lang})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_clinic_bills_the_patient_and_goes_back_to_visits_and_bills(self):
        ar = self.form("medical", "clinic", "w1b_f_clinic")
        self.assertIn("الباقي على المريض", ar)
        self.assertIn("حساب المريض", ar)
        self.assertEqual(ar.count("العودة للكشوفات والفواتير"), 2)  # the link above the title and the one under the buttons
        for shop in ("الباقي على العميل", "حساب العميل", "العودة للمبيعات"):
            with self.subTest(shop=shop):
                self.assertNotIn(shop, ar)

    def test_the_clinic_form_in_english(self):
        en = self.form("medical", "clinic", "w1b_f_clinic_en", "en")
        self.assertIn("Left on the patient", en)
        self.assertIn("Patient account", en)
        self.assertIn("Back to visits &amp; bills", en)
        for shop in ("Left on the customer", "Customer account", "Back to sales"):
            with self.subTest(shop=shop):
                self.assertNotIn(shop, en)

    def test_a_shop_reads_exactly_what_it_always_did(self):
        ar = self.form("commercial", "retail", "w1b_f_shop")
        self.assertIn("الباقي على العميل", ar)
        self.assertIn("حساب العميل", ar)
        self.assertEqual(ar.count("العودة للمبيعات"), 2)
        self.assertNotIn("المريض", ar)

    def test_a_shop_in_english(self):
        en = self.form("commercial", "retail", "w1b_f_shop_en", "en")
        for text in ("Left on the customer", "Customer account", "Back to sales"):
            with self.subTest(text=text):
                self.assertIn(text, en)

    def test_a_workshop_keeps_the_shop_words_it_has_no_word_for(self):
        ar = self.form("services", "maintenance", "w1b_f_work")
        self.assertIn("الباقي على العميل", ar)
        self.assertEqual(ar.count("العودة للمبيعات"), 2)

    def test_a_vet_bills_the_pet_owner(self):
        ar = self.form("medical", "vet", "w1b_f_vet")
        self.assertIn("الباقي على صاحب الحيوان", ar)
        self.assertIn("حساب صاحب الحيوان", ar)
        self.assertNotIn("الباقي على المريض", ar)

    def test_the_purchase_form_is_untouched(self):
        prepared_client("medical", "clinic", ",".join(default_modules("medical", "clinic")) + ",purchases")
        self.client.force_login(person(RoleCode.OWNER, "w1b_f_buy"))
        ar = self.client.get(reverse("purchases:create")).content.decode()
        self.assertIn("الباقي للمورد", ar)
        self.assertIn("حساب المورد", ar)
        self.assertNotIn("الباقي على المريض", ar)
