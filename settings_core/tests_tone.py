"""Guard: five words never reach a screen, a message or a printout (Ahmed's standing tone rule).

The product speaks simple Egyptian Arabic at a premium level, so these words, with their forms, never appear in Arabic text a person
sees: «ورشة» (a repair shop is a service centre), «الأسطى», «بتاع» (and بتاعك، بتاعة، بتوع…), «دوس» (and تدوس، دوست، بدوسة…), «لينك»
(and اللينك، لينكات، بلينك…) and «شغلانة». Use «اضغط»/«بضغطة», «رابط»/«روابط», a possessive or «الخاص بـ», and the plain noun instead.

The scan reads every ``*.py``, ``*.html`` and ``*.js`` file of the repository except tests, docs, migrations, ``staticfiles/``,
``node_modules/`` and this file. A word is matched whole (with its Arabic prefixes and suffixes), never as a fragment of another word.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: The only places a banned-looking text is allowed. Each entry is removed from the line before it is scanned.
ALLOWED_TEXT = (
    "بولي كلينك",  # «polyclinic»: the Latin «clinic» spelled in Arabic letters, whose tail looks like «لينك»
    "برد ورشح",  # a drug name («أقراص برد ورشح», cold and runny nose), not «ورشة»
)

SKIP_DIRS = {"docs", "staticfiles", "node_modules", "migrations", "scratch", "venv", "__pycache__"}
THIS_FILE = "settings_core/tests_tone.py"

_TASHKEEL = re.compile("[ً-ْـ]")  # vowel marks and the tatweel never change which word it is
_ARABIC_WORD = re.compile("[ء-يً-ْـ]+")
_PREFIX = r"(?:[وف]?(?:ال|بال|كال|لل|[بلك])?)"  # و/ف, then ال, بال, كال, لل or a one-letter preposition
_SUFFIX = r"(?:ي|ك|ه|ها|هم|هن|نا|كم|ني|ات)?"  # my, your, his, her, their, our, plural

BANNED = {
    "بتاع": re.compile(rf"^{_PREFIX}بتا(?:ع|عت|عة){_SUFFIX}$|^{_PREFIX}بتوع{_SUFFIX}$"),
    "دوس": re.compile(rf"^{_PREFIX}[هحب]?[تينأا]?دوس(?:ة|ت|وا|تين|ين|ون)?{_SUFFIX}$"),
    "لينك": re.compile(rf"^{_PREFIX}لينك{_SUFFIX}$"),
    "ورشة": re.compile(rf"^{_PREFIX}ورش(?:ة|ه|ات|ت)?{_SUFFIX}$"),
    "الأسطى": re.compile(rf"^{_PREFIX}(?:أ|ا|أو|او)سط[ىي]{_SUFFIX}$"),
    "شغلانة": re.compile(rf"^{_PREFIX}شغلان(?:ة|ه|ات|ت){_SUFFIX}$"),
}


def banned_words_in(line):
    """The banned words a line of source carries, as ``(word, form)`` pairs."""
    for allowed in ALLOWED_TEXT:
        line = line.replace(allowed, " ")
    found = []
    for token in _ARABIC_WORD.findall(line):
        form = _TASHKEEL.sub("", token)
        found.extend((word, form) for word, pattern in BANNED.items() if pattern.match(form))
    return found


def _sources():
    base = Path(settings.BASE_DIR)
    for path in sorted(base.rglob("*")):
        if path.suffix not in (".py", ".html", ".js") or not path.is_file():
            continue
        parts = path.relative_to(base).parts
        name = "/".join(parts)
        if name == THIS_FILE or any(part.startswith(".") or part in SKIP_DIRS for part in parts):
            continue
        if parts[-1].startswith(("tests", "test_")) or "tests" in parts[:-1]:
            continue
        yield name, path.read_text(encoding="utf-8", errors="ignore")


class BannedWordsGuardTests(SimpleTestCase):
    def test_no_screen_message_or_printout_uses_a_banned_word(self):
        # The matcher itself: every family is caught with its forms, and nothing near it is.
        for text, word in (
            ("دوس على الفاتورة", "دوس"), ("لما تدوس حفظ", "دوس"), ("دوست", "دوس"), ("هتدوس", "دوس"), ("هيدوس", "دوس"), ("بدوسة واحدة", "دوس"), ("دوسة", "دوس"),
            ("بتاع الفاتورة", "بتاع"), ("بتاعك", "بتاع"), ("بتاعته", "بتاع"), ("بتوع الشركة", "بتاع"), ("والبتاعة", "بتاع"),
            ("لينك الدفع", "لينك"), ("اللينك", "لينك"), ("لينكات", "لينك"), ("بلينك واحد", "لينك"),
            ("الورشة دلوقتي", "ورشة"), ("ورشه", "ورشة"), ("الأسطى", "الأسطى"), ("اسطى", "الأسطى"), ("شغلانة", "شغلانة"),
        ):
            self.assertEqual([found for found, _ in banned_words_in(text)], [word], text)
        for text in ("اضغط حفظ", "رابط الدفع", "بولي كلينك", "أقراص برد ورشح", "أسطوانة غاز", "دوسيه", "بدائل", "ورشح"):
            self.assertEqual(banned_words_in(text), [], text)

        found = []
        for name, text in _sources():
            for number, line in enumerate(text.splitlines(), 1):
                found.extend(f"{name}:{number}: {word} ({form})" for word, form in banned_words_in(line))
        self.assertEqual(found, [], "banned words in user-visible text:\n" + "\n".join(found))
