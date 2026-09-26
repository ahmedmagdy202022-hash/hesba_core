"""TOKENS-002: the operations and master-data screens draw from the B+ tokens.

A colour typed straight into these stylesheets drifts from the palette the next
time the tokens change, and a misspelt token fails silently in the browser
(the property is dropped), so both have to fail here instead.
"""

import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


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
