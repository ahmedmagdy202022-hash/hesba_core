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


def _design(key):
    """LABEL-003: a shop-made design, keyed "d<id>"."""

    if isinstance(key, str) and key.startswith("d") and key[1:].isdigit():
        from .designs import get

        return get(key[1:])
    return None


def resolve(key):
    if _design(key) is not None:
        return key
    key = LEGACY.get(key, key)
    return key if key in TEMPLATES else DEFAULT


def spec(key):
    design = _design(key)
    if design is not None:
        width, height = design["width"], design["height"]
        return {
            "key": key, "kind": design["kind"], "width": width, "height": height, "columns": design["columns"], "rows": design["rows"],
            "top": design["top"], "side": design["side"], "gap_x": design["gap_x"], "gap_y": design["gap_y"],
            "barcode_width": round(max(width - 4, 8), 1), "small": False, "custom": True, "name": design["name"],
            "name_pt": design["name_pt"], "price_pt": design["price_pt"], "small_pt": design["small_pt"],
            "barcode_pct": design["barcode_pct"], "barcode_text": design["barcode_text"],
            "defaults": {flag: design[flag] for flag in ("show_name", "show_price", "show_code", "show_shop")},
        }
    kind, width, height, columns, rows, top, ar, en = TEMPLATES[resolve(key)]
    small = height <= 25 or width <= 40
    return {"key": resolve(key), "kind": kind, "width": width, "height": height, "columns": columns, "rows": rows, "top": top,
            "side": None, "gap_x": 0, "gap_y": 0, "barcode_width": round(width - (4 if kind == "roll" else 12), 1), "small": small, "custom": False,
            # Text sizes the PDF uses for built-ins, matching labels.css.
            "name_pt": 6.5 if small else (7 if kind == "roll" else 10), "price_pt": 7.5 if small else (8 if kind == "roll" else 11),
            "small_pt": 7, "barcode_pct": 0, "barcode_text": True,
            "defaults": {"show_name": True, "show_price": True, "show_code": False, "show_shop": False}}


def choices(lang):
    from .designs import all_designs, summary

    built_in = [(key, value[7] if lang == "en" else value[6]) for key, value in TEMPLATES.items()]
    return built_in + [(f"d{design['id']}", f"★ {design['name']} — {summary(design, lang)}") for design in all_designs()]


def offset(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(-MAX_OFFSET_MM, min(MAX_OFFSET_MM, round(value, 1)))
