"""Text folding shared by the AI features (A2's mock routing, A3's deterministic matching). No model involved.

``fold`` makes two spellings of the same Arabic or English words compare equal: lower case (casefold), compatibility
forms unified (NFKC), hamza forms of alef folded to ا, ى to ي and ة to ه, harakat and tatweel dropped, Arabic-Indic
digits read as ASCII digits, and spaces collapsed. It never changes which letters or digits a word has otherwise, so
«تيل فرامل أمامي K-22» and «تيل فرامل أمامي» stay different.
"""

import re
import unicodedata

_DIACRITICS = re.compile("[ً-ْـ]")  # harakat and tatweel
_LETTERS = str.maketrans("أإآىة٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "ااايه01234567890123456789")


def fold(text):
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = _DIACRITICS.sub("", text)
    return " ".join(text.translate(_LETTERS).split())
