from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import (
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    stock_in,
)
from inventory.models import StockMovementType
from permissions.models import RoleCode


class InventoryUiTests(TestCase):
    def login_as(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def test_anonymous_inventory_redirects_to_login(self):
        response = self.client.get(reverse("inventory:stock"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_cashier_cannot_view_inventory(self):
        self.login_as(RoleCode.CASHIER, "inventory_cashier")
        self.assertEqual(self.client.get(reverse("inventory:stock")).status_code, 403)

    def test_stock_keeper_sees_quantities_by_selected_location(self):
        self.login_as(RoleCode.STOCK_KEEPER, "inventory_keeper")
        item = make_item(min_stock=Decimal("3"))
        main = make_location()
        branch = make_location(location_code="BRANCH", name_ar="فرع")
        stock_in(item, main, 7, "4.00")
        stock_in(item, branch, 2, "4.00")
        response = self.client.get(reverse("inventory:stock"), {"location": branch.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rows"][0]["quantity"], Decimal("2"))
        self.assertEqual(response.context["rows"][0]["state"], "low")

    def test_zero_stock_item_remains_visible(self):
        self.login_as(RoleCode.STOCK_KEEPER, "inventory_zero")
        item = make_item(item_code="ZERO")
        response = self.client.get(reverse("inventory:stock"), {"q": "ZERO"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rows"][0]["state"], "out")
        self.assertContains(response, item.item_code)

    def test_cost_is_hidden_without_cost_permission(self):
        self.login_as(RoleCode.STOCK_KEEPER, "inventory_no_cost")
        item = make_item(average_cost=Decimal("9876.5432"))
        response = self.client.get(
            reverse("inventory:item_detail", args=[item.pk]), {"lang": "en"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_cost"])
        self.assertNotContains(response, "9876.5432")

    def test_owner_sees_cost_fields(self):
        self.login_as(RoleCode.OWNER, "inventory_cost")
        item = make_item(average_cost=Decimal("12.3456"))
        response = self.client.get(
            reverse("inventory:item_detail", args=[item.pk]), {"lang": "en"}
        )
        self.assertTrue(response.context["can_view_cost"])
        self.assertContains(response, "Average cost")
        self.assertContains(response, "12,3456")

    def test_movement_filter_preserves_direction_and_quantity(self):
        self.login_as(RoleCode.STOCK_KEEPER, "inventory_movement")
        item = make_item()
        location = make_location()
        stock_in(item, location, 5, "3.00")
        response = self.client.get(
            reverse("inventory:movements"),
            {"type": StockMovementType.PURCHASE_IN, "location": location.pk},
        )
        self.assertEqual(response.status_code, 200)
        movement = response.context["page"].object_list[0]
        self.assertEqual(movement.movement_type, StockMovementType.PURCHASE_IN)
        self.assertEqual(movement.quantity, Decimal("5"))

    def test_english_and_approved_stock_operation_action_renders(self):
        self.login_as(RoleCode.OWNER, "inventory_english")
        response = self.client.get(reverse("inventory:stock"), {"lang": "en"})
        self.assertContains(response, "Stock by item and location")
        self.assertContains(response, "Transfers and adjustments")
        self.assertContains(response, reverse("inventory:operations"))
