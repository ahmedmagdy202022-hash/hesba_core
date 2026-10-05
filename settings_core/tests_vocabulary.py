"""ACT-PROFILE-001: each activity's own words reach the screens."""

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from reports.tests_analytics import person

from . import setup_catalog as catalog
from .models import ClientProfile
from .vocabulary import BASE, vocabulary


class VocabularyTableTests(SimpleTestCase):
    def test_every_activity_and_sub_activity_has_every_word_in_both_languages(self):
        for activity in catalog.ACTIVITY_LABELS:
            for sub in list(catalog.SUB_ACTIVITY_LABELS.get(activity, {})) + [""]:
                table = vocabulary(activity, sub)
                with self.subTest(activity=activity, sub=sub):
                    self.assertEqual(set(table), set(BASE))
                    for key, entry in table.items():
                        self.assertTrue(entry["ar"] and entry["en"], key)

    def test_activities_speak_their_own_language(self):
        self.assertEqual(vocabulary("medical", "clinic")["customers"]["ar"], "المرضى")
        self.assertEqual(vocabulary("services", "clinic")["customers"]["ar"], "المرضى")
        self.assertEqual(vocabulary("education", "tutoring_center")["customers"]["ar"], "الطلاب")
        self.assertEqual(vocabulary("education", "nursery")["customers"]["ar"], "الأطفال")
        self.assertEqual(vocabulary("restaurants", "cafe")["items"]["ar"], "المنيو")
        self.assertEqual(vocabulary("contracting", "general")["operations"]["ar"], "المستخلصات والفواتير")
        self.assertEqual(vocabulary("commercial", "pharmacy")["items"]["ar"], "الأدوية والأصناف")
        self.assertEqual(vocabulary("commercial", "retail")["customers"]["ar"], "العملاء")
        self.assertEqual(vocabulary("medical", "vet")["the_customer"]["en"], "Pet owner")


class ScreenWordsTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians")
        self.client.force_login(person(RoleCode.OWNER, "voc_owner"))

    def set_activity(self, activity, sub):
        ClientProfile.objects.filter(is_active=True).update(activity_slug=activity, sub_activity_slug=sub)

    def test_a_clinic_sees_patients_and_visits(self):
        self.set_activity("medical", "clinic")
        page = self.client.get(reverse("dashboard_snapshot")).content.decode()
        nav = page[page.index('class="hs-nav"'): page.index("</nav>", page.index('class="hs-nav"'))]
        self.assertIn(">المرضى<", nav)
        self.assertIn(">الكشوفات والفواتير<", nav)
        self.assertIn(">الأطباء والطاقم<", nav)
        self.assertNotIn(">العملاء<", nav)
        actions = page[page.index("dash-actions"):]
        self.assertIn("مريض جديد", actions)
        self.assertIn("تسجيل كشف", actions)
        listing = self.client.get(reverse("master_data:customers"))
        self.assertContains(listing, "المرضى")
        self.assertContains(self.client.get(reverse("sales:list")), "<th>المريض</th>", html=False)

    def test_english_follows_too_and_a_shop_is_unchanged(self):
        self.set_activity("education", "training")
        page = self.client.get(reverse("dashboard_snapshot"), {"lang": "en"}).content.decode()
        self.assertIn(">Students<", page)
        self.assertIn("New student", page)
        self.set_activity("commercial", "retail")
        page = self.client.get(reverse("dashboard_snapshot")).content.decode()
        self.assertIn(">العملاء<", page)
        self.assertIn("عميل جديد", page)
