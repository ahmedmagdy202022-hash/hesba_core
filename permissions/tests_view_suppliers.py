"""HG-008: suppliers have their own view permission; the cashier does not hold it."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from master_data.models import Supplier
from permissions.models import RoleCode
from permissions.services import user_has_permission
from reports.tests_dashboard import prepared_client


def signed_in(test, role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    test.client.force_login(user)
    return user


class ViewSuppliersMatrixTests(TestCase):
    def test_only_the_cashier_loses_supplier_visibility(self):
        expected = {
            RoleCode.OWNER: True,
            RoleCode.MANAGER: True,
            RoleCode.STOCK_KEEPER: True,
            RoleCode.ACCOUNTANT: True,
            RoleCode.SUPPORT: True,
            RoleCode.CASHIER: False,
        }
        for role_code, allowed in expected.items():
            with self.subTest(role=role_code):
                user = make_user(username=f"vs_{role_code}")
                make_user_profile(user=user, role=make_seeded_role(role_code))
                self.assertEqual(user_has_permission(user, "master_data.view_suppliers"), allowed)
                # The shared master-data permission is untouched.
                self.assertTrue(user_has_permission(user, "master_data.view_master_data"))


class CashierSupplierScreensTests(TestCase):
    def setUp(self):
        prepared_client()
        Supplier.objects.create(supplier_code="SUP-HG8", name="Delta Mills")

    def test_cashier_is_refused_the_supplier_list(self):
        signed_in(self, RoleCode.CASHIER, "hg8_cashier")
        self.assertEqual(self.client.get(reverse("master_data:suppliers")).status_code, 403)
        self.assertEqual(self.client.get(reverse("master_data:list", kwargs={"entity": "suppliers"})).status_code, 403)

    def test_cashier_keeps_customers_and_items(self):
        signed_in(self, RoleCode.CASHIER, "hg8_cashier2")
        for name in ("master_data:customers", "master_data:items"):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_cashier_navigation_has_no_suppliers_link(self):
        signed_in(self, RoleCode.CASHIER, "hg8_cashier3")
        body = self.client.get(reverse("master_data:customers")).content.decode()
        self.assertNotIn(f'href="{reverse("master_data:suppliers")}?', body)
        hub = self.client.get(reverse("master_data:hub")).content.decode()
        self.assertNotIn("Delta Mills", hub)
        self.assertNotIn(reverse("master_data:suppliers"), hub)

    def test_stock_keeper_and_accountant_still_see_suppliers(self):
        for role_code in (RoleCode.STOCK_KEEPER, RoleCode.ACCOUNTANT):
            with self.subTest(role=role_code):
                signed_in(self, role_code, f"hg8_{role_code}")
                response = self.client.get(reverse("master_data:suppliers"))
                self.assertContains(response, "Delta Mills")
                self.assertContains(self.client.get(reverse("master_data:hub")), reverse("master_data:suppliers"))
                self.client.logout()
