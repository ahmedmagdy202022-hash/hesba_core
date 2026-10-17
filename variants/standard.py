"""AN5 (Ahmed: "dropdown فيه المقاسات والألوان القياسية + امكانية اضافة اي حاجة"): the sizes and colours
a shop ticks instead of typing, plus its own.

The picker posts ``<kind>_std`` (the ticked standard values) and
``<kind>_extra`` (anything else, comma separated); ``picked`` joins them in
order without repeats. The item form and the variants screen share it.
"""

from django import forms
from django.utils.html import format_html, format_html_join

from .services import parse_values


SIZES = ("XS", "S", "M", "L", "XL", "XXL", "3XL")
NUMBER_SIZES = ("36", "38", "40", "42", "44", "46")
COLORS = {
    "ar": ("أسود", "أبيض", "كحلي", "رمادي", "بيج", "بني", "أحمر", "أزرق", "أخضر", "وردي"),
    "en": ("Black", "White", "Navy", "Grey", "Beige", "Brown", "Red", "Blue", "Green", "Pink"),
}


def size_choices():
    return [(value, value) for value in SIZES + NUMBER_SIZES]


def color_choices(lang="ar"):
    return [(value, value) for value in COLORS["en" if lang == "en" else "ar"]]


def picked(data, kind):
    """The values ticked under ``<kind>_std`` and typed in ``<kind>_extra``, in that order, each once."""

    ticked = data.getlist(f"{kind}_std") if hasattr(data, "getlist") else list(data.get(f"{kind}_std") or [])
    return parse_values(",".join([*ticked, data.get(f"{kind}_extra") or ""]))


#: AN7 (F32): how a size is often typed in Arabic, read as the standard size, so "لارج" on one item and
#: "L" on another stay one size in the reports.
SPOKEN_SIZES = {
    "اكس سمول": "XS", "إكس سمول": "XS", "سمول": "S", "صغير": "S", "ميديم": "M", "ميديوم": "M", "وسط": "M",
    "لارج": "L", "كبير": "L", "اكس لارج": "XL", "إكس لارج": "XL", "دبل اكس لارج": "XXL", "دبل إكس لارج": "XXL",
    "تريبل اكس لارج": "3XL", "تريبل إكس لارج": "3XL", "xxxl": "3XL",
}


def standard_size(value):
    """A typed size as the standard one when it is one (case and the Arabic spelling aside); else as typed."""

    text = " ".join(str(value or "").split())
    if not text:
        return ""
    key = text.casefold()
    for size in SIZES:
        if key == size.casefold():
            return size
    return SPOKEN_SIZES.get(key, text)


def standard_color(value, lang="ar"):
    text = " ".join(str(value or "").split())
    for color in COLORS["ar"] + COLORS["en"]:
        if text.casefold() == color.casefold():
            return color
    return text


class SuggestInput(forms.TextInput):
    """A text box that offers the standard values as you type and still takes anything else (F32)."""

    def __init__(self, name, options, attrs=None):
        super().__init__(attrs=attrs)
        self.list_id, self.options = f"hs-std-{name}", tuple(options)

    def render(self, name, value, attrs=None, renderer=None):
        html = super().render(name, value, {**(attrs or {}), "list": self.list_id, "autocomplete": "off"}, renderer)
        return html + format_html('<datalist id="{}">{}</datalist>', self.list_id,
                                  format_html_join("", '<option value="{}"></option>', ((option,) for option in self.options)))
