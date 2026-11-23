"""ENT-002: a group with a factory and shops — each entity works in its own activity."""

from django.test import TestCase
from django.urls import reverse

from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from reports.tests_shell import sidebar_links

from . import services
from .models import EntityMembership
from .tests import person

ALL = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports,manufacturing,tables_orders"


class EntitySwitcherTests(TestCase):
    def setUp(self):
        prepared_client("commercial", "retail", ALL)
        self.owner = person(RoleCode.OWNER, "sw_owner")
        self.factory = services.save_entity({"code": "FAC", "name_ar": "المصنع", "activity_slug": "manufacturing", "sub_activity_slug": "garments"}, self.owner)
        self.shops = services.save_entity({"code": "SHOPS", "name_ar": "محلات القطاعي", "activity_slug": "commercial", "sub_activity_slug": "retail"}, self.owner)
        self.client.force_login(self.owner)

    def switch(self, entity):
        return self.client.post(reverse("entities:switch"), {"entity": entity.pk if entity else "", "next": "/dashboard/?lang=ar"})

    def test_the_bar_switches_menu_words_and_books(self):
        page = self.client.get("/dashboard/?lang=ar")
        self.assertContains(page, "data-entity-bar")
        self.assertContains(page, "المجموعة كلها")
        whole = sidebar_links(page)
        self.assertIn(reverse("manufacturing:home"), whole)
        self.assertIn(reverse("restaurant:board"), whole)

        response = self.switch(self.factory)
        self.assertRedirects(response, "/dashboard/?lang=ar", fetch_redirect_response=False)
        page = self.client.get("/dashboard/?lang=ar")
        factory = sidebar_links(page)
        self.assertIn(reverse("manufacturing:home"), factory)
        self.assertNotIn(reverse("restaurant:board"), factory)
        self.assertContains(page, "الخامات والمنتجات")          # the factory's words for items
        self.assertEqual(page.context["activity_slug"], "manufacturing")
        self.assertContains(page, f'data-entity-tab="{self.factory.pk}"')
        statements = self.client.get(reverse("ledger:trial_balance"))
        self.assertEqual(statements.context["entity"], self.factory)

        self.switch(self.shops)
        shops = sidebar_links(self.client.get("/dashboard/?lang=ar"))
        self.assertNotIn(reverse("manufacturing:home"), shops)
        self.assertIn(reverse("sales:pos"), shops)

        self.switch(None)
        self.assertIn(reverse("manufacturing:home"), sidebar_links(self.client.get("/dashboard/?lang=ar")))
        self.assertIsNone(self.client.get(reverse("ledger:trial_balance")).context["entity"])

    def test_a_member_of_one_entity_starts_and_stays_there(self):
        worker = person(RoleCode.MANAGER, "sw_factory_manager")
        EntityMembership.objects.create(user=worker, entity=self.factory, is_default=True)
        self.client.force_login(worker)
        page = self.client.get("/dashboard/?lang=ar")
        self.assertNotContains(page, "المجموعة كلها")
        self.assertEqual(page.context["activity_slug"], "manufacturing")
        # Asking for another entity or the whole group changes nothing.
        self.switch(self.shops)
        self.switch(None)
        self.assertEqual(self.client.get("/dashboard/?lang=ar").context["activity_slug"], "manufacturing")

    def test_single_entity_installs_show_no_bar_and_unsafe_next_is_ignored(self):
        self.factory.active = False
        self.factory.save()
        self.shops.active = False
        self.shops.save()
        self.assertNotContains(self.client.get("/dashboard/?lang=ar"), "data-entity-bar")
        response = self.client.post(reverse("entities:switch"), {"entity": "", "next": "https://evil.example/"})
        self.assertEqual(response["Location"], "/dashboard/")


class MedicalEntityInAGroupTests(TestCase):
    def test_a_clinic_entity_gets_patient_files_while_working_in_it(self):
        from hesba_testing.factories import make_customer

        prepared_client("commercial", "retail", ALL + ",appointments_visits")
        owner = person(RoleCode.OWNER, "med_group_owner")
        clinic = services.save_entity({"code": "CLN", "name_ar": "العيادة", "activity_slug": "medical", "sub_activity_slug": "clinic"}, owner)
        patient = make_customer(customer_code="P-1", name="مريض")
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("medical:patients")).status_code, 404)
        self.client.post(reverse("entities:switch"), {"entity": clinic.pk, "next": "/dashboard/"})
        self.assertIn(reverse("medical:patients"), sidebar_links(self.client.get("/dashboard/?lang=ar")))
        self.assertEqual(self.client.get(reverse("medical:file", args=[patient.pk])).status_code, 200)
