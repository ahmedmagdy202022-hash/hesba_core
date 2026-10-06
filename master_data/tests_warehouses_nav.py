"""WH-002: warehouses have their own place in the menu, next to stock."""

from django.test import TestCase
from django.urls import reverse

from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from reports.tests_shell import sidebar_links, signed_in


class WarehousesInTheMenuTests(TestCase):
    def test_the_owner_finds_and_adds_a_warehouse_from_the_menu(self):
        prepared_client()
        signed_in(self, RoleCode.OWNER, "wh_owner")
        page = self.client.get(reverse("dashboard_snapshot"))
        links = sidebar_links(page)
        self.assertIn(reverse("master_data:locations"), links)
        self.assertEqual(links.index(reverse("master_data:locations")), links.index(reverse("inventory:stock")) + 1)
        warehouses = self.client.get(reverse("master_data:locations"))
        self.assertContains(warehouses, "المخازن")
        self.assertContains(warehouses, 'aria-current="page"', msg_prefix="the menu marks the warehouses entry as current")

    def test_without_the_inventory_module_there_is_no_warehouse_entry(self):
        prepared_client("medical", "clinic", "customers,items_services,sales_operations,cashboxes,reports")
        signed_in(self, RoleCode.OWNER, "wh_clinic")
        self.assertNotIn(reverse("master_data:locations"), sidebar_links(self.client.get(reverse("dashboard_snapshot"))))
