"""M9 (HG-092): importing a lab's results from an analyzer's CSV file.

``sample code, test code, value``, one result per line. Reading, previewing and applying are three separate steps:

* ``read_file`` turns the upload into plain rows (UTF-8 with or without a byte-order mark, then Windows Arabic; a comma or a
  semicolon; Windows or Unix line ends; a header row or none) and refuses what is not a CSV, is too large or is empty. It
  follows the conventions of the opening-data import (``imports.screen_services``: 5 MB, 5000 rows, the same encodings and the
  same ``file_*`` error words) but is its own small reader: that one always treats the first line as a header, takes xlsx
  too, and guesses the delimiter with ``csv.Sniffer``, which reads ``L5-20261112;GLU;12,5`` as comma-separated.
* ``classify`` says, per line, what applying it would do, and writes nothing.
* ``apply`` classifies again inside one transaction, and writes **only the lines that would be written** through
  ``lab.record_results`` (the typed path: the unit and range of the test, the flag, the audit row per result). A result that
  is already entered is never written (its value is shown, and whether the file differs), so importing the same file twice
  changes nothing the second time, and a typed value is never overwritten by a file.

A line never makes a patient, a test, an invoice or an order: it fills a test that was ordered for that patient that day
(``samples.ordered``), and says so when it was not. Nothing here moves money, stock, a cashbox, a ledger or a journal.
"""

import csv
import hashlib
import io
from collections import defaultdict
from datetime import date
from decimal import InvalidOperation

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from imports.screen_services import MAX_BYTES, MAX_ROWS

from . import lab, samples
from .models import LabResult, LabTest

# What applying a line would do (and did).
WRITE = "write"                    # a good line: a new result
SAME = "same"                      # already entered, with this very value: nothing to do
DIFFERS = "differs"                # already entered with another value: left as it is (a file never overwrites a typed result)
UNKNOWN_SAMPLE = "unknown_sample"  # not a sample code of this lab (or a patient or a day that does not exist)
UNKNOWN_TEST = "unknown_test"      # no active test has this code
NOT_NUMBER = "not_number"          # the test is numeric and the value is not a number
NOT_ORDERED = "not_ordered"        # the test was not ordered for that patient that day: nothing is created
DUPLICATE = "duplicate"            # the same sample and test again in the file
BAD_ROW = "bad_row"                # fewer than three columns, an empty or too long cell

#: In the order the summary lists them.
STATUSES = (WRITE, SAME, DIFFERS, NOT_ORDERED, UNKNOWN_SAMPLE, UNKNOWN_TEST, NOT_NUMBER, DUPLICATE, BAD_ROW)
SHOWN_ROWS = 200  # the table lists this many lines, problems first; the counts above it cover the whole file

SAMPLE_MAX, TEST_MAX, VALUE_MAX = 40, 30, 200
TEXT_MAX = 120  # LabResult.text_value
PAYLOAD_SALT = "hesba.medical.lab.import.v1"
PAYLOAD_SECONDS = 30 * 60

SAMPLE_HEADERS = {"samplecode", "sampleid", "sample", "barcode", "sampleno", "specimen", "specimenid", "كودالعينة", "العينة", "رقمالعينة"}
VALUE_HEADERS = {"value", "result", "results", "val", "النتيجة", "القيمة"}

FILE_MESSAGES = {
    "ar": {"file_missing": "اختار ملف CSV الأول.", "file_type": "الملف لازم يكون CSV (امتداده .csv).",
           "file_too_big": "الملف أكبر من 5 ميجا.", "file_unreadable": "مش قادرين نقرا الملف. احفظه تاني كـ CSV (UTF-8) وارفعه.",
           "file_empty": "الملف فاضي أو فيه سطر العناوين بس.", "file_too_many_rows": "الملف فيه أكتر من 5000 سطر؛ قسّمه على أكتر من ملف.",
           "stale": "المعاينة دي قديمة أو مش بتاعتك. ارفع الملف تاني.", "nothing": "مفيش في الملف سطر صالح يتكتب.",
           "forbidden": "استيراد النتايج لصاحب صلاحية تسجيل النتايج بس."},
    "en": {"file_missing": "Choose a CSV file first.", "file_type": "The file must be a CSV (ending in .csv).",
           "file_too_big": "The file is larger than 5 MB.", "file_unreadable": "The file could not be read. Save it again as CSV (UTF-8) and upload it.",
           "file_empty": "The file is empty or holds only a header row.", "file_too_many_rows": "The file has more than 5000 lines; split it into several files.",
           "stale": "This preview is out of date or is not yours. Upload the file again.", "nothing": "The file has no good line to write.",
           "forbidden": "Importing results needs the permission to record results."},
}


def _message(code, lang):
    return FILE_MESSAGES["en" if lang == "en" else "ar"][code]


def _key(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


class Row:
    """One line of the file as read: its number in the file and its three cells. ``bad`` is a reason when the line cannot be read."""

    def __init__(self, line, sample="", test="", value="", bad=""):
        self.line, self.sample, self.test, self.value, self.bad = line, sample, test, value, bad

    def pack(self):
        return [self.line, self.sample, self.test, self.value, self.bad]


def read_file(uploaded, lang="ar"):
    """The rows of an uploaded CSV, or a ValidationError in words a person reads. Reads nothing into the database."""

    if uploaded is None:
        raise ValidationError(_message("file_missing", lang))
    if not str(getattr(uploaded, "name", "") or "").lower().endswith(".csv"):
        raise ValidationError(_message("file_type", lang))
    if getattr(uploaded, "size", 0) and uploaded.size > MAX_BYTES:
        raise ValidationError(_message("file_too_big", lang))
    raw = uploaded.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError(_message("file_too_big", lang))
    if b"\x00" in raw[:8192]:  # a spreadsheet or another binary file renamed .csv
        raise ValidationError(_message("file_type", lang))
    for encoding in ("utf-8-sig", "cp1256"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValidationError(_message("file_unreadable", lang))
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = ";" if first.count(";") > first.count(",") else ","  # never csv.Sniffer: 'L5-20261112;GLU;12,5' is semicolon-separated
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows = []
    try:
        for cells in reader:
            cells = [cell.replace("﻿", "").strip() for cell in cells]
            if not any(cells):
                continue
            if not rows and _is_header(cells):
                rows.append(None)  # a placeholder so the header is not mistaken for data; dropped below
                continue
            rows.append(_row(reader.line_num, cells))
            if len([row for row in rows if row is not None]) > MAX_ROWS:
                raise ValidationError(_message("file_too_many_rows", lang))
    except csv.Error:
        raise ValidationError(_message("file_unreadable", lang))
    rows = [row for row in rows if row is not None]
    if not rows:
        raise ValidationError(_message("file_empty", lang))
    return rows


def _is_header(cells):
    """The first line is a header when its first cell names the sample or its third names the value. A data line never does."""

    return _key(cells[0]) in SAMPLE_HEADERS or (len(cells) > 2 and _key(cells[2]) in VALUE_HEADERS)


def _row(line, cells):
    if len(cells) < 3:
        return Row(line, *(cells + ["", "", ""])[:3], bad="columns")
    sample, test, value = cells[:3]  # any further column (a unit, an analyzer's own flag) is not read
    bad = "empty" if not (sample and test and value) else "long" if (len(sample) > SAMPLE_MAX or len(test) > TEST_MAX or len(value) > VALUE_MAX) else ""
    return Row(line, sample[:SAMPLE_MAX], test[:TEST_MAX], value[:VALUE_MAX], bad=bad)


class Line:
    """What one row would do. ``entry`` is what ``record_results`` takes for it; ``existing`` is the result already there, as text."""

    def __init__(self, row):
        self.row = row
        self.status = BAD_ROW
        self.reason = ""        # a short key the page words ("columns", "empty", "long", "future", ...)
        self.patient = None
        self.day = None
        self.test = None
        self.entry = None       # (raw number, text) for record_results
        self.existing = ""      # the stored result, as text, when there is one
        self.other = None       # the line number a duplicate refers to
        self.new = ""           # the file's value as it would be stored, as text
        self.written = False


def _display(value, text):
    shown = "" if value is None else format(value.normalize(), "f")
    return " ".join(part for part in (shown, text) if part)


def _numeric(test):
    """A test with a normal range is numeric: its value must be a number. One without may carry a number or a word."""

    return test.low is not None or test.high is not None


def _entry(test, value):
    """(raw, text, comparable value, comparable text) for a file value on this test, or raises InvalidOperation / ValueError."""

    if _numeric(test):
        number = lab.number(value)
        if number is None:
            raise ValueError("empty")
        return (value, ""), (number, "")
    try:
        number = lab.number(value)
    except (InvalidOperation, ValueError):
        number = None
    if number is not None:
        return (value, ""), (number, "")
    return ("", value), (None, value)


def classify(rows, today=None):
    """[Line], one per row and in the file's order, saying what applying it would do. Writes nothing."""

    today = today or timezone.localdate()
    lines = [Line(row) for row in rows]
    tests = {}
    for test in LabTest.objects.filter(active=True).exclude(code=""):
        tests[test.code] = test
    parsed = {}
    for line in lines:
        if line.row.bad:
            line.status, line.reason = BAD_ROW, line.row.bad
            continue
        found = samples.parse(line.row.sample)
        if found is None:
            line.status = UNKNOWN_SAMPLE
        elif found[1] > today:
            line.status, line.reason = UNKNOWN_SAMPLE, "future"
        else:
            parsed[id(line)] = found
    patients = {patient.pk: patient for patient in samples.patients().filter(pk__in={pk for pk, _ in parsed.values()})}
    comparable = {}
    for line in lines:
        if id(line) not in parsed:
            continue
        pk, day = parsed[id(line)]
        if pk not in patients:
            line.status = UNKNOWN_SAMPLE
            continue
        line.patient, line.day = patients[pk], day
        code = lab.clean_code(line.row.test)
        line.test = tests.get(code) if code else None
        if line.test is None:
            line.status = UNKNOWN_TEST
            continue
        try:
            line.entry, comparable[id(line)] = _entry(line.test, line.row.value)
            line.new = _display(*comparable[id(line)])
        except ValueError as exc:
            line.status, line.reason = (BAD_ROW, "empty") if str(exc) == "empty" else (NOT_NUMBER, "")
            continue
        except InvalidOperation:
            line.status = NOT_NUMBER
            continue
        if len(line.entry[1]) > TEXT_MAX:
            line.status, line.reason = BAD_ROW, "long"
            continue
        line.status = WRITE  # provisionally: the checks below can still hold it back
    # the same sample and test twice: an identical repeat is dropped, a conflicting one holds every occurrence back
    groups = defaultdict(list)
    for line in lines:
        if line.status == WRITE:
            groups[(line.patient.pk, line.day, line.test.pk)].append(line)
    for group in groups.values():
        if len(group) < 2:
            continue
        values = {comparable[id(line)] for line in group}
        for position, line in enumerate(group):
            if len(values) > 1:
                line.status, line.reason, line.other = DUPLICATE, "conflict", next(other.row.line for other in group if other is not line)
            elif position > 0:
                line.status, line.reason, line.other = DUPLICATE, "same", group[0].row.line
    # what is already there, and whether the test was ordered
    live = [line for line in lines if line.status == WRITE]
    if live:
        pairs = {(line.patient.pk, line.day) for line in live}
        existing = {}
        for result in LabResult.objects.filter(patient_id__in={pk for pk, _ in pairs}, taken_on__in={day for _, day in pairs}):
            existing[(result.patient_id, result.taken_on, result.test_id)] = result
        known = samples.ordered(pairs)
        for line in live:
            result = existing.get((line.patient.pk, line.day, line.test.pk))
            if result is not None:
                line.existing = _display(result.value, result.text_value)
                same = (result.value, result.text_value) == comparable[id(line)]
                line.status = SAME if same else DIFFERS
            elif line.test.pk not in known[(line.patient.pk, line.day)]:
                line.status = NOT_ORDERED
                line.reason = "nothing" if not known[(line.patient.pk, line.day)] else ""
    return lines


def counts(lines):
    totals = {status: 0 for status in STATUSES}
    for line in lines:
        totals[line.status] += 1
    return totals


def shown(lines):
    """The lines the table lists: problems first (in file order), then the rest, up to SHOWN_ROWS."""

    problems = [line for line in lines if line.status != WRITE and line.status != SAME]
    rest = [line for line in lines if line.status in (WRITE, SAME)]
    return (problems + rest)[:SHOWN_ROWS]


# ---- carrying the rows from the preview to the apply, with nothing stored ----

def digest(rows):
    basis = "\n".join("|".join(str(part) for part in row.pack()) for row in rows)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def pack(user, name, rows):
    """A signed, short-lived token holding the file's name and its rows. It binds the signed-in user; every line is judged
    again when it is applied, so a stale preview can never write something that has since become wrong."""

    return signing.dumps({"u": int(user.pk), "n": str(name)[:100], "r": [row.pack() for row in rows]}, salt=PAYLOAD_SALT, compress=True)


def unpack(user, token, lang="ar"):
    """(file name, rows) from a preview's token, or a ValidationError for a forged, expired or someone else's one."""

    try:
        data = signing.loads(token or "", salt=PAYLOAD_SALT, max_age=PAYLOAD_SECONDS)
        if data["u"] != user.pk:
            raise signing.BadSignature("another user")
        rows = [Row(int(line), str(sample), str(test), str(value), str(bad)) for line, sample, test, value, bad in data["r"]]
        name = str(data["n"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError(_message("stale", lang))
    if not rows or len(rows) > MAX_ROWS:
        raise ValidationError(_message("stale", lang))
    return name, rows


# ---- applying ----

@transaction.atomic
def apply(user, name, rows, lang="ar"):
    """Write the lines that would be written, through ``lab.record_results``, in one transaction. Returns (lines, totals)
    judged at this moment, with the written ones marked ``line.written``.

    Lab staff who may record results only (the same rule as typing a result). One audit row for an import that wrote something
    (never a value), besides the one ``record_results`` writes per result."""

    from . import services

    if not (lab.is_lab() and services.can_write(user)):
        raise PermissionDenied("Importing lab results needs medical.write_records on a lab.")
    lines = classify(rows)
    batches = defaultdict(dict)
    patients = {}
    for line in lines:
        line.written = False
        if line.status == WRITE:
            batches[(line.patient.pk, line.day)][line.test] = line.entry
            patients[line.patient.pk] = line.patient
    written = 0
    for (pk, day), entries in batches.items():
        lab.record_results(patients[pk], day, entries, user, lang)
        written += len(entries)
    for line in lines:
        line.written = line.status == WRITE
    totals = counts(lines)
    if not written:  # nothing changed (the same file again, or nothing good in it): nothing to audit
        return lines, totals
    AuditLog.objects.create(event_type=AuditEventType.IMPORT, actor=user, module="medical", action="import_lab_results", object_type="medical.LabResult",
                            object_id=digest(rows)[:16], after_data={"file": str(name)[:100], "rows": len(rows), "written": written, "patients": len(patients),
                                                                          "days": len(batches), "left": {status: n for status, n in totals.items() if n and status != WRITE}})
    return lines, totals
