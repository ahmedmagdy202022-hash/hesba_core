from django.conf import settings
from django.test import TestCase
from django.urls import reverse


SERVICES_SUB_ACTIVITY_SLUGS = [
    'general',
    'maintenance',
    'clinic',
    'beauty',
    'education',
    'professional',
    'digital_marketing',
    'other',
]


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
        self.assertContains(response, 'data-next="/setup/activity/services/"')
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

        arabic_labels = [
            'اختر نوع النشاط التجاري',
            'اختيار نوع النشاط يساعد حِسْبَة في تجهيز الموديولات المناسبة لطريقة البيع والمخزون.',
            'محل تجزئة',
            'سوبر ماركت / بقالة',
            'ملابس وأحذية',
            'موبايلات وإلكترونيات',
            'صيدلية',
            'جملة / مخزن',
            'بيع أونلاين',
            'نشاط تجاري آخر',
        ]
        for label in arabic_labels:
            self.assertContains(response, label)

    def test_commercial_subactivity_english_content_exists(self):
        response = self.client.get('/setup/activity/commercial/?lang=en')

        english_labels = [
            'Choose commercial activity type',
            'Choosing the activity type helps Hesba prepare the right modules for sales and inventory.',
            'Retail store',
            'Supermarket / Grocery',
            'Clothing & Shoes',
            'Mobiles & Electronics',
            'Pharmacy',
            'Wholesale / Warehouse',
            'Online selling',
            'Other commercial',
        ]
        for label in english_labels:
            self.assertContains(response, label)

    def test_commercial_subactivity_has_8_selectable_cards(self):
        response = self.client.get(reverse('setup_activity_commercial'))

        self.assertContains(response, 'data-sub-activity=', count=8)
        self.assertContains(response, 'subactivity-card is-active', count=8)
        self.assertNotContains(response, 'subactivity-card is-disabled')
        self.assertNotContains(response, 'disabled aria-disabled')

    def test_next_starts_disabled(self):
        response = self.client.get(reverse('setup_activity_commercial'))

        self.assertContains(response, 'disabled data-next-button')

    def test_card_slugs_exist_once_each(self):
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
            self.assertContains(response, f'data-sub-activity="{slug}"', count=1)

    def test_selection_script_enables_next_and_moves_single_selection(self):
        response = self.client.get(reverse('setup_activity_commercial'))

        self.assertContains(response, "function clearSelection()")
        self.assertContains(response, "card.classList.remove('is-selected')")
        self.assertContains(response, "card.setAttribute('aria-pressed','false')")
        self.assertContains(response, "card.classList.add('is-selected')")
        self.assertContains(response, "card.setAttribute('aria-pressed','true')")
        self.assertContains(response, "selectedSlug=card.dataset.subActivity||''")
        self.assertContains(response, "next.disabled=false")
        self.assertContains(response, "next.dataset.href=moduleTarget(selectedSlug)")

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
        self.assertContains(response, 'data-activity="services"')
        self.assertContains(response, 'data-next="/setup/activity/services/"')

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

    def test_117b_visual_approval_file_exists(self):
        approval_path = settings.BASE_DIR / 'docs' / '117B_COMMERCIAL_SUB_ACTIVITY_VISUAL_APPROVAL.md'
        self.assertTrue(approval_path.exists())
        approval = approval_path.read_text(encoding='utf-8')

        self.assertIn('VISUAL_APPROVED', approval)
        self.assertIn('/setup/activity/commercial/', approval)
        self.assertIn('retail', approval)
        self.assertIn('other', approval)


class ServicesSubActivitySelectionTests(TestCase):
    def test_services_subactivity_route_renders(self):
        response = self.client.get(reverse('setup_activity_services'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Services Sub-Activity Selection')
        self.assertContains(response, 'activity-stage')
        self.assertContains(response, 'activity-bg-frame')
        self.assertContains(response, 'activity-ui-layer')
        self.assertContains(response, 'activity-panel')
        self.assertContains(response, 'activity-stepper')
        self.assertContains(response, 'activity-footer')
        self.assertContains(response, 'activity-action')

    def test_services_subactivity_arabic_content_exists(self):
        response = self.client.get('/setup/activity/services/?lang=ar')

        arabic_labels = [
            'اختر نوع النشاط الخدمي',
            'اختيار نوع النشاط يساعد حِسْبَة في تجهيز الموديولات المناسبة لطريقة تقديم الخدمة، مع إمكانية إضافة أصناف أو مخزون حسب احتياجك.',
            'خدمات عامة',
            'صيانة وإصلاح',
            'عيادة / مركز طبي',
            'صالون / مركز تجميل',
            'مركز تعليمي / كورسات',
            'مكتب مهني',
            'تسويق وتصميم وخدمات رقمية',
            'نشاط خدمي آخر',
        ]
        for label in arabic_labels:
            self.assertContains(response, label)

    def test_services_subactivity_english_content_exists(self):
        response = self.client.get('/setup/activity/services/?lang=en')

        english_labels = [
            'Choose service activity type',
            'Choosing the service type helps Hesba prepare the right modules for how you deliver services, with items or inventory added when needed.',
            'General services',
            'Maintenance & Repair',
            'Clinic / Medical center',
            'Salon / Beauty center',
            'Education / Courses Center',
            'Professional Office',
            'Marketing, Design & Digital Services',
            'Other Service Activity',
        ]
        for label in english_labels:
            self.assertContains(response, label)

    def test_services_subactivity_has_8_selectable_cards(self):
        response = self.client.get(reverse('setup_activity_services'))

        self.assertContains(response, 'data-sub-activity=', count=8)
        self.assertContains(response, 'service-subactivity-card', count=8)
        self.assertContains(response, 'activity-card is-active service-subactivity-card', count=8)
        self.assertNotContains(response, 'is-disabled')
        self.assertNotContains(response, 'disabled aria-disabled')

    def test_services_next_starts_disabled(self):
        response = self.client.get(reverse('setup_activity_services'))

        self.assertContains(response, 'disabled data-next-button')

    def test_services_card_slugs_exist_once_each(self):
        response = self.client.get(reverse('setup_activity_services'))

        for slug in SERVICES_SUB_ACTIVITY_SLUGS:
            self.assertContains(response, f'data-sub-activity="{slug}"', count=1)

    def test_services_selection_script_enables_next_and_moves_single_selection(self):
        response = self.client.get(reverse('setup_activity_services'))

        self.assertContains(response, 'function clearSelection()')
        self.assertContains(response, "card.classList.remove('is-selected')")
        self.assertContains(response, "card.setAttribute('aria-pressed', 'false')")
        self.assertContains(response, "card.classList.add('is-selected')")
        self.assertContains(response, "card.setAttribute('aria-pressed', 'true')")
        self.assertContains(response, "selectedSubActivity = card.dataset.subActivity || ''")
        self.assertContains(response, 'next.disabled = false')
        self.assertContains(response, 'next.dataset.href = buildNextHref()')

    def test_services_back_target_preserves_language(self):
        response = self.client.get('/setup/activity/services/?lang=en')

        self.assertContains(response, 'href="/setup/activity/?lang=ar"')
        self.assertContains(response, 'function withLang(path)')
        self.assertContains(response, "back.href = withLang('/setup/activity/');")
        self.assertContains(response, "url.searchParams.set('lang', lang);")

    def test_services_next_target_includes_lang_activity_and_sub_activity(self):
        response = self.client.get(reverse('setup_activity_services'))

        self.assertContains(response, 'function buildNextHref()')
        self.assertContains(response, "new URL('/setup/modules/', window.location.origin)")
        self.assertContains(response, "url.searchParams.set('lang', lang);")
        self.assertContains(response, "url.searchParams.set('activity', 'services');")
        self.assertContains(response, "url.searchParams.set('sub_activity', selectedSubActivity);")

    def test_no_services_background_or_shell_redesign(self):
        template_path = settings.BASE_DIR / 'templates' / 'setup' / 'activity_services_subactivity.html'
        template = template_path.read_text(encoding='utf-8')

        self.assertIn('activity-stage', template)
        self.assertIn('activity-bg-frame', template)
        self.assertIn('activity-ui-layer', template)
        self.assertIn('activity-panel', template)
        self.assertIn('activity-stepper', template)
        self.assertIn('activity-footer', template)
        self.assertIn('activity-action', template)
        self.assertIn('setup_gate_logo_approved.png', template)
        self.assertNotIn('setup_gate_web_background_approved.png', template)
        self.assertNotIn('background_approved.png', template)
        self.assertNotIn('image_gen', template)
        self.assertNotIn('generated background', template.lower())

    def test_117c_plan_file_exists(self):
        plan_path = settings.BASE_DIR / 'docs' / '117C_SERVICES_SUB_ACTIVITY_PLAN.md'
        self.assertTrue(plan_path.exists())
        plan = plan_path.read_text(encoding='utf-8')

        self.assertIn('PLANNING_APPROVED', plan)
        self.assertIn('/setup/activity/services/', plan)
        self.assertIn('Service activity does not mean no inventory', plan)
        self.assertIn('digital_marketing', plan)


class ModulesPlaceholderFor117BTests(TestCase):
    def test_modules_placeholder_is_safe_and_out_of_scope(self):
        response = self.client.get('/setup/modules/?lang=en&activity=commercial&sub_activity=retail')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Modules')
        self.assertContains(response, 'out of scope for 117B')
        self.assertContains(response, '/setup/activity/commercial/?lang=')
