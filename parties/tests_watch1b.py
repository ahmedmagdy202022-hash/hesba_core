"""Watch round 1b (CL-01): the customer page says «patient» in a clinic and keeps «customer» everywhere else."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_customer
from medical import tests_insurance
from permissions.models import RoleCode
from reports.tests_analytics import person
from reports.tests_dashboard import prepared_client


class PatientPageWordsTests(tests_insurance.InsuranceSetup):
    """The clinic with an insurer (MED-008): the patient page and the insurer's page, in both languages."""

    def card(self, party, **params):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("parties:card", args=["customer", party.pk]), params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_patients_page_says_what_the_patient_owes(self):
        ar = self.card(self.mona)
        self.assertIn("مستحق على المريض", ar)
        self.assertNotIn("مستحق على العميل", ar)
        en = self.card(self.mona, lang="en")
        self.assertIn("Owed by the patient", en)
        self.assertIn("Billed to them this year", en)
        self.assertNotIn("Owed by the customer", en)
        self.assertNotIn("Sales to them this year", en)

    def test_an_insurance_company_is_not_called_a_patient(self):
        ar = self.card(self.insurer)
        self.assertIn("مستحق على العميل", ar)  # the insurer is a customer record: it keeps the general wording
        self.assertNotIn("مستحق على المريض", ar)
        self.assertIn("Owed by the customer", self.card(self.insurer, lang="en"))

    def test_the_credit_label_follows_the_same_rule(self):
        from parties.views import _words

        self.assertEqual(_words("ar", "customer", self.mona)["credit_customer"], "رصيد دائن للمريض")
        self.assertEqual(_words("en", "customer", self.mona)["credit_customer"], "Patient in credit")
        self.assertEqual(_words("ar", "customer", self.insurer)["credit_customer"], "رصيد دائن للعميل")


class OtherActivitiesKeepTheirWordsTests(TestCase):
    def page(self, activity, sub, username, lang="ar"):
        from settings_core.activity_fit import default_modules

        prepared_client(activity, sub, ",".join(default_modules(activity, sub)))
        self.client.force_login(person(RoleCode.OWNER, username))
        customer = make_customer(customer_code="W1B-C", name="عميل تجريبي")
        return self.client.get(reverse("parties:card", args=["customer", customer.pk]), {"lang": lang}).content.decode()

    def test_a_shop_reads_the_customer_words_it_always_did(self):
        ar = self.page("commercial", "retail", "w1b_p_shop")
        self.assertIn("مستحق على العميل", ar)
        self.assertNotIn("المريض", ar)

    def test_a_shop_in_english(self):
        en = self.page("commercial", "retail", "w1b_p_shop_en", "en")
        self.assertIn("Owed by the customer", en)
        self.assertIn("Sales to them this year", en)

    def test_a_workshop_reads_the_customer_words_it_always_did(self):
        ar = self.page("services", "maintenance", "w1b_p_work")
        self.assertIn("مستحق على العميل", ar)

    def test_a_vet_names_the_pet_owner(self):
        ar = self.page("medical", "vet", "w1b_p_vet")
        self.assertIn("مستحق على صاحب الحيوان", ar)
        self.assertNotIn("مستحق على المريض", ar)
