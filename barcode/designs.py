"""LABEL-003: label designs the shop makes itself.

The built-in sizes (label_templates.TEMPLATES) cover the common rolls and A4
sheets. A design adds any size: the label's width and height, and for an A4
sheet how many across and down, the margins and the gaps between labels, plus
the text sizes and which lines are on by default.

Designs live in one SystemSetting row as JSON, so adding one never needs a
migration. Keys are "d<id>" so they never collide with a built-in key.
"""

import json
from decimal import Decimal, InvalidOperation

from audit.models import AuditEventType, AuditLog

A4_W, A4_H = 210, 297
KEY = "labels.designs"

#: name: (min, max, default, decimals)
NUMBERS = {
    "width": (15, 200, 50, 1),
    "height": (10, 150, 25, 1),
    "columns": (1, 8, 3, 0),
    "rows": (1, 30, 8, 0),
    "top": (0, 40, 5, 1),
    "side": (0, 40, 5, 1),
    "gap_x": (0, 15, 2, 1),
    "gap_y": (0, 15, 0, 1),
    "name_pt": (5, 20, 9, 1),
    "price_pt": (5, 28, 11, 1),
    "small_pt": (4, 12, 7, 1),
    "barcode_pct": (25, 80, 50, 0),
}
FLAGS = ("show_name", "show_price", "show_code", "show_shop", "barcode_text")
DEFAULT_FLAGS = {"show_name": True, "show_price": True, "show_code": False, "show_shop": False, "barcode_text": True}

ERRORS = {
    "name": {"ar": "اكتب اسم للتصميم.", "en": "Give the design a name."},
    "number": {"ar": "«{field}» لازم يكون رقم بين {low} و{high}.", "en": "“{field}” must be a number from {low} to {high}."},
    "too_wide": {"ar": "الملصقات أعرض من ورقة A4 ({used} مم من 210). قلّل العرض أو العدد أو الهوامش.", "en": "The labels are wider than an A4 sheet ({used} mm of 210). Reduce the width, the count or the margins."},
    "too_tall": {"ar": "الملصقات أطول من ورقة A4 ({used} مم من 297). قلّل الطول أو عدد الصفوف أو الهوامش.", "en": "The labels are taller than an A4 sheet ({used} mm of 297). Reduce the height, the rows or the margins."},
}
FIELD_WORDS = {
    "width": {"ar": "عرض الملصق", "en": "Label width"}, "height": {"ar": "طول الملصق", "en": "Label height"},
    "columns": {"ar": "عدد الأعمدة", "en": "Columns"}, "rows": {"ar": "عدد الصفوف", "en": "Rows"},
    "top": {"ar": "الهامش العلوي", "en": "Top margin"}, "side": {"ar": "الهامش الجانبي", "en": "Side margin"},
    "gap_x": {"ar": "المسافة بين الأعمدة", "en": "Gap between columns"}, "gap_y": {"ar": "المسافة بين الصفوف", "en": "Gap between rows"},
    "name_pt": {"ar": "حجم اسم الصنف", "en": "Name size"}, "price_pt": {"ar": "حجم السعر", "en": "Price size"},
    "small_pt": {"ar": "حجم السطور الصغيرة", "en": "Small lines size"}, "barcode_pct": {"ar": "طول الباركود من الملصق", "en": "Barcode share of the label"},
}


def _setting():
    from settings_core.models import SystemSetting

    return SystemSetting.objects.filter(key=KEY).first()


def all_designs():
    row = _setting()
    if row is None or not row.value:
        return []
    try:
        data = json.loads(row.value)
    except ValueError:
        return []
    return [design for design in data if isinstance(design, dict) and "id" in design]


def get(design_id):
    for design in all_designs():
        if str(design["id"]) == str(design_id):
            return design
    return None


def _store(designs):
    from settings_core.models import SystemSetting

    SystemSetting.objects.update_or_create(key=KEY, defaults={
        "value": json.dumps(designs, ensure_ascii=False), "data_type": SystemSetting.DataType.JSON,
        "description": "Barcode label designs (LABEL-003)", "active": True,
    })


def _number(raw, low, high, decimals):
    try:
        value = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value < low or value > high:
        return None
    return int(value) if decimals == 0 else float(round(value, decimals))


def clean(data, lang="ar"):
    """(design, errors) from posted form data. ``design`` has no id yet."""

    errors = []
    name = (data.get("name") or "").strip()[:60]
    if not name:
        errors.append(ERRORS["name"][lang])
    kind = "a4" if data.get("kind") == "a4" else "roll"
    design = {"name": name, "kind": kind}
    for field, (low, high, default, decimals) in NUMBERS.items():
        raw = data.get(field)
        if raw in (None, ""):
            design[field] = default
            continue
        value = _number(raw, low, high, decimals)
        if value is None:
            errors.append(ERRORS["number"][lang].format(field=FIELD_WORDS[field][lang], low=low, high=high))
            value = default
        design[field] = value
    for flag in FLAGS:
        design[flag] = data.get(flag) in ("1", "on", "true", True)
    if kind == "roll":
        design.update(columns=1, rows=1, top=0, side=0, gap_x=0, gap_y=0)
    else:
        used_w = design["columns"] * design["width"] + 2 * design["side"] + (design["columns"] - 1) * design["gap_x"]
        used_h = design["rows"] * design["height"] + design["top"] + (design["rows"] - 1) * design["gap_y"]
        if used_w > A4_W:
            errors.append(ERRORS["too_wide"][lang].format(used=round(used_w, 1)))
        if used_h > A4_H:
            errors.append(ERRORS["too_tall"][lang].format(used=round(used_h, 1)))
    return design, errors


def save(design, user, design_id=None):
    designs = all_designs()
    if design_id is None:
        design["id"] = max((int(d["id"]) for d in designs), default=0) + 1
        designs.append(design)
        action = "create_label_design"
    else:
        design["id"] = int(design_id)
        designs = [design if str(d["id"]) == str(design_id) else d for d in designs]
        action = "update_label_design"
    _store(designs)
    AuditLog.objects.create(event_type=AuditEventType.CREATE if design_id is None else AuditEventType.UPDATE, actor=user, module="barcode",
                            action=action, object_type="barcode.LabelDesign", object_id=str(design["id"]), before_data={}, after_data=design)
    return design


def delete(design_id, user):
    designs = all_designs()
    kept = [d for d in designs if str(d["id"]) != str(design_id)]
    if len(kept) == len(designs):
        return False
    _store(kept)
    AuditLog.objects.create(event_type=AuditEventType.DELETE, actor=user, module="barcode", action="delete_label_design",
                            object_type="barcode.LabelDesign", object_id=str(design_id), before_data={}, after_data={})
    return True


def blank():
    design = {"name": "", "kind": "roll"}
    design.update({field: default for field, (_, _, default, _) in NUMBERS.items()})
    design.update(DEFAULT_FLAGS)
    design.update(columns=1, rows=1)
    return design


def summary(design, lang):
    size = f"{design['width']:g}×{design['height']:g}"
    if design["kind"] == "a4":
        count = design["columns"] * design["rows"]
        return f"A4 · {count} {'labels' if lang == 'en' else 'ملصق'} ({size})"
    return f"{'Roll' if lang == 'en' else 'رول'} {size} {'mm' if lang == 'en' else 'مم'}"
