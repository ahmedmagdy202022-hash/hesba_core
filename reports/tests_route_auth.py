from django.conf import settings
from django.test import TestCase
from django.urls import get_resolver, reverse
from django.urls.resolvers import URLResolver

from reports.test_utils import AuthenticatedTestCase


LOGIN_REQUIRED_MIDDLEWARE = "django.contrib.auth.middleware.LoginRequiredMiddleware"

# Routes deliberately reachable without authentication. Every other route in
# the URLconf must redirect anonymous visitors to the login page, so adding a
# route without gating it fails these tests until it is listed here on purpose.
PUBLIC_ROUTE_NAMES = frozenset(
    {
        "root_redirect",  # bare redirect to /login/, exposes nothing
        "login",
    }
)

# django.contrib.admin ships its own authentication and its own login page, so
# it is asserted as a whole instead of route by route.
ADMIN_ROUTE_PREFIX = "admin/"


def project_routes():
    """Every named, parameterless route declared by the root URLconf."""
    routes = []

    for pattern in get_resolver().url_patterns:
        if isinstance(pattern, URLResolver):
            continue

        route = str(pattern.pattern)
        if "<" in route:
            # Parameterised routes need fixtures to build a URL; there are none
            # today. If one appears, cover it explicitly rather than skipping.
            continue

        routes.append((pattern.name, "/" + route))

    return routes


def is_login_redirect(response):
    return response.status_code in (301, 302) and response["Location"].startswith(
        reverse("login")
    )


class RouteInventoryTests(TestCase):
    """Guards the inventory itself, so the allowlist cannot silently rot."""

    def test_urlconf_exposes_routes_to_check(self):
        self.assertGreater(len(project_routes()), 1)

    def test_public_allowlist_only_names_existing_routes(self):
        names = {name for name, _ in project_routes()}

        self.assertEqual(PUBLIC_ROUTE_NAMES - names, set())

    def test_login_required_middleware_is_enabled(self):
        self.assertIn(LOGIN_REQUIRED_MIDDLEWARE, settings.MIDDLEWARE)

    def test_login_required_middleware_runs_after_authentication(self):
        middleware = list(settings.MIDDLEWARE)

        self.assertLess(
            middleware.index("django.contrib.auth.middleware.AuthenticationMiddleware"),
            middleware.index(LOGIN_REQUIRED_MIDDLEWARE),
        )


class AnonymousRouteAccessTests(TestCase):
    def test_gated_routes_redirect_to_login(self):
        checked = 0

        for name, path in project_routes():
            if name in PUBLIC_ROUTE_NAMES:
                continue

            with self.subTest(route=name, path=path):
                response = self.client.get(path)

                self.assertTrue(
                    is_login_redirect(response),
                    f"{path} did not redirect anonymous visitors to the login page "
                    f"(got {response.status_code} "
                    f"-> {response.headers.get('Location')})",
                )
            checked += 1

        self.assertGreater(checked, 0)

    def test_public_routes_do_not_redirect_to_login(self):
        for name, path in project_routes():
            if name not in PUBLIC_ROUTE_NAMES:
                continue

            with self.subTest(route=name, path=path):
                response = self.client.get(path)

                self.assertNotEqual(response.status_code, 500)
                if name != "root_redirect":
                    self.assertFalse(is_login_redirect(response))

    def test_admin_is_gated_by_its_own_login(self):
        response = self.client.get("/" + ADMIN_ROUTE_PREFIX)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/admin/login/"))


class AuthenticatedRouteAccessTests(AuthenticatedTestCase):
    def test_no_route_bounces_an_authenticated_user_to_login(self):
        for name, path in project_routes():
            with self.subTest(route=name, path=path):
                response = self.client.get(path)

                self.assertFalse(
                    is_login_redirect(response),
                    f"{path} redirected an authenticated user to the login page",
                )

    def test_no_route_raises_a_server_error(self):
        for name, path in project_routes():
            with self.subTest(route=name, path=path):
                response = self.client.get(path)

                self.assertNotEqual(response.status_code, 500)
