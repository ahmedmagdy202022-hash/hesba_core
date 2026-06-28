from django.test import TestCase
from django.urls import reverse


class ActivitySelectionScreenTests(TestCase):
    def test_activity_selection_route_renders_arabic_real_ui(self):
        response = self.client.get('/setup/activity/?lang=ar')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'اختيار النشاط العام - حِسْبَة')
        self.assertContains(response, 'hesba/css/activity_selection.css')
        self.assertContains(response, 'activity-stage')
        self.assertContains(response, 'activity-bg-frame')
        self.assertContains(response, 'activity-ui-layer')
        self.assertContains(response, 'اختر النشاط العام')
        self.assertContains(response, 'التالي: اختيار النشاط الفرعي')
        self.assertContains(response, 'data-next-button')
        self.assertContains(response, 'disabled data-next-button')

    def test_activity_selection_route_contains_english_translations(self):
        response = self.client.get('/setup/activity/?lang=en')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose general activity')
        self.assertContains(response, 'Next: choose sub-activity')
        self.assertContains(response, 'Commercial')
        self.assertContains(response, 'Services')
        self.assertContains(response, 'Soon')

    def test_activity_cards_state_contract(self):
        response = self.client.get(reverse('setup_activity'))

        self.assertContains(response, 'data-activity="commercial"')
        self.assertContains(response, 'data-next="/setup/activity/commercial/"')
        self.assertContains(response, 'data-activity="services"')
        self.assertContains(response, 'data-next="/setup/activity/service/"')
        self.assertContains(response, 'activity-card is-active', count=2)
        self.assertContains(response, 'activity-card is-disabled', count=6)
        self.assertContains(response, 'قريبًا')
        self.assertNotContains(response, 'جاهز الآن')
        self.assertNotContains(response, 'المتاح حاليًا')

    def test_activity_subactivity_placeholder_routes_render_without_business_logic(self):
        for route_name in ['setup_activity_commercial', 'setup_activity_service']:
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'صفحة مؤقتة آمنة')
                self.assertContains(response, 'out of scope for 117A')

    def test_activity_selection_css_uses_responsive_component_states(self):
        css_path = __import__('django.conf').conf.settings.BASE_DIR / 'static' / 'hesba' / 'css' / 'activity_selection.css'
        css = css_path.read_text(encoding='utf-8')

        self.assertIn('.activity-stage', css)
        self.assertIn('.activity-bg-frame', css)
        self.assertIn('display:grid', css)
        self.assertIn('display:flex', css)
        self.assertIn('repeat(4', css)
        self.assertIn('repeat(2', css)
        self.assertIn('is-selected', css)
        self.assertNotIn('mask-image', css)
        self.assertNotIn('-webkit-mask', css)
        self.assertNotIn('activity_selection_web_visual_approved.png', css)
