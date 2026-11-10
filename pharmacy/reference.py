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
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from .models import DrugReference
from .services import _DIGITS, _plain, form_from_text, ingredient_key, strength_key

#: Hesba's template, in this order. Only one of ``name_en`` / ``name_ar`` is needed; every other column may be empty.
TEMPLATE_COLUMNS = ("source_ref", "name_en", "name_ar", "active_ingredient", "strength", "form", "pack", "manufacturer",
                    "public_price", "price_date", "barcode", "prescription_only", "is_drug")

#: The "name, price, barcode" layout: each field and the header names it is recognised by (compared after folding).
SIMPLE_COLUMNS = {
    "name": ("name", "trade_name", "drug_name", "product", "product_name", "الاسم", "اسم الدوا", "اسم الدواء", "الاسم التجاري"),
    "public_price": ("price", "public_price", "new_price", "السعر"),
    "barcode": ("barcode", "ean", "gtin", "الباركود"),
    "name_ar": ("name_ar", "arabic_name", "الاسم العربي"),
    "active_ingredient": ("active_ingredient", "ingredient", "scientific_name", "generic_name", "المادة الفعالة"),
    "manufacturer": ("manufacturer", "company", "الشركة"),
    "source_ref": ("source_ref", "id"),
}

LAYOUTS = ("auto", "hesba", "simple")
BATCH = 1000
UPDATE_BATCH = 200  # a bulk_update builds one CASE per field; small batches keep it fast on SQLite and PostgreSQL
MAX_PRICE = Decimal("9999999999.99")  # what public_price (12 digits, 2 decimals) holds
_LIMITS = {model_field.name: model_field.max_length for model_field in DrugReference._meta.get_fields() if getattr(model_field, "max_length", None)}
_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
_YES = {"1", "true", "yes", "y", "rx", "نعم", "ايوه", "اه"}
_NO = {"0", "false", "no", "n", "otc", "لا"}
_CURRENCY = re.compile(r"^(?:egp|l\.?e\.?|ج\.?م\.?|جنيه)\s*|\s*(?:egp|l\.?e\.?|ج\.?م\.?|جنيه)$")


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
    dry_run: bool = False
    skip_reasons: Counter = field(default_factory=Counter)

    def line(self):
        dry_ar, dry_en = (" (تجربة بس، ومفيش حاجة اتحفظت)", " (dry run, nothing saved)") if self.dry_run else ("", "")
        return (f"قايمة الأدوية «{self.source}»{dry_ar}: اتضاف {self.created}، اتحدّث {self.updated}، اتقفل {self.deactivated}، "
                f"اتساب {self.skipped}، باركود غلط اتشال {self.bad_barcodes}، سعر غلط اتشال {self.bad_prices}. | "
                f"Drug reference “{self.source}”{dry_en}: created {self.created}, updated {self.updated}, deactivated {self.deactivated}, "
                f"skipped {self.skipped}, bad barcodes {self.bad_barcodes}, bad prices {self.bad_prices}.")


# ---- cleaning one value ----

def _cell(value):
    """A spreadsheet or CSV cell as text: ``12.0`` from Excel is ``12``, a date is ``YYYY-MM-DD``."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def clean_text(value, limit):
    """One line of text: control and invisible characters dropped, spaces collapsed, cut to ``limit``."""

    text = "".join(" " if char.isspace() else char for char in str(value or "") if char.isspace() or not unicodedata.category(char).startswith("C"))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def fold(text):
    """The name reduced for searching, as ``pharmacy.services`` reduces text (case, hamza forms, ة, ى, digits) plus spaces."""

    return re.sub(r"\s+", " ", _plain(text)).strip()[:_LIMITS["name_key"]]


def gtin_ok(digits):
    """The GS1 check digit of an EAN-8, UPC-A (12) or EAN-13 code."""

    if len(digits) not in (8, 12, 13) or not (digits.isascii() and digits.isdigit()):
        return False
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits[:-1])))
    return (10 - total % 10) % 10 == int(digits[-1])


def barcode_or_blank(value):
    """(barcode, bad): a valid EAN-8 / UPC-A / EAN-13, or "" with ``bad`` set when something was there but failed."""

    text = re.sub(r"[\s-]+", "", _cell(value).translate(_DIGITS))
    if not text:
        return "", False
    return (text, False) if gtin_ok(text) else ("", True)


def price_or_none(value):
    """(price, bad): a positive price to 2 decimals; empty or zero is no price; anything unreadable or absurd is bad."""

    text = _CURRENCY.sub("", _cell(value).translate(_DIGITS).strip().lower()).strip()
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
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def yes_no(value):
    """True, False, or None when the cell says neither."""

    text = _plain(_cell(value))
    return True if text in _YES else False if text in _NO else None


# ---- reading strength, form and pack from a trade name ----

_MASS = r"mcg|µg|mg|gm|g|iu|%|ميكروجرام|ملجم|مجم|مج|جرام|جم|وحده"
_STRENGTH = re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)*)\s*({_MASS})(?:\s*/\s*(?:\d+(?:\.\d+)?)?\s*(?:ml|مل))?(?!\w)")
_VOLUME = re.compile(r"(?<![\w./])\d+(?:\.\d+)?\s*(?:ml|مل)(?!\w)")
_COUNT = re.compile(r"(?<![\w.])(\d{1,4})\s*(?:['’]s(?!\w)|(?:f\.?c\.?\s*)?(?:tabs?|tablets?|caps?|capsules?|amps?|ampoules?|vials?|sachets?|supp)\.?(?!\w)"
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
    return re.sub(r"[\s\-]+", "_", _plain(_cell(value))).strip("_")


#: Each field's folded header names, in the listed order: with two of them in a file, the first listed wins.
_SIMPLE_HEADERS = {field_name: tuple(dict.fromkeys(_header(alias) for alias in aliases)) for field_name, aliases in SIMPLE_COLUMNS.items()}
_ARABIC = re.compile(r"[\u0600-\u06FF]")


def _csv_rows(path):
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1256")
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(",;\t", key=first.count)
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def _xlsx_rows(path):
    try:
        from openpyxl import load_workbook
    except ImportError as error:  # pragma: no cover - openpyxl is in requirements.txt
        raise ReferenceImportError("ملفات الإكسل محتاجة openpyxl؛ استخدم CSV. | .xlsx needs openpyxl; use a CSV file.") from error
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        return [list(row) for row in book.worksheets[0].iter_rows(values_only=True)]
    finally:
        book.close()


def read_table(path):
    """(headers, rows): the folded header names and each non-empty row as {header: text}."""

    path = Path(path)
    if not path.is_file():
        raise ReferenceImportError(f"الملف مش موجود: {path} | File not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        table = _csv_rows(path)
    elif suffix == ".xlsx":
        table = _xlsx_rows(path)
    else:
        raise ReferenceImportError("الملف لازم يكون CSV أو xlsx. | The file must be .csv or .xlsx.")
    table = [row for row in table if any(_cell(value).strip() for value in row)]
    if not table:
        raise ReferenceImportError("الملف فاضي. | The file is empty.")
    headers = [_header(value) for value in table[0]]
    rows = [{name: _cell(value) for name, value in zip(headers, row) if name} for row in table[1:]]
    return headers, rows


def detect_layout(headers):
    present = set(headers)
    if present & set(_SIMPLE_HEADERS["name"]):
        return "simple"
    if present & {"name_en", "name_ar"}:
        return "hesba"
    raise ReferenceImportError("مفيش عمود للاسم (name_en أو name_ar أو name). | No name column (name_en, name_ar or name).")


# ---- one row ----

def _simple_values(row):
    def pick(field_name):
        return next((row[header] for header in _SIMPLE_HEADERS[field_name] if row.get(header, "").strip()), "")

    name, name_ar = pick("name"), pick("name_ar")
    if not name_ar and _ARABIC.search(name):  # an Arabic trade name is the Arabic name, not the English one
        name, name_ar = "", name
    parsed = parse_name(name or name_ar)
    return {"source_ref": pick("source_ref"), "name_en": name, "name_ar": name_ar, "active_ingredient": pick("active_ingredient"),
            "manufacturer": pick("manufacturer"), "public_price": pick("public_price"), "barcode": pick("barcode"), **parsed}


def build_row(raw, layout, price_date=None):
    """(values, problems) for one row: ``values`` is None when the row is skipped (``problems`` names why)."""

    data = _simple_values(raw) if layout == "simple" else raw
    name_en = clean_text(data.get("name_en"), _LIMITS["name_en"])
    name_ar = clean_text(data.get("name_ar"), _LIMITS["name_ar"])
    if not (name_en or name_ar):
        return None, {"no_name"}
    problems = set()
    barcode, bad = barcode_or_blank(data.get("barcode"))
    if bad:
        problems.add("bad_barcode")
    price, bad = price_or_none(data.get("public_price"))
    if bad:
        problems.add("bad_price")
    ingredient = clean_text(data.get("active_ingredient"), _LIMITS["active_ingredient"])
    strength = clean_text(data.get("strength"), _LIMITS["strength"])
    name_key = fold(name_en or name_ar)
    values = {
        "source_ref": clean_text(data.get("source_ref"), _LIMITS["source_ref"]) or "n:" + hashlib.sha1(name_key.encode()).hexdigest()[:24],
        "name_en": name_en, "name_ar": name_ar, "name_key": name_key,
        "active_ingredient": ingredient, "ingredient_key": ingredient_key(ingredient) if ingredient else "",
        "strength": strength, "strength_key": strength_key(strength) if strength else "",
        "form": form_from_text(data.get("form")) if data.get("form") else "",
        "pack": clean_text(data.get("pack"), _LIMITS["pack"]),
        "manufacturer": clean_text(data.get("manufacturer"), _LIMITS["manufacturer"]),
        "public_price": price, "price_date": date_or_none(data.get("price_date")) or price_date,
        "barcode": barcode,
        "prescription_only": yes_no(data.get("prescription_only")),
        "is_drug": yes_no(data.get("is_drug")) is not False,
    }
    return values, problems


# ---- the import ----

DATA_FIELDS = ("name_en", "name_ar", "name_key", "active_ingredient", "ingredient_key",
                  "strength", "strength_key", "form", "pack", "manufacturer", "public_price", "price_date", "barcode", "prescription_only", "is_drug")


def import_file(path, *, source, source_version, price_date=None, layout="auto", dry_run=False, deactivate_missing=False):
    """Load ``path`` into ``DrugReference`` under ``source``. Returns a :class:`Summary`; ``dry_run`` writes nothing."""

    source = (source or "").strip()
    if not _SOURCE.match(source):
        raise ReferenceImportError("اسم المصدر حروف إنجليزي وأرقام و . _ - بس، لحد 60 حرف. | The source is letters, digits, “.”, “_” or “-”, up to 60.")
    if layout not in LAYOUTS:
        raise ReferenceImportError(f"الشكل لازم يكون واحد من {', '.join(LAYOUTS)}. | The layout must be one of {', '.join(LAYOUTS)}.")
    headers, rows = read_table(path)
    layout = detect_layout(headers) if layout == "auto" else layout
    summary = Summary(source=source, layout=layout, rows=len(rows), dry_run=dry_run)
    parsed = {}
    for raw in rows:
        values, problems = build_row(raw, layout, price_date)
        if values is None:
            summary.skipped += 1
            summary.skip_reasons.update(problems)
        elif values["source_ref"] in parsed:
            summary.skipped += 1
            summary.skip_reasons["duplicate"] += 1
        else:
            parsed[values.pop("source_ref")] = values
            summary.bad_barcodes += "bad_barcode" in problems
            summary.bad_prices += "bad_price" in problems
    if deactivate_missing and not parsed:
        raise ReferenceImportError("مفيش ولا سطر ينفع يتحمّل، فمش هنقفل حاجة. | No usable row in the file, so nothing is deactivated.")
    version = clean_text(source_version, _LIMITS["source_version"])
    now = timezone.now()
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
        # "updated" is every row found again: each gets the new version and import time; only the changed ones are rewritten.
        summary.created, summary.updated, summary.deactivated = len(creates), len(found), len(missing)
        if not dry_run:
            DrugReference.objects.bulk_create(creates, batch_size=BATCH)
            DrugReference.objects.bulk_update(changed, DATA_FIELDS, batch_size=UPDATE_BATCH)
            for start in range(0, len(found), BATCH):
                DrugReference.objects.filter(pk__in=found[start:start + BATCH]).update(source_version=version, imported_at=now, is_active=True)
            for start in range(0, len(missing), BATCH):
                DrugReference.objects.filter(pk__in=missing[start:start + BATCH]).update(is_active=False)
    return summary
