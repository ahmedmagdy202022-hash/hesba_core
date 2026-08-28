from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import (
    make_cashbox,
    make_seeded_role,
    make_user,
    make_user_profile,
)
from permissions.models import RoleCode

from .models import Category, Customer, Item, Location, Supplier


class MasterDataUiTests(TestCase):
    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def test_anonymous_master_data_redirects_to_login(self):
        response = self.client.get(reverse("master_data:hub"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_manager_can_create_location(self):
        self.login_as(RoleCode.MANAGER, "manager_location")
        response = self.client.post(
            reverse("master_data:create", kwargs={"entity": "locations"}),
            {
                "location_code": "ALEX",
                "name_ar": "مخزن اسكندرية",
                "name_en": "Alexandria",
                "description": "",
                "is_default": "on",
                "is_receiving_location": "on",
                "is_selling_location": "on",
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Location.objects.filter(location_code="ALEX").exists())

    def test_manager_can_create_supplier_and_customer(self):
        self.login_as(RoleCode.MANAGER, "manager_parties")
        supplier = self.client.post(
            reverse("master_data:create", kwargs={"entity": "suppliers"}),
            {
                "supplier_code": "SUP-100",
                "name": "Supplier 100",
                "phone": "01000000000",
                "whatsapp": "",
                "email": "",
                "address": "",
                "opening_balance": "125.00",
                "notes": "",
                "active": "on",
            },
        )
        customer = self.client.post(
            reverse("master_data:create", kwargs={"entity": "customers"}),
            {
                "customer_code": "CUS-100",
                "name": "Customer 100",
                "phone": "01100000000",
                "whatsapp": "",
                "email": "",
                "address": "",
                "opening_balance": "50.00",
                "credit_limit": "5000.00",
                "notes": "",
                "active": "on",
            },
        )
        self.assertEqual(supplier.status_code, 302)
        self.assertEqual(customer.status_code, 302)
        self.assertEqual(Supplier.objects.get(supplier_code="SUP-100").opening_balance, Decimal("125.00"))
        self.assertEqual(Customer.objects.get(customer_code="CUS-100").credit_limit, Decimal("5000.00"))

    def test_cashier_can_view_but_cannot_manage_parties(self):
        self.login_as(RoleCode.CASHIER, "cashier_parties")
        list_response = self.client.get(
            reverse("master_data:list", kwargs={"entity": "customers"})
        )
        create_response = self.client.get(
            reverse("master_data:create", kwargs={"entity": "customers"})
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 403)

    def test_item_cost_field_is_hidden_without_cost_permission(self):
        self.login_as(RoleCode.MANAGER, "manager_item")
        response = self.client.get(
            reverse("master_data:create", kwargs={"entity": "items"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("default_purchase_price", response.context["form"].fields)

    def test_owner_can_see_item_purchase_price(self):
        self.login_as(RoleCode.OWNER, "owner_item")
        response = self.client.get(
            reverse("master_data:create", kwargs={"entity": "items"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("default_purchase_price", response.context["form"].fields)

    def test_opening_balance_cannot_be_changed_from_customer_edit(self):
        self.login_as(RoleCode.MANAGER, "manager_opening")
        customer = Customer.objects.create(
            customer_code="CUS-LOCK",
            name="Locked",
            opening_balance=Decimal("200.00"),
        )
        response = self.client.post(
            reverse(
                "master_data:edit",
                kwargs={"entity": "customers", "pk": customer.pk},
            ),
            {
                "customer_code": "CUS-LOCK",
                "name": "Locked updated",
                "phone": "",
                "whatsapp": "",
                "email": "",
                "address": "",
                "opening_balance": "9999.00",
                "credit_limit": "0.00",
                "notes": "",
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Locked updated")
        self.assertEqual(customer.opening_balance, Decimal("200.00"))

    def test_category_rejects_cycle_on_edit(self):
        self.login_as(RoleCode.MANAGER, "manager_category")
        parent = Category.objects.create(category_code="P", name_ar="رئيسي")
        child = Category.objects.create(category_code="C", name_ar="فرعي", parent=parent)
        response = self.client.post(
            reverse(
                "master_data:edit",
                kwargs={"entity": "categories", "pk": parent.pk},
            ),
            {
                "category_code": "P",
                "name_ar": "رئيسي",
                "name_en": "",
                "parent": str(child.pk),
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        parent.refresh_from_db()
        self.assertIsNone(parent.parent)

    def test_cashbox_area_is_view_only_until_management_permission_exists(self):
        self.login_as(RoleCode.OWNER, "owner_cashbox")
        make_cashbox(cashbox_code="SAFE-1")
        response = self.client.get(
            reverse("master_data:list", kwargs={"entity": "cashboxes"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage"])
        self.assertContains(response, "SAFE-1")

    def test_search_and_status_filter_work(self):
        self.login_as(RoleCode.MANAGER, "manager_search")
        Supplier.objects.create(supplier_code="ACTIVE-X", name="Alpha", active=True)
        Supplier.objects.create(supplier_code="INACTIVE-X", name="Beta", active=False)
        response = self.client.get(
            reverse("master_data:list", kwargs={"entity": "suppliers"}),
            {"q": "Beta", "status": "all"},
        )
        self.assertEqual(
            [row["object"].supplier_code for row in response.context["rows"]],
            ["INACTIVE-X"],
        )

    def test_stable_alias_routes_resolve_for_dashboard_links(self):
        self.login_as(RoleCode.MANAGER, "manager_aliases")
        for name in (
            "master_data:customers",
            "master_data:suppliers",
            "master_data:items",
            "master_data:locations",
            "master_data:categories",
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_english_labels_render(self):
        self.login_as(RoleCode.MANAGER, "manager_english")
        response = self.client.get(
            reverse("master_data:hub"),
            {"lang": "en"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Master data")
        self.assertContains(response, "Suppliers")
