"""FB-TYPES: "+ new category" from inside the item and expense forms."""

from django.test import TestCase
from django.urls import reverse

from expenses.models import ExpenseCategory
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from master_data.models import Category
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class QuickCategoryTests(TestCase):
    def setUp(self):
        prepared_client("commercial", "retail", "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        self.client.force_login(person(RoleCode.OWNER, "qc_owner"))

    def test_item_form_offers_and_makes_a_category_once(self):
        self.assertContains(self.client.get(reverse("master_data:item_create")), "data-quick-add-url=\"/master-data/categories/quick/\"")
        first = self.client.post(reverse("master_data:category_quick_create"), {"name": "إكسسوارات"}).json()
        again = self.client.post(reverse("master_data:category_quick_create"), {"name": "إكسسوارات"}).json()
        self.assertEqual(first["id"], again["id"])
        category = Category.objects.get(pk=first["id"])
        self.assertTrue(category.category_code.startswith("CAT-"))
        self.assertEqual(self.client.post(reverse("master_data:category_quick_create"), {"name": ""}).status_code, 400)

    def test_expense_form_offers_and_makes_a_category_once(self):
        self.assertContains(self.client.get(reverse("expenses:create")), "data-quick-add-url=\"/expenses/categories/quick/\"")
        first = self.client.post(reverse("expenses:category_quick_create"), {"name": "صيانة المكيفات"}).json()
        again = self.client.post(reverse("expenses:category_quick_create"), {"name": "صيانة المكيفات"}).json()
        self.assertEqual(first["id"], again["id"])
        self.assertTrue(ExpenseCategory.objects.get(pk=first["id"]).active)

    def test_who_may_not_add_gets_refused(self):
        self.client.force_login(person(RoleCode.CASHIER, "qc_cashier"))
        self.assertEqual(self.client.post(reverse("master_data:category_quick_create"), {"name": "x"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("expenses:category_quick_create"), {"name": "x"}).status_code, 403)
        self.assertFalse(Category.objects.filter(name_ar="x").exists() or ExpenseCategory.objects.filter(name_ar="x").exists())
