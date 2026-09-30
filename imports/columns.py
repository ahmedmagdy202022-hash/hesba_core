"""IMPORT-002: understand the columns of a file exported from another program.

Old accounting programs export Excel with their own headers, usually Arabic
("كود الصنف", "سعر البيع", "الرصيد"). This module knows the fields each
import type takes, the names shops and programs commonly give them, and how
to clean the values that come with them (Arabic-Indic digits, thousands
separators, "نعم/لا"). The screen suggests a mapping from it; the user
confirms or corrects it before any row is checked.
"""

import re


#: Fields per import type: (key, Arabic label, English label, required).
FIELDS = {
    "categories": [("category_code", "كود التصنيف", "Category code", True), ("name_ar", "اسم التصنيف", "Category name", True),
                   ("name_en", "الاسم بالإنجليزي", "English name", False), ("parent_code", "كود التصنيف الأب", "Parent code", False),
                   ("active", "نشط", "Active", False)],
    "locations": [("location_code", "كود المخزن", "Location code", True), ("name_ar", "اسم المخزن", "Location name", True),
                  ("name_en", "الاسم بالإنجليزي", "English name", False), ("description", "الوصف", "Description", False),
                  ("is_default", "افتراضي", "Default", False), ("is_receiving_location", "بيستلم مشتريات", "Receives purchases", False),
                  ("is_selling_location", "بيبيع منه", "Sells from", False), ("active", "نشط", "Active", False)],
    "items": [("item_code", "كود الصنف", "Item code", True), ("barcode", "الباركود", "Barcode", False), ("item_name", "اسم الصنف", "Item name", True),
              ("category_code", "كود التصنيف", "Category code", False), ("size", "المقاس", "Size", False), ("color", "اللون", "Colour", False),
              ("unit", "الوحدة", "Unit", False), ("default_sale_price", "سعر البيع", "Sale price", False),
              ("default_purchase_price", "سعر الشراء", "Purchase price", False), ("average_cost", "متوسط التكلفة", "Average cost", False),
              ("min_stock", "حد الطلب", "Reorder level", False), ("is_stock_tracked", "له مخزون", "Stock tracked", False), ("active", "نشط", "Active", False)],
    "customers": [("customer_code", "كود العميل", "Customer code", True), ("name", "اسم العميل", "Customer name", True), ("phone", "التليفون", "Phone", False),
                  ("whatsapp", "واتساب", "WhatsApp", False), ("email", "الإيميل", "Email", False), ("address", "العنوان", "Address", False),
                  ("opening_balance", "الرصيد الافتتاحي (عليه)", "Opening balance (owes)", False), ("credit_limit", "حد الائتمان", "Credit limit", False),
                  ("notes", "ملاحظات", "Notes", False), ("active", "نشط", "Active", False)],
    "suppliers": [("supplier_code", "كود المورد", "Supplier code", True), ("name", "اسم المورد", "Supplier name", True), ("phone", "التليفون", "Phone", False),
                  ("whatsapp", "واتساب", "WhatsApp", False), ("email", "الإيميل", "Email", False), ("address", "العنوان", "Address", False),
                  ("opening_balance", "الرصيد الافتتاحي (ليه)", "Opening balance (owed)", False), ("notes", "ملاحظات", "Notes", False), ("active", "نشط", "Active", False)],
    "cashboxes": [("cashbox_code", "كود الخزنة", "Cashbox code", True), ("name_ar", "اسم الخزنة", "Cashbox name", True), ("name_en", "الاسم بالإنجليزي", "English name", False),
                  ("opening_balance", "الرصيد الافتتاحي", "Opening balance", False), ("currency", "العملة", "Currency", False),
                  ("is_default", "افتراضية", "Default", False), ("notes", "ملاحظات", "Notes", False), ("active", "نشطة", "Active", False)],
    "stock": [("item_code", "كود الصنف", "Item code", True), ("location_code", "كود المخزن", "Location code", True), ("movement_date", "التاريخ", "Date", False),
              ("quantity", "الكمية", "Quantity", True), ("unit_cost", "تكلفة الوحدة", "Unit cost", False), ("notes", "ملاحظات", "Notes", False)],
    "opening_balances": [("entity_type", "النوع (customer / supplier / cashbox)", "Type (customer / supplier / cashbox)", True),
                         ("entity_code", "الكود", "Code", True), ("opening_balance", "الرصيد", "Balance", True)],
}

#: Code fields Hesba can number itself when the old program has no codes.
AUTO_CODE = {"categories": ("category_code", "CAT-"), "locations": ("location_code", "LOC-"), "items": ("item_code", "ITM-"),
             "customers": ("customer_code", "C-"), "suppliers": ("supplier_code", "S-"), "cashboxes": ("cashbox_code", "CB-")}

#: Names programs and people give each field (besides the Hesba key and label).
ALIASES = {
    "category_code": ["كود المجموعه", "رقم المجموعه", "كود القسم", "رقم التصنيف", "كود الفئه", "group code", "category id"],
    "location_code": ["كود المستودع", "رقم المخزن", "رقم المستودع", "كود الفرع", "المخزن", "المستودع", "warehouse code", "store code", "warehouse", "location"],
    "item_code": ["كود", "الكود", "رقم الصنف", "كود المنتج", "رقم المنتج", "كود السلعه", "sku", "code", "item no", "product code", "item id"],
    "barcode": ["باركود", "رقم الباركود", "كود الباركود", "ean", "upc"],
    "item_name": ["اسم المنتج", "الصنف", "اسم السلعه", "المنتج", "البيان", "الوصف", "product", "product name", "item", "description", "name"],
    "name_ar": ["الاسم", "اسم", "الاسم بالعربي", "اسم المجموعه", "اسم القسم", "اسم المستودع", "اسم الفرع", "name"],
    "name_en": ["english name", "name en", "الاسم الانجليزي"],
    "category_code_items": ["التصنيف", "المجموعه", "القسم", "الفئه", "category", "group"],
    "parent_code": ["التصنيف الاب", "المجموعه الرئيسيه", "parent"],
    "size": ["مقاس", "الحجم", "size"],
    "color": ["لون", "colour", "color"],
    "unit": ["وحده", "الوحده", "وحده القياس", "uom", "unit"],
    "default_sale_price": ["سعر", "السعر", "سعر بيع", "سعر البيع للجمهور", "سعر المستهلك", "سعر القطاعي", "بيع", "price", "sale price", "selling price", "retail price"],
    "default_purchase_price": ["سعر شراء", "سعر التكلفه", "الشراء", "شراء", "cost price", "purchase price", "buy price"],
    "average_cost": ["التكلفه", "تكلفه", "متوسط التكلفه", "متوسط السعر", "avg cost", "average cost", "cost"],
    "min_stock": ["حد اعاده الطلب", "الحد الادني", "حد ادني", "اقل كميه", "reorder level", "min qty", "minimum"],
    "is_stock_tracked": ["مخزني", "يتابع مخزون", "stock item", "tracked"],
    "active": ["فعال", "نشط", "الحاله", "status", "active", "enabled"],
    "customer_code": ["كود", "الكود", "رقم العميل", "رقم الحساب", "كود الحساب", "customer no", "customer id", "code", "account"],
    "supplier_code": ["كود", "الكود", "رقم المورد", "رقم الحساب", "كود الحساب", "supplier no", "supplier id", "vendor code", "code", "account"],
    "cashbox_code": ["كود", "الكود", "رقم الخزنه", "رقم الصندوق", "كود الصندوق", "code"],
    "name": ["الاسم", "اسم", "اسم الحساب", "العميل", "المورد", "customer", "supplier", "vendor", "customer name", "supplier name", "name"],
    "phone": ["تليفون", "الهاتف", "هاتف", "موبايل", "المحمول", "جوال", "رقم التليفون", "رقم الموبايل", "mobile", "phone", "tel"],
    "whatsapp": ["واتس", "رقم الواتساب", "whatsapp"],
    "email": ["ايميل", "البريد", "البريد الالكتروني", "email", "e-mail", "mail"],
    "address": ["عنوان", "العنوان بالتفصيل", "المنطقه", "address"],
    "opening_balance": ["الرصيد", "رصيد", "رصيد اول المده", "الرصيد الافتتاحي", "رصيد افتتاحي", "المديونيه", "المستحق", "balance", "opening balance"],
    "credit_limit": ["حد الائتمان", "حد المديونيه", "الحد الائتماني", "credit limit"],
    "notes": ["ملاحظه", "ملاحظات", "بيان", "notes", "remarks", "comment"],
    "currency": ["عمله", "currency"],
    "is_default": ["افتراضي", "افتراضيه", "default"],
    "movement_date": ["تاريخ", "التاريخ", "تاريخ الجرد", "date"],
    "quantity": ["كميه", "الكميه", "الرصيد", "رصيد المخزن", "الكميه الحاليه", "الموجود", "qty", "quantity", "on hand", "stock"],
    "unit_cost": ["التكلفه", "تكلفه", "سعر التكلفه", "متوسط التكلفه", "cost", "unit cost"],
    "entity_type": ["النوع", "نوع الحساب", "type"],
    "entity_code": ["الكود", "كود", "code"],
}

BOOLEAN_FIELDS = {"active", "is_default", "is_receiving_location", "is_selling_location", "is_stock_tracked"}
NUMBER_FIELDS = {"default_sale_price", "default_purchase_price", "average_cost", "min_stock", "opening_balance", "credit_limit", "quantity", "unit_cost"}
TRUE_WORDS = {"نعم", "ايوه", "اه", "صح", "فعال", "نشط", "نشطه", "مفعل", "✓", "x"}
FALSE_WORDS = {"لا", "لأ", "غير فعال", "غير نشط", "موقوف", "متوقف", "ملغي"}

_DIACRITICS = re.compile(r"[ً-ْـ]")  # harakat and tatweel
_PUNCT = re.compile(r"[\s_\-./\\()\[\]:#*]+")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalise(text):
    """A header reduced to its letters, so "سعر  البيع" and "سعر_البيع" match."""

    text = _DIACRITICS.sub("", str(text or "").replace("﻿", "").strip().lower())
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")
    # "الموبايل" and "موبايل" are the same column: drop the article from each word.
    words = [word[2:] if word.startswith("ال") and len(word) > 3 else word for word in _PUNCT.sub(" ", text).split()]
    return "".join(words)


def fields(target_type):
    return FIELDS.get(target_type, [])


def _names(target_type, key, labels):
    names = [key, labels[0], labels[1]] + ALIASES.get(key, [])
    if target_type == "items" and key == "category_code":
        names += ALIASES["category_code_items"]
    return {normalise(name) for name in names}


def suggest(target_type, headers):
    """{header: field key or ""} for the file's columns; each field used once.

    Exact names win over aliases, and earlier columns over later ones.
    """

    table = [(key, _names(target_type, key, (ar, en)), {normalise(key), normalise(ar), normalise(en)}) for key, ar, en, _req in fields(target_type)]
    mapping, used = {header: "" for header in headers}, set()
    for strict in (True, False):
        for header in headers:
            if mapping[header]:
                continue
            name = normalise(header)
            for key, loose, exact in table:
                if key in used:
                    continue
                if name in (exact if strict else loose):
                    mapping[header] = key
                    used.add(key)
                    break
    return mapping


def is_template(target_type, headers):
    """True when the file already uses Hesba's own column keys (the downloaded template)."""

    keys = {key for key, *_ in fields(target_type)}
    names = {str(header).replace("\ufeff", "").strip().lower().replace(" ", "_") for header in headers}
    required = {key for key, _ar, _en, req in fields(target_type) if req}
    return bool(names) and names <= keys and required <= names


def clean_value(key, value):
    """A cell as the validators expect it: western digits, no thousands commas, true/false."""

    text = str(value if value is not None else "").strip().translate(_DIGITS)
    if not text:
        return ""
    if key in BOOLEAN_FIELDS:
        word = normalise(text)
        if word in {normalise(w) for w in TRUE_WORDS}:
            return "true"
        if word in {normalise(w) for w in FALSE_WORDS}:
            return "false"
        return text
    if key in NUMBER_FIELDS:
        number = text.replace("٬", "").replace("٫", ".").replace("،", ",").replace(" ", "").replace("\u00a0", "")
        if "," in number and "." in number:
            # the separator that comes first groups thousands: "1,250.50" or "1.250,50"
            thousands, decimal = (",", ".") if number.index(",") < number.index(".") else (".", ",")
            number = number.replace(thousands, "").replace(decimal, ".")
        elif "," in number:
            # "1,250" / "12,500,000" group thousands; "25,50" is a decimal comma
            number = number.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+-?", number) else number.replace(",", ".")
        if number.endswith("-") and re.fullmatch(r"[\d.]+-", number):  # "150-" as some programs print a credit
            number = "-" + number[:-1]
        return number
    return text


def apply(target_type, rows, mapping, auto_code=False):
    """Rows re-keyed to Hesba's fields; unmapped columns dropped; values cleaned.

    With ``auto_code``, rows without a code get one numbered from their position.
    """

    code_key, prefix = AUTO_CODE.get(target_type, (None, ""))
    result = []
    for index, row in enumerate(rows, start=1):
        data = {}
        for header, key in mapping.items():
            if key:
                data[key] = clean_value(key, row.get(header, ""))
        if auto_code and code_key and not data.get(code_key):
            data[code_key] = f"{prefix}{index:04d}"
        result.append(data)
    return result


def missing_required(target_type, mapping, auto_code=False):
    """Required fields no column feeds (a code counts when Hesba numbers it)."""

    mapped = set(mapping.values())
    code_key = AUTO_CODE.get(target_type, (None,))[0]
    return [(key, ar, en) for key, ar, en, req in fields(target_type) if req and key not in mapped and not (auto_code and key == code_key)]
