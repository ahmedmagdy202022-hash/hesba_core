"""TOKENS-002: the operations and master-data screens draw from the B+ tokens.

A colour typed straight into these stylesheets drifts from the palette the next
time the tokens change, and a misspelt token fails silently in the browser
(the property is dropped), so both have to fail here instead.
"""

import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


ADOPTING_SHEETS = ("hesba/css/operations.css", "hesba/css/master_data.css")


def read_static(path):
    found = finders.find(path)
    assert found, f"{path} is not collectable"
    with open(found, encoding="utf-8") as handle:
        return handle.read()


class TokenAdoptionTests(SimpleTestCase):
    def test_no_colour_literals_besides_white(self):
        for sheet in ADOPTING_SHEETS:
            with self.subTest(sheet=sheet):
                css = read_static(sheet)
                literals = [c for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css) if c.lower() not in {"#fff", "#ffffff"}]
                self.assertEqual(literals, [])
                self.assertNotRegex(css, r"rgba?\(")

    def test_every_token_used_is_defined(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
        for sheet in ADOPTING_SHEETS:
            with self.subTest(sheet=sheet):
                used = set(re.findall(r"var\((--hs-[\w-]+)", read_static(sheet)))
                self.assertTrue(used)
                self.assertEqual(sorted(used - defined), [])

    def test_body_and_headings_use_the_token_faces(self):
        for sheet in ADOPTING_SHEETS:
            with self.subTest(sheet=sheet):
                css = read_static(sheet)
                self.assertIn("var(--hs-font-body)", css)
                self.assertIn("var(--hs-font-head)", css)
                # The self-hosted faces ship 400-700 only; heavier weights are faux-bolded.
                self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_primary_buttons_are_teal_with_navy_text(self):
        # White on teal fails contrast (about 2.7:1); navy on teal passes AA.
        for sheet, selector in zip(ADOPTING_SHEETS, (".op-primary", ".md-primary")):
            with self.subTest(sheet=sheet):
                rule = re.search(re.escape(selector) + r"\{([^}]*)\}", read_static(sheet))
                self.assertIsNotNone(rule)
                self.assertIn("background:var(--hs-teal)", rule.group(1))
                self.assertIn("color:var(--hs-on-teal)", rule.group(1))

    def test_keyboard_focus_stays_visible(self):
        for sheet in ADOPTING_SHEETS:
            with self.subTest(sheet=sheet):
                self.assertIn("var(--hs-focus-ring)", read_static(sheet))


class RemainingSheetsAdoptionTests(TestCase):
    """TOKENS-003: the dashboard and the login read the same tokens."""

    # The login keeps the gold pair, its off-white ground and the glass whites:
    # they are matched to the approved login artwork until D3.1 redesigns it.
    LOGIN_ART_COLOURS = {"#d9ad50", "#f5dc91", "#f6fbfb", "#fff", "#a5adb8"}

    def test_every_token_used_is_defined(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
        for sheet in ("hesba/css/dashboard.css", "hesba/css/login.css", "hesba/css/shell.css", "hesba/css/table_cards.css"):
            with self.subTest(sheet=sheet):
                used = set(re.findall(r"var\((--hs-[\w-]+)", read_static(sheet)))
                self.assertTrue(used)
                self.assertEqual(sorted(used - defined), [])

    def test_dashboard_has_no_colour_literals(self):
        css = read_static("hesba/css/dashboard.css")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
        self.assertIn("var(--hs-font-head)", css)

    def test_login_keeps_only_the_art_matched_colours(self):
        css = read_static("hesba/css/login.css")
        self.assertEqual(sorted({c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css)} - self.LOGIN_ART_COLOURS), [])
        # The pre-B+ teal and navy are gone, glows included.
        for old in ("#16bdc4", "#05243f", "22,189,196", "5,36,63"):
            with self.subTest(old=old):
                self.assertNotIn(old, css.lower())
        self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_login_loads_the_tokens_first(self):
        # login.css reads var(--hs-*): without tokens.css the text falls back to
        # black serif and the active language pill disappears.
        body = self.client.get(reverse("login")).content.decode()
        self.assertIn("hesba/css/tokens.css", body)
        self.assertLess(body.index("hesba/css/tokens.css"), body.index("hesba/css/login.css"))
