"""FB2-LOC: the "new warehouse" screen (and every master-data form) reads cleanly."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from master_data.models import Location
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


class LocationScreenTests(TestCase):
    def setUp(self):
        prepared_client()
        owner = make_user(username="loc_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        self.url = reverse("master_data:create", kwargs={"entity": "locations"})

    def test_no_empty_entity_label_and_every_tick_box_says_what_it_does(self):
        page = self.client.get(self.url).content.decode()
        # One entity only: the entity is a hidden field, with no label left standing over nothing.
        self.assertIn('type="hidden" name="entity"', page)
        self.assertNotIn('for="id_entity"', page)
        self.assertIn("إضافة جديد — مخزن<", page)
        for help_text in ("ده اللي بيتختار لوحده في الفواتير والحركات الجديدة.", "ينفع تستلم فيه بضاعة المشتريات والتحويلات.",
                          "ينفع تبيع منه وتخصم من رصيده في فواتير البيع."):
            self.assertIn(help_text, page)
        self.assertIn('class="hs-checks"', page)  # AN3: the tick boxes sit together under "الإعدادات"
        self.assertIn('rows="3"', page)
        self.assertNotIn('rows="10"', page)

    def test_the_code_fills_itself_when_left_empty(self):
        self.assertIn("WH-00001", self.client.get(self.url).content.decode())
        response = self.client.post(self.url, {"location_code": "", "name_ar": "مخزن المعادي", "is_receiving_location": "on",
                                               "is_selling_location": "on", "active": "on"})
        self.assertEqual(response.status_code, 302)
        place = Location.objects.get(name_ar="مخزن المعادي")
        self.assertEqual(place.location_code, "WH-00001")
        self.assertTrue(place.is_receiving_location and place.is_selling_location and place.active)
        self.assertIsNotNone(place.entity_id)
        # A typed code is kept as typed.
        self.client.post(self.url, {"location_code": "MAADI-2", "name_ar": "مخزن ٢", "active": "on"})
        self.assertTrue(Location.objects.filter(location_code="MAADI-2").exists())

    def test_an_address_box_is_three_lines_not_ten(self):
        for entity in ("customers", "suppliers", "cashboxes"):
            page = self.client.get(reverse("master_data:create", kwargs={"entity": entity})).content.decode()
            self.assertNotIn('rows="10"', page, entity)
            self.assertIn('rows="3"', page, entity)
