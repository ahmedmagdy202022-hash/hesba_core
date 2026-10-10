"""DATE-001: every app page loads the date picker, and date fields keep the
server contract it relies on (a native date input holding an ISO value)."""

import re

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


class DatePickerTests(TestCase):
    def setUp(self):
        prepared_client("commercial", "retail")
        user = make_user(username="date_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)

    def test_app_pages_load_the_picker(self):
        for url in (reverse("sales:create"), "/reports/profit/", "/dashboard/"):
            with self.subTest(url=url):
                page = self.client.get(url + "?lang=ar")
                self.assertContains(page, "hesba/js/datepicker.js")
                self.assertContains(page, "hesba/css/datepicker.css")

    def test_date_fields_stay_native_with_an_iso_value(self):
        page = self.client.get(reverse("sales:create") + "?lang=ar").content.decode()
        field = re.search(r'<input[^>]*name="invoice_date"[^>]*>', page).group(0)
        self.assertIn('type="date"', field)
        self.assertIn(f'value="{timezone.localdate().isoformat()}"', field)

    def test_the_script_and_styles_exist_and_use_tokens_only(self):
        root = settings.BASE_DIR / "static" / "hesba"
        script = (root / "js" / "datepicker.js").read_text(encoding="utf-8")
        for word in ("يوم/شهر/سنة", "النهارده", "dd/mm/yyyy", "يناير", "ديسمبر"):
            self.assertIn(word, script)
        css = (root / "css" / "datepicker.css").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", css), "colours come from the tokens")

    def test_copied_lines_and_required_stars_are_handled(self):
        root = settings.BASE_DIR / "static" / "hesba"
        script = (root / "js" / "datepicker.js").read_text(encoding="utf-8")
        self.assertIn("function revive(wrap)", script)
        self.assertIn("'aria-invalid'", script)  # a date Django marked invalid stays announced as invalid  # a row copied by "+ line" gets a working picker
        css = (root / "css" / "form_required.css").read_text(encoding="utf-8")
        self.assertIn(".hs-date > [required]", css)  # a required date keeps its star

    def test_changing_the_month_or_year_keeps_the_keyboard_in_the_calendar(self):
        script = (settings.BASE_DIR / "static" / "hesba" / "js" / "datepicker.js").read_text(encoding="utf-8")
        self.assertIn("self.jump(year, +e.target.value, '.hs-date__month')", script)
        self.assertIn("self.jump(+e.target.value, month, '.hs-date__year')", script)
        self.assertIn("if (again) again.focus();", script)  # the rebuilt select takes the focus back

    def test_the_calendar_is_never_clipped_by_a_scrolling_box(self):
        root = settings.BASE_DIR / "static" / "hesba"
        css = (root / "css" / "datepicker.css").read_text(encoding="utf-8")
        self.assertIn(".hs-date .hs-date__pop{position:fixed;", css)  # escapes .inv-lines / .op-table-wrap overflow
        script = (root / "js" / "datepicker.js").read_text(encoding="utf-8")
        self.assertIn("this.wrap.getBoundingClientRect()", script)
        self.assertIn("window.addEventListener('scroll'", script)  # follows the field when a box scrolls

    def test_a_cleared_or_retyped_date_drops_its_old_complaint(self):
        root = settings.BASE_DIR / "static" / "hesba" / "js"
        script = (root / "datepicker.js").read_text(encoding="utf-8")
        self.assertIn("this.text.removeAttribute('aria-invalid');  // an empty optional date is valid too", script)
        self.assertIn("this.text.addEventListener('input', function () { self.text.setCustomValidity('');", script)
        invoice = (root / "invoice_form.js").read_text(encoding="utf-8")
        self.assertIn("wrap.hsPicker.set(null)", invoice)  # the line's clear button goes through the picker

    def test_the_calendar_stays_on_a_short_screen(self):
        script = (settings.BASE_DIR / "static" / "hesba" / "js" / "datepicker.js").read_text(encoding="utf-8")
        self.assertIn("top = Math.max(gutter, Math.min(top, window.innerHeight - gutter - height));", script)
