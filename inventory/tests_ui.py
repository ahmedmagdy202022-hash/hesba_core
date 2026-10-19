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
        # Four-decimal cost through the shared unit_cost filter, not the
        # Arabic-locale decimal comma a bare render used to produce.
        self.assertContains(response, "12.3456")

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


class LastCostTests(TestCase):
    """HG-052 (display only): a sold-out item shows its last purchase cost, not an average of 0.00."""

    def setUp(self):
        from hesba_testing.factories import make_stock_movement

        owner = make_user(username="last_cost_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        self.location = make_location()
        self.sold_out = make_item(item_code="GONE", average_cost=Decimal("0"))
        stock_in(self.sold_out, self.location, 5, "12.50")
        make_stock_movement(self.sold_out, self.location, StockMovementType.SALE_OUT, 5, "12.50")
        self.on_shelf = make_item(item_code="HERE", average_cost=Decimal("8.00"))
        stock_in(self.on_shelf, self.location, 3, "8.00")

    def test_the_stock_list_names_the_last_cost_of_what_ran_out(self):
        page = self.client.get(reverse("inventory:stock"), {"lang": "ar"})
        rows = {row["item"].item_code: row for row in page.context["rows"]}
        self.assertEqual(rows["GONE"]["last_cost"], Decimal("12.5000"))
        self.assertIsNone(rows["HERE"]["last_cost"])  # on hand: the average cost, as before
        self.assertContains(page, "آخر تكلفة")
        self.sold_out.refresh_from_db()
        self.assertEqual(self.sold_out.average_cost, Decimal("0"))  # the cached figure is not touched

    def test_the_item_card_says_it_is_the_last_cost(self):
        card = self.client.get(reverse("inventory:item_detail", args=[self.sold_out.pk]), {"lang": "ar"})
        self.assertContains(card, "آخر تكلفة (مفيش في المخزن)")
        self.assertNotContains(self.client.get(reverse("inventory:item_detail", args=[self.on_shelf.pk]), {"lang": "ar"}), "data-last-cost")

    def test_a_cancelled_purchase_is_not_the_last_cost(self):
        from hesba_testing.factories import add_purchase_line, make_draft_purchase_invoice, recalculate_purchase_totals
        from purchases.services import cancel_posted_purchase_invoice, post_purchase_invoice

        invoice = make_draft_purchase_invoice(location=self.location, invoice_number="PI-VOID")
        add_purchase_line(invoice, self.sold_out, 2, "40.00")
        recalculate_purchase_totals(invoice, paid_now="0.00")
        post_purchase_invoice(invoice.pk)
        cancel_posted_purchase_invoice(invoice.pk, reason="غلط")
        from .views import last_costs

        self.assertEqual(last_costs([self.sold_out.pk])[self.sold_out.pk], Decimal("12.5000"))

    def test_a_free_latest_purchase_shows_zero_not_an_older_price(self):
        from hesba_testing.factories import make_stock_movement

        free = make_item(item_code="FREE", average_cost=Decimal("0"))
        stock_in(free, self.location, 2, "9.00")
        stock_in(free, self.location, 2, "0.00")
        make_stock_movement(free, self.location, StockMovementType.SALE_OUT, 4, "4.50")
        page = self.client.get(reverse("inventory:stock"), {"lang": "ar"})
        rows = {row["item"].item_code: row for row in page.context["rows"]}
        self.assertEqual(rows["FREE"]["last_cost"], Decimal("0"))

    def test_only_the_entity_worked_in_counts(self):
        from entities.current import working_in
        from entities.models import Entity

        from .views import last_costs

        main = Entity.objects.filter(is_main=True).first() or Entity.objects.create(code="MAIN", name_ar="الرئيسي", is_main=True)
        branch = Entity.objects.create(code="BR-LC", name_ar="فرع")
        self.location.entity = main
        self.location.save(update_fields=["entity"])
        stock_in(self.sold_out, make_location(location_code="BR-STORE", entity=branch), 1, "99.00")
        with working_in(main):
            self.assertEqual(last_costs([self.sold_out.pk])[self.sold_out.pk], Decimal("12.5000"))
        with working_in(branch):
            self.assertEqual(last_costs([self.sold_out.pk])[self.sold_out.pk], Decimal("99.0000"))
