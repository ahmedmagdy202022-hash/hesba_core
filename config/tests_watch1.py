"""Watch round 1 fixes: SH-05, the first-run step «اقفل اليوم وشوف الربح» opens today's summary."""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from config import demo_first_run
from reports.tests_dashboard import prepared_client


@override_settings(DEMO_MODE=True)
class TourStepTests(TestCase):
    def setUp(self):
        prepared_client()
        self.user = get_user_model().objects.create_superuser("watch1_root", password="x-Pass-12345")

    def card(self, activity, sub, lang):
        request = RequestFactory().get("/dashboard/")
        request.user = self.user
        return demo_first_run.tour_card(request, lang, SimpleNamespace(activity_slug=activity, sub_activity_slug=sub))

    def test_the_close_the_day_step_opens_todays_summary_not_yesterdays(self):
        today = timezone.localdate().isoformat()
        for activity, sub in (("commercial", "pharmacy"), ("commercial", "retail")):
            for lang, title in (("ar", "اقفل اليوم وشوف الربح"), ("en", "Close the day and see the profit")):
                with self.subTest(activity=activity, sub=sub, lang=lang):
                    steps = {step["title"]: step["url"] for step in self.card(activity, sub, lang)["steps"]}
                    self.assertEqual(steps[title], f"/reports/daily/?lang={lang}&date={today}")

    def test_the_other_steps_keep_their_plain_links(self):
        steps = self.card("commercial", "pharmacy", "ar")["steps"]
        self.assertEqual(steps[0]["url"], "/sales/pos/?lang=ar")
