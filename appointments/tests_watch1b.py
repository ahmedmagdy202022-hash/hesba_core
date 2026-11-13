"""Watch round 1b on the agenda: the clinic's words (CL-01), who may be chosen as the doctor (CL-06), the empty day (CL-07-like)."""

import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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


class AgendaDoctorsTests(TestCase):
    """CL-06: the agenda's filter and booking form offer the clinic's staff, not its sales reps."""

    def business(self, activity, sub, username):
        from staff.services import save_employee

        prepared_client(activity, sub, ",".join(default_modules(activity, sub)))
        self.owner = person(RoleCode.OWNER, username)
        self.doctor = save_employee({"name": "د. سارة", "title": "طبيبة"}, self.owner)
        self.rep = save_employee({"name": "سمير عادل", "title": "مندوب مبيعات"}, self.owner)
        self.client.force_login(self.owner)

    def both_lists(self, html):
        selects = re.findall(r'<select[^>]*name="employee".*?</select>', html, re.S)
        self.assertEqual(len(selects), 2)  # the filter above the table and the booking form
        return [re.findall(r"<option value=\"\d+\"[^>]*>(.*?)</option>", select, re.S) for select in selects]

    def test_a_clinic_offers_its_doctors_and_not_its_rep(self):
        self.business("medical", "clinic", "w1b_ag_clinic")
        for names in self.both_lists(self.client.get(reverse("appointments:agenda")).content.decode()):
            self.assertTrue(any("د. سارة" in name for name in names), names)
            self.assertFalse(any("سمير" in name for name in names), names)

    def test_a_shop_style_business_still_offers_everyone(self):
        self.business("services", "beauty", "w1b_ag_salon")
        for names in self.both_lists(self.client.get(reverse("appointments:agenda")).content.decode()):
            self.assertTrue(any("سمير" in name for name in names), names)  # a rep making a visit at a customer's is a normal use there

    def test_an_appointment_already_booked_with_such_a_person_keeps_them_on_its_page_and_filter(self):
        from datetime import datetime, time

        from hesba_testing.factories import make_customer, make_item

        from .models import Appointment

        self.business("medical", "clinic", "w1b_ag_old")
        today = timezone.localdate()
        old = Appointment.objects.create(number="AP-W1B-1", customer=make_customer(customer_code="W1B-P", name="منى"), employee=self.rep,
                                         service=make_item(item_code="W1B-SRV", item_name="كشف", is_stock_tracked=False), price="100",
                                         starts_at=timezone.make_aware(datetime.combine(today, time(23, 58))), created_by=self.owner)
        page = self.client.get(reverse("appointments:detail", args=[old.pk])).content.decode()
        self.assertIn(f'<option value="{self.rep.pk}" selected>', page)
        filtered = self.client.get(reverse("appointments:agenda"), {"employee": self.rep.pk}).content.decode()
        self.assertIn(f'<option value="{self.rep.pk}" selected>', filtered)


class EmptyAgendaWordsTests(TestCase):
    def test_a_salon_s_empty_day_offers_to_book_an_appointment(self):
        prepared_client("services", "beauty", ",".join(default_modules("services", "beauty")))
        self.client.force_login(person(RoleCode.OWNER, "w1b_ag_empty"))
        ar = self.client.get(reverse("appointments:agenda")).content.decode()
        self.assertIn('<b>مفيش مواعيد في اليوم ده.</b><a href="#new-appointment" data-agenda-empty-book>احجز ميعاد</a>', ar)
        en = self.client.get(reverse("appointments:agenda"), {"lang": "en"}).content.decode()
        self.assertIn('<b>No appointments on this day.</b><a href="#new-appointment" data-agenda-empty-book>Book an appointment</a>', en)
