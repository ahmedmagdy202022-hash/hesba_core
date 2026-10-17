"""AN5 (Ahmed: "dropdown فيه المقاسات والألوان القياسية + امكانية اضافة اي حاجة"): the sizes and colours
a shop ticks instead of typing, plus its own.

The picker posts ``<kind>_std`` (the ticked standard values) and
``<kind>_extra`` (anything else, comma separated); ``picked`` joins them in
order without repeats. The item form and the variants screen share it.
"""

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
