"""The product speaks like a premium product (Ahmed, 12 Nov 2026).

«بلاش كلمة الأسطى»، «بلاش ورشة وبلاش أي حاجة عموماً تدي انطباع متدني للبرنامج»: a repair shop is a service centre, a demo
owner is introduced with a professional title, and neither word comes back in a screen, a message or the demo data.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from config import demo, demo_first_run
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


# ---- the demo data (premium-tone wording, part 3): the first thing a prospect sees ----------------------------------

#: Every file that seeds or words the demo. The checks below read only these, so they never flag a product screen.
DEMO_SOURCES = (
    "reports/demo_catalogs.py", "config/demo.py", "config/demo_templates.py", "config/demo_first_run.py", "config/demo_sandbox.py",
    "config/demo_restart.py", "settings_core/demo_services.py", "settings_core/management/commands/prepare_demo.py",
    "reports/management/commands/seed_demo_business.py", "medical/demo.py",
)
LETTER = "\u0621-\u064A"
#: A person named after the trade after the first name («أحمد الفني», «سمير المندوب», «د. كريم البيطري», «م. سامح مهندس الموقع»).
TRADE_WORDS = (
    "الفني", "فني الصيانة", "الصيدلانية", "الكوافيرة", "المحاسب", "المصمم", "الويتر", "الباريستا", "الكاشير", "الحلواني", "الطيار",
    "المعملي", "المدرس", "المدرب", "مهندس الموقع", "النجار", "الطباع", "الكيميائي", "الحداد", "البيطري", "المندوب", "المندوبة",
)
NAMED_AFTER_TRADE = re.compile(r"[\"']\s*(?:(?:أ|م|د)\.\s*)?(?!(?:من|في|على|مع|عن|إلى|ال)\s)[" + LETTER + r"]+\s+(?:" + "|".join(TRADE_WORDS) + r")(?![" + LETTER + r"])")
#: «Ms. سارة», «مس هبة», «ميس دينا», «مدام نهى», «أستاذ كريم»: a courtesy word in front of a name, instead of «أ.» or «د.».
COURTESY_BEFORE_A_NAME = re.compile(r"[\"']\s*(?:Ms|Mrs|Mr|Miss)\.?\s+\S|[\"']\s*(?:مس|ميس|مدام|أستاذ|استاذ|الأستاذ|الاستاذ)\s+[" + LETTER + r"]")
#: What the demo items, notes and tour used to say in the street's spelling. Each is whole-word (a prefix such as «ال» or «و» is fine).
SLANG = (
    "زبالة", "كهربا", "كوبايات", "كوباية", "شيبسي", "اسكرينة", "نص فرخة", "لحمة", "فراخ طازة", "زراير", "فاضية", "سخن", "ساقع",
    "تلج", "باظت", "الغدا", "مجتش", "مردش", "تنضيف", "ويك إند", "صايم", "الشهر التاني", "الشهر التالت", "التلات", "الأربع",
    "الاتنين", "كويسة", "الباكدج", "باكدجات", "بيعة", "أوردر", "أوردرات", "الويترز", "الأكتر", "وانت", "محل", "المحل", "شرا",
    "تالتة", "بيتصرف", "اتخلعت", "لغى", "لغت", "ببلاش", "دوس", "حط ",
)
SLANG_FRAGMENTS = tuple(re.compile(r"(?<![" + LETTER + r"])(?:ال|و|ب|ل)?" + re.escape(word) + r"(?![" + LETTER + r"])") for word in SLANG)
SPELLINGS_OF_THE_BRAND = ("حِسْبَة", "حِسْبه")
PLAIN_BRAND = re.compile(r"(?<![" + LETTER + r"])حسبة")


def _demo_lines():
    base = Path(settings.BASE_DIR)
    for relative in DEMO_SOURCES:
        text = (base / relative).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if not line.lstrip().startswith("#"):  # a comment may say what the old word was
                yield relative, number, line


class DemoDataToneTests(SimpleTestCase):
    def test_every_demo_source_exists(self):
        for relative in DEMO_SOURCES:
            self.assertTrue((Path(settings.BASE_DIR) / relative).is_file(), relative)

    def test_nobody_in_the_demo_is_named_after_a_trade(self):
        found = [f"{name}:{number}: {match.group(0)}" for name, number, line in _demo_lines() for match in NAMED_AFTER_TRADE.finditer(line)]
        self.assertEqual(found, [])

    def test_nobody_in_the_demo_is_named_with_ms_mis_madam_or_ustaz(self):
        found = [f"{name}:{number}: {match.group(0)}" for name, number, line in _demo_lines() for match in COURTESY_BEFORE_A_NAME.finditer(line)]
        self.assertEqual(found, [])
        for key, owner in DEMO_OWNERS.items():
            self.assertIsNone(COURTESY_BEFORE_A_NAME.search(f'"{owner}"'), key)

    def test_the_demo_businesses_are_not_called_a_shop(self):
        names = {"DEMO_NAME": demo.DEMO_NAME, **{str(key): value for key, value in demo.DEMO_NAMES.items()}}
        for key, name in names.items():
            self.assertNotIn("محل", name, key)
        base = Path(settings.BASE_DIR)
        for relative in ("settings_core/management/commands/prepare_demo.py", "config/demo_restart.py"):
            # the first-run rename (config.demo._name_the_demo_shop) only fires for exactly this name
            self.assertIn(f'display_name="{demo.DEMO_NAME}"', (base / relative).read_text(encoding="utf-8"), relative)

    def test_the_demo_items_notes_and_tour_use_standard_words(self):
        found = []
        for name, number, line in _demo_lines():
            for pattern, word in zip(SLANG_FRAGMENTS, SLANG):
                if pattern.search(line):
                    found.append(f"{name}:{number}: {word}")
        self.assertEqual(found, [])

    def test_the_tour_and_the_landing_say_it_plainly(self):
        texts = [text for tile in demo.DEMO_TILES for text in tile[1:2]] + [hint[0] for hint in demo.DEMO_HINTS.values()]
        texts += [text for steps in demo.DEMO_TOURS.values() for step in steps for text in (step[0], step[2])]
        texts += list(demo_first_run.WORDS["ar"].values()) + list(demo_first_run.BEGIN_WORDS["ar"].values())
        texts += [text for step in demo_first_run.BEGIN_STEPS for text in (step[1], step[3])]
        self.assertGreater(len(texts), 80)
        for text in texts:
            for pattern, word in zip(SLANG_FRAGMENTS, SLANG):
                self.assertIsNone(pattern.search(text), f"{word}: {text}")
        self.assertIn("سجّل عملية بيع من الكاشير", [step[0] for step in demo.demo_tour("commercial", "retail")])
        self.assertIn("افتح طاولة وسجّل طلب", [step[0] for step in demo.demo_tour("restaurants", "restaurant")])

    def test_the_brand_is_spelled_one_way_in_the_demo(self):
        found = []
        for name, number, line in _demo_lines():
            if any(spelling in line for spelling in SPELLINGS_OF_THE_BRAND) or PLAIN_BRAND.search(line):
                found.append(f"{name}:{number}")
        self.assertEqual(found, [])
        self.assertIn("حِسبة", demo_first_run.WORDS["ar"]["page_title"])
        self.assertTrue(demo_first_run.WORDS["ar"]["page_title"].endswith("- حِسبة"))
        self.assertTrue(demo_first_run.BEGIN_WORDS["ar"]["page_title"].endswith("- حِسبة"))
