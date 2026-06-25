import json

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class LoginAndDeviceShellSmokeTests(TestCase):
    def test_login_route_returns_200(self):
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تسجيل الدخول')
        self.assertContains(response, 'الدخول إلى حسبة')
        self.assertContains(response, 'hesba/css/login.css')
        self.assertContains(response, 'data-lang-option')

    def test_root_redirects_to_login(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('login'))

    def test_existing_safe_routes_still_return_200(self):
        for path in ['/dashboard/', '/reports/', '/status/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_manifest_start_url_points_to_login(self):
        manifest_path = settings.BASE_DIR / 'static' / 'hesba' / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(manifest['start_url'], '/login/')


class SetupGateSmokeTests(TestCase):
    def test_setup_gate_route_returns_approved_gate(self):
        response = self.client.get('/setup/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'جهّز حِسبة حسب نشاطك')
        self.assertContains(response, 'نظام إدارة متكامل قابل للتخصيص حسب نشاطك')
        self.assertContains(response, 'hesba/css/setup.css')
        self.assertContains(response, 'setup_gate_web_approved.png')
        self.assertContains(response, 'setup_gate_tablet_approved.png')
        self.assertContains(response, 'setup_gate_mobile_approved.png')
        self.assertContains(response, '/setup/activity/')
        self.assertContains(response, 'تسجيل الخروج')
        self.assertContains(response, 'تجاري')
        self.assertContains(response, 'مطاعم')
        self.assertContains(response, 'data-lang-option')
        self.assertNotContains(response, 'ERP System')

    def test_setup_gate_is_english_ready(self):
        response = self.client.get('/setup/?lang=en')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Set up Hesba for your activity')
        self.assertContains(response, 'customizable management system')
        self.assertContains(response, 'Start Setup')
        self.assertContains(response, 'Logout')
