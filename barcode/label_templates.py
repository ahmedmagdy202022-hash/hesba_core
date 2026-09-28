"""LABEL-002: label sizes people actually buy, and what goes on each label.

A4 sheets are laid out as a grid starting at the sheet's top margin; rolls
print one label per page at the roll's exact size. ``offset`` nudges the
whole print by a few millimetres for printers that feed slightly off.
"""

TEMPLATES = {
    # key: (kind, label width mm, label height mm, columns, rows, sheet top margin mm, arabic, english)
    "a4_24": ("a4", 70, 37, 3, 8, 0.5, "ورقة A4 — ‏24 ملصق (70×37)", "A4 sheet — 24 labels (70×37)"),
    "a4_21": ("a4", 70, 42.3, 3, 7, 0.4, "ورقة A4 — ‏21 ملصق (70×42)", "A4 sheet — 21 labels (70×42)"),
    "a4_40": ("a4", 52.5, 29.7, 4, 10, 0, "ورقة A4 — ‏40 ملصق (52×30)", "A4 sheet — 40 labels (52×30)"),
    "roll_50x25": ("roll", 50, 25, 1, 1, 0, "رول 50×25 مم", "Roll 50×25 mm"),
    "roll_50x30": ("roll", 50, 30, 1, 1, 0, "رول 50×30 مم", "Roll 50×30 mm"),
    "roll_38x25": ("roll", 38, 25, 1, 1, 0, "رول 38×25 مم", "Roll 38×25 mm"),
    "roll_40x30": ("roll", 40, 30, 1, 1, 0, "رول 40×30 مم", "Roll 40×30 mm"),
}
LEGACY = {"a4": "a4_24", "roll": "roll_50x25"}
DEFAULT = "a4_24"
FIELDS = ("name", "price", "code", "shop")
MAX_OFFSET_MM = 5


def resolve(key):
    key = LEGACY.get(key, key)
    return key if key in TEMPLATES else DEFAULT


def spec(key):
    kind, width, height, columns, rows, top, ar, en = TEMPLATES[resolve(key)]
    small = height <= 25 or width <= 40
    return {"key": resolve(key), "kind": kind, "width": width, "height": height, "columns": columns, "rows": rows, "top": top,
            "barcode_width": round(width - (4 if kind == "roll" else 12), 1), "small": small}


def choices(lang):
    return [(key, value[7] if lang == "en" else value[6]) for key, value in TEMPLATES.items()]


def offset(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(-MAX_OFFSET_MM, min(MAX_OFFSET_MM, round(value, 1)))
