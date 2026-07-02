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

MODULE_SLUGS = [
    'customers',
    'suppliers',
    'items_services',
    'sales_operations',
    'purchases',
    'inventory',
    'cashboxes',
    'expenses',
    'reports',
    'pdf_printing',
    'appointments_visits',
    'employees_technicians',
]

COMMERCIAL_REQUIRED_MODULES = [
    'sales_operations',
    'items_services',
    'cashboxes',
    'reports',
]
COMMERCIAL_SUGGESTED_MODULES = [
    'customers',
    'suppliers',
    'purchases',
    'inventory',
    'expenses',
    'pdf_printing',
]
COMMERCIAL_OPTIONAL_MODULES = [
    'appointments_visits',
    'employees_technicians',
]

SERVICES_REQUIRED_MODULES = [
    'items_services',
    'customers',
    'cashboxes',
    'reports',
]
SERVICES_SUGGESTED_MODULES = [
    'expenses',
    'pdf_printing',
]
SERVICES_OPTIONAL_MODULES = [
    'suppliers',
    'purchases',
    'inventory',
    'appointments_visits',
    'employees_technicians',
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


class ModulesSelectionScreenTests(TestCase):
    def test_modules_selection_route_renders(self):
        response = self.client.get('/setup/modules/?lang=ar&activity=commercial&sub_activity=retail')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Modules Selection')
        self.assertContains(response, 'activity-stage')
        self.assertContains(response, 'activity-bg-frame')
        self.assertContains(response, 'activity-ui-layer')
        self.assertContains(response, 'activity-panel')
        self.assertContains(response, 'activity-stepper')
        self.assertContains(response, 'activity-step is-current')
        self.assertContains(response, 'activity-footer')
        self.assertContains(response, 'activity-action')
        self.assertContains(response, 'hesba/css/activity_modules_selection.css')
        self.assertNotContains(response, 'disabled data-next-button')

    def test_modules_arabic_content_exists(self):
        response = self.client.get('/setup/modules/?lang=ar&activity=commercial&sub_activity=retail')

        arabic_labels = [
            'اختر الموديولات المناسبة',
            'جهزنا لك اقتراحًا مبدئيًا حسب نوع نشاطك، ويمكنك تعديله قبل المتابعة.',
            'أساسي',
            'مقترح',
            'اختياري',
            'مقفول',
            'مفعّل',
            'غير مفعّل',
            'الأصناف والخدمات',
            'عمليات البيع',
            'الخزن',
            'التقارير',
        ]
        for label in arabic_labels:
            self.assertContains(response, label)

    def test_modules_english_content_exists(self):
        response = self.client.get('/setup/modules/?lang=en&activity=commercial&sub_activity=retail')

        english_labels = [
            'Choose suitable modules',
            'We prepared an initial suggestion based on your activity type, and you can adjust it before continuing.',
            'Required',
            'Suggested',
            'Optional',
            'Locked',
            'On',
            'Off',
            'Items &amp; services',
            'Sales operations',
            'Cashboxes',
            'Reports',
        ]
        for label in english_labels:
            self.assertContains(response, label)

    def test_all_module_slugs_exist_once_each(self):
        response = self.client.get(reverse('setup_modules'))

        self.assertContains(response, 'class="module-card"', count=12)
        for slug in MODULE_SLUGS:
            self.assertContains(response, f'data-module="{slug}"', count=1)

    def test_commercial_preset_required_suggested_optional_modules_exist(self):
        response = self.client.get('/setup/modules/?lang=ar&activity=commercial&sub_activity=retail')

        self.assertContains(response, 'data-commercial-state="required"', count=4)
        self.assertContains(response, 'data-commercial-state="suggested"', count=6)
        self.assertContains(response, 'data-commercial-state="optional"', count=2)
        for slug in COMMERCIAL_REQUIRED_MODULES:
            self.assertContains(response, f'data-module="{slug}" data-commercial-state="required"')
        for slug in COMMERCIAL_SUGGESTED_MODULES:
            self.assertContains(response, f'data-module="{slug}" data-commercial-state="suggested"')
        for slug in COMMERCIAL_OPTIONAL_MODULES:
            self.assertContains(response, f'data-module="{slug}" data-commercial-state="optional"')

    def test_services_preset_required_suggested_optional_modules_exist(self):
        response = self.client.get('/setup/modules/?lang=ar&activity=services&sub_activity=general')

        self.assertContains(response, 'data-services-state="required"', count=4)
        self.assertContains(response, 'data-services-state="suggested"', count=2)
        self.assertContains(response, 'data-services-state="optional"', count=6)
        for slug in SERVICES_REQUIRED_MODULES:
            self.assertContains(response, f'data-module="{slug}" data-commercial-state=', html=False)
            self.assertContains(response, f'data-services-state="required"')
        for slug in SERVICES_SUGGESTED_MODULES:
            self.assertContains(response, f'data-module="{slug}" data-commercial-state=', html=False)
            self.assertContains(response, f'data-services-state="suggested"')
        for slug in SERVICES_OPTIONAL_MODULES:
            self.assertContains(response, f'data-module="{slug}" data-commercial-state=', html=False)
            self.assertContains(response, f'data-services-state="optional"')

    def test_required_modules_are_locked_selected_and_cannot_be_turned_off(self):
        response = self.client.get(reverse('setup_modules'))

        self.assertContains(response, "card.classList.toggle('is-locked', state === 'required');")
        self.assertContains(response, "card.classList.toggle('is-selected', selected);")
        self.assertContains(response, "card.setAttribute('aria-disabled', state === 'required' ? 'true' : 'false');")
        self.assertContains(response, "if(state === 'required'){")
        self.assertContains(response, 'selectedModules.add(slug);')
        self.assertContains(response, 'return;')

    def test_suggested_modules_start_selected(self):
        response = self.client.get(reverse('setup_modules'))

        self.assertContains(response, 'function resetPresetSelection()')
        self.assertContains(response, "if(state === 'required' || state === 'suggested'){")
        self.assertContains(response, 'selectedModules.add(slug);')

    def test_optional_modules_start_unselected_and_are_toggleable(self):
        response = self.client.get(reverse('setup_modules'))

        self.assertContains(response, 'data-commercial-state="optional"', count=2)
        self.assertContains(response, 'data-services-state="optional"', count=6)
        self.assertContains(response, 'selectedModules.delete(slug);')
        self.assertContains(response, 'selectedModules.add(slug);')
        self.assertContains(response, "card.classList.toggle('is-optional', state === 'optional');")

    def test_back_target_preserves_lang_and_activity(self):
        commercial = self.client.get('/setup/modules/?lang=en&activity=commercial&sub_activity=retail')
        services = self.client.get('/setup/modules/?lang=en&activity=services&sub_activity=general')

        self.assertContains(commercial, 'Back to commercial activity type selection')
        self.assertContains(commercial, "return withLang('/setup/activity/commercial/');")
        self.assertContains(services, 'Back to service activity type selection')
        self.assertContains(services, "return withLang('/setup/activity/services/');")
        self.assertContains(services, "url.searchParams.set('lang', lang);")

    def test_next_target_includes_lang_activity_sub_activity_and_modules(self):
        response = self.client.get('/setup/modules/?lang=en&activity=commercial&sub_activity=retail')

        self.assertContains(response, 'function buildNextHref()')
        self.assertContains(response, "new URL('/setup/review/', window.location.origin)")
        self.assertContains(response, "url.searchParams.set('lang', lang);")
        self.assertContains(response, "url.searchParams.set('activity', rawActivity || 'unknown');")
        self.assertContains(response, "url.searchParams.set('sub_activity', subActivity);")
        self.assertContains(response, "url.searchParams.set('modules', selectedModulesCsv());")

    def test_missing_or_unknown_activity_fallback_is_safe(self):
        response = self.client.get('/setup/modules/?lang=en&activity=unknown&sub_activity=other')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Back to general activity selection')
        self.assertContains(response, 'fallbackBack')
        self.assertContains(response, "return withLang('/setup/activity/');")
        self.assertContains(response, "url.searchParams.set('activity', rawActivity || 'unknown');")

    def test_review_placeholder_route_exists_safely(self):
        response = self.client.get('/setup/review/?lang=en&activity=commercial&sub_activity=retail&modules=sales_operations,items_services')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Setup review')
        self.assertContains(response, 'safe placeholder')
        self.assertContains(response, 'Back to modules selection')
        self.assertContains(response, "new URL('/setup/modules/', window.location.origin)")

    def test_no_modules_background_or_shell_redesign(self):
        template_path = settings.BASE_DIR / 'templates' / 'setup' / 'modules_selection.html'
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

    def test_118_plan_file_exists(self):
        plan_path = settings.BASE_DIR / 'docs' / '118_MODULES_SELECTION_PLAN.md'
        self.assertTrue(plan_path.exists())
        plan = plan_path.read_text(encoding='utf-8')

        self.assertIn('PLANNING_APPROVED', plan)
        self.assertIn('/setup/modules/', plan)
        self.assertIn('Required  = ON and locked', plan)
        self.assertIn('Services can still use items, inventory, spare parts, consumables, purchases, and suppliers.', plan)
