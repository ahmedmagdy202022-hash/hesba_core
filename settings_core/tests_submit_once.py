"""UX-ONCE: a POST form is sent once, however fast it is clicked."""

from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import SimpleTestCase


class SubmitOnceTests(SimpleTestCase):
    def test_every_signed_in_page_carries_the_guard(self):
        self.assertIn("hesba/js/submit_once.js", get_template("base_app.html").template.source)

    def test_the_guard_covers_post_forms_only_and_lets_go_again(self):
        with open(finders.find("hesba/js/submit_once.js"), encoding="utf-8") as handle:
            js = handle.read()
        for needed in ("'post'", "data-submit-many", "defaultPrevented", "pageshow", "event.preventDefault()", "setTimeout"):
            with self.subTest(needed=needed):
                self.assertIn(needed, js)
        with open(finders.find("hesba/css/form_controls.css"), encoding="utf-8") as handle:
            self.assertIn("[data-submit-busy]", handle.read())
