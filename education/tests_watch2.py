"""Watch round 2 fixes for education: ED-01 (letters are no phone number on a student or a parent)."""

from django.core.exceptions import ValidationError
from django.urls import reverse

from master_data.models import Customer
from sales.pos_customers import quick_add_customer

from . import services
from .models import Student
from .tests import EducationSetup

PHONE_AR = "اكتب رقم تليفون بأرقام بس (ينفع + في الأول ومسافات وشرطات وأقواس)."
PHONE_EN = "Type a phone number in digits only (a leading +, spaces, dashes and brackets are fine)."


class StudentPhoneTests(EducationSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def post_new(self, phone="", payer_phone="01001234501", lang="ar", **extra):
        return self.client.post(reverse("education:student_new"), {"name": "يوسف محمد", "phone": phone, "payer_name": "محمد السيد", "payer_phone": payer_phone,
                                                                  "lang": lang, **extra})

    def test_letters_in_the_students_phone_are_refused_in_both_languages(self):
        self.assertContains(self.post_new(phone="abc123"), PHONE_AR)
        self.assertContains(self.post_new(phone="abc123", lang="en"), PHONE_EN)
        self.assertFalse(Student.objects.exists())
        self.assertFalse(Customer.objects.filter(name="محمد السيد").exists())

    def test_letters_in_the_parents_phone_are_refused_and_no_parent_is_made(self):
        self.assertContains(self.post_new(payer_phone="xyz"), PHONE_AR)
        self.assertContains(self.post_new(payer_phone="0100123abc", lang="en"), PHONE_EN)
        self.assertFalse(Student.objects.exists())
        self.assertFalse(Customer.objects.filter(name="محمد السيد").exists())

    def test_a_student_who_pays_for_themselves_is_checked_once_with_the_same_message(self):
        answer = self.client.post(reverse("education:student_new"), {"name": "هاني سمير", "phone": "call me", "self_pays": "1", "lang": "en"})
        self.assertContains(answer, PHONE_EN)
        self.assertFalse(Student.objects.exists())

    def test_ordinary_ways_of_writing_a_number_still_save(self):
        for index, phone in enumerate(["01012345678", "+20 100 123-4567", "(02) 2345 6789", "٠١٠١٢٣٤٥٦٧٨", ""]):
            answer = self.client.post(reverse("education:student_new"), {"name": f"طالب {index}", "phone": phone, "payer_name": f"ولي {index}",
                                                                         "payer_phone": phone, "lang": "ar"})
            self.assertEqual(answer.status_code, 302, phone)
            self.assertEqual(Student.objects.get(name=f"طالب {index}").phone, phone)

    def test_an_old_student_with_an_odd_phone_still_saves_until_the_phone_is_touched(self):
        old = self.student("طالب قديم")
        Student.objects.filter(pk=old.pk).update(phone="abc123")
        old.refresh_from_db()
        url = reverse("education:student", args=[old.pk])
        self.assertEqual(self.client.post(url, {"action": "edit", "name": "طالب قديم معدّل", "phone": "abc123", "lang": "ar"}).status_code, 302)
        old.refresh_from_db()
        self.assertEqual((old.name, old.phone), ("طالب قديم معدّل", "abc123"))
        self.assertContains(self.client.post(url, {"action": "edit", "name": "طالب قديم معدّل", "phone": "abc1234", "lang": "ar"}), PHONE_AR)
        old.refresh_from_db()
        self.assertEqual(old.phone, "abc123")


class TillQuickAddTests(EducationSetup):
    def test_the_quick_add_refuses_letters_in_a_phone_but_takes_a_plain_one(self):
        with self.assertRaises(ValidationError) as caught:
            quick_add_customer("عميل", "01012abc345678", self.owner)
        self.assertEqual(caught.exception.messages, ["phone"])
        self.assertFalse(Customer.objects.filter(name="عميل").exists())
        customer, created = quick_add_customer("عميل", "+20 101 234-5678", self.owner)
        self.assertTrue(created)
        self.assertEqual(customer.phone, "+20 101 234-5678")
        with self.assertRaises(ValidationError):  # the old rule stays: too few digits
            quick_add_customer("عميل تاني", "123", self.owner)
