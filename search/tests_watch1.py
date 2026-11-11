"""Watch round 1, SH-11: the search page's keyboard hint is hidden on a phone."""

import re

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


class KeyboardHintTests(TestCase):
    def setUp(self):
        prepared_client()
        user = make_user(username="search_w1")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)

    def test_the_hint_is_still_on_the_page_and_carries_the_phone_hiding_class(self):
        ar = self.client.get(reverse("search:results")).content.decode()
        self.assertIn('<p class="op-muted hs-kbd-hint" data-search-hint>اختصار: اضغط / أو Ctrl+K من أي شاشة.</p>', ar)
        en = self.client.get(reverse("search:results"), {"lang": "en"}).content.decode()
        self.assertIn('<p class="op-muted hs-kbd-hint" data-search-hint>Shortcut: press / or Ctrl+K on any screen.</p>', en)

    def test_the_stylesheet_hides_it_at_the_phone_breakpoint_with_no_new_colour(self):
        with open(finders.find("hesba/css/lists.css"), encoding="utf-8") as handle:
            css = handle.read()
        self.assertIn("@media (max-width:700px){.hs-phone-only{display:inline;margin-inline-end:.25em}.hs-kbd-hint{display:none}}", css)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
