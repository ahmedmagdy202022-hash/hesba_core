"""POS-002: finding a customer at the till by phone, and adding one without leaving it."""

import json

from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_seeded_role, make_user, make_user_profile
from master_data.models import Customer
from permissions.models import RoleCode
from permissions.services import user_has_permission
from reports.tests_dashboard import prepared_client

from .pos_customers import customer_directory, quick_add_customer


ADD = reverse("sales:pos_add_customer")


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class PosCustomerTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "posc_owner")
        self.karim = make_customer(customer_code="KARIM", name="Karim", phone="0100 123-4567")

    def post(self, payload):
        return self.client.post(ADD, json.dumps(payload), content_type="application/json")

    def test_directory_carries_phone_digits_and_the_till_embeds_it(self):
        self.assertIn({"id": self.karim.pk, "name": "Karim", "phone": "01001234567"}, customer_directory())
        self.client.force_login(self.owner)
        page = self.client.get(reverse("sales:pos"))
        self.assertContains(page, 'id="hs-customers"')
        self.assertContains(page, "data-pos-customer-find")
        self.assertContains(page, "data-new-customer-save")

    def test_quick_add_creates_a_coded_audited_customer_and_never_duplicates_a_phone(self):
        self.client.force_login(self.owner)
        response = self.post({"name": "Mona Adel", "phone": "01112223334"})
        data = response.json()
        self.assertTrue(data["ok"] and data["created"])
        mona = Customer.objects.get(pk=data["id"])
        self.assertTrue(mona.customer_code.startswith("C-"))
        self.assertTrue(AuditLog.objects.filter(action="quick_add_customer", object_id=str(mona.pk)).exists())
        again = self.post({"name": "Mona again", "phone": "011 1222 3334"}).json()
        self.assertEqual((again["id"], again["created"]), (mona.pk, False))
        self.assertEqual(self.post({"name": "", "phone": "0100"}).status_code, 400)
        self.assertEqual(self.post({"name": "Short phone", "phone": "12"}).status_code, 400)
        no_phone, created = quick_add_customer("Walk-in named", "", self.owner)
        self.assertTrue(created)
        self.assertEqual(no_phone.phone, "")

    def test_only_people_who_manage_customers_can_add_them(self):
        cashier = person(RoleCode.CASHIER, "posc_cashier")
        self.client.force_login(cashier)
        response = self.post({"name": "X", "phone": "01000000000"})
        expected = 200 if user_has_permission(cashier, "master_data.manage_parties") else 403
        self.assertEqual(response.status_code, expected)
        if expected == 403:
            self.assertNotContains(self.client.get(reverse("sales:pos")), "data-new-customer-save")
        self.assertEqual(self.client.get(ADD).status_code, 405)
