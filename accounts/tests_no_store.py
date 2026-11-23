from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from config.middleware import NoStoreHtmlMiddleware
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode


class NoStoreHtmlTests(TestCase):
    def login(self):
        user = make_user(username="cache_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)

    def assertNoStore(self, response):
        cache_control = response.get("Cache-Control", "")
        self.assertIn("no-store", cache_control)
        self.assertIn("private", cache_control)

    def test_signed_in_financial_pages_are_not_cached(self):
        # The Back button after logout must not re-display these.
        self.login()
        for name in ("dashboard_snapshot", "sales:list", "cashboxes:list", "reports:profit"):
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertNoStore(response)

    def test_login_page_is_not_cached(self):
        # A cached login form would post a stale CSRF token.
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertNoStore(response)

    def test_explicit_cache_control_and_non_html_are_left_alone(self):
        def run(response):
            return NoStoreHtmlMiddleware(lambda request: response)(RequestFactory().get("/"))

        chosen = HttpResponse("<p>x</p>")
        chosen["Cache-Control"] = "max-age=60"
        self.assertEqual(run(chosen)["Cache-Control"], "max-age=60")

        data = JsonResponse({"ok": True})
        self.assertFalse(run(data).has_header("Cache-Control"))
