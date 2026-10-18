"""AN8 (Ahmed: "ليه فى تشغيلات فى محل بقالة او سوبر ماركت؟ ممكن تواريخ صلاحية لكن ليه تشغيلات؟")."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from purchases.forms import PurchaseLineInputForm
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import label, set_capability_enabled

from .words import expiry_only


class ExpiryDatesOnlyTests(TestCase):
    def open(self, sub):
        profile = prepared_client(sub_activity=sub)
        set_capability_enabled(profile, "batches_expiry", True)
        owner = make_user(username=f"exp_{sub}")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        return self.client.get(reverse("batches:index"), {"lang": "ar"}).content.decode()

    def test_a_supermarket_keeps_expiry_dates_not_batches(self):
        page = self.open("grocery")
        self.assertTrue(expiry_only())
        self.assertIn("تواريخ الصلاحية", page)
        self.assertNotIn("التشغيلات", page)
        self.assertNotIn('name="batch_no"', page)  # nothing to type but the date
        self.assertNotIn("رقم التشغيلة", page)
        self.assertEqual(label("batches_expiry", "ar"), "تواريخ الصلاحية")
        self.assertNotIn("batch_no", PurchaseLineInputForm(lang="ar").fields)
        self.assertIn("expiry_date", PurchaseLineInputForm(lang="ar").fields)

    def test_a_pharmacy_still_reads_the_lot_number(self):
        page = self.open("pharmacy")
        self.assertFalse(expiry_only())
        self.assertIn("رقم التشغيلة", page)
        self.assertIn('name="batch_no"', page)
        self.assertIn("batch_no", PurchaseLineInputForm(lang="ar").fields)
