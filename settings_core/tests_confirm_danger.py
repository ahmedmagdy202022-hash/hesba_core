"""FB-27: "are you sure?" before anything that deletes, removes or cancels."""

from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import SimpleTestCase


class ConfirmDangerTests(SimpleTestCase):
    def test_every_signed_in_page_asks_first(self):
        source = get_template("base_app.html").template.source
        self.assertIn("hesba/js/confirm_danger.js", source)
        self.assertLess(source.index("confirm_danger.js"), source.index("submit_once.js"))  # asked before the form is marked as sent

    def test_what_counts_as_risky_and_what_is_left_alone(self):
        with open(finders.find("hesba/js/confirm_danger.js"), encoding="utf-8") as handle:
            js = handle.read()
        for needed in ("op-danger", "/^(remove|delete|cancel)/", "data-no-confirm", "onsubmit", "data-confirm", "data-confirmed", "requestSubmit",
                       "event.preventDefault()", "}, true);"):
            with self.subTest(needed=needed):
                self.assertIn(needed, js)
        with open(finders.find("hesba/css/form_controls.css"), encoding="utf-8") as handle:
            self.assertIn(".hs-confirm", handle.read())
