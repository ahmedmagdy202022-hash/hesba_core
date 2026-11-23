"""R2: every screen opens. Adding an entity or an account used to be a server
error that no test opened; this walks every URL that takes no arguments."""

from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core import setup_catalog as catalog

# Actions, downloads and the PWA files: not screens.
SKIP = ("logout", "demo/", "export", "download", "backup", "csv", "pdf", "manifest", "service-worker", "sw.js", "healthz", "nightly", "offline")


def plain_paths(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        route = str(entry.pattern)
        if isinstance(entry, URLResolver):
            if "<" not in route:
                yield from plain_paths(entry, prefix + route)
        elif isinstance(entry, URLPattern) and "<" not in route and not route.startswith("^"):
            path = "/" + prefix + route
            if not any(word in path for word in SKIP):
                yield path


@override_settings(DEMO_MODE=False)
class EveryScreenOpensTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        prepared_client(modules=",".join(catalog.MODULE_SLUGS))
        cls.owner = make_user(username="smoke_owner", is_superuser=True)
        make_user_profile(user=cls.owner, role=make_seeded_role(RoleCode.OWNER))

    def test_no_screen_is_a_server_error(self):
        self.client.raise_request_exception = False
        self.client.force_login(self.owner)
        paths = sorted(set(plain_paths()))
        self.assertGreater(len(paths), 60)
        broken = []
        for path in paths:
            for lang in ("ar", "en"):
                response = self.client.get(f"{path}?lang={lang}")
                if response.status_code >= 500:
                    broken.append(f"{path} ({lang}): {response.status_code}")
        self.assertEqual(broken, [])
