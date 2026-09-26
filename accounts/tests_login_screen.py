"""LOGIN-001 (roadmap D3.1): the B+ sign-in screen."""

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse


class LoginScreenTests(TestCase):
    def test_brand_half_uses_the_reversed_lockup_and_real_text(self):
        body = self.client.get(reverse("login")).content.decode()
        self.assertIn("hesba/brand/hesba-logo-reversed.png", body)
        self.assertIn('data-i18n="tagline"', body)
        # The credit is real text now, not pixels inside a background image.
        self.assertIn("Developed by <strong>Ahmed Magdy</strong>", body)

    def test_old_artwork_background_is_not_loaded(self):
        with open(finders.find("hesba/css/login.css"), encoding="utf-8") as handle:
            css = handle.read()
        self.assertNotIn("login_web.final", css)
        self.assertNotIn("background-image", css)

    def test_form_contract_is_unchanged(self):
        body = self.client.get(reverse("login")).content.decode()
        for fragment in ('id="id_username"', 'id="id_password"', 'name="hesba_lang"', 'data-lang-option="en"', 'class="login-btn"'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, body)

    def test_wrong_password_shows_the_error_box(self):
        response = self.client.post(reverse("login"), {"username": "nobody", "password": "wrong"})
        self.assertContains(response, 'class="error" role="alert"')
