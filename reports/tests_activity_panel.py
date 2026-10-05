"""ACT-PROFILE-002: each activity's dashboard leads with its own work."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_customer, make_item, make_location, stock_in
from permissions.models import RoleCode

from .tests_analytics import person
from .tests_dashboard import prepared_client

BASE_MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports"


class PanelSetup(TestCase):
    activity, sub, extra = "commercial", "retail", ""

    def setUp(self):
        prepared_client(self.activity, self.sub, BASE_MODULES + ("," + self.extra if self.extra else ""))
        self.owner = person(RoleCode.OWNER, f"ap_{self.activity}")
        self.client.force_login(self.owner)

    def page(self, **params):
        return self.client.get(reverse("dashboard_snapshot"), params)

    def nav_keys(self, response):
        return [item["key"] for item in response.context["nav_items"]]


class RestaurantPanelTests(PanelSetup):
    activity, sub, extra = "restaurants", "restaurant", "tables_orders"

    def test_open_tables_and_orders_lead(self):
        from restaurant import services as tables

        t1 = tables.save_table({"name": "1", "seats": 4}, self.owner)
        tables.save_table({"name": "2", "seats": 2}, self.owner)
        dish = make_item(item_code="DISH-1", item_name="كشري", default_sale_price="45.00", is_stock_tracked=False)
        order = tables.open_order(self.owner, table=t1)
        tables.add_item(order, dish, self.owner, quantity=2)
        response = self.page()
        panel = response.context["activity_panel"]
        tiles = {t["key"]: t["value"] for t in panel["tiles"]}
        self.assertEqual(panel["kind"], "kitchen")
        self.assertEqual((tiles["open_orders"], tiles["open_value"], tiles["tables_busy"]), (1, D("90.00"), "1 من 2"))
        self.assertContains(response, 'data-activity-panel="kitchen"')
        self.assertEqual(self.nav_keys(response)[:2], ["dashboard", "restaurant"])
        actions = response.context["quick_actions"]
        self.assertEqual((actions[0]["key"], actions[0]["primary"]), ("open_tables", True))
        self.assertEqual([a["key"] for a in actions if a["primary"]], ["open_tables"])
        self.assertEqual(response.context["nav_items"][1]["label"], "الطاولات")


class ClinicPanelTests(PanelSetup):
    activity, sub, extra = "medical", "clinic", "appointments_visits"

    def test_todays_bookings_and_no_shows(self):
        from appointments.models import Appointment, AppointmentStatus

        patient = make_customer(customer_code="P-1", name="منى")
        now = timezone.localtime()
        later = now + timedelta(hours=2) if now.hour < 21 else now + timedelta(minutes=30)
        for status, when in ((AppointmentStatus.DONE, now - timedelta(hours=1)), (AppointmentStatus.NO_SHOW, now - timedelta(hours=1)),
                             (AppointmentStatus.BOOKED, later)):
            Appointment.objects.create(number=f"AP-{status}", customer=patient, starts_at=when, status=status, created_by=self.owner)
        response = self.page()
        tiles = {t["key"]: t for t in response.context["activity_panel"]["tiles"]}
        self.assertEqual(tiles["done_today"]["value"], 1)
        self.assertEqual(tiles["no_show_today"]["tone"], "bad")
        self.assertEqual(tiles["no_show_rate"]["value"], "50%")
        if later.date() == now.date():
            self.assertIn("منى", tiles["next"]["value"])
        self.assertEqual(self.nav_keys(response)[:2], ["dashboard", "appointments"])
        self.assertEqual(response.context["nav_items"][1]["label"], "الحجوزات")
        self.assertEqual(response.context["quick_actions"][0]["key"], "bookings")


class ContractingPanelTests(PanelSetup):
    activity, sub, extra = "contracting", "general", "projects"

    def test_projects_and_losers(self):
        from projects.services import save_project

        save_project({"name": "فيلا التجمع", "customer": make_customer(customer_code="O-1"), "contract_value": "100000"}, self.owner)
        response = self.page()
        tiles = {t["key"]: t["value"] for t in response.context["activity_panel"]["tiles"]}
        self.assertEqual((tiles["active_projects"], tiles["contract_total"], tiles["billed_pct"]), (1, D("100000.00"), "0%"))
        self.assertEqual(self.nav_keys(response)[:2], ["dashboard", "projects"])


class ManufacturingPanelTests(PanelSetup):
    activity, sub, extra = "manufacturing", "food", "manufacturing"

    def test_recipes_short_of_materials_are_named(self):
        from manufacturing.services import save_recipe

        make_location(location_code="MAIN", is_default=True)
        flour = make_item(item_code="RAW-1", item_name="دقيق")
        bread = make_item(item_code="FG-1", item_name="عيش فينو")
        save_recipe({"product": bread, "output_quantity": "10"}, [(flour, "5")], self.owner)
        response = self.page()
        tiles = {t["key"]: t for t in response.context["activity_panel"]["tiles"]}
        self.assertEqual(tiles["short_recipes"]["value"], 1)
        self.assertIn("عيش فينو", tiles["short_recipes"]["sub"])
        self.assertEqual(self.nav_keys(response)[:2], ["dashboard", "manufacturing"])


class ShopHasNoPanelTests(PanelSetup):
    def test_a_shop_keeps_the_sale_as_its_lead(self):
        response = self.page()
        self.assertIsNone(response.context["activity_panel"])
        self.assertEqual([a["key"] for a in response.context["quick_actions"] if a["primary"]], ["record_sale"])

    def test_a_panel_is_hidden_without_its_section(self):
        cashier = person(RoleCode.CASHIER, "ap_cashier")
        self.client.force_login(cashier)
        self.assertIsNone(self.page().context["activity_panel"])


class DemoSwitchTests(PanelSetup):
    def test_only_a_demo_install_can_switch_and_the_words_follow(self):
        from django.test import override_settings

        self.assertEqual(self.client.post(reverse("demo_activity"), {"activity": "medical:clinic"}).status_code, 404)
        self.assertNotContains(self.page(), "data-demo-activity")
        with override_settings(DEMO_MODE=True):
            self.assertContains(self.page(), "data-demo-activity")
            self.client.post(reverse("demo_activity"), {"activity": "medical:clinic"})
            self.assertContains(self.page(), ">المرضى<")
            self.client.post(reverse("demo_activity"), {"activity": "evil:thing"})
            self.assertContains(self.page(), ">المرضى<")  # unknown choices are ignored
            self.client.force_login(person(RoleCode.CASHIER, "ap_demo_cashier"))
            self.assertEqual(self.client.post(reverse("demo_activity"), {"activity": "commercial:retail"}).status_code, 403)
