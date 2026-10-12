"""FB-TYPES: an asset type of the business's own, beside the built-in ones."""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from fixed_assets.models import FixedAsset
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core import capabilities


class OwnAssetTypeTests(TestCase):
    def setUp(self):
        prepared_client()
        from settings_core.models import ClientProfile

        capabilities.set_capability_enabled(ClientProfile.get_active(), "fixed_assets", True)
        user = make_user(username="fa_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)

    def register(self, **extra):
        data = {"name": "ماكينة قهوة", "category": "equipment", "in_service_on": date(2026, 9, 1).isoformat(), "cost": "12000",
                "salvage_value": "0", "useful_months": "36"}
        data.update(extra)
        return self.client.post(reverse("fixed_assets:list"), data)

    def test_a_new_type_is_kept_shown_and_offered_again(self):
        page = self.client.get(reverse("fixed_assets:list"))
        self.assertContains(page, "data-quick-add")
        self.register(category="أدوات مطبخ", category_new="أدوات مطبخ")
        asset = FixedAsset.objects.get()
        self.assertEqual(asset.category, "أدوات مطبخ")
        page = self.client.get(reverse("fixed_assets:list"))
        self.assertContains(page, '<td>أدوات مطبخ</td>')  # shown as typed
        self.assertContains(page, '<option value="أدوات مطبخ"')  # and offered for the next asset
        self.register(name="فريزر", category="أدوات مطبخ")
        self.assertEqual(FixedAsset.objects.filter(category="أدوات مطبخ").count(), 2)

    def test_built_in_types_keep_their_words_and_junk_falls_back_to_other(self):
        self.register()
        self.register(name="غلط", category="not-a-type")
        self.assertEqual(sorted(FixedAsset.objects.values_list("category", flat=True)), ["equipment", "other"])
        self.assertContains(self.client.get(reverse("fixed_assets:list")), "معدات وماكينات")
