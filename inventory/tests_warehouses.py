"""R2-7: the warehouses hub reads each warehouse from its stock movements."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_item, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from .models import StockAdjustmentDirection
from .services import adjust_stock, transfer_stock
from . import warehouses


class WarehousesHubTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        prepared_client()
        cls.owner = make_user(username="wh_hub_owner")
        make_user_profile(user=cls.owner, role=make_seeded_role(RoleCode.OWNER))
        cls.keeper = make_user(username="wh_hub_keeper")
        make_user_profile(user=cls.keeper, role=make_seeded_role(RoleCode.STOCK_KEEPER))
        cls.main = make_location(location_code="WH-MAIN", name_ar="المخزن الرئيسي", is_default=True)
        cls.branch = make_location(location_code="WH-BR", name_ar="مخزن الفرع", name_en="Branch store")
        cls.fast = make_item(item_code="WH-FAST", item_name="صنف سريع")
        cls.slow = make_item(item_code="WH-SLOW", item_name="صنف راكد")
        today = timezone.localdate()
        old = today - timedelta(days=90)
        adjust_stock("WH-IN-1", old, cls.fast, cls.main, StockAdjustmentDirection.IN, D("10"), "opening", cls.owner, unit_cost=D("20"))
        adjust_stock("WH-IN-2", old, cls.slow, cls.main, StockAdjustmentDirection.IN, D("4"), "opening", cls.owner, unit_cost=D("50"))
        transfer_stock("WH-TR-1", today, cls.fast, cls.main, cls.branch, D("3"), cls.owner, reason="to the branch")

    def test_each_warehouse_holds_what_its_movements_say(self):
        cards = {card["location"].location_code: card for card in warehouses.summaries([self.main, self.branch], timezone.localdate(), with_value=True)}
        self.assertEqual((cards["WH-MAIN"]["items"], cards["WH-MAIN"]["units"]), (2, D("11")))
        self.assertEqual((cards["WH-BR"]["items"], cards["WH-BR"]["units"]), (1, D("3")))
        # 7 x 20 + 4 x 50 at average cost; the branch holds 3 x 20.
        self.assertEqual(cards["WH-MAIN"]["value"], D("340.00"))
        self.assertEqual(cards["WH-BR"]["value"], D("60.00"))
        self.assertEqual(cards["WH-BR"]["recent"], 1)

    def test_a_warehouse_page_shows_slow_movers(self):
        data = warehouses.detail(self.main, timezone.localdate(), with_value=True)
        rows = {row["item"].item_code: row for row in data["rows"]}
        self.assertTrue(rows["WH-SLOW"]["slow"])        # never left in 90 days
        self.assertFalse(rows["WH-FAST"]["slow"])       # moved out today
        self.assertEqual([row["item"].item_code for row in data["slow"]], ["WH-SLOW"])
        self.assertEqual(data["total_value"], D("340.00"))

    def test_the_owner_sees_values_and_the_menu_leads_here(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("inventory:warehouses") + "?lang=ar")
        self.assertContains(page, "مخزن الفرع")
        self.assertContains(page, "400.00")  # total value
        self.assertContains(page, 'aria-current="page"')
        self.assertContains(page, reverse("inventory:warehouse_detail", args=[self.branch.pk]))
        detail = self.client.get(reverse("inventory:warehouse_detail", args=[self.main.pk]) + "?lang=en")
        self.assertContains(detail, "data-slow")
        self.assertContains(detail, f"?from={self.main.pk}&lang=en")
        self.assertContains(detail, "340.00")

    def test_without_the_cost_permission_no_values_show(self):
        self.client.force_login(self.keeper)
        page = self.client.get(reverse("inventory:warehouses") + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "400.00")
        detail = self.client.get(reverse("inventory:warehouse_detail", args=[self.main.pk]) + "?lang=ar")
        self.assertNotContains(detail, "340.00")
        self.assertContains(detail, "صنف راكد")

    def test_transfer_from_here_names_the_source(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("inventory:transfer") + f"?from={self.branch.pk}&lang=ar")
        self.assertContains(page, f'<option value="{self.branch.pk}" selected>')
