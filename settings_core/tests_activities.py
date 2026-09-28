"""ACT-002: medical, education and "other" activities in the setup wizard."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from settings_core import setup_catalog as catalog
from settings_core.models import ActivityType, ClientProfile
from settings_core.setup_services import complete_setup

NEW = (catalog.MEDICAL, catalog.EDUCATION, catalog.OTHER, catalog.CONTRACTING)


class ActivityWizardTests(TestCase):
    def setUp(self):
        user = make_user(username="act_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)

    def test_every_activity_card_leads_to_its_own_step(self):
        page = self.client.get(reverse("setup_activity")).content.decode()
        for activity in NEW + (catalog.RESTAURANTS,):
            with self.subTest(activity=activity):
                self.assertIn(f'data-activity="{activity}" data-next="/setup/activity/{activity}/"', page)
        for locked in ("manufacturing",):
            self.assertIn(f'data-activity="{locked}" disabled', page)

    def test_the_step_lists_the_catalog_and_hands_on_the_activity(self):
        for activity in NEW:
            with self.subTest(activity=activity):
                page = self.client.get(f"/setup/activity/{activity}/")
                self.assertContains(page, f'data-activity-step="{activity}"')
                for slug, labels in catalog.SUB_ACTIVITY_LABELS[activity].items():
                    self.assertContains(page, f'data-sub-activity="{slug}"')
                    self.assertContains(page, labels["ar"])
                self.assertContains(page, 'id="hs-sub-copy"')
                self.assertContains(page, "url.searchParams.set('activity',activitySlug)")

    def test_module_cards_carry_the_catalog_presets(self):
        page = self.client.get("/setup/modules/?lang=ar&activity=medical&sub_activity=clinic").content.decode()
        for activity in NEW:
            for slug in catalog.MODULE_SLUGS:
                with self.subTest(activity=activity, slug=slug):
                    self.assertRegex(page, rf'data-module="{slug}"[^>]*data-{activity}-state="{catalog.preset_state(activity, slug)}"')

    def test_review_shows_catalog_labels_in_both_languages(self):
        page = self.client.get("/setup/review/?lang=ar&activity=education&sub_activity=languages&modules=customers")
        self.assertContains(page, 'data-ar="نشاط تعليمي" data-en="Education"')
        self.assertContains(page, 'data-ar="مركز لغات" data-en="Language center"')
        self.assertContains(page, "activityLabel.dataset[lang]")


class ActivityPresetTests(TestCase):
    def test_saving_maps_each_activity_and_locks_its_required_modules(self):
        for activity, kind, must in ((catalog.MEDICAL, ActivityType.SERVICES, {"appointments_visits", "sales_operations"}),
                                     (catalog.EDUCATION, ActivityType.SERVICES, {"customers", "sales_operations"}),
                                     (catalog.OTHER, ActivityType.MIXED, {"items_services", "sales_operations"}),
                                     (catalog.CONTRACTING, ActivityType.CONTRACTING, {"customers", "projects"})):
            with self.subTest(activity=activity):
                ClientProfile.objects.all().delete()
                profile = ClientProfile.objects.create(client_code="DEMO", legal_name="Demo", display_name="Demo")
                complete_setup(profile, activity, next(iter(catalog.SUB_ACTIVITY_LABELS[activity])), "")
                profile.refresh_from_db()
                self.assertEqual((profile.activity_slug, profile.activity_type), (activity, kind))
                self.assertTrue(must <= set(catalog.required_modules(activity)))
