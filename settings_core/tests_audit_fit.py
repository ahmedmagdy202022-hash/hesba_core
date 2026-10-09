"""AUDIT-3: each activity is offered the capabilities that fit it, in its own words."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import capabilities as caps
from . import setup_catalog as catalog
from .setup_views import _capability_rows


class CapabilityFitTests(TestCase):
    def test_a_builder_is_never_offered_a_till_or_sizes(self):
        offered = {row["slug"] for row in _capability_rows("contracting", "general", "ar")}
        self.assertFalse(offered & {"pos", "barcode", "variants", "serials", "batches_expiry", "installments"})
        self.assertEqual(set(caps.default_selection("contracting", "general")), {"units", "fixed_assets"})
        self.assertNotIn("vat", caps.default_selection("contracting", "general"))   # changes bill totals: the owner's call
        units = next(row for row in _capability_rows("contracting", "general", "ar") if row["slug"] == "units")
        self.assertIn("المتر", units["about"])

    def test_each_activity_suggests_what_it_works_with(self):
        for activity, sub, must in (("education", "tutoring_center", {"installments"}), ("restaurants", "cafe", {"pos", "variants", "units"}),
                                    ("restaurants", "bakery", {"batches_expiry"}), ("medical", "dental", {"installments"}),
                                    ("services", "maintenance", {"serials"}), ("commercial", "pharmacy", {"batches_expiry", "pos"})):
            with self.subTest(activity=activity, sub=sub):
                self.assertTrue(must <= set(caps.default_selection(activity, sub)), caps.default_selection(activity, sub))

    def test_a_school_never_sees_a_till(self):
        offered = {row["slug"] for row in _capability_rows("education", "languages", "ar")}
        self.assertNotIn("pos", offered)
        self.assertIn("installments", offered)

    def test_settings_list_follows_the_activity_but_keeps_what_is_on(self):
        profile = prepared_client("contracting", "general", ",".join(catalog.default_modules("contracting")))
        rows = {row["slug"] for row in caps.settings_rows(profile, "ar")}
        self.assertNotIn("pos", rows)
        caps._write("pos", True)   # switched on before this change: still listed so it can be switched off
        self.assertIn("pos", {row["slug"] for row in caps.settings_rows(profile, "ar")})
        owner = make_user(username="fit_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        page = self.client.get(reverse("settings_core:overview") + "?lang=ar")
        self.assertNotContains(page, 'data-feature="variants"')
