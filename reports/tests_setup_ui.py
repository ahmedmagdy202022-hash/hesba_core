"""SETUP-UI: every setup step shares the navy brand rail, at its own step."""

from reports.test_utils import AuthenticatedTestCase

STEPS = (
    ("/setup/activity/?lang=ar", "1"),
    ("/setup/activity/commercial/?lang=ar", "2"),
    ("/setup/activity/restaurants/?lang=ar", "2"),
    ("/setup/activity/services/?lang=ar", "2"),
    ("/setup/activity/medical/?lang=ar", "2"),
    ("/setup/activity/manufacturing/?lang=ar", "2"),
    ("/setup/modules/?lang=ar&activity=commercial&sub_activity=retail", "3"),
    ("/setup/review/?lang=ar&activity=commercial&sub_activity=retail&modules=sales_operations,items_services", "4"),
)


class SetupRailTests(AuthenticatedTestCase):
    def test_every_step_has_the_rail_at_its_step_in_both_languages(self):
        for url, step in STEPS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'class="setup-rail" data-setup-step="{step}"', count=1)
                self.assertContains(response, "hesba/brand/hesba-logo-reversed.png")
                self.assertContains(response, f"خطوة {step} من 4")
                self.assertContains(response, f"Step {step} of 4")
                self.assertContains(response, 'class="is-current"', count=1)
                self.assertContains(response, 'class="is-done"', count=int(step) - 1)

    def test_activity_cards_say_what_each_activity_covers(self):
        response = self.client.get("/setup/activity/?lang=ar")
        self.assertContains(response, 'class="activity-card__hint"', count=8)
        self.assertContains(response, "محلات، سوبر ماركت، صيدليات وجملة")
        self.assertContains(response, "Garments, food, furniture, workshops")
