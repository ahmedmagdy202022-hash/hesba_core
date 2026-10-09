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
