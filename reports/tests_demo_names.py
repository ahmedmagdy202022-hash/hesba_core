"""The product speaks like a premium product (Ahmed, 12 Nov 2026).

«بلاش كلمة الأسطى»، «بلاش ورشة وبلاش أي حاجة عموماً تدي انطباع متدني للبرنامج»: a repair shop is a service centre, a demo
owner is introduced with a professional title, and neither word comes back in a screen, a message or the demo data.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from config.demo_templates import DEMO_OWNERS, owner_name

UNWANTED_TITLES = ("أسطى", "اسطى", "أسطي", "اسطي", "أوسطى", "اوسطى", "الحاج", "المعلم")
UNWANTED_WORDS = ("أسطى", "اسطى", "أسطي", "اسطي", "أوسطى", "اوسطى", "ورشة", "الورشة", "ورش ", "صنايعي")
TITLE_BEFORE_A_NAME = re.compile(r"(الحاج|المعلم)\s+[\u0621-\u064A]")  # «الحاج سيد», never «الحاجة» or «عند الحاجة»
ALLOWED_FRAGMENTS = ("أسطوان", "ورشح")  # a gas cylinder, a runny nose
SKIP_DIRS = {"docs", "staticfiles", "node_modules", "migrations", "scratch", "venv"}


def _sources():
    base = Path(settings.BASE_DIR)
    for path in sorted(base.rglob("*")):
        if path.suffix not in (".py", ".html", ".js") or not path.is_file():
            continue
        parts = path.relative_to(base).parts
        if any(part.startswith(".") or part in SKIP_DIRS for part in parts) or parts[-1].startswith("tests"):
            continue
        yield "/".join(parts), path.read_text(encoding="utf-8", errors="ignore")


class PremiumToneTests(SimpleTestCase):
    def test_demo_owners_carry_a_professional_title(self):
        for key, name in DEMO_OWNERS.items():
            for word in UNWANTED_TITLES:
                self.assertNotIn(word, name, key)
        self.assertEqual(owner_name("services", "maintenance"), "م. محمد رمضان")
        self.assertEqual(owner_name("manufacturing", "food"), "م. سيد عبد الفتاح")
        self.assertEqual(owner_name("commercial", "grocery"), "أ. سعيد عبد العال")

    def test_no_screen_message_or_demo_text_uses_the_words(self):
        found = []
        for name, text in _sources():
            for number, line in enumerate(text.splitlines(), 1):
                clean = line
                for fragment in ALLOWED_FRAGMENTS:
                    clean = clean.replace(fragment, "")
                for word in UNWANTED_WORDS:
                    if word in clean:
                        found.append(f"{name}:{number}: {word}")
                if name in ("config/demo_templates.py", "reports/demo_catalogs.py", "config/demo.py") and TITLE_BEFORE_A_NAME.search(clean):
                    found.append(f"{name}:{number}: title")
        self.assertEqual(found, [])
