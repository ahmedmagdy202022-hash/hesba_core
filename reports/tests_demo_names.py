"""The demo's people are named the way a business would introduce them (Ahmed, 12 Nov 2026: «بلاش كلمة الأسطى»)."""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from config.demo_templates import DEMO_OWNERS, owner_name

UNWANTED = ("أسطى", "اسطى", "أسطي", "اسطي", "أوسطى", "اوسطى")


class DemoNamesTests(SimpleTestCase):
    def test_no_demo_owner_is_called_usta(self):
        for key, name in DEMO_OWNERS.items():
            for word in UNWANTED:
                self.assertNotIn(word, name, key)
        self.assertEqual(owner_name("services", "maintenance"), "م. محمد رمضان")

    def test_no_seeded_name_in_the_demo_sources_carries_the_word(self):
        base = Path(settings.BASE_DIR)
        for relative in ("reports/demo_catalogs.py", "config/demo_templates.py", "config/demo.py", "settings_core/management/commands/prepare_demo.py"):
            text = (base / relative).read_text(encoding="utf-8")
            for word in UNWANTED:
                for line in text.splitlines():
                    if word in line:
                        self.assertIn("أسطوان", line, f"{relative}: {line.strip()[:80]}")  # a gas cylinder is not a person
