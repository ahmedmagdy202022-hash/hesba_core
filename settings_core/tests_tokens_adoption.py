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

    def test_login_has_no_colour_literals(self):
        # LOGIN-001 (D3.1) replaced the artwork-matched login with the B+ one.
        css = read_static("hesba/css/login.css")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
        self.assertIn("var(--hs-font-head)", css)
        self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_login_loads_the_tokens_first(self):
        # login.css reads var(--hs-*): without tokens.css the text falls back to
        # black serif and the active language pill disappears.
        body = self.client.get(reverse("login")).content.decode()
        self.assertIn("hesba/css/tokens.css", body)
        self.assertLess(body.index("hesba/css/tokens.css"), body.index("hesba/css/login.css"))


SETUP_SHEETS = (
    "hesba/css/activity_selection.css",
    "hesba/css/activity_selection_final_overrides.css",
    "hesba/css/activity_modules_selection.css",
    "hesba/css/activity_review_setup.css",
    "hesba/css/setup_gate_web.css",
    "hesba/css/setup_gate_pack.css",
    "hesba/css/setup_gate_mobile_refine.css",
)


class SetupSheetsAdoptionTests(TestCase):
    """SETUP-001 (roadmap D3.2): the setup wizard reads the same tokens."""

    def test_no_colour_literals(self):
        for sheet in SETUP_SHEETS:
            with self.subTest(sheet=sheet):
                css = read_static(sheet)
                self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
                # Translucent colours go through the channel tokens.
                self.assertEqual(re.findall(r"rgba?\(\s*\d", css), [])
                self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_every_token_used_is_defined(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
        for sheet in SETUP_SHEETS:
            with self.subTest(sheet=sheet):
                used = set(re.findall(r"var\((--hs-[\w-]+)", read_static(sheet)))
                self.assertEqual(sorted(used - defined), [])

    def test_every_setup_page_loads_the_tokens_first(self):
        import pathlib

        from django.conf import settings

        for template in sorted((pathlib.Path(settings.BASE_DIR) / "templates/setup").glob("*.html")):
            with self.subTest(template=template.name):
                html = template.read_text(encoding="utf-8")
                self.assertIn("hesba/css/tokens.css", html)
                first_sheet = html.index("stylesheet")
                self.assertIn("tokens.css", html[first_sheet:first_sheet + 120])
