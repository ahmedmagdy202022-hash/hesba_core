from django.conf import settings
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

    def test_service_subactivity_placeholder_route_renders_without_business_logic(self):
        response = self.client.get(reverse('setup_activity_service'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'صفحة مؤقتة آمنة')
        self.assertContains(response, 'out of scope for 117A')


class CommercialSubActivitySelectionTests(TestCase):
    def test_commercial_subactivity_route_renders(self):
        response = self.client.get(reverse('setup_activity_commercial'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Commercial Sub-Activity Selection')
        self.assertContains(response, 'activity-stage')
        self.assertContains(response, 'activity-bg-frame')
        self.assertContains(response, 'activity-ui-layer')
        self.assertContains(response, 'activity-panel')
        self.assertContains(response, 'activity-stepper')
        self.assertContains(response, 'activity-footer')
        self.assertContains(response, 'activity-action')

    def test_commercial_subactivity_arabic_content_exists(self):
        response = self.client.get('/setup/activity/commercial/?lang=ar')

        self.assertContains(response, 'اختر نوع النشاط التجاري')
        self.assertContains(response, 'اختيار نوع النشاط يساعد حِسْبَة في تجهيز الموديولات المناسبة لطريقة البيع والمخزون.')
        self.assertContains(response, 'محل تجزئة')
        self.assertContains(response, 'سوبر ماركت / بقالة')
        self.assertContains(response, 'نشاط تجاري آخر')

    def test_commercial_subactivity_english_content_exists(self):
        response = self.client.get('/setup/activity/commercial/?lang=en')

        self.assertContains(response, 'Choose commercial activity type')
        self.assertContains(response, 'Choosing the activity type helps Hesba prepare the right modules for sales and inventory.')
        self.assertContains(response, 'Retail store')
        self.assertContains(response, 'Supermarket / Grocery')
        self.assertContains(response, 'Other commercial')

    def test_commercial_subactivity_has_8_selectable_cards(self):
        response = self.client.get(reverse('setup_activity_commercial'))

        self.assertContains(response, 'data-sub-activity=', count=8)
        self.assertContains(response, 'subactivity-card is-active', count=8)
        self.assertNotContains(response, 'subactivity-card is-disabled')
        self.assertNotContains(response, 'disabled aria-disabled')

    def test_next_starts_disabled(self):
        response = self.client.get(reverse('setup_activity_commercial'))

        self.assertContains(response, 'disabled data-next-button')

    def test_card_slugs_exist(self):
        response = self.client.get(reverse('setup_activity_commercial'))

        for slug in [
            'retail',
            'grocery',
            'fashion',
            'electronics',
            'pharmacy',
            'wholesale',
            'online',
            'other',
        ]:
            self.assertContains(response, f'data-sub-activity="{slug}"')

    def test_back_target_preserves_language(self):
        response = self.client.get('/setup/activity/commercial/?lang=en')

        self.assertContains(response, 'href="/setup/activity/?lang=ar"')
        self.assertContains(response, 'function withLang(path)')
        self.assertContains(response, "back.href=withLang('/setup/activity/')")
        self.assertContains(response, "url.searchParams.set('lang',lang)")

    def test_next_target_includes_lang_activity_and_sub_activity(self):
        response = self.client.get(reverse('setup_activity_commercial'))

        self.assertContains(response, 'function moduleTarget(slug)')
        self.assertContains(response, "url.searchParams.set('lang',lang)")
        self.assertContains(response, "url.searchParams.set('activity','commercial')")
        self.assertContains(response, "url.searchParams.set('sub_activity',slug)")
        self.assertContains(response, "'/setup/modules/'")

    def test_117a_activity_route_still_works(self):
        response = self.client.get(reverse('setup_activity'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'اختر النشاط العام')
        self.assertContains(response, 'Choose general activity')
        self.assertContains(response, 'data-activity="commercial"')
        self.assertContains(response, 'data-next="/setup/activity/commercial/"')

    def test_setup_gate_still_routes_to_117a(self):
        response = self.client.get(reverse('setup_gate'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/setup/activity/?lang=ar"')
        self.assertContains(response, 'data-setup-start')

    def test_no_background_image_generation_change(self):
        template_path = settings.BASE_DIR / 'templates' / 'setup' / 'activity_commercial_subactivity.html'
        template = template_path.read_text(encoding='utf-8')

        self.assertIn('activity-bg-frame', template)
        self.assertIn('setup_gate_logo_approved.png', template)
        self.assertNotIn('setup_gate_web_background_approved.png', template)
        self.assertNotIn('background_approved.png', template)
        self.assertNotIn('image_gen', template)
        self.assertNotIn('generated background', template.lower())

    def test_117a_visual_lock_file_exists(self):
        lock_path = settings.BASE_DIR / 'docs' / '117A_SETUP_FLOW_VISUAL_LOCK.md'
        self.assertTrue(lock_path.exists())
        lock = lock_path.read_text(encoding='utf-8')

        self.assertIn('APPROVED_MAIN_LOCK', lock)
        self.assertIn('activity-stage', lock)
        self.assertIn('activity-bg-frame', lock)
        self.assertIn('activity-ui-layer', lock)
        self.assertIn('activity-panel', lock)
        self.assertIn('activity-stepper', lock)
        self.assertIn('activity-footer', lock)
        self.assertIn('activity-action', lock)


class ModulesPlaceholderFor117BTests(TestCase):
    def test_modules_placeholder_is_safe_and_out_of_scope(self):
        response = self.client.get('/setup/modules/?lang=en&activity=commercial&sub_activity=retail')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Modules')
        self.assertContains(response, 'out of scope for 117B')
        self.assertContains(response, '/setup/activity/commercial/?lang=')
