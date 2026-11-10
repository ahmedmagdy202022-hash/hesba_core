"""R1 (HG-082): loading a reference drug list from a file into :class:`~pharmacy.models.DrugReference`.

Three layouts are read (``docs/DRUG_REFERENCE_IMPORT.md``):

* ``hesba``: Hesba's own template, the columns in :data:`TEMPLATE_COLUMNS`, as CSV or ``.xlsx``;
* ``simple``: a "name, price, barcode" list (a community file or a pharmacy's own sheet). Strength, form and pack are
  read from the trade name by conservative patterns and left empty whenever the name is unclear;
* either of the two as ``.xlsx`` (the first sheet, the first non-empty row as the header).

Rows are upserted by (``source``, ``source_ref``) in one transaction. Nothing here touches ``Item``, ``DrugInfo``, a
price, stock, a cashbox or a ledger: the only table written is ``DrugReference``. Nothing is guessed: an unreadable
price, a barcode with a wrong check digit or an unclear form is left empty (and counted), never made up.
"""

import csv
import hashlib
import io
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import DrugReference
from .services import _DIGITS, _plain, form_from_text, ingredient_key, strength_key

#: Hesba's template, in this order. Only one of ``name_en`` / ``name_ar`` is needed; every other column may be empty.
TEMPLATE_COLUMNS = ("source_ref", "name_en", "name_ar", "active_ingredient", "strength", "form", "pack", "manufacturer",
                    "public_price", "price_date", "barcode", "prescription_only", "is_drug")

#: Each field and the header names it is recognised by (compared after folding), in both layouts. With two of them in
#: one file, the first listed wins. ``name`` is a trade name in either language: Arabic letters make it the Arabic name.
COLUMN_ALIASES = {
    "source_ref": ("source_ref", "id"),
    "name_en": ("name_en",),
    "name": ("name", "trade_name", "drug_name", "product", "product_name", "الاسم", "اسم الدوا", "اسم الدواء", "الاسم التجاري"),
    "name_ar": ("name_ar", "arabic_name", "الاسم العربي"),
    "active_ingredient": ("active_ingredient", "ingredient", "scientific_name", "generic_name", "المادة الفعالة"),
    "strength": ("strength",),
    "form": ("form",),
    "pack": ("pack",),
    "manufacturer": ("manufacturer", "company", "الشركة"),
    "public_price": ("public_price", "price", "new_price", "price (EGP)", "السعر"),
    "price_date": ("price_date",),
    "barcode": ("barcode", "ean", "gtin", "الباركود"),
    "prescription_only": ("prescription_only",),
    "is_drug": ("is_drug",),
}
#: The fields the "name, price, barcode" layout reads; strength, form and pack always come from the name there.
SIMPLE_COLUMNS = {name: COLUMN_ALIASES[name] for name in ("name", "public_price", "barcode", "name_ar", "active_ingredient", "manufacturer", "source_ref")}
#: A header only Hesba's template has: with one of them in the file, it is the template even beside a ``name`` column.
HESBA_ONLY = ("strength", "form", "pack", "source_ref", "is_drug", "prescription_only")
_FROM_NAME = ("strength", "form", "pack")

LAYOUTS = ("auto", "hesba", "simple")
BATCH = 1000
UPDATE_BATCH = 200  # a bulk_update builds one CASE per field; small batches keep it fast on SQLite and PostgreSQL
MAX_PRICE = Decimal("9999999999.99")  # what public_price (12 digits, 2 decimals) holds
MAX_DEACTIVATE = Decimal("0.5")      # --deactivate-missing refuses to switch off more than this share of a source
_LIMITS = {model_field.name: model_field.max_length for model_field in DrugReference._meta.get_fields() if getattr(model_field, "max_length", None)}
_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
_YES = {"1", "true", "yes", "y", "rx", "نعم", "ايوه", "اه"}
_NO = {"0", "false", "no", "n", "otc", "لا"}
_CURRENCY = re.compile(r"^(?:egp|l\.?e\.?|ج\.?م\.?|جنيه)\s*|\s*(?:egp|l\.?e\.?|ج\.?م\.?|جنيه)$")
_MARKS = re.compile("[ـً-ْٰ]")  # tatweel, harakat and the dagger alif: not part of a spelling
_PAD = {7: 8, 11: 12, 12: 13}  # a barcode typed as a number loses its leading zeros
_SKIP_WORDS = {"no_name": ("من غير اسم", "no name"), "duplicate": ("متكرر", "duplicate")}


class ReferenceImportError(Exception):
    """The file cannot be imported at all; the message is in Arabic and English."""


@dataclass
class Summary:
    source: str = ""
    layout: str = ""
    rows: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    skipped: int = 0
    bad_barcodes: int = 0
    bad_prices: int = 0
    bad_dates: int = 0
    dry_run: bool = False
    skip_reasons: Counter = field(default_factory=Counter)
    skip_lines: dict = field(default_factory=lambda: defaultdict(list))  # reason -> the file's line numbers (1-based)
    ignored_columns: list = field(default_factory=list)

    def skip(self, reason, line):
        self.skipped += 1
        self.skip_reasons[reason] += 1
        self.skip_lines[reason].append(line)

    def line(self):
        dry_ar, dry_en = (" (تجربة بس، ومفيش حاجة اتحفظت)", " (dry run, nothing saved)") if self.dry_run else ("", "")
        return (f"قايمة الأدوية «{self.source}»{dry_ar}: اتضاف {self.created}، اتحدّث {self.updated}، اتقفل {self.deactivated}، "
                f"اتساب {self.skipped}، باركود غلط اتشال {self.bad_barcodes}، سعر غلط اتشال {self.bad_prices}، تاريخ غلط اتساب {self.bad_dates}. | "
                f"Drug reference “{self.source}”{dry_en}: created {self.created}, updated {self.updated}, deactivated {self.deactivated}, "
                f"skipped {self.skipped}, bad barcodes {self.bad_barcodes}, bad prices {self.bad_prices}, bad dates {self.bad_dates}.")

    def lines(self):
        """The summary line, then one line on the skipped rows and one on the ignored columns when there are any."""

        out = [self.line()]
        if self.skip_reasons:
            def where(reason, word):
                lines = self.skip_lines[reason]
                shown = ", ".join(str(number) for number in lines[:3]) + ("…" if len(lines) > 3 else "")
                return f"{word} {self.skip_reasons[reason]} ({shown})"
            reasons = sorted(self.skip_reasons)
            arabic = "، ".join(where(reason, _SKIP_WORDS.get(reason, (reason, reason))[0]) for reason in reasons)
            english = ", ".join(where(reason, _SKIP_WORDS.get(reason, (reason, reason))[1]) for reason in reasons)
            out.append(f"السطور اللي اتسابت (رقم السطر في الملف): {arabic} | Skipped rows (line in the file): {english}")
        if self.ignored_columns:
            out.append(f"أعمدة اتجاهلت / ignored columns: {', '.join(self.ignored_columns)}")
        return out


# ---- cleaning one value ----

def _cell(value):
    """A spreadsheet or CSV cell as text: ``12.0`` from Excel is ``12``, ``27.500000000000004`` is ``27.5``, a date is ``YYYY-MM-DD``."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        if not math.isfinite(value):
            return repr(value)
        try:  # a float's binary noise is cut at 6 decimals, far below any price or code a list holds
            return format(Decimal(repr(value)).quantize(Decimal("0.000001")).normalize(), "f")
        except InvalidOperation:
            return repr(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _numeric(value):
    """True for a number cell (an Excel number), not for text that looks like one."""

    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def clean_text(value, limit):
    """One line of text: control and invisible characters dropped, spaces collapsed, cut to ``limit``."""

    text = "".join(" " if char.isspace() else char for char in _cell(value) if char.isspace() or not unicodedata.category(char).startswith("C"))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def fold(text):
    """The name reduced for searching: as ``pharmacy.services`` reduces text (case, hamza forms, ة, ى, digits), plus
    spaces, harakat and tatweel. ``_plain`` itself is left alone: ``DrugInfo`` keys are built with it."""

    return re.sub(r"\s+", " ", _MARKS.sub("", _plain(text))).strip()[:_LIMITS["name_key"]]


def gtin_ok(digits):
    """The GS1 check digit of an EAN-8, UPC-A (12) or EAN-13 code."""

    if len(digits) not in (8, 12, 13) or not (digits.isascii() and digits.isdigit()):
        return False
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits[:-1])))
    return (10 - total % 10) % 10 == int(digits[-1])


def barcode_or_blank(value):
    """(barcode, bad): a valid EAN-8 / UPC-A / EAN-13, or "" with ``bad`` set when something was there but failed.

    A number cell of 7, 11 or 12 digits lost its leading zeros in the spreadsheet: it is padded and checked again. A
    UPC-A (12 digits) is stored as its EAN-13 form (a leading 0), so one product has one barcode string."""

    text = re.sub(r"[\s-]+", "", _cell(value).translate(_DIGITS))
    if not text:
        return "", False
    if _numeric(value) and len(text) in _PAD and gtin_ok(text.zfill(_PAD[len(text)])):
        text = text.zfill(_PAD[len(text)])
    if not gtin_ok(text):
        return "", True
    return ("0" + text if len(text) == 12 else text), False


def price_or_none(value):
    """(price, bad): a positive price to 2 decimals; empty or zero is no price; anything unreadable, ambiguous or past
    the column's limit is bad. ``.`` is the decimal point and ``,`` groups thousands, so text such as ``1.250`` (1.25 or
    1250?) is ambiguous; a number cell is not."""

    text = _CURRENCY.sub("", _cell(value).translate(_DIGITS).strip().lower()).strip()
    if not _numeric(value) and re.fullmatch(r"[1-9]\d{0,2}\.\d{3}", text):
        return None, True
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        text = text.replace(",", "")
    if not text:
        return None, False
    if not re.fullmatch(r"\d{1,15}(?:\.\d{1,6})?", text):
        return None, True
    amount = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount == 0:
        return None, False
    return (None, True) if amount > MAX_PRICE else (amount, False)


def date_or_none(value):
    text = _cell(value).translate(_DIGITS).strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def yes_no(value):
    """True, False, or None when the cell says neither."""

    text = _plain(_cell(value))
    return True if text in _YES else False if text in _NO else None


def form_or_blank(value):
    """The dosage form of a form cell (``tab``, ``أقراص``, ``F.C.Tabs``, ``fc tabs``), or "" when it is not clear."""

    text = _plain(_cell(value)).strip(" .")
    if not text:
        return ""
    # "f.c." (film-coated) in front of a form word is read here, not added to services.FORM_WORDS (save_drug uses it).
    return form_from_text(text) or form_from_text(re.sub(r"^f\.?\s*c\.?\s*", "", text).strip(" ."))


# ---- reading strength, form and pack from a trade name ----
# A comma before the digits (“1,000 IU”, “2,500mg”) is a grouped number the reader does not understand: the whole
# strength stays blank rather than keeping the suffix after the comma.

_MASS = r"mcg|µg|mg|gm|g|iu|%|ميكروجرام|ملجم|مجم|مج|جرام|جم|وحده"
_STRENGTH = re.compile(rf"(?<![\w.,])(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)*)\s*({_MASS})(?:\s*/\s*(?:\d+(?:\.\d+)?)?\s*(?:ml|مل))?(?!\w)")
_VOLUME = re.compile(r"(?<![\w./,])\d+(?:\.\d+)?\s*(?:ml|مل)(?!\w)")
_COUNT = re.compile(r"(?<![\w.,])(\d{1,4})\s*(?:['’]s(?!\w)|(?:f\.?c\.?\s*)?(?:tabs?|tablets?|caps?|capsules?|amps?|ampoules?|vials?|sachets?|supp)\.?(?!\w)"
                    r"|(?:اقراص|قرص|كبسوله|كبسول|امبول|اكياس|كيس|لبوس)(?!\w))")
_GRAMS = ("g", "gm", "جم", "جرام")
_FORM_NOISE = re.compile(r"^\d+(?:\.\d+)?|^f\.?c\.?|[.,;:()'’\[\]]+")


def parse_name(name):
    """{"strength", "form", "pack"} read from a trade name such as “Hesbacillin 500mg caps 20's”.

    A field is filled only when exactly one clear reading is found; two different strengths, forms or packs leave it empty.
    Grams on a cream are its pack (“30 gm”), not a strength."""

    text = _plain(name)
    forms = set()
    for token in text.split():
        word = _FORM_NOISE.sub("", token)
        form = form_from_text(word) if word else ""
        if form and form != "other":
            forms.add(form)
    form = forms.pop() if len(forms) == 1 else ""
    strengths, packs, taken = set(), set(), []
    for match in _STRENGTH.finditer(text):
        taken.append(match.span())
        found = re.sub(r"\s+", " ", match.group(0)).strip()
        if form == "cream" and match.group(2) in _GRAMS and "/" not in found:
            packs.add(found)
        else:
            strengths.add(found)
    for match in _VOLUME.finditer(text):
        if not any(start <= match.start() < end for start, end in taken):
            packs.add(re.sub(r"\s+", " ", match.group(0)).strip())
    for match in _COUNT.finditer(text):
        packs.add(re.sub(r"\s+", " ", match.group(0)).strip().rstrip("."))
    return {"strength": strengths.pop() if len(strengths) == 1 else "", "form": form, "pack": packs.pop() if len(packs) == 1 else ""}


# ---- reading the file ----

def _header(value):
    """A header folded for matching: case, Arabic letter forms, harakat, spaces, hyphens and punctuation."""

    return re.sub(r"[^\w]+", "_", _MARKS.sub("", _plain(_cell(value)))).strip("_")


#: Each field's folded header names, in the listed order.
_HEADERS = {field_name: tuple(dict.fromkeys(_header(alias) for alias in aliases)) for field_name, aliases in COLUMN_ALIASES.items()}
_ARABIC = re.compile(r"[؀-ۿ]")


def _decode(raw):
    """The text of a CSV file: UTF-16 with its byte-order mark, else UTF-8, else Windows Arabic (cp1256).

    A file that is mostly UTF-8 with a few broken bytes is refused, naming the first one, instead of being read as
    cp1256 and garbling every Arabic letter. (A cp1256 file can hold a pair that is valid UTF-8 by chance, e.g. a letter
    before «،», so it is the share of valid UTF-8 characters that decides, not a single one.)"""

    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError as error:
            raise ReferenceImportError(f"ملف UTF-16 فيه بايت بايظ عند الموضع {error.start}. | The UTF-16 file has an invalid byte at offset {error.start}.") from error
    try:
        return raw.decode("utf-8").removeprefix("﻿")
    except UnicodeDecodeError as error:
        loose = raw.decode("utf-8", errors="replace")
        broken = loose.count("�")
        valid = sum(1 for char in loose if ord(char) > 0x7F and char != "�")
        if raw.startswith(b"\xef\xbb\xbf") or valid * 3 > broken:
            raise ReferenceImportError(
                f"الملف UTF-8 بس فيه بايت بايظ عند الموضع {error.start}؛ احفظه تاني UTF-8. | "
                f"The file is UTF-8 but has an invalid byte at offset {error.start}; save it again as UTF-8.") from error
    return raw.decode("cp1256")


def _csv_rows(path):
    """[(line, cells)]: each record with the 1-based line it starts on."""

    text = _decode(Path(path).read_bytes())
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(",;\t", key=first.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows, last = [], 0
    for cells in reader:
        rows.append((last + 1, cells))
        last = reader.line_num
    return rows


def _xlsx_rows(path):
    try:
        from openpyxl import load_workbook
    except ImportError as error:  # pragma: no cover - openpyxl is in requirements.txt
        raise ReferenceImportError("ملفات الإكسل محتاجة openpyxl؛ استخدم CSV. | .xlsx needs openpyxl; use a CSV file.") from error
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        # iter_rows starts at row 1 (padding the empty rows above the data), so the count is the Excel row number.
        return [(number, list(row)) for number, row in enumerate(book.worksheets[0].iter_rows(values_only=True), start=1)]
    finally:
        book.close()


def read_table(path):
    """(headers, titles, rows): the folded header names, the headers as written, and each non-empty row as
    (line, {header: cell}). A cell keeps its type (an Excel number stays a number) for the price and barcode checks."""

    path = Path(path)
    if not path.is_file():
        raise ReferenceImportError(f"الملف مش موجود: {path} | File not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        table = _csv_rows(path)
    elif suffix == ".xlsx":
        table = _xlsx_rows(path)
    else:
        raise ReferenceImportError("الملف لازم يكون CSV أو txt أو xlsx. | The file must be .csv, .txt or .xlsx.")
    table = [(line, row) for line, row in table if any(_cell(value).strip() for value in row)]
    if not table:
        raise ReferenceImportError("الملف فاضي. | The file is empty.")
    titles = [clean_text(value, 80) for value in table[0][1]]
    headers = [_header(value) for value in table[0][1]]
    repeated = sorted({name for name in headers if name and headers.count(name) > 1})
    if repeated:
        raise ReferenceImportError(f"فيه عمودين بنفس الاسم: {', '.join(repeated)}. | Two columns have the same header: {', '.join(repeated)}.")
    rows = [(line, {name: value for name, value in zip(headers, row) if name}) for line, row in table[1:]]
    return headers, titles, rows


def detect_layout(headers):
    present = set(headers)
    if present & set(HESBA_ONLY):
        return "hesba"
    if present & set(_HEADERS["name"]):
        return "simple"
    if present & (set(_HEADERS["name_en"]) | set(_HEADERS["name_ar"])):
        return "hesba"
    raise ReferenceImportError("مفيش عمود للاسم (name_en أو name_ar أو name). | No name column (name_en, name_ar or name).")


def columns_for(headers, titles, layout):
    """({field: [header, ...]}, ignored): the file's headers each field is read from, in priority order, and the
    headers (as written) nothing reads."""

    fields = SIMPLE_COLUMNS if layout == "simple" else COLUMN_ALIASES
    present = set(headers)
    columns = {name: [header for header in _HEADERS[name] if header in present] for name in fields}
    used = {header for found in columns.values() for header in found}
    ignored = [title or header for header, title in zip(headers, titles) if header and header not in used]
    return columns, ignored


# ---- one row ----

def build_row(raw, columns, price_date=None):
    """(values, problems) for one row: ``values`` is None when the row is skipped (``problems`` names why).

    ``public_price`` is left out of ``values`` when the cell is unreadable, and ``price_date`` whenever the row has no
    price or its date cell is unreadable: a stored row then keeps what it had."""

    def pick(field_name):
        return next((raw[header] for header in columns.get(field_name, ()) if _cell(raw.get(header)).strip()), "")

    name_en, name_ar, name = pick("name_en"), pick("name_ar"), _cell(pick("name"))
    if not name_en and name:
        if not name_ar and _ARABIC.search(name):  # an Arabic trade name is the Arabic name, not the English one
            name_ar = name
        else:
            name_en = name
    name_en = clean_text(name_en, _LIMITS["name_en"])
    name_ar = clean_text(name_ar, _LIMITS["name_ar"])
    if not (name_en or name_ar):
        return None, {"no_name"}
    # Strength, form and pack: the file's column when it has one (an empty cell stays empty), else read from the name.
    parsed = parse_name(name_en or name_ar) if any(not columns.get(name) for name in _FROM_NAME) else {}
    data = {name: pick(name) if columns.get(name) else parsed[name] for name in _FROM_NAME}
    problems = set()
    barcode, bad = barcode_or_blank(pick("barcode"))
    if bad:
        problems.add("bad_barcode")
    ingredient = clean_text(pick("active_ingredient"), _LIMITS["active_ingredient"])
    strength = clean_text(data["strength"], _LIMITS["strength"])
    form = form_or_blank(data["form"])
    pack = clean_text(data["pack"], _LIMITS["pack"])
    name_key = fold(name_en or name_ar)
    strength_folded = strength_key(strength) if strength else ""
    derived = "|".join((name_key, strength_folded, form, fold(pack)))
    values = {
        "source_ref": clean_text(pick("source_ref"), _LIMITS["source_ref"]) or "n:" + hashlib.sha1(derived.encode()).hexdigest()[:24],
        "name_en": name_en, "name_ar": name_ar, "name_key": name_key,
        "active_ingredient": ingredient, "ingredient_key": ingredient_key(ingredient) if ingredient else "",
        "strength": strength, "strength_key": strength_folded, "form": form, "pack": pack,
        "manufacturer": clean_text(pick("manufacturer"), _LIMITS["manufacturer"]),
        "barcode": barcode,
        "prescription_only": yes_no(pick("prescription_only")),  # an empty cell is unknown, also over a stored answer
        "is_drug": yes_no(pick("is_drug")) is not False,
    }
    price, bad = price_or_none(pick("public_price"))
    if bad:
        problems.add("bad_price")
    else:
        values["public_price"] = price
    date_cell, row_date = pick("price_date"), None
    if _cell(date_cell).strip():
        row_date = date_or_none(date_cell)
        if row_date is None:
            problems.add("bad_date")
    if price is not None and "bad_date" not in problems and (row_date or price_date):
        values["price_date"] = row_date or price_date
    return values, problems


# ---- the import ----

DATA_FIELDS = ("name_en", "name_ar", "name_key", "active_ingredient", "ingredient_key",
               "strength", "strength_key", "form", "pack", "manufacturer", "public_price", "price_date", "barcode", "prescription_only", "is_drug")


def import_file(path, *, source, source_version, price_date=None, layout="auto", dry_run=False, deactivate_missing=False, force_deactivate=False):
    """Load ``path`` into ``DrugReference`` under ``source``. Returns a :class:`Summary`; ``dry_run`` writes nothing."""

    source = (source or "").strip()
    if not _SOURCE.match(source):
        raise ReferenceImportError("اسم المصدر حروف إنجليزي وأرقام و . _ - بس، لحد 60 حرف. | The source is letters, digits, “.”, “_” or “-”, up to 60.")
    if layout not in LAYOUTS:
        raise ReferenceImportError(f"الشكل لازم يكون واحد من {', '.join(LAYOUTS)}. | The layout must be one of {', '.join(LAYOUTS)}.")
    headers, titles, rows = read_table(path)
    layout = detect_layout(headers) if layout == "auto" else layout
    columns, ignored = columns_for(headers, titles, layout)
    summary = Summary(source=source, layout=layout, rows=len(rows), dry_run=dry_run, ignored_columns=ignored)
    parsed = {}
    for line, raw in rows:
        values, problems = build_row(raw, columns, price_date)
        if values is None:
            for problem in sorted(problems):
                summary.skip(problem, line)
        elif values["source_ref"] in parsed:  # the first row with an id wins; a later one is skipped, never merged
            summary.skip("duplicate", line)
        else:
            parsed[values.pop("source_ref")] = values
            summary.bad_barcodes += "bad_barcode" in problems
            summary.bad_prices += "bad_price" in problems
            summary.bad_dates += "bad_date" in problems
    if deactivate_missing and not parsed:
        raise ReferenceImportError("مفيش ولا سطر ينفع يتحمّل، فمش هنقفل حاجة. | No usable row in the file, so nothing is deactivated.")
    version = clean_text(source_version, _LIMITS["source_version"])
    now = timezone.now()
    try:
        with transaction.atomic():
            existing = {row.source_ref: row for row in DrugReference.objects.select_for_update().filter(source=source)}
            creates, found, changed = [], [], []
            for ref, values in parsed.items():
                row = existing.get(ref)
                if row is None:
                    creates.append(DrugReference(source=source, source_ref=ref, source_version=version, imported_at=now, is_active=True, **values))
                    continue
                found.append(row.pk)
                if any(getattr(row, name) != value for name, value in values.items()):
                    for name, value in values.items():
                        setattr(row, name, value)
                    changed.append(row)
            missing = [row.pk for ref, row in existing.items() if ref not in parsed and row.is_active] if deactivate_missing else []
            active = sum(1 for row in existing.values() if row.is_active)
            if missing and not force_deactivate and len(missing) > active * MAX_DEACTIVATE:
                raise ReferenceImportError(
                    f"الملف ده هيقفل {len(missing)} من {active} صنف شغالين في «{source}» (أكتر من النص)؛ لو ده المقصود ضيف --force-deactivate. | "
                    f"This file would deactivate {len(missing)} of the {active} active rows of “{source}” (more than half); "
                    "pass --force-deactivate if that is intended.")
            # "updated" is every row found again: each gets the new version and import time; only the changed ones are rewritten.
            summary.created, summary.updated, summary.deactivated = len(creates), len(found), len(missing)
            if not dry_run:
                DrugReference.objects.bulk_create(creates, batch_size=BATCH)
                DrugReference.objects.bulk_update(changed, DATA_FIELDS, batch_size=UPDATE_BATCH)
                for start in range(0, len(found), BATCH):
                    DrugReference.objects.filter(pk__in=found[start:start + BATCH]).update(source_version=version, imported_at=now, is_active=True)
                for start in range(0, len(missing), BATCH):
                    DrugReference.objects.filter(pk__in=missing[start:start + BATCH]).update(is_active=False)
    except IntegrityError as error:
        raise ReferenceImportError(
            f"مقدرناش نحفظ القايمة «{source}» (غالبًا استيراد تاني لنفس المصدر شغال في نفس الوقت)؛ مفيش حاجة اتحفظت، جرّب تاني. | "
            f"Could not save “{source}” (most likely another import of the same source ran at the same time); nothing was saved, try again.") from error
    return summary
