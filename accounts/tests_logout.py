"""Logout and stale-token behaviour with CSRF enforcement switched on.

Django's test client skips CSRF checks by default, which is how a daily 403 on
logout went unnoticed. Every client here enforces them, and every token is
scraped from a rendered page exactly as a browser would submit it.
"""

import re

from django.test import Client, TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode


PASSWORD = "logout-test-pass-1"
TOKEN = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
RAW_DJANGO_PAGE = "CSRF verification failed"


class LogoutCsrfTests(TestCase):
    def setUp(self):
        self.user = make_user(username="logout_owner")
        self.user.set_password(PASSWORD)
        self.user.save()
        make_user_profile(user=self.user, role=make_seeded_role(RoleCode.OWNER))
        self.client = Client(enforce_csrf_checks=True)

    def token_from(self, url):
        response = self.client.get(url, follow=True)
        match = TOKEN.search(response.content.decode())
        self.assertIsNotNone(match, f"no CSRF token rendered on {url}")
        return match.group(1)

    def log_in(self):
        token = self.token_from(reverse("login"))
        response = self.client.post(
            reverse("login"),
            {"username": "logout_owner", "password": PASSWORD, "csrfmiddlewaretoken": token},
        )
        self.assertEqual(response.status_code, 302)

    def test_logout_from_the_dashboard_signs_out(self):
        self.log_in()
        token = self.token_from(reverse("dashboard_snapshot"))
        response = self.client.post(reverse("logout"), {"csrfmiddlewaretoken": token})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_signed_out_logout_from_stale_page_goes_straight_to_login(self):
        self.log_in()
        token = self.token_from(reverse("dashboard_snapshot"))
        self.client.post(reverse("logout"), {"csrfmiddlewaretoken": token})

        # Back button to the cached dashboard, logout pressed again.
        response = self.client.post(reverse("logout"), {"csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)
        # Previously /login/?next=/logout/, which after the next sign-in sent
        # the user to a GET of the POST-only logout view (405).
        self.assertEqual(response.url, reverse("login"))

    def test_stale_tab_after_new_login_gets_a_readable_page_not_a_raw_403(self):
        self.log_in()
        stale = self.token_from(reverse("dashboard_snapshot"))
        self.client.post(reverse("logout"), {"csrfmiddlewaretoken": stale})
        self.log_in()  # rotates the CSRF secret; the stale token no longer matches

        response = self.client.post(reverse("logout"), {"csrfmiddlewaretoken": stale})
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, RAW_DJANGO_PAGE, status_code=403)
        self.assertContains(response, "تأكيد تسجيل الخروج", status_code=403)
        self.assertContains(response, "Confirm logout", status_code=403)
        # Protection holds: the unverified request did not sign anyone out.
        self.assertIn("_auth_user_id", self.client.session)

        # The page carries a fresh token, so one more click completes logout.
        fresh = TOKEN.search(response.content.decode()).group(1)
        response = self.client.post(reverse("logout"), {"csrfmiddlewaretoken": fresh})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_without_any_token_does_not_sign_out(self):
        self.log_in()
        response = self.client.post(reverse("logout"))
        self.assertEqual(response.status_code, 403)
        self.assertIn("_auth_user_id", self.client.session)

    def test_other_stale_forms_get_the_bilingual_expired_page(self):
        stale = self.token_from(reverse("login"))
        self.log_in()
        self.client.post(reverse("logout"), {"csrfmiddlewaretoken": self.token_from(reverse("dashboard_snapshot"))})
        self.log_in()

        response = self.client.post(
            reverse("login"),
            {"username": "logout_owner", "password": PASSWORD, "csrfmiddlewaretoken": stale},
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, RAW_DJANGO_PAGE, status_code=403)
        self.assertContains(response, "انتهت صلاحية الصفحة", status_code=403)
        self.assertContains(response, "This page has expired", status_code=403)
        self.assertContains(response, f'href="{reverse("login")}"', status_code=403)

    def test_logout_rejects_get(self):
        self.log_in()
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_expired_page_reloads_the_page_the_form_came_from(self):
        # A stale setup-review form posts to /setup/complete/; reloading that
        # target as a GET would lose the choices held in the review URL.
        stale = self.token_from(reverse("login"))
        self.log_in()
        review = "http://testserver/setup/review/?activity=commercial&modules=sales&lang=en"
        response = self.client.post(
            reverse("setup_complete"), {"csrfmiddlewaretoken": stale}, HTTP_REFERER=review
        )
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            'href="http://testserver/setup/review/?activity=commercial&amp;modules=sales&amp;lang=en"',
            status_code=403,
        )

    def test_expired_page_ignores_a_foreign_referer(self):
        stale = self.token_from(reverse("login"))
        self.log_in()
        response = self.client.post(
            reverse("setup_complete"),
            {"csrfmiddlewaretoken": stale},
            HTTP_REFERER="https://evil.example/phish",
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "evil.example", status_code=403)
        self.assertContains(response, f'href="{reverse("setup_complete")}"', status_code=403)
