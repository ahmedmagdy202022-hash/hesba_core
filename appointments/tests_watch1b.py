"""Watch round 1b on the agenda: the clinic's words (CL-01), who may be chosen as the doctor (CL-06), the empty day (CL-07-like)."""

from django.test import TestCase
from django.urls import reverse

from permissions.models import RoleCode
from reports.tests_analytics import person
from reports.tests_dashboard import prepared_client
from settings_core.activity_fit import default_modules


class AgendaWordsTests(TestCase):
    def agenda(self, activity, sub, username, lang="ar"):
        prepared_client(activity, sub, ",".join(default_modules(activity, sub)))
        self.client.force_login(person(RoleCode.OWNER, username))
        response = self.client.get(reverse("appointments:agenda"), {"lang": lang})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_clinic_books_a_patient_for_a_visit(self):
        ar = self.agenda("medical", "clinic", "w1b_a_clinic")
        self.assertIn("احجز كشف للمريض مع الدكتور، أو زيارة عند المريض. لما الكشف يخلص، اعمل فاتورته بدوسة.", ar)
        self.assertIn("<th>المريض</th><th>الكشف</th>", ar)
        self.assertIn('<label for="id_customer">المريض</label>', ar)
        self.assertIn('<label for="id_service">الكشف</label>', ar)
        self.assertIn(">في العيادة</option>", ar)
        self.assertIn(">زيارة عند المريض</option>", ar)
        for shop in ("في المحل", "زيارة عند العميل", "<th>العميل</th>", "<th>الخدمة</th>"):
            with self.subTest(shop=shop):
                self.assertNotIn(shop, ar)

    def test_the_clinic_agenda_in_english(self):
        en = self.agenda("medical", "clinic", "w1b_a_clinic_en", "en")
        self.assertIn("Book a visit for a patient with the doctor, or a visit at the patient&#x27;s home.", en.replace("&#39;", "&#x27;"))
        self.assertIn("<th>Patient</th><th>Visit</th>", en)
        self.assertIn(">At the clinic</option>", en)
        self.assertIn(">Visit to the patient</option>", en)
        self.assertNotIn("At the shop", en)

    def test_a_lab_and_a_centre_do_not_say_clinic(self):
        self.assertIn(">في المعمل</option>", self.agenda("medical", "lab", "w1b_a_lab"))

    def test_a_vet_names_the_pet_owner(self):
        ar = self.agenda("medical", "vet", "w1b_a_vet")
        self.assertIn("<th>صاحب الحيوان</th><th>الكشف</th>", ar)
        self.assertIn(">في العيادة البيطرية</option>", ar)
        self.assertIn(">زيارة عند صاحب الحيوان</option>", ar)

    def test_a_shop_style_business_reads_exactly_what_it_always_did(self):
        ar = self.agenda("services", "beauty", "w1b_a_salon_ar")
        self.assertIn("احجز ميعاد في المحل أو زيارة عند العميل، واربطه بالخدمة والموظف. لما الشغل يخلص، اعمل فاتورته بدوسة.", ar)
        self.assertIn("<th>العميل</th><th>الخدمة</th>", ar)
        self.assertIn(">في المحل</option>", ar)
        self.assertIn(">زيارة عند العميل</option>", ar)
        self.assertNotIn("المريض", ar)

    def test_a_shop_style_business_in_english(self):
        en = self.agenda("services", "beauty", "w1b_a_salon", "en")
        self.assertIn("<th>Customer</th><th>Service</th>", en)
        self.assertIn(">At the shop</option>", en)
        self.assertIn(">Visit to the customer</option>", en)
