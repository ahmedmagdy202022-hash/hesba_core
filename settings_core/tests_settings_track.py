"""SETTINGS-TRACK: every option is offered only where it fits, and options ride on the modules they need.

Ahmed, 20 Oct: "تتبع مسار كل اختيار في شاشات الإعدادات … مش كل الأنشطة ينفع نربط التشغيلات بتواريخ الصلاحية".
The track switched every option on and off in all 51 sub-activities; these are the rules it led to.
"""

from django.test import TestCase
from django.urls import reverse

from permissions.models import RoleCode
from reports.tests_crawl_matrix import person
from reports.tests_dashboard import prepared_client

from . import capabilities as caps
from .setup_services import enabled_modules, set_module_enabled
from .setup_views import _capability_rows


class FitTests(TestCase):
    def test_each_shop_is_offered_what_its_trade_uses(self):
        self.assertFalse(caps.fits("commercial", "batches_expiry", "fashion"))  # clothes do not expire
        self.assertFalse(caps.fits("commercial", "serials", "grocery"))
        self.assertFalse(caps.fits("commercial", "variants", "pharmacy"))
        self.assertTrue(caps.fits("commercial", "batches_expiry", "pharmacy"))
        self.assertTrue(caps.fits("commercial", "serials", "electronics"))
        for slug in ("pos", "shift_required", "barcode", "units", "serials"):
            self.assertFalse(caps.fits("services", slug, "professional"), slug)  # an office has nothing to scan or weigh
        self.assertTrue(caps.fits("services", "serials", "maintenance"))
        self.assertTrue(caps.fits("other", "serials", "mixed"))  # works like a maintenance business
        self.assertFalse(caps.fits("manufacturing", "batches_expiry", "printing"))
        self.assertTrue(caps.fits("manufacturing", "batches_expiry", "food"))

    def test_the_setup_review_only_lists_what_fits(self):
        offered = {row["slug"] for row in _capability_rows("commercial", "fashion", "ar")}
        self.assertNotIn("batches_expiry", offered)
        self.assertIn("variants", offered)

    def test_an_option_already_on_still_shows_so_it_can_be_switched_off(self):
        profile = prepared_client("commercial", "fashion", "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports")
        caps.set_capability_enabled(profile, "batches_expiry", True)
        self.assertIn("batches_expiry", {row["slug"] for row in caps.settings_rows(profile)})
        caps.set_capability_enabled(profile, "batches_expiry", False)
        self.assertNotIn("batches_expiry", {row["slug"] for row in caps.settings_rows(profile)})


class NeedsTests(TestCase):
    MODULES = "customers,suppliers,items_services,sales_operations,purchases,cashboxes,reports"  # no store

    def setUp(self):
        self.profile = prepared_client("commercial", "retail", self.MODULES)
        self.owner = person(RoleCode.OWNER, "track_owner")
        self.client.force_login(self.owner)

    def switch(self, url, field, slug, on):
        return self.client.post(reverse(url) + "?lang=ar", {field: slug, "enabled": "1" if on else "0"}, follow=True)

    def test_switching_on_expiry_dates_switches_the_store_on(self):
        self.assertNotIn("inventory", enabled_modules())
        page = self.switch("settings_core:capabilities", "capability", "batches_expiry", True)
        self.assertIn("inventory", enabled_modules())
        self.assertTrue(caps.capability_enabled("batches_expiry"))
        self.assertContains(page, "واشتغل معاها قسم: المخزون")

    def test_switching_the_store_off_pauses_expiry_dates_and_serials(self):
        set_module_enabled(self.profile, "inventory", True)
        for slug in ("batches_expiry", "serials"):
            caps.set_capability_enabled(self.profile, slug, True)
        page = self.switch("settings_core:modules", "module", "inventory", False)
        self.assertContains(page, "ووقفت معاه لحد ما يرجع")
        self.assertFalse(caps.capability_enabled("batches_expiry"))
        self.assertEqual(self.client.get("/batches/?lang=ar").status_code, 403)  # its screen closes with the store
        self.assertIn("batches_expiry", caps.switched_on())  # the owner's choice is kept
        row = next(row for row in caps.settings_rows(self.profile) if row["slug"] == "batches_expiry")
        self.assertEqual((row["state"], row["paused_by"]), ("paused", ["المخزون"]))
        self.switch("settings_core:modules", "module", "inventory", True)
        self.assertTrue(caps.capability_enabled("batches_expiry"))  # back with the store
        self.assertEqual(self.client.get("/batches/?lang=ar").status_code, 200)

    def test_instalments_need_customers(self):
        set_module_enabled(self.profile, "customers", False)
        caps.set_capability_enabled(self.profile, "installments", True)
        self.assertIn("customers", enabled_modules())

    def test_purchases_bring_suppliers_and_keep_them(self):
        set_module_enabled(self.profile, "purchases", False)
        set_module_enabled(self.profile, "suppliers", False)
        page = self.switch("settings_core:modules", "module", "purchases", True)
        self.assertIn("suppliers", enabled_modules())
        self.assertContains(page, "واشتغل معاه: الموردون")
        page = self.switch("settings_core:modules", "module", "suppliers", False)
        self.assertIn("suppliers", enabled_modules())
        self.assertContains(page, "مينفعش تقفل «الموردون» و«المشتريات» شغّال")


class SetupNeedsTests(TestCase):
    def test_setup_adds_what_the_chosen_modules_and_options_need(self):
        prepared_client("commercial", "retail", "items_services,sales_operations,purchases,cashboxes,reports")
        self.assertIn("suppliers", enabled_modules())

    def test_a_polyclinic_that_keeps_expiry_dates_gets_its_store(self):
        from . import activity_fit

        self.assertNotIn("inventory", activity_fit.default_modules("medical", "medical_center"))
        prepared_client("medical", "medical_center", ",".join(activity_fit.default_modules("medical", "medical_center")))
        self.assertIn("inventory", enabled_modules())
        self.assertTrue(caps.capability_enabled("batches_expiry"))
