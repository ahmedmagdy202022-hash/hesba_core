"""Watch round 1b (CL-06, MT-05): a sales rep is not offered as the doctor of a visit or the technician of an order."""

import re

from django.test import TestCase

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .models import Employee
from .services import is_sales_rep, save_employee, serving_staff


def select_options(html, name):
    """The visible names in the options of the <select name="..."> (the empty choice, shown as a dash or a word, is left out)."""

    select = re.search(r'<select[^>]*name="%s".*?</select>' % name, html, re.S).group(0)
    return [text.strip() for value, text in re.findall(r'<option value="(\d+)"[^>]*>(.*?)</option>', select, re.S)]


class ServingStaffTests(TestCase):
    def setUp(self):
        user = make_user(username="w1b_staff_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.owner = user

    def staff(self, name, title=""):
        return save_employee({"name": name, "title": title}, self.owner)

    def test_only_a_title_that_says_sales_rep_is_left_out(self):
        doctor, nurse, blank = self.staff("د. سارة", "طبيبة"), self.staff("منى", "تمريض"), self.staff("عم سيد")
        rep, repa, english = self.staff("سمير", "مندوب مبيعات"), self.staff("هالة", "مندوبة"), self.staff("Sam", "Senior Sales Rep")
        names = list(serving_staff().values_list("name", flat=True))
        self.assertEqual(sorted(names), sorted([doctor.name, nurse.name, blank.name]))
        for person in (rep, repa, english):
            self.assertTrue(is_sales_rep(person), person.name)
        for person in (doctor, nurse, blank):
            self.assertFalse(is_sales_rep(person), person.name)

    def test_whoever_the_record_already_has_stays_offered_and_an_inactive_one_does_not_come_back(self):
        rep, other = self.staff("سمير", "مندوب مبيعات"), self.staff("هالة", "مندوبة")
        self.assertEqual([e.pk for e in serving_staff(rep.pk)], [rep.pk])
        self.assertEqual(list(serving_staff(0, None)), [])
        Employee.objects.filter(pk=rep.pk).update(active=False)
        self.assertEqual(list(serving_staff(rep.pk)), [])
        self.assertNotIn(other.pk, [e.pk for e in serving_staff(rep.pk)])
