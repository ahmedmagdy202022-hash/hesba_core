"""The design-token stylesheet is present, complete and wired into the shell."""

import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode


REQUIRED_TOKENS = (
    "--hs-navy", "--hs-teal", "--hs-on-teal", "--hs-teal-deep", "--hs-gold", "--hs-gold-bright",
    "--hs-ground", "--hs-surface", "--hs-ink", "--hs-muted", "--hs-line",
    "--hs-success", "--hs-warning", "--hs-danger", "--hs-info",
    "--hs-font-body", "--hs-font-head", "--hs-text-base", "--hs-leading",
    "--hs-space-4", "--hs-radius-md", "--hs-control-height", "--hs-shadow-md", "--hs-focus-ring",
)


def tokens_css():
    path = finders.find("hesba/css/tokens.css")
    assert path, "hesba/css/tokens.css is not collectable"
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TokenFileTests(SimpleTestCase):
    def test_every_required_token_is_defined(self):
        css = tokens_css()
        for token in REQUIRED_TOKENS:
            with self.subTest(token=token):
                self.assertRegex(css, re.escape(token) + r"\s*:")

    def test_every_font_file_referenced_is_shipped(self):
        # A missing woff2 fails silently in the browser (fallback font), so
        # it has to fail here instead.
        urls = re.findall(r"url\('\.\./fonts/([^']+)'\)", tokens_css())
        self.assertGreaterEqual(len(urls), 12)
        for url in urls:
            with self.subTest(font=url):
                self.assertIsNotNone(finders.find(f"hesba/fonts/{url}"))

    def test_fonts_ship_with_their_licence(self):
        for family in ("ibm-plex-sans-arabic", "noto-kufi-arabic"):
            with self.subTest(family=family):
                self.assertIsNotNone(finders.find(f"hesba/fonts/{family}/OFL.txt"))

    def test_no_third_party_font_cdn(self):
        self.assertNotIn("fonts.googleapis.com", tokens_css())


class TokenWiringTests(TestCase):
    def test_signed_in_screens_load_the_tokens(self):
        user = make_user(username="tokens_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)
        for name in ("dashboard_snapshot", "sales:list", "master_data:hub"):
            with self.subTest(route=name):
                self.assertContains(self.client.get(reverse(name)), "hesba/css/tokens.css")
