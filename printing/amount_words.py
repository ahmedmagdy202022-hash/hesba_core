"""Amounts in words (تفقيط) for printed invoices and receipts.

Written in the plain style Egyptian and Gulf receipts use: "فقط ألف ومائتان
وخمسون جنيه مصري وخمسون قرش لا غير". Scale nouns agree with their count
(ألف / ألفان / آلاف), the rest stays in the invariant receipt form.
"""

from decimal import ROUND_HALF_UP, Decimal


#: code -> (major ar, minor ar, major en, minor en, minor digits)
CURRENCY_WORDS = {
    "EGP": ("جنيه مصري", "قرش", "Egyptian pounds", "piasters", 2),
    "SAR": ("ريال سعودي", "هللة", "Saudi riyals", "halalas", 2),
    "AED": ("درهم إماراتي", "فلس", "UAE dirhams", "fils", 2),
    "KWD": ("دينار كويتي", "فلس", "Kuwaiti dinars", "fils", 3),
    "QAR": ("ريال قطري", "درهم", "Qatari riyals", "dirhams", 2),
    "BHD": ("دينار بحريني", "فلس", "Bahraini dinars", "fils", 3),
    "OMR": ("ريال عماني", "بيسة", "Omani rials", "baisa", 3),
    "JOD": ("دينار أردني", "فلس", "Jordanian dinars", "fils", 3),
    "USD": ("دولار أمريكي", "سنت", "US dollars", "cents", 2),
    "EUR": ("يورو", "سنت", "euros", "cents", 2),
}

_AR_UNITS = ("", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة")
_AR_TEENS = ("عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر")
_AR_TENS = ("", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون")
_AR_HUNDREDS = ("", "مائة", "مائتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة")
#: (one, two, plural 3-10, singular 11+)
_AR_SCALES = (
    None,
    ("ألف", "ألفان", "آلاف", "ألف"),
    ("مليون", "مليونان", "ملايين", "مليون"),
    ("مليار", "ملياران", "مليارات", "مليار"),
)

_EN_ONES = ("", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_EN_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_EN_SCALES = ("", "thousand", "million", "billion")


def _ar_below_thousand(n):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(_AR_HUNDREDS[hundreds])
    if rest:
        if rest < 10:
            parts.append(_AR_UNITS[rest])
        elif rest < 20:
            parts.append(_AR_TEENS[rest - 10])
        else:
            tens, units = divmod(rest, 10)
            parts.append(f"{_AR_UNITS[units]} و{_AR_TENS[tens]}" if units else _AR_TENS[tens])
    return " و".join(parts)


def arabic_number(n):
    """Whole number in Arabic words. 0 -> "صفر"."""

    n = int(n)
    if n == 0:
        return "صفر"
    if n < 0:
        return "سالب " + arabic_number(-n)
    groups = []
    scale = 0
    while n:
        n, group = divmod(n, 1000)
        if group:
            if scale == 0:
                groups.append(_ar_below_thousand(group))
            else:
                one, two, plural, singular = _AR_SCALES[scale]
                if group == 1:
                    groups.append(one)
                elif group == 2:
                    groups.append(two)
                elif group <= 10:
                    groups.append(f"{_ar_below_thousand(group)} {plural}")
                else:
                    groups.append(f"{_ar_below_thousand(group)} {singular}")
        scale += 1
        if scale >= len(_AR_SCALES) and n:
            raise ValueError("Amount too large to write in words.")
    return " و".join(reversed(groups))


def _en_below_thousand(n):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_EN_ONES[hundreds]} hundred")
    if rest:
        if rest < 20:
            parts.append(_EN_ONES[rest])
        else:
            tens, units = divmod(rest, 10)
            parts.append(f"{_EN_TENS[tens]}-{_EN_ONES[units]}" if units else _EN_TENS[tens])
    return " ".join(parts)


def english_number(n):
    n = int(n)
    if n == 0:
        return "zero"
    if n < 0:
        return "minus " + english_number(-n)
    groups = []
    scale = 0
    while n:
        n, group = divmod(n, 1000)
        if group:
            words = _en_below_thousand(group)
            groups.append(f"{words} {_EN_SCALES[scale]}".strip())
        scale += 1
        if scale >= len(_EN_SCALES) and n:
            raise ValueError("Amount too large to write in words.")
    return " ".join(reversed(groups))


def amount_in_words(amount, currency="EGP", lang="ar"):
    """The full receipt phrase for ``amount`` in ``currency``."""

    code = (currency or "EGP").upper()
    major_ar, minor_ar, major_en, minor_en, digits = CURRENCY_WORDS.get(code, (code, "", code, "", 2))
    value = Decimal(amount or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = value < 0
    value = abs(value)
    whole = int(value)
    minor = int(((value - whole) * (10 ** digits)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    if lang == "en":
        text = f"{english_number(whole)} {major_en}"
        if minor and minor_en:
            text += f" and {english_number(minor)} {minor_en}"
        text = ("minus " if negative else "") + text
        return f"{text[0].upper()}{text[1:]} only"

    text = f"{arabic_number(whole)} {major_ar}"
    if minor and minor_ar:
        text += f" و{arabic_number(minor)} {minor_ar}"
    return f"فقط {'سالب ' if negative else ''}{text} لا غير"
