"""FB2-CASHBOX: a new cashbox from the cashboxes page, with its code filled in by itself."""

from django.test import TestCase
from django.urls import reverse

from cashboxes.models import Cashbox
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class NewCashboxTests(TestCase):
    def setUp(self):
        prepared_client()

    def test_the_owner_gets_the_button_a_cashier_does_not(self):
        self.client.force_login(person(RoleCode.OWNER, "nc_owner"))
        page = self.client.get(reverse("cashboxes:list")).content.decode()
        self.assertIn("data-new-cashbox", page)
        self.assertIn(reverse("master_data:create", kwargs={"entity": "cashboxes"}), page)
        self.client.force_login(person(RoleCode.CASHIER, "nc_cashier"))
        response = self.client.get(reverse("cashboxes:list"))
        self.assertEqual(response.status_code, 200)  # the cashier sees the cashboxes…
        self.assertNotIn("data-new-cashbox", response.content.decode())  # …but does not add one

    def test_a_new_cashbox_gets_its_code_by_itself(self):
        self.client.force_login(person(RoleCode.OWNER, "nc_owner2"))
        url = reverse("master_data:create", kwargs={"entity": "cashboxes"})
        self.assertIn("CB-00001", self.client.get(url).content.decode())
        response = self.client.post(url, {"cashbox_code": "", "name_ar": "خزنة الفرع", "opening_balance": "0", "currency": "EGP", "active": "on"})
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors)
        self.assertEqual(Cashbox.objects.get(name_ar="خزنة الفرع").cashbox_code, "CB-00001")
